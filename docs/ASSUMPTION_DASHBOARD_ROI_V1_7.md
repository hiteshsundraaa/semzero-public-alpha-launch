# SemZero Core v1.7 — Assumption Dashboard ROI Signals

This release keeps SemZero focused on the dbt Assumption Gate core. It does not add Terraform, Kubernetes, cross-domain graphing, RGCN, Chaos Mode, Streaming Gate, full Wind Tunnel, or repair automation.

## What changed

`semzero assumption-dashboard` now turns shadow-mode receipts and developer feedback into a more useful value dashboard.

It reports:

- rough cost exposure surfaced by assumption findings
- rough avoided cost from findings marked `fixed`
- validated cost exposure from findings marked `agree` or `fixed`
- accepted-risk cost exposure from findings marked `accepted_risk`
- fixed finding count
- accepted-risk finding count
- recurring assumption families
- family-level feedback counts
- daily receipt/finding/feedback trends
- most-exposed blast-radius nodes with family mix and cost exposure

## Important cost caveat

Cost numbers remain directional. They are derived from finding-level rough estimates produced by the Assumption Gate, not from live warehouse billing. The dashboard intentionally labels these as cost exposure / avoided-cost signals, not audited savings.

Avoided cost is counted only when a finding with rough cost exposure is marked `fixed` through `semzero assumption-feedback`.

## Example

```bash
semzero assumption-dashboard \
  --receipt-dir data/semzero_assumption_gate \
  --feedback-file data/semzero_assumption_gate/assumption_feedback.jsonl \
  --output data/semzero_assumption_gate/assumption_dashboard.json \
  --markdown-output data/semzero_assumption_gate/assumption_dashboard.md
```

## Why this matters

The dashboard is the trust and retention loop:

```text
receipt → developer feedback → fixed / accepted-risk history → ROI signal → advisory-mode confidence
```

The goal is not to claim perfect dollar savings. The goal is to show teams where SemZero repeatedly surfaced real, review-worthy assumptions and which findings actually led to fixes.
