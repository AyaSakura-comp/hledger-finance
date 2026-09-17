# CSV Import Reference

## Recommended schema

```csv
date,description,amount,currency,category,installments,id
2026-09-01,Laptop,30000,TWD,electronics,3,bank-abc-001
2026-09-02,Lunch,150,TWD,food,1,bank-abc-002
```

The current helper is only for known-schema expense/purchase rows and accepts one currency per invocation. Amounts must be positive magnitudes. Negative or zero rows are rejected so refunds, income, transfers, or signed bank statements cannot be silently misposted as expenses. For signed statements, mixed kinds/currencies, transfers, adjustments, opening balances, or app migrations, normalize the rows to `hfin ingest-json` and follow [HISTORICAL_IMPORT.md](HISTORICAL_IMPORT.md). Never copy source signs into postings or silently convert currencies.

## Column mapping

```bash
hfin import-csv FILE \
  --date-column transaction_date \
  --description-column merchant \
  --amount-column value \
  --id-column transaction_id \
  --installment-column terms \
  --category-column category \
  --category-prefix expenses \
  --debit auto \
  --credit liabilities:credit-card \
  --currency TWD
```

- `--installments N`: default count for every row.
- `--installment-column NAME`: row-specific count; blank cells fall back to `--installments`.
- `--id-column NAME`: always required; every row must contain a non-empty canonical ID. Stores `import-id:VALUE`; repeats are skipped. There is no missing-ID mode.
- `--category-column NAME`: creates `CATEGORY_PREFIX:VALUE`; blank values use `--debit`.
- `--debit auto` is the default. Rows without a category are inferred from custom literal rules, journal history, then built-in merchant/keyword rules. Unknown rows use `expenses:uncategorized` and remain easy to review. This is a narrow exception for this positive-expense-only command: structured or historical records sent through `ingest-json` must provide explicit debit and credit accounts.
- `--encoding`: defaults to `utf-8-sig`, which handles UTF-8 files with a BOM.

## Required workflow

1. Inspect headers and a small redacted sample.
2. Confirm date format is ISO `YYYY-MM-DD`; normalize externally if necessary.
3. Confirm every imported row is a positive expense amount. Separate income, refunds, transfers, and signed cash movements before using this helper.
4. Select the credit/payment account explicitly. Let ordinary expenses auto-classify unless the source already provides trustworthy categories.
5. For a known schema, import directly; the helper validates before writing and creates a Git commit.
6. For an uncertain file, add `--preview`, inspect entries and totals, then run again without `--preview`.
7. Run `hfin check` and `hfin audit --strict`, then compare row counts and expense totals to the source separately for each currency. Use `hfin undo` to reverse the latest import if needed.

The helper adds `source:structured-csv`, `kind:expense`, and `import-id:*` tags. Auto-category is safe here because `import-csv` accepts only positive expenses, validates the payment account, and resolves the expense account before rendering; it does not relax the explicit-account rule for historical `ingest-json`. Preserve the actual merchant/payee in the description rather than using a generic source/type label.

## Bank-specific and complex CSV

For files with separate debit/credit columns, locale-formatted numbers, multiple currencies, balances, or mixed transaction kinds, normalize to the strict JSON contract instead of writing to the production journal with native `hledger import`. This preserves explicit semantic `kind`, stable IDs, source tags, atomic validation, and one-step Git undo.

If native CSV rules are needed for investigation, use them only against an isolated temporary journal and treat their output as an intermediate artifact. Verify every row type, run `hfin audit --strict`, reconcile control totals, and then convert the result to the normalized ingestion workflow. Keep original source files in a private archive outside the Git repository if they contain sensitive identifiers.
