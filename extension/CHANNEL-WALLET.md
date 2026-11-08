# Channel wallet (stable model interface)

Deployed package entrypoint: `extension/channel.ts`. It wraps the existing guarded agent FSM in `agent.ts`, not the experimental Laya path. `agent.ts` preserves the committed legacy factory independently so this package can be checked out without local prototype dependencies. Capture `PI_FINANCE_HOST` once per extension initialization:

- exact `piweb`: real existing journal (`PI_FINANCE_JOURNAL`, otherwise `~/finance/main.journal`).
- `piscord`, missing or any other value: no wallet. No backend, account/rule/source reads or pending-store access through finance hooks/tools. Denials do not claim a zero balance or successful write.

Set the host in the service launcher, never in user text, tool arguments or system prompt. User systemd drop-ins: `piweb-worker.service.d/60-finance-wallet.conf`, `pi-discord-gateway.service.d/60-finance-wallet.conf`. Both use the identical globally installed package. Existing RPC workers must restart/reload for package discovery; service environment changes require service restart.

## Cache invariants

All seven finance tools remain registered/active, ordered identically. Contextual operation registrations replace execution closures only, not the initial public description/schema. FSM tool-call gates enforce allowed operations without changing active tools. Workflow instructions/context use ordinary hidden messages after the system prefix. No finance hook changes system prompt, provider thinking/template options or tool_choice. Existing dynamic behavior remains available only to direct callers of the legacy factory in `index.ts`; it is not the deployed package entrypoint.

System/tools equality does not imply identical chat histories or a measured 100% KV hit-rate. Other extensions, cwd context, model templates and gateway settings can independently change the prompt. Installing/updating the extension may cause a normal one-time KV golden refresh.

## Authorization and limits

Session IDs for financial authorization are host-prefixed. Persisted token claims validate the originating session before the atomic claim, so rejection does not consume another session's preview. Disabled channels never claim previews, including explicit tokens from PiWeb.

Generic-tool guards block recognizable ledger directory reads/writes and backend commands on disabled channels. This is defense in depth, NOT an OS filesystem/security boundary: arbitrary code executed as the same UID can bypass string/path checks and environment policy. Strict privacy requires an independently configured sandbox/UID or capability-gated backend. No sandbox, model change, thinking change, KV-manager change or unrelated gateway edits are made by this rollout.

## Result presentation

The host formats ordinary transaction/balance queries and statistics as Markdown tables. Internal transaction comments are hidden from those tables, not deleted from the journal. Every posting retains its signed amount/currency string; no currency mixing, amount rounding or model-side total calculation is introduced. Money precedes long accounts on phones; unsupported report layouts and explicit raw formats use fenced output. Read-only filters, schemas, confirmation previews and mutation authorization are unchanged. PiWeb's existing renderer/CSS is used unchanged.

## Model result feedback

Terminal `finance_finish`/`finance_action` results now continue to a brief model explanation instead of forcing an immediate end. Failed raw host error dumps are replaced by model feedback; successful host receipts/tables remain authoritative. During explanation every tool is blocked, including direct finance calls and `finance_release`, so narration cannot replay a completed/uncertain write. Wrong fast-path tools get two bounded corrections before explanation; failed narration gets one readable fallback. Token previews/questions and unavailable-wallet denials retain their host behavior. Details and actual gateway evidence: [model-feedback report](../docs/eval/2026-10-09-model-feedback.md).

## Verification

- `npm test` in `extension`: regression and new stable-interface/real-isolated-ledger tests.
- `tsx channel-serialize.ts`: real Pi SDK request serialization, deliberately stopped before inference. Compares system/tools across active PiWeb financial FSM and ordinary Discord chat; checks production hash before/after this test.
- TypeScript noEmit check and Python backend unittest suite.

No performance/hit-rate claim or independent review is implied. Existing uncommitted Laya work is preserved, not deployed. Rollback: remove only the two named drop-ins and unregister the finance package; reload/restart affected workers through the existing restart-service hub.
