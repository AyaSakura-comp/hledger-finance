# Pi finance FSM + Qwen screening (2026-10-03)

## Verdict and scope

The **short-expense milestone** reduced matched local-agent work from 3 model responses + 2 tool calls to **1 model response + 1 tool call**. Median measured latency: **6.07 s → 1.51 s**, about 4.0× (75% lower) in this small sample. The entire finance skill has NOT been converted. Not globally enabled, not committed, and independent review/UI transport verification are still pending.

## Method

- Direct pi SDK sessions, no subagents or external CLI workers.
- Configured `local-llama/qwen3.6-35b-q4`, thinking `off`, existing local service. No restarts/model replacement/GPU profiling.
- Baseline: full repository `SKILL.md` in system context plus a restricted `finance_cli` adapter, explicit exact command syntax and abstract placeholders. It is deliberately easier/safer than shell-based skill discovery and is **not the historical unconstrained pi UI workflow**.
- FSM: host narrow parser obtains date/currency/amount, runs classify; Qwen selects category; host executes fixed atomic ingest and returns a non-triggering receipt. Only finance_finish enabled and other tools blocked for that request.
- Inputs: `【記帳】1500 午餐`, `YouBike 15 元`, `威力彩 100 元`; each twice, with skill→FSM order in repeat 0 and FSM→skill in repeat 1. Fresh empty initialized Git journals, fresh in-memory sessions. Same model and local-date base instructions.
- Timer surrounds session.prompt: includes host prepare/classify, model generation, ingest/Git and output, excludes SDK/session creation and journal initialization.
- Correctness: exactly one transaction, correct local date, expected account, both positive/negative exact amount in TWD. Does not assess the full finance workflow.
- Host production journal checksum checked after every run; unchanged. Models have no file/shell/network tool and cannot select another journal. Receipts/backend calls and journal content preserved in [evidence JSON](2026-10-03-pi-qwen-fsm-evidence.json), with source hashes. Temp originals: `/tmp/finance-qwen-bench-x5gITo`.

## Final matched run

| Input | Repeat | Skill seconds | FSM seconds | Skill correct | FSM correct |
|---|---:|---:|---:|---|---|
| Lunch 1500 | 0 | 8.27 | 1.57 | yes | yes |
| YouBike 15 | 0 | 5.60 | 1.49 | yes | yes |
| Lottery 100 | 0 | 6.17 | 1.53 | no: amount 1500 | yes |
| Lunch 1500 | 1 | 5.97 | 1.50 | yes | yes |
| YouBike 15 | 1 | 7.11 | 1.49 | yes | yes |
| Lottery 100 | 1 | 5.96 | 1.61 | no: amount 1500 | yes |

| Metric | Skill adapter | FSM |
|---|---:|---:|
| Median seconds | 6.066 | 1.515 |
| Model responses per request | 3 | 1 |
| Agent tool calls per request | 2 | 1 |
| Backend CLI calls per request | 2 | 2 |
| Exact end-to-end correctness | 4/6 | 6/6 |
| Writes per request | 1 | 1 |
| Median generated output tokens | 193.5 | 33 |

The backend work is **not** reduced to one command: classification + atomic ingest remain two CLI operations. The savings come from removing model-driven orchestration, full-skill context and final model reply. The successful baseline cases' median is 6.54 s (no need to count wrong writes as successful speed results).

The two baseline lottery errors had a correct category/date but used **1500 instead of the user's 100** in ingest payload. The cause of number copying has not been proven; source transcripts show the wrong payload. Host fact ownership eliminates this class of mutation in supported FSM inputs.

## Debugging evidence / excluded exploratory runs

Earlier screening is NOT pooled into final numbers:

- `/tmp/finance-qwen-bench-6uyRAv`: terminate-only prototype still made a second model response. Source investigation: default sendMessage enqueues a steering message while streaming. Fix: `triggerTurn:false`; regression test added.
- `/tmp/finance-qwen-bench-xvPtz2`: CLI adapter lacked explicit syntax; Qwen repeatedly sent `['classify','午餐']`, one run aborted at 180 s. Final adapter gives exact syntax. No performance claim from that run.
- `/tmp/finance-qwen-bench-rSqXDv`: baseline tool description contained a concrete amount example. Replaced it with abstract placeholders before final run to avoid tool-example anchoring. Final run still displayed wrong lottery amounts; no root-cause attribution beyond observed payloads.

## Validation

- TDD: parser/FSM tests first failed for missing module; adapter tests first failed for missing implementation. Receipt-channel, abort and unsupported-date regressions were red before their fixes.
- Extension unit/fake-pi + real isolated ingest tests: **11/11 passed**. Post-benchmark added/tested an input concurrency guard so a second idle input cannot replace a PREPARING flow; measured snapshots/hashes predate only that guard/test, not the amount/category or single-turn execution path.
- TypeScript noEmit NodeNext type check: passed.
- Existing backend `python3 -m unittest discover -s tests`: **445 tests passed**.
- Initial attempted pytest unavailable (`No module named pytest`); repository uses unittest, which was then executed.
- Static source scan for shell:true, eval/exec and hardcoded secret assignment: no hits. Python subprocess uses argument arrays.
- No independent reviewer was launched without operator delegation; no production deployment or commit.

## Limitations and remaining work

Six observations on three deliberately supported inputs is a screening result, not confidence intervals/general classification accuracy, cold-start or sustained throughput evidence. Shared local model/cache/load may affect latency; no GPU A/B claim. The evaluation omits real UI transport and does not prove custom receipts display correctly in PiWeb/Piscord.

Unsupported prompts fall through to the ordinary agent, not a system sandbox. Narrow parser intentionally rejects numeric item names, ambiguous amounts/estimates, unsupported dates, images and explicit payment sources. Clarification is terminal and currently requires a fresh complete request. New user retries get new import IDs; no cross-request exactly-once guarantee. In-progress backend cancellation/timeout may have an uncertain outcome and must never be automatically retried.

Whole-skill roadmap: income/refund/transfer/batch/image capture, reports/visualization, installment/loan flows, destructive confirmation-token FSMs, undo/backups/rules, persisted guarded clarification and 50-case regression. See [implementation plan](../plans/2026-10-03-pi-finance-state-machine.md) and [extension README](../../extension/README.md).
