# Loan Amortization Resume Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Add a safe, idempotent `hfin loan-resume` command that writes the remaining fixed-payment reducing-balance loan schedule as future-dated, semantically audited hledger transactions.

**Architecture:** Add a pure Decimal-based schedule builder and dedicated three-posting renderer in `scripts/finance.py`, then wrap them in a lock-scoped CLI command that compares the complete expected import-ID set before either doing nothing, failing closed, previewing, or using the existing atomic append/check/Git/rollback path. Extend strict audit with a reserved `kind:loan-payment` / `source:scheduled-loan` semantic form and group validation; keep this source and kind unavailable to `ingest-json` so only the deterministic command can create them.

**Tech Stack:** Python 3.11+ standard library (`argparse`, `datetime`, `decimal` with `ROUND_HALF_UP`, `fcntl`), hledger 1.32+, Git, `unittest`.

---

## Contract decisions to keep fixed during implementation

- CLI spelling and required options:

  ```text
  hfin loan-resume \
    --loan-id PERSONAL-2023 \
    --start 2026-10-08 \
    --description "Personal loan payment" \
    --principal 1907335 \
    --annual-rate 2.1 \
    --payment 56229 \
    --paid-periods 25 \
    --remaining-periods 35 \
    --currency TWD \
    --liability liabilities:loans:personal \
    --interest-account expenses:interest:loans \
    --payment-account assets:bank:checking \
    --rounding 1 \
    [--preview]
  ```

- `paid_periods` is a nonnegative integer; `remaining_periods` is positive. The absolute total is `paid_periods + remaining_periods`, and descriptions are numbered `[paid+1/total]` through `[total/total]`. `paid-periods=0` is valid and starts at `[1/remaining]`.
- Principal and fixed payment are positive finite decimals. Annual rate is a finite decimal greater than or equal to zero. Zero interest is valid.
- A supported rounding quantum is a positive power of ten no greater than one (`1`, `0.1`, `0.01`, ...). Reject `0`, negatives, non-finite values, `10`, and non-power-of-ten values such as `0.05`; `Decimal.quantize` does not implement arbitrary nickel-style increments.
- Monthly interest is `(balance * annual_rate / 100 / 12).quantize(quantum, rounding=ROUND_HALF_UP)`.
- Every non-final payment has total credit equal to the fixed payment and principal equal to `payment - interest`. Reject principal `<= 0` as negative/no amortization. If a non-final principal component is greater than or equal to the current balance, reject early payoff rather than silently shortening the requested schedule.
- The final payment debits the exact remaining principal, debits rounded interest, and credits their sum. It may differ from the fixed payment and must leave an exact zero balance.
- The loan account must be `liabilities:*`, the interest account `expenses:*`, and the payment account `assets:*` or `liabilities:*`. Reject exact equality and parent/child overlap between role accounts so a loan cannot pay itself through a parent or child account.
- Reuse `_validate_description`, `_validate_currency`, `_validate_account`, and `_validate_import_id`; do not introduce a second grammar. The canonical loan ID follows the existing import-ID grammar: nonempty ASCII letters, digits, `.`, `_`, and `-` only.
- Every generated transaction has exactly these managed tags:

  ```hledger
  ; source:scheduled-loan
  ; kind:loan-payment
  ; loan-id:PERSONAL-2023
  ; loan-period:26
  ; loan-total-periods:60
  ; import-id:loan.PERSONAL-2023.period.26
  ```

  The import ID is stable and unique for `(loan-id, absolute-period)`. The description is `Personal loan payment [26/60]`.
- Each payment has exactly three explicit regular postings in one commodity, including an explicit `0 CURRENCY` interest posting at zero rate. No virtual postings, balanced virtual postings, assignments/assertions, prices, or costs are permitted.
- Under the journal lock, build and validate the full schedule, read canonical import IDs from the root and included journals, and compare against the complete expected set. No expected IDs present means append; all present means a clean no-op; a nonempty proper subset means fail closed. This lock scope is also used by preview so collision results cannot be read from a racing state.
- `source:scheduled-loan` and `kind:loan-payment` are audit-managed values, not new `ingest-json` values. Keep `INGEST_KINDS` and the agent-approved ingest source set unchanged; add separate managed audit sets.
- `hledger print --output-format=json` already returns dated future transactions in hledger 1.32.3. Do not add the unsupported `--future` flag. Preserve the unbounded audit query and add an integration regression test with a far-future malformed scheduled loan.

## Existing seams to reuse

- Month clamping: `scripts/finance.py:29-34` (`add_months`).
- Text/account/currency/tag/ID safety: `scripts/finance.py:115-182` and `scripts/finance.py:1754-1771`.
- Canonical import-ID scanning across active included files: `scripts/finance.py:1773-1823`, `scripts/finance.py:2542-2554`.
- Semantic audit: `scripts/finance.py:696-1225`.
- Lock, candidate `hledger check`, atomic replacement, Git commit, and rollback: `scripts/finance.py:2269-2464`.
- Lock-scoped ingest/import command pattern: `scripts/finance.py:2557-2635`.
- CLI parser: `scripts/finance.py:2932-3071`.
- Existing tests for month clamping, unsafe rendering, duplicate IDs, lock/concurrency, rollback, audit shape attacks, and CLI preview: `tests/test_finance.py`.

### Task 1: Add the pure amortization schedule and exact known fixture

**Objective:** Produce the required 35-row resumed schedule with exact half-up interest, absolute numbering, stable IDs, and zero final balance.

**Files:**
- Modify: `tests/test_finance.py:12-46,73-100`
- Modify: `scripts/finance.py:22-35,103-122,516-633`

**Step 1: Write failing tests**

Import `ROUND_HALF_UP` behavior only through the public builder, not by duplicating its calculation in tests. Add `build_loan_resume_schedule` to the test import list and add a `LoanResumeScheduleTests` class. The main test must use the exact acceptance fixture and parse enough output to prove both endpoints and all IDs:

```python
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
        self.assertEqual(periods[0], {
            "period": 26,
            "total_periods": 60,
            "date": date(2026, 10, 8),
            "opening_principal": Decimal("1907335"),
            "principal": Decimal("52891"),
            "interest": Decimal("3338"),
            "payment": Decimal("56229"),
            "closing_principal": Decimal("1854444"),
            "import_id": "loan.personal-3200000.period.26",
        })
        self.assertEqual(periods[-1]["period"], 60)
        self.assertEqual(periods[-1]["principal"], Decimal("56128"))
        self.assertEqual(periods[-1]["interest"], Decimal("98"))
        self.assertEqual(periods[-1]["payment"], Decimal("56226"))
        self.assertEqual(periods[-1]["closing_principal"], Decimal("0"))
        self.assertIn("2026-10-08 Personal loan payment [26/60]", journal)
        self.assertIn("liabilities:loans:personal", journal)
        self.assertIn("expenses:interest:loans", journal)
        self.assertIn("assets:bank:checking", journal)
        self.assertIn("; import-id:loan.personal-3200000.period.26", journal)
        self.assertIn("; import-id:loan.personal-3200000.period.60", journal)
        self.assertEqual(journal.count("; source:scheduled-loan"), 35)
```

Also assert every non-final `period["payment"] == Decimal("56229")`, that the sum of principal portions is `1907335`, and that import IDs are unique.

**Step 2: Run the focused test to verify RED**

Run:

```bash
python3 -m unittest tests.test_finance.LoanResumeScheduleTests.test_exact_resumed_schedule_uses_half_up_interest_and_clears_balance -v
```

Expected: FAIL with `ImportError` because `build_loan_resume_schedule` does not exist.

**Step 3: Implement the smallest pure builder and dedicated renderer**

Import `ROUND_HALF_UP` beside `ROUND_DOWN`. Return both rendered text and schedule records so arithmetic can be tested without reparsing hledger text. Use this shape (the validation helpers are completed in Task 3):

```python
def _loan_import_id(loan_id: str, period: int) -> str:
    return f"loan.{loan_id}.period.{period}"


def render_loan_payment(
    *, txn_date: date, description: str, loan_id: str, period: int,
    total_periods: int, principal: Decimal, interest: Decimal,
    currency: str, liability_account: str, interest_account: str,
    payment_account: str,
) -> str:
    tags = (
        "source:scheduled-loan",
        "kind:loan-payment",
        f"loan-id:{loan_id}",
        f"loan-period:{period}",
        f"loan-total-periods:{total_periods}",
        f"import-id:{_loan_import_id(loan_id, period)}",
    )
    lines = [f"{txn_date.isoformat()} {description} [{period}/{total_periods}]"]
    lines.extend(f"    ; {_validate_tag(tag)}" for tag in tags)
    lines.append(f"    {liability_account:<40}  {_decimal_text(principal)} {currency}")
    lines.append(f"    {interest_account:<40}  {_decimal_text(interest)} {currency}")
    lines.append(
        f"    {payment_account:<40}  -{_decimal_text(principal + interest)} {currency}"
    )
    return "\n".join(lines) + "\n\n"


def build_loan_resume_schedule(
    *,
    loan_id: str,
    start: date,
    description: str,
    principal: Decimal,
    annual_rate: Decimal,
    payment: Decimal,
    paid_periods: int,
    remaining_periods: int,
    currency: str,
    liability_account: str,
    interest_account: str,
    payment_account: str,
    rounding_quantum: Decimal,
) -> tuple[str, list[dict]]:
    total_periods = paid_periods + remaining_periods
    monthly_rate = annual_rate / Decimal("100") / Decimal("12")
    balance = principal
    chunks: list[str] = []
    periods: list[dict] = []
    for offset in range(remaining_periods):
        absolute_period = paid_periods + offset + 1
        txn_date = add_months(start, offset)
        opening = balance
        interest = (balance * monthly_rate).quantize(
            rounding_quantum, rounding=ROUND_HALF_UP
        )
        final = offset == remaining_periods - 1
        principal_part = balance if final else payment - interest
        payment_total = principal_part + interest
        closing = balance - principal_part
        # Negative-amortization and early-payoff guards are added in Task 3.
        import_id = _loan_import_id(loan_id, absolute_period)
        periods.append(
            {
                "period": absolute_period,
                "total_periods": total_periods,
                "date": txn_date,
                "opening_principal": opening,
                "principal": principal_part,
                "interest": interest,
                "payment": payment_total,
                "closing_principal": closing,
                "import_id": import_id,
            }
        )
        chunks.append(
            render_loan_payment(
                txn_date=txn_date,
                description=description,
                loan_id=loan_id,
                period=absolute_period,
                total_periods=total_periods,
                principal=principal_part,
                interest=interest,
                currency=currency,
                liability_account=liability_account,
                interest_account=interest_account,
                payment_account=payment_account,
            )
        )
        balance = closing
    if balance != 0:
        raise ValueError("Loan schedule did not clear the outstanding principal")
    return "".join(chunks), periods
```

Do not call `render_transaction`: it skips zero-valued extra postings, while zero-rate loans still require the explicit interest posting.

**Step 4: Run the focused test to verify GREEN**

Run the same command. Expected: PASS with first split `52891/3338`, final split `56128/98`, final total `56226`, and zero balance.

**Step 5: Commit**

```bash
git add scripts/finance.py tests/test_finance.py
git commit -m "feat: calculate resumed loan schedules"
```

### Task 2: Cover month clamping, paid-periods zero, zero rates, and decimal commodities

**Objective:** Prove the pure schedule handles all supported calendar and precision variants before adding command I/O.

**Files:**
- Modify: `tests/test_finance.py` in `LoanResumeScheduleTests`
- Modify only if a failing test exposes a defect: `scripts/finance.py` in `build_loan_resume_schedule`

**Step 1: Write failing/characterization tests**

Add these complete cases:

```python
def test_schedule_reuses_month_clamping_and_absolute_numbering_from_zero(self):
    _journal, periods = build_loan_resume_schedule(
        loan_id="zero-paid", start=date(2026, 1, 31), description="Loan",
        principal=Decimal("300"), annual_rate=Decimal("0"),
        payment=Decimal("100"), paid_periods=0, remaining_periods=3,
        currency="TWD", liability_account="liabilities:loan",
        interest_account="expenses:interest",
        payment_account="assets:bank", rounding_quantum=Decimal("1"),
    )
    self.assertEqual([row["period"] for row in periods], [1, 2, 3])
    self.assertEqual(
        [row["date"] for row in periods],
        [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31)],
    )


def test_zero_rate_keeps_explicit_zero_interest_postings(self):
    journal, periods = build_loan_resume_schedule(
        loan_id="zero-rate", start=date(2026, 4, 1), description="Zero-rate loan",
        principal=Decimal("200"), annual_rate=Decimal("0"),
        payment=Decimal("100"), paid_periods=0, remaining_periods=2,
        currency="TWD", liability_account="liabilities:loan",
        interest_account="expenses:interest",
        payment_account="assets:bank", rounding_quantum=Decimal("1"),
    )
    self.assertTrue(all(row["interest"] == 0 for row in periods))
    self.assertEqual(journal.count("expenses:interest"), len(periods))
    self.assertIn("0 TWD", journal)


def test_decimal_currency_rounds_half_up_to_requested_quantum(self):
    journal, periods = build_loan_resume_schedule(
        loan_id="decimal-usd", start=date(2026, 1, 15), description="USD loan",
        principal=Decimal("100.00"), annual_rate=Decimal("12"),
        payment=Decimal("34.00"), paid_periods=0, remaining_periods=3,
        currency="USD", liability_account="liabilities:loan:usd",
        interest_account="expenses:interest:usd",
        payment_account="assets:bank:usd", rounding_quantum=Decimal("0.01"),
    )
    self.assertEqual(periods[0]["interest"], Decimal("1.00"))
    self.assertEqual(periods[0]["principal"], Decimal("33.00"))
    self.assertEqual(periods[-1]["closing_principal"], Decimal("0.00"))
    self.assertIn("1 USD", journal)
```

Use a payment/period combination that does not trigger early payoff when Task 3 adds that guard.

**Step 2: Run tests before implementation adjustment**

Run:

```bash
python3 -m unittest tests.test_finance.LoanResumeScheduleTests -v
```

Expected: at least one new assertion FAIL until zero postings, absolute numbering, and date generation are all correct; if Task 1 already satisfies a case, retain it as a regression and continue with the remaining RED case.

**Step 3: Make minimal schedule corrections**

Use `add_months(start, offset)` exactly; do not advance from the previously clamped date, which would turn January 31 → February 28 → March 28. Always emit the interest posting, even when zero.

**Step 4: Verify GREEN**

Run the class again. Expected: all loan schedule tests PASS.

**Step 5: Commit**

```bash
git add scripts/finance.py tests/test_finance.py
git commit -m "test: cover loan schedule calendar and precision"
```

### Task 3: Reject invalid arithmetic, rounding, identities, and account roles

**Objective:** Fail before rendering for every unsafe or economically impossible input.

**Files:**
- Modify: `tests/test_finance.py` in `LoanResumeScheduleTests`
- Modify: `scripts/finance.py:115-182` and the new loan helpers

**Step 1: Add table-driven failing tests**

Cover all of the following with `subTest` and focused error regexes:

```python
invalid_decimal_cases = (
    ("principal", Decimal("0"), "principal.*positive"),
    ("principal", Decimal("NaN"), "principal.*finite"),
    ("annual_rate", Decimal("-0.01"), "annual rate.*nonnegative"),
    ("annual_rate", Decimal("Infinity"), "annual rate.*finite"),
    ("payment", Decimal("0"), "payment.*positive"),
    ("payment", Decimal("NaN"), "payment.*finite"),
)
invalid_count_cases = (
    ("paid_periods", -1, "paid periods.*nonnegative"),
    ("paid_periods", True, "paid periods.*integer"),
    ("remaining_periods", 0, "remaining periods.*positive"),
    ("remaining_periods", Decimal("2"), "remaining periods.*integer"),
)
invalid_rounding = ("0", "-0.01", "NaN", "Infinity", "10", "0.05")
invalid_loan_ids = ("", "loan/id", "loan id; forged", "貸款")
```

Add dedicated tests for:

- negative amortization (`interest >= payment`) on a non-final row;
- early payoff (`payment - interest >= balance`) before the final period;
- malformed date (at command parse/call boundary);
- unsafe description, currency, account, and generated tag/ID input;
- liability not in `liabilities:*`;
- interest not in `expenses:*`;
- payment account not in `assets:*` or `liabilities:*`;
- exact and ancestor/descendant account overlap, especially `liabilities:loans` versus `liabilities:loans:personal`.

**Step 2: Run the validation tests to verify RED**

Run:

```bash
python3 -m unittest tests.test_finance.LoanResumeScheduleTests -v
```

Expected: FAIL because non-finite comparisons can raise `InvalidOperation`, unsupported quantums are accepted, and impossible schedules/accounts are not yet rejected.

**Step 3: Add centralized validation and amortization guards**

Use small helpers so CLI and tests share one implementation:

```python
def _validate_loan_decimal(value: Decimal, label: str, *, allow_zero: bool = False) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{label} must be finite")
    if value < 0 or (value == 0 and not allow_zero):
        qualifier = "nonnegative" if allow_zero else "positive"
        raise ValueError(f"{label} must be {qualifier}")
    return value


def _validate_rounding_quantum(value: Decimal) -> Decimal:
    value = _validate_loan_decimal(value, "Rounding quantum")
    normalized = value.normalize()
    if normalized > 1 or normalized.as_tuple().digits != (1,):
        raise ValueError("Rounding quantum must be a power of ten no greater than 1")
    return normalized


def _accounts_conflict(left: str, right: str) -> bool:
    return left == right or left.startswith(right + ":") or right.startswith(left + ":")
```

Validate counts with `type(value) is int`, not `isinstance(value, int)`, so booleans are rejected. Validate the loan ID through `_validate_import_id` and additionally require nonempty. Run all existing text/currency/account validators before calculations. Enforce account families and pairwise conflict checks.

Inside each non-final iteration:

```python
principal_part = payment - interest
if principal_part <= 0:
    raise ValueError(f"Loan period {absolute_period} has negative amortization")
if principal_part >= balance:
    raise ValueError(
        f"Loan would pay off before final period {total_periods}; correct the remaining periods"
    )
```

Do not apply the early-payoff check to the final row; it intentionally clears the exact balance.

**Step 4: Verify validation GREEN and arithmetic remains GREEN**

Run:

```bash
python3 -m unittest tests.test_finance.LoanResumeScheduleTests -v
```

Expected: all schedule and validation tests PASS.

**Step 5: Commit**

```bash
git add scripts/finance.py tests/test_finance.py
git commit -m "feat: validate resumed loan inputs"
```

### Task 4: Add the lock-scoped CLI, preview, and atomic write path

**Objective:** Expose all required arguments and write the complete future schedule through the existing checked, atomic, Git-backed path.

**Files:**
- Modify: `tests/test_finance.py` near `CliWriteBehaviorTests` (`:2665-3232`)
- Modify: `scripts/finance.py` near commands (`:2511-2635`) and parser (`:2932-3000`)

**Step 1: Add failing CLI integration tests**

Create a reusable argument helper in the test class:

```python
def loan_resume_args(self):
    return [
        "loan-resume", "--loan-id", "personal-3200000",
        "--start", "2026-10-08", "--description", "Personal loan payment",
        "--principal", "1907335", "--annual-rate", "2.1", "--payment", "56229",
        "--paid-periods", "25", "--remaining-periods", "35",
        "--currency", "TWD", "--liability", "liabilities:loans:personal",
        "--interest-account", "expenses:interest:loans",
        "--payment-account", "assets:bank:checking", "--rounding", "1",
    ]
```

Add tests that initialize an isolated journal and assert:

1. `--preview` returns 0, prints all 35 transactions plus `Preview only`, leaves journal bytes and Git HEAD unchanged.
2. A normal invocation writes all 35 rows, `hledger check` succeeds, and Git creates exactly one commit with subject `Add remaining loan schedule: Personal loan payment`.
3. Invalid date/decimal/integer CLI text returns 2 with a focused error and does not write.
4. The generated journal has future-dated rows immediately (not a periodic/automated rule), proving no daemon is involved.

**Step 2: Run the CLI tests to verify RED**

Run the new methods explicitly, for example:

```bash
python3 -m unittest \
  tests.test_finance.CliWriteBehaviorTests.test_loan_resume_preview_writes_nothing \
  tests.test_finance.CliWriteBehaviorTests.test_loan_resume_writes_one_checked_git_commit -v
```

Expected: FAIL with argparse `invalid choice: 'loan-resume'`.

**Step 3: Implement `command_loan_resume` and parser arguments**

Parse all decimals inside the command with a helper that converts `InvalidOperation` into `ValueError` rather than leaking a traceback. Parse `--start` with `date.fromisoformat`; use `type=int` for period counts.

Use the lock pattern, keeping read/check/write in one critical section:

```python
def command_loan_resume(args: argparse.Namespace) -> int:
    _ensure_journal(args.journal)
    _require_regular_journal(args.journal)
    with _acquire_journal_lock(args.journal):
        _require_regular_journal(args.journal)
        schedule, periods = build_loan_resume_schedule(
            loan_id=args.loan_id,
            start=date.fromisoformat(args.start),
            description=args.description,
            principal=_parse_decimal_argument(args.principal, "Principal"),
            annual_rate=_parse_decimal_argument(args.annual_rate, "Annual rate"),
            payment=_parse_decimal_argument(args.payment, "Payment"),
            paid_periods=args.paid_periods,
            remaining_periods=args.remaining_periods,
            currency=args.currency,
            liability_account=args.liability,
            interest_account=args.interest_account,
            payment_account=args.payment_account,
            rounding_quantum=_parse_decimal_argument(args.rounding, "Rounding quantum"),
        )
        expected_ids = {row["import_id"] for row in periods}
        existing_ids = _existing_import_ids(_read_active_journal_text(args.journal))
        present = expected_ids & existing_ids
        if present == expected_ids:
            print(f"Loan schedule already present: {len(expected_ids)} payments", file=sys.stderr)
            return 0
        if present:
            raise ValueError(
                "Partial loan schedule import-ID collision; refusing to append any payments"
            )
        if not args.preview:
            _append_validated_and_commit_locked(
                args.journal,
                schedule,
                f"Add remaining loan schedule: {args.description}",
            )
    if args.preview:
        print(schedule, end="")
        print("Preview only; nothing was written.", file=sys.stderr)
    else:
        print(f"Appended {len(periods)} loan payments to {args.journal}")
    return 0
```

Build before locking only if it is fully pure; perform all journal-derived decisions under the lock. It is acceptable and simpler to build inside the lock. Do not use `_print_preview_or_append`, because that helper acquires its own lock and does not implement all/partial ID-set semantics.

Register every option as required, including `--currency` and `--rounding`; the goal explicitly requires inputs rather than implicit defaults. Add only `--preview` as optional.

**Step 4: Verify CLI GREEN**

Run the new CLI tests and then:

```bash
python3 scripts/finance.py loan-resume --help
```

Expected: tests PASS and help lists every required option.

**Step 5: Commit**

```bash
git add scripts/finance.py tests/test_finance.py
git commit -m "feat: add loan-resume command"
```

### Task 5: Enforce idempotency, partial-collision failure, concurrency, and Git rollback

**Objective:** Prove retries and failures cannot duplicate or partially append the schedule.

**Files:**
- Modify: `tests/test_finance.py` in `CliWriteBehaviorTests`
- Modify only if tests expose a defect: `scripts/finance.py` in `command_loan_resume`

**Step 1: Add failing integration tests**

Add four tests:

- **Idempotent rerun:** run the exact command twice; both return 0, journal bytes and Git HEAD after run two equal those after run one, and each expected ID appears once.
- **Partial collision:** seed only `loan.personal-3200000.period.26` as a canonical transaction tag (preferably in an included journal to verify active-source scanning), commit the fixture, invoke the command, expect return 2 with `Partial loan schedule`, and assert root/included journal bytes and HEAD are unchanged.
- **Concurrent rerun:** hold `_acquire_journal_lock`, start two identical subprocesses, release, then assert both return 0, only 35 scheduled IDs exist, and only one schedule commit exists. This mirrors `test_concurrent_duplicate_import_ids_are_written_once`.
- **Git rollback:** initialize the journal, install an executable failing `.git/hooks/pre-commit`, invoke `loan-resume`, expect failure, and assert the journal bytes, index, and HEAD are restored exactly. Remove the hook in test cleanup if subsequent operations are needed.

The partial fixture still needs a balanced hledger transaction; only the canonical transaction-level comment should count:

```hledger
2026-10-08 Existing collision
    ; import-id:loan.personal-3200000.period.26
    expenses:test  1 TWD
    assets:cash  -1 TWD
```

**Step 2: Run to verify RED**

Run:

```bash
python3 -m unittest \
  tests.test_finance.CliWriteBehaviorTests.test_loan_resume_rerun_is_noop \
  tests.test_finance.CliWriteBehaviorTests.test_loan_resume_partial_collision_fails_closed \
  tests.test_finance.CliWriteBehaviorTests.test_concurrent_loan_resume_writes_schedule_once \
  tests.test_finance.CliWriteBehaviorTests.test_loan_resume_restores_journal_when_git_commit_fails -v
```

Expected: at least the concurrency/collision/rollback test FAIL until the entire ID read and append path is correctly lock-scoped and uses `_append_validated_and_commit_locked`.

**Step 3: Make minimal command corrections**

Ensure:

- `_read_active_journal_text` is called only after acquiring the journal lock;
- the set comparison happens before preview or append;
- partial collision does not skip individual rows;
- all-present returns without calling Git or touching the journal;
- the append helper performs candidate `hledger check`, atomic replace, one-path Git add/commit, and rollback. Do not duplicate those mechanisms in the loan command.

**Step 4: Verify GREEN**

Run the four methods again. Expected: all PASS, one schedule commit under the race, no journal mutation on partial collision or Git failure.

**Step 5: Commit**

```bash
git add scripts/finance.py tests/test_finance.py
git commit -m "test: harden resumed loan writes"
```

### Task 6: Define and strictly audit the scheduled-loan semantic form

**Objective:** Make legitimate generated loan payments pass strict audit while rejecting forged tags and non-regular or ambiguous posting shapes.

**Files:**
- Modify: `tests/test_finance.py` in `AuditTests` (`:1663-2459`)
- Modify: `scripts/finance.py:197-216,696-1225`

**Step 1: Add failing semantic audit tests**

Add a helper that writes journal text to a temporary file, runs hledger JSON print, calls `audit_journal`, and optionally runs `hfin audit --strict`. Test:

1. A small genuine `build_loan_resume_schedule` result passes strict audit.
2. Required loan metadata has exactly one value each; reject missing/duplicate/wrong `kind`, `source`, `loan-id`, `loan-period`, `loan-total-periods`, mismatched description suffix, and import ID not equal to `loan.<loan-id>.period.<period>`.
3. Reject wrong account families, conflicting accounts, wrong signs, a missing/extra posting, mixed or unitless commodities, unbalanced values, prices/costs, balance assertions/assignments, and virtual or balanced-virtual postings.
4. Reject a schedule group with duplicate/missing/non-consecutive absolute periods, differing totals/base descriptions/accounts/commodities, non-monthly dates, or unequal non-final total payments.
5. Accept a resumed group beginning after one (`26..60`) and a full group beginning at one (`paid-periods=0`). Do not require nonexistent historical periods `1..25` for a resumed schedule.
6. `source:scheduled-loan` on another kind and `kind:loan-payment` with another source both fail.
7. `ingest-json` still rejects `source:scheduled-loan` and `kind:loan-payment`; these are CLI-reserved.

For real parser-shape attacks, use the established adjustment tests as templates, replacing tags/accounts with scheduled-loan values. Ensure actual hledger JSON shows `ptype`, `pbalanceassertion`, `aprice`, and commodity behavior before asserting audit failure.

**Step 2: Run the new audit tests to verify RED**

Run:

```bash
python3 -m unittest tests.test_finance.AuditTests -v
```

Expected: valid loan rows fail as an unsupported kind/source, while malformed variants are not yet specifically protected.

**Step 3: Separate ingest values from audit-managed values**

Keep current ingest contracts stable:

```python
APPROVED_SOURCE_TAGS = {
    "source:fuzzy-text", "source:pasted-table", "source:messy-csv",
    "source:structured-csv", "source:historical-import",
    "source:receipt-image", "source:invoice-image", "source:pdf",
    "source:voice-transcript",
}
HISTORICAL_SOURCE_TAGS = {"source:structured-csv", "source:historical-import"}
INGEST_KINDS = {
    "expense", "income", "transfer", "refund", "opening-balance", "adjustment"
}
MANAGED_SOURCE_TAGS = APPROVED_SOURCE_TAGS | {"source:scheduled-loan"}
MANAGED_KINDS = INGEST_KINDS | {"loan-payment"}
```

`build_ingest_transactions` continues to validate against `APPROVED_SOURCE_TAGS` and `INGEST_KINDS`. `audit_journal` validates source/kind membership against the managed sets.

**Step 4: Add exact transaction and group validators**

Implement a dedicated validator instead of weakening generic expense/transfer rules:

```python
def _loan_payment_metadata(transaction: dict) -> dict | None:
    tags = _transaction_tag_values(transaction)
    required = ("source", "kind", "loan-id", "loan-period", "loan-total-periods", "import-id")
    if any(len(tags.get(name, [])) != 1 for name in required):
        return None
    # Validate canonical source/kind, canonical loan ID, positive period <= total,
    # exact import ID, and exact description suffix. Return parsed group fields.


def _loan_payment_structure_is_valid(transaction: dict) -> bool:
    # Exactly 3 regular postings, one explicit amount each, no assertion/cost/price,
    # one nonempty commodity, liability principal > 0, expense interest >= 0,
    # asset-or-liability payment < 0, pairwise nonconflicting accounts,
    # and principal + interest == -payment.
```

Have `_kind_structure_is_valid(..., "loan-payment")` delegate to the structural validator. In `audit_journal`, count metadata/source-pairing failures as a critical `scheduled-loan-invalid-metadata` finding and structural failures through the existing `tagged-transaction-invalid-structure` finding.

Group rows by `loan-id` only after each row has valid metadata. Validate:

- periods are unique and exactly `range(min_period, total + 1)`;
- all rows use one total, base description, account triple, and commodity;
- dates equal `add_months(first_date, period - min_period)`;
- all non-final payment credits are equal;
- the group reaches its tagged total period.

Emit one critical `scheduled-loan-invalid-schedule` finding per invalid loan ID. Do not attempt to recompute historical balances or interest without stored source inputs.

**Step 5: Verify audit GREEN**

Run:

```bash
python3 -m unittest tests.test_finance.AuditTests -v
```

Expected: generated schedules pass; every malicious tag/virtual/assignment/price/cost/assertion/group shape produces a critical finding and strict exit status 2.

**Step 6: Commit**

```bash
git add scripts/finance.py tests/test_finance.py
git commit -m "feat: audit scheduled loan payments"
```

### Task 7: Prove strict audit includes future transactions

**Objective:** Lock in unbounded strict-audit behavior so future generated rows cannot evade semantic checks.

**Files:**
- Modify: `tests/test_finance.py` near `CliWriteBehaviorTests.test_audit_checks_included_journal_sources_for_date_only_rules`
- Modify only if needed: `scripts/finance.py:2658-2676`

**Step 1: Add a far-future failing regression test**

Construct a date safely in the future at test runtime (for example `date.today().year + 20`) and write a malformed `kind:loan-payment` transaction with valid-looking managed tags but virtual postings. Run `hfin audit --strict` and assert:

```python
self.assertEqual(result.returncode, 2, result.stderr or result.stdout)
report = json.loads(result.stdout)
self.assertEqual(report["transaction_count"], 1)
self.assertIn(
    "tagged-transaction-invalid-structure",
    {finding["code"] for finding in report["findings"]},
)
```

Also add a positive CLI integration assertion that a valid future schedule contributes all rows to `transaction_count` and passes strict audit.

**Step 2: Run to verify the regression**

Run:

```bash
python3 -m unittest \
  tests.test_finance.CliWriteBehaviorTests.test_strict_audit_rejects_future_malformed_loan_payment \
  tests.test_finance.CliWriteBehaviorTests.test_strict_audit_includes_valid_future_loan_schedule -v
```

Expected before any code change: these may already PASS because unbounded `hledger print` includes future dated transactions. If so, keep the tests as characterization coverage and do not manufacture a production change. If they fail, remove any implicit current-date bound in `command_audit`; do **not** add `--future`, which hledger 1.32.3 rejects.

**Step 3: Run strict audit coverage GREEN**

Repeat the command. Expected: malformed future row fails strict audit; valid future schedule passes and all scheduled rows are counted.

**Step 4: Commit**

```bash
git add tests/test_finance.py scripts/finance.py
git commit -m "test: audit future scheduled loan entries"
```

If no production file changed, omit it from `git add`.

### Task 8: Document command usage, accounting limits, and reserved ingestion metadata

**Objective:** Teach users and agents when and how to use the command without implying bank automation or immutable forecasts.

**Files:**
- Modify: `README.md:51-66,116-151`
- Modify: `SKILL.md:17-38,155-176,197-210,359-373`
- Modify: `references/ACCOUNTING.md:84-99`
- Modify: `references/INGEST.md:92-136`

**Step 1: Update README**

Add `hfin loan-resume` to Quick start and a dedicated “Resume a reducing-balance loan” section containing the exact acceptance example. Explain:

- it is for an already-known fixed-payment reducing-balance loan, including `paid-periods=0`;
- it writes all remaining dated transactions now, and hledger includes them according to report dates without a daemon;
- first 2026-10-08 row is principal `52891` plus interest `3338`; final row is principal `56128` plus interest `98`, total `56226`;
- preview and rerun behavior;
- this automates bookkeeping only and does **not** instruct a bank, initiate withdrawals, or confirm settlement;
- future entries are projections and must be corrected if the bank changes the rate, payment, fees, timing, rounding, or actual charged amount.

**Step 2: Update SKILL.md agent instructions**

Add a loan-resume workflow that requires every input listed in the CLI contract and forbids guessing current balance, rate, remaining term, account, or rounding quantum. Tell the agent to use `--preview` when source terms are uncertain and to explain the bookkeeping-only caveat. State that an all-ID retry is a no-op and a partial collision requires investigation rather than deletion/retry.

Update the audit description to mention scheduled-loan semantic validation and future rows.

**Step 3: Update accounting reference**

Extend the Loan section with the reducing-balance formula, `ROUND_HALF_UP`, non-final/final treatment, absolute numbering, and a representative managed hledger transaction:

```hledger
2026-10-08 Personal loan payment [26/60]
    ; source:scheduled-loan
    ; kind:loan-payment
    ; loan-id:personal-3200000
    ; loan-period:26
    ; loan-total-periods:60
    ; import-id:loan.personal-3200000.period.26
    liabilities:loans:personal              52891 TWD
    expenses:interest:loans                   3338 TWD
    assets:bank:checking                    -56229 TWD
```

Clearly distinguish this principal/interest repayment model from `hfin installment`, which splits a purchase amount rather than amortizing a current loan balance.

**Step 4: Update ingestion reference**

Keep the approved fuzzy-ingestion source list unchanged and add a note that `source:scheduled-loan` / `kind:loan-payment` are reserved for `hfin loan-resume`; agents must not forge them through `ingest-json`. Point to the command for deterministic generation and audit.

**Step 5: Review docs for the two required warnings**

Run:

```bash
rg -n "loan-resume|bookkeeping|withdraw|actual bank|scheduled-loan|loan-payment" \
  README.md SKILL.md references/ACCOUNTING.md references/INGEST.md
```

Expected: all four files discuss their relevant contract, and README/SKILL explicitly say no bank withdrawal occurs and actual bank differences require correction.

**Step 6: Commit**

```bash
git add README.md SKILL.md references/ACCOUNTING.md references/INGEST.md
git commit -m "docs: explain resumed loan schedules"
```

### Task 9: Run end-to-end acceptance and final safety review

**Objective:** Verify the complete command, audit, repository suite, and no-touch constraint before delivery.

**Files:**
- Modify only for defects found: `scripts/finance.py`, `tests/test_finance.py`, `README.md`, `SKILL.md`, `references/ACCOUNTING.md`, `references/INGEST.md`
- Do not modify: `/home/chihmin/finance`

**Step 1: Run focused loan tests**

```bash
python3 -m unittest \
  tests.test_finance.LoanResumeScheduleTests \
  tests.test_finance.AuditTests \
  tests.test_finance.CliWriteBehaviorTests -v
```

Expected: all focused tests PASS.

**Step 2: Run the full test suite**

```bash
python3 -m unittest discover -s tests -v
```

Expected: all tests PASS (baseline before implementation is 172 passing tests).

**Step 3: Run exact acceptance CLI in an isolated repository**

Create a temporary journal with `hfin init`, run the exact command from this plan, then verify:

```bash
hledger -f "$TMP/main.journal" check
python3 scripts/finance.py --journal "$TMP/main.journal" audit --strict
hledger -f "$TMP/main.journal" print 'tag:loan-id=personal-3200000'
git -C "$TMP" log --oneline -3
```

Expected: 35 transactions numbered 26–60; first split `52891 + 3338 = 56229`; final split `56128 + 98 = 56226`; strict audit exit 0; one schedule commit.

**Step 4: Verify preview/no-op and repository boundaries**

- Re-run the same command and confirm journal hash/HEAD do not change.
- Run `--preview` against a fresh isolated temporary journal and confirm hash/HEAD do not change.
- Inspect `git status --short` in this source repository.
- Confirm no command or test used `/home/chihmin/finance`; all integration tests must use `TemporaryDirectory`.
- Do not push.

**Step 5: Commit only if verification required a fix**

```bash
git add scripts/finance.py tests/test_finance.py README.md SKILL.md references/ACCOUNTING.md references/INGEST.md
git commit -m "fix: complete loan resume acceptance"
```

Skip this commit when verification is clean. Finish with a clean working tree and report the commit hashes; do not push.
