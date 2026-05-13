# SemZero Core v1.17 — Offline Warehouse History Calibration

This release keeps SemZero core-only and non-blocking. It adds optional offline warehouse-history ingestion so Snowflake/Databricks/dbt cost estimates can be calibrated from exported history files without live credentials.

## New input

```bash
semzero assumption-gate \
  --dbt-manifest target/manifest.json \
  --changed-file models/marts/incremental_events.sql \
  --warehouse-history exports/snowflake_query_history.csv \
  --cost-profiles .semzero/cost_profiles.yml \
  --output data/receipt.json \
  --comment-out data/comment.md
```

`--warehouse-history` accepts CSV or JSON. Supported row fields include common aliases such as:

- `engine`, `model_name`, `unique_id`, `relation_name`, `query_text`
- `bytes_scanned`, `credits_used`, `cost_usd`, `total_elapsed_time`
- `dbu`, `dbu_hours`, `run_duration_seconds`, `execution_time`

The importer builds model-level profiles such as average runtime, average cost, average bytes scanned, credits, DBU, and sample count.

## What this improves

Cost findings can now include:

- `method: offline_history_per_run_multiplier`
- `history_calibrated: true`
- `warehouse_history.sample_count`
- `avg_runtime_seconds`
- `avg_bytes_scanned`
- `avg_cost_usd`
- `avg_credits_used` or `avg_dbu`

This makes the savings story more credible while avoiding live Snowflake/Databricks credentials.

## Guardrail

This remains directional and advisory. Offline history improves calibration, but SemZero does not claim audited billing precision. Teams should validate high-value findings against query profiles, billing dashboards, or job-run histories before changing enforcement policy.
