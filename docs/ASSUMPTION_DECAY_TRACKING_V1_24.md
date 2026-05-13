# SemZero Assumption Decay Tracking Lite v1.24

This release adds advisory-only assumption decay tracking.

It uses stable finding IDs, receipts, Replay Lite validation, developer feedback,
exceptions, and evidence age to identify assumptions that may be becoming fragile
over time.

Decay states include:

- `decaying_high_confidence`
- `decaying_needs_feedback`
- `accepted_risk_debt`
- `review_expired_exception`
- `tune_or_suppress`
- `needs_review`
- `watch`
- `stable_or_insufficient_history`

The command is:

```bash
semzero assumption-decay \
  --receipt-dir data/semzero_assumption_gate \
  --feedback-file data/semzero_assumption_gate/assumption_feedback.jsonl \
  --exceptions-file data/semzero_assumption_gate/assumption_exceptions.jsonl
```

It writes `assumption_decay.json` and `assumption_decay.md` by default.

Guardrail: decay tracking is not enforcement. It does not block, suppress, or
mutate policy. It produces a review queue for humans.
