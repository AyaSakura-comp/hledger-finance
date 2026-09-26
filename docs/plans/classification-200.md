# Expense classification: 200-scenario implementation plan

**Goal:** Fix cross-category history poisoning and establish repeatable category standards without modifying the production journal.
**Architecture:** Explicit debit remains authoritative. Literal user rules precede product/purpose rules, which precede merchant-only defaults. History needs >=3 comparable observations and >=80% agreement; it cannot override an explicit product family. Ties and conflicting purpose families return a reviewable fallback. Confidence is a heuristic score, not a probability.
**Tech stack:** Python standard library, unittest, existing hledger ingestion and validation.

Execution is by the parent agent (no delegated work requested).

1. Create `tests/test_classification_scenarios.py`: freeze 150 ordinary descriptions across 25 category families, 10 conflict/boundary cases, 20 historical cases, 10 custom-rule cases and 10 normalization cases. Give each a stable ID, expected account and rationale. Generate independent unittest methods and a JSON/Markdown reporting entry point.
2. Run `python3 tests/test_classification_scenarios.py --report /tmp/hfin-classification-200/baseline` BEFORE implementation; preserve every result including failures.
3. Modify `scripts/finance.py`: separate product/purpose from merchant rules; fix English word boundaries; replace last-row history selection with consensus on strongest matches, collapse installment observations, refuse mixed-expense historical examples. Never infer non-expense directions here. Protect custom literal overrides and prefer the most specific match; equal-specificity conflicts abstain.
4. Re-run the 200 cases. Revise legacy history tests only where they encode the intentionally retired single-row/last-row policy. Add integration regressions for ingestion (auto and explicit override), mixed amounts and non-expense bypass. Run full unittest suite, py_compile and diff check.
5. Update `references/CLASSIFICATION.md`, `SKILL.md`, `README.md`, `references/ACCOUNTING.md` and `references/CSV.md` to the same policy. Clearly separate actual item from merchant/location/project; acknowledge ambiguous bundles and unknown meanings. Do not promise automatic persistence of corrections: the literal configuration file is the explicit durable override.
6. Read review guidance, review diff locally, and deliver the 200-row before/after report. Do not change the production ledger, push, or claim unrelated loan release blockers resolved. Preserve a hash of the production journal before/after verification.

Acceptance: 200 unique fixed scenarios, baseline recorded, all final assertions passing, original suite preserved except obsolete policy assertions, report clearly scoped to deterministic category classification (not Qwen intent/OCR or real-world accuracy).
