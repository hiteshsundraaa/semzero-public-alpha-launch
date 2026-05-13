# SemZero Core v1.25 — Assumption Drift Memory Lite

This release adds organization/team/model-level memory over existing Assumption Gate evidence.

It is intentionally broader than a single PR, but it does **not** add new detectors, new adapters, or stricter enforcement. It aggregates existing receipts, Replay Lite results, feedback, exceptions, business criticality, and cost exposure so broader memory cannot reduce detector precision.

## Command

```bash
semzero assumption-memory \
  --receipt-dir data/semzero_assumption_gate \
  --feedback-file data/semzero_assumption_gate/assumption_feedback.jsonl \
  --exceptions-file data/semzero_assumption_gate/assumption_exceptions.jsonl
```

Default outputs:

```text
assumption_memory.json
assumption_memory.md
```

## What it summarizes

- organization-wide assumption memory
- recurring assumption families
- source/model-level risk memory
- owner/team-level risk memory
- business-severity memory
- watchlist items for weekly review

## Memory patterns

- `validated_recurring_drift`
- `recurring_drift_needs_feedback`
- `accepted_risk_memory`
- `expired_risk_memory`
- `noisy_pattern`
- `high_risk_unvalidated`
- `watch`

## Accuracy guardrail

Drift Memory Lite is advisory aggregation only. It does not create new findings, block merges, or auto-change policy.
