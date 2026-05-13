# SemZero Assumption Lineage Lite v1.23

This release adds a receipt-derived assumption lineage graph for the focused dbt Assumption Gate.

It remains core-only and advisory-only. It does not add Terraform/Kubernetes adapters, a cross-domain platform graph, RGCN, Chaos Mode, full Wind Tunnel, or repair automation.

## Command

```bash
semzero assumption-lineage \
  --receipt-dir examples/dogfood_dbt_assumption_gate/receipts \
  --output examples/dogfood_dbt_assumption_gate/assumption_lineage.json \
  --markdown-output examples/dogfood_dbt_assumption_gate/assumption_lineage.md
```

## What the graph contains

Nodes:

- assumption nodes keyed by stable finding ID
- dbt source/model/exposure blast-radius nodes
- receipt nodes
- Replay Lite validation nodes, when present
- feedback nodes, when present
- exception nodes, when present

Edges:

- source resource `contains_assumption` assumption
- assumption `exposes` downstream blast-radius node
- assumption `evidenced_by` receipt
- assumption `validated_by` replay
- assumption `calibrated_by_feedback` feedback
- assumption `annotated_by_exception` exception

## Why this matters

This turns SemZero from a flat findings list into a lightweight assumption graph:

```text
assumption → source model → downstream exposure/dashboard → business criticality → replay status → feedback/exception state
```

That is the foundation for future assumption lineage and assumption decay tracking without expanding into a broad platform too early.
