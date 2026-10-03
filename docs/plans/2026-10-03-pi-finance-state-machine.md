# Pi finance state-machine implementation plan

Goal: replace open-ended skill/tool navigation with executable workflow guards, starting with short expense capture, and measure local pi + Qwen.

Architecture: input hook recognizes a deliberately narrow completed expense grammar. Host obtains local date and runs existing classify CLI once before model execution. The only active tool accepts category selection or clarification. Host builds ingest-json itself, executes fixed Python path with argument arrays, reports evidence, and terminates the tool batch. Existing atomic journal locking/validation/Git code remains the source of truth. No direct journal editing or service restarts.

Scope: first milestone is short expense capture (including explicit prefix and relative/ISO dates). NOT full skill replacement. Unsupported income, refunds, transfers, batches, images, schedules and reports continue to existing agent unchanged. No global activation until reviewed.

Files:
- extension/core.ts: strict grammar, legal transition table, date resolution, serialized backend workflow and duplicate execution guard.
- extension/index.ts: pi input, before_agent_start, tool_call, agent_settled hooks; one terminating finance tool, restore original tools.
- extension/core.test.ts: TDD parser, transition, fail-closed, idempotency, custom category and real isolated journal checks.
- extension/bench.ts: isolated SDK sessions, local Qwen, matched inputs, skill-only versus extension; save transcript/metrics and checksums.
- extension/README.md: usage, limits, coverage roadmap and benchmark caveats.

Steps:
1. Write failing core tests for missing implementation; run node/tsx test runner.
2. Implement narrow routing, FSM and fixed CLI adapter; prove tests green.
3. Write adapter tests with fake pi API for hook gates/restoration; implement pi integration.
4. Run real ingest integration only on fresh temporary Git journals. Repeat execution must never append twice; backend errors stop.
5. Run actual pi SDK + local configured Qwen (no CLI delegation/subagents) against isolated journals. Same prompt, model/thinking, initial ledger, no network/built-in escape for extension. Baseline limited to sandbox tools. Save failures rather than silently retrying.
6. Compare correctness first, then rounds/tokens/wall latency; small warm sample is screening, not sustained GPU A/B or statistical generalization.

Full-skill roadmap (not implemented in milestone 1):
- record normalized batch: interpretation -> facts -> classify -> atomic ingest -> done; historical import additionally requires strict audit + source reconciliation.
- query/stats/visualize: intent -> validated filters -> fixed CLI -> artifact verification -> done.
- installments/loan: collect required terms -> preview when ambiguous -> validated write -> done.
- delete: search -> display exact candidates/token -> WAIT_USER_CONFIRM -> same-filter token verification -> delete -> done; model cannot mint confirmation.
- undo/backup/config rules: explicit authorized intent -> fixed operation -> validation -> done.

Security acceptance: no amount/date/currency fabrication, no shell interpolation, no automatic retry after uncertain write, no persistent tool lockdown on unrelated conversation, branch/reload never automatically replays mutations, no production ledger access in evaluation.
