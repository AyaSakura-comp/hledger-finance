# Markdown financial results — 2026-10-09

## Change

- The host, not a second model pass, renders ordinary transaction queries, balances and statistics into Markdown tables.
- Normal journal tables omit internal comments (`source`, `inferred:*`, `import-id`) and redundant `[1/1]`; actual installment progress remains visible. All postings, signed monetary strings, currencies and dates remain backend-owned, without recalculation or rounding.
- On phones, money comes before long accounts; tables use no HTML `<br>` because PiWeb's actual DOM renderer displays it literally. Additional postings use separate rows.
- Explicit JSON/CSV/HTML remain fenced raw data. Unknown layouts retain all output in a code block. A zero total without visible accounts is explicitly inconclusive, not a claim that cash is zero. Unfiltered all-account reports warn that their net includes counterpart postings and is not available cash.
- Table text escapes markup/media-marker delimiters. Existing schemas, filters, system prefix, confirmation state machine, model/thinking options and gateway UI source are unchanged.
- `agent.ts` preserves the committed guarded factory separately from locally modified `index.ts`. Local unaccepted Laya sources/evidence remain unstaged and are not dependencies of the deployed channel entrypoint.

## Verification

- RED: four initial presentation tests failed; subsequent media-marker, portable-no-HTML, stats-table, empty-balance and mobile-money-visibility regressions also failed before their fixes.
- Extension unit/integration suite: 103/103 in the local tree, including preserved local experimental tests. Self-contained staged-tree checkout: 85/85, plus TypeScript noEmit; excluded prototype dependencies are not required.
- Python backend: 458/458, `/tmp/finance-render-backend.log`.
- TypeScript noEmit and whitespace checks; no backend source changes.
- Real SDK serialization: `/tmp/finance-channel-sdk-iNI1QG`; equal finance-owned system/tools across hosts, production hash unchanged during this test. Equality is not a measured KV hit-rate.
- Actual PiWeb renderer and CSS, disposable static Chrome fixture, 390×844: `/tmp/finance-render-browser-GDq9ek`. Four posting rows, exact synthetic amounts, money cells visible without horizontal scrolling, table-internal scrolling rather than document overflow, no page exceptions. Screenshot directly inspected. This is not a full live-chat/interaction/video audit; no gateway frontend files changed.
- Actual deployed gateway print/RPC rerun: `/tmp/finance-gateway-live-zc9fXr`, **11/12 cases passed**, not 12/12. Discord isolation held and each Web route wrote one isolated expense. One print-mode model read used nonexistent `asset:cash` instead of `assets:cash`; the formatter faithfully received an empty report. Persistent RPC displayed the correct -120 TWD posting, but also included all accounts rather than a cash-only filter. Account selection remains a model-query correctness issue, not solved by presentation; this failure is retained, not replaced with a successful rerun. The new inconclusive-empty-balance and unfiltered-total warnings were unit-tested after this discovery; the failed live run remains the reported gateway evidence.

## Limits / ship status

No real ledger entry was created by these tests. No service/model/KV-manager restart or configuration change was made for rendering. No independent review is claimed. Commit/push are pending the required independently authorized review; do not label this feature fully accepted solely from passing formatting tests. Gateway account-selection correctness needs follow-up before financial read acceptance can be claimed.

## Reproduction

```sh
cd extension
npm test
./node_modules/.bin/tsc --noEmit --module nodenext --moduleResolution nodenext --target es2022 --allowImportingTsExtensions --skipLibCheck *.ts
PIWEB_DIR=/path/to/piweb ./node_modules/.bin/tsx render-browser.ts
./node_modules/.bin/tsx channel-serialize.ts
node channel-gateway-live.mjs
```

The browser/gateway/SDK fixtures require this machine's Pi/gateway dependencies; unit formatter tests are self-contained with the extension dependencies. Artifacts stay outside Git and contain synthetic journal data only.
