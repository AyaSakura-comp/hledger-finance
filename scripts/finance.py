#!/usr/bin/env python3
"""Deterministic hledger helper used by the hledger-finance agent skill."""

from __future__ import annotations

import argparse
import calendar
import csv
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
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


def _validate_account(account: str) -> None:
    if not account.strip() or "\n" in account or "\r" in account:
        raise ValueError("Invalid account name")


def _validate_text(value: str, label: str) -> str:
    cleaned = " ".join(value.splitlines()).strip()
    if not cleaned:
        raise ValueError(f"{label} must not be empty")
    return cleaned


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
    _validate_account(debit_account)
    _validate_account(credit_account)
    description = _validate_text(description, "Description")
    currency = _validate_text(currency, "Currency")
    if amount <= 0:
        raise ValueError("Amount must be positive")
    total_credit = amount + sum((value for _, value in extra_postings), Decimal("0"))
    lines = [f"{txn_date.isoformat()} {description}"]
    for tag in tags:
        lines.append(f"    ; {_validate_text(tag, 'Tag')}")
    lines.append(f"    {debit_account:<40} {_decimal_text(amount)} {currency}")
    for account, value in extra_postings:
        _validate_account(account)
        if value:
            lines.append(f"    {account:<40} {_decimal_text(value)} {currency}")
    lines.append(f"    {credit_account:<40} -{_decimal_text(total_credit)} {currency}")
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
                    expenses[currency] += value
                    expense_categories[account][currency] += value
    currencies = {}
    for currency in sorted(set(incomes) | set(expenses)):
        income = incomes[currency]
        expense = expenses[currency]
        currencies[currency] = {
            "income": _decimal_text(income),
            "expense": _decimal_text(expense),
            "net": _decimal_text(income - expense),
        }

    def serialize_categories(values: dict[str, dict[str, Decimal]]) -> dict:
        return {
            account: {currency: _decimal_text(amount) for currency, amount in sorted(amounts.items())}
            for account, amounts in sorted(values.items())
        }

    return {
        "transaction_count": transaction_count,
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
                    reported = -value
                    months[month]["income"] += reported
                    income_categories[account] += reported
                    daily_net[txn_date] += reported
                    matched = True
                elif account == "expenses" or account.startswith("expenses:"):
                    months[month]["expense"] += value
                    expense_categories[account] += value
                    daily_net[txn_date] -= value
                    matched = True
        if matched:
            matched_transactions += 1

    total_income = sum((values["income"] for values in months.values()), Decimal("0"))
    total_expense = sum((values["expense"] for values in months.values()), Decimal("0"))
    running = Decimal("0")
    cumulative_net: list[tuple[str, Decimal]] = []
    for txn_date, value in sorted(daily_net.items()):
        running += value
        cumulative_net.append((txn_date, running))
    return {
        "currency": currency,
        "transaction_count": matched_transactions,
        "totals": {
            "income": total_income,
            "expense": total_expense,
            "net": total_income - total_expense,
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

    cards = [
        ("收入", totals["income"], palette["matcha"], palette["green_wash"]),
        ("支出", totals["expense"], palette["sakura"], palette["pink_wash"]),
        ("淨額", totals["net"], palette["indigo"], palette["blue_wash"]),
    ]
    card_grid = grid[0, :].subgridspec(1, 3, wspace=0.18)
    for index, (label, value, accent, background) in enumerate(cards):
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
        axis.text(0.07, 0.72, label, fontsize=10.5, color=palette["gray"], va="center")
        axis.text(
            0.07,
            0.36,
            _chart_amount(value, currency),
            fontsize=18,
            fontweight="bold",
            color=accent,
            va="center",
        )

    def style_axis(axis, title_text: str) -> None:
        axis.set_title(title_text, loc="left", fontsize=12, fontweight="bold", color=palette["sumi"], pad=12)
        axis.spines[["top", "right", "left"]].set_visible(False)
        axis.spines["bottom"].set_color(palette["grid"])
        axis.tick_params(colors=palette["gray"], labelsize=8.5, length=0)
        axis.grid(axis="y", color=palette["grid"], linewidth=0.8, alpha=0.8)
        axis.set_axisbelow(True)

    month_axis = fig.add_subplot(grid[1, :4])
    style_axis(month_axis, "月度收支趨勢")
    month_labels = list(data["months"])
    positions = list(range(len(month_labels)))
    incomes = [float(data["months"][month]["income"]) for month in month_labels]
    expenses = [float(data["months"][month]["expense"]) for month in month_labels]
    width = 0.34
    month_axis.bar([x - width / 2 for x in positions], incomes, width, label="收入", color=palette["matcha"])
    month_axis.bar([x + width / 2 for x in positions], expenses, width, label="支出", color=palette["sakura"])
    month_axis.set_xticks(positions, [month.replace("-", "/") for month in month_labels])
    if len(month_labels) > 8:
        month_axis.tick_params(axis="x", rotation=35)
    month_axis.yaxis.set_major_formatter(lambda value, _pos: _axis_number(value))
    month_axis.legend(frameon=False, fontsize=9, ncol=2, loc="upper right")

    donut_axis = fig.add_subplot(grid[1, 4:])
    donut_axis.set_title("支出組成", loc="left", fontsize=12, fontweight="bold", color=palette["sumi"], pad=12)
    positive_categories = [
        (account, amount) for account, amount in data["expense_categories"].items() if amount > 0
    ]
    positive_categories.sort(key=lambda item: item[1], reverse=True)
    top = positive_categories[:5]
    remainder = sum((amount for _, amount in positive_categories[5:]), Decimal("0"))
    if remainder > 0:
        top.append(("expenses:其他", remainder))
    if top:
        donut_colors = [
            palette["sakura"],
            palette["indigo"],
            palette["gold"],
            palette["matcha"],
            "#9B8FA6",
            "#B7B0A4",
        ]
        labels = [
            _ellipsize(account.removeprefix("expenses:").replace(":", "/"), 26)
            for account, _ in top
        ]
        sizes = [float(amount) for _, amount in top]
        donut_axis.pie(
            sizes,
            colors=donut_colors[: len(sizes)],
            startangle=90,
            counterclock=False,
            wedgeprops={"width": 0.38, "edgecolor": "white", "linewidth": 2},
        )
        donut_axis.text(0, 0.05, "總支出", ha="center", va="center", fontsize=9, color=palette["gray"])
        donut_axis.text(
            0,
            -0.15,
            _chart_amount(totals["expense"], currency),
            ha="center",
            va="center",
            fontsize=10.5,
            fontweight="bold",
            color=palette["sumi"],
        )
        donut_axis.legend(labels, loc="lower center", bbox_to_anchor=(0.5, -0.28), frameon=False, fontsize=7.5, ncol=2)
    else:
        donut_axis.text(0.5, 0.5, "無支出資料", ha="center", va="center", color=palette["gray"])
        donut_axis.set_axis_off()

    cumulative_axis = fig.add_subplot(grid[2, :3])
    style_axis(cumulative_axis, "累積淨額")
    cumulative_dates = [datetime.fromisoformat(day) for day, _ in data["cumulative_net"]]
    cumulative_values = [float(value) for _, value in data["cumulative_net"]]
    cumulative_axis.plot(cumulative_dates, cumulative_values, color=palette["indigo"], linewidth=2.4)
    cumulative_axis.fill_between(cumulative_dates, cumulative_values, 0, color=palette["blue_wash"], alpha=0.9)
    cumulative_axis.axhline(0, color=palette["grid"], linewidth=1)
    cumulative_axis.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=3, maxticks=7))
    cumulative_axis.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
    cumulative_axis.yaxis.set_major_formatter(lambda value, _pos: _axis_number(value))

    category_axis = fig.add_subplot(grid[2, 3:])
    style_axis(category_axis, "支出分類排行")
    category_top = positive_categories[:6][::-1]
    if category_top:
        labels = [
            _ellipsize(account.removeprefix("expenses:").replace(":", "/"), 26)
            for account, _ in category_top
        ]
        values = [float(amount) for _, amount in category_top]
        colors = [palette["sakura"]] * len(values)
        colors[-1] = palette["indigo"]
        category_axis.barh(labels, values, color=colors, height=0.58)
        category_axis.margins(x=0.16)
        category_axis.xaxis.set_major_formatter(lambda value, _pos: _axis_number(value))
        for index, value in enumerate(values):
            category_axis.text(value, index, f"  {_axis_number(value)}", va="center", fontsize=8, color=palette["gray"])
    else:
        category_axis.text(0.5, 0.5, "無支出資料", transform=category_axis.transAxes, ha="center", va="center", color=palette["gray"])

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
    id_column: str | None = None,
    installment_column: str | None = None,
    category_column: str | None = None,
    default_installments: int = 1,
    category_prefix: str = "expenses",
    classification_transactions: Sequence[dict] = (),
    classification_rules: Sequence[dict[str, str]] = (),
) -> tuple[str, int, int]:
    output: list[str] = []
    imported = 0
    skipped = 0
    seen_ids = set(re.findall(r"import-id:([^\s]+)", existing_text))
    for index, row in enumerate(rows, start=2):
        import_id = (row.get(id_column, "") if id_column else "").strip()
        if import_id and import_id in seen_ids:
            skipped += 1
            continue
        try:
            txn_date = date.fromisoformat(row[date_column].strip())
            description = _validate_text(row[description_column], "Description")
            amount = Decimal(row[amount_column].strip())
            if amount <= 0:
                raise ValueError(
                    "amount must be a positive expense amount; split income/refunds or normalize direction first"
                )
        except (KeyError, ValueError, InvalidOperation) as error:
            raise ValueError(f"Invalid CSV row {index}: {error}") from error
        count_text = (row.get(installment_column, "") if installment_column else "").strip()
        count = int(count_text) if count_text else default_installments
        if count < 1:
            raise ValueError(f"Invalid installment count on CSV row {index}")
        category = (row.get(category_column, "") if category_column else "").strip()
        debit = f"{category_prefix}:{category}" if category else default_debit
        if debit == "auto":
            debit = classify_description(
                description,
                transactions=classification_transactions,
                rules=classification_rules,
            )["account"]
        tags = [f"import-id:{import_id}"] if import_id else []
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


def _validate_candidate(journal: Path, addition: str) -> None:
    import tempfile

    existing = journal.read_text(encoding="utf-8") if journal.exists() else ""
    with tempfile.NamedTemporaryFile("w", suffix=".journal", encoding="utf-8", delete=False) as handle:
        handle.write(existing)
        if existing and not existing.endswith("\n"):
            handle.write("\n")
        handle.write(addition)
        candidate = Path(handle.name)
    try:
        result = _run(["hledger", "-f", str(candidate), "check"], capture=True)
        if result.returncode:
            raise ValueError(result.stderr.strip() or "hledger validation failed")
    finally:
        candidate.unlink(missing_ok=True)


def append_validated(journal: Path, addition: str) -> None:
    _ensure_journal(journal)
    _validate_candidate(journal, addition)
    with journal.open("a", encoding="utf-8") as handle:
        if journal.stat().st_size and not journal.read_text(encoding="utf-8").endswith("\n"):
            handle.write("\n")
        handle.write(addition)


def _git_commit(book_dir: Path, message: str) -> None:
    if not (book_dir / ".git").exists():
        return
    subprocess.run(["git", "-C", str(book_dir), "add", "."], check=True)
    diff = subprocess.run(["git", "-C", str(book_dir), "diff", "--cached", "--quiet"], check=False)
    if diff.returncode:
        subprocess.run(["git", "-C", str(book_dir), "commit", "-m", message], check=True)


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
    append_validated(args.journal, journal_text)
    _git_commit(args.journal.parent, message)
    print(f"Appended to {args.journal}")
    return 0


def command_init(args: argparse.Namespace) -> int:
    book_dir = args.journal.parent
    book_dir.mkdir(parents=True, exist_ok=True)
    if not args.journal.exists():
        args.journal.write_text(
            "; hledger personal finance journal\n"
            "; Managed through the hledger-finance skill.\n\n",
            encoding="utf-8",
        )
    if not (book_dir / ".git").exists():
        subprocess.run(["git", "init", str(book_dir)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(book_dir), "config", "user.name", "Finance Skill"], check=True)
        subprocess.run(["git", "-C", str(book_dir), "config", "user.email", "finance-skill@localhost"], check=True)
    _git_commit(book_dir, "Initialize hledger journal")
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


def command_import_csv(args: argparse.Namespace) -> int:
    _ensure_journal(args.journal)
    with args.file.open(newline="", encoding=args.encoding) as handle:
        rows = list(csv.DictReader(handle))
    journal_text, imported, skipped = import_csv_transactions(
        rows=rows,
        existing_text=args.journal.read_text(encoding="utf-8"),
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
    print(f"Rows ready: {imported}; duplicates skipped: {skipped}", file=sys.stderr)
    if not journal_text:
        return 0
    return _print_preview_or_append(args, journal_text, f"Import {imported} CSV transactions")


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
    original = args.journal.read_text(encoding="utf-8")
    args.journal.write_text(replacement, encoding="utf-8")
    validation = _run(["hledger", "-f", str(args.journal), "check"], capture=True)
    if validation.returncode:
        args.journal.write_text(original, encoding="utf-8")
        raise ValueError(validation.stderr.strip() or "Deletion would make the journal invalid")
    descriptions = ", ".join(transaction.get("tdescription", "") for _, _, transaction in spans[:3])
    if len(spans) > 3:
        descriptions += ", ..."
    _git_commit(args.journal.parent, f"Delete {len(spans)} transaction(s): {descriptions}")
    print(f"Deleted {len(spans)} transaction(s). Use 'hfin undo' to restore them.")
    return 0


def command_undo(args: argparse.Namespace) -> int:
    _ensure_journal(args.journal)
    book_dir = args.journal.parent
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

    import_parser = subparsers.add_parser("import-csv", help="Import CSV, optionally splitting rows into installments")
    import_parser.add_argument("file", type=Path)
    import_parser.add_argument("--encoding", default="utf-8-sig")
    import_parser.add_argument("--date-column", default="date")
    import_parser.add_argument("--description-column", default="description")
    import_parser.add_argument("--amount-column", default="amount")
    import_parser.add_argument("--id-column")
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
