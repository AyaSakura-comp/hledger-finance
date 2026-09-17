#!/usr/bin/env python3
"""Run a deterministic 50-scenario QA matrix for ``hfin visualize``.

This is both an executable regression suite and a template for an agent that
needs to test visualization behavior without touching the production journal.
All journals, PNGs, evidence cards, contact sheets, and reports are written
under the selected output directory.
"""

from __future__ import annotations

import argparse
import calendar
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Callable

SCRIPT = Path(__file__).with_name("finance.py")


@dataclass(frozen=True)
class Scenario:
    id: str
    name: str
    kind: str
    fixture: Callable[[date], str]
    args: Callable[[date], list[str]]
    focus: str
    expected_error: str | None = None
    expected_rc: int = 0
    output_mode: str = "explicit"
    precreate_output: bool = False


def entry(day: date | str, description: str, postings: list[tuple[str, str, str]], tags: tuple[str, ...] = ()) -> str:
    lines = [f"{day} {description}"]
    lines.extend(f"    ; {tag}" for tag in tags)
    for account, amount, currency in postings:
        suffix = f" {currency}" if currency else ""
        lines.append(f"    {account:<46} {amount}{suffix}")
    return "\n".join(lines) + "\n\n"


def transaction(
    day: date | str,
    description: str,
    debit: str,
    credit: str,
    amount: str,
    currency: str = "TWD",
    tags: tuple[str, ...] = (),
) -> str:
    value = Decimal(amount)
    return entry(
        day,
        description,
        [(debit, format(value, "f"), currency), (credit, format(-value, "f"), currency)],
        tags,
    )


def standard(today: date, *, currency: str = "TWD", month: int = 1, year: int = 2026) -> str:
    return (
        transaction(f"{year}-{month:02d}-01", "Salary", "assets:bank", "income:salary", "50000", currency)
        + transaction(f"{year}-{month:02d}-08", "Rent", "expenses:housing", "assets:bank", "18000", currency)
        + transaction(f"{year}-{month:02d}-15", "Lunch", "expenses:food", "assets:cash", "250", currency)
    )


def month_start(value: date) -> date:
    return value.replace(day=1)


def shift_month(value: date, count: int) -> date:
    index = value.year * 12 + value.month - 1 + count
    return date(index // 12, index % 12 + 1, 1)


def inclusive_period(start: date, end: date) -> list[str]:
    return ["--period", f"{start.isoformat()}..{end.isoformat()}"]


def current_month_fixture(today: date) -> str:
    start = month_start(today)
    return standard(today, year=start.year, month=start.month)


def last_month_fixture(today: date) -> str:
    start = shift_month(month_start(today), -1)
    return standard(today, year=start.year, month=start.month)


def three_months(today: date) -> str:
    return "".join(standard(today, month=month) for month in (1, 2, 3))


def year_boundary(today: date) -> str:
    return (
        transaction("2025-12-31", "Year End Dinner", "expenses:food", "assets:bank", "1200")
        + transaction("2026-01-01", "New Year Salary", "assets:bank", "income:salary", "50000")
    )


def leap_day(today: date) -> str:
    return transaction("2024-02-29", "Leap Day", "expenses:travel", "assets:bank", "2024") + transaction(
        "2024-03-01", "March Salary", "assets:bank", "income:salary", "50000"
    )


def months_fixture(count: int, start: date = date(2025, 1, 1), long_labels: bool = False) -> str:
    chunks: list[str] = []
    current = start
    for index in range(count):
        label = "Monthly Salary With A Deliberately Long Description" if long_labels else "Salary"
        chunks.append(transaction(current, f"{label} {index + 1}", "assets:bank", "income:salary", "50000"))
        expense_day = current.replace(day=15)
        account = "expenses:household:recurring:monthly:essential:very-long-category" if long_labels else "expenses:housing"
        chunks.append(transaction(expense_day, f"Rent {index + 1}", account, "assets:bank", "18000"))
        current = shift_month(current, 1)
    return "".join(chunks)


def daily_fixture(count: int) -> str:
    chunks = [transaction("2026-01-01", "Opening Salary", "assets:bank", "income:salary", "100000")]
    start = date(2026, 1, 1)
    for index in range(count):
        chunks.append(transaction(start + timedelta(days=index), f"Daily {index + 1}", "expenses:daily", "assets:cash", "10"))
    return "".join(chunks)


def performance_fixture(today: date) -> str:
    chunks = [transaction("2026-01-01", "Annual Income", "assets:bank", "income:salary", "1000000")]
    start = date(2026, 1, 1)
    for index in range(250):
        day = start + timedelta(days=index % 250)
        chunks.append(transaction(day, f"Purchase {index + 1}", f"expenses:batch:{index % 12:02d}", "assets:cash", str(index % 97 + 1)))
    return "".join(chunks)


def refund_fixture(today: date) -> str:
    return (
        transaction("2026-02-01", "Salary", "assets:bank", "income:salary", "50000")
        + transaction("2026-02-05", "Purchase", "expenses:food", "assets:bank", "5000")
        + entry("2026-02-10", "Expense Refund", [("assets:bank", "500", "TWD"), ("expenses:food", "-500", "TWD")])
    )


def income_reversal_fixture(today: date) -> str:
    return (
        transaction("2026-03-01", "Salary", "assets:bank", "income:salary", "10000")
        + entry("2026-03-20", "Salary Reversal", [("income:salary", "1000", "TWD"), ("assets:bank", "-1000", "TWD")])
    )


def transfer_plus_expense(today: date) -> str:
    return (
        transaction("2026-04-01", "Transfer", "assets:cash", "assets:bank", "5000")
        + transaction("2026-04-02", "Dinner", "expenses:food", "assets:cash", "800")
        + transaction("2026-04-03", "Salary", "assets:bank", "income:salary", "50000")
    )


def many_categories(today: date, count: int = 15) -> str:
    chunks = [transaction("2026-05-01", "Salary", "assets:bank", "income:salary", "100000")]
    for index in range(count):
        chunks.append(
            transaction(
                f"2026-05-{index + 2:02d}",
                f"Category {index + 1}",
                f"expenses:category:{index + 1:02d}",
                "assets:cash",
                str((index + 1) * 100),
            )
        )
    return "".join(chunks)


def mixed_currency(today: date) -> str:
    return standard(today, currency="TWD") + standard(today, currency="USD", month=2)


def filter_fixture(today: date) -> str:
    return (
        transaction("2026-06-01", "Work Salary", "assets:bank", "income:salary", "60000", tags=("project:work",))
        + transaction("2026-06-05", "Team Lunch", "expenses:food", "assets:bank", "1200", tags=("project:work",))
        + transaction("2026-06-08", "Family Lunch", "expenses:food", "assets:cash", "800", tags=("project:home",))
        + transaction("2026-06-10", "Flight", "expenses:travel", "assets:bank", "20000", tags=("project:italy",))
    )


def unicode_fixture(today: date) -> str:
    return (
        transaction("2026-07-01", "薪資 💴", "assets:銀行", "income:薪資", "50000")
        + transaction("2026-07-05", "拉麵 🍜", "expenses:餐飲:日本料理", "assets:銀行", "350")
        + transaction("2026-07-10", "台北／東京", "expenses:旅行/日本", "assets:銀行", "15000")
    )


def long_labels_fixture(today: date) -> str:
    label = "expenses:household:groceries:organic-and-imported-food-with-a-very-long-category-name"
    return transaction("2026-08-01", "Salary", "assets:bank", "income:salary", "50000") + transaction(
        "2026-08-10", "A very long purchase description that must not break the dashboard", label, "assets:bank", "12345"
    )


def empty_fixture(today: date) -> str:
    return "; intentionally empty\n"


def malformed_fixture(today: date) -> str:
    return "2026-01-01 Broken\n    expenses:food  100 TWD\n    assets:bank     50 TWD\n"


def asset_only_fixture(today: date) -> str:
    return transaction("2026-01-01", "Transfer", "assets:cash", "assets:bank", "5000")


def unitless_fixture(today: date) -> str:
    return entry("2026-01-01", "Points", [("expenses:points", "10", ""), ("assets:points", "-10", "")])


def decimal_fixture(currency: str, income: str, expense: str) -> Callable[[date], str]:
    def build(today: date) -> str:
        return transaction("2026-01-01", "Income", "assets:bank", "income:salary", income, currency) + transaction(
            "2026-01-02", "Expense", "expenses:misc", "assets:bank", expense, currency
        )

    return build


def args(*values: str) -> Callable[[date], list[str]]:
    return lambda today: list(values)


def dynamic_args(builder: Callable[[date], list[str]]) -> Callable[[date], list[str]]:
    return builder


def scenario_matrix() -> list[Scenario]:
    cases = [
        Scenario("V01", "基本單月收支", "render", standard, args("--period", "2026-01-01..2026-01-31", "--currency", "TWD"), "KPI、單月柱狀圖、分類"),
        Scenario("V02", "僅收入", "render", lambda t: transaction("2026-01-01", "Salary", "assets:bank", "income:salary", "50000"), args("--period", "2026-01-01..2026-01-31", "--currency", "TWD"), "無支出空狀態"),
        Scenario("V03", "僅支出", "render", lambda t: transaction("2026-01-02", "Rent", "expenses:housing", "assets:bank", "18000"), args("--period", "2026-01-01..2026-01-31", "--currency", "TWD"), "零收入、負淨額"),
        Scenario("V04", "淨額剛好為零", "render", decimal_fixture("TWD", "10000", "10000"), args("--period", "2026-01-01..2026-01-31", "--currency", "TWD"), "零線與淨額卡"),
        Scenario("V05", "大幅負淨額", "render", decimal_fixture("TWD", "1000", "50000"), args("--period", "2026-01-01..2026-01-31", "--currency", "TWD"), "負值座標與累積線"),
        Scenario("V06", "同日多筆交易", "render", lambda t: standard(t) + transaction("2026-01-01", "Bonus", "assets:bank", "income:bonus", "3000"), args("--period", "2026-01-01", "--currency", "TWD"), "單日累積彙總"),
        Scenario("V07", "跨三個月", "render", three_months, args("--period", "2026-01-01..2026-03-31", "--currency", "TWD"), "三組月份柱"),
        Scenario("V08", "跨年邊界", "render", year_boundary, args("--period", "2025-12-01..2026-01-31", "--currency", "TWD"), "12月與1月排序"),
        Scenario("V09", "閏年二月二十九日", "render", leap_day, args("--period", "2024-02-01..2024-03-31", "--currency", "TWD"), "閏日與月底邊界"),
        Scenario("V10", "十二個月", "render", lambda t: months_fixture(12, date(2026, 1, 1)), args("--period", "2026-01-01..2026-12-31", "--currency", "TWD"), "月份標籤旋轉"),
        Scenario("V11", "二十四個月", "render", lambda t: months_fixture(24), args("--period", "2025-01-01..2026-12-31", "--currency", "TWD"), "密集月份標籤"),
        Scenario("V12", "連續一百二十日", "render", lambda t: daily_fixture(120), args("--period", "2026-01-01..2026-04-30", "--currency", "TWD"), "日期 locator 與長折線"),
        Scenario("V13", "二百五十筆效能資料", "render", performance_fixture, args("--period", "2026-01-01..2026-09-30", "--currency", "TWD"), "大量交易效能"),
        Scenario("V14", "支出退款", "render", refund_fixture, args("--period", "2026-02-01..2026-02-28", "--currency", "TWD"), "負支出抵銷"),
        Scenario("V15", "收入沖回", "render", income_reversal_fixture, args("--period", "2026-03-01..2026-03-31", "--currency", "TWD"), "負收入抵銷"),
        Scenario("V16", "轉帳與支出混合", "render", transfer_plus_expense, args("--period", "2026-04-01..2026-04-30", "--currency", "TWD"), "資產轉帳不計收支"),
        Scenario("V17", "純資產轉帳", "error", asset_only_fixture, args("--period", "2026-01-01..2026-01-31", "--currency", "TWD"), "無可視化資料錯誤", "No income or expense data is available to visualize", 2),
        Scenario("V18", "深層分類", "render", lambda t: months_fixture(1, long_labels=True), args("--period", "2025-01-01..2025-01-31", "--currency", "TWD"), "巢狀分類標籤"),
        Scenario("V19", "中文與斜線分類", "render", unicode_fixture, args("--period", "2026-07-01..2026-07-31", "--currency", "TWD"), "CJK、emoji、斜線"),
        Scenario("V20", "十五種支出分類", "render", many_categories, args("--period", "2026-05-01..2026-05-31", "--currency", "TWD"), "Top 5 加其他、Top 6 排名"),
        Scenario("V21", "超長分類名稱", "render", long_labels_fixture, args("--period", "2026-08-01..2026-08-31", "--currency", "TWD"), "長標籤裁切與留白"),
        Scenario("V22", "Emoji 描述", "render", unicode_fixture, args("--period", "2026-07-01..2026-07-31", "--currency", "TWD", "--description", "拉麵"), "emoji 不影響渲染"),
        Scenario("V23", "超長自訂標題", "render", standard, args("--period", "2026-01-01..2026-01-31", "--currency", "TWD", "--title", "這是一個非常非常非常非常非常非常長的年度財務分析標題"), "標題不重疊右上資訊"),
        Scenario("V24", "篩選後無匹配資料", "error", filter_fixture, args("--period", "2026-06-01..2026-06-30", "--currency", "TWD", "--description", "NeverMatches"), "無匹配資料錯誤", "No income or expense data is available to visualize", 2),
        Scenario("V25", "美元小數", "render", decimal_fixture("USD", "3000.50", "1234.56"), args("--period", "2026-01-01..2026-01-31", "--currency", "USD"), "兩位小數"),
        Scenario("V26", "日圓整數", "render", decimal_fixture("JPY", "300000", "12500"), args("--period", "2026-01-01..2026-01-31", "--currency", "JPY"), "零小數幣別"),
        Scenario("V27", "比特幣八位小數", "render", decimal_fixture("BTC", "0.12345678", "0.00001234"), args("--period", "2026-01-01..2026-01-31", "--currency", "BTC"), "極小高精度數值"),
        Scenario("V28", "混幣選美元", "render", mixed_currency, args("--period", "2026-01-01..2026-02-28", "--currency", "USD"), "只顯示 USD"),
        Scenario("V29", "混幣選台幣", "render", mixed_currency, args("--period", "2026-01-01..2026-02-28", "--currency", "TWD"), "只顯示 TWD"),
        Scenario("V30", "混幣未選幣別", "error", mixed_currency, args("--period", "2026-01-01..2026-02-28"), "拒絕合併幣別", "Data contains multiple currencies", 2),
        Scenario("V31", "指定不存在幣別", "error", standard, args("--period", "2026-01-01..2026-01-31", "--currency", "EUR"), "可行動錯誤訊息", "Currency 'EUR' is not present", 2),
        Scenario("V32", "無單位商品", "render", unitless_fixture, args("--period", "2026-01-01..2026-01-31", "--currency", "unitless"), "unitless fallback"),
        Scenario("V33", "極小金額", "render", decimal_fixture("TWD", "0.02", "0.01"), args("--period", "2026-01-01..2026-01-31", "--currency", "TWD"), "0.01 可讀性"),
        Scenario("V34", "極大金額", "render", decimal_fixture("TWD", "999999999999.99", "123456789.01"), args("--period", "2026-01-01..2026-01-31", "--currency", "TWD"), "無 overflow、千分位"),
        Scenario("V35", "三位小數", "render", decimal_fixture("TWD", "5000", "1234.567"), args("--period", "2026-01-01..2026-01-31", "--currency", "TWD"), "顯示四捨五入"),
        Scenario("V36", "帳戶篩選", "render", filter_fixture, args("--period", "2026-06-01..2026-06-30", "--currency", "TWD", "--account", "expenses:food"), "只保留餐飲"),
        Scenario("V37", "描述篩選", "render", filter_fixture, args("--period", "2026-06-01..2026-06-30", "--currency", "TWD", "--description", "Lunch"), "描述 regex"),
        Scenario("V38", "標籤篩選", "render", filter_fixture, args("--period", "2026-06-01..2026-06-30", "--currency", "TWD", "--tag", "project:italy"), "project tag"),
        Scenario("V39", "Raw where 篩選", "render", filter_fixture, args("--period", "2026-06-01..2026-06-30", "--currency", "TWD", "--where", "amt:>10000"), "hledger raw query"),
        Scenario("V40", "複合篩選", "render", filter_fixture, args("--period", "2026-06-01..2026-06-30", "--currency", "TWD", "--account", "expenses:food", "--description", "Team", "--tag", "project:work"), "帳戶、描述、tag 交集"),
        Scenario("V41", "本月相對期間", "render", current_month_fixture, args("--period", "this-month", "--currency", "TWD"), "動態本月邊界"),
        Scenario("V42", "上月相對期間", "render", last_month_fixture, args("--period", "last-month", "--currency", "TWD"), "動態跨月邊界"),
        Scenario("V43", "只有開始日期", "render", standard, args("--begin", "2026-01-01", "--currency", "TWD"), "open-ended period label"),
        Scenario("V44", "只有結束日期", "render", standard, args("--end", "2026-01-31", "--currency", "TWD"), "截至日期 label"),
        Scenario("V45", "完全不設期間", "render", standard, args("--currency", "TWD"), "全部期間 label"),
        Scenario("V46", "Unicode 與空白輸出路徑", "render", unicode_fixture, args("--period", "2026-07-01..2026-07-31", "--currency", "TWD", "--title", "旅行財務分析｜東京"), "路徑與標題 Unicode", output_mode="unicode"),
        Scenario("V47", "覆寫既有 PNG", "render", standard, args("--period", "2026-01-01..2026-01-31", "--currency", "TWD"), "安全覆寫輸出", precreate_output=True),
        Scenario("V48", "預設 reports 輸出", "render", standard, args("--period", "2026-01-01..2026-01-31", "--currency", "TWD"), "預設檔名與目錄", output_mode="default"),
        Scenario("V49", "空帳本", "error", empty_fixture, args("--period", "2026-01-01..2026-01-31", "--currency", "TWD"), "空資料錯誤", "No income or expense data is available to visualize", 2),
        Scenario("V50", "起訖日期顛倒", "error", filter_fixture, args("--period", "2026-12-01..2026-01-01", "--currency", "TWD"), "顛倒期間錯誤", "Period start must not be after end", 2),
    ]
    if len(cases) != 50 or len({case.id for case in cases}) != 50:
        raise RuntimeError("The visualization QA matrix must contain exactly 50 unique scenarios")
    return cases


def font(size: int):
    from PIL import ImageFont

    for candidate in (
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJKtc-Regular.otf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    ):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            pass
    return ImageFont.load_default()


def evidence_card(path: Path, scenario: Scenario, text: str, passed: bool) -> None:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (1800, 1200), "white")
    draw = ImageDraw.Draw(image)
    color = "#7F9E7A" if passed else "#C95D63"
    draw.rounded_rectangle((70, 70, 1730, 1130), radius=35, fill="#F7F4EE", outline=color, width=7)
    draw.text((120, 120), f"{scenario.id} · {scenario.name}", font=font(48), fill="#34383F")
    draw.text((120, 205), "EXPECTED ERROR · PASS" if passed else "FAIL", font=font(34), fill=color)
    y = 290
    for line in text.splitlines()[:16]:
        draw.text((120, y), line[:90], font=font(25), fill="#5F646B")
        y += 48
    image.save(path)


def inspect_png(path: Path) -> dict:
    from PIL import Image

    with Image.open(path) as image:
        image.load()
        if image.format != "PNG":
            raise AssertionError(f"format is {image.format}, not PNG")
        if image.size != (1800, 1200):
            raise AssertionError(f"size is {image.size}, expected 1800x1200")
        rgb = image.convert("RGB")
        corner = rgb.getpixel((0, 0))
        if min(corner) < 245:
            raise AssertionError(f"top-left corner is not white: {corner}")
        sample = rgb.resize((180, 120))
        colors = sample.getcolors(maxcolors=100000) or []
        unique_colors = len(colors)
        if unique_colors <= 20:
            raise AssertionError(f"only {unique_colors} sampled colors")
        near_white = sum(count for count, value in colors if min(value) >= 245)
        white_ratio = near_white / (180 * 120)
        if white_ratio < 0.45:
            raise AssertionError(f"near-white background ratio is only {white_ratio:.1%}")
        return {"format": image.format, "width": 1800, "height": 1200, "colors": unique_colors, "near_white_ratio": round(white_ratio, 4)}


def run_scenario(scenario: Scenario, today: date, root: Path) -> dict:
    case_dir = root / "cases" / scenario.id
    case_dir.mkdir(parents=True, exist_ok=True)
    journal = case_dir / "main.journal"
    journal.write_text(scenario.fixture(today), encoding="utf-8")
    if scenario.output_mode == "unicode":
        output = case_dir / "輸出 目錄" / "財務 圖表.png"
    else:
        output = case_dir / f"{scenario.id}.png"
    if scenario.precreate_output:
        output.write_bytes(b"old-placeholder")

    command = [sys.executable, str(SCRIPT), "--journal", str(journal), "visualize", *scenario.args(today)]
    if scenario.kind == "render" and scenario.output_mode != "default":
        command.extend(["--output", str(output)])
    started = time.monotonic()
    result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=120)
    elapsed = round(time.monotonic() - started, 3)
    record = {
        "id": scenario.id,
        "name": scenario.name,
        "kind": scenario.kind,
        "focus": scenario.focus,
        "status": "FAIL",
        "exit_code": result.returncode,
        "elapsed_seconds": elapsed,
        "command": command,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
        "artifact": None,
        "image": None,
        "reason": "",
    }
    try:
        if result.returncode != scenario.expected_rc:
            raise AssertionError(f"exit {result.returncode}, expected {scenario.expected_rc}")
        if scenario.kind == "error":
            if scenario.expected_error and scenario.expected_error not in result.stderr:
                raise AssertionError(f"missing expected error: {scenario.expected_error}")
            evidence = case_dir / f"{scenario.id}-expected-error.png"
            evidence_card(evidence, scenario, result.stderr.strip() or "Expected non-zero exit", True)
            record["artifact"] = str(evidence)
        else:
            glyph_errors = [line for line in result.stderr.splitlines() if "Glyph" in line or "findfont" in line]
            if glyph_errors:
                raise AssertionError("font/glyph warning: " + " | ".join(glyph_errors[:3]))
            if not result.stdout.strip():
                raise AssertionError("successful visualize command returned no output path")
            rendered = Path(result.stdout.strip().splitlines()[-1])
            if not rendered.exists():
                raise AssertionError(f"output does not exist: {rendered}")
            if scenario.output_mode == "default":
                expected_parent = journal.parent / "reports"
                if rendered.parent != expected_parent:
                    raise AssertionError(f"default output parent is {rendered.parent}, expected {expected_parent}")
            if scenario.precreate_output and rendered.read_bytes() == b"old-placeholder":
                raise AssertionError("existing output was not replaced")
            record["image"] = inspect_png(rendered)
            record["artifact"] = str(rendered)
        record["status"] = "PASS"
    except Exception as error:
        record["reason"] = str(error)
        evidence = case_dir / f"{scenario.id}-failure.png"
        evidence_card(evidence, scenario, str(error) + "\n" + result.stderr[:800], False)
        record["artifact"] = str(evidence)
    return record


def contact_sheets(records: list[dict], root: Path) -> list[str]:
    from PIL import Image, ImageDraw

    paths: list[str] = []
    for sheet_index in range(5):
        subset = records[sheet_index * 10 : (sheet_index + 1) * 10]
        sheet = Image.new("RGB", (3600, 1300), "white")
        draw = ImageDraw.Draw(sheet)
        draw.text((35, 18), f"hledger-finance · Visualization QA · {sheet_index + 1}/5", font=font(34), fill="#34383F")
        for index, record in enumerate(subset):
            column = index % 5
            row = index // 5
            x = column * 720
            y = 70 + row * 610
            with Image.open(record["artifact"]) as source:
                thumb = source.convert("RGB")
                thumb.thumbnail((700, 467))
                sheet.paste(thumb, (x + 10, y + 10))
            status_color = "#668466" if record["status"] == "PASS" else "#C95D63"
            draw.text((x + 18, y + 490), f"{record['id']} {record['status']}", font=font(25), fill=status_color)
            draw.text((x + 18, y + 530), record["name"][:25], font=font(19), fill="#5F646B")
        destination = root / f"contact-sheet-{sheet_index + 1}.png"
        sheet.save(destination)
        paths.append(str(destination))
    return paths


def write_report(records: list[dict], contacts: list[str], root: Path) -> Path:
    passed = sum(record["status"] == "PASS" for record in records)
    report = [
        "# hledger-finance — 50 Visualization Scenarios",
        "",
        f"- Result: **{passed}/50 PASS**",
        f"- Render scenarios: **{sum(record['kind'] == 'render' for record in records)}**",
        f"- Expected-error scenarios: **{sum(record['kind'] == 'error' for record in records)}**",
        f"- Total command time: **{sum(record['elapsed_seconds'] for record in records):.2f}s**",
        "",
        "| ID | Scenario | Kind | Status | Seconds | Focus |",
        "|---|---|---:|---:|---:|---|",
    ]
    for record in records:
        report.append(
            f"| {record['id']} | {record['name']} | {record['kind']} | {record['status']} | "
            f"{record['elapsed_seconds']:.3f} | {record['focus']} |"
        )
    report.extend(["", "## Contact sheets", ""])
    report.extend(f"- `{path}`" for path in contacts)
    destination = root / "report.md"
    destination.write_text("\n".join(report) + "\n", encoding="utf-8")
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list-json", action="store_true", help="Print the 50-scenario manifest and exit")
    parser.add_argument("--output-dir", type=Path, default=Path("/tmp/hledger-finance-visualization-qa"))
    options = parser.parse_args()
    scenarios = scenario_matrix()
    if options.list_json:
        print(
            json.dumps(
                [
                    {"id": case.id, "name": case.name, "kind": case.kind, "focus": case.focus}
                    for case in scenarios
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    root = options.output_dir.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    today = date.today()
    records: list[dict] = []
    for index, scenario in enumerate(scenarios, start=1):
        record = run_scenario(scenario, today, root)
        records.append(record)
        print(f"{index:02d}/50 {scenario.id} {record['status']} — {scenario.name}", flush=True)
    (root / "results.json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    contacts = contact_sheets(records, root)
    report = write_report(records, contacts, root)
    passed = sum(record["status"] == "PASS" for record in records)
    print(f"SUMMARY {passed}/50 PASS")
    print(report)
    return 0 if passed == 50 else 1


if __name__ == "__main__":
    raise SystemExit(main())
