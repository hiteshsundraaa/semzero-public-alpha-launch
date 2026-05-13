# SemZero Core v1.13 — Business Criticality + Advisory Control Coverage

This release stays core-only. It does not add Terraform/Kubernetes adapters, cross-domain graph, RGCN, Chaos Mode, Streaming Gate, full Wind Tunnel, or repair automation.

## Added

### Business-criticality weighting

SemZero can now enrich dbt blast-radius nodes with business context using either an explicit registry or conservative keyword inference.

Optional registry:

```yaml
nodes:
  exposure.analytics.executive_revenue_dashboard:
    severity: BOARD_CRITICAL
    label: Executive Revenue Dashboard
    reason: Board-facing revenue dashboard used in monthly operating review.
```

Command:

```bash
semzero assumption-gate \
  --dbt-manifest target/manifest.json \
  --changed-file models/staging/stg_events.sql \
  --criticality-registry .semzero/business_criticality.yml \
  --output data/receipt.json \
  --comment-out data/comment.md
```

Findings remain non-blocking by default. Business criticality raises precision and prioritization, not automatic enforcement.

### Cybersec/DevOps-inspired control coverage

Each finding now includes advisory-only `control_coverage` metadata:

```json
{
  "kind": "assumption_control_coverage_v1",
  "status": "weak",
  "present_controls": [],
  "missing_controls": ["before/after temporal bucket comparison"],
  "experimental_note": "Cybersec/DevOps-inspired control coverage: advisory-only, used to improve calibration without blocking."
}
```

This borrows the idea of control coverage from cybersec/GRC, but applies it to hidden SQL assumptions: uniqueness tests, ELSE branches, temporal bucket comparison, null-rate checks, and warehouse cost profiles.

### Incident-chain trace

Each finding now includes an advisory incident chain:

```text
changed/scanned dbt resource → hidden assumption family → downstream business-critical node
```

This borrows from attack-path / incident-chain reasoning in cybersec and SRE, but applies it to data PRs. It is used for explanation and calibration, not blocking.

## Why this matters

The same assumption can have very different priority depending on blast radius:

- internal staging model only → advisory
- finance model → higher priority
- board/executive dashboard → review-worthy even in shadow mode

This makes SemZero more precise without making it stricter.
