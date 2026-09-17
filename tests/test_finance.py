import csv
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
import warnings
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest import mock

SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_DIR))

from scripts.finance import (  # noqa: E402
    _acquire_journal_lock,
    _append_validated_and_commit,
    _axis_number,
    _composition_entries,
    audit_journal,
    _dashboard_presentation,
    _require_regular_journal,
    _chart_amount,
    _ellipsize,
    add_months,
    build_hledger_query,
    build_ingest_transactions,
    build_visualization_data,
    classify_description,
    compute_stats,
    import_csv_transactions,
    parse_ingest_json,
    parse_period,
    render_dashboard_png,
    render_installments,
    render_transaction,
    split_amount,
)


class PeriodTests(unittest.TestCase):
    def test_explicit_period_is_end_exclusive(self):
        self.assertEqual(
            parse_period("2026-01-10..2026-02-20", date(2026, 9, 16)),
            (date(2026, 1, 10), date(2026, 2, 21)),
        )

    def test_reversed_explicit_period_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "start must not be after end"):
            parse_period("2026-12-01..2026-01-01")

    def test_last_month_handles_year_boundary(self):
        self.assertEqual(
            parse_period("last-month", date(2026, 1, 10)),
            (date(2025, 12, 1), date(2026, 1, 1)),
        )

    def test_last_n_days_is_inclusive_of_today(self):
        self.assertEqual(
            parse_period("last-30-days", date(2026, 9, 16)),
            (date(2026, 8, 18), date(2026, 9, 17)),
        )


class InstallmentTests(unittest.TestCase):
    def test_split_amount_preserves_total_and_puts_remainder_last(self):
        parts = split_amount(Decimal("100.00"), 3)
        self.assertEqual(parts, [Decimal("33.33"), Decimal("33.33"), Decimal("33.34")])
        self.assertEqual(sum(parts), Decimal("100.00"))

    def test_add_months_clamps_to_month_end(self):
        self.assertEqual(add_months(date(2026, 1, 31), 1), date(2026, 2, 28))
        self.assertEqual(add_months(date(2028, 1, 31), 1), date(2028, 2, 29))

    def test_render_installments_creates_balanced_future_transactions(self):
        journal = render_installments(
            start=date(2026, 10, 31),
            description="Phone",
            total=Decimal("100.00"),
            count=3,
            debit_account="expenses:electronics",
            credit_account="liabilities:card",
            currency="TWD",
            fee_per_installment=Decimal("2.00"),
            fee_account="expenses:fees",
        )
        self.assertIn("2026-10-31 Phone [1/3]", journal)
        self.assertIn("2026-11-30 Phone [2/3]", journal)
        self.assertIn("2026-12-31 Phone [3/3]", journal)
        self.assertIn("expenses:fees", journal)
        self.assertIn("-35.34 TWD", journal)


class RenderingSafetyTests(unittest.TestCase):
    def test_render_transaction_rejects_hledger_grammar_in_text_fields(self):
        cases = [
            ({"description": "! Cleared-looking expense"}, "Description"),
            ({"description": "Lunch ; kind:income"}, "Description"),
            ({"debit_account": "(expenses:food)"}, "Account"),
            ({"debit_account": "expenses:food  999 USD"}, "Account"),
            ({"credit_account": "assets:cash ; comment"}, "Account"),
            ({"currency": "TWD ; kind:income"}, "Currency"),
            ({"tags": ["project:trip, kind:income"]}, "Tag"),
            ({"tags": ["note:x; import-id:forged"]}, "Tag"),
        ]
        base = {
            "txn_date": date(2026, 1, 1),
            "description": "Lunch",
            "debit_account": "expenses:food",
            "credit_account": "assets:cash",
            "amount": Decimal("100"),
            "currency": "TWD",
            "tags": ["source:fuzzy-text"],
        }
        for changes, label in cases:
            with self.subTest(changes=changes):
                with self.assertRaisesRegex(ValueError, label):
                    render_transaction(**{**base, **changes})

    def test_render_transaction_accepts_safe_unicode_and_punctuation(self):
        journal = render_transaction(
            date(2026, 1, 1),
            "全聯-台北店 42",
            "expenses:餐飲-外食",
            "assets:銀行帳戶",
            Decimal("100"),
            "TWD",
            tags=["project:台北旅行"],
        )
        self.assertIn("全聯-台北店 42", journal)
        self.assertIn("; project:台北旅行", journal)


class QueryTests(unittest.TestCase):
    def test_query_supports_arbitrary_period_filters_and_raw_terms(self):
        command = build_hledger_query(
            journal=Path("/tmp/book.journal"),
            report="register",
            begin=date(2026, 1, 1),
            end=date(2026, 4, 1),
            accounts=["expenses:food", "expenses:travel"],
            descriptions=["uber"],
            tags=["project:italy"],
            raw_terms=["amt:>100"],
            interval="monthly",
            output_format="csv",
        )
        self.assertEqual(command[:3], ["hledger", "-f", "/tmp/book.journal"])
        self.assertIn("--begin=2026-01-01", command)
        self.assertIn("--end=2026-04-01", command)
        self.assertIn("acct:expenses:food|expenses:travel", command)
        self.assertIn("desc:uber", command)
        self.assertIn("tag:project=italy", command)
        self.assertIn("amt:>100", command)
        self.assertIn("--monthly", command)
        self.assertIn("--output-format=csv", command)

    def test_query_rejects_html_for_transaction_reports(self):
        with self.assertRaisesRegex(ValueError, "not supported"):
            build_hledger_query(
                journal=Path("/tmp/book.journal"),
                report="transactions",
                output_format="html",
            )

    def test_query_allows_html_for_financial_statements(self):
        command = build_hledger_query(
            journal=Path("/tmp/book.journal"),
            report="income-statement",
            output_format="html",
        )
        self.assertIn("--output-format=html", command)

    def test_query_rejects_structured_output_for_journal_stats(self):
        with self.assertRaisesRegex(ValueError, "not supported"):
            build_hledger_query(
                journal=Path("/tmp/book.journal"),
                report="stats",
                output_format="json",
            )


class StatsTests(unittest.TestCase):
    def test_stats_groups_income_expense_and_net_by_currency(self):
        transactions = [
            {
                "tpostings": [
                    {"paccount": "assets:bank", "pamount": [{"acommodity": "TWD", "aquantity": {"floatingPoint": 50000}}]},
                    {"paccount": "income:salary", "pamount": [{"acommodity": "TWD", "aquantity": {"floatingPoint": -50000}}]},
                ]
            },
            {
                "tpostings": [
                    {"paccount": "expenses:food", "pamount": [{"acommodity": "TWD", "aquantity": {"floatingPoint": 150}}]},
                    {"paccount": "assets:bank", "pamount": [{"acommodity": "TWD", "aquantity": {"floatingPoint": -150}}]},
                ]
            },
        ]
        result = compute_stats(transactions)
        self.assertEqual(
            result["sign_convention"],
            {
                "basis": "cashflow",
                "income": "credit_positive_debit_negative",
                "expense": "debit_negative_credit_positive",
                "net": "income_plus_expense",
            },
        )
        self.assertEqual(result["currencies"]["TWD"]["income"], "50000")
        self.assertEqual(result["currencies"]["TWD"]["expense"], "-150")
        self.assertEqual(result["currencies"]["TWD"]["net"], "49850")
        self.assertEqual(result["expense_categories"]["expenses:food"]["TWD"], "-150")


class ClassificationTests(unittest.TestCase):
    def test_builtin_rules_classify_common_taiwan_merchants(self):
        result = classify_description("全聯福利中心買菜")
        self.assertEqual(result["account"], "expenses:food:groceries")
        self.assertEqual(result["source"], "builtin-rule")
        self.assertGreaterEqual(result["confidence"], 0.9)

    def test_builtin_category_matrix(self):
        cases = {
            "Foodpanda 晚餐": "expenses:food:delivery",
            "Uber 行程": "expenses:transport:taxi",
            "高鐵票": "expenses:transport:transit",
            "中華電信月租": "expenses:utilities",
            "九月房租": "expenses:housing",
            "大樹藥局": "expenses:health",
            "Netflix 訂閱": "expenses:subscriptions",
            "Disney+": "expenses:subscriptions",
            "7-11": "expenses:food:convenience",
            "蝦皮網購": "expenses:shopping",
            "Booking.com": "expenses:travel",
            "銀行手續費": "expenses:fees",
            "寵物飼料": "expenses:pets",
        }
        for description, expected in cases.items():
            with self.subTest(description=description):
                self.assertEqual(classify_description(description)["account"], expected)

    def test_custom_rules_take_priority(self):
        result = classify_description(
            "毛孩市集飼料",
            rules=[{"pattern": "毛孩市集", "account": "expenses:pets:supplies"}],
        )
        self.assertEqual(result["account"], "expenses:pets:supplies")
        self.assertEqual(result["source"], "custom-rule")

    def test_custom_literal_punctuation_does_not_overmatch(self):
        result = classify_description(
            "Costco",
            rules=[{"pattern": "C++", "account": "expenses:education:programming"}],
        )
        self.assertNotEqual(result["account"], "expenses:education:programming")

    def test_matching_history_reuses_the_previous_expense_account(self):
        history = [
            {
                "tdescription": "藍瓶咖啡 台北",
                "tpostings": [{"paccount": "expenses:food:coffee", "pamount": []}],
            }
        ]
        result = classify_description("藍瓶咖啡 台北", transactions=history)
        self.assertEqual(result["account"], "expenses:food:coffee")
        self.assertEqual(result["source"], "history")

    def test_history_uses_the_primary_expense_not_a_small_installment_fee(self):
        history = [
            {
                "tdescription": "Phone [1/12]",
                "tpostings": [
                    {
                        "paccount": "expenses:electronics",
                        "pamount": [{"acommodity": "TWD", "aquantity": {"floatingPoint": 3000}}],
                    },
                    {
                        "paccount": "expenses:fees",
                        "pamount": [{"acommodity": "TWD", "aquantity": {"floatingPoint": 30}}],
                    },
                ],
            }
        ]
        result = classify_description("Phone", transactions=history)
        self.assertEqual(result["account"], "expenses:electronics")

    def test_latest_equal_history_match_wins_after_a_user_correction(self):
        history = [
            {"tdescription": "Corner Cafe", "tpostings": [{"paccount": "expenses:misc"}]},
            {"tdescription": "Corner Cafe", "tpostings": [{"paccount": "expenses:food:coffee"}]},
        ]
        result = classify_description("Corner Cafe", transactions=history)
        self.assertEqual(result["account"], "expenses:food:coffee")

    def test_unknown_description_uses_reviewable_fallback(self):
        result = classify_description("XYZ-9482")
        self.assertEqual(result["account"], "expenses:uncategorized")
        self.assertEqual(result["source"], "fallback")
        self.assertLess(result["confidence"], 0.5)


class VisualizationTests(unittest.TestCase):
    def test_long_visual_labels_are_compacted_without_splitting_short_labels(self):
        self.assertEqual(_ellipsize("餐飲", 8), "餐飲")
        self.assertEqual(_ellipsize("household/groceries/organic/imported", 18), "household/groceri…")

    def test_small_fractional_amounts_remain_visible_in_cards_and_axes(self):
        self.assertEqual(_chart_amount(Decimal("0.00001234"), "BTC"), "0.00001234 BTC")
        self.assertEqual(_axis_number(0.00001234), "0.00001234")
        self.assertEqual(_axis_number(0.0), "0")

    def setUp(self):
        self.transactions = [
            {
                "tdate": "2026-01-05",
                "tdescription": "Salary",
                "tpostings": [
                    {"paccount": "assets:bank", "pamount": [{"acommodity": "TWD", "aquantity": {"floatingPoint": 50000}}]},
                    {"paccount": "income:salary", "pamount": [{"acommodity": "TWD", "aquantity": {"floatingPoint": -50000}}]},
                ],
            },
            {
                "tdate": "2026-01-10",
                "tdescription": "Lunch",
                "tpostings": [
                    {"paccount": "expenses:food", "pamount": [{"acommodity": "TWD", "aquantity": {"floatingPoint": 150}}]},
                    {"paccount": "assets:cash", "pamount": [{"acommodity": "TWD", "aquantity": {"floatingPoint": -150}}]},
                ],
            },
            {
                "tdate": "2026-02-01",
                "tdescription": "Train",
                "tpostings": [
                    {"paccount": "expenses:transport", "pamount": [{"acommodity": "TWD", "aquantity": {"floatingPoint": 1200}}]},
                    {"paccount": "assets:cash", "pamount": [{"acommodity": "TWD", "aquantity": {"floatingPoint": -1200}}]},
                ],
            },
        ]

    def test_visualization_data_aggregates_months_categories_and_cumulative_net(self):
        data = build_visualization_data(self.transactions, currency="TWD")
        self.assertEqual(data["currency"], "TWD")
        self.assertEqual(data["flow_mode"], "mixed")
        self.assertEqual(
            data["sign_convention"],
            {
                "basis": "cashflow",
                "income": "credit_positive_debit_negative",
                "expense": "debit_negative_credit_positive",
                "net": "income_plus_expense",
            },
        )
        self.assertEqual(data["totals"], {"income": Decimal("50000"), "expense": Decimal("-1350"), "net": Decimal("48650")})
        self.assertEqual(data["months"]["2026-01"], {"income": Decimal("50000"), "expense": Decimal("-150")})
        self.assertEqual(data["months"]["2026-02"], {"income": Decimal("0"), "expense": Decimal("-1200")})
        self.assertEqual(data["expense_categories"]["expenses:transport"], Decimal("-1200"))
        self.assertEqual(data["cumulative_net"][-1], ("2026-02-01", Decimal("48650")))

    def test_expense_only_data_and_presentation_hide_income(self):
        data = build_visualization_data(self.transactions[1:], currency="TWD")
        self.assertEqual(data["flow_mode"], "expense")
        self.assertEqual(data["totals"], {"income": Decimal("0"), "expense": Decimal("-1350"), "net": Decimal("-1350")})
        presentation = _dashboard_presentation(data)
        self.assertEqual([card[0] for card in presentation["cards"]], ["支出"])
        self.assertEqual([series[0] for series in presentation["month_series"]], ["expense"])
        self.assertEqual(presentation["composition_kind"], "expense")
        self.assertEqual(presentation["account_prefix"], "expenses:")
        self.assertNotIn("收入", str(presentation))

    def test_income_only_data_and_presentation_hide_expense(self):
        data = build_visualization_data(self.transactions[:1], currency="TWD")
        self.assertEqual(data["flow_mode"], "income")
        self.assertEqual(data["totals"], {"income": Decimal("50000"), "expense": Decimal("0"), "net": Decimal("50000")})
        presentation = _dashboard_presentation(data)
        self.assertEqual([card[0] for card in presentation["cards"]], ["收入"])
        self.assertEqual([series[0] for series in presentation["month_series"]], ["income"])
        self.assertEqual(presentation["composition_kind"], "income")
        self.assertEqual(presentation["account_prefix"], "income:")
        self.assertNotIn("支出", str(presentation))

    def test_expense_refund_and_income_reversal_keep_signed_contra_categories_visible(self):
        transactions = [
            {
                "tdate": "2026-03-01",
                "tdescription": "Expense refund",
                "tpostings": [
                    {"paccount": "expenses:food", "pamount": [{"acommodity": "TWD", "aquantity": {"floatingPoint": -300}}]},
                    {"paccount": "assets:cash", "pamount": [{"acommodity": "TWD", "aquantity": {"floatingPoint": 300}}]},
                ],
            },
            {
                "tdate": "2026-03-02",
                "tdescription": "Income reversal",
                "tpostings": [
                    {"paccount": "income:bonus", "pamount": [{"acommodity": "TWD", "aquantity": {"floatingPoint": 100}}]},
                    {"paccount": "assets:cash", "pamount": [{"acommodity": "TWD", "aquantity": {"floatingPoint": -100}}]},
                ],
            },
        ]
        refund_data = build_visualization_data(transactions[:1], currency="TWD")
        self.assertEqual(refund_data["totals"]["expense"], Decimal("300"))
        refund_entries = _composition_entries(refund_data, _dashboard_presentation(refund_data))
        self.assertEqual(refund_entries, [("expenses:food", Decimal("300"), Decimal("300"))])

        reversal_data = build_visualization_data(transactions[1:], currency="TWD")
        self.assertEqual(reversal_data["totals"]["income"], Decimal("-100"))
        reversal_entries = _composition_entries(reversal_data, _dashboard_presentation(reversal_data))
        self.assertEqual(reversal_entries, [("income:bonus", Decimal("100"), Decimal("-100"))])

    def test_income_and_expense_only_dashboards_both_render(self):
        from PIL import Image

        for name, transactions in (("income", self.transactions[:1]), ("expense", self.transactions[1:2])):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                data = build_visualization_data(transactions, currency="TWD")
                output = Path(tmp) / f"{name}.png"
                render_dashboard_png(data, output)
                with Image.open(output) as image:
                    image.load()
                    self.assertEqual(image.size, (1800, 1200))
                    colors = image.convert("RGB").getcolors(maxcolors=10_000_000)
                    self.assertIsNotNone(colors)
                    self.assertGreater(len(colors), 20)

    def test_single_expense_prefers_a_pie_composition(self):
        data = build_visualization_data(self.transactions[1:2], currency="TWD")
        presentation = _dashboard_presentation(data)
        self.assertEqual(presentation["composition_style"], "pie")
        self.assertEqual(presentation["composition_title"], "支出組成")

    def test_visualization_requires_currency_when_multiple_are_present(self):
        mixed = list(self.transactions)
        mixed.append(
            {
                "tdate": "2026-02-02",
                "tdescription": "USD expense",
                "tpostings": [
                    {"paccount": "expenses:travel", "pamount": [{"acommodity": "USD", "aquantity": {"floatingPoint": 10}}]},
                    {"paccount": "assets:cash", "pamount": [{"acommodity": "USD", "aquantity": {"floatingPoint": -10}}]},
                ],
            }
        )
        with self.assertRaisesRegex(ValueError, "multiple currencies"):
            build_visualization_data(mixed)

    def test_dashboard_renderer_creates_large_white_background_png(self):
        from PIL import Image

        data = build_visualization_data(self.transactions, currency="TWD")
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "dashboard.png"
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                render_dashboard_png(
                    data,
                    output,
                    title="財務分析",
                    period_label="2026-01-01 ～ 2026-02-28",
                )
            with Image.open(output) as image:
                image.load()
                size = image.size
                image_format = image.format
                corner = image.convert("RGB").getpixel((0, 0))
                colors = image.convert("RGB").getcolors(maxcolors=10_000_000)
        self.assertEqual(image_format, "PNG")
        self.assertGreaterEqual(size[0], 1400)
        self.assertGreaterEqual(size[1], 900)
        self.assertTrue(all(channel >= 245 for channel in corner))
        self.assertIsNotNone(colors)
        self.assertGreater(len(colors), 20)
        glyph_warnings = [warning for warning in caught if "Glyph" in str(warning.message)]
        self.assertEqual(glyph_warnings, [])


class VisualizationQATemplateTests(unittest.TestCase):
    def test_visualization_qa_template_lists_exactly_50_unique_scenarios(self):
        script = SKILL_DIR / "scripts" / "visualization_qa.py"
        result = subprocess.run(
            [sys.executable, str(script), "--list-json"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        scenarios = json.loads(result.stdout)
        self.assertEqual(len(scenarios), 50)
        self.assertEqual(len({scenario["id"] for scenario in scenarios}), 50)
        for scenario in scenarios:
            self.assertTrue(scenario["name"])
            self.assertIn(scenario["kind"], {"render", "error"})


class FuzzyIngestTests(unittest.TestCase):
    def test_normalized_batch_uses_safe_defaults_and_auto_category(self):
        payload = {
            "defaults": {
                "date": "2026-09-17",
                "kind": "expense",
                "currency": "TWD",
                "credit": "assets:cash",
                "tags": ["source:fuzzy-text"],
            },
            "transactions": [
                {"description": "晚餐", "amount": "1200", "tags": ["inferred:date"]},
            ],
        }
        journal_text, imported, skipped, decisions = build_ingest_transactions(
            payload,
            existing_text="",
            classification_transactions=[],
            classification_rules=[],
        )
        self.assertEqual((imported, skipped), (1, 0))
        self.assertIn("2026-09-17 晚餐", journal_text)
        self.assertIn("expenses:food:dining", journal_text)
        self.assertIn("assets:cash", journal_text)
        self.assertIn("; source:fuzzy-text", journal_text)
        self.assertIn("; inferred:date", journal_text)
        self.assertEqual(decisions[0]["account"], "expenses:food:dining")

    def test_import_id_text_in_a_description_does_not_cause_a_false_duplicate(self):
        payload = {
            "transactions": [
                {
                    "kind": "expense",
                    "description": "Dinner",
                    "amount": "1200",
                    "import_id": "receipt-abc",
                    "tags": ["source:receipt-image"],
                }
            ]
        }
        existing = "2026-09-16 Note import-id:receipt-abc\n    expenses:food  1 TWD\n    assets:cash\n"
        journal_text, imported, skipped, _decisions = build_ingest_transactions(
            payload,
            existing_text=existing,
        )
        self.assertEqual((imported, skipped), (1, 0))
        self.assertIn("; import-id:receipt-abc", journal_text)

    def test_duplicate_record_still_requires_strict_schema_validation(self):
        payload = {
            "transactions": [
                {
                    "description": "Dinner",
                    "amount": "not-a-number",
                    "import_id": "receipt-abc",
                }
            ]
        }
        existing = "2026-09-17 Old\n    ; import-id:receipt-abc\n    expenses:food  1 TWD\n    assets:cash\n"
        with self.assertRaisesRegex(ValueError, "kind|amount|source"):
            build_ingest_transactions(payload, existing_text=existing)

    def test_batch_deduplicates_canonical_import_id_on_transaction_header(self):
        payload = {
            "transactions": [
                {
                    "kind": "expense",
                    "description": "Receipt dinner",
                    "amount": "1200",
                    "import_id": "receipt-inline",
                    "tags": ["source:receipt-image"],
                }
            ]
        }
        existing = (
            "2026-09-17 Old ; import-id:receipt-inline\n"
            "    expenses:food  1 TWD\n"
            "    assets:cash\n"
        )
        journal_text, imported, skipped, _ = build_ingest_transactions(
            payload, existing_text=existing
        )
        self.assertEqual(journal_text, "")
        self.assertEqual((imported, skipped), (0, 1))

    def test_batch_deduplicates_valid_comma_separated_transaction_tags(self):
        payload = {
            "transactions": [
                {
                    "kind": "expense",
                    "description": "Receipt dinner",
                    "amount": "1200",
                    "import_id": "receipt-inline",
                    "tags": ["source:receipt-image"],
                }
            ]
        }
        existing_variants = (
            "2026-09-17 Old ; project:x, import-id:receipt-inline\n"
            "    expenses:food  1 TWD\n"
            "    assets:cash\n",
            "2026/9/17 Old ; project:x, import-id:receipt-inline\n"
            "    expenses:food  1 TWD\n"
            "    assets:cash\n",
            "2026-09-17 Old\n"
            "    ; project:x, import-id:receipt-inline\n"
            "    expenses:food  1 TWD\n"
            "    assets:cash\n",
            "2026-09-16 Account named comment\n"
            "    comment\n"
            "    assets:cash  -1 TWD\n"
            "2026-09-17 Old ; import-id:receipt-inline\n"
            "    expenses:food  1 TWD\n"
            "    assets:cash\n",
        )
        for existing in existing_variants:
            with self.subTest(existing=existing):
                journal_text, imported, skipped, _ = build_ingest_transactions(
                    payload, existing_text=existing
                )
                self.assertEqual(journal_text, "")
                self.assertEqual((imported, skipped), (0, 1))

    def test_batch_ignores_detached_and_noncanonical_import_id_comments(self):
        payload = {
            "transactions": [
                {
                    "kind": "expense",
                    "description": "Receipt dinner",
                    "amount": "1200",
                    "import_id": "receipt-inline",
                    "tags": ["source:receipt-image"],
                }
            ]
        }
        existing_variants = (
            "; import-id:receipt-inline\n\n"
            "2026-09-17 Old\n"
            "    expenses:food  1 TWD\n"
            "    assets:cash\n",
            "2026-09-17 Old ; import-id:receipt-inline/forged\n"
            "    expenses:food  1 TWD\n"
            "    assets:cash\n",
            "2026-09-17 Old\n"
            "    ; note:x import-id:receipt-inline\n"
            "    expenses:food  1 TWD\n"
            "    assets:cash\n",
            "2026-09-17 Old\n"
            "    expenses:food  1 TWD ; import-id:receipt-inline\n"
            "    assets:cash\n",
            "2026-09-17 Old\n"
            "    expenses:food  1 TWD\n"
            "    ; import-id:receipt-inline\n"
            "    assets:cash\n",
            "comment\n"
            "2026-09-17 Old ; import-id:receipt-inline\n"
            "    expenses:food  1 TWD\n"
            "    assets:cash\n"
            "end comment\n",
        )
        for existing in existing_variants:
            with self.subTest(existing=existing):
                journal_text, imported, skipped, _ = build_ingest_transactions(
                    payload, existing_text=existing
                )
                self.assertEqual((imported, skipped), (1, 0))
                self.assertIn("; import-id:receipt-inline", journal_text)

    def test_batch_is_idempotent_when_import_id_already_exists(self):
        payload = {
            "defaults": {
                "date": "2026-09-17",
                "kind": "expense",
                "credit": "assets:cash",
                "tags": ["source:receipt-image"],
            },
            "transactions": [
                {"description": "Receipt dinner", "amount": "1200", "import_id": "receipt-abc"}
            ],
        }
        existing = "2026-09-17 Old\n    ; import-id:receipt-abc\n    expenses:food  1 TWD\n    assets:cash\n"
        journal_text, imported, skipped, _decisions = build_ingest_transactions(
            payload,
            existing_text=existing,
            classification_transactions=[],
            classification_rules=[],
        )
        self.assertEqual(journal_text, "")
        self.assertEqual((imported, skipped), (0, 1))

    def test_batch_rejects_unknown_fields_instead_of_ignoring_agent_typos(self):
        payload = {
            "defaults": {
                "date": "2026-09-17",
                "kind": "expense",
                "credit": "assets:cash",
                "tags": ["source:fuzzy-text"],
            },
            "transactions": [
                {"description": "Dinner", "ammount": "1200"},
            ],
        }
        with self.assertRaisesRegex(ValueError, "unknown fields.*ammount"):
            build_ingest_transactions(
                payload,
                existing_text="",
                classification_transactions=[],
                classification_rules=[],
            )

    def test_cli_ingest_json_writes_multiple_records_in_one_undoable_commit(self):
        script = SKILL_DIR / "scripts" / "finance.py"
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "main.journal"
            batch = Path(tmp) / "batch.json"
            batch.write_text(
                json.dumps(
                    {
                        "defaults": {
                            "date": "2026-09-17",
                            "kind": "expense",
                            "currency": "TWD",
                            "credit": "assets:cash",
                            "tags": ["source:messy-csv"],
                        },
                        "transactions": [
                            {"description": "早餐", "amount": "80"},
                            {"description": "晚餐", "amount": "1200"},
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            base = [sys.executable, str(script), "--journal", str(journal)]
            subprocess.run(base + ["init"], check=True, capture_output=True, text=True)
            before = subprocess.run(
                ["git", "-C", tmp, "rev-list", "--count", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            result = subprocess.run(
                base + ["ingest-json", str(batch)],
                check=False,
                capture_output=True,
                text=True,
            )
            after = subprocess.run(
                ["git", "-C", tmp, "rev-list", "--count", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            text = journal.read_text(encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Records ready: 2", result.stderr)
        self.assertIn("早餐", text)
        self.assertIn("晚餐", text)
        self.assertEqual(int(after), int(before) + 1)

    def test_historical_import_requires_a_stable_import_id(self):
        payload = {
            "transactions": [
                {
                    "kind": "expense",
                    "date": "2024-01-02",
                    "description": "Historical lunch",
                    "amount": "120",
                    "tags": ["source:historical-import"],
                }
            ]
        }
        with self.assertRaisesRegex(ValueError, "historical imports require import_id"):
            build_ingest_transactions(payload, existing_text="")

    def test_historical_import_rejects_inferred_core_fields(self):
        payload = {
            "transactions": [
                {
                    "kind": "expense",
                    "description": "Historical lunch",
                    "amount": "120",
                    "import_id": "legacy-lunch",
                    "tags": ["source:historical-import"],
                }
            ]
        }
        with self.assertRaisesRegex(ValueError, "explicit date, currency, and accounts"):
            build_ingest_transactions(payload, existing_text="")

    def test_historical_import_requires_explicit_expense_category(self):
        payload = {
            "transactions": [
                {
                    "kind": "expense",
                    "date": "2024-01-02",
                    "description": "Historical lunch",
                    "amount": "120",
                    "currency": "TWD",
                    "debit": "auto",
                    "credit": "assets:cash",
                    "import_id": "legacy-lunch",
                    "tags": ["source:historical-import"],
                }
            ]
        }
        with self.assertRaisesRegex(ValueError, "explicit date, currency, and accounts"):
            build_ingest_transactions(payload, existing_text="")

    def test_opening_liability_rejects_reversed_normal_balance(self):
        payload = {
            "transactions": [
                {
                    "kind": "opening-balance",
                    "date": "2024-01-01",
                    "description": "Opening card debt",
                    "amount": "5000",
                    "currency": "TWD",
                    "debit": "liabilities:credit-card",
                    "credit": "equity:opening-balances",
                    "import_id": "legacy-opening-card",
                    "tags": ["source:historical-import"],
                }
            ]
        }
        with self.assertRaisesRegex(ValueError, "opening liability must debit equity.*credit liabilities"):
            build_ingest_transactions(payload, existing_text="")

    def test_historical_kinds_enforce_semantic_account_families(self):
        cases = [
            ("expense", "assets:bank", "expenses:food"),
            ("expense", "expenses:food", "income:salary"),
            ("income", "income:salary", "assets:bank"),
            ("income", "assets:bank", "expenses:food"),
            ("refund", "expenses:food", "assets:bank"),
            ("refund", "assets:bank", "income:salary"),
            ("transfer", "expenses:food", "assets:bank"),
            ("transfer", "assets:bank", "equity:opening-balances"),
        ]
        for kind, debit, credit in cases:
            with self.subTest(kind=kind, debit=debit, credit=credit):
                payload = {
                    "transactions": [
                        {
                            "kind": kind,
                            "date": "2024-01-02",
                            "description": "Historical row",
                            "amount": "120",
                            "currency": "TWD",
                            "debit": debit,
                            "credit": credit,
                            "import_id": f"row-{kind}",
                            "tags": ["source:historical-import"],
                        }
                    ]
                }
                with self.assertRaisesRegex(ValueError, "account families"):
                    build_ingest_transactions(payload, existing_text="")

    def test_historical_refund_and_transfer_accept_valid_account_families(self):
        payload = {
            "transactions": [
                {
                    "kind": "refund", "date": "2024-01-02", "description": "Refund",
                    "amount": "120", "currency": "TWD", "debit": "assets:bank",
                    "credit": "expenses:food", "import_id": "refund-1",
                    "tags": ["source:historical-import"],
                },
                {
                    "kind": "transfer", "date": "2024-01-03", "description": "Card payment",
                    "amount": "500", "currency": "TWD", "debit": "liabilities:card",
                    "credit": "assets:bank", "import_id": "transfer-1",
                    "tags": ["source:historical-import"],
                },
            ]
        }
        journal, imported, skipped, _ = build_ingest_transactions(payload, existing_text="")
        self.assertEqual((imported, skipped), (2, 0))
        self.assertIn("; kind:refund", journal)
        self.assertIn("; kind:transfer", journal)

    def test_opening_balance_rejects_installments_and_fees(self):
        base = {
            "kind": "opening-balance",
            "date": "2024-01-01",
            "description": "Opening bank balance",
            "amount": "5000",
            "currency": "TWD",
            "debit": "assets:bank",
            "credit": "equity:opening-balances",
            "import_id": "legacy-opening-bank",
            "tags": ["source:historical-import"],
        }
        for field, value in (("installments", 2), ("fee", "1"), ("fee", "-1")):
            with self.subTest(field=field, value=value):
                with self.assertRaisesRegex(ValueError, "opening-balance.*installments.*fees"):
                    build_ingest_transactions(
                        {"transactions": [{**base, field: value}]}, existing_text=""
                    )

    def test_opening_balance_is_explicit_and_gets_a_kind_tag(self):
        payload = {
            "transactions": [
                {
                    "kind": "opening-balance",
                    "date": "2024-01-01",
                    "description": "Opening bank balance",
                    "amount": "5000",
                    "currency": "TWD",
                    "debit": "assets:bank",
                    "credit": "equity:opening-balances",
                    "import_id": "legacy-opening-bank",
                    "tags": ["source:historical-import"],
                }
            ]
        }
        journal_text, imported, skipped, _decisions = build_ingest_transactions(
            payload,
            existing_text="",
        )
        self.assertEqual((imported, skipped), (1, 0))
        self.assertIn("; kind:opening-balance", journal_text)
        self.assertIn("assets:bank", journal_text)
        self.assertIn("5000 TWD", journal_text)
        self.assertIn("equity:opening-balances", journal_text)
        self.assertNotIn("\n= ", journal_text)

    def test_ingest_rejects_compound_tags_that_forge_reserved_metadata(self):
        payload = {
            "transactions": [
                {
                    "kind": "expense",
                    "description": "Dinner",
                    "amount": "100",
                    "tags": ["source:fuzzy-text", "project:trip, kind:income"],
                }
            ]
        }
        with self.assertRaisesRegex(ValueError, "Tag|tag"):
            build_ingest_transactions(payload, existing_text="")

    def test_ingest_reserves_kind_and_import_id_tags_for_schema_fields(self):
        for reserved in ("kind:expense", "import-id:bypass"):
            with self.subTest(reserved=reserved):
                payload = {
                    "transactions": [
                        {
                            "kind": "expense",
                            "description": "Dinner",
                            "amount": "100",
                            "tags": ["source:fuzzy-text", reserved],
                        }
                    ]
                }
                with self.assertRaisesRegex(ValueError, "reserved tag"):
                    build_ingest_transactions(payload, existing_text="")

    def test_batch_requires_explicit_transaction_kind(self):
        payload = {
            "defaults": {"tags": ["source:fuzzy-text"]},
            "transactions": [{"description": "Possibly a refund", "amount": "500"}],
        }
        with self.assertRaisesRegex(ValueError, "kind"):
            build_ingest_transactions(payload, existing_text="")

    def test_non_expense_kind_requires_explicit_accounts(self):
        payload = {
            "defaults": {"tags": ["source:messy-csv"]},
            "transactions": [
                {"kind": "income", "description": "Salary", "amount": "50000"},
            ],
        }
        with self.assertRaisesRegex(ValueError, "explicit debit and credit"):
            build_ingest_transactions(payload, existing_text="")

    def test_installment_count_rejects_fractional_and_boolean_json_values(self):
        for invalid_count in (1.9, True):
            with self.subTest(invalid_count=invalid_count):
                payload = {
                    "defaults": {"kind": "expense", "tags": ["source:fuzzy-text"]},
                    "transactions": [
                        {"description": "Phone", "amount": "1200", "installments": invalid_count},
                    ],
                }
                with self.assertRaisesRegex(ValueError, "installments must be a positive integer"):
                    build_ingest_transactions(payload, existing_text="")

    def test_ingest_requires_exactly_one_approved_source_tag(self):
        for tags in ([], ["source:fuzzy-text", "source:receipt-image"], ["source:unknown"]):
            with self.subTest(tags=tags):
                payload = {
                    "defaults": {"kind": "expense", "tags": tags},
                    "transactions": [{"description": "Dinner", "amount": "1200"}],
                }
                with self.assertRaisesRegex(ValueError, "source tag"):
                    build_ingest_transactions(payload, existing_text="")

    def test_json_decimal_literals_preserve_exact_value(self):
        payload = parse_ingest_json('{"amount":9007199254740993.01}')
        self.assertEqual(payload["amount"], Decimal("9007199254740993.01"))

    def test_non_finite_json_constants_and_decimals_are_rejected(self):
        for constant in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(constant=constant):
                with self.assertRaisesRegex(ValueError, "non-finite"):
                    parse_ingest_json(f'{{"amount":{constant}}}')
        for field, value in (("amount", "NaN"), ("fee", "Infinity")):
            with self.subTest(field=field):
                record = {
                    "kind": "expense",
                    "description": "Dinner",
                    "amount": "1200",
                    "tags": ["source:fuzzy-text"],
                    field: value,
                }
                with self.assertRaisesRegex(ValueError, "finite"):
                    build_ingest_transactions({"transactions": [record]}, existing_text="")

    def test_duplicate_json_keys_are_rejected(self):
        raw = '{"transactions":[{"kind":"expense","description":"Dinner","amount":"100","amount":"900","tags":["source:fuzzy-text"]}]}'
        with self.assertRaisesRegex(ValueError, "duplicate key.*amount"):
            parse_ingest_json(raw)

    def test_terminal_control_characters_are_rejected(self):
        payload = {
            "defaults": {"kind": "expense", "tags": ["source:fuzzy-text"]},
            "transactions": [{"description": "Dinner\u001b[31m", "amount": "1200"}],
        }
        with self.assertRaisesRegex(ValueError, "control"):
            build_ingest_transactions(payload, existing_text="")

    def test_concurrent_duplicate_import_ids_are_written_once(self):
        script = SKILL_DIR / "scripts" / "finance.py"
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "main.journal"
            batch = Path(tmp) / "batch.json"
            payload = {
                "transactions": [
                    {
                        "kind": "expense",
                        "description": "Dinner",
                        "amount": "1200",
                        "import_id": "same-receipt",
                        "tags": ["source:receipt-image"],
                    }
                ]
            }
            batch.write_text(json.dumps(payload), encoding="utf-8")
            base = [sys.executable, str(script), "--journal", str(journal)]
            subprocess.run(base + ["init"], check=True, capture_output=True, text=True)
            lock = _acquire_journal_lock(journal)
            try:
                first = subprocess.Popen(base + ["ingest-json", str(batch)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                second = subprocess.Popen(base + ["ingest-json", str(batch)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                time.sleep(0.5)
            finally:
                lock.close()
            first_stdout, first_stderr = first.communicate(timeout=10)
            second_stdout, second_stderr = second.communicate(timeout=10)
            text = journal.read_text(encoding="utf-8")
        self.assertEqual(first.returncode, 0, first_stderr or first_stdout)
        self.assertEqual(second.returncode, 0, second_stderr or second_stdout)
        self.assertEqual(text.count("import-id:same-receipt"), 1)

    def test_ingest_rejects_non_string_schema_values(self):
        cases = [
            ({"kind": "expense", "description": None, "amount": "10", "tags": ["source:fuzzy-text"]}, "description"),
            ({"kind": "income", "description": "Salary", "amount": "10", "debit": "assets:bank", "credit": None, "tags": ["source:fuzzy-text"]}, "credit"),
            ({"kind": "expense", "description": "Dinner", "amount": "10", "currency": None, "tags": ["source:fuzzy-text"]}, "currency"),
            ({"kind": "expense", "description": "Dinner", "amount": "10", "import_id": None, "tags": ["source:fuzzy-text"]}, "import_id"),
            ({"kind": "expense", "description": "Dinner", "amount": "10", "tags": ["source:fuzzy-text", None]}, "tag"),
        ]
        for record, field in cases:
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, field):
                    build_ingest_transactions({"transactions": [record]}, existing_text="")

    def test_false_or_empty_date_is_rejected_instead_of_defaulted(self):
        for invalid_date in (False, ""):
            with self.subTest(invalid_date=invalid_date):
                payload = {
                    "transactions": [
                        {
                            "kind": "expense",
                            "date": invalid_date,
                            "description": "Dinner",
                            "amount": "1200",
                            "tags": ["source:fuzzy-text"],
                        }
                    ]
                }
                with self.assertRaisesRegex(ValueError, "date"):
                    build_ingest_transactions(payload, existing_text="")

    def test_atomic_append_refuses_an_untracked_existing_journal(self):
        with tempfile.TemporaryDirectory() as tmp:
            book = Path(tmp)
            subprocess.run(["git", "init", "-q", str(book)], check=True)
            journal = book / "main.journal"
            journal.write_text("; manually created\n", encoding="utf-8")
            addition = "2026-09-17 Dinner\n    expenses:food  100 TWD\n    assets:cash  -100 TWD\n\n"
            with self.assertRaisesRegex(ValueError, "untracked"):
                _append_validated_and_commit(journal, addition, "test commit")
            self.assertEqual(journal.read_text(encoding="utf-8"), "; manually created\n")

    def test_atomic_append_refuses_unstaged_manual_journal_edits(self):
        with tempfile.TemporaryDirectory() as tmp:
            book = Path(tmp)
            journal = book / "main.journal"
            journal.write_text("; original\n", encoding="utf-8")
            subprocess.run(["git", "init", "-q", str(book)], check=True)
            subprocess.run(["git", "-C", str(book), "config", "user.name", "Test"], check=True)
            subprocess.run(["git", "-C", str(book), "config", "user.email", "test@example.com"], check=True)
            subprocess.run(["git", "-C", str(book), "add", "main.journal"], check=True)
            subprocess.run(["git", "-C", str(book), "commit", "-qm", "init"], check=True)
            journal.write_text("; original\n; manual edit\n", encoding="utf-8")
            addition = "2026-09-17 Dinner\n    expenses:food  100 TWD\n    assets:cash  -100 TWD\n\n"
            with self.assertRaisesRegex(ValueError, "unstaged"):
                _append_validated_and_commit(journal, addition, "test commit")
            self.assertEqual(journal.read_text(encoding="utf-8"), "; original\n; manual edit\n")

    def test_atomic_append_refuses_unrelated_staged_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            book = Path(tmp)
            journal = book / "main.journal"
            journal.write_text("; original\n", encoding="utf-8")
            subprocess.run(["git", "init", "-q", str(book)], check=True)
            subprocess.run(["git", "-C", str(book), "config", "user.name", "Test"], check=True)
            subprocess.run(["git", "-C", str(book), "config", "user.email", "test@example.com"], check=True)
            subprocess.run(["git", "-C", str(book), "add", "main.journal"], check=True)
            subprocess.run(["git", "-C", str(book), "commit", "-qm", "init"], check=True)
            note = book / "note.txt"
            note.write_text("keep staged\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(book), "add", "note.txt"], check=True)
            addition = "2026-09-17 Dinner\n    expenses:food  100 TWD\n    assets:cash  -100 TWD\n\n"
            with self.assertRaisesRegex(ValueError, "staged"):
                _append_validated_and_commit(journal, addition, "test commit")
            self.assertEqual(journal.read_text(encoding="utf-8"), "; original\n")
            staged = subprocess.run(
                ["git", "-C", str(book), "diff", "--cached", "--name-only"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.splitlines()
            self.assertEqual(staged, ["note.txt"])

    def test_regular_journal_check_rejects_fifo(self):
        with tempfile.TemporaryDirectory() as tmp:
            fifo = Path(tmp) / "main.journal"
            os.mkfifo(fifo)
            with self.assertRaisesRegex(ValueError, "regular file"):
                _require_regular_journal(fifo)

    def test_atomic_append_rejects_symlink_journal_without_replacing_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            book = Path(tmp)
            target = book / "actual.journal"
            target.write_text("; original\n", encoding="utf-8")
            link = book / "main.journal"
            link.symlink_to(target)
            addition = "2026-09-17 Dinner\n    expenses:food  100 TWD\n    assets:cash  -100 TWD\n\n"
            with self.assertRaisesRegex(ValueError, "symlink"):
                _append_validated_and_commit(link, addition, "test commit")
            self.assertTrue(link.is_symlink())
            self.assertEqual(target.read_text(encoding="utf-8"), "; original\n")

    def test_atomic_append_restores_original_when_git_commit_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "main.journal"
            original = "; original\n"
            journal.write_text(original, encoding="utf-8")
            addition = "2026-09-17 Dinner\n    expenses:food  100 TWD\n    assets:cash  -100 TWD\n\n"
            with mock.patch(
                "scripts.finance._git_commit",
                side_effect=subprocess.CalledProcessError(1, ["git", "commit"]),
            ):
                with self.assertRaises(subprocess.CalledProcessError):
                    _append_validated_and_commit(journal, addition, "test commit")
            self.assertEqual(journal.read_text(encoding="utf-8"), original)


class AuditTests(unittest.TestCase):
    @staticmethod
    def _transaction(account: str, amount: float, *, tags=None, description="Row"):
        counterpart = -amount
        return {
            "tdate": "2024-01-01",
            "tdescription": description,
            "ttags": tags or [],
            "tpostings": [
                {"paccount": account, "pamount": [{"acommodity": "TWD", "aquantity": {"floatingPoint": amount}}]},
                {"paccount": "assets:cash", "pamount": [{"acommodity": "TWD", "aquantity": {"floatingPoint": counterpart}}]},
            ],
        }

    def test_audit_flags_overwhelmingly_reversed_expenses_with_one_normal_row(self):
        transactions = [self._transaction("expenses:food", -100) for _ in range(4184)]
        transactions.append(self._transaction("expenses:food", 100))
        report = audit_journal(transactions, "")
        codes = {finding["code"] for finding in report["findings"]}
        self.assertFalse(report["ok"])
        self.assertIn("mostly-expenses-credit-normal", codes)

    def test_audit_treats_untagged_contra_only_data_as_warning(self):
        for account, amount, code in (
            ("expenses:food", -100, "contra-only-expenses"),
            ("income:salary", 100, "contra-only-income"),
        ):
            with self.subTest(account=account):
                report = audit_journal(
                    [self._transaction(account, amount) for _ in range(20)], ""
                )
                self.assertTrue(report["ok"])
                self.assertEqual(report["critical_count"], 0)
                self.assertIn(code, {finding["code"] for finding in report["findings"]})

    def test_audit_validates_kind_tags_and_structure_for_nonhistorical_transactions(self):
        duplicate_kind = self._transaction(
            "expenses:food",
            100,
            tags=[["kind", "expense"], ["kind", "refund"], ["source", "receipt-image"]],
        )
        invalid_structure = self._transaction(
            "income:other",
            -100,
            tags=[["kind", "expense"], ["source", "fuzzy-text"]],
        )
        report = audit_journal([duplicate_kind, invalid_structure], "")
        codes = {finding["code"] for finding in report["findings"]}
        self.assertIn("tagged-transaction-invalid-kind", codes)
        self.assertIn("tagged-transaction-invalid-structure", codes)

    def test_audit_validates_optional_source_and_import_id_tags(self):
        transactions = [
            self._transaction(
                "expenses:food",
                100,
                tags=[["kind", "expense"], ["source", "unknown"]],
            ),
            self._transaction(
                "expenses:food",
                100,
                tags=[
                    ["kind", "expense"],
                    ["source", "fuzzy-text"],
                    ["source", "receipt-image"],
                ],
            ),
            self._transaction(
                "expenses:food",
                100,
                tags=[["kind", "expense"], ["import-id", "bad/id"]],
            ),
            self._transaction(
                "expenses:food",
                100,
                tags=[["kind", "expense"], ["import-id", "one"], ["import-id", "two"]],
            ),
        ]
        report = audit_journal(transactions, "")
        codes = {finding["code"] for finding in report["findings"]}
        self.assertIn("tagged-transaction-invalid-source", codes)
        self.assertIn("tagged-transaction-invalid-import-id", codes)

    def test_audit_detects_import_id_collisions_except_complete_installments(self):
        collision = [
            self._transaction(
                "expenses:food",
                100,
                tags=[["kind", "expense"], ["import-id", "same-row"]],
                description=description,
            )
            for description in ("Lunch", "Dinner")
        ]
        report = audit_journal(collision, "")
        self.assertIn(
            "duplicate-import-id",
            {finding["code"] for finding in report["findings"]},
        )

        complete = [
            self._transaction(
                "expenses:electronics",
                100,
                tags=[
                    ["kind", "expense"],
                    ["source", "structured-csv"],
                    ["import-id", "phone-plan"],
                ],
                description=f"Phone [{index}/3]",
            )
            for index in range(1, 4)
        ]
        for transaction, txn_date in zip(
            complete,
            ("2024-01-31", "2024-02-29", "2024-03-31"),
        ):
            transaction["tdate"] = txn_date
        report = audit_journal(complete, "")
        self.assertNotIn(
            "duplicate-import-id",
            {finding["code"] for finding in report["findings"]},
        )

        remainder_group = [
            self._transaction(
                "expenses:electronics",
                amount,
                tags=[
                    ["kind", "expense"],
                    ["source", "structured-csv"],
                    ["import-id", "phone-six-parts"],
                ],
                description=f"Phone [{index}/6]",
            )
            for index, amount in enumerate([16.66, 16.66, 16.66, 16.66, 16.66, 16.70], start=1)
        ]
        for index, transaction in enumerate(remainder_group):
            transaction["tdate"] = add_months(date(2024, 1, 31), index).isoformat()
        report = audit_journal(remainder_group, "")
        self.assertNotIn(
            "duplicate-import-id",
            {finding["code"] for finding in report["findings"]},
        )

    def test_audit_rejects_installment_id_group_with_mismatched_metadata(self):
        transactions = [
            self._transaction(
                account,
                100,
                tags=[
                    ["kind", "expense"],
                    ["source", source],
                    ["import-id", "spoofed-installments"],
                ],
                description=f"Phone [{index}/2]",
            )
            for index, account, source in (
                (1, "expenses:electronics", "structured-csv"),
                (2, "expenses:travel", "historical-import"),
            )
        ]
        transactions[0]["tdate"] = "2024-01-31"
        transactions[1]["tdate"] = "2024-03-31"
        report = audit_journal(transactions, "")
        self.assertIn(
            "duplicate-import-id",
            {finding["code"] for finding in report["findings"]},
        )

    def test_audit_rejects_installment_id_group_with_inconsistent_amounts(self):
        transactions = [
            self._transaction(
                "expenses:electronics",
                amount,
                tags=[
                    ["kind", "expense"],
                    ["source", "structured-csv"],
                    ["import-id", "bad-amount-installments"],
                ],
                description=f"Phone [{index}/2]",
            )
            for index, amount in ((1, 100), (2, 900))
        ]
        transactions[0]["tdate"] = "2024-01-31"
        transactions[1]["tdate"] = "2024-02-29"
        report = audit_journal(transactions, "")
        self.assertIn(
            "duplicate-import-id",
            {finding["code"] for finding in report["findings"]},
        )

    def test_audit_rejects_incomplete_or_ambiguous_installment_id_groups(self):
        descriptions_by_case = {
            "incomplete": ["Phone [1/3]", "Phone [2/3]"],
            "duplicate-index": ["Phone [1/2]", "Phone [1/2]"],
            "different-base": ["Phone [1/2]", "Tablet [2/2]"],
            "different-total": ["Phone [1/2]", "Phone [2/3]"],
        }
        for name, descriptions in descriptions_by_case.items():
            with self.subTest(name=name):
                transactions = [
                    self._transaction(
                        "expenses:electronics",
                        100,
                        tags=[["kind", "expense"], ["import-id", f"group-{name}"]],
                        description=description,
                    )
                    for description in descriptions
                ]
                report = audit_journal(transactions, "")
                self.assertIn(
                    "duplicate-import-id",
                    {finding["code"] for finding in report["findings"]},
                )

    def test_audit_validates_historical_tag_cardinality_and_structure(self):
        transaction = self._transaction(
            "expenses:food",
            -100,
            tags=[
                ["kind", "expense"], ["kind", "refund"],
                ["source", "historical-import"], ["source", "structured-csv"],
                ["import-id", "row-1"], ["import-id", "row-2"],
            ],
        )
        report = audit_journal([transaction], "")
        codes = {finding["code"] for finding in report["findings"]}
        self.assertIn("historical-import-invalid-tags", codes)
        self.assertIn("historical-import-invalid-structure", codes)

    def test_recurring_legitimate_description_is_informational_not_strict_warning(self):
        transactions = [
            self._transaction("expenses:rent", 100, description="Monthly rent")
            for _ in range(12)
        ]
        report = audit_journal(transactions, "")
        finding = next(item for item in report["findings"] if item["code"] == "generic-description-dominates")
        self.assertEqual(finding["severity"], "info")
        self.assertEqual(report["warning_count"], 0)
        self.assertTrue(report["ok"])

    def test_audit_recognizes_compact_commented_rules_but_ignores_comment_blocks(self):
        report = audit_journal(
            [],
            "comment\n= 2024-01-01\nend comment\n"
            "=2024-02-01 ; mistaken opening\n"
            "= 2024/3/1\n"
            "2024-04-02 Account named comment\n"
            "    comment\n"
            "    assets:cash  -1 TWD\n"
            "=2024.04.01 ; another mistaken opening\n",
        )
        finding = next(
            item for item in report["findings"]
            if item["code"] == "date-only-automated-posting"
        )
        self.assertEqual(finding["count"], 3)
        self.assertEqual(finding["lines"], [4, 5, 9])

    def test_audit_resets_comment_state_at_active_file_boundaries(self):
        report = audit_journal(
            [],
            "comment\nignored forever in this file\n\x00hfin-source-boundary\x00\n= 2024-05-01\n",
        )
        self.assertIn(
            "date-only-automated-posting",
            {finding["code"] for finding in report["findings"]},
        )

    def test_audit_flags_bulk_reversed_expenses_and_date_only_automated_rules(self):
        transactions = [
            {
                "tdate": f"2024-01-{day:02d}",
                "tdescription": "Legacy expense",
                "tpostings": [
                    {"paccount": "expenses:food", "pamount": [{"acommodity": "TWD", "aquantity": {"floatingPoint": -100}}]},
                    {"paccount": "assets:cash", "pamount": [{"acommodity": "TWD", "aquantity": {"floatingPoint": 100}}]},
                ],
            }
            for day in range(1, 11)
        ]
        report = audit_journal(
            transactions,
            "= 2024-01-01\n  assets:bank  5000 TWD\n  equity:opening-balances\n",
        )
        codes = {finding["code"] for finding in report["findings"]}
        self.assertFalse(report["ok"])
        self.assertIn("contra-only-expenses", codes)
        self.assertIn("date-only-automated-posting", codes)
        self.assertEqual(report["direction_summary"]["expense"]["credit_count"], 10)

    def test_audit_accepts_normal_expense_income_and_opening_transactions(self):
        transactions = [
            {
                "tdate": "2024-01-01",
                "tdescription": "Opening bank balance",
                "ttags": [["kind", "opening-balance"], ["source", "historical-import"], ["import-id", "opening-bank"]],
                "tpostings": [
                    {"paccount": "assets:bank", "pamount": [{"acommodity": "TWD", "aquantity": {"floatingPoint": 5000}}]},
                    {"paccount": "equity:opening-balances", "pamount": [{"acommodity": "TWD", "aquantity": {"floatingPoint": -5000}}]},
                ],
            },
            {
                "tdate": "2024-01-02",
                "tdescription": "Lunch",
                "ttags": [["kind", "expense"], ["source", "historical-import"], ["import-id", "lunch-1"]],
                "tpostings": [
                    {"paccount": "expenses:food", "pamount": [{"acommodity": "TWD", "aquantity": {"floatingPoint": 120}}]},
                    {"paccount": "assets:cash", "pamount": [{"acommodity": "TWD", "aquantity": {"floatingPoint": -120}}]},
                ],
            },
            {
                "tdate": "2024-01-03",
                "tdescription": "Salary",
                "ttags": [["kind", "income"], ["source", "historical-import"], ["import-id", "salary-1"]],
                "tpostings": [
                    {"paccount": "assets:bank", "pamount": [{"acommodity": "TWD", "aquantity": {"floatingPoint": 30000}}]},
                    {"paccount": "income:salary", "pamount": [{"acommodity": "TWD", "aquantity": {"floatingPoint": -30000}}]},
                ],
            },
        ]
        report = audit_journal(transactions, "")
        self.assertTrue(report["ok"])
        self.assertEqual(report["critical_count"], 0)

    def test_audit_flags_historical_source_rows_without_import_ids(self):
        transactions = [
            {
                "tdate": "2024-01-02",
                "tdescription": "Lunch",
                "ttags": [["kind", "expense"], ["source", "historical-import"]],
                "tpostings": [
                    {"paccount": "expenses:food", "pamount": [{"acommodity": "TWD", "aquantity": {"floatingPoint": 120}}]},
                    {"paccount": "assets:cash", "pamount": [{"acommodity": "TWD", "aquantity": {"floatingPoint": -120}}]},
                ],
            }
        ]
        report = audit_journal(transactions, "")
        self.assertIn("historical-import-missing-id", {item["code"] for item in report["findings"]})


class ImportTests(unittest.TestCase):
    def test_empty_csv_import_is_a_clean_noop(self):
        journal, imported, skipped = import_csv_transactions(
            rows=[],
            existing_text="",
            default_debit="auto",
            credit_account="assets:cash",
            currency="TWD",
            date_column="date",
            description_column="description",
            amount_column="amount",
            id_column="id",
        )
        self.assertEqual((journal, imported, skipped), ("", 0, 0))

    def test_csv_import_rejects_noncanonical_import_id(self):
        rows = [{"date": "2026-09-17", "description": "Dinner", "amount": "120", "id": "bank/123"}]
        with self.assertRaisesRegex(ValueError, "import ID"):
            import_csv_transactions(
                rows=rows,
                existing_text="",
                default_debit="expenses:food",
                credit_account="assets:cash",
                currency="TWD",
                date_column="date",
                description_column="description",
                amount_column="amount",
                id_column="id",
            )

    def test_csv_import_requires_expense_debit_category_prefix_and_payment_account(self):
        rows = [{"date": "2026-09-17", "description": "Dinner", "amount": "120", "id": "row-1"}]
        cases = [
            ({"default_debit": "income:salary"}, "debit"),
            ({"category_prefix": "income"}, "category prefix"),
            ({"credit_account": "income:salary"}, "credit"),
        ]
        base = {
            "rows": rows,
            "existing_text": "",
            "default_debit": "expenses:food",
            "credit_account": "assets:cash",
            "currency": "TWD",
            "date_column": "date",
            "description_column": "description",
            "amount_column": "amount",
            "id_column": "id",
        }
        for changes, message in cases:
            with self.subTest(changes=changes):
                with self.assertRaisesRegex(ValueError, message):
                    import_csv_transactions(**{**base, **changes})

    def test_csv_import_supports_per_row_installment_count_and_deduplication(self):
        rows = list(csv.DictReader(io.StringIO(
            "date,description,amount,installments,id,category\n"
            "2026-09-01,Laptop,30000,3,abc,electronics\n"
            "2026-09-02,Lunch,150,,def,food\n"
        )))
        journal, imported, skipped = import_csv_transactions(
            rows=rows,
            existing_text=(
                "2026-08-01 Existing\n"
                "    ; import-id:abc\n"
                "    expenses:old  1 TWD\n"
                "    assets:cash  -1 TWD\n"
            ),
            default_debit="expenses:uncategorized",
            credit_account="liabilities:card",
            currency="TWD",
            date_column="date",
            description_column="description",
            amount_column="amount",
            id_column="id",
            installment_column="installments",
            category_column="category",
        )
        self.assertEqual(imported, 1)
        self.assertEqual(skipped, 1)
        self.assertIn("2026-09-02 Lunch", journal)
        self.assertIn("expenses:food", journal)
        self.assertIn("; import-id:def", journal)
        self.assertNotIn("Laptop", journal)

    def test_csv_import_rejects_signed_or_negative_rows_in_expense_mode(self):
        rows = [{"date": "2026-09-17", "description": "Refund", "amount": "-980", "id": "refund-1"}]
        with self.assertRaisesRegex(ValueError, "positive expense amount"):
            import_csv_transactions(
                rows=rows,
                existing_text="",
                default_debit="auto",
                credit_account="assets:cash",
                currency="TWD",
                date_column="date",
                description_column="description",
                amount_column="amount",
                id_column="id",
            )

    def test_csv_import_rejects_non_finite_amounts(self):
        for value in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(value=value):
                rows = [
                    {
                        "date": "2026-09-17",
                        "description": "Invalid amount",
                        "amount": value,
                        "id": f"amount-{value}",
                    }
                ]
                with self.assertRaisesRegex(ValueError, "finite"):
                    import_csv_transactions(
                        rows=rows,
                        existing_text="",
                        default_debit="auto",
                        credit_account="assets:cash",
                        currency="TWD",
                        date_column="date",
                        description_column="description",
                        amount_column="amount",
                        id_column="id",
                    )

    def test_duplicate_csv_rows_are_validated_before_they_are_skipped(self):
        rows = [
            {
                "date": "2026-09-17",
                "description": "Invalid duplicate",
                "amount": "NaN",
                "id": "duplicate-amount",
            }
        ]
        existing = (
            "2026-09-01 Existing\n"
            "    ; import-id:duplicate-amount\n"
            "    expenses:food  1 TWD\n"
            "    assets:cash  -1 TWD\n"
        )
        with self.assertRaisesRegex(ValueError, "finite"):
            import_csv_transactions(
                rows=rows,
                existing_text=existing,
                default_debit="auto",
                credit_account="assets:cash",
                currency="TWD",
                date_column="date",
                description_column="description",
                amount_column="amount",
                id_column="id",
            )

    def test_csv_import_requires_nonempty_stable_ids(self):
        rows = [{"date": "2026-09-17", "description": "Dinner", "amount": "120", "id": ""}]
        with self.assertRaisesRegex(ValueError, "requires a non-empty import ID"):
            import_csv_transactions(
                rows=rows,
                existing_text="",
                default_debit="auto",
                credit_account="assets:cash",
                currency="TWD",
                date_column="date",
                description_column="description",
                amount_column="amount",
                id_column="id",
            )

    def test_csv_import_adds_machine_checkable_source_and_kind_tags(self):
        rows = [{"date": "2026-09-17", "description": "Dinner", "amount": "120", "id": "row-1"}]
        journal, imported, skipped = import_csv_transactions(
            rows=rows,
            existing_text="",
            default_debit="expenses:food",
            credit_account="assets:cash",
            currency="TWD",
            date_column="date",
            description_column="description",
            amount_column="amount",
            id_column="id",
        )
        self.assertEqual((imported, skipped), (1, 0))
        self.assertIn("; source:structured-csv", journal)
        self.assertIn("; kind:expense", journal)
        self.assertIn("; import-id:row-1", journal)

    def test_csv_import_auto_classifies_rows_without_category(self):
        rows = [
            {"date": "2026-09-17", "description": "全聯採買", "amount": "980", "id": "groceries-1"}
        ]
        journal, imported, skipped = import_csv_transactions(
            rows=rows,
            existing_text="",
            default_debit="auto",
            credit_account="assets:cash",
            currency="TWD",
            date_column="date",
            description_column="description",
            amount_column="amount",
            id_column="id",
        )
        self.assertEqual((imported, skipped), (1, 0))
        self.assertIn("expenses:food:groceries", journal)


class CliWriteBehaviorTests(unittest.TestCase):
    def test_audit_command_returns_failure_for_reversed_bulk_expenses(self):
        script = SKILL_DIR / "scripts" / "finance.py"
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "main.journal"
            reversed_transaction = (
                "2024-01-01 Legacy expense\n"
                "    expenses:food  -100 TWD\n"
                "    assets:cash  100 TWD\n\n"
            )
            normal_transaction = (
                "2024-01-02 Normal expense\n"
                "    expenses:food  100 TWD\n"
                "    assets:cash  -100 TWD\n\n"
            )
            journal.write_text(
                reversed_transaction * 100 + normal_transaction,
                encoding="utf-8",
            )
            result = subprocess.run(
                [sys.executable, str(script), "--journal", str(journal), "audit"],
                check=False,
                capture_output=True,
                text=True,
            )
        report = json.loads(result.stdout)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(report["ok"])
        self.assertIn("mostly-expenses-credit-normal", {item["code"] for item in report["findings"]})

    def test_audit_checks_included_journal_sources_for_date_only_rules(self):
        script = SKILL_DIR / "scripts" / "finance.py"
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "main.journal"
            included = Path(tmp) / "history.journal"
            journal.write_text("include history.journal\n", encoding="utf-8")
            included.write_text(
                "= 2024-01-01\n"
                "    assets:bank  5000 TWD\n"
                "    equity:opening-balances\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                [sys.executable, str(script), "--journal", str(journal), "audit"],
                check=False,
                capture_output=True,
                text=True,
            )
        report = json.loads(result.stdout)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn(
            "date-only-automated-posting",
            {item["code"] for item in report["findings"]},
        )

    def test_ingest_deduplicates_ids_from_included_journals(self):
        script = SKILL_DIR / "scripts" / "finance.py"
        with tempfile.TemporaryDirectory() as tmp:
            book = Path(tmp)
            journal = book / "main.journal"
            included = book / "history.journal"
            batch = book / "batch.json"
            base = [sys.executable, str(script), "--journal", str(journal)]
            subprocess.run(base + ["init"], check=True, capture_output=True, text=True)
            included.write_text(
                "2024-01-02 Historical lunch\n"
                "    ; source:historical-import\n"
                "    ; kind:expense\n"
                "    ; import-id:included-row-1\n"
                "    expenses:food  120 TWD\n"
                "    assets:cash  -120 TWD\n",
                encoding="utf-8",
            )
            journal.write_text(
                journal.read_text(encoding="utf-8") + "include history.journal\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "-C", tmp, "add", "main.journal", "history.journal"], check=True)
            subprocess.run(["git", "-C", tmp, "commit", "-qm", "include history"], check=True)
            batch.write_text(
                json.dumps(
                    {
                        "transactions": [
                            {
                                "kind": "expense",
                                "date": "2024-01-02",
                                "description": "Historical lunch",
                                "amount": "120",
                                "currency": "TWD",
                                "debit": "expenses:food",
                                "credit": "assets:cash",
                                "import_id": "included-row-1",
                                "tags": ["source:historical-import"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            result = subprocess.run(
                base + ["ingest-json", str(batch)],
                check=False,
                capture_output=True,
                text=True,
            )
            skipped_text = journal.read_text(encoding="utf-8")
            payload = json.loads(batch.read_text(encoding="utf-8"))
            payload["transactions"][0]["description"] = "Unique historical dinner"
            payload["transactions"][0]["import_id"] = "included-row-2"
            batch.write_text(json.dumps(payload), encoding="utf-8")
            unique_result = subprocess.run(
                base + ["ingest-json", str(batch)],
                check=False,
                capture_output=True,
                text=True,
            )
            written_text = journal.read_text(encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("duplicates skipped: 1", result.stderr)
        self.assertNotIn("Historical lunch", skipped_text)
        self.assertEqual(unique_result.returncode, 0, unique_result.stderr)
        self.assertIn("Unique historical dinner", written_text)

    def test_audit_waits_for_the_shared_journal_lock(self):
        script = SKILL_DIR / "scripts" / "finance.py"
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "main.journal"
            journal.write_text("; empty\n", encoding="utf-8")
            base = [sys.executable, str(script), "--journal", str(journal)]
            lock = _acquire_journal_lock(journal)
            try:
                process = subprocess.Popen(
                    base + ["audit"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                time.sleep(0.3)
                blocked = process.poll() is None
            finally:
                lock.close()
            stdout, stderr = process.communicate(timeout=10)
        self.assertTrue(blocked, "audit read the journal without waiting for the shared lock")
        self.assertEqual(process.returncode, 0, stderr or stdout)

    def test_import_csv_requires_id_column(self):
        script = SKILL_DIR / "scripts" / "finance.py"
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "main.journal"
            source = Path(tmp) / "rows.csv"
            source.write_text("date,description,amount\n2024-01-02,Lunch,120\n", encoding="utf-8")
            base = [sys.executable, str(script), "--journal", str(journal)]
            subprocess.run(base + ["init"], check=True, capture_output=True, text=True)
            result = subprocess.run(
                base + ["import-csv", str(source), "--credit", "assets:cash"],
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(result.returncode, 2)
        self.assertIn("--id-column", result.stderr)

    def test_import_csv_rejects_removed_allow_missing_id_option(self):
        script = SKILL_DIR / "scripts" / "finance.py"
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "main.journal"
            source = Path(tmp) / "rows.csv"
            journal.write_text("; empty\n", encoding="utf-8")
            source.write_text(
                "date,description,amount,id\n2024-01-02,Lunch,120,row-1\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "--journal",
                    str(journal),
                    "import-csv",
                    str(source),
                    "--credit",
                    "assets:cash",
                    "--id-column",
                    "id",
                    "--allow-missing-id",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(result.returncode, 2)
        self.assertIn("unrecognized arguments: --allow-missing-id", result.stderr)

    def test_init_rejects_a_symlink_journal(self):
        script = SKILL_DIR / "scripts" / "finance.py"
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "actual.journal"
            target.write_text("; original\n", encoding="utf-8")
            journal = Path(tmp) / "main.journal"
            journal.symlink_to(target)
            result = subprocess.run(
                [sys.executable, str(script), "--journal", str(journal), "init"],
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("symlink", result.stderr)

    def test_add_writes_by_default_without_confirmation_flag(self):
        script = SKILL_DIR / "scripts" / "finance.py"
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "main.journal"
            subprocess.run(
                [sys.executable, str(script), "--journal", str(journal), "init"],
                check=True,
                capture_output=True,
                text=True,
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "--journal",
                    str(journal),
                    "add",
                    "--date",
                    "2026-09-16",
                    "--description",
                    "Lunch",
                    "--amount",
                    "150",
                    "--debit",
                    "expenses:food",
                    "--credit",
                    "assets:cash",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            text = journal.read_text(encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("2026-09-16 Lunch", text)

    def test_preview_flag_does_not_write(self):
        script = SKILL_DIR / "scripts" / "finance.py"
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "main.journal"
            subprocess.run(
                [sys.executable, str(script), "--journal", str(journal), "init"],
                check=True,
                capture_output=True,
                text=True,
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "--journal",
                    str(journal),
                    "add",
                    "--date",
                    "2026-09-16",
                    "--description",
                    "Lunch",
                    "--amount",
                    "150",
                    "--debit",
                    "expenses:food",
                    "--credit",
                    "assets:cash",
                    "--preview",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            text = journal.read_text(encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("2026-09-16 Lunch", text)
        self.assertIn("Preview only", result.stderr)
        self.assertIn("2026-09-16 Lunch", result.stdout)

    def test_undo_reverts_the_latest_journal_action_without_removing_earlier_actions(self):
        script = SKILL_DIR / "scripts" / "finance.py"
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "main.journal"
            base = [sys.executable, str(script), "--journal", str(journal)]
            subprocess.run(base + ["init"], check=True, capture_output=True, text=True)
            for description, amount in (("Breakfast", "100"), ("Lunch", "150")):
                subprocess.run(
                    base
                    + [
                        "add",
                        "--date",
                        "2026-09-16",
                        "--description",
                        description,
                        "--amount",
                        amount,
                        "--debit",
                        "expenses:food",
                        "--credit",
                        "assets:cash",
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            result = subprocess.run(base + ["undo"], check=False, capture_output=True, text=True)
            text = journal.read_text(encoding="utf-8")
            log = subprocess.run(
                ["git", "-C", tmp, "log", "-1", "--format=%s"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Breakfast", text)
        self.assertNotIn("Lunch", text)
        self.assertIn("Undo finance action", log)

    def test_undo_waits_for_the_shared_journal_lock(self):
        script = SKILL_DIR / "scripts" / "finance.py"
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "main.journal"
            base = [sys.executable, str(script), "--journal", str(journal)]
            subprocess.run(base + ["init"], check=True, capture_output=True, text=True)
            subprocess.run(
                base + [
                    "add", "--date", "2026-09-17", "--description", "Dinner",
                    "--amount", "100", "--debit", "expenses:food", "--credit", "assets:cash",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            lock = _acquire_journal_lock(journal)
            try:
                process = subprocess.Popen(base + ["undo"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                time.sleep(0.3)
                blocked = process.poll() is None
            finally:
                lock.close()
            stdout, stderr = process.communicate(timeout=10)
        self.assertTrue(blocked, "undo modified the journal without waiting for the shared lock")
        self.assertEqual(process.returncode, 0, stderr or stdout)

    def test_add_requires_explicit_auto_mode_when_debit_is_omitted(self):
        script = SKILL_DIR / "scripts" / "finance.py"
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "main.journal"
            base = [sys.executable, str(script), "--journal", str(journal)]
            subprocess.run(base + ["init"], check=True, capture_output=True, text=True)
            result = subprocess.run(
                base
                + [
                    "add",
                    "--date",
                    "2026-09-17",
                    "--description",
                    "Salary",
                    "--amount",
                    "50000",
                    "--credit",
                    "income:salary",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--debit", result.stderr)

    def test_add_auto_classifies_when_debit_is_auto(self):
        script = SKILL_DIR / "scripts" / "finance.py"
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "main.journal"
            base = [sys.executable, str(script), "--journal", str(journal)]
            subprocess.run(base + ["init"], check=True, capture_output=True, text=True)
            result = subprocess.run(
                base
                + [
                    "add",
                    "--date",
                    "2026-09-17",
                    "--description",
                    "全聯福利中心買菜",
                    "--amount",
                    "680",
                    "--debit",
                    "auto",
                    "--credit",
                    "assets:cash",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            journal_text = journal.read_text(encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("expenses:food:groceries", journal_text)
        self.assertIn("Auto-category:", result.stderr)

    def test_classify_command_prefers_custom_rules_file(self):
        script = SKILL_DIR / "scripts" / "finance.py"
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "main.journal"
            base = [sys.executable, str(script), "--journal", str(journal)]
            subprocess.run(base + ["init"], check=True, capture_output=True, text=True)
            (Path(tmp) / "classification-rules.json").write_text(
                json.dumps(
                    {"rules": [{"pattern": "毛孩市集", "account": "expenses:pets:supplies"}]},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            result = subprocess.run(
                base + ["classify", "--description", "毛孩市集飼料"],
                check=False,
                capture_output=True,
                text=True,
            )
            decision = json.loads(result.stdout)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(decision["account"], "expenses:pets:supplies")
        self.assertEqual(decision["source"], "custom-rule")

    def test_visualize_command_generates_dashboard_for_filtered_period(self):
        from PIL import Image

        script = SKILL_DIR / "scripts" / "finance.py"
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "main.journal"
            output = Path(tmp) / "analysis.png"
            base = [sys.executable, str(script), "--journal", str(journal)]
            subprocess.run(base + ["init"], check=True, capture_output=True, text=True)
            for description, amount, debit, credit in (
                ("Salary", "50000", "assets:bank", "income:salary"),
                ("Lunch", "150", "expenses:food", "assets:cash"),
            ):
                subprocess.run(
                    base
                    + [
                        "add",
                        "--date",
                        "2026-09-16",
                        "--description",
                        description,
                        "--amount",
                        amount,
                        "--debit",
                        debit,
                        "--credit",
                        credit,
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            result = subprocess.run(
                base
                + [
                    "visualize",
                    "--period",
                    "2026-09-01..2026-09-30",
                    "--currency",
                    "TWD",
                    "--title",
                    "九月財務分析",
                    "--output",
                    str(output),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            with Image.open(output) as image:
                image.load()
                image_format = image.format
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(image_format, "PNG")
        self.assertIn(str(output), result.stdout)

    def test_delete_search_waits_for_the_shared_journal_lock(self):
        script = SKILL_DIR / "scripts" / "finance.py"
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "main.journal"
            base = [sys.executable, str(script), "--journal", str(journal)]
            subprocess.run(base + ["init"], check=True, capture_output=True, text=True)
            subprocess.run(
                base + [
                    "add", "--date", "2026-09-17", "--description", "Dinner",
                    "--amount", "100", "--debit", "expenses:food", "--credit", "assets:cash",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            lock = _acquire_journal_lock(journal)
            try:
                process = subprocess.Popen(
                    base + ["delete", "--period", "2026-09-17", "--description", "Dinner"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                time.sleep(0.3)
                blocked = process.poll() is None
            finally:
                lock.close()
            stdout, stderr = process.communicate(timeout=10)
        self.assertTrue(blocked, "delete read the journal without waiting for the shared lock")
        self.assertEqual(process.returncode, 3, stderr or stdout)

    def test_delete_requires_matching_confirmation_token_and_can_be_undone(self):
        script = SKILL_DIR / "scripts" / "finance.py"
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "main.journal"
            base = [sys.executable, str(script), "--journal", str(journal)]
            subprocess.run(base + ["init"], check=True, capture_output=True, text=True)
            for description, amount in (("Breakfast", "100"), ("Lunch", "150")):
                subprocess.run(
                    base
                    + [
                        "add",
                        "--date",
                        "2026-09-16",
                        "--description",
                        description,
                        "--amount",
                        amount,
                        "--debit",
                        "expenses:food",
                        "--credit",
                        "assets:cash",
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            preview = subprocess.run(
                base + ["delete", "--description", "^Lunch$"],
                check=False,
                capture_output=True,
                text=True,
            )
            unchanged = journal.read_text(encoding="utf-8")
            token_match = re.search(r"Confirmation token: ([0-9a-f]+)", preview.stdout)
            self.assertIsNotNone(token_match, preview.stdout)
            confirmed = subprocess.run(
                base + ["delete", "--description", "^Lunch$", "--confirm", token_match.group(1)],
                check=False,
                capture_output=True,
                text=True,
            )
            deleted = journal.read_text(encoding="utf-8")
            subprocess.run(base + ["undo"], check=True, capture_output=True, text=True)
            restored = journal.read_text(encoding="utf-8")
        self.assertEqual(preview.returncode, 3, preview.stderr)
        self.assertIn("Breakfast", unchanged)
        self.assertIn("Lunch", unchanged)
        self.assertEqual(confirmed.returncode, 0, confirmed.stderr)
        self.assertIn("Breakfast", deleted)
        self.assertNotIn("Lunch", deleted)
        self.assertIn("Lunch", restored)


class HledgerIntegrationTests(unittest.TestCase):
    def test_long_debit_credit_and_fee_accounts_keep_amount_separators(self):
        accounts = {
            "debit": "expenses:electronics:phones:personal:replacement",
            "credit": "liabilities:credit-card:provider:platinum:primary",
            "fee": "expenses:fees:installments:provider:processing:monthly",
        }
        journal = render_installments(
            start=date(2026, 10, 1),
            description="Phone",
            total=Decimal("1200"),
            count=2,
            debit_account=accounts["debit"],
            credit_account=accounts["credit"],
            currency="TWD",
            fee_per_installment=Decimal("10"),
            fee_account=accounts["fee"],
        )
        for account in accounts.values():
            self.assertRegex(journal, rf"(?m)^    {re.escape(account)}  +-?\d")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "book.journal"
            path.write_text(journal, encoding="utf-8")
            result = subprocess.run(
                ["hledger", "-f", str(path), "check"],
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_generated_installments_pass_hledger_validation(self):
        journal = render_installments(
            start=date(2026, 10, 1),
            description="Laptop",
            total=Decimal("30000"),
            count=6,
            debit_account="expenses:electronics",
            credit_account="liabilities:card",
            currency="TWD",
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "book.journal"
            path.write_text(journal, encoding="utf-8")
            result = subprocess.run(
                ["hledger", "-f", str(path), "check"],
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
