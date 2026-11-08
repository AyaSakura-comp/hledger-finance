# Laya finance decision layer: failed acceptance, not deployed

Date: 2026-10-06, Asia/Taipei. Repository base: `41b71f0`; changes are local/uncommitted. The default installed finance workflow and Qwen service/model/thinking configuration were not changed. The existing `laya.service` was started with its existing CPU configuration for these tests. No production ledger mutations were performed; evaluation harnesses verified the production-journal SHA-256 remained unchanged.

## Result / recommendation

**The requested full-flow migration is NOT complete and this pure-Laya prototype does NOT pass acceptance.** Do not switch production to it. The extension-owned interface is feasible, but this classifier/gate configuration does not provide reliable Taiwan bookkeeping routing or categorization. Fast refusals are not successful bookkeeping.

The reference browser implementation is also not Laya-only: `~/src/laya-browser-intent/router.py` and `REPORT.md` describe exact/anchor matching, multilingual E5 + lexical shortlist of eight, Laya/embedding agreement, then a local Qwen shortlist judge (with reversed-order checks). Its own report says Chinese `none_match` probabilities were uncalibrated and its Laya-only experiment missed most targets. The next useful experiment is an explicitly labeled **hybrid extension-owned dispatcher**, not silently claiming Qwen judge decisions are Laya decisions.

## Implemented prototype

Only `finance_handoff(reason?)` is exposed to the main agent. Amounts, categories and operation parameters cannot be supplied through that tool. The extension calls local Laya for route/category/report decisions and handles supported facts and execution itself. The old decision tools are not registered in this experimental mode.

Implemented experimentally: typed simple expense/income batches, catalog category/payment choice, limited read queries/stats/charts/check/audit/settings, limited delete previews and undo through the existing host confirmation/snapshot machinery, focused ambiguity handling, verified-receipt repeat context. The transport validates complete probabilities/allowed labels and refuses external endpoints/redirects, oversize input/output, timeout and cancellation. Ordinary intervening messages retire pending authorization even without another handoff; uncertain writes invalidate repeat memory.

Unsupported operations stop instead of falling back silently: image/PDF/CSV/historical extraction, advanced transaction phrasing (FX/refund/opening balance), amendments/rule updates/backup/installments/loans. Defaults retain all original operations. The prototype requires an explicit isolated journal and refuses the production journal; it is not enabled in settings.

## 1. Real Laya + real backend, 100 existing Taiwan phrasings

Final artifact root: `/tmp/finance-laya-eval-A2Gr77`. Every case used a new initialized disposable Git journal and real hledger backend; the timer excludes initialization. Median/max include failed refusals as well as writes. Threshold: top probability >= 0.55 and margin >= 0.15. CPU service uses its pre-existing eight-thread configuration.

| Measure | Result |
|---|---:|
| Count/description/kind/date/amount/currency and final postings correct | **8/100** |
| Correct AND completed within ten seconds | **8/100** |
| Any return within ten seconds (NOT success) | 100/100 |
| All-case extension median / max | 0.185 s / 0.595 s |
| State `uncertain` / `released` | 75 / 11 |
| State `done` | 14, of which six incorrectly performed a read instead of a write |
| Incorrect-amount/date/currency/row-count writes found | 0; failed cases did not write |

**The 100-case check does not prove categories are correct.** That is a separate test below. The eight successful-facts cases include telephone subscription, taxi, hotpot, Japan pharmacy purchase, LINE Pay lunch, JKO drink, red envelope and Shopee purchase. Familiar cases such as breakfast/lunch/coffee often stopped on category or route uncertainty.

Summary and all case outcomes: [direct evidence](2026-10-06-laya-direct-evidence.json). Raw probabilities, questions, backend payloads, receipts and each actual journal are retained in the artifact tree, including failures. Earlier complete run `/tmp/finance-laya-eval-PEszmT` also remains intact (same 8/100 result).

## 2. Separate 50-category corpus

Used the existing `2026-09-26-pi-qwen-taiwan-50-cases.tsv` accepted-account sets and a fresh catalog, independent of intent routing, so route failures cannot hide poor categories.

- Correct classification accepted by the confidence gate: **1/50**.
- Top-1 category correct, ignoring confidence: **8/50**.
- The gate accepted two category choices: tutoring tuition → education (correct), Poya face wash → shopping (wrong under the corpus's personal-care/beauty categories).
- No persisted custom binding was involved in those fifty; binding behavior is tested independently and bypasses nonbinding Laya selection.

This is a direct 28-category choice, **not** the browser's embedding-recalled eight-candidate hybrid. Its poor result cannot establish that a proper hybrid or another tuned prompt/model is inherently incapable. The current proposal nevertheless plainly fails deployment acceptance. [Evidence](2026-10-06-laya-direct-evidence.json)

## 3. Intent/routing corpus

42 diagnostic messages: 38 four-way route cases (record/query/change/none) plus four clarification/repeat cases. Clarification cases are not represented by a fifth route enum; they exercise a gap in a pure four-way classifier and must not be conflated with four-way classifier accuracy.

- Four-way correct top-1: **19/38**; accepted correct decisions: **12/38**.
- Gate accepted 21 four-way decisions: 12 right, nine wrong. It abstained on the other 17.
- Among 19 nonfinance messages, **five** were confidently routed as finance: `午餐120不要記帳`, `請寫函式解析午餐120元`, `我要產生房租15000的測試資料`, `請重啟API server`, `下個月打算買耳機2990元`.
- These are raw classifier false positives, **not evidence of five ledger writes**. Host guards and unit tests separately ensure negation, hypotheticals, translations and known software/test examples cannot auto-write even after a confident false classification.
- Bare “多一筆” never duplicates an entry; the extension asks whether it means add another or complain of a duplicate. Actual successful backend receipts, not quoted chat, are required for explicit repeats.

[Evidence](2026-10-06-laya-direct-evidence.json). Initial long/bilingual and short-Chinese probes are preserved separately: [initial probe](2026-10-06-laya-initial-probe.jsonl), [Chinese probe](2026-10-06-laya-zh-probe.jsonl). None is treated as acceptance or a held-out generalization test.

## 4. Real Pi + configured local Qwen, paired six-case screening

Final artifact root: `/tmp/finance-laya-pi-0m1scg`. Same prompts, alternating agent/Laya order; fresh books and in-memory sessions, same configured `local-llama/qwen3.6-35b-q4`. Minimal thinking belongs only to this in-memory SDK harness; no persistent settings or Qwen service changes. Main-agent inference time IS included; UI/gateway transport/startup is not.

| Measure | Existing extension | Pure-Laya handoff prototype |
|---|---:|---:|
| Correct actual outcomes | **5/6** | **1/6** |
| Median elapsed (includes failures) | 1.504 s | 7.879 s |
| Maximum elapsed | 47.399 s (timeout) | 11.720 s |
| Expense/income writes | 4/4 correct | 0/4 written |
| Cash balance request | Correct 850 TWD from ledger | Wrong routing; no answer |
| Ambiguous “多一筆” | Timed out after real tools; no writes | Correct clarification; no writes |

All Laya cases made exactly one real `finance_handoff` call. This proves the interface exists, **not** its correctness or speed target. The main model spent time reasoning before forwarding; 0.185-second direct extension latency must not be advertised as end-to-end bookkeeping latency. One small screening run is not a statistically stable speed comparison; concurrent backend regression testing also ran during this screening.

[Pi summary](2026-10-06-laya-pi-evidence.json). Raw messages/tool results/journals remain per case. The original interrupted attempt `/tmp/finance-laya-pi-E0A8C0` is preserved: first eight paired rows completed, then the query seed fixture failed because it lacked the required approved source tag. The fixture was corrected and the whole screening rerun in a NEW artifact root; failures were not overwritten.

## Verification / safety limits

- Extension unit/regression tests: **89/89**.
- Python backend tests: **458/458**.
- TypeScript type check and `git diff --check`: pass.
- Tests were written first and failed before transport/engine/extension/isolation/safety fixes. Red logs retained under `/tmp/finance-laya-*-red.log`.
- Confidence thresholds are conservative engineering gates, **not calibrated financial risk probabilities**.
- No independent reviewer was invoked (operator did not request delegation). No commit/push or production activation was performed. Unit-test success is not model acceptance.

Code: `extension/laya.ts`, `laya-engine.ts`, `laya-extension.ts`, their tests and evaluation harnesses. Usage/limitations: [LAYA.md](../../extension/LAYA.md). Plan: [plan](../plans/2026-10-06-laya-finance.md).

## Next step

Keep the old production path. If a hybrid design is approved, keep the same single handoff interface, add bounded candidate recall plus an extension-internal judge using the existing model, and separately implement validated complex fact extraction. Preserve snapshot/authorization logic and source isolation. Evaluate Laya-only versus hybrid versus original, including all 100 facts cases, all 50 category cases, negative routing and all seventeen complex workflows. Production activation requires actual correct final ledger outcomes and end-to-end timing, not safe abstention or verbal receipts.
