# SemZero Core v1.12 — Warehouse-aware Cost Profiles

This release keeps SemZero core-only: dbt Assumption Gate, blast radius, receipts, PR comments, feedback, dashboard, and policy calibration.

It does not add Snowflake or Databricks API integrations. Instead, it adds a lightweight `cost_profiles.yml` path so teams can attach credible directional cost context to dbt PR findings before connecting billing/query-history systems.

## What changed

### Cost profiles

`semzero assumption-gate` and `semzero assumption-ci` now accept:

```bash
--cost-profiles .semzero/cost_profiles.yml
```

Example:

```yaml
models:
  incremental_events:
    engine: snowflake
    table_size_gb: 500
    run_frequency: daily
    rough_cost_per_tb_scanned_usd: 24

  session_metrics:
    engine: databricks
    table_size_gb: 800
    run_frequency: hourly
    rough_cost_per_run_usd: 8
```

The estimates remain directional. They are meant to answer: “is this PR worth review before merge?”, not to replace warehouse billing.

### Monthly exposure

Receipts and dashboards now include both per-run and monthly exposure fields:

```json
{
  "estimated_extra_cost_per_run_usd": 117.19,
  "estimated_extra_cost_per_month_usd": 3515.7
}
```

The dashboard also rolls these into ROI fields such as:

- estimated cost exposure per month
- estimated avoided cost per month from fixed findings
- accepted-risk cost exposure per month
- validated cost exposure per month

### Engine-aware language

Cost findings now carry engine-specific notes:

- Snowflake: micro-partition pruning, warehouse runtime, credits
- Databricks: Delta file pruning, Spark scan/shuffle, DBU/runtime, MERGE bounds
- dbt: materialization, incremental predicate selectivity, downstream fanout

### Materialization cost family

A new focused assumption family was added:

```text
materialization_cost
```

It detects dbt full-refresh / replace-table / materialized-table changes that may rebuild full history unexpectedly.

## Reused from the old broad platform

This run reused ideas from the old broad SemZero platform without re-expanding scope:

- FinOps static-scan concepts: full-refresh paths, unbounded merge, fanout joins, wide scans.
- Business-impact thinking: cost findings matter more when blast radius includes finance/executive/revenue assets.
- Existing receipt/dashboard architecture: cost evidence remains in the same typed receipt loop.

The old broad modules are still not part of the customer-facing wedge. Their useful ideas were folded into the core dbt Assumption Gate.
