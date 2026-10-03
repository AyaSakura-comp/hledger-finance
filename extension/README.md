# Pi finance FSM — short-expense milestone

**Experimental; not globally installed; not a complete replacement of SKILL.md.**

Supported: `【記帳】1500 午餐`, `午餐 150 元`, `YouBike 15 元`, `威力彩 100 元`; optional leading valid ISO date, 今天/昨天/前天. NFKC full-width amounts supported. Single positive amount with ≤2 decimals; missing currency defaults TWD; missing date is obtained by the host in Asia/Taipei. Receipt exposes defaults; backend owns payment routing. Description is restricted to letters/marks/spaces.

Unsupported inputs (questions, estimates, examples, transfers, income, installments, foreign currency, explicit payment source, multiple expenses, numeric item names and images) **fall through to the existing agent**. This extension is not a sandbox for unsupported conversations. Be especially careful with reports/deletion/import until their FSMs are implemented. `/finance` with a space is a parser prefix, not a registered interactive slash command; prefer `【記帳】`.

## State machine

```
IDLE -> PREPARING -> READY -> WRITING -> DONE
                    |         |
                    v         v
                 CLARIFYING  FAILED
```

Preparation failure goes to FAILED. During supported requests only `finance_finish` is active, and a tool-call hook blocks other tools. Host calls `classify` once; Qwen sees all category candidates and binding evidence, then chooses one category or asks one question. The model cannot set amounts/dates, journal paths, shell commands or payment sources. Host uses argument-array Python subprocesses and existing atomic `ingest-json`/Git validation. Tool returns `terminate: true` and sends a displayed receipt with **`triggerTurn: false`**; otherwise pi queues another model turn despite termination.

On agent settlement/shutdown/start, original tools are restored. Streaming/steering inputs are not intercepted. Clarification does not automatically resume a write: user must submit a new complete supported request. Model-only replies receive a host no-write notice. Already-aborted tools do not write; cancellation during an in-progress backend transaction may still complete atomically—check the receipt/journal first.

Each input creates an import ID, shared by all executions within that flow. Second/concurrent execution cannot append again; the backend also skips an identical import ID. Reload/fork never auto-replays in-flight state. State entries are audit hints, not a resumable transaction log. **Re-submitting a new user input produces a new ID; this is not cross-request exactly-once delivery.** Timeout/unknown receipts fail closed with no automatic retry.

## 在 Pi 安裝（實驗版，先用測試帳本）

此 extension **只處理單筆簡短支出**，不是整套記帳 skill 的替代品。完整評估未通過正式部署驗收；自訂 binding 的未使用子帳戶有已知 `Invalid category` 缺陷。報表、刪除、匯入等不支援的輸入會回到原 agent，並不受此 FSM 保護。請勿直接對正式帳本啟用。

需求：已安裝 pi、Python 3.11+、Git、hledger 1.32+。不必另裝 `hfin` 指令；extension 使用同一份 repository 的 `scripts/finance.py`，**不能只複製 `index.ts` 或 `extension/` 資料夾**。

### 1. 建議：一次性載入

```bash
git clone https://github.com/AyaSakura-comp/hledger-finance.git "$HOME/src/hledger-finance"
# 先依原 skill 建立一份可拋棄、已初始化的 Git 帳本。
# 請將下面的路徑改成該帳本，不能指向正式帳本。
PI_FINANCE_JOURNAL=/absolute/path/to/disposable/main.journal \
  pi -e "$HOME/src/hledger-finance/extension/index.ts"
```

輸入 `午餐 150 元` 測試。核對 receipt 與測試帳本的日期、金額及科目；無寫入訊息不等於已記帳。結束此 pi 程序即停止一次性載入。

### 2. 可選：Pi package 安裝

Repository 根目錄的 `package.json` 宣告 `pi.extensions`，因此可用 Git package 安裝，或用已 clone 的本機路徑。二擇一，避免重複載入：

```bash
# 限目前測試專案（在專案目錄執行；需信任該專案）
pi install -l git:github.com/AyaSakura-comp/hledger-finance
# 或：pi install -l "$HOME/src/hledger-finance"

# 每次啟動仍必須指定測試帳本；省略會預設使用 ~/finance/main.journal。
PI_FINANCE_JOURNAL=/absolute/path/to/disposable/main.journal pi
```

在已開啟的 pi 中可用 `/reload` 重新載入。不要同時 package 安裝又使用 `-e`，也不要額外建立 auto-discovery symlink。若經後續審查決定全域安裝，移除 `-l` 即可；本次沒有替你啟用。

```bash
# 移除專案安裝（source 必須與安裝時相同）
pi remove -l git:github.com/AyaSakura-comp/hledger-finance
# 本機安裝則用：pi remove -l "$HOME/src/hledger-finance"
```

模型可在 pi `/model` 選擇已設定的本機 Qwen；此套件不新增模型設定、不啟動／重啟模型服務。保留原 skill 作為未涵蓋操作的說明來源，但不要因安裝本 extension 就信任其刪除授權或未驗證的 fallback。

## Manual loading (after review)

Dependencies: pi package `@earendil-works/pi-coding-agent`, `typebox`, Node with TypeScript loader, Python and hledger. Do not enable this against production until an independent review and UI receipt verification.

```bash
# Set to a disposable, initialized Git journal for initial testing.
PI_FINANCE_JOURNAL=/absolute/path/to/disposable/main.journal \
  pi -e /home/chihmin/src/hledger-finance/extension/index.ts
```

Default journal (if no environment override) is `~/finance/main.journal`. **Loading it without an override permits supported expenses to mutate your real ledger.** This change does not install itself or alter global pi settings. The backend script is resolved relative to this extension, eliminating `hfin` PATH discovery.

Local tests in this workspace use an ignored `node_modules` symlink to `/home/chihmin/src/pi-prefill-api/node_modules` (not a portable installation). Install peer/dev dependencies in other environments.

```bash
cd /home/chihmin/src/hledger-finance/extension
npm test # core, extension hooks, and evaluation-support tests
./node_modules/.bin/tsc --noEmit --module nodenext --moduleResolution nodenext \
  --target es2022 --allowImportingTsExtensions --skipLibCheck *.ts
./node_modules/.bin/tsx bench.ts
```

`bench.ts` runs **direct local SDK sessions, not subagents**. Fresh temporary Git journals for every run, no model-accessible shell/read/write/network tools, fixed backend paths. Formal journal is only read by the host for checksum verification. It compares full-skill instructions + a constrained CLI adapter against the FSM; not the prior unconstrained assistant session. Explicit command syntax/placeholders are supplied to baseline to avoid command-discovery artifacts. Baseline amounts remain model-controlled. Three cases × two interleaved orderings is a screening sample, not a production or sustained GPU benchmark. `BENCH_PROVIDER`, `BENCH_MODEL`, `BENCH_REPEATS` configure local model and repetitions; default is existing local-llama/qwen3.6-35b-q4, thinking off. No service restarts.

## Publication verification

Before publishing this experimental milestone: backend suite **445/445**, extension/core/evaluation-support suite **17/17**, and TypeScript NodeNext check passed. `known-defects.test.ts` remains a separate **expected failing** custom-binding regression, not part of the green suite. The raw Qwen screening/full-evaluation evidence was retained unchanged; it predates the pre-commit English-intent and CLI-option hardening and was not rerun as a full model matrix.

Pre-commit review prompted two fixes: reject English example/budget/hypothetical and selected non-expense keywords; reject noncanonical helper options so argparse abbreviations cannot bypass evaluation output/backup checks. Parser exclusions remain heuristic, **not a general intent classifier**. A new unknown wording may still require explicit user caution; unsupported fallback remains outside this FSM's protection.

## Remaining whole-skill conversion

See `../docs/plans/2026-10-03-pi-finance-state-machine.md`: income/refund/transfer/batch, image ingestion, queries/stats/visualizations, loan/installments, destructive confirmation tokens, undo/backups and custom rules still use the original skill. Persistent guarded clarification, transport replay IDs, external UI receipt delivery and broader Taiwan regression coverage are also pending.
