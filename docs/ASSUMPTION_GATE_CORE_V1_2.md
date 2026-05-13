# SemZero Assumption Gate Core v1.2

This patch keeps SemZero focused on the core product: an assumption-aware dbt PR gate.

## Scope

Built now:
- dbt Assumption Gate
- typed blast-radius nodes
- domain-neutral evidence receipts
- PR-comment output
- assumption dashboard aggregation
- stronger noise controls and why-now trigger evidence

Still deferred:
- Terraform adapter
- Kubernetes adapter
- cross-domain graph
- RGCN / graph ML
- Chaos Mode
- Streaming Gate
- full Wind Tunnel
- repair automation

## New in v1.2

### Trigger evidence

Findings now include concrete `trigger_evidence` excerpts showing why the pattern is relevant for this PR. This avoids turning the gate into a generic SQL linter.

### Risk score and confidence

Every finding now includes:

```json
{
  "confidence": "high",
  "risk_score": 95,
  "noise_controls": [
    "pattern emitted only because a related changed-resource trigger was present",
    "finding is tied to changed resource or transitive downstream resource"
  ]
}
```

These are deterministic explanatory fields, not ML scores.

### Better join noise control

Join-cardinality findings now inspect dbt test resources connected through the manifest. Unique/relationship-style tests reduce severity where present.

### Optional diff input

The CLI accepts optional unified diff text or a diff file:

```bash
semzero assumption-gate \
  --dbt-manifest target/manifest.json \
  --changed-file models/staging/stg_events.sql \
  --changed-diff pr.diff \
  --output data/assumption_gate_receipt.json \
  --comment-out data/assumption_gate_comment.md
```

The diff is used only for stronger trigger evidence and explanation.

## Receipt kind

v1.2 receipts use:

```json
"receipt_kind": "dbt_assumption_gate_v1_2"
```

The dashboard accepts older v1 and v1.1 receipts as well.

## Product rule

SemZero should emit a finding only when:

```text
assumption pattern + related changed-resource trigger + changed/downstream context = finding
```

That is the core noise-control rule.
