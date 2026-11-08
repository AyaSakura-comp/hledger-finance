# Channel wallet implementation plan

Goal: identical loaded finance entrypoint, public tool schemas/order and system prefix in PiWeb/Piscord; only host execution authority differs. No model, KV manager or gateway working-tree changes.

Architecture: new `extension/channel.ts` adapts the existing guarded FSM. Keep every finance tool active with its initial schema; replace dynamic registrations' execution closures only. Move workflow instructions to non-system context messages. Capture the service-owned PI_FINANCE_HOST once at startup: piweb is full, piscord/unknown are disabled. Disabled hooks/tools must never access backend, sources, rules or pending store. Existing experimental/legacy factory remains unchanged and is not deployed.

1. Add `extension/channel.test.ts`, assert schema and active-tool equality across input/handoff/settle; prefix unchanged and provider payload unchanged; disabled direct tools/input/confirmation cannot call backend or inspect a source. Watch failures.
2. Add `extension/channel.ts`; capture policy, register stable wrappers and adapt context. Namespace persisted confirmation sessions by host. Run regression/TypeScript checks.
3. Add real isolated backend and SDK/request-serialization evidence. Verify true ledger SHA-256 unchanged. No production writes and no inference-performance claims.
4. Switch package manifest to channel entrypoint. Reinstall same package globally. Set host in two dedicated user-service drop-ins; use existing restart-service hub only. Verify actual process environment and registration. PiWeb worker-only restart must occur outside its own cgroup, after delivery of this turn.

Acceptance: same tools and system prefix, deny source spoofing and cross-host confirmation, real isolated PiWeb bookkeeping/query; zero Discord backend calls, no false zero-balance or success. Finance wrapper is not an OS security boundary: unrestricted shell on the same UID can bypass tool-level path guards.

No delegation, commit or push without operator authorization. Preserve all existing dirty files.
