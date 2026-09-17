# CSV Import Reference

## Recommended schema

```csv
date,description,amount,currency,category,installments,id
2026-09-01,Laptop,30000,TWD,electronics,3,bank-abc-001
2026-09-02,Lunch,150,TWD,food,1,bank-abc-002
```

The current helper is for expense/purchase rows and accepts one currency per invocation. Amounts must be positive. Negative or zero rows are rejected so refunds, income, transfers, or signed bank statements cannot be silently misposted as expenses. Split those directions first or use native hledger CSV rules. Split mixed-currency input into separate runs, or normalize it before import. Never silently convert currencies.

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
- `--id-column NAME`: stores `import-id:VALUE`; repeats are skipped.
- `--category-column NAME`: creates `CATEGORY_PREFIX:VALUE`; blank values use `--debit`.
- `--debit auto` is the default. Rows without a category are inferred from custom literal rules, journal history, then built-in merchant/keyword rules. Unknown rows use `expenses:uncategorized` and remain easy to review.
- `--encoding`: defaults to `utf-8-sig`, which handles UTF-8 files with a BOM.

## Required workflow

1. Inspect headers and a small redacted sample.
2. Confirm date format is ISO `YYYY-MM-DD`; normalize externally if necessary.
3. Confirm every imported row is a positive expense amount. Separate income, refunds, transfers, and signed cash movements before using this helper.
4. Select the credit/payment account explicitly. Let ordinary expenses auto-classify unless the source already provides trustworthy categories.
5. For a known schema, import directly; the helper validates before writing and creates a Git commit.
6. For an uncertain file, add `--preview`, inspect entries and totals, then run again without `--preview`.
7. Run `hfin check` and compare imported totals to the source. Use `hfin undo` to reverse the latest import if needed.

## Bank-specific and complex CSV

For files with separate debit/credit columns, locale-formatted numbers, multiple currencies, or complex categorization, prefer native hledger CSV rules. Store rules under `~/finance/rules/`, preview with:

```bash
hledger -f ~/finance/main.journal import bank.csv --rules-file ~/finance/rules/bank.rules --dry-run
```

Then import without `--dry-run` after review. Keep original source files under a private archive outside the Git repository if they contain sensitive identifiers.
