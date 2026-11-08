# Pi finance extension — guarded state machines

**Root package entrypoint: `channel.ts` (installed here 2026-10-09).** Both gateways load identical tools/system interfaces; only trusted `PI_FINANCE_HOST=piweb` enables the wallet (`~/finance/main.journal`, unless `PI_FINANCE_JOURNAL` overrides it). Piscord/unknown hosts deny finance, not a fake empty ledger. See [channel configuration and limits](CHANNEL-WALLET.md). The legacy direct `index.ts` installation examples below do not provide this channel wrapper; install the repository root for the guarded channel policy. Two internal layers:

1. **Fast path (`agent.ts` + `core.ts`; legacy direct factory in `index.ts`)** — short single expenses such as `【記帳】1500 午餐`, `午餐 150 元`, `YouBike 15 元`. Host parses amount/date/currency; Qwen only picks a category through `finance_finish`.
2. **Guarded workflows (`managed.ts` + `workflows.ts` + `../scripts/finance_bridge.py`)** — every other finance request. Qwen proposes one structured operation; the host validates it, previews it, and only a **new user message with the exact token** (`確認 <uuid>`) executes a mutation.

| Area | Operations |
|---|---|
| Recording | batch rows, income, transfer, refund, opening balance, foreign currency, cross-currency transfer, explicit payment account |
| Sources | CSV/TSV/JSON/text, PDF text (`pdftotext`), receipt images (image-capable model only) — explicit user paths only, treated as untrusted data |
| Schedules | installments, loan schedules (all terms required, never guessed) |
| Migration | historical import with independent `controls` (count + kind:currency totals), isolated trial ingest and strict audit before preview |
| Read-only | query/register/balance reports, stats, native hledger reports, per-currency dashboard PNGs, check/audit/classify/settings |
| Changes | delete (complete candidate preview; partial confirmation never executes), undo, classification/payment rules, backup to a configured rclone remote |

Measured with local Qwen (`../docs/eval/2026-10-03-pi-qwen-workflows.md`): 17 workflow scenarios × 4 runs **68/68**; 50 Taiwan short expenses **47/50**.

## Guarded workflow state machine

```
idle -> collecting -> preparing -> awaiting_confirmation -> executing -> done
            |  ^          |                 |                    |
            v  |          v                 v                    v
       clarifying   failed (repairable ≤2) cancelled           failed (no retry)
```

- Only `finance_inspect`, `finance_source`, `finance_action`, `finance_question` are active; other tools are blocked. Provider requests carry `tool_choice: "required"` while collecting.
- No model-supplied shell/argv, journal path, output path or confirmation field. Money is decimal strings; safe integers are converted losslessly.
- Host-owned facts: source provenance tag, explicit cross-currency sides, a single explicitly named payment account, canonical debit/credit orientation per kind.
- Mutations bind to a journal/rules snapshot; a stale snapshot, expired token (10 min) or uncertain outcome stops without retry.
- Readonly results return directly; chart paths are created by the host.

## Without the original skill (2026-10-04)

The `hledger-finance` skill is no longer registered (symlink removed from `~/.hermes/skills`, which `~/.pi/agent/skills` and `~/.claude/skills` point to; restore with `ln -s ~/src/hledger-finance ~/.hermes/skills/hledger-finance`). The extension alone owns finance:

- **Ledger guard:** pi's `write`/`edit` into the ledger directory and shell commands touching it, `finance.py`/`hfin`, or `hledger add/import` are blocked in every turn. Reads stay allowed.
- **`finance_handoff`:** the only finance tool visible in ordinary turns. When the model judges a message to be about the user's money (anything keyword detection missed), it hands the latest message to the guarded workflow mid-turn.
- **Detection:** everyday phrasing (`花了多少錢`, `現金還剩多少`, `午餐 120、飲料 50`, `花了 230`) and follow-ups (`剛剛那筆`, `改成`, `刪掉`) within 15 minutes of a finance action.
- **`amend`:** changes (`把早餐改成 80`) remove exactly one matching entry and add the corrected row under one confirmation; a replacement that would not be written refuses the preview, and a failed add undoes the deletion.

## Gateway integration (piweb / piscord)

- **Envelopes:** gateways send `[optional history]\n[Web user: x]` or `[Discord user: x]` followed by the message (voice under `[Voice message transcription: f]`). The extension only inspects the text after the last user header, so history can't trigger finance mode and wrapped `確認 <token>` still matches. Spoken amounts (`四百六十塊`) become digits before parsing.
- **Confirmation across processes:** previews are stored at `~/.local/state/pi-finance/<journal-hash>/<token>.json` (0600, override with `PI_FINANCE_STATE_DIR`). A `確認` in any pi process (print mode for attachments, recycled RPC worker) atomically claims the file, so a token executes at most once; execution is still bound to the preview snapshot, and expiry is 10 minutes.
- **Replies:** previews and receipts are displayed custom messages. pi-agent ≥ this fork emits `input_handled` when an extension consumes a prompt; piweb/piscord finish the turn on it and fall back to displayed custom messages when a turn has no assistant text.
- **Receipts:** one photo/PDF = one row with the final paid total (line items only on explicit request); the import id is the image content digest, so re-sending the same photo is skipped as a duplicate. Expense accounts must come from the category catalog or the user's own words.

## Fast-path state machine

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

此 extension 涵蓋單筆快速記帳與上表 guarded workflows；完整評估見 `docs/eval/2026-10-03-pi-qwen-workflows.md`。尚未驗證真實 piweb／Discord 確認往返、真實收據照片與雲端備份 remote，請先用測試帳本。需要 `pdftotext`（poppler-utils）才能讀 PDF；收據圖片需要支援影像輸入的模型。

需求：已安裝 pi、Python 3.11+、Git、hledger 1.32+。不必另裝 `hfin` 指令；extension 使用同一份 repository 的 `scripts/finance.py`，**不能只複製 `index.ts` 或 `extension/` 資料夾**。

### 1. 建議：一次性載入

```bash
git clone https://github.com/AyaSakura-comp/hledger-finance.git "$HOME/src/hledger-finance"
# 先依原 skill 建立一份可拋棄、已初始化的 Git 帳本。
# 請將下面的路徑改成該帳本，不能指向正式帳本。
PI_FINANCE_JOURNAL=/absolute/path/to/disposable/main.journal \
  pi -e "$HOME/src/hledger-finance/extension/index.ts"
```

輸入 `午餐 150 元` 測試快速路徑；輸入 `【財務】查詢所有帳戶餘額` 測試 guarded workflow，寫入類操作會先顯示預覽與 `確認 <token>`。核對 receipt 與測試帳本的日期、金額及科目；無寫入訊息不等於已記帳。結束此 pi 程序即停止一次性載入。

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

Backend suite **453/453**, extension suite **42/42**, TypeScript NodeNext check passed. Full local-Qwen evidence: `../docs/eval/2026-10-03-pi-qwen-workflows.md` and `-evidence.json` (`workflow-eval.ts`, `full-eval.ts EVAL_FILTER=^C`).

Parser exclusions remain heuristic, **not a general intent classifier**. Remaining unverified: real piweb/Discord confirmation round-trip, real phone receipt photos, cloud rclone remotes, voice input.
