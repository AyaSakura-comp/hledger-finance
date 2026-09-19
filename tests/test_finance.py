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
from decimal import Decimal, localcontext
from pathlib import Path
from unittest import mock

SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_DIR))

from scripts.finance import (  # noqa: E402
    _acquire_journal_lock,
    _amount_decimal,
    _append_validated_and_commit,
    _axis_number,
    _composition_entries,
    audit_journal,
    _dashboard_presentation,
    _require_regular_journal,
    _chart_amount,
    _ellipsize,
    _load_payment_account_rules,
    MAX_LOAN_PERIODS,
    add_months,
    build_hledger_query,
    build_loan_resume_schedule,
    build_ingest_transactions,
    build_visualization_data,
    classify_description,
    compute_stats,
    import_csv_transactions,
    parse_ingest_json,
    parse_period,
    render_cross_currency_transfer,
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


class LoanResumeScheduleTests(unittest.TestCase):
    def test_exact_resumed_schedule_uses_half_up_interest_and_clears_balance(self):
        journal, periods = build_loan_resume_schedule(
            loan_id="personal-3200000",
            start=date(2026, 10, 8),
            description="Personal loan payment",
            principal=Decimal("1907335"),
            annual_rate=Decimal("2.1"),
            payment=Decimal("56229"),
            paid_periods=25,
            remaining_periods=35,
            currency="TWD",
            liability_account="liabilities:loans:personal",
            interest_account="expenses:interest:loans",
            payment_account="assets:bank:checking",
            rounding_quantum=Decimal("1"),
        )

        self.assertEqual(len(periods), 35)
        self.assertEqual(
            periods[0],
            {
                "period": 26,
                "total_periods": 60,
                "date": date(2026, 10, 8),
                "opening_principal": Decimal("1907335"),
                "principal": Decimal("52891"),
                "interest": Decimal("3338"),
                "payment": Decimal("56229"),
                "closing_principal": Decimal("1854444"),
                "import_id": "loan.personal-3200000.period.26",
            },
        )
        self.assertEqual(periods[-1]["period"], 60)
        self.assertEqual(periods[-1]["principal"], Decimal("56128"))
        self.assertEqual(periods[-1]["interest"], Decimal("98"))
        self.assertEqual(periods[-1]["payment"], Decimal("56226"))
        self.assertEqual(periods[-1]["closing_principal"], Decimal("0"))
        self.assertTrue(
            all(period["payment"] == Decimal("56229") for period in periods[:-1])
        )
        self.assertEqual(
            sum((period["principal"] for period in periods), Decimal("0")),
            Decimal("1907335"),
        )
        import_ids = [period["import_id"] for period in periods]
        self.assertEqual(len(import_ids), len(set(import_ids)))
        self.assertIn("2026-10-08 Personal loan payment [26/60]", journal)
        self.assertIn("liabilities:loans:personal", journal)
        self.assertIn("expenses:interest:loans", journal)
        self.assertIn("assets:bank:checking", journal)
        self.assertIn("; import-id:loan.personal-3200000.period.26", journal)
        self.assertIn("; import-id:loan.personal-3200000.period.60", journal)
        self.assertEqual(journal.count("; source:scheduled-loan"), 35)

    def test_schedule_reuses_month_clamping_and_absolute_numbering_from_zero(self):
        _journal, periods = build_loan_resume_schedule(
            loan_id="zero-paid",
            start=date(2026, 1, 31),
            description="Loan",
            principal=Decimal("300"),
            annual_rate=Decimal("0"),
            payment=Decimal("100"),
            paid_periods=0,
            remaining_periods=3,
            currency="TWD",
            liability_account="liabilities:loan",
            interest_account="expenses:interest",
            payment_account="assets:bank",
            rounding_quantum=Decimal("1"),
        )
        self.assertEqual([row["period"] for row in periods], [1, 2, 3])
        self.assertEqual(
            [row["date"] for row in periods],
            [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31)],
        )

    def test_zero_rate_keeps_explicit_zero_interest_postings(self):
        journal, periods = build_loan_resume_schedule(
            loan_id="zero-rate",
            start=date(2026, 4, 1),
            description="Zero-rate loan",
            principal=Decimal("200"),
            annual_rate=Decimal("0"),
            payment=Decimal("100"),
            paid_periods=0,
            remaining_periods=2,
            currency="TWD",
            liability_account="liabilities:loan",
            interest_account="expenses:interest",
            payment_account="assets:bank",
            rounding_quantum=Decimal("1"),
        )
        self.assertTrue(all(row["interest"] == 0 for row in periods))
        self.assertEqual(journal.count("expenses:interest"), len(periods))
        self.assertIn("0 TWD", journal)

    def test_decimal_currency_rounds_half_up_to_requested_quantum(self):
        journal, periods = build_loan_resume_schedule(
            loan_id="decimal-usd",
            start=date(2026, 1, 15),
            description="USD loan",
            principal=Decimal("100.00"),
            annual_rate=Decimal("12"),
            payment=Decimal("34.00"),
            paid_periods=0,
            remaining_periods=3,
            currency="USD",
            liability_account="liabilities:loan:usd",
            interest_account="expenses:interest:usd",
            payment_account="assets:bank:usd",
            rounding_quantum=Decimal("0.01"),
        )
        self.assertEqual(periods[0]["interest"], Decimal("1.00"))
        self.assertEqual(periods[0]["principal"], Decimal("33.00"))
        self.assertEqual(periods[-1]["closing_principal"], Decimal("0.00"))
        self.assertIn("1 USD", journal)

    @staticmethod
    def _valid_kwargs():
        return {
            "loan_id": "safe-loan",
            "start": date(2026, 1, 1),
            "description": "Loan payment",
            "principal": Decimal("300"),
            "annual_rate": Decimal("0"),
            "payment": Decimal("100"),
            "paid_periods": 0,
            "remaining_periods": 3,
            "currency": "TWD",
            "liability_account": "liabilities:loans:personal",
            "interest_account": "expenses:interest:loans",
            "payment_account": "assets:bank:checking",
            "rounding_quantum": Decimal("1"),
        }

    def test_reviewer_high_precision_fixture_preserves_every_principal_digit(self):
        principal = Decimal("9999999999999999999.999999999999999999")
        regular_principal = Decimal("3333333333333333333.333333333333333333")
        _journal, periods = build_loan_resume_schedule(
            **{
                **self._valid_kwargs(),
                "principal": principal,
                "annual_rate": Decimal("0"),
                "payment": regular_principal,
                "remaining_periods": 3,
                "rounding_quantum": Decimal("1e-18"),
            }
        )

        self.assertEqual(
            [period["principal"] for period in periods],
            [regular_principal, regular_principal, regular_principal],
        )
        with localcontext() as context:
            context.prec = 100
            self.assertEqual(
                sum((period["principal"] for period in periods), Decimal("0")),
                principal,
            )
        self.assertEqual(periods[-1]["closing_principal"], Decimal("0"))

    def test_high_precision_rate_and_quantum_do_not_use_ambient_context(self):
        principal = Decimal("9999999999999999999.999999999999999999")
        with localcontext() as context:
            context.prec = 6
            _journal, periods = build_loan_resume_schedule(
                **{
                    **self._valid_kwargs(),
                    "principal": principal,
                    "annual_rate": Decimal("1"),
                    "payment": Decimal("6000000000000000000"),
                    "remaining_periods": 2,
                    "rounding_quantum": Decimal("1e-18"),
                }
            )

        self.assertEqual(
            periods[0]["interest"],
            Decimal("8333333333333333.333333333333333333"),
        )
        self.assertEqual(
            periods[0]["principal"],
            Decimal("5991666666666666666.666666666666666667"),
        )
        with localcontext() as context:
            context.prec = 100
            self.assertEqual(
                sum((period["principal"] for period in periods), Decimal("0")),
                principal,
            )
        self.assertEqual(periods[-1]["closing_principal"], Decimal("0"))

    def test_rejects_invalid_decimal_inputs(self):
        cases = (
            ("principal", Decimal("0"), "principal.*positive"),
            ("principal", Decimal("NaN"), "principal.*finite"),
            ("annual_rate", Decimal("-0.01"), "annual rate.*nonnegative"),
            ("annual_rate", Decimal("Infinity"), "annual rate.*finite"),
            ("payment", Decimal("0"), "payment.*positive"),
            ("payment", Decimal("NaN"), "payment.*finite"),
        )
        for field, value, message in cases:
            with self.subTest(field=field, value=value):
                with self.assertRaisesRegex(ValueError, message):
                    build_loan_resume_schedule(**{**self._valid_kwargs(), field: value})

    def test_rejects_invalid_period_counts(self):
        cases = (
            ("paid_periods", -1, "paid periods.*nonnegative"),
            ("paid_periods", True, "paid periods.*integer"),
            ("remaining_periods", 0, "remaining periods.*positive"),
            ("remaining_periods", Decimal("2"), "remaining periods.*integer"),
        )
        for field, value, message in cases:
            with self.subTest(field=field, value=value):
                with self.assertRaisesRegex(ValueError, message):
                    build_loan_resume_schedule(**{**self._valid_kwargs(), field: value})

    def test_rejects_period_inputs_and_total_above_defensive_limit(self):
        cases = (
            ({"paid_periods": MAX_LOAN_PERIODS + 1}, "paid periods.*1200"),
            ({"remaining_periods": MAX_LOAN_PERIODS + 1}, "remaining periods.*1200"),
            (
                {"paid_periods": MAX_LOAN_PERIODS, "remaining_periods": 1},
                "total periods.*1200",
            ),
        )
        for changes, message in cases:
            with self.subTest(changes=changes):
                with self.assertRaisesRegex(ValueError, message):
                    build_loan_resume_schedule(**{**self._valid_kwargs(), **changes})

    def test_rejects_rounding_quantum_beyond_documented_precision(self):
        for value in ("1e-19", "1e-1000"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "18 decimal places"):
                    build_loan_resume_schedule(
                        **{
                            **self._valid_kwargs(),
                            "rounding_quantum": Decimal(value),
                        }
                    )
        _journal, periods = build_loan_resume_schedule(
            **{
                **self._valid_kwargs(),
                "rounding_quantum": Decimal("1e-18"),
            }
        )
        self.assertEqual(len(periods), 3)

    def test_rejects_loan_values_outside_documented_magnitude_and_scale_limits(self):
        cases = (
            ("principal", "1e-1000000000"),
            ("principal", "1e1000000000"),
            ("payment", "1e-1000000000"),
            ("payment", "1e1000000000"),
            ("annual_rate", "1e-1000000000"),
            ("annual_rate", "1e1000000000"),
            ("principal", "123456789012345678901234567890123456789"),
            ("payment", "1.0000000000000000001"),
        )
        for field, text in cases:
            with self.subTest(field=field, text=text):
                with self.assertRaisesRegex(ValueError, "safe (magnitude|precision|scale)"):
                    build_loan_resume_schedule(
                        **{**self._valid_kwargs(), field: Decimal(text)}
                    )

    def test_decimal_arithmetic_failures_are_reported_as_value_errors(self):
        with self.assertRaisesRegex(ValueError, "safe magnitude"):
            build_loan_resume_schedule(
                **{
                    **self._valid_kwargs(),
                    "principal": Decimal("1e999999"),
                    "annual_rate": Decimal("1"),
                }
            )

    def test_rejects_unsupported_rounding_quantums(self):
        for value in ("0", "-0.01", "NaN", "Infinity", "10", "0.05"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "Rounding quantum"):
                    build_loan_resume_schedule(
                        **{
                            **self._valid_kwargs(),
                            "rounding_quantum": Decimal(value),
                        }
                    )

    def test_rejects_invalid_loan_ids(self):
        for loan_id in ("", "loan/id", "loan id; forged", "貸款"):
            with self.subTest(loan_id=loan_id):
                with self.assertRaisesRegex(ValueError, "Loan ID"):
                    build_loan_resume_schedule(
                        **{**self._valid_kwargs(), "loan_id": loan_id}
                    )

    def test_rejects_negative_amortization_and_early_payoff(self):
        with self.assertRaisesRegex(ValueError, "negative amortization"):
            build_loan_resume_schedule(
                **{
                    **self._valid_kwargs(),
                    "principal": Decimal("1000"),
                    "annual_rate": Decimal("120"),
                    "payment": Decimal("100"),
                }
            )
        with self.assertRaisesRegex(ValueError, "pay off before final period"):
            build_loan_resume_schedule(
                **{
                    **self._valid_kwargs(),
                    "principal": Decimal("100"),
                    "payment": Decimal("100"),
                }
            )

    def test_rejects_unsafe_text_and_wrong_account_roles(self):
        cases = (
            ({"description": "Loan; forged"}, "Description"),
            ({"currency": "TWD; forged"}, "Currency"),
            ({"liability_account": "(liabilities:loan)"}, "Account"),
            ({"liability_account": "assets:loan"}, "Loan account.*liabilities"),
            ({"interest_account": "assets:interest"}, "Interest account.*expenses"),
            ({"payment_account": "expenses:cash"}, "Payment account.*assets.*liabilities"),
        )
        for changes, message in cases:
            with self.subTest(changes=changes):
                with self.assertRaisesRegex(ValueError, message):
                    build_loan_resume_schedule(**{**self._valid_kwargs(), **changes})

    def test_rejects_conflicting_role_accounts(self):
        cases = (
            {"payment_account": "liabilities:loans:personal"},
            {"payment_account": "liabilities:loans"},
            {"payment_account": "liabilities:loans:personal:checking"},
        )
        for changes in cases:
            with self.subTest(changes=changes):
                with self.assertRaisesRegex(ValueError, "accounts.*overlap"):
                    build_loan_resume_schedule(**{**self._valid_kwargs(), **changes})

    def test_rejects_non_date_start(self):
        with self.assertRaisesRegex(ValueError, "Start.*date"):
            build_loan_resume_schedule(
                **{**self._valid_kwargs(), "start": "2026-01-01"}
            )


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


class PaymentAccountRoutingTests(unittest.TestCase):
    RULES = {
        "currency_accounts": {
            "TWD": "assets:cash:wallet",
            "JPY": "assets:bank:jpy-account",
            "USD": "assets:bank:usd-account",
        },
        "require_match": True,
    }

    def test_optional_rules_file_has_backward_compatible_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            rules = _load_payment_account_rules(Path(tmp) / "main.journal")
        self.assertEqual(rules, {"currency_accounts": {}, "require_match": False})

    def test_rules_loader_accepts_a_valid_regular_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "main.journal"
            (Path(tmp) / "payment-account-rules.json").write_text(
                json.dumps(self.RULES), encoding="utf-8"
            )
            self.assertEqual(_load_payment_account_rules(journal), self.RULES)

    def test_rules_loader_rejects_symlinks_including_dangling_and_looping(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for case in ("regular-target", "dangling", "loop"):
                with self.subTest(case=case):
                    case_dir = root / case
                    case_dir.mkdir()
                    path = case_dir / "payment-account-rules.json"
                    if case == "regular-target":
                        target = case_dir / "target.json"
                        target.write_text(json.dumps(self.RULES), encoding="utf-8")
                        path.symlink_to(target)
                    elif case == "dangling":
                        path.symlink_to(case_dir / "missing.json")
                    else:
                        path.symlink_to(path.name)
                    with self.assertRaisesRegex(ValueError, "symlink"):
                        _load_payment_account_rules(case_dir / "main.journal")

    def test_rules_loader_rejects_fifo_without_blocking(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "payment-account-rules.json"
            os.mkfifo(path)
            probe = (
                "from pathlib import Path; "
                "from scripts.finance import _load_payment_account_rules; "
                "journal = Path(__import__('sys').argv[1]); "
                "\ntry: _load_payment_account_rules(journal)"
                "\nexcept ValueError as error: assert 'regular file' in str(error), error"
                "\nelse: raise AssertionError('FIFO was accepted')"
            )
            result = subprocess.run(
                [sys.executable, "-c", probe, str(Path(tmp) / "main.journal")],
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
                cwd=SKILL_DIR,
            )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_rules_loader_rejects_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "payment-account-rules.json"
            path.mkdir()
            with self.assertRaisesRegex(ValueError, "regular file"):
                _load_payment_account_rules(Path(tmp) / "main.journal")

    def test_rules_loader_does_not_treat_filesystem_errors_as_absence(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "main.journal"
            with mock.patch(
                "scripts.finance.os.lstat", side_effect=PermissionError("permission denied")
            ):
                with self.assertRaisesRegex(ValueError, "inspect.*permission denied"):
                    _load_payment_account_rules(journal)

    def test_rules_loader_does_not_follow_a_symlink_swapped_in_before_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "payment-account-rules.json"
            target = root / "target.json"
            raw_rules = json.dumps(self.RULES)
            path.write_text(raw_rules, encoding="utf-8")
            target.write_text(raw_rules, encoding="utf-8")
            real_open = os.open

            def swap_then_open(open_path, flags, *args, **kwargs):
                path.unlink()
                path.symlink_to(target)
                return real_open(open_path, flags, *args, **kwargs)

            with mock.patch("scripts.finance.os.open", side_effect=swap_then_open):
                with self.assertRaisesRegex(ValueError, "symlink"):
                    _load_payment_account_rules(root / "main.journal")

    def test_rules_file_uses_a_strict_non_executable_schema(self):
        invalid_payloads = (
            "{not json",
            json.dumps({}),
            json.dumps({"currency_accounts": {}}),
            json.dumps({"require_match": False}),
            json.dumps({"currency_accounts": {}, "require_match": False, "unknown": 1}),
            json.dumps({"currency_accounts": [], "require_match": False}),
            json.dumps({"currency_accounts": {"TWD": 123}, "require_match": False}),
            json.dumps({"currency_accounts": {"$TWD": "assets:cash"}, "require_match": False}),
            json.dumps({"currency_accounts": {"TWD": "assets:cash ; injected"}, "require_match": False}),
            json.dumps({"currency_accounts": {"TWD": "expenses:food"}, "require_match": False}),
            json.dumps({"currency_accounts": {}, "require_match": "false"}),
            '{"currency_accounts":{},"currency_accounts":{},"require_match":false}',
        )
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "main.journal"
            path = Path(tmp) / "payment-account-rules.json"
            for raw in invalid_payloads:
                with self.subTest(raw=raw):
                    path.write_text(raw, encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, "payment account rules"):
                        _load_payment_account_rules(journal)

    def test_expenses_route_by_resolved_currency_and_add_audit_tags(self):
        for currency, account in self.RULES["currency_accounts"].items():
            with self.subTest(currency=currency):
                record = {
                    "kind": "expense",
                    "date": "2026-09-18",
                    "description": f"{currency} lunch",
                    "amount": "100",
                    "tags": ["source:fuzzy-text"],
                }
                if currency != "TWD":
                    record["currency"] = currency
                payload = {"transactions": [record]}
                journal, imported, skipped, _ = build_ingest_transactions(
                    payload,
                    existing_text="",
                    payment_account_rules=self.RULES,
                )
                self.assertEqual((imported, skipped), (1, 0))
                self.assertIn(account, journal)
                self.assertIn("; inferred:payment-account", journal)
                self.assertIn("; inferred:currency-routing", journal)

    def test_explicit_record_or_default_credit_always_wins(self):
        cases = (
            ({"credit": "liabilities:card"}, {}, "liabilities:card"),
            ({}, {"credit": "assets:cash:explicit"}, "assets:cash:explicit"),
        )
        for record_extra, defaults_extra, expected in cases:
            with self.subTest(expected=expected):
                payload = {
                    "defaults": {
                        "kind": "expense", "date": "2026-09-18", "currency": "TWD",
                        "tags": ["source:fuzzy-text"], **defaults_extra,
                    },
                    "transactions": [{
                        "description": "Lunch", "amount": "100", **record_extra,
                    }],
                }
                journal, _, _, _ = build_ingest_transactions(
                    payload, existing_text="", payment_account_rules=self.RULES
                )
                self.assertIn(expected, journal)
                self.assertNotIn("inferred:payment-account", journal)
                self.assertNotIn("inferred:currency-routing", journal)

    def test_require_match_rejects_unrouted_currency_with_focused_error(self):
        payload = {
            "transactions": [{
                "kind": "expense", "date": "2026-09-18", "currency": "SGD",
                "description": "Lunch", "amount": "10", "tags": ["source:fuzzy-text"],
            }]
        }
        with self.assertRaisesRegex(ValueError, "SGD.*explicit credit"):
            build_ingest_transactions(
                payload, existing_text="", payment_account_rules=self.RULES
            )

    def test_unmatched_currency_keeps_legacy_cash_fallback_when_not_required(self):
        payload = {
            "transactions": [{
                "kind": "expense", "date": "2026-09-18", "currency": "SGD",
                "description": "Lunch", "amount": "10", "tags": ["source:fuzzy-text"],
            }]
        }
        journal, _, _, _ = build_ingest_transactions(
            payload,
            existing_text="",
            payment_account_rules={"currency_accounts": {}, "require_match": False},
        )
        self.assertIn("assets:cash", journal)
        self.assertIn("inferred:payment-account", journal)
        self.assertNotIn("inferred:currency-routing", journal)

    def test_non_expenses_are_not_auto_routed(self):
        record = {
            "kind": "income", "date": "2026-09-18", "currency": "JPY",
            "description": "Salary", "amount": "100", "debit": "assets:bank",
            "tags": ["source:fuzzy-text"],
        }
        with self.assertRaisesRegex(ValueError, "explicit debit and credit"):
            build_ingest_transactions(
                {"transactions": [record]},
                existing_text="",
                payment_account_rules=self.RULES,
            )

        record["credit"] = "income:salary"
        journal, _, _, _ = build_ingest_transactions(
            {"transactions": [record]},
            existing_text="",
            payment_account_rules=self.RULES,
        )
        self.assertIn("income:salary", journal)
        self.assertNotIn("jpy-account", journal)
        self.assertNotIn("inferred:currency-routing", journal)

    def test_historical_and_structured_expenses_preserve_explicit_source_account(self):
        for source in ("source:historical-import", "source:structured-csv"):
            with self.subTest(source=source):
                explicit = {
                    "kind": "expense", "date": "2024-01-02", "currency": "JPY",
                    "description": "Historical lunch", "amount": "120",
                    "debit": "expenses:food", "credit": "liabilities:legacy-card",
                    "import_id": "legacy-1", "tags": [source],
                }
                journal, _, _, _ = build_ingest_transactions(
                    {"transactions": [explicit]},
                    existing_text="",
                    payment_account_rules=self.RULES,
                )
                self.assertIn("liabilities:legacy-card", journal)
                self.assertNotIn("inferred:currency-routing", journal)

                del explicit["credit"]
                explicit["import_id"] = "legacy-2"
                with self.assertRaisesRegex(ValueError, "historical imports require explicit"):
                    build_ingest_transactions(
                        {"transactions": [explicit]},
                        existing_text="",
                        payment_account_rules=self.RULES,
                    )

    def test_cli_loads_routes_beside_journal_for_jpy_usd_twd_and_override(self):
        script = SKILL_DIR / "scripts" / "finance.py"
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "main.journal"
            batch = Path(tmp) / "batch.json"
            base = [sys.executable, str(script), "--journal", str(journal)]
            subprocess.run(base + ["init"], check=True, capture_output=True, text=True)
            (Path(tmp) / "payment-account-rules.json").write_text(
                json.dumps(self.RULES), encoding="utf-8"
            )
            batch.write_text(json.dumps({
                "defaults": {
                    "kind": "expense", "date": "2026-09-18",
                    "tags": ["source:fuzzy-text"],
                },
                "transactions": [
                    {"description": "JPY lunch", "amount": "100", "currency": "JPY"},
                    {"description": "USD lunch", "amount": "10", "currency": "USD"},
                    {"description": "TWD lunch", "amount": "300", "currency": "TWD"},
                    {"description": "Card lunch", "amount": "400", "currency": "TWD",
                     "credit": "liabilities:card"},
                ],
            }), encoding="utf-8")
            result = subprocess.run(
                base + ["ingest-json", str(batch)], check=False, capture_output=True, text=True
            )
            text = journal.read_text(encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        for account in (*self.RULES["currency_accounts"].values(), "liabilities:card"):
            self.assertIn(account, text)
        self.assertEqual(text.count("inferred:currency-routing"), 3)

    def test_cli_rejects_unsupported_currency_and_malformed_rules(self):
        script = SKILL_DIR / "scripts" / "finance.py"
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "main.journal"
            batch = Path(tmp) / "batch.json"
            rules_path = Path(tmp) / "payment-account-rules.json"
            base = [sys.executable, str(script), "--journal", str(journal)]
            subprocess.run(base + ["init"], check=True, capture_output=True, text=True)
            batch.write_text(json.dumps({"transactions": [{
                "kind": "expense", "date": "2026-09-18", "currency": "SGD",
                "description": "SGD lunch", "amount": "10", "tags": ["source:fuzzy-text"],
            }]}), encoding="utf-8")
            rules_path.write_text(json.dumps(self.RULES), encoding="utf-8")
            unsupported = subprocess.run(
                base + ["ingest-json", str(batch)], check=False, capture_output=True, text=True
            )
            rules_path.write_text("{bad json", encoding="utf-8")
            malformed = subprocess.run(
                base + ["ingest-json", str(batch)], check=False, capture_output=True, text=True
            )
            text = journal.read_text(encoding="utf-8")
        self.assertNotEqual(unsupported.returncode, 0)
        self.assertRegex(unsupported.stderr, "SGD.*explicit credit")
        self.assertNotEqual(malformed.returncode, 0)
        self.assertIn("Invalid payment account rules", malformed.stderr)
        self.assertNotIn("SGD lunch", text)


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

    def test_transfer_rejects_identical_debit_and_credit_accounts(self):
        payload = {
            "transactions": [
                {
                    "kind": "transfer",
                    "date": "2024-09-08",
                    "description": "Loan principal payment",
                    "amount": "50629",
                    "currency": "TWD",
                    "debit": "assets:cash:wallet",
                    "credit": "assets:cash:wallet",
                    "import_id": "moze-loan-payment-1",
                    "tags": ["source:historical-import"],
                }
            ]
        }
        with self.assertRaisesRegex(ValueError, "different debit and credit accounts"):
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

    def test_adjustment_accepts_only_the_four_migration_balance_directions(self):
        account_pairs = (
            ("assets:bank", "equity:migration:legacy", "Asset increase"),
            ("equity:migration:legacy", "assets:bank", "Asset decrease"),
            ("equity:migration:legacy", "liabilities:card", "Liability increase"),
            ("liabilities:card", "equity:migration:legacy", "Liability decrease"),
        )
        payload = {
            "transactions": [
                {
                    "kind": "adjustment",
                    "date": f"2024-01-0{index}",
                    "description": description,
                    "amount": "1.25",
                    "currency": "TWD",
                    "debit": debit,
                    "credit": credit,
                    "import_id": f"adjustment-{index}",
                    "tags": [
                        "source:historical-import" if index % 2 else "source:structured-csv"
                    ],
                }
                for index, (debit, credit, description) in enumerate(account_pairs, start=1)
            ]
        }
        journal, imported, skipped, _ = build_ingest_transactions(payload, existing_text="")
        self.assertEqual((imported, skipped), (4, 0))
        self.assertEqual(journal.count("; kind:adjustment"), 4)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "adjustments.journal"
            path.write_text(journal, encoding="utf-8")
            check = subprocess.run(
                ["hledger", "-f", str(path), "check"],
                text=True,
                capture_output=True,
                check=False,
            )
            printed = subprocess.run(
                ["hledger", "-f", str(path), "print", "--output-format=json"],
                text=True,
                capture_output=True,
                check=False,
            )
            strict_audit = subprocess.run(
                [
                    sys.executable,
                    str(SKILL_DIR / "scripts" / "finance.py"),
                    "--journal",
                    str(path),
                    "audit",
                    "--strict",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertEqual(check.returncode, 0, check.stderr)
        self.assertEqual(printed.returncode, 0, printed.stderr)
        self.assertEqual(strict_audit.returncode, 0, strict_audit.stderr or strict_audit.stdout)
        report = audit_journal(json.loads(printed.stdout), journal)
        self.assertTrue(report["ok"], report["findings"])
        self.assertEqual(report["critical_count"], 0)

    def test_adjustment_requires_explicit_migration_fields(self):
        base = {
            "kind": "adjustment",
            "date": "2024-01-02",
            "description": "Legacy balance correction",
            "amount": "10",
            "currency": "TWD",
            "debit": "assets:bank",
            "credit": "equity:migration:legacy",
            "import_id": "adjustment-required-fields",
            "tags": ["source:historical-import"],
        }
        for field in ("amount", "date", "currency", "debit", "credit", "import_id"):
            with self.subTest(field=field):
                record = {key: value for key, value in base.items() if key != field}
                with self.assertRaisesRegex(ValueError, field.replace("_", " ") + "|explicit|import_id"):
                    build_ingest_transactions({"transactions": [record]}, existing_text="")

    def test_adjustment_rejects_nonhistorical_or_inferred_sources(self):
        base = {
            "kind": "adjustment",
            "date": "2024-01-02",
            "description": "Legacy balance correction",
            "amount": "10",
            "currency": "TWD",
            "debit": "assets:bank",
            "credit": "equity:migration:legacy",
            "import_id": "adjustment-source",
        }
        for tags in (
            ["source:fuzzy-text"],
            ["source:historical-import", "source:structured-csv"],
            ["source:historical-import", "inferred:currency"],
        ):
            with self.subTest(tags=tags):
                with self.assertRaisesRegex(ValueError, "historical source|exactly one|inferred|explicit"):
                    build_ingest_transactions(
                        {"transactions": [{**base, "tags": tags}]}, existing_text=""
                    )

    def test_adjustment_rejects_non_migration_account_pairs(self):
        invalid_pairs = (
            ("assets:bank", "assets:cash"),
            ("assets:bank", "income:other"),
            ("expenses:fees", "equity:migration:legacy"),
            ("assets:bank", "equity:capital"),
            ("assets:bank", "equity:opening-balances"),
            ("equity:migration:legacy", "equity:migration:rounding"),
            ("assets:bank", "assets:bank"),
        )
        for index, (debit, credit) in enumerate(invalid_pairs, start=1):
            with self.subTest(debit=debit, credit=credit):
                record = {
                    "kind": "adjustment",
                    "date": "2024-01-02",
                    "description": "Invalid adjustment",
                    "amount": "10",
                    "currency": "TWD",
                    "debit": debit,
                    "credit": credit,
                    "import_id": f"invalid-adjustment-{index}",
                    "tags": ["source:historical-import"],
                }
                with self.assertRaisesRegex(ValueError, "different|account families|migration"):
                    build_ingest_transactions({"transactions": [record]}, existing_text="")

    def test_adjustment_rejects_installments_fees_and_cross_currency_fields(self):
        base = {
            "kind": "adjustment",
            "date": "2024-01-02",
            "description": "Legacy balance correction",
            "amount": "10",
            "currency": "TWD",
            "debit": "assets:bank",
            "credit": "equity:migration:legacy",
            "import_id": "adjustment-options",
            "tags": ["source:structured-csv"],
        }
        invalid_options = (
            {"installments": 2},
            {"fee": "0.01"},
            {"fee": "-0.01"},
            {"debit_amount": "1", "debit_currency": "USD"},
        )
        for options in invalid_options:
            with self.subTest(options=options):
                with self.assertRaisesRegex(ValueError, "installments|fee|debit_amount|debit_currency"):
                    build_ingest_transactions(
                        {"transactions": [{**base, **options}]}, existing_text=""
                    )

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

    @staticmethod
    def _loan_transactions(*, paid_periods=0, remaining_periods=3):
        journal, _periods = build_loan_resume_schedule(
            loan_id="audit-loan",
            start=date(2026, 1, 31),
            description="Audit loan",
            principal=Decimal("300"),
            annual_rate=Decimal("0"),
            payment=Decimal("100"),
            paid_periods=paid_periods,
            remaining_periods=remaining_periods,
            currency="TWD",
            liability_account="liabilities:loans:audit",
            interest_account="expenses:interest:loans",
            payment_account="assets:bank:checking",
            rounding_quantum=Decimal("1"),
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "loan.journal"
            path.write_text(journal, encoding="utf-8")
            result = subprocess.run(
                ["hledger", "-f", str(path), "print", "--output-format=json"],
                check=False,
                capture_output=True,
                text=True,
            )
        if result.returncode:
            raise AssertionError(result.stderr)
        return journal, json.loads(result.stdout)

    @staticmethod
    def _copy_transaction(transaction):
        return json.loads(json.dumps(transaction))

    def test_audit_accepts_generated_full_and_resumed_loan_schedules(self):
        for paid_periods, remaining_periods in ((0, 3), (25, 3)):
            with self.subTest(paid_periods=paid_periods):
                journal, transactions = self._loan_transactions(
                    paid_periods=paid_periods,
                    remaining_periods=remaining_periods,
                )
                report = audit_journal(transactions, journal)
                self.assertTrue(report["ok"], report["findings"])

    def test_audit_rejects_invalid_scheduled_loan_metadata(self):
        _journal, transactions = self._loan_transactions()
        original = transactions[0]
        required = (
            "source",
            "kind",
            "loan-id",
            "loan-period",
            "loan-total-periods",
            "import-id",
        )
        cases = []
        for name in required:
            missing = self._copy_transaction(original)
            missing["ttags"] = [tag for tag in missing["ttags"] if tag[0] != name]
            cases.append((f"missing-{name}", missing))
            duplicate = self._copy_transaction(original)
            duplicate["ttags"].append(next(tag for tag in duplicate["ttags"] if tag[0] == name))
            cases.append((f"duplicate-{name}", duplicate))
        replacements = (
            ("source", "fuzzy-text"),
            ("kind", "expense"),
            ("loan-id", "bad/id"),
            ("loan-period", "0"),
            ("loan-total-periods", "0"),
            ("import-id", "wrong-id"),
        )
        for name, value in replacements:
            changed = self._copy_transaction(original)
            changed["ttags"] = [
                [tag_name, value if tag_name == name else tag_value]
                for tag_name, tag_value in changed["ttags"]
            ]
            cases.append((f"wrong-{name}", changed))
        wrong_description = self._copy_transaction(original)
        wrong_description["tdescription"] = "Audit loan [2/3]"
        cases.append(("wrong-description", wrong_description))

        for name, transaction in cases:
            with self.subTest(name=name):
                report = audit_journal([transaction], "")
                self.assertIn(
                    "scheduled-loan-invalid-metadata",
                    {finding["code"] for finding in report["findings"]},
                )

    def test_audit_rejects_adversarial_scheduled_loan_period_metadata(self):
        _journal, transactions = self._loan_transactions(remaining_periods=1)
        original = transactions[0]
        cases = (
            ("huge", str(sys.maxsize + 1)),
            ("negative", "-1"),
            ("zero", "0"),
            ("over-limit", str(MAX_LOAN_PERIODS + 1)),
        )
        for tag_name in ("loan-period", "loan-total-periods"):
            for name, adversarial_value in cases:
                with self.subTest(tag_name=tag_name, name=name):
                    transaction = self._copy_transaction(original)
                    transaction["ttags"] = [
                        [name, adversarial_value if name == tag_name else value]
                        for name, value in transaction["ttags"]
                    ]
                    if tag_name == "loan-period":
                        transaction["tdescription"] = (
                            f"Audit loan [{adversarial_value}/1]"
                        )
                    else:
                        transaction["tdescription"] = (
                            f"Audit loan [1/{adversarial_value}]"
                        )
                    report = audit_journal([transaction], "")
                    self.assertIn(
                        "scheduled-loan-invalid-metadata",
                        {finding["code"] for finding in report["findings"]},
                    )

    def test_audit_fails_closed_when_reserved_loan_raw_source_is_unavailable(self):
        _journal, transactions = self._loan_transactions(remaining_periods=1)
        report = audit_journal(transactions, "")
        self.assertIn(
            "scheduled-loan-implicit-posting-amount",
            {finding["code"] for finding in report["findings"]},
        )

    def test_audit_rejects_real_hledger_implicit_loan_posting_amounts(self):
        header = (
            "2026-01-31 Audit loan [1/1]\n"
            "    ; source:scheduled-loan\n"
            "    ; kind:loan-payment\n"
            "    ; loan-id:audit-loan\n"
            "    ; loan-period:1\n"
            "    ; loan-total-periods:1\n"
            "    ; import-id:loan.audit-loan.period.1\n"
        )
        posting_lines = (
            "    liabilities:loans:audit  100 TWD",
            "    expenses:interest:loans  0 TWD",
            "    assets:bank:checking  -100 TWD",
        )
        for omitted_index, role in enumerate(("principal", "interest", "payment")):
            with self.subTest(role=role), tempfile.TemporaryDirectory() as tmp:
                lines = list(posting_lines)
                lines[omitted_index] = lines[omitted_index].rsplit("  ", 1)[0]
                journal_text = header + "\n".join(lines) + "\n"
                path = Path(tmp) / "loan.journal"
                path.write_text(journal_text, encoding="utf-8")
                printed = subprocess.run(
                    ["hledger", "-f", str(path), "print", "--output-format=json"],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(printed.returncode, 0, printed.stderr)
                report = audit_journal(json.loads(printed.stdout), journal_text)
                self.assertIn(
                    "scheduled-loan-implicit-posting-amount",
                    {finding["code"] for finding in report["findings"]},
                )

    def test_cli_strict_audit_correlates_secondary_date_and_rejects_posting_tag_spoof(self):
        script = SKILL_DIR / "scripts" / "finance.py"
        header = (
            "2026-01-31=2026-02-01 * (AUTO) Audit loan [1/1]\n"
            "    ; source:scheduled-loan\n"
            "    ; kind:loan-payment\n"
            "    ; loan-id:audit-loan\n"
            "    ; loan-period:1\n"
            "    ; loan-total-periods:1\n"
            "    ; import-id:loan.audit-loan.period.1\n"
        )
        spoof = (
            "\n2026-02-02 Unrelated transaction\n"
            "    expenses:test  1 TWD  ; source:scheduled-loan\n"
            "    expenses:other  0 TWD\n"
            "    assets:cash  -1 TWD\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "main.journal"
            journal.write_text(
                header
                + "    liabilities:loans:audit  100 TWD\n"
                + "    expenses:interest:loans  0 TWD\n"
                + "    assets:bank:checking\n"
                + spoof,
                encoding="utf-8",
            )
            result = subprocess.run(
                [sys.executable, str(script), "--journal", str(journal), "audit", "--strict"],
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(result.returncode, 2, result.stderr or result.stdout)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn(
            "scheduled-loan-implicit-posting-amount",
            {finding["code"] for finding in json.loads(result.stdout)["findings"]},
        )

    def test_cli_strict_audit_accepts_secondary_date_status_code_header(self):
        script = SKILL_DIR / "scripts" / "finance.py"
        journal_text, _transactions = self._loan_transactions(remaining_periods=1)
        journal_text = journal_text.replace(
            "2026-01-31 Audit loan", "2026-01-31=2026-02-01 * (AUTO) Audit loan", 1
        )
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "main.journal"
            journal.write_text(journal_text, encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(script), "--journal", str(journal), "audit", "--strict"],
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_cli_strict_audit_correlates_each_omitted_amount_in_included_source(self):
        script = SKILL_DIR / "scripts" / "finance.py"
        journal_text, _transactions = self._loan_transactions(remaining_periods=1)
        posting_accounts = (
            "liabilities:loans:audit",
            "expenses:interest:loans",
            "assets:bank:checking",
        )
        for omitted_index, account in enumerate(posting_accounts):
            with self.subTest(omitted_index=omitted_index), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / "main.journal"
                included = Path(tmp) / "loans.journal"
                root.write_text("include loans.journal\n", encoding="utf-8")
                lines = journal_text.splitlines()
                line_index = next(
                    index for index, line in enumerate(lines) if line.strip().startswith(account)
                )
                lines[line_index] = "    " + account
                included.write_text("\n".join(lines) + "\n", encoding="utf-8")
                result = subprocess.run(
                    [sys.executable, str(script), "--journal", str(root), "audit", "--strict"],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 2, result.stderr or result.stdout)
                self.assertIn(
                    "scheduled-loan-implicit-posting-amount",
                    {finding["code"] for finding in json.loads(result.stdout)["findings"]},
                )

    def test_cli_strict_audit_accepts_valid_included_loan_source(self):
        script = SKILL_DIR / "scripts" / "finance.py"
        journal_text, _transactions = self._loan_transactions(remaining_periods=1)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "main.journal"
            included = Path(tmp) / "loans.journal"
            root.write_text("include loans.journal\n", encoding="utf-8")
            included.write_text(journal_text, encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(script), "--journal", str(root), "audit", "--strict"],
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_audit_raw_loan_validation_ignores_comment_blocks_and_accepts_formatting(self):
        journal, transactions = self._loan_transactions(remaining_periods=1)
        source = (
            "comment\n"
            "2026-01-01 Fake [1/1]\n"
            "    ; source:scheduled-loan\n"
            "    liabilities:fake\n"
            "end comment\n"
            f"\n{journal.replace('  300 TWD', '  300 TWD  ; explicit principal')}"
        )
        report = audit_journal(transactions, source)
        self.assertNotIn(
            "scheduled-loan-implicit-posting-amount",
            {finding["code"] for finding in report["findings"]},
        )
        self.assertTrue(report["ok"], report["findings"])

    def test_audit_rejects_invalid_scheduled_loan_posting_shapes(self):
        _journal, transactions = self._loan_transactions()
        original = transactions[0]
        cases = []

        wrong_liability = self._copy_transaction(original)
        wrong_liability["tpostings"][0]["paccount"] = "assets:loan"
        cases.append(("wrong-liability", wrong_liability))
        wrong_interest = self._copy_transaction(original)
        wrong_interest["tpostings"][1]["paccount"] = "assets:interest"
        cases.append(("wrong-interest", wrong_interest))
        wrong_payment = self._copy_transaction(original)
        wrong_payment["tpostings"][2]["paccount"] = "expenses:payment"
        cases.append(("wrong-payment", wrong_payment))
        overlapping = self._copy_transaction(original)
        overlapping["tpostings"][2]["paccount"] = "liabilities:loans"
        cases.append(("overlapping", overlapping))
        wrong_signs = self._copy_transaction(original)
        wrong_signs["tpostings"][0]["pamount"][0]["aquantity"] = {"floatingPoint": -100}
        wrong_signs["tpostings"][2]["pamount"][0]["aquantity"] = {"floatingPoint": 100}
        cases.append(("wrong-signs", wrong_signs))
        missing = self._copy_transaction(original)
        missing["tpostings"].pop()
        cases.append(("missing", missing))
        extra = self._copy_transaction(original)
        extra["tpostings"].append(self._copy_transaction(extra["tpostings"][1]))
        cases.append(("extra", extra))
        mixed = self._copy_transaction(original)
        mixed["tpostings"][1]["pamount"][0]["acommodity"] = "USD"
        cases.append(("mixed", mixed))
        unitless = self._copy_transaction(original)
        unitless["tpostings"][1]["pamount"][0]["acommodity"] = ""
        cases.append(("unitless", unitless))
        unbalanced = self._copy_transaction(original)
        unbalanced["tpostings"][2]["pamount"][0]["aquantity"] = {"floatingPoint": -99}
        cases.append(("unbalanced", unbalanced))
        priced = self._copy_transaction(original)
        priced["tpostings"][0]["pamount"][0]["aprice"] = {"tag": "UnitPrice"}
        cases.append(("price", priced))
        costed = self._copy_transaction(original)
        costed["tpostings"][0]["pcost"] = {"tag": "UnitCost"}
        cases.append(("cost", costed))
        asserted = self._copy_transaction(original)
        asserted["tpostings"][0]["pbalanceassertion"] = {"baamount": {}}
        cases.append(("assertion", asserted))
        virtual = self._copy_transaction(original)
        virtual["tpostings"][0]["ptype"] = "VirtualPosting"
        cases.append(("virtual", virtual))
        balanced_virtual = self._copy_transaction(original)
        balanced_virtual["tpostings"][0]["ptype"] = "BalancedVirtualPosting"
        cases.append(("balanced-virtual", balanced_virtual))

        for name, transaction in cases:
            with self.subTest(name=name):
                report = audit_journal([transaction], "")
                self.assertIn(
                    "tagged-transaction-invalid-structure",
                    {finding["code"] for finding in report["findings"]},
                )

    def test_strict_audit_rejects_real_hledger_loan_shape_attacks(self):
        header = (
            "2026-01-31 Audit loan [1/1]\n"
            "    ; source:scheduled-loan\n"
            "    ; kind:loan-payment\n"
            "    ; loan-id:audit-loan\n"
            "    ; loan-period:1\n"
            "    ; loan-total-periods:1\n"
            "    ; import-id:loan.audit-loan.period.1\n"
        )
        cases = {
            "virtual": (
                "    (liabilities:loans:audit)  100 TWD\n"
                "    (expenses:interest:loans)  0 TWD\n"
                "    (assets:bank:checking)  -100 TWD\n"
            ),
            "balanced-virtual": (
                "    [liabilities:loans:audit]  100 TWD\n"
                "    [expenses:interest:loans]  0 TWD\n"
                "    [assets:bank:checking]  -100 TWD\n"
            ),
            "assertion": (
                "    liabilities:loans:audit  100 TWD = 100 TWD\n"
                "    expenses:interest:loans  0 TWD\n"
                "    assets:bank:checking  -100 TWD\n"
            ),
            "assignment": (
                "    liabilities:loans:audit  = 100 TWD\n"
                "    expenses:interest:loans  0 TWD\n"
                "    assets:bank:checking  -100 TWD\n"
            ),
            "price": (
                "    liabilities:loans:audit  100 TWD @ 1 USD\n"
                "    expenses:interest:loans  0 USD\n"
                "    assets:bank:checking  -100 USD\n"
            ),
            "cost": (
                "    liabilities:loans:audit  100 TWD {1 USD}\n"
                "    expenses:interest:loans  0 USD\n"
                "    assets:bank:checking  -100 USD\n"
            ),
            "unitless": (
                "    liabilities:loans:audit  100\n"
                "    expenses:interest:loans  0\n"
                "    assets:bank:checking  -100\n"
            ),
            "mixed": (
                "    liabilities:loans:audit  100 TWD\n"
                "    expenses:interest:loans  0 USD\n"
                "    assets:bank:checking  -100 TWD\n"
            ),
        }
        for name, postings in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                journal = Path(tmp) / "malformed-loan.journal"
                journal_text = header + postings
                journal.write_text(journal_text, encoding="utf-8")
                printed = subprocess.run(
                    ["hledger", "-f", str(journal), "print", "--output-format=json"],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(printed.returncode, 0, printed.stderr)
                transactions = json.loads(printed.stdout)
                parsed_postings = transactions[0]["tpostings"]
                if name == "virtual":
                    self.assertTrue(all(p["ptype"] == "VirtualPosting" for p in parsed_postings))
                elif name == "balanced-virtual":
                    self.assertTrue(
                        all(p["ptype"] == "BalancedVirtualPosting" for p in parsed_postings)
                    )
                elif name in {"assertion", "assignment"}:
                    self.assertIsNotNone(parsed_postings[0]["pbalanceassertion"])
                elif name in {"price", "cost"}:
                    self.assertIsNotNone(parsed_postings[0]["pamount"][0]["aprice"])
                elif name == "unitless":
                    self.assertTrue(
                        all(
                            amount["acommodity"] == ""
                            for posting in parsed_postings
                            for amount in posting["pamount"]
                        )
                    )
                elif name == "mixed":
                    self.assertEqual(
                        {
                            amount["acommodity"]
                            for posting in parsed_postings
                            for amount in posting["pamount"]
                        },
                        {"TWD", "USD"},
                    )
                report = audit_journal(transactions, journal_text)
                self.assertIn(
                    "tagged-transaction-invalid-structure",
                    {finding["code"] for finding in report["findings"]},
                )
                strict = subprocess.run(
                    [
                        sys.executable,
                        str(SKILL_DIR / "scripts" / "finance.py"),
                        "--journal",
                        str(journal),
                        "audit",
                        "--strict",
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(strict.returncode, 2, strict.stderr or strict.stdout)

    def test_audit_rejects_invalid_scheduled_loan_groups(self):
        _journal, original = self._loan_transactions()
        cases = []
        cases.append(("missing-period", [original[0], original[2]]))
        duplicate = self._copy_transaction(original)
        duplicate[1] = self._copy_transaction(duplicate[0])
        cases.append(("duplicate-period", duplicate))

        different_total = self._copy_transaction(original)
        for tag in different_total[1]["ttags"]:
            if tag[0] == "loan-total-periods":
                tag[1] = "4"
            elif tag[0] == "import-id":
                tag[1] = "loan.audit-loan.period.2"
        different_total[1]["tdescription"] = "Audit loan [2/4]"
        cases.append(("different-total", different_total))

        different_base = self._copy_transaction(original)
        different_base[1]["tdescription"] = "Changed loan [2/3]"
        cases.append(("different-base", different_base))
        different_account = self._copy_transaction(original)
        different_account[1]["tpostings"][0]["paccount"] = "liabilities:loans:other"
        cases.append(("different-account", different_account))
        different_commodity = self._copy_transaction(original)
        for posting in different_commodity[1]["tpostings"]:
            posting["pamount"][0]["acommodity"] = "USD"
        cases.append(("different-commodity", different_commodity))
        wrong_date = self._copy_transaction(original)
        wrong_date[1]["tdate"] = "2026-03-01"
        cases.append(("wrong-date", wrong_date))
        unequal_payment = self._copy_transaction(original)
        unequal_payment[1]["tpostings"][0]["pamount"][0]["aquantity"] = {"floatingPoint": 90}
        unequal_payment[1]["tpostings"][2]["pamount"][0]["aquantity"] = {"floatingPoint": -90}
        cases.append(("unequal-payment", unequal_payment))

        for name, transactions in cases:
            with self.subTest(name=name):
                report = audit_journal(transactions, "")
                self.assertIn(
                    "scheduled-loan-invalid-schedule",
                    {finding["code"] for finding in report["findings"]},
                )

    def test_audit_rejects_unpaired_scheduled_loan_source_and_kind(self):
        _journal, transactions = self._loan_transactions()
        for tag_name, value in (("kind", "expense"), ("source", "fuzzy-text")):
            with self.subTest(tag_name=tag_name):
                transaction = self._copy_transaction(transactions[0])
                transaction["ttags"] = [
                    [name, value if name == tag_name else current]
                    for name, current in transaction["ttags"]
                ]
                report = audit_journal([transaction], "")
                self.assertIn(
                    "scheduled-loan-invalid-metadata",
                    {finding["code"] for finding in report["findings"]},
                )

    def test_ingest_json_rejects_reserved_scheduled_loan_metadata(self):
        base = {
            "date": "2026-01-01",
            "description": "Forged loan",
            "amount": "100",
            "currency": "TWD",
            "debit": "expenses:test",
            "credit": "assets:cash",
        }
        cases = (
            {**base, "kind": "loan-payment", "tags": ["source:fuzzy-text"]},
            {**base, "kind": "expense", "tags": ["source:scheduled-loan"]},
        )
        for record in cases:
            with self.subTest(record=record):
                with self.assertRaises(ValueError):
                    build_ingest_transactions(
                        {"transactions": [record]}, existing_text=""
                    )

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

    def test_audit_installment_duplicate_exemption_rejects_virtual_and_asserted_postings(self):
        for name, mutate in (
            (
                "virtual",
                lambda transaction: transaction["tpostings"][0].__setitem__(
                    "ptype", "VirtualPosting"
                ),
            ),
            (
                "balance-assertion",
                lambda transaction: transaction["tpostings"][0].__setitem__(
                    "pbalanceassertion", {"amount": "100 TWD"}
                ),
            ),
        ):
            with self.subTest(name=name):
                transactions = [
                    self._transaction(
                        "expenses:electronics",
                        100,
                        tags=[
                            ["kind", "expense"],
                            ["source", "structured-csv"],
                            ["import-id", f"unsafe-{name}"],
                        ],
                        description=f"Phone [{index}/2]",
                    )
                    for index in range(1, 3)
                ]
                transactions[0]["tdate"] = "2024-01-31"
                transactions[1]["tdate"] = "2024-02-29"
                mutate(transactions[0])
                report = audit_journal(transactions, "")
                self.assertIn(
                    "duplicate-import-id",
                    {finding["code"] for finding in report["findings"]},
                )

    def test_audit_strict_rejects_real_virtual_and_asserted_duplicate_installments(self):
        cases = {
            "virtual": (
                "    expenses:electronics  100 TWD\n"
                "    assets:cash  -100 TWD\n"
                "    (assets:virtual)  -1 TWD\n",
                "    expenses:electronics  100 TWD\n"
                "    assets:cash  -100 TWD\n"
                "    (assets:virtual)  -1 TWD\n",
            ),
            "balance-assertion": (
                "    expenses:electronics  100 TWD = 100 TWD\n"
                "    assets:cash  -100 TWD\n",
                "    expenses:electronics  100 TWD = 200 TWD\n"
                "    assets:cash  -100 TWD\n",
            ),
        }
        for name, (first_postings, second_postings) in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                journal_text = (
                    "2024-01-31 Phone [1/2]\n"
                    "    ; kind:expense\n"
                    "    ; source:structured-csv\n"
                    f"    ; import-id:unsafe-{name}\n"
                    f"{first_postings}\n"
                    "2024-02-29 Phone [2/2]\n"
                    "    ; kind:expense\n"
                    "    ; source:structured-csv\n"
                    f"    ; import-id:unsafe-{name}\n"
                    f"{second_postings}"
                )
                journal = Path(tmp) / "unsafe-installments.journal"
                journal.write_text(journal_text, encoding="utf-8")
                printed = subprocess.run(
                    ["hledger", "-f", str(journal), "print", "--output-format=json"],
                    text=True,
                    capture_output=True,
                    check=False,
                )
                strict_audit = subprocess.run(
                    [
                        sys.executable,
                        str(SKILL_DIR / "scripts" / "finance.py"),
                        "--journal",
                        str(journal),
                        "audit",
                        "--strict",
                    ],
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(printed.returncode, 0, printed.stderr)
                transactions = json.loads(printed.stdout)
                report = audit_journal(transactions, journal_text)
                self.assertIn(
                    "duplicate-import-id",
                    {finding["code"] for finding in report["findings"]},
                )
                self.assertEqual(
                    strict_audit.returncode,
                    2,
                    strict_audit.stderr or strict_audit.stdout,
                )

    def test_audit_strict_rejects_real_hledger_adjustments_disguised_as_installments(self):
        journal_text = (
            "2024-01-31 Legacy correction [1/2]\n"
            "    ; kind:adjustment\n"
            "    ; source:historical-import\n"
            "    ; import-id:duplicate-adjustment\n"
            "    assets:bank  10 TWD\n"
            "    equity:migration:legacy  -10 TWD\n"
            "\n"
            "2024-02-29 Legacy correction [2/2]\n"
            "    ; kind:adjustment\n"
            "    ; source:historical-import\n"
            "    ; import-id:duplicate-adjustment\n"
            "    assets:bank  10 TWD\n"
            "    equity:migration:legacy  -10 TWD\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "duplicate-adjustment.journal"
            journal.write_text(journal_text, encoding="utf-8")
            printed = subprocess.run(
                ["hledger", "-f", str(journal), "print", "--output-format=json"],
                text=True,
                capture_output=True,
                check=False,
            )
            strict_audit = subprocess.run(
                [
                    sys.executable,
                    str(SKILL_DIR / "scripts" / "finance.py"),
                    "--journal",
                    str(journal),
                    "audit",
                    "--strict",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(printed.returncode, 0, printed.stderr)
        transactions = json.loads(printed.stdout)
        self.assertEqual(len(transactions), 2)
        self.assertTrue(all(
            ["kind", "adjustment"] in transaction["ttags"]
            for transaction in transactions
        ))
        report = audit_journal(transactions, journal_text)
        self.assertIn(
            "duplicate-import-id",
            {finding["code"] for finding in report["findings"]},
        )
        self.assertEqual(strict_audit.returncode, 2, strict_audit.stderr or strict_audit.stdout)

    def test_audit_strict_rejects_real_hledger_priced_transfer_disguised_as_installments(self):
        journal_text = (
            "2024-01-31 FX transfer [1/2]\n"
            "    ; kind:transfer\n"
            "    ; source:historical-import\n"
            "    ; import-id:duplicate-fx-transfer\n"
            "    assets:bank:usd  10 USD @@ 300 TWD\n"
            "    assets:bank:twd  -300 TWD\n"
            "\n"
            "2024-02-29 FX transfer [2/2]\n"
            "    ; kind:transfer\n"
            "    ; source:historical-import\n"
            "    ; import-id:duplicate-fx-transfer\n"
            "    assets:bank:usd  10 USD @@ 300 TWD\n"
            "    assets:bank:twd  -300 TWD\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "duplicate-fx-transfer.journal"
            journal.write_text(journal_text, encoding="utf-8")
            printed = subprocess.run(
                ["hledger", "-f", str(journal), "print", "--output-format=json"],
                text=True,
                capture_output=True,
                check=False,
            )
            strict_audit = subprocess.run(
                [
                    sys.executable,
                    str(SKILL_DIR / "scripts" / "finance.py"),
                    "--journal",
                    str(journal),
                    "audit",
                    "--strict",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(printed.returncode, 0, printed.stderr)
        transactions = json.loads(printed.stdout)
        self.assertEqual(len(transactions), 2)
        self.assertTrue(all(
            transaction["tpostings"][0]["pamount"][0]["aprice"] is not None
            for transaction in transactions
        ))
        self.assertTrue(all(
            {
                amount["acommodity"]
                for posting in transaction["tpostings"]
                for amount in posting["pamount"]
            } == {"USD", "TWD"}
            for transaction in transactions
        ))
        report = audit_journal(transactions, journal_text)
        self.assertIn(
            "duplicate-import-id",
            {finding["code"] for finding in report["findings"]},
        )
        self.assertEqual(strict_audit.returncode, 2, strict_audit.stderr or strict_audit.stdout)

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
            "=2024.04.01 ; another mistaken opening\n"
            "2024-06-01 Account resembles directive\n"
            "    = 2024-06-01\n"
            "    assets:cash  -1 TWD\n",
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

    def test_audit_rejects_adjustment_with_nonhistorical_source_or_extra_posting(self):
        base = {
            "tdate": "2024-01-02",
            "tdescription": "Legacy correction",
            "ttags": [
                ["kind", "adjustment"],
                ["source", "historical-import"],
                ["import-id", "adjustment-audit"],
            ],
            "tpostings": [
                {"paccount": "assets:bank", "pamount": [{"acommodity": "TWD", "aquantity": {"floatingPoint": 10}}]},
                {"paccount": "equity:migration:legacy", "pamount": [{"acommodity": "TWD", "aquantity": {"floatingPoint": -10}}]},
            ],
        }
        wrong_source = {**base, "ttags": [
            ["kind", "adjustment"], ["source", "fuzzy-text"], ["import-id", "adjustment-audit"]
        ]}
        extra_posting = {**base, "tpostings": [
            *base["tpostings"],
            {"paccount": "equity:migration:rounding", "pamount": [{"acommodity": "TWD", "aquantity": {"floatingPoint": -1}}]},
        ]}
        for transaction in (wrong_source, extra_posting):
            with self.subTest(transaction=transaction):
                report = audit_journal([transaction], "")
                self.assertFalse(report["ok"])
                self.assertGreater(report["critical_count"], 0)

    def test_audit_rejects_adjustment_with_prices_or_mismatched_amounts(self):
        tags = [
            ["kind", "adjustment"],
            ["source", "historical-import"],
            ["import-id", "invalid-adjustment-amounts"],
        ]
        cases = (
            (
                {"acommodity": "USD", "aquantity": {"floatingPoint": 10}},
                {"acommodity": "TWD", "aquantity": {"floatingPoint": -10}},
            ),
            (
                {"acommodity": "TWD", "aquantity": {"floatingPoint": 10}},
                {"acommodity": "TWD", "aquantity": {"floatingPoint": -9}},
            ),
            (
                {
                    "acommodity": "TWD",
                    "aquantity": {"floatingPoint": 10},
                    "aprice": {
                        "tag": "TotalPrice",
                        "contents": {
                            "acommodity": "TWD",
                            "aquantity": {"floatingPoint": 10},
                        },
                    },
                },
                {"acommodity": "TWD", "aquantity": {"floatingPoint": -10}},
            ),
        )
        for asset_amount, equity_amount in cases:
            with self.subTest(asset_amount=asset_amount, equity_amount=equity_amount):
                transaction = {
                    "tdate": "2024-01-02",
                    "tdescription": "Invalid adjustment amounts",
                    "ttags": tags,
                    "tpostings": [
                        {"paccount": "assets:bank", "pamount": [asset_amount]},
                        {"paccount": "equity:migration:legacy", "pamount": [equity_amount]},
                    ],
                }
                report = audit_journal([transaction], "")
                self.assertIn(
                    "tagged-transaction-invalid-structure",
                    {finding["code"] for finding in report["findings"]},
                )

    def test_audit_strict_accepts_real_hledger_same_currency_adjustment(self):
        journal_text = (
            "2024-01-02 Ordinary adjustment\n"
            "    ; kind:adjustment\n"
            "    ; source:historical-import\n"
            "    ; import-id:ordinary-adjustment\n"
            "    assets:bank  10 TWD\n"
            "    equity:migration:legacy  -10 TWD\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "ordinary-adjustment.journal"
            journal.write_text(journal_text, encoding="utf-8")
            printed = subprocess.run(
                ["hledger", "-f", str(journal), "print", "--output-format=json"],
                text=True,
                capture_output=True,
                check=False,
            )
            strict_audit = subprocess.run(
                [
                    sys.executable,
                    str(SKILL_DIR / "scripts" / "finance.py"),
                    "--journal",
                    str(journal),
                    "audit",
                    "--strict",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(printed.returncode, 0, printed.stderr)
        transactions = json.loads(printed.stdout)
        self.assertTrue(all(
            posting["ptype"] == "RegularPosting"
            for posting in transactions[0]["tpostings"]
        ))
        self.assertTrue(all(
            posting["pbalanceassertion"] is None
            for posting in transactions[0]["tpostings"]
        ))
        report = audit_journal(transactions, journal_text)
        self.assertTrue(report["ok"], report["findings"])
        self.assertEqual(strict_audit.returncode, 0, strict_audit.stderr or strict_audit.stdout)

    def test_audit_strict_rejects_real_hledger_dynamic_balance_assignment(self):
        journal_text = (
            "2024-01-02 Dynamic balance assignment\n"
            "    ; kind:adjustment\n"
            "    ; source:historical-import\n"
            "    ; import-id:dynamic-balance-assignment\n"
            "    assets:bank  = 10 TWD\n"
            "    equity:migration:legacy\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "dynamic-balance-assignment.journal"
            journal.write_text(journal_text, encoding="utf-8")
            printed = subprocess.run(
                ["hledger", "-f", str(journal), "print", "--output-format=json"],
                text=True,
                capture_output=True,
                check=False,
            )
            strict_audit = subprocess.run(
                [
                    sys.executable,
                    str(SKILL_DIR / "scripts" / "finance.py"),
                    "--journal",
                    str(journal),
                    "audit",
                    "--strict",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(printed.returncode, 0, printed.stderr)
        transactions = json.loads(printed.stdout)
        self.assertIsNotNone(transactions[0]["tpostings"][0]["pbalanceassertion"])
        report = audit_journal(transactions, journal_text)
        self.assertIn(
            "tagged-transaction-invalid-structure",
            {finding["code"] for finding in report["findings"]},
        )
        self.assertEqual(strict_audit.returncode, 2, strict_audit.stderr or strict_audit.stdout)

    def test_audit_strict_rejects_real_hledger_balance_assertion_adjustment(self):
        journal_text = (
            "2024-01-02 Balance assertion adjustment\n"
            "    ; kind:adjustment\n"
            "    ; source:historical-import\n"
            "    ; import-id:balance-assertion-adjustment\n"
            "    assets:bank  10 TWD = 10 TWD\n"
            "    equity:migration:legacy  -10 TWD\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "balance-assertion-adjustment.journal"
            journal.write_text(journal_text, encoding="utf-8")
            printed = subprocess.run(
                ["hledger", "-f", str(journal), "print", "--output-format=json"],
                text=True,
                capture_output=True,
                check=False,
            )
            strict_audit = subprocess.run(
                [
                    sys.executable,
                    str(SKILL_DIR / "scripts" / "finance.py"),
                    "--journal",
                    str(journal),
                    "audit",
                    "--strict",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(printed.returncode, 0, printed.stderr)
        transactions = json.loads(printed.stdout)
        self.assertIsNotNone(transactions[0]["tpostings"][0]["pbalanceassertion"])
        report = audit_journal(transactions, journal_text)
        self.assertIn(
            "tagged-transaction-invalid-structure",
            {finding["code"] for finding in report["findings"]},
        )
        self.assertEqual(strict_audit.returncode, 2, strict_audit.stderr or strict_audit.stdout)

    def test_audit_strict_rejects_real_hledger_virtual_adjustment(self):
        journal_text = (
            "2024-01-02 Virtual adjustment\n"
            "    ; kind:adjustment\n"
            "    ; source:historical-import\n"
            "    ; import-id:virtual-adjustment\n"
            "    (assets:bank)  10 TWD\n"
            "    (equity:migration:legacy)  -10 TWD\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "virtual-adjustment.journal"
            journal.write_text(journal_text, encoding="utf-8")
            printed = subprocess.run(
                ["hledger", "-f", str(journal), "print", "--output-format=json"],
                text=True,
                capture_output=True,
                check=False,
            )
            strict_audit = subprocess.run(
                [
                    sys.executable,
                    str(SKILL_DIR / "scripts" / "finance.py"),
                    "--journal",
                    str(journal),
                    "audit",
                    "--strict",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(printed.returncode, 0, printed.stderr)
        transactions = json.loads(printed.stdout)
        self.assertTrue(all(
            posting["ptype"] == "VirtualPosting"
            for posting in transactions[0]["tpostings"]
        ))
        report = audit_journal(transactions, journal_text)
        self.assertIn(
            "tagged-transaction-invalid-structure",
            {finding["code"] for finding in report["findings"]},
        )
        self.assertEqual(strict_audit.returncode, 2, strict_audit.stderr or strict_audit.stdout)

    def test_audit_strict_rejects_real_hledger_unitless_adjustment(self):
        journal_text = (
            "2024-01-02 Unitless adjustment\n"
            "    ; kind:adjustment\n"
            "    ; source:historical-import\n"
            "    ; import-id:unitless-adjustment\n"
            "    assets:bank  10\n"
            "    equity:migration:legacy  -10\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "unitless-adjustment.journal"
            journal.write_text(journal_text, encoding="utf-8")
            printed = subprocess.run(
                ["hledger", "-f", str(journal), "print", "--output-format=json"],
                text=True,
                capture_output=True,
                check=False,
            )
            strict_audit = subprocess.run(
                [
                    sys.executable,
                    str(SKILL_DIR / "scripts" / "finance.py"),
                    "--journal",
                    str(journal),
                    "audit",
                    "--strict",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(printed.returncode, 0, printed.stderr)
        transactions = json.loads(printed.stdout)
        self.assertTrue(all(
            posting["ptype"] == "RegularPosting"
            for posting in transactions[0]["tpostings"]
        ))
        self.assertTrue(all(
            amount["acommodity"] == ""
            for posting in transactions[0]["tpostings"]
            for amount in posting["pamount"]
        ))
        report = audit_journal(transactions, journal_text)
        self.assertIn(
            "tagged-transaction-invalid-structure",
            {finding["code"] for finding in report["findings"]},
        )
        self.assertEqual(strict_audit.returncode, 2, strict_audit.stderr or strict_audit.stdout)

    def test_audit_strict_rejects_real_hledger_priced_cross_currency_adjustment(self):
        journal_text = (
            "2024-01-02 Priced cross-currency adjustment\n"
            "    ; kind:adjustment\n"
            "    ; source:historical-import\n"
            "    ; import-id:priced-cross-currency-adjustment\n"
            "    assets:bank  10 USD @@ 300 TWD\n"
            "    equity:migration:legacy  -300 TWD\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "priced-cross-currency-adjustment.journal"
            journal.write_text(journal_text, encoding="utf-8")
            printed = subprocess.run(
                ["hledger", "-f", str(journal), "print", "--output-format=json"],
                text=True,
                capture_output=True,
                check=False,
            )
            strict_audit = subprocess.run(
                [
                    sys.executable,
                    str(SKILL_DIR / "scripts" / "finance.py"),
                    "--journal",
                    str(journal),
                    "audit",
                    "--strict",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(printed.returncode, 0, printed.stderr)
        transactions = json.loads(printed.stdout)
        self.assertIsNotNone(transactions[0]["tpostings"][0]["pamount"][0]["aprice"])
        report = audit_journal(transactions, journal_text)
        self.assertIn(
            "tagged-transaction-invalid-structure",
            {finding["code"] for finding in report["findings"]},
        )
        self.assertEqual(strict_audit.returncode, 2, strict_audit.stderr or strict_audit.stdout)


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
    @staticmethod
    def loan_resume_args():
        return [
            "loan-resume",
            "--loan-id",
            "personal-3200000",
            "--start",
            "2026-10-08",
            "--description",
            "Personal loan payment",
            "--principal",
            "1907335",
            "--annual-rate",
            "2.1",
            "--payment",
            "56229",
            "--paid-periods",
            "25",
            "--remaining-periods",
            "35",
            "--currency",
            "TWD",
            "--liability",
            "liabilities:loans:personal",
            "--interest-account",
            "expenses:interest:loans",
            "--payment-account",
            "assets:bank:checking",
            "--rounding",
            "1",
        ]

    def test_loan_resume_preview_writes_nothing(self):
        script = SKILL_DIR / "scripts" / "finance.py"
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "main.journal"
            base = [sys.executable, str(script), "--journal", str(journal)]
            subprocess.run(base + ["init"], check=True, capture_output=True, text=True)
            before = journal.read_bytes()
            head_before = subprocess.run(
                ["git", "-C", tmp, "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            result = subprocess.run(
                base + self.loan_resume_args() + ["--preview"],
                check=False,
                capture_output=True,
                text=True,
            )
            head_after = subprocess.run(
                ["git", "-C", tmp, "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            after = journal.read_bytes()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count("; source:scheduled-loan"), 35)
        self.assertIn("Preview only", result.stderr)
        self.assertEqual(after, before)
        self.assertEqual(head_after, head_before)

    def test_loan_resume_writes_one_checked_git_commit(self):
        script = SKILL_DIR / "scripts" / "finance.py"
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "main.journal"
            base = [sys.executable, str(script), "--journal", str(journal)]
            subprocess.run(base + ["init"], check=True, capture_output=True, text=True)
            result = subprocess.run(
                base + self.loan_resume_args(),
                check=False,
                capture_output=True,
                text=True,
            )
            text = journal.read_text(encoding="utf-8")
            check = subprocess.run(
                ["hledger", "-f", str(journal), "check"],
                check=False,
                capture_output=True,
                text=True,
            )
            subjects = subprocess.run(
                ["git", "-C", tmp, "log", "--format=%s"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.splitlines()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(check.returncode, 0, check.stderr)
        self.assertEqual(text.count("; source:scheduled-loan"), 35)
        self.assertEqual(
            subjects.count("Add remaining loan schedule: Personal loan payment"), 1
        )

    def test_loan_resume_rejects_invalid_cli_values_without_writing(self):
        script = SKILL_DIR / "scripts" / "finance.py"
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "main.journal"
            base = [sys.executable, str(script), "--journal", str(journal)]
            subprocess.run(base + ["init"], check=True, capture_output=True, text=True)
            before = journal.read_bytes()
            head = subprocess.run(
                ["git", "-C", tmp, "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            cases = (
                ("--start", "not-a-date", "Invalid isoformat"),
                ("--principal", "not-a-decimal", "Principal"),
                ("--remaining-periods", "not-an-integer", "invalid int value"),
            )
            results = []
            for option, bad_value, _message in cases:
                command = self.loan_resume_args()
                command[command.index(option) + 1] = bad_value
                results.append(
                    subprocess.run(
                        base + command,
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                )
            head_after = subprocess.run(
                ["git", "-C", tmp, "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            after = journal.read_bytes()
        for result, (_option, _bad_value, message) in zip(results, cases):
            self.assertEqual(result.returncode, 2)
            self.assertIn(message, result.stderr)
        self.assertEqual(after, before)
        self.assertEqual(head_after, head)

    def test_loan_resume_rejects_excessive_periods_and_precision_without_traceback(self):
        script = SKILL_DIR / "scripts" / "finance.py"
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "main.journal"
            base = [sys.executable, str(script), "--journal", str(journal)]
            subprocess.run(base + ["init"], check=True, capture_output=True, text=True)
            cases = (
                ("--remaining-periods", str(MAX_LOAN_PERIODS + 1), "1200"),
                ("--rounding", "1e-1000", "18 decimal places"),
            )
            results = []
            for option, value, _message in cases:
                command = self.loan_resume_args()
                command[command.index(option) + 1] = value
                results.append(
                    subprocess.run(
                        base + command,
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                )
        for result, (_option, _value, message) in zip(results, cases):
            self.assertEqual(result.returncode, 2)
            self.assertIn(message, result.stderr)
            self.assertNotIn("Traceback", result.stderr)

    def test_loan_resume_rejects_extreme_decimal_exponents_without_write(self):
        script = SKILL_DIR / "scripts" / "finance.py"
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "main.journal"
            base = [sys.executable, str(script), "--journal", str(journal)]
            subprocess.run(base + ["init"], check=True, capture_output=True, text=True)
            before = journal.read_bytes()
            head_before = subprocess.run(
                ["git", "-C", tmp, "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            results = []
            for option in ("--principal", "--payment", "--annual-rate"):
                for value in ("1e-1000000000", "1e1000000000"):
                    command = self.loan_resume_args()
                    command[command.index(option) + 1] = value
                    results.append(
                        subprocess.run(base + command, check=False, capture_output=True, text=True)
                    )
            for value in ("0e-1000000000", "0e1000000000"):
                command = self.loan_resume_args()
                command[command.index("--annual-rate") + 1] = value
                results.append(
                    subprocess.run(base + command, check=False, capture_output=True, text=True)
                )
            after = journal.read_bytes()
            head_after = subprocess.run(
                ["git", "-C", tmp, "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        for result in results:
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            self.assertRegex(result.stderr, "safe (magnitude|scale)")
        self.assertEqual(after, before)
        self.assertEqual(head_after, head_before)

    def test_loan_resume_writes_future_transactions_immediately(self):
        script = SKILL_DIR / "scripts" / "finance.py"
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "main.journal"
            base = [sys.executable, str(script), "--journal", str(journal)]
            subprocess.run(base + ["init"], check=True, capture_output=True, text=True)
            subprocess.run(
                base + self.loan_resume_args(),
                check=True,
                capture_output=True,
                text=True,
            )
            printed = subprocess.run(
                [
                    "hledger",
                    "-f",
                    str(journal),
                    "print",
                    "tag:loan-id=personal-3200000",
                ],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
        self.assertEqual(printed.count("; source:scheduled-loan"), 35)
        self.assertIn("2026-10-08 Personal loan payment [26/60]", printed)
        self.assertIn("2029-08-08 Personal loan payment [60/60]", printed)

    def test_loan_resume_rerun_is_noop(self):
        script = SKILL_DIR / "scripts" / "finance.py"
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "main.journal"
            base = [sys.executable, str(script), "--journal", str(journal)]
            subprocess.run(base + ["init"], check=True, capture_output=True, text=True)
            first = subprocess.run(
                base + self.loan_resume_args(),
                check=False,
                capture_output=True,
                text=True,
            )
            first_bytes = journal.read_bytes()
            first_head = subprocess.run(
                ["git", "-C", tmp, "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            second = subprocess.run(
                base + self.loan_resume_args(),
                check=False,
                capture_output=True,
                text=True,
            )
            second_bytes = journal.read_bytes()
            second_head = subprocess.run(
                ["git", "-C", tmp, "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("already present", second.stderr)
        self.assertEqual(second_bytes, first_bytes)
        self.assertEqual(second_head, first_head)
        for period in range(26, 61):
            self.assertEqual(
                second_bytes.count(
                    f"; import-id:loan.personal-3200000.period.{period}".encode()
                ),
                1,
            )

    def test_loan_resume_all_ids_require_exact_schedule_semantics(self):
        script = SKILL_DIR / "scripts" / "finance.py"
        changes = (
            ("--annual-rate", "2.2"),
            ("--payment", "56228"),
            ("--principal", "1907336"),
            ("--start", "2026-10-09"),
            ("--liability", "liabilities:loans:changed"),
            ("--interest-account", "expenses:interest:changed"),
            ("--payment-account", "assets:bank:changed"),
            ("--currency", "USD"),
            ("--description", "Changed loan payment"),
        )
        for option, value in changes:
            with self.subTest(option=option), tempfile.TemporaryDirectory() as tmp:
                journal = Path(tmp) / "main.journal"
                base = [sys.executable, str(script), "--journal", str(journal)]
                subprocess.run(base + ["init"], check=True, capture_output=True, text=True)
                subprocess.run(base + self.loan_resume_args(), check=True, capture_output=True, text=True)
                before = journal.read_bytes()
                head_before = subprocess.run(
                    ["git", "-C", tmp, "rev-parse", "HEAD"], check=True,
                    capture_output=True, text=True,
                ).stdout.strip()
                command = self.loan_resume_args()
                command[command.index(option) + 1] = value
                result = subprocess.run(base + command, check=False, capture_output=True, text=True)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("terms-mismatch", result.stderr)
                self.assertEqual(journal.read_bytes(), before)
                self.assertEqual(
                    subprocess.run(
                        ["git", "-C", tmp, "rev-parse", "HEAD"], check=True,
                        capture_output=True, text=True,
                    ).stdout.strip(),
                    head_before,
                )

    def test_loan_resume_all_ids_reject_altered_header_fields_and_omitted_amounts(self):
        script = SKILL_DIR / "scripts" / "finance.py"
        mutations = (
            (
                "secondary-date",
                lambda text: text.replace(
                    "2026-10-08 Personal loan payment",
                    "2026-10-08=2026-10-09 Personal loan payment",
                    1,
                ),
            ),
            (
                "status",
                lambda text: text.replace(
                    "2026-10-08 Personal loan payment",
                    "2026-10-08 * Personal loan payment",
                    1,
                ),
            ),
            (
                "code",
                lambda text: text.replace(
                    "2026-10-08 Personal loan payment",
                    "2026-10-08 (changed) Personal loan payment",
                    1,
                ),
            ),
            (
                "omitted-principal-amount",
                lambda text: text.replace("  52891 TWD", "", 1),
            ),
            (
                "omitted-interest-amount",
                lambda text: text.replace("  3338 TWD", "", 1),
            ),
            (
                "omitted-payment-amount",
                lambda text: text.replace("  -56229 TWD", "", 1),
            ),
        )
        for name, mutate in mutations:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                journal = Path(tmp) / "main.journal"
                base = [sys.executable, str(script), "--journal", str(journal)]
                subprocess.run(base + ["init"], check=True, capture_output=True, text=True)
                subprocess.run(base + self.loan_resume_args(), check=True, capture_output=True, text=True)
                original = journal.read_text(encoding="utf-8")
                changed = mutate(original)
                self.assertNotEqual(changed, original)
                journal.write_text(changed, encoding="utf-8")
                subprocess.run(["git", "-C", tmp, "add", "main.journal"], check=True)
                subprocess.run(["git", "-C", tmp, "commit", "-qm", "tamper fixture"], check=True)
                before = journal.read_bytes()
                head_before = subprocess.run(
                    ["git", "-C", tmp, "rev-parse", "HEAD"],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip()
                result = subprocess.run(
                    base + self.loan_resume_args(), check=False, capture_output=True, text=True
                )
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("terms-mismatch", result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertEqual(journal.read_bytes(), before)
                self.assertEqual(
                    subprocess.run(
                        ["git", "-C", tmp, "rev-parse", "HEAD"],
                        check=True,
                        capture_output=True,
                        text=True,
                    ).stdout.strip(),
                    head_before,
                )

    def test_loan_resume_rejects_tampered_root_and_included_schedules(self):
        script = SKILL_DIR / "scripts" / "finance.py"
        for location in ("root", "included"):
            with self.subTest(location=location), tempfile.TemporaryDirectory() as tmp:
                journal = Path(tmp) / "main.journal"
                base = [sys.executable, str(script), "--journal", str(journal)]
                subprocess.run(base + ["init"], check=True, capture_output=True, text=True)
                subprocess.run(base + self.loan_resume_args(), check=True, capture_output=True, text=True)
                original = journal.read_text(encoding="utf-8")
                prefix, schedule = original.split("2026-10-08 Personal loan payment", 1)
                schedule = "2026-10-08 Personal loan payment" + schedule
                schedule = schedule.replace("52891 TWD", "52890 TWD", 1).replace(
                    "-56229 TWD", "-56228 TWD", 1
                )
                if location == "root":
                    journal.write_text(prefix + schedule, encoding="utf-8")
                else:
                    included = Path(tmp) / "loans.journal"
                    included.write_text(schedule, encoding="utf-8")
                    journal.write_text(prefix + "include loans.journal\n", encoding="utf-8")
                subprocess.run(["git", "-C", tmp, "add", "-A"], check=True)
                subprocess.run(["git", "-C", tmp, "commit", "-qm", "tamper fixture"], check=True)
                before = journal.read_bytes()
                included_before = (
                    (Path(tmp) / "loans.journal").read_bytes() if location == "included" else None
                )
                head_before = subprocess.run(
                    ["git", "-C", tmp, "rev-parse", "HEAD"], check=True,
                    capture_output=True, text=True,
                ).stdout.strip()
                result = subprocess.run(
                    base + self.loan_resume_args(), check=False, capture_output=True, text=True
                )
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("terms-mismatch", result.stderr)
                self.assertEqual(journal.read_bytes(), before)
                if included_before is not None:
                    self.assertEqual((Path(tmp) / "loans.journal").read_bytes(), included_before)
                self.assertEqual(
                    subprocess.run(
                        ["git", "-C", tmp, "rev-parse", "HEAD"], check=True,
                        capture_output=True, text=True,
                    ).stdout.strip(),
                    head_before,
                )

    def test_loan_resume_partial_collision_fails_closed(self):
        script = SKILL_DIR / "scripts" / "finance.py"
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "main.journal"
            included = Path(tmp) / "history.journal"
            base = [sys.executable, str(script), "--journal", str(journal)]
            subprocess.run(base + ["init"], check=True, capture_output=True, text=True)
            included.write_text(
                "2026-10-08 Existing collision\n"
                "    ; import-id:loan.personal-3200000.period.26\n"
                "    expenses:test  1 TWD\n"
                "    assets:cash  -1 TWD\n",
                encoding="utf-8",
            )
            journal.write_text(
                journal.read_text(encoding="utf-8") + "include history.journal\n",
                encoding="utf-8",
            )
            subprocess.run(
                ["git", "-C", tmp, "add", "main.journal", "history.journal"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", tmp, "commit", "-qm", "Add collision fixture"],
                check=True,
            )
            root_before = journal.read_bytes()
            included_before = included.read_bytes()
            head_before = subprocess.run(
                ["git", "-C", tmp, "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            result = subprocess.run(
                base + self.loan_resume_args(),
                check=False,
                capture_output=True,
                text=True,
            )
            head_after = subprocess.run(
                ["git", "-C", tmp, "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            root_after = journal.read_bytes()
            included_after = included.read_bytes()
        self.assertEqual(result.returncode, 2)
        self.assertIn("Partial loan schedule", result.stderr)
        self.assertEqual(root_after, root_before)
        self.assertEqual(included_after, included_before)
        self.assertEqual(head_after, head_before)

    def test_concurrent_loan_resume_writes_schedule_once(self):
        script = SKILL_DIR / "scripts" / "finance.py"
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "main.journal"
            base = [sys.executable, str(script), "--journal", str(journal)]
            subprocess.run(base + ["init"], check=True, capture_output=True, text=True)
            lock = _acquire_journal_lock(journal)
            try:
                processes = [
                    subprocess.Popen(
                        base + self.loan_resume_args(),
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                    )
                    for _ in range(2)
                ]
                time.sleep(0.3)
                self.assertTrue(all(process.poll() is None for process in processes))
            finally:
                lock.close()
            results = [process.communicate(timeout=15) for process in processes]
            text = journal.read_text(encoding="utf-8")
            subjects = subprocess.run(
                ["git", "-C", tmp, "log", "--format=%s"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.splitlines()
        for process, (stdout, stderr) in zip(processes, results):
            self.assertEqual(process.returncode, 0, stderr or stdout)
        self.assertEqual(text.count("; source:scheduled-loan"), 35)
        self.assertEqual(
            subjects.count("Add remaining loan schedule: Personal loan payment"), 1
        )

    def test_loan_resume_restores_journal_when_git_commit_fails(self):
        script = SKILL_DIR / "scripts" / "finance.py"
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "main.journal"
            base = [sys.executable, str(script), "--journal", str(journal)]
            subprocess.run(base + ["init"], check=True, capture_output=True, text=True)
            hook = Path(tmp) / ".git" / "hooks" / "pre-commit"
            hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            hook.chmod(0o755)
            before = journal.read_bytes()
            head_before = subprocess.run(
                ["git", "-C", tmp, "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            index_before = subprocess.run(
                ["git", "-C", tmp, "diff", "--cached", "--binary"],
                check=True,
                capture_output=True,
            ).stdout
            result = subprocess.run(
                base + self.loan_resume_args(),
                check=False,
                capture_output=True,
                text=True,
            )
            head_after = subprocess.run(
                ["git", "-C", tmp, "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            index_after = subprocess.run(
                ["git", "-C", tmp, "diff", "--cached", "--binary"],
                check=True,
                capture_output=True,
            ).stdout
            after = journal.read_bytes()
        self.assertEqual(result.returncode, 2)
        self.assertEqual(after, before)
        self.assertEqual(head_after, head_before)
        self.assertEqual(index_after, index_before)

    def test_strict_audit_rejects_future_malformed_loan_payment(self):
        script = SKILL_DIR / "scripts" / "finance.py"
        future = date(date.today().year + 20, 1, 1)
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "main.journal"
            journal.write_text(
                f"{future.isoformat()} Future loan [1/1]\n"
                "    ; source:scheduled-loan\n"
                "    ; kind:loan-payment\n"
                "    ; loan-id:future-loan\n"
                "    ; loan-period:1\n"
                "    ; loan-total-periods:1\n"
                "    ; import-id:loan.future-loan.period.1\n"
                "    (liabilities:loans:future)  100 TWD\n"
                "    (expenses:interest:loans)  0 TWD\n"
                "    (assets:bank:checking)  -100 TWD\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "--journal",
                    str(journal),
                    "audit",
                    "--strict",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(result.returncode, 2, result.stderr or result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["transaction_count"], 1)
        self.assertIn(
            "tagged-transaction-invalid-structure",
            {finding["code"] for finding in report["findings"]},
        )

    def test_strict_audit_rejects_implicit_amount_in_included_loan_source(self):
        script = SKILL_DIR / "scripts" / "finance.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "main.journal"
            included = Path(tmp) / "loans.journal"
            root.write_text(
                "comment\n"
                "2026-01-01 Ignored fake loan [1/1]\n"
                "    ; kind:loan-payment\n"
                "    liabilities:fake\n"
                "end comment\n"
                "include loans.journal\n",
                encoding="utf-8",
            )
            included.write_text(
                "2026-01-31 Included loan [1/1]\n"
                "    ; source:scheduled-loan\n"
                "    ; kind:loan-payment\n"
                "    ; loan-id:included-loan\n"
                "    ; loan-period:1\n"
                "    ; loan-total-periods:1\n"
                "    ; import-id:loan.included-loan.period.1\n"
                "    liabilities:loans:included  100 TWD  ; principal\n"
                "    expenses:interest:loans  0 TWD\n"
                "    ; payment amount is deliberately omitted\n"
                "    assets:bank:checking\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "--journal",
                    str(root),
                    "audit",
                    "--strict",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(result.returncode, 2, result.stderr or result.stdout)
        self.assertNotIn("Traceback", result.stderr)
        report = json.loads(result.stdout)
        self.assertIn(
            "scheduled-loan-implicit-posting-amount",
            {finding["code"] for finding in report["findings"]},
        )

    def test_strict_audit_includes_valid_future_loan_schedule(self):
        script = SKILL_DIR / "scripts" / "finance.py"
        future = date(date.today().year + 20, 1, 8).isoformat()
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "main.journal"
            base = [sys.executable, str(script), "--journal", str(journal)]
            subprocess.run(base + ["init"], check=True, capture_output=True, text=True)
            command = self.loan_resume_args()
            command[command.index("--start") + 1] = future
            subprocess.run(base + command, check=True, capture_output=True, text=True)
            result = subprocess.run(
                base + ["audit", "--strict"],
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["transaction_count"], 35)
        self.assertTrue(report["ok"], report["findings"])

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

    def test_audit_rejects_root_and_included_source_mutation_during_hledger_parse(self):
        script = SKILL_DIR / "scripts" / "finance.py"
        transaction = (
            "2026-01-31 Raced loan [1/1]\n"
            "    ; source:scheduled-loan\n"
            "    ; kind:loan-payment\n"
            "    ; loan-id:raced-loan\n"
            "    ; loan-period:1\n"
            "    ; loan-total-periods:1\n"
            "    ; import-id:loan.raced-loan.period.1\n"
            "    liabilities:loans:raced  100 TWD\n"
            "    expenses:interest:loans  0 TWD\n"
            "    assets:bank:checking  -100 TWD\n"
        )
        mutator = (
            "#!/usr/bin/python3\n"
            "import os, pathlib, sys\n"
            "if 'print' in sys.argv:\n"
            "    target = pathlib.Path(os.environ['MUTATE_TARGET'])\n"
            "    original = target.read_text(encoding='utf-8')\n"
            "    changed = original.replace('  -100 TWD', '', 1)\n"
            "    if os.environ['MUTATE_MODE'] == 'replace':\n"
            "        replacement = target.with_name(target.name + '.replacement')\n"
            "        replacement.write_text(changed, encoding='utf-8')\n"
            "        os.replace(replacement, target)\n"
            "    else:\n"
            "        target.write_text(changed, encoding='utf-8')\n"
            "os.execv('/usr/bin/hledger', ['/usr/bin/hledger', *sys.argv[1:]])\n"
        )
        for location in ("root", "included"):
            for mode in ("replace", "in-place"):
                with self.subTest(location=location, mode=mode), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp) / "main.journal"
                    if location == "root":
                        root.write_text(transaction, encoding="utf-8")
                        target = root
                    else:
                        target = Path(tmp) / "loans.journal"
                        target.write_text(transaction, encoding="utf-8")
                        root.write_text("include loans.journal\n", encoding="utf-8")
                    bin_dir = Path(tmp) / "bin"
                    bin_dir.mkdir()
                    wrapper = bin_dir / "hledger"
                    wrapper.write_text(mutator, encoding="utf-8")
                    wrapper.chmod(0o755)
                    environment = os.environ.copy()
                    environment.update(
                        {
                            "PATH": f"{bin_dir}{os.pathsep}{environment['PATH']}",
                            "MUTATE_TARGET": str(target),
                            "MUTATE_MODE": mode,
                        }
                    )
                    result = subprocess.run(
                        [
                            sys.executable,
                            str(script),
                            "--journal",
                            str(root),
                            "audit",
                            "--strict",
                        ],
                        check=False,
                        capture_output=True,
                        text=True,
                        env=environment,
                    )
                self.assertEqual(result.returncode, 2, result.stderr or result.stdout)
                self.assertIn("changed", result.stderr.lower())
                self.assertNotIn("Traceback", result.stderr)

    def test_strict_audit_rejects_source_added_after_initial_enumeration(self):
        script = SKILL_DIR / "scripts" / "finance.py"
        wrapper_text = (
            "#!/usr/bin/python3\n"
            "import pathlib, subprocess, sys\n"
            "state = pathlib.Path(__file__).with_name('enumerated')\n"
            "if 'files' in sys.argv and not state.exists():\n"
            "    result = subprocess.run(['/usr/bin/hledger', *sys.argv[1:]], "
            "capture_output=True, text=True)\n"
            "    state.write_text('yes', encoding='utf-8')\n"
            "    target = pathlib.Path(__file__).parents[1] / 'parts' / 'added.journal'\n"
            "    target.write_text('; added after enumeration\\n', encoding='utf-8')\n"
            "    sys.stdout.write(result.stdout)\n"
            "    sys.stderr.write(result.stderr)\n"
            "    raise SystemExit(result.returncode)\n"
            "os_exec = __import__('os').execv\n"
            "os_exec('/usr/bin/hledger', ['/usr/bin/hledger', *sys.argv[1:]])\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "main.journal"
            parts = Path(tmp) / "parts"
            parts.mkdir()
            (parts / "base.journal").write_text("; stable include\n", encoding="utf-8")
            root.write_text("include parts/*.journal\n", encoding="utf-8")
            bin_dir = Path(tmp) / "bin"
            bin_dir.mkdir()
            wrapper = bin_dir / "hledger"
            wrapper.write_text(wrapper_text, encoding="utf-8")
            wrapper.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = f"{bin_dir}{os.pathsep}{environment['PATH']}"
            result = subprocess.run(
                [sys.executable, str(script), "--journal", str(root), "audit", "--strict"],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )
        self.assertEqual(result.returncode, 2, result.stderr or result.stdout)
        self.assertIn("source set changed", result.stderr.lower())
        self.assertNotIn("Traceback", result.stderr)

    def test_audit_rejects_root_change_during_initial_source_enumeration(self):
        script = SKILL_DIR / "scripts" / "finance.py"
        wrapper_text = (
            "#!/usr/bin/python3\n"
            "import os, pathlib, subprocess, sys\n"
            "state = pathlib.Path(__file__).with_name('enumerated')\n"
            "if 'files' in sys.argv and not state.exists():\n"
            "    result = subprocess.run(['/usr/bin/hledger', *sys.argv[1:]], "
            "capture_output=True, text=True)\n"
            "    state.write_text('yes', encoding='utf-8')\n"
            "    root = pathlib.Path(os.environ['ROOT_JOURNAL'])\n"
            "    root.write_text(root.read_text(encoding='utf-8') + '; raced root\\n', "
            "encoding='utf-8')\n"
            "    sys.stdout.write(result.stdout)\n"
            "    sys.stderr.write(result.stderr)\n"
            "    raise SystemExit(result.returncode)\n"
            "os.execv('/usr/bin/hledger', ['/usr/bin/hledger', *sys.argv[1:]])\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "main.journal"
            root.write_text("; stable root\n", encoding="utf-8")
            bin_dir = Path(tmp) / "bin"
            bin_dir.mkdir()
            wrapper = bin_dir / "hledger"
            wrapper.write_text(wrapper_text, encoding="utf-8")
            wrapper.chmod(0o755)
            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": f"{bin_dir}{os.pathsep}{environment['PATH']}",
                    "ROOT_JOURNAL": str(root),
                }
            )
            result = subprocess.run(
                [sys.executable, str(script), "--journal", str(root), "audit", "--strict"],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )
        self.assertEqual(result.returncode, 2, result.stderr or result.stdout)
        self.assertIn("root journal changed", result.stderr.lower())
        self.assertNotIn("Traceback", result.stderr)

    def test_audit_rejects_added_removed_and_renamed_sources_after_parse(self):
        script = SKILL_DIR / "scripts" / "finance.py"
        wrapper_text = (
            "#!/usr/bin/python3\n"
            "import os, pathlib, subprocess, sys\n"
            "state = pathlib.Path(__file__).with_name('parsed')\n"
            "if 'print' in sys.argv and not state.exists():\n"
            "    result = subprocess.run(['/usr/bin/hledger', *sys.argv[1:]], "
            "capture_output=True, text=True)\n"
            "    state.write_text('yes', encoding='utf-8')\n"
            "    parts = pathlib.Path(os.environ['PARTS'])\n"
            "    mode = os.environ['MUTATE_MODE']\n"
            "    if mode == 'add':\n"
            "        (parts / 'added.journal').write_text('; added\\n', encoding='utf-8')\n"
            "    elif mode == 'remove':\n"
            "        (parts / 'target.journal').unlink()\n"
            "    else:\n"
            "        (parts / 'target.journal').rename(parts / 'renamed.journal')\n"
            "    sys.stdout.write(result.stdout)\n"
            "    sys.stderr.write(result.stderr)\n"
            "    raise SystemExit(result.returncode)\n"
            "os.execv('/usr/bin/hledger', ['/usr/bin/hledger', *sys.argv[1:]])\n"
        )
        for mode in ("add", "remove", "rename"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / "main.journal"
                parts = Path(tmp) / "parts"
                parts.mkdir()
                (parts / "base.journal").write_text("; stable\n", encoding="utf-8")
                if mode != "add":
                    (parts / "target.journal").write_text("; target\n", encoding="utf-8")
                root.write_text("include parts/*.journal\n", encoding="utf-8")
                bin_dir = Path(tmp) / "bin"
                bin_dir.mkdir()
                wrapper = bin_dir / "hledger"
                wrapper.write_text(wrapper_text, encoding="utf-8")
                wrapper.chmod(0o755)
                environment = os.environ.copy()
                environment.update(
                    {
                        "PATH": f"{bin_dir}{os.pathsep}{environment['PATH']}",
                        "PARTS": str(parts),
                        "MUTATE_MODE": mode,
                    }
                )
                result = subprocess.run(
                    [sys.executable, str(script), "--journal", str(root), "audit", "--strict"],
                    check=False,
                    capture_output=True,
                    text=True,
                    env=environment,
                )
            self.assertEqual(result.returncode, 2, result.stderr or result.stdout)
            self.assertIn("source set changed", result.stderr.lower())
            self.assertNotIn("Traceback", result.stderr)

    def test_ingest_and_loan_resume_reject_source_addition_before_parse(self):
        script = SKILL_DIR / "scripts" / "finance.py"
        wrapper_text = (
            "#!/usr/bin/python3\n"
            "import os, pathlib, sys\n"
            "state = pathlib.Path(__file__).with_name('mutated')\n"
            "if 'print' in sys.argv and not state.exists():\n"
            "    state.write_text('yes', encoding='utf-8')\n"
            "    pathlib.Path(os.environ['ADDED_SOURCE']).write_text('; raced include\\n', "
            "encoding='utf-8')\n"
            "os.execv('/usr/bin/hledger', ['/usr/bin/hledger', *sys.argv[1:]])\n"
        )
        for command_name in ("ingest", "loan-resume"):
            with self.subTest(command=command_name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / "main.journal"
                base = [sys.executable, str(script), "--journal", str(root)]
                subprocess.run(base + ["init"], check=True, capture_output=True, text=True)
                parts = Path(tmp) / "parts"
                parts.mkdir()
                (parts / "base.journal").write_text("; stable include\n", encoding="utf-8")
                root.write_text(
                    root.read_text(encoding="utf-8") + "include parts/*.journal\n",
                    encoding="utf-8",
                )
                subprocess.run(["git", "-C", tmp, "add", "-A"], check=True)
                subprocess.run(["git", "-C", tmp, "commit", "-qm", "add include"], check=True)
                if command_name == "ingest":
                    batch = Path(tmp) / "batch.json"
                    batch.write_text(
                        json.dumps(
                            {
                                "transactions": [
                                    {
                                        "kind": "expense",
                                        "date": "2026-01-01",
                                        "description": "Race fixture",
                                        "amount": "1",
                                        "currency": "TWD",
                                        "debit": "expenses:test",
                                        "credit": "assets:cash",
                                        "import_id": "race-fixture-1",
                                        "tags": ["source:manual"],
                                    }
                                ]
                            }
                        ),
                        encoding="utf-8",
                    )
                    command = base + ["ingest-json", str(batch)]
                else:
                    command = base + self.loan_resume_args()
                before = root.read_bytes()
                head_before = subprocess.run(
                    ["git", "-C", tmp, "rev-parse", "HEAD"],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip()
                bin_dir = Path(tmp) / "bin"
                bin_dir.mkdir()
                wrapper = bin_dir / "hledger"
                wrapper.write_text(wrapper_text, encoding="utf-8")
                wrapper.chmod(0o755)
                environment = os.environ.copy()
                environment.update(
                    {
                        "PATH": f"{bin_dir}{os.pathsep}{environment['PATH']}",
                        "ADDED_SOURCE": str(parts / "added.journal"),
                    }
                )
                result = subprocess.run(
                    command,
                    check=False,
                    capture_output=True,
                    text=True,
                    env=environment,
                )
                head_after = subprocess.run(
                    ["git", "-C", tmp, "rev-parse", "HEAD"],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip()
                after = root.read_bytes()
            self.assertEqual(result.returncode, 2, result.stderr or result.stdout)
            self.assertIn("source set changed", result.stderr.lower())
            self.assertEqual(after, before)
            self.assertEqual(head_after, head_before)
            self.assertNotIn("Traceback", result.stderr)

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


class CrossCurrencyTransferTests(unittest.TestCase):
    """RED tests for cross-currency historical transfer rendering and validation."""

    def test_render_cross_currency_uses_total_price_syntax_for_debit(self):
        journal = render_cross_currency_transfer(
            date(2024, 9, 8),
            "MOZE loan repayment",
            "liabilities:loans:personal",
            "assets:cash:wallet",
            amount=Decimal("3000"),
            currency="TWD",
            debit_amount=Decimal("14368"),
            debit_currency="JPY",
        )
        # debit_amount (14368 JPY) is the debit posting; amount (3000 TWD) is the @@ target and credit
        self.assertIn("14368 JPY @@ 3000 TWD", journal)
        self.assertIn("liabilities:loans:personal", journal)
        self.assertIn("assets:cash:wallet", journal)
        self.assertIn("-3000 TWD", journal)
        # The @@ target and credit must both use `amount`, NOT debit_amount
        self.assertNotIn("@@ 14368 TWD", journal)
        self.assertNotIn("-14368 TWD", journal)

    def test_render_cross_currency_balances_with_hledger_check(self):
        journal = render_cross_currency_transfer(
            date(2024, 9, 8),
            "Cross-currency transfer",
            "liabilities:card",
            "assets:bank",
            amount=Decimal("12345"),
            currency="TWD",
            debit_amount=Decimal("5000"),
            debit_currency="USD",
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

    def test_render_cross_currency_rejects_zero_debit_amount(self):
        with self.assertRaisesRegex(ValueError, "debit_amount"):
            render_cross_currency_transfer(
                date(2024, 9, 8),
                "Zero debit",
                "assets:bank",
                "assets:cash",
                amount=Decimal("3000"),
                currency="TWD",
                debit_amount=Decimal("0"),
                debit_currency="USD",
            )

    def test_render_cross_currency_rejects_negative_debit_amount(self):
        with self.assertRaisesRegex(ValueError, "debit_amount"):
            render_cross_currency_transfer(
                date(2024, 9, 8),
                "Negative debit",
                "assets:bank",
                "assets:cash",
                amount=Decimal("3000"),
                currency="TWD",
                debit_amount=Decimal("-50"),
                debit_currency="USD",
            )

    def test_render_cross_currency_rejects_nonfinite_debit_amount(self):
        with self.assertRaisesRegex(ValueError, "debit_amount"):
            render_cross_currency_transfer(
                date(2024, 9, 8),
                "NaN debit",
                "assets:bank",
                "assets:cash",
                amount=Decimal("3000"),
                currency="TWD",
                debit_amount=Decimal("NaN"),
                debit_currency="USD",
            )

    def test_render_cross_currency_rejects_same_debit_and_credit_currency(self):
        with self.assertRaisesRegex(ValueError, "different"):
            render_cross_currency_transfer(
                date(2024, 9, 8),
                "Same currency",
                "assets:bank",
                "assets:cash",
                amount=Decimal("3000"),
                currency="TWD",
                debit_amount=Decimal("50"),
                debit_currency="TWD",
            )

    def test_render_cross_currency_rejects_invalid_debit_currency(self):
        with self.assertRaisesRegex(ValueError, "debit_currency"):
            render_cross_currency_transfer(
                date(2024, 9, 8),
                "Bad currency",
                "assets:bank",
                "assets:cash",
                amount=Decimal("3000"),
                currency="TWD",
                debit_amount=Decimal("50"),
                debit_currency="USD; kind:expense",
            )

    def test_render_cross_currency_rejects_zero_amount(self):
        with self.assertRaisesRegex(ValueError, "amount"):
            render_cross_currency_transfer(
                date(2024, 9, 8),
                "Zero amount",
                "assets:bank",
                "assets:cash",
                amount=Decimal("0"),
                currency="TWD",
                debit_amount=Decimal("14368"),
                debit_currency="JPY",
            )

    def test_render_cross_currency_rejects_negative_amount(self):
        with self.assertRaisesRegex(ValueError, "amount"):
            render_cross_currency_transfer(
                date(2024, 9, 8),
                "Negative amount",
                "assets:bank",
                "assets:cash",
                amount=Decimal("-3000"),
                currency="TWD",
                debit_amount=Decimal("14368"),
                debit_currency="JPY",
            )

    def test_render_cross_currency_rejects_nonfinite_amount(self):
        with self.assertRaisesRegex(ValueError, "amount"):
            render_cross_currency_transfer(
                date(2024, 9, 8),
                "NaN amount",
                "assets:bank",
                "assets:cash",
                amount=Decimal("NaN"),
                currency="TWD",
                debit_amount=Decimal("14368"),
                debit_currency="JPY",
            )

    def test_render_cross_currency_rejects_infinity_amount(self):
        with self.assertRaisesRegex(ValueError, "amount"):
            render_cross_currency_transfer(
                date(2024, 9, 8),
                "Infinity amount",
                "assets:bank",
                "assets:cash",
                amount=Decimal("Infinity"),
                currency="TWD",
                debit_amount=Decimal("14368"),
                debit_currency="JPY",
            )

    def test_build_ingest_rejects_single_cross_currency_field(self):
        payload = {
            "transactions": [
                {
                    "kind": "transfer",
                    "date": "2024-09-08",
                    "description": "MOZE repayment",
                    "amount": "50629",
                    "currency": "TWD",
                    "debit": "liabilities:loans:personal",
                    "credit": "assets:cash:wallet",
                    "import_id": "moze-repay-001",
                    "tags": ["source:historical-import"],
                    "debit_amount": "14368",
                }
            ]
        }
        with self.assertRaisesRegex(ValueError, "debit_amount|debit_currency"):
            build_ingest_transactions(payload, existing_text="")

    def test_build_ingest_rejects_kind_transfer_missing_cross_currency_fields(self):
        payload = {
            "transactions": [
                {
                    "kind": "transfer",
                    "date": "2024-09-08",
                    "description": "Loan repayment",
                    "amount": "3000",
                    "currency": "TWD",
                    "debit": "liabilities:loans:personal",
                    "credit": "assets:cash:wallet",
                    "import_id": "moze-repay-001",
                    "tags": ["source:historical-import"],
                    "debit_amount": "14368",
                    "debit_currency": "JPY",
                }
            ]
        }
        # Same-currency cross-currency transfer: credit TWD but debit TWD
        payload["transactions"][0]["debit_currency"] = "TWD"
        with self.assertRaisesRegex(ValueError, "different"):
            build_ingest_transactions(payload, existing_text="")

    def test_build_ingest_rejects_installments_on_cross_currency_transfer(self):
        payload = {
            "transactions": [
                {
                    "kind": "transfer",
                    "date": "2024-09-08",
                    "description": "Loan repayment",
                    "amount": "3000",
                    "currency": "TWD",
                    "debit": "liabilities:loans:personal",
                    "credit": "assets:cash:wallet",
                    "import_id": "moze-repay-001",
                    "tags": ["source:historical-import"],
                    "debit_amount": "14368",
                    "debit_currency": "JPY",
                    "installments": 3,
                }
            ]
        }
        with self.assertRaisesRegex(ValueError, "installments"):
            build_ingest_transactions(payload, existing_text="")

    def test_build_ingest_rejects_fees_on_cross_currency_transfer(self):
        payload = {
            "transactions": [
                {
                    "kind": "transfer",
                    "date": "2024-09-08",
                    "description": "Loan repayment",
                    "amount": "3000",
                    "currency": "TWD",
                    "debit": "liabilities:loans:personal",
                    "credit": "assets:cash:wallet",
                    "import_id": "moze-repay-001",
                    "tags": ["source:historical-import"],
                    "debit_amount": "14368",
                    "debit_currency": "JPY",
                    "fee": "50",
                }
            ]
        }
        with self.assertRaisesRegex(ValueError, "fee"):
            build_ingest_transactions(payload, existing_text="")

    def test_build_ingest_rejects_non_positive_debit_amount(self):
        payload = {
            "transactions": [
                {
                    "kind": "transfer",
                    "date": "2024-09-08",
                    "description": "MOZE repayment",
                    "amount": "3000",
                    "currency": "TWD",
                    "debit": "liabilities:loans:personal",
                    "credit": "assets:cash:wallet",
                    "import_id": "moze-repay-001",
                    "tags": ["source:historical-import"],
                    "debit_amount": "0",
                    "debit_currency": "USD",
                }
            ]
        }
        with self.assertRaisesRegex(ValueError, "debit_amount"):
            build_ingest_transactions(payload, existing_text="")

    def test_build_ingest_rejects_non_finite_debit_amount(self):
        payload = {
            "transactions": [
                {
                    "kind": "transfer",
                    "date": "2024-09-08",
                    "description": "MOZE repayment",
                    "amount": "3000",
                    "currency": "TWD",
                    "debit": "liabilities:loans:personal",
                    "credit": "assets:cash:wallet",
                    "import_id": "moze-repay-001",
                    "tags": ["source:historical-import"],
                    "debit_amount": "Infinity",
                    "debit_currency": "USD",
                }
            ]
        }
        with self.assertRaisesRegex(ValueError, "debit_amount|finite"):
            build_ingest_transactions(payload, existing_text="")

    def test_build_ingest_rejects_invalid_debit_currency_format(self):
        payload = {
            "transactions": [
                {
                    "kind": "transfer",
                    "date": "2024-09-08",
                    "description": "MOZE repayment",
                    "amount": "3000",
                    "currency": "TWD",
                    "debit": "liabilities:loans:personal",
                    "credit": "assets:cash:wallet",
                    "import_id": "moze-repay-001",
                    "tags": ["source:historical-import"],
                    "debit_amount": "14368",
                    "debit_currency": "JPY; kind:expense",
                }
            ]
        }
        with self.assertRaisesRegex(ValueError, "debit_currency"):
            build_ingest_transactions(payload, existing_text="")

    def test_build_ingest_allows_regular_transfer_without_cross_currency(self):
        payload = {
            "transactions": [
                {
                    "kind": "transfer",
                    "date": "2024-09-08",
                    "description": "Transfer between accounts",
                    "amount": "10000",
                    "currency": "TWD",
                    "debit": "assets:bank",
                    "credit": "assets:cash",
                    "import_id": "transfer-001",
                    "tags": ["source:historical-import"],
                }
            ]
        }
        journal_text, imported, skipped, _ = build_ingest_transactions(
            payload, existing_text=""
        )
        self.assertEqual((imported, skipped), (1, 0))
        self.assertIn("10000 TWD", journal_text)
        self.assertNotIn("@@", journal_text)
        self.assertIn("assets:bank", journal_text)
        self.assertIn("assets:cash", journal_text)

    def test_build_ingest_rendered_cross_currency_transaction_passes_hledger_check(self):
        payload = {
            "transactions": [
                {
                    "kind": "transfer",
                    "date": "2024-09-08",
                    "description": "Loan principal repayment",
                    "amount": "3000",
                    "currency": "TWD",
                    "debit": "liabilities:loans:personal",
                    "credit": "assets:cash:wallet",
                    "import_id": "moze-repay-001",
                    "tags": ["source:historical-import"],
                    "debit_amount": "14368",
                    "debit_currency": "JPY",
                }
            ]
        }
        journal_text, imported, skipped, _ = build_ingest_transactions(
            payload, existing_text=""
        )
        self.assertEqual((imported, skipped), (1, 0))
        # Verify the rendered text has the correct @@ target and credit
        self.assertIn("14368 JPY @@ 3000 TWD", journal_text)
        self.assertIn("-3000 TWD", journal_text)
        self.assertNotIn("@@ 14368 TWD", journal_text)
        self.assertNotIn("-14368 TWD", journal_text)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "book.journal"
            path.write_text(journal_text, encoding="utf-8")
            result = subprocess.run(
                ["hledger", "-f", str(path), "check"],
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_build_ingest_cross_currency_audit_reports_no_structure_error(self):
        payload = {
            "transactions": [
                {
                    "kind": "transfer",
                    "date": "2024-09-08",
                    "description": "Cross-currency asset transfer",
                    "amount": "14368",
                    "currency": "JPY",
                    "debit": "assets:bank:checking",
                    "credit": "assets:cash:wallet",
                    "import_id": "moze-asset-pair-001",
                    "tags": ["source:historical-import"],
                    "debit_amount": "3000",
                    "debit_currency": "TWD",
                }
            ]
        }
        journal_text, imported, skipped, _ = build_ingest_transactions(
            payload, existing_text=""
        )
        self.assertEqual((imported, skipped), (1, 0))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cross-currency.journal"
            path.write_text(journal_text, encoding="utf-8")
            printed = subprocess.run(
                ["hledger", "-f", str(path), "print", "--output-format=json"],
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertEqual(printed.returncode, 0, printed.stderr)
        transactions = json.loads(printed.stdout)
        self.assertEqual(len(transactions), 1)
        postings = {posting["paccount"]: posting for posting in transactions[0]["tpostings"]}
        destination_amount = postings["assets:bank:checking"]["pamount"][0]
        source_amount = postings["assets:cash:wallet"]["pamount"][0]
        self.assertEqual(destination_amount["acommodity"], "TWD")
        self.assertEqual(_amount_decimal(destination_amount), Decimal("3000"))
        self.assertEqual(source_amount["acommodity"], "JPY")
        self.assertEqual(_amount_decimal(source_amount), Decimal("-14368"))

        report = audit_journal(transactions, journal_text)
        self.assertTrue(report["ok"], report["findings"])
        self.assertEqual(report["critical_count"], 0)
        self.assertNotIn(
            "tagged-transaction-invalid-structure",
            {f["code"] for f in report["findings"]}
        )

    def test_build_ingest_preserves_kind_import_id_source_tags_on_cross_currency(self):
        payload = {
            "transactions": [
                {
                    "kind": "transfer",
                    "date": "2024-09-08",
                    "description": "Loan principal repayment",
                    "amount": "3000",
                    "currency": "TWD",
                    "debit": "liabilities:loans:personal",
                    "credit": "assets:cash:wallet",
                    "import_id": "moze-repay-001",
                    "tags": ["source:historical-import"],
                    "debit_amount": "14368",
                    "debit_currency": "JPY",
                }
            ]
        }
        journal_text, imported, skipped, _ = build_ingest_transactions(
            payload, existing_text=""
        )
        self.assertEqual((imported, skipped), (1, 0))
        self.assertIn("; kind:transfer", journal_text)
        self.assertIn("; import-id:moze-repay-001", journal_text)
        self.assertIn("; source:historical-import", journal_text)
        # Exact ingest-to-render assertion: output must match render_cross_currency_transfer
        expected = render_cross_currency_transfer(
            date(2024, 9, 8),
            "Loan principal repayment",
            "liabilities:loans:personal",
            "assets:cash:wallet",
            amount=Decimal("3000"),
            currency="TWD",
            debit_amount=Decimal("14368"),
            debit_currency="JPY",
            tags=["source:historical-import", "kind:transfer", "import-id:moze-repay-001"],
        )
        self.assertEqual(journal_text, expected)

    def test_build_ingest_same_currency_transfer_without_debit_fields_is_normal_transfer(self):
        payload = {
            "transactions": [
                {
                    "kind": "transfer",
                    "date": "2024-09-08",
                    "description": "Normal transfer",
                    "amount": "10000",
                    "currency": "TWD",
                    "debit": "assets:bank",
                    "credit": "assets:cash",
                    "import_id": "normal-transfer-001",
                    "tags": ["source:historical-import"],
                }
            ]
        }
        journal_text, imported, skipped, _ = build_ingest_transactions(
            payload, existing_text=""
        )
        self.assertEqual((imported, skipped), (1, 0))
        self.assertIn("10000 TWD", journal_text)
        self.assertNotIn("@@", journal_text)

    def test_build_ingest_rejects_debit_amount_on_non_transfer_kind(self):
        cases = [
            ("expense", "expenses:food", "assets:cash"),
            ("income", "assets:bank", "income:salary"),
            ("refund", "assets:bank", "expenses:food"),
            ("opening-balance", "assets:bank", "equity:opening-balances"),
        ]
        for kind, debit, credit in cases:
            with self.subTest(kind=kind):
                payload = {
                    "transactions": [
                        {
                            "kind": kind,
                            "date": "2024-09-08",
                            "description": "Non-transfer with debit_amount",
                            "amount": "120",
                            "currency": "TWD",
                            "debit": debit,
                            "credit": credit,
                            "import_id": f"non-transfer-debit-{kind}",
                            "tags": ["source:historical-import"],
                            "debit_amount": "50",
                            "debit_currency": "JPY",
                        }
                    ]
                }
                with self.assertRaisesRegex(ValueError, "debit_amount.*transfer|debit_amount.*only valid"):
                    build_ingest_transactions(payload, existing_text="")

    def test_build_ingest_rejects_debit_currency_on_non_transfer_kind(self):
        for kind, debit, credit in (("expense", "expenses:food", "assets:cash"),
                                     ("income", "assets:bank", "income:salary"),
                                     ("refund", "assets:bank", "expenses:food"),
                                     ("opening-balance", "assets:bank", "equity:opening-balances")):
            with self.subTest(kind=kind):
                payload = {
                    "transactions": [
                        {
                            "kind": kind,
                            "date": "2024-09-08",
                            "description": "Non-transfer with debit_currency",
                            "amount": "120",
                            "currency": "TWD",
                            "debit": debit,
                            "credit": credit,
                            "import_id": f"non-transfer-cur-{kind}",
                            "tags": ["source:historical-import"],
                            "debit_currency": "JPY",
                        }
                    ]
                }
                with self.assertRaisesRegex(ValueError, "debit_currency.*transfer|debit_currency.*only valid"):
                    build_ingest_transactions(payload, existing_text="")

    def test_build_ingest_cross_currency_render_matches_renderer_directly(self):
        payload = {
            "transactions": [
                {
                    "kind": "transfer",
                    "date": "2024-09-08",
                    "description": "Loan principal repayment",
                    "amount": "3000",
                    "currency": "TWD",
                    "debit": "liabilities:loans:personal",
                    "credit": "assets:cash:wallet",
                    "import_id": "moze-repay-001",
                    "tags": ["source:historical-import"],
                    "debit_amount": "14368",
                    "debit_currency": "JPY",
                }
            ]
        }
        journal_text, imported, skipped, _ = build_ingest_transactions(
            payload, existing_text=""
        )
        expected = render_cross_currency_transfer(
            date(2024, 9, 8),
            "Loan principal repayment",
            "liabilities:loans:personal",
            "assets:cash:wallet",
            amount=Decimal("3000"),
            currency="TWD",
            debit_amount=Decimal("14368"),
            debit_currency="JPY",
            tags=["source:historical-import", "kind:transfer", "import-id:moze-repay-001"],
        )
        self.assertEqual(journal_text, expected)

    def test_build_ingest_rejects_negative_amount_on_cross_currency_transfer(self):
        payload = {
            "transactions": [
                {
                    "kind": "transfer",
                    "date": "2024-09-08",
                    "description": "Negative amount",
                    "amount": "-3000",
                    "currency": "TWD",
                    "debit": "liabilities:loans:personal",
                    "credit": "assets:cash:wallet",
                    "import_id": "neg-amount",
                    "tags": ["source:historical-import"],
                    "debit_amount": "14368",
                    "debit_currency": "JPY",
                }
            ]
        }
        with self.assertRaisesRegex(ValueError, "positive"):
            build_ingest_transactions(payload, existing_text="")


if __name__ == "__main__":
    unittest.main()
