# Financial result feedback to the model — 2026-10-09

## Requested behavior

Separate ending a financial operation from ending the assistant reply. Permit a natural-language explanation instead of showing a raw backend error. This work does not repair/clear the production journal's Git state or replay the screenshot's 1,690 TWD expense.

## Implementation

- `channel.ts` wraps terminal `finance_finish`/`finance_action` results (`done`, `failed`, `cancelled`) with explanation context and `terminate:false`.
- Successful authoritative host receipts/tables/media remain displayed. Failed host error dumps are suppressed and handed to the model. Preparation failures can also proceed to an explanation turn.
- New per-session `FinanceFeedback` closes all tool execution during explanation, including direct finance executors, shell tools and `finance_release`. The model may explain and suggest a next step, not execute another operation. Completed/uncertain writes are never automatically replayed.
- Incorrect fast-path tool selection now returns repair feedback twice instead of terminating with an empty reply. Exhaustion closes the prepared batch and switches to explanation. Category/parameter validation retains its existing bounded corrections before writes.
- Up to three explanation turns handle mistaken tool attempts. If the model fails to reply, abort the loop and show one readable host fallback. Only assistant text after the terminal result counts as the reply; thinking/prefaces do not.
- Guidance asks for a short Traditional Chinese response, original currency codes and no routine category questions or destructive/unconditional Git advice. Wording is generative: this is not a guarantee that every future explanation is perfectly factual. Backend authorization/replay prevention is host-enforced, not dependent on model compliance.
- Confirmation previews/questions, token authorization and unavailable-wallet denials retain their existing host behavior. Some input-handled confirmations still use direct host receipts rather than a model continuation.
- No public tools/schema/active-tool differences, channel-specific system prompt, provider thinking overrides, model configuration or KV-manager changes. Disabled channels never enter model narration or the backend.

## Verification

- Eight new tests, RED before implementation; wrong-tool correction/exhaustion also tested RED before fixing. Full local extension suite **111/111**; clean deployment-scope snapshot without local Laya dependencies **93/93** (`/tmp/finance-feedback-clean-C8YHP1`); TypeScript noEmit and whitespace checks passed.
- Actual SDK serialization `/tmp/finance-channel-sdk-uywuxh`: finance-owned system/tools equal across PiWeb and Piscord; production hash unchanged within this test. One earlier audit attempt timed out, retained in `/tmp/finance-feedback-serialize.log`; later completed audit is not a measured KV-hit-rate.
- Final actual installed PiWeb gateway print/RPC acceptance: `/tmp/finance-feedback-live-eVo4nM`, **4/4**. Every case had exactly one `finance_finish` completion and actual final model text after its result, with no fallback. Success recorded exactly one synthetic 1690 TWD expense; rejection changed neither journal, staged index nor HEAD. No real chat/Discord transport messages were posted.
- Earlier failures retained: `/tmp/finance-feedback-live-tCOrMd` exposed wrong-tool termination; `/tmp/finance-feedback-live-EOqtCn` had correct financial/model behavior but the harness incorrectly rejected comma-formatted `1,690`; fixed to accept only exact fixture equivalents. `/tmp/finance-feedback-live-lMZHxE` exposed fallback/preface accounting and verbosity/currency-guidance limits. Final harness distinguishes final model replies from safe fallbacks.
- Python backend unchanged; prior backend suite remains 458/458, not claimed as newly rerun for this feature. No independent code review, commit or push is claimed.

## Runtime reload

At 2026-10-09 16:03:55 Asia/Taipei (clock check immediately before scheduling), scheduled `piweb-finance-feedback-20261009.timer` with a 60-second delay. Its external service invokes the approved restart hub with `PIWEB_WORKER_ONLY=1` and `PIWEB_CHANNEL_JID` unset, avoiding self-cgroup termination while this reply is delivered. Only the affected PiWeb worker is targeted; model, gateway, Docker, Tailscale and settings are untouched. Timer creation was confirmed; restart completion is not yet claimed in this report.

## Reproduce

```sh
cd extension
npm test
./node_modules/.bin/tsc --noEmit --module nodenext --moduleResolution nodenext --target es2022 --allowImportingTsExtensions --skipLibCheck *.ts
node feedback-gateway-live.mjs
```

The live harness requires the installed PiWeb gateway/service environment and creates disposable journals. It inherits real model/provider options without overrides; tests never automatically replay an uncertain operation.
