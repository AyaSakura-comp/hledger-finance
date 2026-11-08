# Installed gateway acceptance — 2026-10-09

## Installed runtime

Global Pi package `/home/chihmin/src/hledger-finance`, manifest entry `extension/channel.ts`. Both services run the same Pi build and global extension package. Actual running process environments verified: PiWeb worker PI_FINANCE_HOST=piweb (PID 2479755); Piscord gateway PI_FINANCE_HOST=piscord (PID 2461889). Prior worker-only restart confirmed by hub journal; model, Docker and other services unchanged.

## Real gateway execution

Harness `extension/channel-gateway-live.mjs` imports the **deployed compiled** gateway invokeAgent and RpcSession modules, inherits the environment of each running service, and invokes the actual Pi CLI with globally installed extensions and configured model/thinking. Only journal, pending state, session and DB destinations are redirected to isolated temporary paths. No custom system prompt, model, thinking or loaded-extension overrides.

Final artifacts: `/tmp/finance-gateway-live-Li5QLb/summary.json`; complete events and gateway logs under each host/mode directory. **12/12 cases passed**:

| Host | Path | Cases | Result |
|---|---|---|---|
| Piscord | print/JSON | expense, balance, forged Web label, explicit model finance_handoff call | All denied visibly; 0 transactions |
| Piscord | persistent RPC | same four | All denied visibly; 0 transactions |
| PiWeb | print/JSON | lunch 120 TWD, cash balance | Exactly 1 correct food expense; query -120 TWD |
| PiWeb | persistent RPC | same two | Exactly 1 correct food expense; query -120 TWD |

PiWeb tests had zero opening cash, so -120 is the expected synthetic balance, not the user's actual balance. Expense/query each completed in approximately 3.6–4.0 s in this run; no general performance guarantee. Discord's direct-model handoff was an actual tool call (asserted), not merely a model's claim of denial.

## Defect discovered and repaired

Initial artifacts `/tmp/finance-gateway-live-jVxsKO` retained. Ordinary Discord financial input correctly displayed a refusal, but explicit model-triggered handoff terminated on the blocked-tool result and the gateway displayed `(empty response)`. Root cause: gateways display custom receipts/final assistant text; termination skipped assistant follow-up and the denial hook had not emitted a displayed receipt. Added failing regression then minimal visible-denial receipt for hook/direct-executor paths. Re-ran every live case in fresh isolated books; all passed.

Extension regression **98/98** (`/tmp/finance-channel-suite-final.log`), TypeScript noEmit pass, real SDK system/tools equality still passes (`/tmp/finance-channel-sdk-hfvnnh/evidence.json`). No schema/system-prefix changes introduced by the receipt fix; real chat history differs normally.

## Scope/limits

This tests the actual installed gateway → Pi execution paths, including persistent RPC, not a Discord-server message delivery or browser-UI action. No test messages were posted to Discord users/channels. Tool-level policy is not a same-UID OS sandbox. Existing uncommitted prototype/user edits preserved; no commit/push or delegation performed.
