# SemZero Core v1.18 — Assumption Precision / Noise Evaluation

This release adds an advisory-only precision evaluation harness for the focused dbt Assumption Gate.

The goal is to improve calibration before any stricter policy mode. It does not block CI, mutate policy, or suppress findings automatically.

## Command

```bash
semzero assumption-precision-eval \
  --receipt-dir data/semzero_assumption_gate \
  --feedback-file data/semzero_assumption_gate/assumption_feedback.jsonl \
  --exceptions-file data/semzero_assumption_gate/assumption_exceptions.jsonl \
  --output data/semzero_assumption_gate/assumption_precision_eval.json \
  --markdown-output data/semzero_assumption_gate/assumption_precision_eval.md
```

## What it evaluates

- findings missing why-now trigger evidence
- findings with no downstream blast radius
- over-broad blast-radius findings
- findings with negative/false-positive developer feedback
- findings under active exceptions/accepted risk
- developer-validated cost/business-critical findings
- family-level readiness for advisory mode

## Guardrail

This is a calibration report, not enforcement. It helps decide what to tune before moving from shadow to advisory or require-review.
