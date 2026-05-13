# SemZero Core v1.1 — Assumption Gate Hardening

This patch keeps SemZero focused on the core product: an assumption-aware dbt PR gate.
It does **not** add Terraform, Kubernetes, RGCN, Chaos Mode, Streaming Gate, full Wind Tunnel, or repair automation.

## What changed

### 1. Domain-neutral evidence model

Added `semzero/core/evidence_model.py` and mirrored it under `src/core/evidence_model.py`.

The new internal object model is adapter-ready while staying core-only:

- `BlastRadiusNode`
- `EvidenceFinding`
- `GateReceipt`

The current active adapter is still only:

```text
adapter: dbt_assumption_gate
domain: data
```

This prevents later cross-domain adapters from requiring a receipt rewrite.

### 2. Typed blast-radius nodes

Assumption Gate findings now emit typed nodes such as:

```json
{
  "node_type": "dbt_model",
  "type": "dbt_model",
  "name": "finance_daily_revenue",
  "unique_id": "model.demo.finance_daily_revenue",
  "domain": "data",
  "path": "models/marts/finance_daily_revenue.sql",
  "criticality": "high"
}
```

The old `type` key remains as a compatibility alias, but `node_type` is now the preferred field.

### 3. Adapter/domain metadata in receipts

Assumption Gate receipts are now emitted as:

```text
receipt_kind: dbt_assumption_gate_v1_1
schema_version: semzero.evidence.v1
adapter: dbt_assumption_gate
domain: data
```

Each finding also carries `domain` and `adapter`.

### 4. Lightweight assumption dashboard

Added:

```bash
semzero assumption-dashboard
```

Example:

```bash
semzero assumption-dashboard \
  --receipt-dir data \
  --output data/assumption_dashboard.json \
  --markdown-output data/assumption_dashboard.md
```

The dashboard aggregates:

- run count
- assumption finding count
- would-require-review count
- family counts
- severity counts
- domain counts
- adapter counts
- rough cost exposure
- most-exposed blast-radius nodes
- top findings

This is separate from the older broad `shadow-dashboard`. The new dashboard is narrower and assumption-first.

## What intentionally stayed out

Adapters are not implemented in this patch.

Deferred:

- Terraform adapter
- Kubernetes adapter
- cross-domain graph
- RGCN / Graph ML
- Chaos Mode
- full Wind Tunnel
- repair automation
- Streaming Gate expansion

## Current product shape

SemZero remains:

> Assumption Gate + Blast Radius + Evidence Receipt + PR Comment + Shadow Dashboard

The architecture is now safer for future adapters, but the product remains focused on the dbt/data wedge.
