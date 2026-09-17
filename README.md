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
hfin query --report transactions --period this-month
hfin stats --period this-month
hfin visualize --period this-month
hfin check
```

By default, journal data lives in `~/finance/main.journal`. It remains separate from this source repository.

## Automatic expense categorization

Automatic classification uses this precedence:

1. An explicit debit account
2. Literal custom rules from `~/finance/classification-rules.json`
3. Exact or fuzzy historical transaction matches
4. Built-in Taiwan-oriented merchant and keyword rules
5. `expenses:uncategorized` as a reviewable fallback

Generic `add` requires either an explicit debit account or `--debit auto`, preventing omitted accounts on income, refunds, and transfers from being silently recorded as expenses.

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
