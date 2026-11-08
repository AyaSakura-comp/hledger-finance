# Experimental Laya decision layer — NOT production ready

The default extension is unchanged. No settings.json change is needed or made. This prototype fails accuracy acceptance; do **not** replace the existing finance workflow with it.

## Architecture

`main agent → finance_handoff(reason?) → extension → local Laya candidates/probabilities → confidence/margin gate → host parser/catalog/payment rules → backend receipt`

The main agent cannot submit amounts, categories or an operation. In this mode it sees only `finance_handoff`, not `finance_finish` / `finance_action` / `finance_question`. Laya chooses record/query/change/none, allowed report/operation and catalog categories; the host owns money/date/currency facts, binding rules, exact confirmation, one-use authorization and execution. Only actual write receipts establish repeat context. Ambiguous “多一筆” cannot duplicate a record.

The protocol matches the local browser-intent prototype (`/v1/systemone` with state/questions/choice criteria). Transport permits local HTTP only, refuses redirects, has a 5-second timeout, bounds request/response sizes and validates all returned labels/probabilities. Outages or low-confidence decisions stop without silently switching to another model. Generic ledger writes remain blocked.

## Scope / gaps

Implemented experimentally: simple typed income/expense batches, classification/payment selection, limited read queries/stats/charts, checks/audit/settings, limited delete previews and undo with existing snapshot-bound confirmation. Unrecognized filters are rejected rather than widened. Exact confirmation/cancellation is handled locally; ordinary intervening input invalidates memory and disk previews even without another handoff.

**Not implemented:** complex fact extraction for receipts/images/PDF/CSV/historical imports, FX/refund/opening-balance phrasing, amendments, rule mutation, backups, installments and loans. Choosing their enum does not execute them; it returns unsupported. The original default implementation retains those capabilities. This is not completion of the requested full-flow migration.

## Isolated testing only

The factory refuses the production journal and requires an explicit isolated journal in Laya mode. Initialization must use the established finance backend in an authorized development harness. Never use the real finance directory for these tests.

```bash
# When the package is already registered, do not load it twice with -e.
PI_FINANCE_JOURNAL=/absolute/path/to/disposable/main.journal \
PI_FINANCE_STATE_DIR=/absolute/path/to/disposable/preview-state \
PI_FINANCE_DECISIONS=laya pi
```

Optional local endpoint override: `PI_FINANCE_LAYA_URL=http://127.0.0.1:8000/v1/systemone` (default). The extension does not start/restart services or change model/thinking settings. Existing Laya CPU service must already be available.

## Reproducible evidence

From `extension/`:

```bash
npm test
./node_modules/.bin/tsx laya-eval.ts
./node_modules/.bin/tsx laya-pi-eval.ts
```

`laya-eval.ts`: real Laya CPU + real backend, 100 existing Taiwan phrasings, separate 50-category corpus, 42 intent cases. Disposable Git journals, exact source facts and final postings checked. The 100-case correctness metric validates count/description/kind/date/currency/amount, **not category correctness**; category accuracy is measured separately. Every failure retains the raw decisions, probabilities, backend inputs and ledger. It measures extension execution, not end-to-end main-agent/UI latency.

`laya-pi-eval.ts`: six paired real Pi SDK + configured `local-llama/qwen3.6-35b-q4` cases, alternating mode order, minimal thinking in the in-memory harness only. It checks actual tool results and ledger, not verbal claims. A single screening run is not a stable latency benchmark. Both harnesses hash the production journal before/after and stop on a mismatch.

The browser reference is itself a **hybrid**, not pure Laya: exact/anchor matching, embedding recall of eight candidates, Laya/embedding agreement, then a local Qwen shortlist judge for ambiguous results. Its existing report explicitly found uncalibrated `none_match` probabilities in Chinese. The finance prototype intentionally measures pure Laya first and has no silent Qwen fallback. A hybrid version requires separate implementation/evaluation; do not label its decisions “Laya-only”.
