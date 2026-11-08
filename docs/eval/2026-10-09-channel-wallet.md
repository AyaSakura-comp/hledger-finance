# Channel wallet acceptance — 2026-10-09

## Results

- Extension regression: **97/97 passed**, `/tmp/finance-channel-suite.log`.
- Backend regression: **458/458 passed**, `/tmp/finance-channel-backend.log`.
- TypeScript noEmit: pass, `/tmp/finance-channel-typecheck.log`.
- `git diff --check`: pass.
- Actual Pi SDK serialized system/tools equality across active PiWeb fast-FSM and ordinary Piscord chat: **pass**. Evidence `/tmp/finance-channel-sdk-xK88vn/evidence.json`; prefix hash `59717b8924a71cd44b38caca0caa0b47b6e59e244406cee0c5f5ff03170c5557`. Inference stopped deliberately before network request; no real KV hit-rate or latency claim.
- Real isolated journal: PiWeb lunch 120 TWD write, cash query, deletion preview and subsequent confirmed deletion pass. Discord explicit Web token/forged Web label/direct financial calls: zero backend calls, same isolated ledger unchanged. Initial/final public tool order/schema remains equal.
- Missing/unknown host denies. No false zero-balance/success. Ordinary Discord chat remains ordinary. Direct recognizable ledger reads denied (defense in depth only).
- Persisted claims bind session and reject wrong-session claims without consuming the rightful preview. Stable public operation enum preserves settings/native/classify/full mutation workflows; narrower contextual operation locks enforced at execution, not schema.

## Rollout

Package manifest now points to `extension/channel.ts`; same global local package installed for both launchers. Other global settings compared against backup and unchanged. Two dedicated user-systemd drop-ins set PI_FINANCE_HOST=piweb/piscord. No model, KV manager, gateway source or unrelated service configuration edits. Experimental Laya working tree retained but not activated. No commit/push or independent delegated review performed.

Piscord restarted through the restart-service hub and verified Gateway running with actual process PI_FINANCE_HOST=piscord. PiWeb worker restart must be scheduled from an external systemd unit after this reply, using the hub's PIWEB_WORKER_ONLY=1 route (not Docker/gateway/model restart).

## Production ledger observation

The SDK-only test's production SHA-256 remained unchanged during each run. Across the broader task, the ledger changed from `f32804ed...` to `9f4c65cc...`. Investigation identified a separate active PiWeb session `web_life_703b8df8` issuing shell append calls at 2026-10-09T02:15:58.728Z and 02:16:03.113Z for `flow-return-trip-20261007`. That unrelated entry/change was preserved; this rollout did not revert it or claim whole-task hash equality. All new write/delete tests target temporary journals.

## Limits

Same finance-owned system/tools prefix is verified in real SDK serialization, not equality of every existing gateway context/extension setting. Finance responses and histories differ normally. Unrestricted shell running as the same OS UID can bypass extension-level protection; no OS sandbox/privacy boundary was deployed. Updated extension set may cause the expected one-time golden refresh.
