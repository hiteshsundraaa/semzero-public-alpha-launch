# SemZero Core v1.8 — Policy Calibration Recommendations

This release stays core-only. It does not add Terraform, Kubernetes, cross-domain graphing, RGCN, Chaos Mode, Streaming Gate, full Wind Tunnel, or repair automation.

v1.8 connects the assumption dashboard back to policy tuning without automatically changing enforcement.

## What changed

`semzero assumption-dashboard` now emits a `policy_recommendations` block. The block uses shadow receipts, developer feedback, fixed findings, accepted-risk history, false positives, recurring assumption families, and rough cost exposure to recommend policy tuning candidates.

The recommendations are advisory only:

```json
{
  "auto_applied": false,
  "guardrail": "Recommendations are advisory only. Do not auto-promote enforcement without human review and sufficient shadow sample size."
}
```

## Recommendation classes

Family-level recommendations can include:

- `require_review_candidate` — the family has fixed/value signal, often with directional cost exposure.
- `advisory_candidate` — developer agreement is high enough to surface the family more prominently.
- `lower_severity_or_suppress_candidate` — feedback suggests high false-positive rate.
- `accepted_risk_policy_review_candidate` — the family is repeatedly accepted as risk and may need explicit owner sign-off rather than more warnings.
- `keep_shadow_collect_feedback` — signal is too sparse or mixed.

## Why this matters

SemZero should not jump from shadow mode to enforcement because it found a few scary-looking SQL patterns. The dashboard now answers a better question:

> Which assumption families deserve stronger policy, and which are too noisy?

This makes the path from shadow → advisory → require-review evidence-driven.

## CLI

```bash
semzero assumption-dashboard \
  --receipt-dir data/semzero_assumption_gate \
  --feedback-file data/semzero_assumption_gate/assumption_feedback.jsonl \
  --output data/semzero_assumption_gate/assumption_dashboard.json \
  --markdown-output data/semzero_assumption_gate/assumption_dashboard.md
```

The Markdown report now includes a **Policy calibration recommendations** section.

## Current scope

The active adapter remains only:

```text
adapter: dbt_assumption_gate
domain: data
```

Future Terraform/Kubernetes adapters should feed the same typed receipt and blast-radius model later, but they are intentionally not part of this release.
