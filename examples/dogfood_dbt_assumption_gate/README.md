# SemZero Dogfood dbt Assumption Gate Fixture

This fixture is a local, offline mini dbt project designed to demonstrate the focused SemZero core without needing a warehouse, GitHub token, or external service.

It covers the five core assumption families:

| Scenario | Expected family | What it represents |
|---|---|---|
| `01_temporal_bucket_timezone` | `temporal_bucket` | A timezone conversion can silently shift records across `DATE(event_ts)` daily buckets. |
| `02_incremental_filter_cost` | `incremental_filter` | A weakened incremental predicate can reduce partition pruning and increase warehouse cost. |
| `03_join_fanout` | `join_cardinality` | A join key change without uniqueness evidence can inflate aggregates. |
| `04_enum_closure` | `enum_domain_closure` | A new valid status can be silently unhandled by `CASE`/`IN` logic. |
| `05_null_fallback` | `null_default_fallback` | New null semantics can be hidden by `COALESCE(..., 0)`. |

## Run every scenario

From the repository root:

```bash
python scripts/run_dogfood_assumption_gate.py
```

Outputs are written to:

```text
examples/dogfood_dbt_assumption_gate/receipts/
examples/dogfood_dbt_assumption_gate/comments/
examples/dogfood_dbt_assumption_gate/assumption_dashboard.json
examples/dogfood_dbt_assumption_gate/assumption_dashboard.md
examples/dogfood_dbt_assumption_gate/dogfood_run_summary.json
```

## Run one scenario through the CLI

```bash
semzero assumption-gate \
  --dbt-manifest examples/dogfood_dbt_assumption_gate/target/manifest.json \
  --changed-file models/staging/stg_events.sql \
  --changed-diff examples/dogfood_dbt_assumption_gate/scenarios/01_temporal_bucket_timezone.diff \
  --table-sizes examples/dogfood_dbt_assumption_gate/table_sizes/table_sizes.json \
  --output data/dogfood_temporal_receipt.json \
  --comment-out data/dogfood_temporal_comment.md
```

## Why this fixture exists

This is not a benchmark claim. It is a dogfooding/demo pack for testing whether the core loop is understandable end-to-end:

```text
dbt manifest + changed PR diff
→ trigger-linked hidden assumption
→ typed blast radius
→ stable finding ID
→ receipt
→ PR-ready comment
→ shadow dashboard
```

The fixture intentionally stays core-only. It does not add Terraform/Kubernetes adapters, streaming checks, RGCN, Chaos Mode, repair automation, or full Wind Tunnel.
