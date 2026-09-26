# hledger-finance

A complete Pi/Hermes agent skill and deterministic `hfin` CLI for personal finance workflows backed by [hledger](https://hledger.org/).

The repository contains the whole installable skill: CLI source, agent instructions, accounting references, visualization QA, and tests. Personal journals are **not** stored in this repository.

## Repository layout

```text
hledger-finance/
├── SKILL.md
├── install.sh
├── scripts/
│   ├── finance.py
│   └── visualization_qa.py
├── references/
│   ├── ACCOUNTING.md
│   ├── CSV.md
│   ├── INGEST.md
│   ├── HISTORICAL_IMPORT.md
│   └── QUERY.md
└── tests/
    └── test_finance.py
```

## Requirements

- Python 3.11+
- hledger 1.32+
- Git
- Matplotlib and Pillow for dashboard rendering
- rclone (optional, for Google Drive backup)

On Debian/Ubuntu:

```bash
sudo apt install hledger git python3-matplotlib python3-pil rclone
```

## Install the skill and CLI

Clone the project under `~/src`, then run the installer:

```bash
mkdir -p ~/src
git clone git@github.com:AyaSakura-comp/hledger-finance.git ~/src/hledger-finance
cd ~/src/hledger-finance
./install.sh
```

The installer creates these links:

```text
~/.hermes/skills/hledger-finance -> ~/src/hledger-finance
~/.local/bin/hfin                -> ~/src/hledger-finance/scripts/finance.py
```

If a previous copied installation already exists, review it first and then replace it explicitly:

```bash
./install.sh --force
```

Use `--skills-dir PATH` or `--bin-dir PATH` to override the installation locations.

Make sure `~/.local/bin` is in `PATH`:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

## Quick start

```bash
hfin init
hfin add --date 2026-09-17 --description "全聯買菜" --amount 680 \
  --currency TWD --debit auto --credit assets:cash
hfin classify --description "Uber Eats"
hfin ingest-json /tmp/agent-normalized-transactions.json
hfin loan-resume --help
hfin query --report transactions --period this-month
hfin stats --period this-month
hfin visualize --period this-month
hfin check
hfin audit --strict
```

By default, journal data lives in `~/finance/main.journal`. It remains separate from this source repository.

## Fuzzy and multimodal ingestion

The agent skill accepts natural phrases such as `晚餐1200元`, pasted transaction lists, malformed/headerless tables, and attached receipt or invoice images. The agent extracts financial facts and normalizes them; the CLI validates and writes them atomically:

```bash
hfin ingest-json /tmp/agent-normalized-transactions.json
```

A whole batch creates one Git commit and can be reversed with one `hfin undo`. Missing date, TWD currency, or payment source may use audited defaults; unreadable amounts and materially ambiguous transaction directions are never invented. See [`references/INGEST.md`](references/INGEST.md) for the JSON contract and receipt/table extraction policy.

Optional currency routing for future fuzzy capture lives beside the journal in `payment-account-rules.json`:

```json
{
  "currency_accounts": {
    "TWD": "assets:cash:wallet",
    "JPY": "assets:bank:jpy-account",
    "USD": "assets:bank:usd-account"
  },
  "require_match": true
}
```

For an ordinary `ingest-json` expense with no `credit` in either the record or defaults, the already-resolved currency selects the payment account and the CLI adds `inferred:payment-account` plus `inferred:currency-routing`. Explicit accounts always win. With `require_match: false`, an unmatched currency keeps the compatible `assets:cash` fallback; with `true`, it requires explicit `credit`. The exact schema is mandatory when the file exists: unknown fields, invalid currencies/accounts, non-string routes, and routes outside `assets:*` or `liabilities:*` are rejected. Routes are literal map keys, never regexes or executable rules.

## Historical CSV and app migration

Do not translate an existing export by copying signed amounts into raw hledger postings. Profile the source convention, normalize positive magnitudes with explicit semantic kinds, require stable IDs, keep real merchant descriptions, and represent opening balances as dated balanced transactions—not `=` automated posting rules. Currency routing is only for future fuzzy expense capture: historical and structured imports must preserve each source payment account with explicit `debit` and `credit` and are never auto-routed. Run the complete migration against an isolated journal, reconcile control totals by kind/currency, then require both `hfin check` and `hfin audit --strict` before production import. See [`references/HISTORICAL_IMPORT.md`](references/HISTORICAL_IMPORT.md).

## Resume a reducing-balance loan

Use `loan-resume` for an already-known fixed-payment reducing-balance loan. `--paid-periods 0` is valid for a new full schedule; a resumed schedule uses the current outstanding principal and absolute period counts:

```bash
hfin loan-resume \
  --loan-id personal-3200000 \
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
  --rounding 1
```

This writes all 35 future-dated transactions immediately—there is no daemon. Loan schedules are capped at 1200 total monthly periods. Principal, payment, and rate are bounded before arithmetic to an adjusted decimal exponent of ±18, 38 significant digits, and 18 fractional places; this covers ordinary mortgages and commodities with 18-decimal precision without allowing attacker-sized Decimal expansion. The power-of-ten rounding quantum also supports at most 18 decimal places. Reports include future entries according to their dates. The first row on 2026-10-08 is principal `52891` plus interest `3338`, totaling `56229`; the final row is principal `56128` plus interest `98`, totaling `56226` and clearing the balance.

Add `--preview` to inspect the complete schedule without changing the journal or Git. Retrying is a clean no-op only when every existing payment exactly matches the requested dates, description, metadata, accounts, commodity, principal, interest, and total; any mismatch or partial import-ID collision fails closed for investigation. The command automates bookkeeping only: it does **not** instruct a bank, initiate a withdrawal, or confirm actual settlement. Future entries are projections. Correct them if the bank changes the rate, payment, fees, timing, rounding, or actual charged amount.

## Automatic expense categorization

Automatic classification uses this precedence:

1. An explicit debit account
2. Literal custom rules from `~/finance/classification-rules.json` (longest match; equal-specificity conflicts abstain)
3. Clear product/purpose rules, protected from contradictory historical categories
4. Historical consensus: >=3 supporting purchases and >=80% agreement among the strongest matches; compatible fine-grained categories can refine purpose rules
5. Merchant-only defaults, then reviewable `expenses:uncategorized` when evidence is absent or conflicting

See [the category standard](references/CLASSIFICATION.md) for 25 ordinary category groups, mixed purchases, non-expense boundaries and permanent preferences. A travel meal remains food; an online purchase of cat litter is pets. Scores are heuristic, not calibrated probabilities. Single-row corrections are not automatically persisted as rules, and no historical journal is automatically rewritten.

Run the fixed 200-case classification matrix (JSON and Markdown reports; no production journal access):

```bash
python3 tests/test_classification_scenarios.py --report /tmp/hfin-classification-200
```

Generic `add` requires either an explicit debit account or `--debit auto`, preventing omitted accounts on income, refunds, and transfers from being silently recorded as expenses.

## Signed adaptive visualization

Statistics and dashboards use a cash-flow projection: normal income credits are positive, normal expense debits are negative, contra postings use the opposite sign, and net is their sum. Raw hledger postings keep their accounting signs.

Expense-only and income-only dashboards hide the unrelated flow type. A single matched transaction uses a full pie composition; larger sets use a donut chart.

## Development

Run the complete test suite:

```bash
python3 -m unittest discover -s tests -v
```

Run the 50-case isolated visualization QA suite:

```bash
python3 scripts/visualization_qa.py \
  --output-dir /tmp/hledger-finance-visualization-qa
```

The QA suite never writes to the production journal.
