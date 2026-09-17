# Flexible Query Reference

## Helper reports

| User intent | `hfin query --report` |
|---|---|
| list matching entries | `transactions` |
| running account activity | `register` |
| balances by account | `balance` |
| income and expenses | `income-statement` |
| assets, liabilities, equity | `balance-sheet` |
| cash movement | `cashflow` |
| actual versus budget | `budget` |
| journal metadata | `stats` |

## Time

`--period START..END` treats both written dates as inclusive. Internally the end passed to hledger is the following day because hledger end dates are exclusive.

Use `--begin YYYY-MM-DD --end YYYY-MM-DD` when dates are supplied separately; helper `--end` is also inclusive.

## Filters

```bash
--account expenses:food
--description 'costco|carrefour'
--tag project:italy
--where 'amt:>1000'
--where 'cur:TWD'
--where 'status:pending'
```

Multiple `--account` arguments are combined as one regular-expression OR. Other terms are combined by hledger query semantics (normally AND).

Common raw terms:

- `acct:PATTERN`
- `desc:PATTERN`
- `date:PERIOD`
- `tag:NAME=VALUE`
- `amt:>NUMBER`, `amt:<NUMBER`
- `cur:TWD`
- `status:pending`, `status:cleared`, `status:unmarked`
- `not:QUERY`
- `depth:N`

## Aggregation and export

```bash
--interval daily|weekly|monthly|quarterly|yearly
--format txt|csv|json|html
```

Format support is report-specific in the installed hledger 1.32:

| Report | Supported helper formats |
|---|---|
| transactions, register, balance, budget | txt, csv, json |
| income-statement, balance-sheet, cashflow | txt, csv, json, html |
| stats | default text only; omit `--format` |

The helper rejects unsupported combinations before invoking hledger.

## Direct hledger examples

```bash
# Food spending by month
hledger -f ~/finance/main.journal balance expenses:food --monthly --begin 2026-01-01 --end 2027-01-01

# Transactions over TWD 1,000
hledger -f ~/finance/main.journal print 'amt:>1000' 'cur:TWD'

# Market-valued balance sheet
hledger -f ~/finance/main.journal balancesheet --market

# Investment return
hledger -f ~/finance/main.journal roi --begin 2026-01-01 --end 2027-01-01
```

When a user asks a natural-language query, translate it into explicit dates, report type, and query terms. Echo the interpretation when ambiguity could change the answer.
