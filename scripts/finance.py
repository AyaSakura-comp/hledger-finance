#!/usr/bin/env python3
"""Deterministic hledger helper used by the hledger-finance agent skill."""

from __future__ import annotations

import argparse
import calendar
import csv
import fcntl
import hashlib
import json
import os
import re
import shlex
import stat
import subprocess
import sys
import tempfile
import unicodedata
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable, Sequence


def add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _quarter_start(value: date) -> date:
    return date(value.year, ((value.month - 1) // 3) * 3 + 1, 1)


def parse_period(spec: str, today: date | None = None) -> tuple[date, date]:
    """Return hledger begin/end dates, where end is exclusive."""
    today = today or date.today()
    spec = spec.strip().lower()
    if ".." in spec:
        start_text, end_text = spec.split("..", 1)
        if not start_text or not end_text:
            raise ValueError("Explicit periods must be START..END")
        start = date.fromisoformat(start_text)
        inclusive_end = date.fromisoformat(end_text)
        if start > inclusive_end:
            raise ValueError("Period start must not be after end")
        return start, inclusive_end + timedelta(days=1)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", spec):
        start = date.fromisoformat(spec)
        return start, start + timedelta(days=1)
    if spec == "today":
        return today, today + timedelta(days=1)
    if spec == "yesterday":
        return today - timedelta(days=1), today
    if spec == "this-week":
        start = today - timedelta(days=today.weekday())
        return start, start + timedelta(days=7)
    if spec == "last-week":
        end = today - timedelta(days=today.weekday())
        return end - timedelta(days=7), end
    if spec == "this-month":
        start = today.replace(day=1)
        return start, add_months(start, 1)
    if spec == "last-month":
        end = today.replace(day=1)
        return add_months(end, -1), end
    if spec == "this-quarter":
        start = _quarter_start(today)
        return start, add_months(start, 3)
    if spec == "last-quarter":
        end = _quarter_start(today)
        return add_months(end, -3), end
    if spec == "this-year":
        return date(today.year, 1, 1), date(today.year + 1, 1, 1)
    if spec == "last-year":
        return date(today.year - 1, 1, 1), date(today.year, 1, 1)
    match = re.fullmatch(r"last-(\d+)-days", spec)
    if match:
        count = int(match.group(1))
        if count < 1:
            raise ValueError("Day count must be positive")
        return today - timedelta(days=count - 1), today + timedelta(days=1)
    match = re.fullmatch(r"last-(\d+)-months", spec)
    if match:
        count = int(match.group(1))
        if count < 1:
            raise ValueError("Month count must be positive")
        end = add_months(today.replace(day=1), 1)
        return add_months(end, -count), end
    raise ValueError(
        "Unknown period. Use START..END, YYYY-MM-DD, today, yesterday, "
        "this/last-week, this/last-month, this/last-quarter, this/last-year, "
        "last-N-days, or last-N-months."
    )


def split_amount(total: Decimal, count: int) -> list[Decimal]:
    if count < 1:
        raise ValueError("Installment count must be at least 1")
    if total < 0:
        raise ValueError("Installment total must not be negative")
    quantum = Decimal("0.01")
    regular = (total / count).quantize(quantum, rounding=ROUND_DOWN)
    parts = [regular] * count
    parts[-1] = total - regular * (count - 1)
    return parts


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _reject_control_characters(value: str, label: str) -> None:
    if any(unicodedata.category(character).startswith("C") for character in value):
        raise ValueError(f"{label} contains unsafe control characters")


def _validate_account(account: str) -> str:
    _reject_control_characters(account, "Account name")
    cleaned = account.strip()
    component = r"\w(?:[\w.' -]*\w)?"
    if not cleaned or "  " in cleaned or not re.fullmatch(
        rf"{component}(?::{component})*", cleaned
    ):
        raise ValueError("Account name contains unsafe hledger grammar")
    return cleaned


def _validate_text(value: str, label: str) -> str:
    _reject_control_characters(value, label)
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{label} must not be empty")
    return cleaned


def _validate_description(value: str) -> str:
    cleaned = _validate_text(value, "Description")
    if ";" in cleaned or cleaned[0] in "*!(":
        raise ValueError("Description contains unsafe hledger grammar")
    return cleaned


def _validate_currency(value: str) -> str:
    cleaned = _validate_text(value, "Currency")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9._-]*", cleaned):
        raise ValueError("Currency contains unsafe hledger grammar")
    return cleaned


def _validate_tag(value: str) -> str:
    cleaned = _validate_text(value, "Tag")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*:[\w.+/-]+", cleaned):
        raise ValueError("Tag must be one canonical name:value pair")
    return cleaned


def _account_family(account: str) -> str | None:
    root = account.split(":", 1)[0]
    return root if root in {"assets", "liabilities", "expenses", "income", "equity"} else None


def _is_opening_equity(account: str) -> bool:
    return account == "equity:opening-balances" or account.startswith(
        "equity:opening-balances:"
    )


def _validate_kind_accounts(kind: str, debit: str, credit: str, label: str) -> None:
    debit_family = _account_family(debit)
    credit_family = _account_family(credit)
    asset_or_liability = {"assets", "liabilities"}
    valid = {
        "expense": debit_family == "expenses" and credit_family in asset_or_liability,
        "income": debit_family in asset_or_liability and credit_family == "income",
        "refund": debit_family in asset_or_liability and credit_family == "expenses",
        "transfer": debit_family in asset_or_liability and credit_family in asset_or_liability,
    }
    if kind in valid and not valid[kind]:
        raise ValueError(f"{label} kind {kind} has invalid debit/credit account families")


APPROVED_SOURCE_TAGS = {
    "source:fuzzy-text",
    "source:pasted-table",
    "source:messy-csv",
    "source:structured-csv",
    "source:historical-import",
    "source:receipt-image",
    "source:invoice-image",
    "source:pdf",
    "source:voice-transcript",
}
HISTORICAL_SOURCE_TAGS = {"source:structured-csv", "source:historical-import"}
INGEST_KINDS = {"expense", "income", "transfer", "refund", "opening-balance"}
_SOURCE_BOUNDARY_SENTINEL = "\x00hfin-source-boundary\x00"


BUILTIN_CATEGORY_RULES: tuple[tuple[str, str], ...] = (
    (r"(?:uber\s*eats|ubereats|foodpanda|熊貓外送)", "expenses:food:delivery"),
    (r"(?:全聯|px\s*mart|家樂福|carrefour|costco|好市多|超市|grocery|菜市場)", "expenses:food:groceries"),
    (r"(?:7[\s-]?11|統一超商|全家|familymart|萊爾富|ok超商)", "expenses:food:convenience"),
    (r"(?:早餐|午餐|晚餐|便當|餐廳|拉麵|火鍋|麥當勞|肯德基|咖啡|星巴克|restaurant|cafe)", "expenses:food:dining"),
    (r"(?:uber(?!\s*eats)|計程車|taxi|台灣大車隊)", "expenses:transport:taxi"),
    (r"(?:高鐵|台鐵|捷運|公車|客運|thsr|railway)", "expenses:transport:transit"),
    (r"(?:中油|台塑石油|加油|gasoline|fuel|停車|parking)", "expenses:transport:driving"),
    (r"(?:中華電信|台灣大哥大|遠傳|水費|電費|瓦斯|網路費|電話費|internet|utility)", "expenses:utilities"),
    (r"(?:房租|租金|管理費|rent|mortgage)", "expenses:housing"),
    (r"(?:醫院|診所|藥局|掛號|看醫生|牙醫|medical|pharmacy)", "expenses:health"),
    (r"(?:學費|書店|課程|補習|udemy|coursera|education|tuition)", "expenses:education"),
    (r"(?:netflix|spotify|youtube\s*premium|disney(?:\+|\s+plus)?|訂閱|subscription)", "expenses:subscriptions"),
    (r"(?:電影|影城|遊戲|steam|演唱會|娛樂|cinema)", "expenses:entertainment"),
    (r"(?:momo|pchome|蝦皮|shopee|amazon|uniqlo|無印良品|網購)", "expenses:shopping"),
    (r"(?:機票|航空|飯店|旅館|住宿|airbnb|booking(?:\.|\s*)com|travel)", "expenses:travel"),
    (r"(?:手續費|年費|利息|fee|bank\s*charge)", "expenses:fees"),
    (r"(?:寵物|獸醫|飼料|pet)", "expenses:pets"),
    (r"(?:捐款|donation|慈善)", "expenses:donations"),
)


def _literal_description(value: str) -> str:
    """Normalize case and width while preserving meaningful punctuation."""
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _normalize_description(value: str) -> str:
    normalized = _literal_description(value)
    normalized = re.sub(r"\[\s*\d+\s*/\s*\d+\s*\]", " ", normalized)
    normalized = re.sub(r"[^\w\u3400-\u9fff]+", " ", normalized, flags=re.UNICODE)
    return " ".join(normalized.split())


def _posting_magnitude(posting: dict) -> Decimal:
    total = Decimal("0")
    for amount in posting.get("pamount", []):
        quantity = amount.get("aquantity", {})
        if "decimalMantissa" in quantity and "decimalPlaces" in quantity:
            value = Decimal(quantity["decimalMantissa"]).scaleb(-int(quantity["decimalPlaces"]))
        else:
            value = Decimal(str(quantity.get("floatingPoint", 0)))
        total += abs(value)
    return total


def _history_expense_accounts(transactions: Sequence[dict]) -> list[tuple[str, str]]:
    history: list[tuple[str, str]] = []
    for transaction in transactions:
        description = _normalize_description(str(transaction.get("tdescription", "")))
        if not description:
            continue
        expense_postings = [
            (index, posting)
            for index, posting in enumerate(transaction.get("tpostings", []))
            if (str(posting.get("paccount", "")) == "expenses" or str(posting.get("paccount", "")).startswith("expenses:"))
        ]
        if not expense_postings:
            continue
        _index, primary = max(
            expense_postings,
            key=lambda item: (_posting_magnitude(item[1]), -item[0]),
        )
        history.append((description, str(primary.get("paccount", ""))))
    return history


def classify_description(
    description: str,
    *,
    transactions: Sequence[dict] = (),
    rules: Sequence[dict[str, str]] = (),
) -> dict:
    """Choose an expense account from custom rules, history, and built-ins."""
    cleaned = _validate_text(description, "Description")
    literal = _literal_description(cleaned)
    normalized = _normalize_description(cleaned)

    for rule in rules:
        pattern = _literal_description(str(rule.get("pattern", "")))
        account = str(rule.get("account", "")).strip()
        if not pattern or not (account == "expenses" or account.startswith("expenses:")):
            continue
        if pattern in literal:
            return {
                "account": account,
                "source": "custom-rule",
                "confidence": 1.0,
                "matched": str(rule.get("pattern", "")),
            }

    best_history: tuple[float, str, str] | None = None
    for previous_description, account in _history_expense_accounts(transactions):
        if previous_description == normalized:
            score = 0.99
        elif min(len(previous_description), len(normalized)) >= 4 and (
            previous_description in normalized or normalized in previous_description
        ):
            score = 0.91
        else:
            similarity = SequenceMatcher(None, normalized, previous_description).ratio()
            if similarity < 0.86:
                continue
            score = min(0.89, similarity)
        candidate = (score, account, previous_description)
        if best_history is None or score >= best_history[0]:
            best_history = candidate
    if best_history is not None:
        score, account, matched = best_history
        return {
            "account": account,
            "source": "history",
            "confidence": round(score, 2),
            "matched": matched,
        }

    for pattern, account in BUILTIN_CATEGORY_RULES:
        match = re.search(pattern, literal, flags=re.IGNORECASE)
        if match:
            return {
                "account": account,
                "source": "builtin-rule",
                "confidence": 0.93,
                "matched": match.group(0),
            }

    return {
        "account": "expenses:uncategorized",
        "source": "fallback",
        "confidence": 0.25,
        "matched": None,
    }


def _load_classification_rules(journal: Path) -> list[dict[str, str]]:
    path = journal.parent / "classification-rules.json"
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Invalid classification rules file {path}: {error}") from error
    rules = payload.get("rules", []) if isinstance(payload, dict) else payload
    if not isinstance(rules, list):
        raise ValueError(f"Classification rules in {path} must be a list")
    return [rule for rule in rules if isinstance(rule, dict)]


def _load_classification_history(journal: Path) -> list[dict]:
    result = _run(["hledger", "-f", str(journal), "print", "--output-format=json"], capture=True)
    if result.returncode:
        raise ValueError(result.stderr.strip() or "Could not read journal history for classification")
    return json.loads(result.stdout)


def _auto_debit(journal: Path, description: str) -> dict:
    return classify_description(
        description,
        transactions=_load_classification_history(journal),
        rules=_load_classification_rules(journal),
    )


def render_transaction(
    txn_date: date,
    description: str,
    debit_account: str,
    credit_account: str,
    amount: Decimal,
    currency: str,
    *,
    tags: Sequence[str] = (),
    extra_postings: Sequence[tuple[str, Decimal]] = (),
) -> str:
    debit_account = _validate_account(debit_account)
    credit_account = _validate_account(credit_account)
    description = _validate_description(description)
    currency = _validate_currency(currency)
    if amount <= 0:
        raise ValueError("Amount must be positive")
    total_credit = amount + sum((value for _, value in extra_postings), Decimal("0"))
    lines = [f"{txn_date.isoformat()} {description}"]
    for tag in tags:
        lines.append(f"    ; {_validate_tag(tag)}")
    lines.append(f"    {debit_account:<40}  {_decimal_text(amount)} {currency}")
    for account, value in extra_postings:
        account = _validate_account(account)
        if value:
            lines.append(f"    {account:<40}  {_decimal_text(value)} {currency}")
    lines.append(f"    {credit_account:<40}  -{_decimal_text(total_credit)} {currency}")
    return "\n".join(lines) + "\n\n"


def render_installments(
    *,
    start: date,
    description: str,
    total: Decimal,
    count: int,
    debit_account: str,
    credit_account: str,
    currency: str,
    fee_per_installment: Decimal = Decimal("0"),
    fee_account: str = "expenses:fees",
    tags: Sequence[str] = (),
) -> str:
    if fee_per_installment < 0:
        raise ValueError("Fee must not be negative")
    chunks: list[str] = []
    for index, amount in enumerate(split_amount(total, count), start=1):
        extras = [(fee_account, fee_per_installment)] if fee_per_installment else []
        chunks.append(
            render_transaction(
                add_months(start, index - 1),
                f"{description} [{index}/{count}]",
                debit_account,
                credit_account,
                amount,
                currency,
                tags=tags,
                extra_postings=extras,
            )
        )
    return "".join(chunks)


def build_hledger_query(
    *,
    journal: Path,
    report: str,
    begin: date | None = None,
    end: date | None = None,
    accounts: Sequence[str] = (),
    descriptions: Sequence[str] = (),
    tags: Sequence[str] = (),
    raw_terms: Sequence[str] = (),
    interval: str | None = None,
    output_format: str | None = None,
) -> list[str]:
    report_commands = {
        "transactions": "print",
        "register": "register",
        "balance": "balance",
        "income-statement": "incomestatement",
        "balance-sheet": "balancesheet",
        "cashflow": "cashflow",
        "budget": "balance",
        "stats": "stats",
    }
    if report not in report_commands:
        raise ValueError(f"Unsupported report: {report}")
    command = ["hledger", "-f", str(journal), report_commands[report]]
    if report == "budget":
        command.append("--budget")
    if begin:
        command.append(f"--begin={begin.isoformat()}")
    if end:
        command.append(f"--end={end.isoformat()}")
    if interval:
        allowed = {"daily", "weekly", "monthly", "quarterly", "yearly"}
        if interval not in allowed:
            raise ValueError(f"Unsupported interval: {interval}")
        command.append(f"--{interval}")
    if output_format:
        report_formats = {
            "transactions": {"txt", "csv", "json"},
            "register": {"txt", "csv", "json"},
            "balance": {"txt", "csv", "json"},
            "income-statement": {"txt", "csv", "json", "html"},
            "balance-sheet": {"txt", "csv", "json", "html"},
            "cashflow": {"txt", "csv", "json", "html"},
            "budget": {"txt", "csv", "json"},
            "stats": set(),
        }
        if output_format not in report_formats[report]:
            supported = ", ".join(sorted(report_formats[report])) or "default text only"
            raise ValueError(
                f"Output format {output_format!r} is not supported for {report}; supported: {supported}"
            )
        command.append(f"--output-format={output_format}")
    if accounts:
        command.append("acct:" + "|".join(accounts))
    command.extend(f"desc:{item}" for item in descriptions)
    for tag in tags:
        command.append("tag:" + tag.replace(":", "=", 1))
    command.extend(raw_terms)
    return command


def _amount_decimal(amount: dict) -> Decimal:
    quantity = amount.get("aquantity", {})
    if "decimalMantissa" in quantity and "decimalPlaces" in quantity:
        return Decimal(quantity["decimalMantissa"]).scaleb(-int(quantity["decimalPlaces"]))
    return Decimal(str(quantity.get("floatingPoint", 0)))


def _transaction_tag_values(transaction: dict) -> dict[str, list[str]]:
    values: defaultdict[str, list[str]] = defaultdict(list)
    for item in transaction.get("ttags", []) or []:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            values[str(item[0])].append(str(item[1]))
    return dict(values)


def _kind_structure_is_valid(transaction: dict, kind: str) -> bool:
    entries: list[tuple[str | None, str, Decimal]] = []
    for posting in transaction.get("tpostings", []):
        account = str(posting.get("paccount", ""))
        for amount in posting.get("pamount", []):
            value = _amount_decimal(amount)
            if value:
                entries.append((_account_family(account), account, value))
    if not entries:
        return False

    asset_or_liability = {"assets", "liabilities"}
    if kind == "expense":
        return (
            all((family == "expenses" and value > 0) or (family in asset_or_liability and value < 0)
                for family, _account, value in entries)
            and any(family == "expenses" for family, _account, _value in entries)
            and any(family in asset_or_liability for family, _account, _value in entries)
        )
    if kind == "income":
        return (
            all(
                (family in asset_or_liability and value > 0)
                or (family == "expenses" and value > 0)
                or (family == "income" and value < 0)
                for family, _account, value in entries
            )
            and any(family in asset_or_liability for family, _account, _value in entries)
            and any(family == "income" for family, _account, _value in entries)
        )
    if kind == "refund":
        return (
            all(
                (family in asset_or_liability and value > 0)
                or family == "expenses"
                for family, _account, value in entries
            )
            and any(family in asset_or_liability for family, _account, _value in entries)
            and any(family == "expenses" and value < 0 for family, _account, value in entries)
        )
    if kind == "transfer":
        return (
            all(
                family in asset_or_liability or (family == "expenses" and value > 0)
                for family, _account, value in entries
            )
            and any(family in asset_or_liability and value > 0 for family, _account, value in entries)
            and any(family in asset_or_liability and value < 0 for family, _account, value in entries)
        )
    if kind == "opening-balance":
        asset_opening = (
            all(
                (family == "assets" and value > 0)
                or (_is_opening_equity(account) and value < 0)
                for family, account, value in entries
            )
            and any(family == "assets" for family, _account, _value in entries)
            and any(_is_opening_equity(account) for _family, account, _value in entries)
        )
        liability_opening = (
            all(
                (_is_opening_equity(account) and value > 0)
                or (family == "liabilities" and value < 0)
                for family, account, value in entries
            )
            and any(family == "liabilities" for family, _account, _value in entries)
            and any(_is_opening_equity(account) for _family, account, _value in entries)
        )
        return asset_opening or liability_opening
    return False


def _installment_transaction_signature(transaction: dict) -> tuple:
    tags = _transaction_tag_values(transaction)
    tag_signature = tuple(
        sorted((name, tuple(sorted(values))) for name, values in tags.items() if name != "import-id")
    )
    posting_signature = []
    for posting in transaction.get("tpostings", []):
        amounts = tuple(
            sorted(
                (
                    amount.get("acommodity") or "unitless",
                    1 if _amount_decimal(amount) > 0 else -1 if _amount_decimal(amount) < 0 else 0,
                )
                for amount in posting.get("pamount", [])
            )
        )
        posting_signature.append((str(posting.get("paccount", "")), amounts))
    return tag_signature, tuple(posting_signature)


def _installment_amount_vector(transaction: dict) -> tuple[Decimal, ...]:
    return tuple(
        _amount_decimal(amount)
        for posting in transaction.get("tpostings", [])
        for amount in posting.get("pamount", [])
    )


def _is_complete_installment_group(transactions: Sequence[dict]) -> bool:
    parts: list[tuple[str, int, int, date, tuple]] = []
    for transaction in transactions:
        description = str(transaction.get("tdescription", "")).strip()
        match = re.fullmatch(r"(.+?)\s+\[(\d+)/(\d+)\]", description)
        try:
            txn_date = date.fromisoformat(str(transaction.get("tdate", "")))
        except ValueError:
            return False
        if not match:
            return False
        parts.append(
            (
                match.group(1),
                int(match.group(2)),
                int(match.group(3)),
                txn_date,
                _installment_transaction_signature(transaction),
            )
        )
    bases = {base for base, _index, _total, _date, _signature in parts}
    totals = {total for _base, _index, total, _date, _signature in parts}
    signatures = {signature for _base, _index, _total, _date, signature in parts}
    if len(bases) != 1 or len(totals) != 1 or len(signatures) != 1:
        return False
    total = next(iter(totals))
    by_index = {index: txn_date for _base, index, _total, txn_date, _signature in parts}
    if total < 2 or len(parts) != total or sorted(by_index) != list(range(1, total + 1)):
        return False
    first_date = by_index[1]
    if not all(by_index[index] == add_months(first_date, index - 1) for index in by_index):
        return False
    amount_vectors = [_installment_amount_vector(transaction) for transaction in transactions]
    if not amount_vectors or len({len(vector) for vector in amount_vectors}) != 1:
        return False
    maximum_rounding_remainder = Decimal(total - 1) * Decimal("0.01")
    return all(
        max(values) - min(values) <= maximum_rounding_remainder
        for values in zip(*amount_vectors)
    )


def _date_only_automated_rule_lines(journal_text: str) -> list[int]:
    matches: list[int] = []
    in_comment_block = False
    for index, line in enumerate(journal_text.splitlines(), start=1):
        if line == _SOURCE_BOUNDARY_SENTINEL:
            in_comment_block = False
            continue
        stripped = line.strip()
        top_level = line == line.lstrip()
        if top_level and stripped == "comment":
            in_comment_block = True
            continue
        if top_level and stripped == "end comment":
            in_comment_block = False
            continue
        if in_comment_block or not stripped or stripped.startswith(";"):
            continue
        directive = line.split(";", 1)[0].strip()
        if top_level and re.fullmatch(
            r"=\s*\d{4}(?P<separator>[-/.])\d{1,2}(?P=separator)\d{1,2}",
            directive,
        ):
            matches.append(index)
    return matches


def audit_journal(transactions: Sequence[dict], journal_text: str) -> dict:
    """Report semantic import hazards that hledger's balance check cannot detect."""
    direction = {
        "expense": {"debit_count": 0, "credit_count": 0, "zero_count": 0, "amounts": defaultdict(Decimal)},
        "income": {"debit_count": 0, "credit_count": 0, "zero_count": 0, "amounts": defaultdict(Decimal)},
    }
    untagged_direction = {
        "expense": {"debit_count": 0, "credit_count": 0},
        "income": {"debit_count": 0, "credit_count": 0},
    }
    bad_kind_expenses = 0
    bad_kind_incomes = 0
    invalid_kinds = 0
    invalid_structures = 0
    invalid_sources = 0
    invalid_import_ids = 0
    historical_missing_ids = 0
    historical_invalid_tags = 0
    historical_invalid_structures = 0
    import_id_transactions: defaultdict[str, list[dict]] = defaultdict(list)
    descriptions: defaultdict[str, int] = defaultdict(int)

    for transaction in transactions:
        description = str(transaction.get("tdescription", "")).strip()
        if description:
            descriptions[description] += 1
        tags = _transaction_tag_values(transaction)
        kinds = tags.get("kind", [])
        source_values = tags.get("source", [])
        import_ids = tags.get("import-id", [])
        managed = bool(kinds or source_values or import_ids)
        valid_kind = len(kinds) == 1 and kinds[0] in INGEST_KINDS
        kind = kinds[0] if valid_kind else None
        structure_valid = bool(kind and _kind_structure_is_valid(transaction, kind))
        if managed and not valid_kind:
            invalid_kinds += 1
        if managed and not structure_valid:
            invalid_structures += 1

        valid_source = len(source_values) == 1 and f"source:{source_values[0]}" in APPROVED_SOURCE_TAGS
        if source_values and not valid_source:
            invalid_sources += 1
        valid_id = len(import_ids) == 1 and bool(
            re.fullmatch(r"[A-Za-z0-9._-]+", import_ids[0])
        )
        if import_ids and not valid_id:
            invalid_import_ids += 1
        if valid_id:
            import_id_transactions[import_ids[0]].append(transaction)

        historical = any(f"source:{value}" in HISTORICAL_SOURCE_TAGS for value in source_values)
        if historical:
            valid_tags = (
                valid_source
                and f"source:{source_values[0]}" in HISTORICAL_SOURCE_TAGS
                and valid_kind
                and valid_id
            )
            if not import_ids:
                historical_missing_ids += 1
            if not valid_tags:
                historical_invalid_tags += 1
            if not structure_valid:
                historical_invalid_structures += 1

        for posting in transaction.get("tpostings", []):
            account = str(posting.get("paccount", ""))
            account_family = _account_family(account)
            if account_family == "expenses":
                family = "expense"
            elif account_family == "income":
                family = "income"
            else:
                continue
            for amount in posting.get("pamount", []):
                value = _amount_decimal(amount)
                currency = amount.get("acommodity") or "unitless"
                direction[family]["amounts"][currency] += value
                if value > 0:
                    direction[family]["debit_count"] += 1
                    if kind is None:
                        untagged_direction[family]["debit_count"] += 1
                    if kind == "income" and family == "income":
                        bad_kind_incomes += 1
                elif value < 0:
                    direction[family]["credit_count"] += 1
                    if kind is None:
                        untagged_direction[family]["credit_count"] += 1
                    if kind == "expense" and family == "expense":
                        bad_kind_expenses += 1
                else:
                    direction[family]["zero_count"] += 1

    findings: list[dict] = []
    date_only_rules = _date_only_automated_rule_lines(journal_text)
    if date_only_rules:
        findings.append(
            {
                "severity": "critical",
                "code": "date-only-automated-posting",
                "count": len(date_only_rules),
                "lines": date_only_rules[:20],
                "message": "Date-only '=' directives are automated posting rules, not opening balances; use dated balanced transactions.",
            }
        )

    heuristic_specs = (
        ("expense", "credit_count", "debit_count", "mostly-expenses-credit-normal", "contra-only-expenses"),
        ("income", "debit_count", "credit_count", "mostly-income-debit-normal", "contra-only-income"),
    )
    for family, contra_key, normal_key, mixed_code, contra_code in heuristic_specs:
        values = untagged_direction[family]
        contra_count = values[contra_key]
        normal_count = values[normal_key]
        if contra_count >= 100 and normal_count > 0 and contra_count >= normal_count * 20:
            findings.append(
                {
                    "severity": "critical",
                    "code": mixed_code,
                    "count": contra_count,
                    "normal_count": normal_count,
                    "message": f"Untagged {family} postings are overwhelmingly reversed relative to normal postings; inspect the import sign mapping.",
                }
            )
        elif contra_count >= 10 and normal_count == 0:
            findings.append(
                {
                    "severity": "warning",
                    "code": contra_code,
                    "count": contra_count,
                    "message": f"This journal contains only untagged contra {family} postings; this may be legitimate refunds/reversals, but should be reviewed.",
                }
            )

    if invalid_kinds:
        findings.append(
            {
                "severity": "critical",
                "code": "tagged-transaction-invalid-kind",
                "count": invalid_kinds,
                "message": "Managed transactions need exactly one supported kind tag.",
            }
        )
    if invalid_structures:
        findings.append(
            {
                "severity": "critical",
                "code": "tagged-transaction-invalid-structure",
                "count": invalid_structures,
                "message": "Managed transaction postings do not match the account families and direction required by their kind.",
            }
        )
    if invalid_sources:
        findings.append(
            {
                "severity": "critical",
                "code": "tagged-transaction-invalid-source",
                "count": invalid_sources,
                "message": "When source tags are present, exactly one approved source is required.",
            }
        )
    if invalid_import_ids:
        findings.append(
            {
                "severity": "critical",
                "code": "tagged-transaction-invalid-import-id",
                "count": invalid_import_ids,
                "message": "When import-id tags are present, exactly one canonical import ID is required.",
            }
        )
    colliding_ids = sorted(
        import_id
        for import_id, grouped_transactions in import_id_transactions.items()
        if len(grouped_transactions) > 1
        and not _is_complete_installment_group(grouped_transactions)
    )
    if colliding_ids:
        findings.append(
            {
                "severity": "critical",
                "code": "duplicate-import-id",
                "count": len(colliding_ids),
                "import_ids": colliding_ids[:20],
                "message": "Import IDs may repeat only across one complete, uniquely numbered installment group.",
            }
        )

    if bad_kind_expenses:
        findings.append(
            {
                "severity": "critical",
                "code": "tagged-expense-has-credit-posting",
                "count": bad_kind_expenses,
                "message": "Transactions tagged kind:expense contain credit postings to expenses.",
            }
        )
    if bad_kind_incomes:
        findings.append(
            {
                "severity": "critical",
                "code": "tagged-income-has-debit-posting",
                "count": bad_kind_incomes,
                "message": "Transactions tagged kind:income contain debit postings to income.",
            }
        )
    if historical_missing_ids:
        findings.append(
            {
                "severity": "critical",
                "code": "historical-import-missing-id",
                "count": historical_missing_ids,
                "message": "Historical/structured import transactions need stable import-id tags for idempotent retries.",
            }
        )
    if historical_invalid_tags:
        findings.append(
            {
                "severity": "critical",
                "code": "historical-import-invalid-tags",
                "count": historical_invalid_tags,
                "message": "Historical transactions need exactly one approved historical source, kind, and canonical import-id tag.",
            }
        )
    if historical_invalid_structures:
        findings.append(
            {
                "severity": "critical",
                "code": "historical-import-invalid-structure",
                "count": historical_invalid_structures,
                "message": "Historical transaction postings do not match the account families and direction required by their kind.",
            }
        )
    if transactions and descriptions:
        repeated_description, repeated_count = max(descriptions.items(), key=lambda item: item[1])
        if repeated_count >= 10 and repeated_count * 2 > len(transactions):
            findings.append(
                {
                    "severity": "info",
                    "code": "generic-description-dominates",
                    "count": repeated_count,
                    "description": repeated_description,
                    "message": "One description dominates the journal; verify that imports preserve the actual merchant or purpose.",
                }
            )

    serialized_direction = {}
    for family, values in direction.items():
        serialized_direction[family] = {
            "debit_count": values["debit_count"],
            "credit_count": values["credit_count"],
            "zero_count": values["zero_count"],
            "accounting_totals": {
                currency: _decimal_text(amount)
                for currency, amount in sorted(values["amounts"].items())
            },
        }
    critical_count = sum(1 for finding in findings if finding["severity"] == "critical")
    warning_count = sum(1 for finding in findings if finding["severity"] == "warning")
    return {
        "ok": critical_count == 0,
        "transaction_count": len(transactions),
        "critical_count": critical_count,
        "warning_count": warning_count,
        "direction_summary": serialized_direction,
        "findings": findings,
    }


def compute_stats(transactions: Sequence[dict]) -> dict:
    incomes: defaultdict[str, Decimal] = defaultdict(Decimal)
    expenses: defaultdict[str, Decimal] = defaultdict(Decimal)
    income_categories: defaultdict[str, defaultdict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    expense_categories: defaultdict[str, defaultdict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    transaction_count = len(transactions)
    for transaction in transactions:
        for posting in transaction.get("tpostings", []):
            account = posting.get("paccount", "")
            for amount in posting.get("pamount", []):
                currency = amount.get("acommodity") or "unitless"
                value = _amount_decimal(amount)
                if account == "income" or account.startswith("income:"):
                    reported = -value
                    incomes[currency] += reported
                    income_categories[account][currency] += reported
                elif account == "expenses" or account.startswith("expenses:"):
                    reported = -value
                    expenses[currency] += reported
                    expense_categories[account][currency] += reported
    currencies = {}
    for currency in sorted(set(incomes) | set(expenses)):
        income = incomes[currency]
        expense = expenses[currency]
        currencies[currency] = {
            "income": _decimal_text(income),
            "expense": _decimal_text(expense),
            "net": _decimal_text(income + expense),
        }

    def serialize_categories(values: dict[str, dict[str, Decimal]]) -> dict:
        return {
            account: {currency: _decimal_text(amount) for currency, amount in sorted(amounts.items())}
            for account, amounts in sorted(values.items())
        }

    return {
        "transaction_count": transaction_count,
        "sign_convention": {
            "basis": "cashflow",
            "income": "credit_positive_debit_negative",
            "expense": "debit_negative_credit_positive",
            "net": "income_plus_expense",
        },
        "currencies": currencies,
        "income_categories": serialize_categories(income_categories),
        "expense_categories": serialize_categories(expense_categories),
    }


def build_visualization_data(transactions: Sequence[dict], currency: str | None = None) -> dict:
    """Aggregate hledger JSON transactions into one-currency chart data."""
    available: set[str] = set()
    for transaction in transactions:
        for posting in transaction.get("tpostings", []):
            account = posting.get("paccount", "")
            if not (account == "income" or account.startswith("income:") or account == "expenses" or account.startswith("expenses:")):
                continue
            for amount in posting.get("pamount", []):
                available.add(amount.get("acommodity") or "unitless")
    if not available:
        raise ValueError("No income or expense data is available to visualize")
    if currency is None:
        if len(available) > 1:
            choices = ", ".join(sorted(available))
            raise ValueError(f"Data contains multiple currencies ({choices}); specify --currency")
        currency = next(iter(available))
    if currency not in available:
        choices = ", ".join(sorted(available))
        raise ValueError(f"Currency {currency!r} is not present; available: {choices}")

    months: defaultdict[str, dict[str, Decimal]] = defaultdict(
        lambda: {"income": Decimal("0"), "expense": Decimal("0")}
    )
    expense_categories: defaultdict[str, Decimal] = defaultdict(Decimal)
    income_categories: defaultdict[str, Decimal] = defaultdict(Decimal)
    daily_net: defaultdict[str, Decimal] = defaultdict(Decimal)
    present_kinds: set[str] = set()
    matched_transactions = 0
    for transaction in transactions:
        txn_date = str(transaction.get("tdate", ""))
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", txn_date):
            continue
        month = txn_date[:7]
        matched = False
        for posting in transaction.get("tpostings", []):
            account = posting.get("paccount", "")
            for amount in posting.get("pamount", []):
                commodity = amount.get("acommodity") or "unitless"
                if commodity != currency:
                    continue
                value = _amount_decimal(amount)
                if account == "income" or account.startswith("income:"):
                    flow = -value
                    months[month]["income"] += flow
                    income_categories[account] += flow
                    daily_net[txn_date] += flow
                    present_kinds.add("income")
                    matched = True
                elif account == "expenses" or account.startswith("expenses:"):
                    flow = -value
                    months[month]["expense"] += flow
                    expense_categories[account] += flow
                    daily_net[txn_date] += flow
                    present_kinds.add("expense")
                    matched = True
        if matched:
            matched_transactions += 1

    total_income = sum((values["income"] for values in months.values()), Decimal("0"))
    total_expense = sum((values["expense"] for values in months.values()), Decimal("0"))
    if present_kinds == {"income"}:
        flow_mode = "income"
    elif present_kinds == {"expense"}:
        flow_mode = "expense"
    else:
        flow_mode = "mixed"
    running = Decimal("0")
    cumulative_net: list[tuple[str, Decimal]] = []
    for txn_date, value in sorted(daily_net.items()):
        running += value
        cumulative_net.append((txn_date, running))
    return {
        "currency": currency,
        "transaction_count": matched_transactions,
        "flow_mode": flow_mode,
        "sign_convention": {
            "basis": "cashflow",
            "income": "credit_positive_debit_negative",
            "expense": "debit_negative_credit_positive",
            "net": "income_plus_expense",
        },
        "totals": {
            "income": total_income,
            "expense": total_expense,
            "net": total_income + total_expense,
        },
        "months": dict(sorted(months.items())),
        "income_categories": dict(sorted(income_categories.items())),
        "expense_categories": dict(sorted(expense_categories.items())),
        "cumulative_net": cumulative_net,
    }


def _chart_amount(value: Decimal, currency: str) -> str:
    if value == value.to_integral():
        text = f"{float(value):,.0f}"
    elif abs(value) < 1:
        text = f"{float(value):,.8f}".rstrip("0").rstrip(".")
    else:
        text = f"{float(value):,.2f}"
    return f"{text} {currency}"


def _axis_number(value: float) -> str:
    """Format both ordinary money and sub-unit commodities without zeroing them."""
    if value == 0:
        return "0"
    if abs(value) >= 1:
        return f"{value:,.0f}"
    return f"{value:,.8f}".rstrip("0").rstrip(".")


def _ellipsize(value: str, max_characters: int) -> str:
    """Bound chart text so user-controlled labels cannot escape their panel."""
    if len(value) <= max_characters:
        return value
    return value[: max_characters - 1].rstrip() + "…"


def _dashboard_presentation(data: dict) -> dict:
    """Choose only the visual elements relevant to the available cash-flow kinds."""
    mode = data.get("flow_mode", "mixed")
    if mode == "expense":
        return {
            "mode": mode,
            "cards": [("支出", "expense", "sakura", "pink_wash")],
            "month_series": [("expense", "支出", "sakura")],
            "month_title": "月度支出趨勢",
            "composition_kind": "expense",
            "account_prefix": "expenses:",
            "composition_colors": ["sakura", "indigo", "gold", "matcha"],
            "composition_title": "支出組成",
            "composition_style": "pie" if data.get("transaction_count") == 1 else "donut",
            "cumulative_title": "累積支出",
            "ranking_title": "支出分類排行",
        }
    if mode == "income":
        return {
            "mode": mode,
            "cards": [("收入", "income", "matcha", "green_wash")],
            "month_series": [("income", "收入", "matcha")],
            "month_title": "月度收入趨勢",
            "composition_kind": "income",
            "account_prefix": "income:",
            "composition_colors": ["matcha", "indigo", "gold", "sakura"],
            "composition_title": "收入組成",
            "composition_style": "pie" if data.get("transaction_count") == 1 else "donut",
            "cumulative_title": "累積收入",
            "ranking_title": "收入分類排行",
        }
    return {
        "mode": "mixed",
        "cards": [
            ("收入", "income", "matcha", "green_wash"),
            ("支出", "expense", "sakura", "pink_wash"),
            ("淨額", "net", "indigo", "blue_wash"),
        ],
        "month_series": [
            ("income", "收入", "matcha"),
            ("expense", "支出", "sakura"),
        ],
        "month_title": "月度收支趨勢",
        "composition_kind": "expense",
        "account_prefix": "expenses:",
        "composition_colors": ["sakura", "indigo", "gold", "matcha"],
        "composition_title": "支出組成",
        "composition_style": "donut",
        "cumulative_title": "累積淨額",
        "ranking_title": "支出分類排行",
    }


def _composition_entries(data: dict, presentation: dict) -> list[tuple[str, Decimal, Decimal]]:
    """Return account, absolute chart magnitude, and signed cash-flow value."""
    kind = presentation["composition_kind"]
    entries = [
        (account, abs(amount), amount)
        for account, amount in data[f"{kind}_categories"].items()
        if amount != 0
    ]
    entries.sort(key=lambda item: item[1], reverse=True)
    return entries


def render_dashboard_png(
    data: dict,
    output: Path,
    *,
    title: str = "財務分析",
    period_label: str = "全部期間",
) -> Path:
    """Render a white-background Japanese-palette financial dashboard PNG."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.dates as mdates
        import matplotlib.pyplot as plt
        from matplotlib import font_manager
        from matplotlib.patches import FancyBboxPatch
    except ImportError as error:
        raise ValueError("Visualization requires matplotlib") from error

    cjk_candidates = [
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJKtc-Regular.otf"),
        Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
    ]
    font_path = next((candidate for candidate in cjk_candidates if candidate.exists()), None)
    if font_path is not None:
        font_manager.fontManager.addfont(str(font_path))
        font_name = font_manager.FontProperties(fname=str(font_path)).get_name()
    else:
        font_name = "DejaVu Sans"
    plt.rcParams.update(
        {
            "font.family": font_name,
            "axes.unicode_minus": False,
            "figure.facecolor": "#FFFFFF",
            "axes.facecolor": "#FFFFFF",
            "savefig.facecolor": "#FFFFFF",
        }
    )

    palette = {
        "indigo": "#526D82",
        "sakura": "#D89CA6",
        "matcha": "#7F9E7A",
        "gold": "#D3A54A",
        "sumi": "#34383F",
        "gray": "#7A8088",
        "grid": "#E8E4DC",
        "wash": "#F7F4EE",
        "blue_wash": "#EDF2F5",
        "pink_wash": "#F8EEF0",
        "green_wash": "#EFF4ED",
    }
    currency = data["currency"]
    totals = data["totals"]
    presentation = _dashboard_presentation(data)
    output = Path(output).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)

    fig = plt.figure(figsize=(12, 8), dpi=150)
    grid = fig.add_gridspec(
        3,
        6,
        height_ratios=[0.85, 2.55, 2.35],
        left=0.055,
        right=0.97,
        top=0.84,
        bottom=0.075,
        hspace=0.58,
        wspace=0.72,
    )
    fig.text(0.055, 0.945, _ellipsize(title, 30), fontsize=24, fontweight="bold", color=palette["sumi"], va="top")
    fig.text(0.055, 0.9, period_label, fontsize=10.5, color=palette["gray"], va="top")
    fig.text(
        0.97,
        0.94,
        f"{data['transaction_count']} 筆交易 · {currency}",
        fontsize=10,
        color=palette["gray"],
        ha="right",
        va="top",
    )

    cards = presentation["cards"]
    card_grid = grid[0, :].subgridspec(1, len(cards), wspace=0.18)
    for index, (label, value_key, accent_key, background_key) in enumerate(cards):
        value = totals[value_key]
        accent = palette[accent_key]
        background = palette[background_key]
        axis = fig.add_subplot(card_grid[0, index])
        axis.set_axis_off()
        axis.add_patch(
            FancyBboxPatch(
                (0, 0),
                1,
                1,
                transform=axis.transAxes,
                boxstyle="round,pad=0.018,rounding_size=0.04",
                facecolor=background,
                edgecolor="none",
            )
        )
        text_x = 0.5 if len(cards) == 1 else 0.07
        text_align = "center" if len(cards) == 1 else "left"
        axis.text(text_x, 0.72, label, fontsize=10.5, color=palette["gray"], va="center", ha=text_align)
        axis.text(
            text_x,
            0.36,
            _chart_amount(value, currency),
            fontsize=18,
            fontweight="bold",
            color=accent,
            va="center",
            ha=text_align,
        )

    def style_axis(axis, title_text: str) -> None:
        axis.set_title(title_text, loc="left", fontsize=12, fontweight="bold", color=palette["sumi"], pad=12)
        axis.spines[["top", "right", "left"]].set_visible(False)
        axis.spines["bottom"].set_color(palette["grid"])
        axis.tick_params(colors=palette["gray"], labelsize=8.5, length=0)
        axis.grid(axis="y", color=palette["grid"], linewidth=0.8, alpha=0.8)
        axis.set_axisbelow(True)

    month_axis = fig.add_subplot(grid[1, :4])
    style_axis(month_axis, presentation["month_title"])
    month_labels = list(data["months"])
    positions = list(range(len(month_labels)))
    month_series = presentation["month_series"]
    width = 0.34 if len(month_series) == 2 else 0.56
    offsets = [-width / 2, width / 2] if len(month_series) == 2 else [0]
    for (key, label, color_key), offset in zip(month_series, offsets):
        values = [float(data["months"][month][key]) for month in month_labels]
        month_axis.bar(
            [position + offset for position in positions],
            values,
            width,
            label=label,
            color=palette[color_key],
        )
    month_axis.set_xticks(positions, [month.replace("-", "/") for month in month_labels])
    if len(month_labels) > 8:
        month_axis.tick_params(axis="x", rotation=35)
    month_axis.axhline(0, color=palette["grid"], linewidth=1)
    month_axis.yaxis.set_major_formatter(lambda value, _pos: _axis_number(value))
    if len(month_series) > 1:
        month_axis.legend(frameon=False, fontsize=9, ncol=2, loc="upper right")

    donut_axis = fig.add_subplot(grid[1, 4:])
    donut_axis.set_title(
        presentation["composition_title"],
        loc="left",
        fontsize=12,
        fontweight="bold",
        color=palette["sumi"],
        pad=12,
    )
    composition_kind = presentation["composition_kind"]
    account_prefix = presentation["account_prefix"]
    composition_entries = _composition_entries(data, presentation)
    composition_top = composition_entries[:5]
    remainder = sum((magnitude for _, magnitude, _ in composition_entries[5:]), Decimal("0"))
    if remainder > 0:
        signed_remainder = -remainder if composition_kind == "expense" else remainder
        composition_top.append((f"{account_prefix}其他", remainder, signed_remainder))
    if composition_top:
        donut_colors = [
            *(palette[color_key] for color_key in presentation["composition_colors"]),
            "#9B8FA6",
            "#B7B0A4",
        ]
        labels = [
            _ellipsize(account.removeprefix(account_prefix).replace(":", "/"), 26)
            for account, _, _ in composition_top
        ]
        sizes = [float(magnitude) for _, magnitude, _ in composition_top]
        wedgeprops = {"edgecolor": "white", "linewidth": 2}
        if presentation["composition_style"] == "donut":
            wedgeprops["width"] = 0.38
        donut_axis.pie(
            sizes,
            colors=donut_colors[: len(sizes)],
            startangle=90,
            counterclock=False,
            wedgeprops=wedgeprops,
        )
        if presentation["composition_style"] == "donut":
            total_label = "總支出" if composition_kind == "expense" else "總收入"
            donut_axis.text(0, 0.05, total_label, ha="center", va="center", fontsize=9, color=palette["gray"])
            donut_axis.text(
                0,
                -0.15,
                _chart_amount(totals[composition_kind], currency),
                ha="center",
                va="center",
                fontsize=10.5,
                fontweight="bold",
                color=palette["sumi"],
            )
        donut_axis.legend(labels, loc="lower center", bbox_to_anchor=(0.5, -0.28), frameon=False, fontsize=7.5, ncol=2)
    else:
        missing_label = "無支出資料" if composition_kind == "expense" else "無收入資料"
        donut_axis.text(0.5, 0.5, missing_label, ha="center", va="center", color=palette["gray"])
        donut_axis.set_axis_off()

    cumulative_axis = fig.add_subplot(grid[2, :3])
    style_axis(cumulative_axis, presentation["cumulative_title"])
    cumulative_dates = [datetime.fromisoformat(day) for day, _ in data["cumulative_net"]]
    cumulative_values = [float(value) for _, value in data["cumulative_net"]]
    cumulative_axis.plot(cumulative_dates, cumulative_values, color=palette["indigo"], linewidth=2.4)
    cumulative_axis.fill_between(cumulative_dates, cumulative_values, 0, color=palette["blue_wash"], alpha=0.9)
    cumulative_axis.axhline(0, color=palette["grid"], linewidth=1)
    if len(cumulative_dates) == 1:
        cumulative_axis.scatter(cumulative_dates, cumulative_values, color=palette["indigo"], s=24, zorder=3)
        cumulative_axis.set_xlim(
            cumulative_dates[0] - timedelta(days=1),
            cumulative_dates[0] + timedelta(days=1),
        )
        cumulative_axis.set_xticks(cumulative_dates)
    else:
        cumulative_axis.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=3, maxticks=7))
    cumulative_axis.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
    cumulative_axis.yaxis.set_major_formatter(lambda value, _pos: _axis_number(value))

    category_axis = fig.add_subplot(grid[2, 3:])
    style_axis(category_axis, presentation["ranking_title"])
    category_top = composition_entries[:6][::-1]
    if category_top:
        labels = [
            _ellipsize(account.removeprefix(account_prefix).replace(":", "/"), 26)
            for account, _, _ in category_top
        ]
        values = [float(magnitude) for _, magnitude, _ in category_top]
        signed_values = [signed for _, _, signed in category_top]
        base_color = palette["sakura"] if composition_kind == "expense" else palette["matcha"]
        colors = [base_color] * len(values)
        if len(colors) > 1:
            colors[-1] = palette["indigo"]
        category_axis.barh(labels, values, color=colors, height=0.58)
        category_axis.margins(x=0.2)
        category_axis.xaxis.set_major_formatter(lambda value, _pos: _axis_number(value))
        for index, (value, signed_value) in enumerate(zip(values, signed_values)):
            category_axis.text(
                value,
                index,
                f"  {_chart_amount(signed_value, currency)}",
                va="center",
                fontsize=8,
                color=palette["gray"],
            )
    else:
        missing_label = "無支出資料" if composition_kind == "expense" else "無收入資料"
        category_axis.text(0.5, 0.5, missing_label, transform=category_axis.transAxes, ha="center", va="center", color=palette["gray"])

    fig.text(
        0.055,
        0.026,
        "hledger-finance · 本圖依帳本資料自動產生",
        fontsize=8,
        color="#9A9A96",
    )
    fig.savefig(output, dpi=150, facecolor="white")
    plt.close(fig)
    return output


def _reject_duplicate_json_keys(pairs: Sequence[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Ingest JSON contains duplicate key: {key}")
        result[key] = value
    return result


def _reject_nonfinite_json_constant(value: str):
    raise ValueError(f"Ingest JSON contains non-finite number: {value}")


def parse_ingest_json(raw: str) -> object:
    try:
        return json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_float=Decimal,
            parse_constant=_reject_nonfinite_json_constant,
        )
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid ingest JSON: {error}") from error


def _ingest_string(value: object, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    _reject_control_characters(value, label)
    cleaned = value.strip()
    if not cleaned and not allow_empty:
        raise ValueError(f"{label} must not be empty")
    return cleaned


def _validate_import_id(value: object, label: str) -> str:
    import_id = _ingest_string(value, label, allow_empty=True)
    if import_id and not re.fullmatch(r"[A-Za-z0-9._-]+", import_id):
        raise ValueError(
            f"{label} may contain only ASCII letters, digits, '.', '_', and '-'"
        )
    return import_id


def _import_ids_in_tag_comment(comment: str) -> set[str]:
    import_ids: set[str] = set()
    for item in comment.split(","):
        match = re.fullmatch(r"\s*import-id:([A-Za-z0-9._-]+)\s*", item)
        if match:
            import_ids.add(match.group(1))
    return import_ids


def _existing_import_ids(journal_text: str) -> set[str]:
    """Read only canonical transaction tags, never detached or posting comments."""
    import_ids: set[str] = set()
    in_transaction = False
    transaction_comments_open = False
    in_comment_block = False
    transaction_header = re.compile(
        r"^\d{4}(?P<separator>[-/.])\d{1,2}(?P=separator)\d{1,2}(?:=\S+)?(?:\s|$)"
    )
    for line in journal_text.splitlines():
        if line == _SOURCE_BOUNDARY_SENTINEL:
            in_comment_block = False
            in_transaction = False
            transaction_comments_open = False
            continue
        stripped = line.strip()
        top_level = line == line.lstrip()
        if top_level and stripped == "comment":
            in_comment_block = True
            in_transaction = False
            transaction_comments_open = False
            continue
        if top_level and stripped == "end comment":
            in_comment_block = False
            continue
        if in_comment_block:
            continue
        if transaction_header.match(line):
            in_transaction = True
            transaction_comments_open = True
            if ";" in line:
                import_ids.update(_import_ids_in_tag_comment(line.split(";", 1)[1]))
            continue
        if not stripped or (line and not line[0].isspace()):
            in_transaction = False
            transaction_comments_open = False
            continue
        if in_transaction and transaction_comments_open and re.match(r"^\s+;", line):
            import_ids.update(_import_ids_in_tag_comment(line.lstrip()[1:]))
        elif in_transaction:
            transaction_comments_open = False
    return import_ids


def build_ingest_transactions(
    payload: object,
    *,
    existing_text: str,
    classification_transactions: Sequence[dict] = (),
    classification_rules: Sequence[dict[str, str]] = (),
    today: date | None = None,
) -> tuple[str, int, int, list[dict]]:
    """Render agent-normalized fuzzy input as one validated journal addition."""
    if isinstance(payload, list):
        defaults: dict = {}
        records = payload
    elif isinstance(payload, dict):
        unknown_top = set(payload) - {"defaults", "transactions"}
        if unknown_top:
            raise ValueError(f"Ingest payload has unknown fields: {', '.join(sorted(unknown_top))}")
        defaults = payload.get("defaults", {})
        records = payload.get("transactions")
    else:
        raise ValueError("Ingest payload must be a JSON object or list")
    if not isinstance(defaults, dict):
        raise ValueError("Ingest defaults must be an object")
    allowed_defaults = {
        "kind", "date", "currency", "debit", "credit", "tags", "installments", "fee", "fee_account"
    }
    unknown_defaults = set(defaults) - allowed_defaults
    if unknown_defaults:
        raise ValueError(f"Ingest defaults have unknown fields: {', '.join(sorted(unknown_defaults))}")
    if not isinstance(records, list) or not records:
        raise ValueError("Ingest payload requires a non-empty transactions list")

    allowed_record = allowed_defaults | {"description", "amount", "import_id"}
    seen_ids = _existing_import_ids(existing_text)
    output: list[str] = []
    decisions: list[dict] = []
    imported = 0
    skipped = 0
    today = today or date.today()

    for index, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            raise ValueError(f"Ingest record {index} must be an object")
        unknown = set(record) - allowed_record
        if unknown:
            raise ValueError(f"Ingest record {index} has unknown fields: {', '.join(sorted(unknown))}")
        if "description" not in record or "amount" not in record:
            raise ValueError(f"Ingest record {index} requires description and amount")

        import_id = _validate_import_id(
            record.get("import_id", ""),
            f"Ingest record {index} import_id",
        )

        description = _validate_description(
            _ingest_string(record["description"], f"Ingest record {index} description")
        )
        if isinstance(record["amount"], bool) or not isinstance(record["amount"], (str, int, float, Decimal)):
            raise ValueError(f"Ingest record {index} amount must be a decimal string or number")
        try:
            amount = Decimal(str(record["amount"]))
        except InvalidOperation as error:
            raise ValueError(f"Ingest record {index} has an invalid amount") from error
        if not amount.is_finite():
            raise ValueError(f"Ingest record {index} amount must be finite")
        if amount <= 0:
            raise ValueError(f"Ingest record {index} amount must be positive")

        kind_value = record.get("kind", defaults.get("kind"))
        kind = _ingest_string(kind_value, f"Ingest record {index} kind") if kind_value is not None else None
        if kind not in INGEST_KINDS:
            raise ValueError(
                f"Ingest record {index} kind must be expense, income, transfer, refund, or opening-balance"
            )
        has_date = "date" in record or "date" in defaults
        raw_date = record.get("date", defaults.get("date"))
        if has_date:
            date_text = _ingest_string(raw_date, f"Ingest record {index} date")
            txn_date = date.fromisoformat(date_text)
        else:
            txn_date = today
        currency = _validate_currency(
            _ingest_string(
                record.get("currency", defaults.get("currency", "TWD")),
                f"Ingest record {index} currency",
            )
        )
        has_debit = "debit" in record or "debit" in defaults
        has_credit = "credit" in record or "credit" in defaults
        if kind == "expense":
            debit = _ingest_string(
                record.get("debit", defaults.get("debit", "auto")),
                f"Ingest record {index} debit",
            )
            credit = _ingest_string(
                record.get("credit", defaults.get("credit", "assets:cash")),
                f"Ingest record {index} credit",
            )
        else:
            if not has_debit or not has_credit:
                raise ValueError(
                    f"Ingest record {index} kind {kind} requires explicit debit and credit accounts"
                )
            debit = _ingest_string(
                record.get("debit", defaults.get("debit")),
                f"Ingest record {index} debit",
            )
            credit = _ingest_string(
                record.get("credit", defaults.get("credit")),
                f"Ingest record {index} credit",
            )
            if debit == "auto":
                raise ValueError(f"Ingest record {index} kind {kind} cannot use debit auto")
        if debit != "auto":
            debit = _validate_account(debit)
        credit = _validate_account(credit)
        if kind == "opening-balance":
            debit_is_asset = debit == "assets" or debit.startswith("assets:")
            debit_is_opening_equity = _is_opening_equity(debit)
            credit_is_liability = credit == "liabilities" or credit.startswith("liabilities:")
            credit_is_opening_equity = _is_opening_equity(credit)
            if not (
                (debit_is_asset and credit_is_opening_equity)
                or (debit_is_opening_equity and credit_is_liability)
            ):
                raise ValueError(
                    f"Ingest record {index} opening asset must debit assets and credit equity:opening-balances; "
                    "opening liability must debit equity:opening-balances and credit liabilities"
                )
        elif debit != "auto":
            _validate_kind_accounts(kind, debit, credit, f"Ingest record {index}")

        count_value = record.get("installments", defaults.get("installments", 1))
        if isinstance(count_value, bool) or not isinstance(count_value, int) or count_value < 1:
            raise ValueError(f"Ingest record {index} installments must be a positive integer")
        count = count_value
        try:
            fee = Decimal(str(record.get("fee", defaults.get("fee", "0"))))
        except InvalidOperation as error:
            raise ValueError(f"Ingest record {index} has an invalid fee") from error
        if not fee.is_finite():
            raise ValueError(f"Ingest record {index} fee must be finite")
        if kind == "opening-balance" and (count != 1 or fee != 0):
            raise ValueError(
                f"Ingest record {index} opening-balance requires installments=1 and fees=0"
            )
        fee_account = _validate_account(
            _ingest_string(
                record.get("fee_account", defaults.get("fee_account", "expenses:fees")),
                f"Ingest record {index} fee_account",
            )
        )
        if fee < 0:
            raise ValueError(f"Ingest record {index} fee must not be negative")
        if fee and _account_family(fee_account) != "expenses":
            raise ValueError(f"Ingest record {index} fee_account must be an expenses account")

        default_tags = defaults.get("tags", [])
        record_tags = record.get("tags", [])
        if not isinstance(default_tags, list) or not isinstance(record_tags, list):
            raise ValueError(f"Ingest record {index} tags must be lists")
        tags = list(
            dict.fromkeys(
                _validate_tag(_ingest_string(tag, f"Ingest record {index} tag"))
                for tag in [*default_tags, *record_tags]
            )
        )
        if not has_date:
            tags.append("inferred:date")
        if "credit" not in record and "credit" not in defaults:
            tags.append("inferred:payment-account")
        if "currency" not in record and "currency" not in defaults:
            tags.append("inferred:currency")
        source_tags = [tag for tag in tags if tag.startswith("source:")]
        if len(source_tags) != 1 or source_tags[0] not in APPROVED_SOURCE_TAGS:
            raise ValueError(
                f"Ingest record {index} requires exactly one approved source tag"
            )
        reserved_tags = [
            tag for tag in tags if tag.startswith("kind:") or tag.startswith("import-id:")
        ]
        if reserved_tags:
            raise ValueError(
                f"Ingest record {index} uses reserved tag {reserved_tags[0]!r}; use kind/import_id fields"
            )
        if source_tags[0] in HISTORICAL_SOURCE_TAGS and not import_id:
            raise ValueError(
                f"Ingest record {index} historical imports require import_id"
            )
        if source_tags[0] in HISTORICAL_SOURCE_TAGS and (
            debit == "auto" or any(tag.startswith("inferred:") for tag in tags)
        ):
            raise ValueError(
                f"Ingest record {index} historical imports require explicit date, currency, and accounts"
            )
        if import_id and import_id in seen_ids:
            skipped += 1
            continue
        tags.append(f"kind:{kind}")
        if import_id:
            tags.append(f"import-id:{import_id}")

        if debit == "auto":
            decision = classify_description(
                description,
                transactions=classification_transactions,
                rules=classification_rules,
            )
            debit = str(decision["account"])
        else:
            decision = {"account": debit, "source": "explicit", "confidence": 1.0, "matched": description}
        if kind != "opening-balance":
            _validate_kind_accounts(kind, debit, credit, f"Ingest record {index}")
        decisions.append({"record": index, "description": description, **decision})
        output.append(
            render_installments(
                start=txn_date,
                description=description,
                total=amount,
                count=count,
                debit_account=debit,
                credit_account=credit,
                currency=currency,
                fee_per_installment=fee,
                fee_account=fee_account,
                tags=tags,
            )
        )
        imported += 1
        if import_id:
            seen_ids.add(import_id)

    return "".join(output), imported, skipped, decisions


def import_csv_transactions(
    *,
    rows: Iterable[dict[str, str]],
    existing_text: str,
    default_debit: str,
    credit_account: str,
    currency: str,
    date_column: str,
    description_column: str,
    amount_column: str,
    id_column: str,
    installment_column: str | None = None,
    category_column: str | None = None,
    default_installments: int = 1,
    category_prefix: str = "expenses",
    classification_transactions: Sequence[dict] = (),
    classification_rules: Sequence[dict[str, str]] = (),
) -> tuple[str, int, int]:
    if default_debit != "auto":
        default_debit = _validate_account(default_debit)
        if _account_family(default_debit) != "expenses":
            raise ValueError("CSV fallback debit must be an expenses account")
    category_prefix = _validate_account(category_prefix)
    if _account_family(category_prefix) != "expenses":
        raise ValueError("CSV category prefix must be in the expenses account family")
    credit_account = _validate_account(credit_account)
    if _account_family(credit_account) not in {"assets", "liabilities"}:
        raise ValueError("CSV credit must be an asset or liability account")
    currency = _validate_currency(currency)

    output: list[str] = []
    imported = 0
    skipped = 0
    seen_ids = _existing_import_ids(existing_text)
    for index, row in enumerate(rows, start=2):
        import_id = _validate_import_id(
            row.get(id_column, ""),
            f"CSV row {index} import ID",
        )
        if not import_id:
            raise ValueError(f"CSV row {index} requires a non-empty import ID")
        try:
            txn_date = date.fromisoformat(row[date_column].strip())
            description = _validate_description(row[description_column])
            amount = Decimal(row[amount_column].strip())
            if not amount.is_finite():
                raise ValueError("amount must be finite")
            if amount <= 0:
                raise ValueError(
                    "amount must be a positive expense amount; split income/refunds or normalize direction first"
                )
        except (KeyError, AttributeError, ValueError, InvalidOperation) as error:
            raise ValueError(f"Invalid CSV row {index}: {error}") from error
        count_text = (row.get(installment_column, "") if installment_column else "").strip()
        try:
            count = int(count_text) if count_text else default_installments
        except ValueError as error:
            raise ValueError(f"Invalid installment count on CSV row {index}") from error
        if count < 1:
            raise ValueError(f"Invalid installment count on CSV row {index}")
        category = (row.get(category_column, "") if category_column else "").strip()
        debit = f"{category_prefix}:{category}" if category else default_debit
        if debit != "auto":
            debit = _validate_account(debit)
            if _account_family(debit) != "expenses":
                raise ValueError(f"CSV row {index} debit must be an expenses account")
        if import_id in seen_ids:
            skipped += 1
            continue
        if debit == "auto":
            debit = classify_description(
                description,
                transactions=classification_transactions,
                rules=classification_rules,
            )["account"]
        tags = ["source:structured-csv", "kind:expense", f"import-id:{import_id}"]
        output.append(
            render_installments(
                start=txn_date,
                description=description,
                total=amount,
                count=count,
                debit_account=debit,
                credit_account=credit_account,
                currency=currency,
                tags=tags,
            )
        )
        imported += 1
        if import_id:
            seen_ids.add(import_id)
    return "".join(output), imported, skipped


def _ensure_journal(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"Journal does not exist: {path}. Run init first.")


def _run(command: Sequence[str], *, capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(command, text=True, capture_output=capture, check=False)


def _combined_journal_text(existing: str, addition: str) -> str:
    separator = "\n" if existing and not existing.endswith("\n") else ""
    return existing + separator + addition


def _validate_journal_text(text: str, *, base_dir: Path | None = None) -> None:
    with tempfile.NamedTemporaryFile(
        "w",
        dir=base_dir,
        prefix=".hfin-candidate-",
        suffix=".tmp",
        encoding="utf-8",
        delete=False,
    ) as handle:
        handle.write(text)
        candidate = Path(handle.name)
    try:
        result = _run(["hledger", "-f", str(candidate), "check"], capture=True)
        if result.returncode:
            raise ValueError(result.stderr.strip() or "hledger validation failed")
    finally:
        candidate.unlink(missing_ok=True)


def _validate_candidate(journal: Path, addition: str) -> None:
    existing = journal.read_text(encoding="utf-8") if journal.exists() else ""
    _validate_journal_text(
        _combined_journal_text(existing, addition),
        base_dir=journal.parent,
    )


def _atomic_replace_text(path: Path, text: str) -> None:
    if path.exists() or path.is_symlink():
        _require_regular_journal(path)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists():
            os.chmod(temporary, path.stat().st_mode & 0o777)
        if path.exists() or path.is_symlink():
            _require_regular_journal(path)
        os.replace(temporary, path)
        directory_descriptor = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def _acquire_journal_lock(journal: Path):
    identity = hashlib.sha256(str(journal.resolve()).encode("utf-8")).hexdigest()[:20]
    lock_path = Path(tempfile.gettempdir()) / f"hfin-{identity}.lock"
    handle = lock_path.open("a+", encoding="utf-8")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    return handle


def _require_regular_journal(journal: Path) -> None:
    if journal.is_symlink():
        raise ValueError("Journal symlinks are not supported for atomic writes")
    if not stat.S_ISREG(journal.lstat().st_mode):
        raise ValueError("Journal must be a regular file for atomic writes")


def _reject_dirty_journal(journal: Path) -> None:
    book_dir = journal.parent
    if not (book_dir / ".git").exists():
        return
    relative = str(journal.relative_to(book_dir))
    tracked = subprocess.run(
        ["git", "-C", str(book_dir), "ls-files", "--error-unmatch", "--", relative],
        check=False,
        capture_output=True,
        text=True,
    )
    if tracked.returncode:
        raise ValueError("Refusing to ingest an existing untracked journal; run hfin init first")
    result = subprocess.run(
        ["git", "-C", str(book_dir), "diff", "--quiet", "--", relative],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode == 1:
        raise ValueError("Refusing to ingest while the journal has unstaged Git changes")
    if result.returncode:
        raise subprocess.CalledProcessError(result.returncode, result.args, result.stdout, result.stderr)


def _reject_staged_changes(book_dir: Path) -> None:
    if not (book_dir / ".git").exists():
        return
    result = subprocess.run(
        ["git", "-C", str(book_dir), "diff", "--cached", "--quiet"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode == 1:
        raise ValueError("Refusing to commit while unrelated staged Git changes exist")
    if result.returncode:
        raise subprocess.CalledProcessError(result.returncode, result.args, result.stdout, result.stderr)


def append_validated(journal: Path, addition: str) -> None:
    _ensure_journal(journal)
    _require_regular_journal(journal)
    with _acquire_journal_lock(journal):
        _require_regular_journal(journal)
        existing = journal.read_text(encoding="utf-8")
        candidate = _combined_journal_text(existing, addition)
        _validate_journal_text(candidate, base_dir=journal.parent)
        _atomic_replace_text(journal, candidate)


def _git_commit(book_dir: Path, message: str, paths: Sequence[Path] | None = None) -> None:
    if not (book_dir / ".git").exists():
        return
    targets = [str(path.relative_to(book_dir)) for path in paths] if paths else ["."]
    subprocess.run(["git", "-C", str(book_dir), "add", "--", *targets], check=True)
    diff = subprocess.run(["git", "-C", str(book_dir), "diff", "--cached", "--quiet"], check=False)
    if diff.returncode:
        subprocess.run(["git", "-C", str(book_dir), "commit", "-m", message], check=True)


def _replace_validated_and_commit_locked(journal: Path, replacement: str, message: str) -> None:
    _require_regular_journal(journal)
    _reject_staged_changes(journal.parent)
    _reject_dirty_journal(journal)
    existing = journal.read_text(encoding="utf-8")
    _validate_journal_text(replacement, base_dir=journal.parent)
    _atomic_replace_text(journal, replacement)
    try:
        _git_commit(journal.parent, message, paths=[journal])
    except Exception:
        _atomic_replace_text(journal, existing)
        if (journal.parent / ".git").exists():
            relative = str(journal.relative_to(journal.parent))
            subprocess.run(
                ["git", "-C", str(journal.parent), "reset", "--quiet", "HEAD", "--", relative],
                check=False,
                capture_output=True,
            )
        raise


def _append_validated_and_commit_locked(journal: Path, addition: str, message: str) -> None:
    existing = journal.read_text(encoding="utf-8")
    candidate = _combined_journal_text(existing, addition)
    _replace_validated_and_commit_locked(journal, candidate, message)


def _append_validated_and_commit(journal: Path, addition: str, message: str) -> None:
    _ensure_journal(journal)
    _require_regular_journal(journal)
    with _acquire_journal_lock(journal):
        _append_validated_and_commit_locked(journal, addition, message)


def _resolve_period(args: argparse.Namespace) -> tuple[date | None, date | None]:
    if getattr(args, "period", None):
        return parse_period(args.period)
    begin = date.fromisoformat(args.begin) if getattr(args, "begin", None) else None
    end = date.fromisoformat(args.end) + timedelta(days=1) if getattr(args, "end", None) else None
    return begin, end


def _print_preview_or_append(args: argparse.Namespace, journal_text: str, message: str) -> int:
    if args.preview:
        print(journal_text, end="")
        print("Preview only; nothing was written.", file=sys.stderr)
        return 0
    _append_validated_and_commit(args.journal, journal_text, message)
    print(f"Appended to {args.journal}")
    return 0


def command_init(args: argparse.Namespace) -> int:
    book_dir = args.journal.parent
    book_dir.mkdir(parents=True, exist_ok=True)
    with _acquire_journal_lock(args.journal):
        if args.journal.is_symlink():
            raise ValueError("Journal symlinks are not supported for atomic writes")
        if args.journal.exists():
            _require_regular_journal(args.journal)
        else:
            _atomic_replace_text(
                args.journal,
                "; hledger personal finance journal\n"
                "; Managed through the hledger-finance skill.\n\n",
            )
        if not (book_dir / ".git").exists():
            subprocess.run(["git", "init", str(book_dir)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(book_dir), "config", "user.name", "Finance Skill"], check=True)
            subprocess.run(["git", "-C", str(book_dir), "config", "user.email", "finance-skill@localhost"], check=True)
        _reject_staged_changes(book_dir)
        _git_commit(book_dir, "Initialize hledger journal", paths=[args.journal])
    print(args.journal)
    return 0


def _resolve_debit_account(journal: Path, description: str, debit: str | None) -> str:
    if debit and debit != "auto":
        return debit
    _ensure_journal(journal)
    decision = _auto_debit(journal, description)
    print(
        f"Auto-category: {decision['account']} "
        f"(source={decision['source']}, confidence={decision['confidence']:.2f})",
        file=sys.stderr,
    )
    return str(decision["account"])


def command_classify(args: argparse.Namespace) -> int:
    _ensure_journal(args.journal)
    decision = _auto_debit(args.journal, args.description)
    print(json.dumps(decision, ensure_ascii=False, indent=2))
    return 0


def command_add(args: argparse.Namespace) -> int:
    debit = _resolve_debit_account(args.journal, args.description, args.debit)
    transaction = render_transaction(
        date.fromisoformat(args.date),
        args.description,
        debit,
        args.credit,
        Decimal(args.amount),
        args.currency,
        tags=args.tag,
    )
    return _print_preview_or_append(args, transaction, f"Add transaction: {args.description}")


def command_installment(args: argparse.Namespace) -> int:
    debit = _resolve_debit_account(args.journal, args.description, args.debit)
    transaction = render_installments(
        start=date.fromisoformat(args.start),
        description=args.description,
        total=Decimal(args.total),
        count=args.count,
        debit_account=debit,
        credit_account=args.credit,
        currency=args.currency,
        fee_per_installment=Decimal(args.fee),
        fee_account=args.fee_account,
        tags=args.tag,
    )
    return _print_preview_or_append(args, transaction, f"Add {args.count} installments: {args.description}")


def _read_active_journal_text(journal: Path) -> str:
    result = _run(["hledger", "-f", str(journal), "files"], capture=True)
    if result.returncode:
        raise ValueError(result.stderr.strip() or "Could not list active journal files")
    source_texts: list[str] = []
    for filename in result.stdout.splitlines():
        if not filename.strip():
            continue
        try:
            source_texts.append(Path(filename).read_text(encoding="utf-8"))
        except OSError as error:
            raise ValueError(f"Could not read journal source {filename}: {error}") from error
    return f"\n{_SOURCE_BOUNDARY_SENTINEL}\n".join(source_texts)


def command_ingest_json(args: argparse.Namespace) -> int:
    _ensure_journal(args.journal)
    _require_regular_journal(args.journal)
    try:
        raw = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8")
    except OSError as error:
        raise ValueError(f"Could not read ingest JSON: {error}") from error
    payload = parse_ingest_json(raw)
    with _acquire_journal_lock(args.journal):
        _require_regular_journal(args.journal)
        journal_text, imported, skipped, decisions = build_ingest_transactions(
            payload,
            existing_text=_read_active_journal_text(args.journal),
            classification_transactions=_load_classification_history(args.journal),
            classification_rules=_load_classification_rules(args.journal),
        )
        if journal_text and not args.preview:
            _append_validated_and_commit_locked(
                args.journal,
                journal_text,
                f"Ingest {imported} normalized transactions",
            )
    for decision in decisions:
        print(
            f"Record {decision['record']}: {decision['account']} "
            f"(source={decision['source']}, confidence={decision['confidence']:.2f})",
            file=sys.stderr,
        )
    print(f"Records ready: {imported}; duplicates skipped: {skipped}", file=sys.stderr)
    if not journal_text:
        return 0
    if args.preview:
        print(journal_text, end="")
        print("Preview only; nothing was written.", file=sys.stderr)
    else:
        print(f"Appended to {args.journal}")
    return 0


def command_import_csv(args: argparse.Namespace) -> int:
    _ensure_journal(args.journal)
    _require_regular_journal(args.journal)
    with args.file.open(newline="", encoding=args.encoding) as handle:
        rows = list(csv.DictReader(handle))
    with _acquire_journal_lock(args.journal):
        _require_regular_journal(args.journal)
        journal_text, imported, skipped = import_csv_transactions(
            rows=rows,
            existing_text=_read_active_journal_text(args.journal),
            default_debit=args.debit,
            credit_account=args.credit,
            currency=args.currency,
            date_column=args.date_column,
            description_column=args.description_column,
            amount_column=args.amount_column,
            id_column=args.id_column,
            installment_column=args.installment_column,
            category_column=args.category_column,
            default_installments=args.installments,
            category_prefix=args.category_prefix,
            classification_transactions=_load_classification_history(args.journal),
            classification_rules=_load_classification_rules(args.journal),
        )
        if journal_text and not args.preview:
            _append_validated_and_commit_locked(
                args.journal,
                journal_text,
                f"Import {imported} CSV transactions",
            )
    print(f"Rows ready: {imported}; duplicates skipped: {skipped}", file=sys.stderr)
    if not journal_text:
        return 0
    if args.preview:
        print(journal_text, end="")
        print("Preview only; nothing was written.", file=sys.stderr)
    else:
        print(f"Appended to {args.journal}")
    return 0


def command_query(args: argparse.Namespace) -> int:
    _ensure_journal(args.journal)
    begin, end = _resolve_period(args)
    command = build_hledger_query(
        journal=args.journal,
        report=args.report,
        begin=begin,
        end=end,
        accounts=args.account,
        descriptions=args.description,
        tags=args.tag,
        raw_terms=args.where,
        interval=args.interval,
        output_format=args.format,
    )
    if args.show_command:
        print(shlex.join(command), file=sys.stderr)
    return subprocess.run(command, check=False).returncode


def command_audit(args: argparse.Namespace) -> int:
    _ensure_journal(args.journal)
    _require_regular_journal(args.journal)
    with _acquire_journal_lock(args.journal):
        _require_regular_journal(args.journal)
        active_journal_text = _read_active_journal_text(args.journal)
        result = _run(
            ["hledger", "-f", str(args.journal), "print", "--output-format=json"],
            capture=True,
        )
        if result.returncode:
            raise ValueError(result.stderr.strip() or "Could not read journal for audit")
        transactions = json.loads(result.stdout)
    report = audit_journal(transactions, active_journal_text)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["critical_count"] or (args.strict and report["warning_count"]):
        return 2
    return 0


def command_stats(args: argparse.Namespace) -> int:
    _ensure_journal(args.journal)
    begin, end = _resolve_period(args)
    command = ["hledger", "-f", str(args.journal), "print", "--output-format=json"]
    if begin:
        command.append(f"--begin={begin.isoformat()}")
    if end:
        command.append(f"--end={end.isoformat()}")
    command.extend(args.where)
    result = _run(command, capture=True)
    if result.returncode:
        print(result.stderr, file=sys.stderr)
        return result.returncode
    stats = compute_stats(json.loads(result.stdout))
    stats["period"] = {
        "begin": begin.isoformat() if begin else None,
        "end_exclusive": end.isoformat() if end else None,
    }
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


def command_visualize(args: argparse.Namespace) -> int:
    _ensure_journal(args.journal)
    begin, end = _resolve_period(args)
    command = build_hledger_query(
        journal=args.journal,
        report="transactions",
        begin=begin,
        end=end,
        accounts=args.account,
        descriptions=args.description,
        tags=args.tag,
        raw_terms=args.where,
        output_format="json",
    )
    result = _run(command, capture=True)
    if result.returncode:
        print(result.stderr, file=sys.stderr)
        return result.returncode
    transactions = json.loads(result.stdout)
    data = build_visualization_data(transactions, currency=args.currency)
    if args.output:
        output = args.output.expanduser().resolve()
    else:
        begin_text = begin.isoformat() if begin else "start"
        end_text = (end - timedelta(days=1)).isoformat() if end else "latest"
        output = (args.journal.parent / "reports" / f"finance-{begin_text}-{end_text}-{data['currency']}.png").resolve()
    if begin and end:
        period_label = f"{begin.isoformat()} ～ {(end - timedelta(days=1)).isoformat()}"
    elif begin:
        period_label = f"{begin.isoformat()} 起"
    elif end:
        period_label = f"截至 {(end - timedelta(days=1)).isoformat()}"
    else:
        period_label = "全部期間"
    render_dashboard_png(data, output, title=args.title, period_label=period_label)
    print(output)
    return 0


def command_check(args: argparse.Namespace) -> int:
    _ensure_journal(args.journal)
    return subprocess.run(["hledger", "-f", str(args.journal), "check"], check=False).returncode


def _delete_spans(journal: Path, transactions: Sequence[dict]) -> list[tuple[int, int, dict]]:
    spans: list[tuple[int, int, dict]] = []
    resolved_journal = journal.resolve()
    for transaction in transactions:
        positions = transaction.get("tsourcepos") or []
        if len(positions) != 2:
            raise ValueError("A matched transaction has no deletable source location")
        source = Path(positions[0]["sourceName"]).resolve()
        if source != resolved_journal:
            raise ValueError(f"Matched transaction is stored in an included file: {source}")
        start = int(positions[0]["sourceLine"])
        end = int(positions[1]["sourceLine"])
        if start < 1 or end <= start:
            raise ValueError("A matched transaction has an invalid source range")
        spans.append((start, end, transaction))
    spans.sort(key=lambda item: item[0])
    for previous, current in zip(spans, spans[1:]):
        if previous[1] > current[0]:
            raise ValueError("Matched transaction source ranges overlap")
    return spans


def _delete_token(journal: Path, spans: Sequence[tuple[int, int, dict]]) -> str:
    digest = hashlib.sha256()
    digest.update(journal.read_bytes())
    identity = [
        [start, end, transaction.get("tdate"), transaction.get("tdescription"), transaction.get("tindex")]
        for start, end, transaction in spans
    ]
    digest.update(json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    return digest.hexdigest()[:16]


def _posting_summary(transaction: dict) -> str:
    parts: list[str] = []
    for posting in transaction.get("tpostings", []):
        amounts = []
        for amount in posting.get("pamount", []):
            amounts.append(f"{_decimal_text(_amount_decimal(amount))} {amount.get('acommodity') or ''}".rstrip())
        parts.append(f"{posting.get('paccount', '?')}={' + '.join(amounts) or '?'}")
    return "; ".join(parts)


def command_delete(args: argparse.Namespace) -> int:
    _ensure_journal(args.journal)
    _require_regular_journal(args.journal)
    with _acquire_journal_lock(args.journal):
        return _command_delete_locked(args)


def _command_delete_locked(args: argparse.Namespace) -> int:
    _require_regular_journal(args.journal)
    selectors = [args.period, args.begin, args.end, *args.account, *args.description, *args.tag, *args.where]
    if not any(selectors):
        raise ValueError("Deletion requires at least one date, account, description, tag, or raw query filter")
    begin, end = _resolve_period(args)
    command = build_hledger_query(
        journal=args.journal,
        report="transactions",
        begin=begin,
        end=end,
        accounts=args.account,
        descriptions=args.description,
        tags=args.tag,
        raw_terms=args.where,
        output_format="json",
    )
    result = _run(command, capture=True)
    if result.returncode:
        raise ValueError(result.stderr.strip() or "hledger search failed")
    transactions = json.loads(result.stdout)
    if not transactions:
        raise ValueError("No transactions matched; nothing was deleted")
    spans = _delete_spans(args.journal, transactions)
    token = _delete_token(args.journal, spans)
    print(f"Delete candidates: {len(spans)}")
    for number, (start, end, transaction) in enumerate(spans, start=1):
        print(
            f"[{number}] {transaction.get('tdate')} {transaction.get('tdescription')} "
            f"(lines {start}-{end - 1})"
        )
        print(f"    {_posting_summary(transaction)}")
    print(f"Confirmation token: {token}")
    if not args.confirm:
        print("Nothing was deleted. Repeat the same command with --confirm TOKEN.", file=sys.stderr)
        return 3
    if args.confirm != token:
        raise ValueError("Confirmation token does not match the current journal and candidate set")
    lines = args.journal.read_text(encoding="utf-8").splitlines(keepends=True)
    for start, end, _ in reversed(spans):
        del lines[start - 1 : end - 1]
    replacement = "".join(lines)
    descriptions = ", ".join(transaction.get("tdescription", "") for _, _, transaction in spans[:3])
    if len(spans) > 3:
        descriptions += ", ..."
    _replace_validated_and_commit_locked(
        args.journal,
        replacement,
        f"Delete {len(spans)} transaction(s): {descriptions}",
    )
    print(f"Deleted {len(spans)} transaction(s). Use 'hfin undo' to restore them.")
    return 0


def command_undo(args: argparse.Namespace) -> int:
    _ensure_journal(args.journal)
    _require_regular_journal(args.journal)
    with _acquire_journal_lock(args.journal):
        return _command_undo_locked(args)


def _command_undo_locked(args: argparse.Namespace) -> int:
    _require_regular_journal(args.journal)
    book_dir = args.journal.parent
    _reject_staged_changes(book_dir)
    _reject_dirty_journal(args.journal)
    if not (book_dir / ".git").exists():
        raise ValueError("The finance folder is not a Git repository")
    try:
        relative_journal = args.journal.relative_to(book_dir)
    except ValueError as error:
        raise ValueError("Journal must be inside its Git repository") from error
    latest = subprocess.run(
        [
            "git",
            "-C",
            str(book_dir),
            "log",
            "-1",
            "--format=%H%x00%s",
            "--",
            str(relative_journal),
        ],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    if not latest or "\x00" not in latest:
        raise ValueError("No journal action is available to undo")
    commit_hash, subject = latest.split("\x00", 1)
    if subject == "Initialize hledger journal":
        raise ValueError("Refusing to undo journal initialization")
    reverted = subprocess.run(
        ["git", "-C", str(book_dir), "revert", "--no-commit", commit_hash],
        text=True,
        capture_output=True,
        check=False,
    )
    if reverted.returncode:
        subprocess.run(["git", "-C", str(book_dir), "revert", "--abort"], check=False, capture_output=True)
        raise ValueError(reverted.stderr.strip() or "Git could not undo the last action")
    validation = _run(["hledger", "-f", str(args.journal), "check"], capture=True)
    if validation.returncode:
        subprocess.run(["git", "-C", str(book_dir), "revert", "--abort"], check=False, capture_output=True)
        raise ValueError(validation.stderr.strip() or "Undo would make the journal invalid")
    subprocess.run(
        ["git", "-C", str(book_dir), "commit", "-m", f"Undo finance action: {subject}"],
        check=True,
        capture_output=True,
    )
    print(f"Undid: {subject}")
    return 0


def command_backup(args: argparse.Namespace) -> int:
    _ensure_journal(args.journal)
    _require_regular_journal(args.journal)
    with _acquire_journal_lock(args.journal):
        return _command_backup_locked(args)


def _command_backup_locked(args: argparse.Namespace) -> int:
    _require_regular_journal(args.journal)
    _git_commit(args.journal.parent, args.message)
    remotes = subprocess.run(["rclone", "listremotes"], text=True, capture_output=True, check=False)
    remote_name = args.remote.split(":", 1)[0] + ":"
    if remote_name not in remotes.stdout.splitlines():
        print(
            f"rclone remote {remote_name} is not configured. Run: rclone config",
            file=sys.stderr,
        )
        return 2
    return subprocess.run(
        ["rclone", "copy", str(args.journal.parent), args.remote, "--create-empty-src-dirs", "--progress"],
        check=False,
    ).returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Flexible, validated hledger workflows")
    parser.add_argument(
        "--journal",
        type=Path,
        default=Path(os.environ.get("FINANCE_JOURNAL", "~/finance/main.journal")).expanduser(),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Create a version-controlled journal")
    init_parser.set_defaults(func=command_init)

    add_parser = subparsers.add_parser("add", help="Add a generic balanced transaction")
    add_parser.add_argument("--date", required=True)
    add_parser.add_argument("--description", required=True)
    add_parser.add_argument("--amount", required=True)
    add_parser.add_argument("--currency", default="TWD")
    add_parser.add_argument("--debit", required=True, help="Debit account; use 'auto' to classify an expense")
    add_parser.add_argument("--credit", required=True)
    add_parser.add_argument("--tag", action="append", default=[])
    add_parser.add_argument("--preview", action="store_true", help="Show entries without writing")
    add_parser.add_argument("--yes", action="store_true", help=argparse.SUPPRESS)
    add_parser.set_defaults(func=command_add)

    classify_parser = subparsers.add_parser("classify", help="Suggest an expense category from rules and history")
    classify_parser.add_argument("--description", required=True)
    classify_parser.set_defaults(func=command_classify)

    installment_parser = subparsers.add_parser("installment", help="Create N exact monthly installments")
    installment_parser.add_argument("--start", required=True)
    installment_parser.add_argument("--description", required=True)
    installment_parser.add_argument("--total", required=True)
    installment_parser.add_argument("--count", type=int, required=True)
    installment_parser.add_argument("--currency", default="TWD")
    installment_parser.add_argument("--debit", default="auto", help="Debit account; default auto-classifies expenses")
    installment_parser.add_argument("--credit", required=True)
    installment_parser.add_argument("--fee", default="0", help="Fee per installment")
    installment_parser.add_argument("--fee-account", default="expenses:fees")
    installment_parser.add_argument("--tag", action="append", default=[])
    installment_parser.add_argument("--preview", action="store_true", help="Show entries without writing")
    installment_parser.add_argument("--yes", action="store_true", help=argparse.SUPPRESS)
    installment_parser.set_defaults(func=command_installment)

    ingest_parser = subparsers.add_parser(
        "ingest-json",
        help="Atomically write agent-normalized fuzzy text, image, or tabular records",
    )
    ingest_parser.add_argument("file", help="Normalized JSON file, or - for stdin")
    ingest_parser.add_argument("--preview", action="store_true", help="Show entries without writing")
    ingest_parser.set_defaults(func=command_ingest_json)

    import_parser = subparsers.add_parser("import-csv", help="Import CSV, optionally splitting rows into installments")
    import_parser.add_argument("file", type=Path)
    import_parser.add_argument("--encoding", default="utf-8-sig")
    import_parser.add_argument("--date-column", default="date")
    import_parser.add_argument("--description-column", default="description")
    import_parser.add_argument("--amount-column", default="amount")
    import_parser.add_argument("--id-column", required=True)
    import_parser.add_argument("--installment-column")
    import_parser.add_argument("--category-column")
    import_parser.add_argument("--category-prefix", default="expenses")
    import_parser.add_argument("--installments", type=int, default=1)
    import_parser.add_argument("--debit", default="auto", help="Fallback debit; default auto-classifies each description")
    import_parser.add_argument("--credit", required=True)
    import_parser.add_argument("--currency", default="TWD")
    import_parser.add_argument("--preview", action="store_true", help="Show entries without writing")
    import_parser.add_argument("--yes", action="store_true", help=argparse.SUPPRESS)
    import_parser.set_defaults(func=command_import_csv)

    query_parser = subparsers.add_parser("query", help="Run arbitrary filtered hledger reports")
    query_parser.add_argument(
        "--report",
        choices=["transactions", "register", "balance", "income-statement", "balance-sheet", "cashflow", "budget", "stats"],
        default="transactions",
    )
    query_parser.add_argument("--period")
    query_parser.add_argument("--begin")
    query_parser.add_argument("--end", help="Inclusive end date")
    query_parser.add_argument("--account", action="append", default=[])
    query_parser.add_argument("--description", action="append", default=[])
    query_parser.add_argument("--tag", action="append", default=[])
    query_parser.add_argument("--where", action="append", default=[], help="Raw hledger query term")
    query_parser.add_argument("--interval", choices=["daily", "weekly", "monthly", "quarterly", "yearly"])
    query_parser.add_argument("--format", choices=["txt", "csv", "json", "html"])
    query_parser.add_argument("--show-command", action="store_true")
    query_parser.set_defaults(func=command_query)

    stats_parser = subparsers.add_parser("stats", help="Income, expense, net, and category totals as JSON")
    stats_parser.add_argument("--period")
    stats_parser.add_argument("--begin")
    stats_parser.add_argument("--end", help="Inclusive end date")
    stats_parser.add_argument("--where", action="append", default=[])
    stats_parser.set_defaults(func=command_stats)

    visualize_parser = subparsers.add_parser("visualize", help="Render a Japanese-palette financial dashboard PNG")
    visualize_parser.add_argument("--period")
    visualize_parser.add_argument("--begin")
    visualize_parser.add_argument("--end", help="Inclusive end date")
    visualize_parser.add_argument("--account", action="append", default=[])
    visualize_parser.add_argument("--description", action="append", default=[])
    visualize_parser.add_argument("--tag", action="append", default=[])
    visualize_parser.add_argument("--where", action="append", default=[], help="Raw hledger query term")
    visualize_parser.add_argument("--currency", help="Required when matching data contains multiple currencies")
    visualize_parser.add_argument("--title", default="財務分析")
    visualize_parser.add_argument("--output", type=Path)
    visualize_parser.set_defaults(func=command_visualize)

    check_parser = subparsers.add_parser("check", help="Validate the journal")
    check_parser.set_defaults(func=command_check)

    audit_parser = subparsers.add_parser(
        "audit",
        help="Detect semantic sign, opening-balance, and historical-import hazards",
    )
    audit_parser.add_argument(
        "--strict",
        action="store_true",
        help="Also return a failure status when warnings are present",
    )
    audit_parser.set_defaults(func=command_audit)

    delete_parser = subparsers.add_parser("delete", help="Delete matching transactions after token confirmation")
    delete_parser.add_argument("--period")
    delete_parser.add_argument("--begin")
    delete_parser.add_argument("--end", help="Inclusive end date")
    delete_parser.add_argument("--account", action="append", default=[])
    delete_parser.add_argument("--description", action="append", default=[])
    delete_parser.add_argument("--tag", action="append", default=[])
    delete_parser.add_argument("--where", action="append", default=[], help="Raw hledger query term")
    delete_parser.add_argument("--confirm", help="Token printed by an identical unconfirmed delete command")
    delete_parser.set_defaults(func=command_delete)

    undo_parser = subparsers.add_parser("undo", help="Revert the latest journal-changing action")
    undo_parser.set_defaults(func=command_undo)

    backup_parser = subparsers.add_parser("backup", help="Commit and copy the finance folder with rclone")
    backup_parser.add_argument("--remote", default="gdrive:finance-backup")
    backup_parser.add_argument("--message", default="Finance backup")
    backup_parser.set_defaults(func=command_backup)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (ValueError, FileNotFoundError, subprocess.CalledProcessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
