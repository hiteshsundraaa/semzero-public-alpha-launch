# SemZero Core v1.22 — Replay-aware dashboard and precision integration

This release keeps SemZero core-only and non-blocking. It does not add adapters, full warehouse replay, RGCN, Chaos Mode, Streaming Gate, or repair automation.

## What changed

v1.22 connects Assumption Validation Replay Lite into dashboard and precision calibration.

The dashboard now separates:

- inferred assumption risks where no Replay Lite fixture was supplied
- replay-validated drift findings
- low-fidelity findings that need better evidence before policy promotion
- family-level replay coverage and drift rates

The precision evaluator now uses replay status and replay/evidence fidelity when classifying findings.

## New dashboard fields

`semzero assumption-dashboard` now emits a `replay_aware` section:

```json
{
  "kind": "semzero_replay_aware_dashboard_v1",
  "replay_ran_count": 3,
  "replay_not_run_count": 2,
  "drift_detected_count": 3,
  "low_fidelity_count": 1,
  "replay_coverage_rate": 0.6,
  "average_fidelity_score": 0.72,
  "family_replay": [],
  "review_queue": [],
  "guardrail": "Replay-aware dashboarding is advisory-only."
}
```

## Precision states

`semzero assumption-precision-eval` can now classify findings as:

- `replay_validated_developer_validated`
- `replay_validated_shadow`
- `low_fidelity_review`
- existing states such as `high_signal_shadow`, `developer_validated`, `needs_noise_review`, and `insufficient_evidence`

## Guardrail

Replay Lite is still targeted local/sample validation. It is not a full Snowflake, Databricks, or production warehouse clone.

The intended use is:

```text
Assumption finding
→ Replay Lite drift signal if fixture/sample exists
→ dashboard separates validated drift from inferred risk
→ precision evaluator recommends feedback/replay work before policy promotion
```
