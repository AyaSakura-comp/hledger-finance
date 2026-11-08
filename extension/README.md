# Pi finance extension

`finance.ts` gives the model five tools on `~/finance/main.journal` (override with `PI_FINANCE_JOURNAL`):

| Tool | What it does |
|---|---|
| `finance_record` | Writes one or more entries **immediately**. Missing date → today, currency → TWD, expense category → auto-classified, payment account → routed by `payment-account-rules.json`. Any account name is accepted; new ones are flagged in the reply. Kinds: expense, income, refund, transfer (incl. cross-currency), or raw `debit`/`credit`. |
| `finance_query` | transactions / register / balance / income-statement / balance-sheet / cashflow / budget / stats / accounts, with period, account, description, tag and raw hledger filters. |
| `finance_delete` | Deletes the matching entries right away (`dry_run` only lists them). |
| `finance_undo` | Reverts the latest ledger commit; call again to go further back. |
| `finance_cli` | Runs any `finance.py` subcommand (visualize, installment, loan-resume, import-csv, audit, backup, …). |

Nothing is intercepted or blocked: no confirmation tokens, no tool lockdown, no input hijacking, no guard on shell access to the ledger directory. Safety comes from the backend:

- every write is checked with `hledger check` before it replaces the journal, and is one Git commit;
- `finance_undo` reverts the newest commit (record batch, delete, import, or adopted hand edit);
- valid hand edits to the journal are committed as their own "Adopt external journal edits" commit instead of blocking the next write.

The tools are off when `PI_FINANCE_HOST` is listed in `PI_FINANCE_DISABLED_HOSTS` (default `piscord`, because Discord channels can be shared with other people). Set `PI_FINANCE_DISABLED_HOSTS=` to enable it everywhere.

Tests run on a temporary journal: `cd extension && npx tsx --test finance.test.ts`.
