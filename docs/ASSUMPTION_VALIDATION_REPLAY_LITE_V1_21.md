# SemZero Core v1.21 — Assumption Validation Replay Lite

This release adds targeted local validation replay for SemZero Assumption Gate findings. It does **not** clone Snowflake, Databricks, or production warehouses. It validates the specific assumption family using supplied local fixture/sample data or precomputed counts.

Supported families:

- temporal_bucket: bucket movement under timezone/date-boundary samples
- incremental_filter: old vs new selected row counts
- join_cardinality: join fanout ratio from sample left/right rows
- enum_domain_closure: unhandled sampled domain values
- null_default_fallback: rows masked by fallback values
- materialization_cost: old vs new processed row scope

CLI usage:

```bash
semzero assumption-gate \
  --dbt-manifest target/manifest.json \
  --changed-file models/marts/incremental_events.sql \
  --changed-diff pr.diff \
  --replay-fixtures replay_lite_samples.json \
  --output data/receipt.json \
  --comment-out data/comment.md
```

The receipt adds `validation_replay` per finding and `validation_replay_summary` at summary level. Replay Lite is advisory/non-blocking and is intentionally labelled as fixture/sample-based evidence, not full warehouse replay.
