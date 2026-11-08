# Laya finance decisions (experimental, not default)

Goal: main agent hands off the latest real user message; extension owns routing, category selection, bounded fact extraction, validation and execution. Follow the browser-intent pattern: candidates → Laya probabilities → confidence/margin gate → host execution → actual receipt.

Safety boundaries: do not edit the production book; preserve Qwen model/service/thinking settings; complex mutations retain snapshot-bound confirmation. Laya cannot generate arbitrary JSON/facts or authorize changes. Exact confirmation, cancellation, replay and uncertain-write handling remain programmatic, not probabilistic.

1. Test-first bounded local Laya transport, enum validation, low-confidence abstention, quoted/gateway input isolation.
2. Implement opt-in `PI_FINANCE_DECISIONS=laya`; do not register legacy decision tools in this mode. Only finance_handoff is exposed to the main agent.
3. Host parses supported everyday amounts/dates/currencies. Laya chooses route/category/payment from authorized catalogs; rules remain binding. Unsupported material facts stop without writing (no silent legacy fallback).
4. Test execution, locks, uncertain mutations, confirmation, preview-only and ambiguous repeats on disposable journals.
5. Measure real Laya routing + classification and Pi/local-Qwen end-to-end against existing 100 phrases and 50 category cases; keep all failures/raw evidence. Separate parser support, correct amount/currency, category accuracy, write correctness and latency.
6. Extend complex fact extraction only after the initial accuracy gate. No production default change, service config change, commit or push in this task without separately passing acceptance.
