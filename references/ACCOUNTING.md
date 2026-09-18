# Accounting Patterns

## Expense

```hledger
2026-09-16 Lunch
    expenses:food                 150 TWD
    assets:cash                  -150 TWD
```

## Category inference

Automatic categorization applies only to ordinary expenses. Priority is explicit account, custom literal rule, matching journal history, built-in merchant/keyword rules, then agent semantic judgment. Income, transfers, refunds, receivables, loans, assets, and liabilities must use their economic posting direction instead of being forced into an expense category.

Prefer stable, reusable hierarchies and existing accounts. For example, use `expenses:food:groceries` consistently rather than creating both `expenses:groceries` and `expenses:food:supermarket` for the same meaning.

## Income

```hledger
2026-09-05 Salary
    assets:bank                 50000 TWD
    income:salary             -50000 TWD
```

## Transfer

```hledger
2026-09-06 ATM withdrawal
    assets:cash                  3000 TWD
    assets:bank                 -3000 TWD
```

## Credit-card purchase recognized monthly

Use `hfin installment` with the expense as debit and card liability as credit. This is simple personal-finance reporting: each installment becomes expense in its month.

```hledger
2026-10-15 Phone [1/12]
    expenses:electronics         3000 TWD
    liabilities:credit-card     -3000 TWD
```

## Purchase recognized upfront

For accrual-style reporting, recognize the full expense and installment liability on purchase date:

```hledger
2026-10-01 Phone
    expenses:electronics        36000 TWD
    liabilities:phone-plan     -36000 TWD
```

Then each payment reduces the liability; interest/fees are separate expenses:

```hledger
2026-10-15 Phone payment [1/12]
    liabilities:phone-plan       3000 TWD
    expenses:interest              30 TWD
    assets:bank                  -3030 TWD
```

Ask which recognition model the user wants when it affects period expenses. Do not record both monthly expense recognition and upfront expense recognition.

## Refund

Reverse the original economic direction:

```hledger
2026-09-20 Lunch refund
    assets:cash                   150 TWD
    expenses:food                -150 TWD
```

## Receivable

```hledger
2026-09-01 Friend owes me
    assets:receivable:friend     1000 TWD
    assets:cash                 -1000 TWD

2026-09-10 Friend repaid
    assets:bank                  1000 TWD
    assets:receivable:friend    -1000 TWD
```

## Loan

```hledger
2026-01-01 Loan received
    assets:bank                100000 TWD
    liabilities:loan         -100000 TWD
```

For a known fixed-payment reducing-balance loan, `hfin loan-resume` calculates monthly interest as `(opening principal × annual rate / 100 / 12)`, rounded with decimal `ROUND_HALF_UP` to the lender's supplied power-of-ten quantum. Every non-final row credits the fixed payment and assigns the remainder to principal. The final row debits the exact remaining principal plus rounded interest, so its payment may differ and the balance reaches exactly zero.

`paid-periods` is the number already completed. Descriptions and stable IDs use absolute numbering, so 25 paid plus 35 remaining produces periods 26 through 60:

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

The command rejects negative amortization and a fixed payment that would pay off before the requested final period. It writes projections, not evidence that the bank charged or settled them; reconcile and correct future rows when actual terms differ.

This differs from `hfin installment`: installments split a purchase amount across months, while loan amortization reduces a current liability and separately recognizes interest.

## Tags and projects

```hledger
2026-09-16 Museum
    ; project:italy
    ; person:me
    expenses:travel              500 TWD
    liabilities:credit-card     -500 TWD
```

Query with `tag:project=italy`.
