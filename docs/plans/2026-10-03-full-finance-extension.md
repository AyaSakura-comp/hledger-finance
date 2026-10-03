# Guarded whole-skill finance extension implementation plan

**Goal:** Replace unsupported finance fallthrough with explicit guarded workflows, retaining the host-owned single-expense fast path.
**Architecture:** Typed operation proposals -> fixed Python bridge -> validation/preview -> host-held, exact user confirmation -> mutation -> deterministic receipt. No model-supplied shell/argv, journal path, confirmation token or report output path. Existing finance.py remains the accounting source of truth. Complex mutations require an explicit host preview confirmation because parsed facts are not yet host-verifiable. No production activation or automatic commit/push in this task.
**Stack:** Pi extension SDK, TypeBox, Node built-ins, Python standard library, hledger; existing matplotlib/rclone/PDF extractor when available.

## Ordered tasks (parent executes; no implementation delegation)
1. Fix known binding regression: expose validated binding target to FinanceFlow. RED known-defects.test.ts; GREEN core tests.
2. Add `extension/workflows.ts` and `workflows.test.ts`: finite states, single-flight, immutable preview/authorization, exact token commands, cancellation, stale snapshot, query/report receipts, expiry. Begin with failing tests; use backend stubs only for orchestration.
3. Add `scripts/finance_bridge.py` and `tests/test_finance_bridge.py`: strict structured operations and canonical parameter mapping, snapshots under existing journal lock, no raw command tool. Tests use temporary Git journals, all transaction kinds and explicit account routing.
4. Transaction batch / complex payment / schedules: normalized ingest-json or deterministic loan/installment command, preview before complex writes, exact authorization, one atomic action, undo. Validate original records with backend, never generate raw ledger text in model.
5. Historical migration: stable source IDs, kind/currency control totals, isolated trial ingest + strict audit before final preview; never waive explicit historical fields.
6. Queries/stats/native readonly reports/visualization: bounded fixed report schemas, multi-currency split, host-selected unique artifacts and verified media markers.
7. Delete and undo: preview complete candidates, immutable filters/backend token, new-turn exact user token only; subsets require a new preview. Bind all mutations to journal/rules/source/HEAD fingerprint under the same lock.
8. Rules/backup: strict existing rule validators, fixed filenames, atomic replacement/rollback, path-specific Git commit, explicit remote shown in confirmation, no OAuth setup/service restarts.
9. Attachments: read only explicit user source paths, size and type limits; CSV/text/PDF extraction treated as data; attached images use current model capability (fail closed if unavailable). No new OCR/bank/voice integrations claimed.
10. Wire guarded routing into `extension/index.ts`, add complete hook tests, use real SDK+Qwen smoke cases against disposable journals. Keep incomplete outcomes/failures visible.
11. Update install/capability docs and evidence. Run backend + all extension + TS suites, independent review before any requested publication. Full model acceptance is separate from unit tests; document remaining unverified transports/cloud/vision.

## Acceptance tests
- No finance model tool escapes during managed requests; text resembling a tool call cannot mutate.
- No same-turn/model confirmation, no broader deletion on partial confirmation, no reuse after outcome uncertainty.
- Explicit amounts, accounts and currencies in preview match committed fixed payload.
- Batched write one commit, failed row no writes, cross-currency routing and all non-expense roles preserved.
- Historical count/totals/IDs and audit checked before production writes; mismatches rejected.
- Loan required terms not guessed; schedules stable IDs; query/report flags canonical and readonly.
- Artifacts have verified content/path, reports split per commodity, exact outbox markers emitted by host.
- Only fixed rule filenames can be written, rollback on commit failure, backup only the user-approved remote.
- Production journal checksum unchanged throughout evaluation.
