# SemZero Core v1.4 — Assumption Gate Feedback Calibration

This release keeps SemZero focused on the core dbt Assumption Gate. It does not add Terraform, Kubernetes, cross-domain graphing, RGCN, Chaos Mode, Streaming Gate, full Wind Tunnel, or repair automation.

## Scope

SemZero Core v1.4 adds the missing trust loop for shadow mode:

```text
Assumption Gate receipt
→ developer agreement/disagreement feedback
→ assumption dashboard calibration metrics
→ safer promotion from shadow to advisory later
```

## New command: `semzero assumption-feedback`

Record developer feedback against a receipt or individual finding:

```bash
semzero assumption-feedback \
  --receipt data/assumption_gate_receipt.json \
  --finding-id AG-TEMPORAL-BUCKET-001 \
  --disposition agree \
  --reviewer analytics@example.com \
  --comment "This is a real timezone-boundary risk." \
  --feedback-file data/assumption_feedback.jsonl
```

Supported dispositions:

```text
agree
disagree
false_positive
false_negative
needs_context
fixed
accepted_risk
```

The ledger is JSONL by default so teams can use it in CI without a database.

## Dashboard integration

`semzero assumption-dashboard` now reads developer feedback:

```bash
semzero assumption-dashboard \
  --receipt-dir data \
  --feedback-file data/assumption_feedback.jsonl \
  --output data/assumption_dashboard.json \
  --markdown-output data/assumption_dashboard.md
```

The dashboard now reports:

```text
feedback_count
developer_agreement_count
developer_disagreement_count
developer_agreement_rate
developer_disagreement_rate
false_positive_count
false_negative_count
fixed_count
accepted_risk_count
needs_context_count
disposition_counts
```

## Why this matters

The Assumption Gate should not earn enforcement authority by assertion. It should earn it through shadow-mode evidence.

The v1.4 feedback loop lets a team answer:

```text
Did developers agree with SemZero's findings?
Which assumption families are noisy?
Which findings got fixed?
Are there false positives before we move to advisory or require-review mode?
```

## Receipt/dashboard versions

Receipts now emit:

```text
dbt_assumption_gate_v1_4
```

Dashboard now emits:

```text
semzero_assumption_dashboard_v1_4
```

Older v1/v1.1/v1.2/v1.3 receipts remain accepted by the dashboard.

## Verification

Targeted tests:

```bash
pytest -q tests/test_dbt_assumption_gate_v1.py tests/test_assumption_dashboard_v1.py tests/test_assumption_feedback_v1.py
```

Verified result:

```text
6 passed
```
