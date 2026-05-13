# SemZero Core v1.9 — Stable Finding IDs + Softer Calibration

This release keeps SemZero core-only: dbt Assumption Gate, typed receipts, PR comments, developer feedback, and assumption dashboard.

No adapters were added. Terraform/Kubernetes/cross-domain graph work remains deferred.

## What changed

### Stable finding IDs

Assumption findings now emit stable IDs derived from:

- assumption family
- source dbt resource
- detector pattern type
- normalized evidence excerpt

Example:

```json
{
  "id": "AG-TEMPORAL-BUCKET-6182494A68",
  "stable_id": "AG-TEMPORAL-BUCKET-6182494A68",
  "legacy_id": "AG-TEMPORAL-BUCKET-001",
  "fingerprint": "..."
}
```

The legacy ID is retained for backward-compatible feedback capture.

### Feedback can target stable IDs

`semzero assumption-feedback` now supports:

```bash
semzero assumption-feedback \
  --receipt data/receipt.json \
  --stable-finding-id AG-INCREMENTAL-FILTER-ABC123DEF0 \
  --disposition fixed
```

Legacy `--finding-id AG-INCREMENTAL-FILTER-001` still works.

### Recurring stable findings

The dashboard now reports recurring stable findings, not only recurring families. This lets teams distinguish:

- a broad noisy family
- a specific recurring fragile query/model assumption

### Softer calibration recommendations

Policy calibration is intentionally less strict by default. A fixed cost-bearing finding now usually becomes an `advisory_candidate` first unless there is enough feedback, stable recurrence, and low false-positive signal to justify human review for require-review mode.

SemZero does **not** auto-apply policy changes.

### Calibration readiness

The dashboard now emits:

```json
"calibration_readiness": {
  "state": "shadow_only | advisory_candidate | tune_noise_before_enforcement | require_review_candidate_human_review_required",
  "guardrail": "This is not enforcement..."
}
```

This is meant to guide humans, not enforce automatically.

## Guardrail

Stable IDs and calibration recommendations increase trust over time, but they do not make SemZero production-blocking by default. Teams should move from shadow to advisory before considering require-review.
