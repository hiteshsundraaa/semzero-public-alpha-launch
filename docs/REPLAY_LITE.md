# Replay Lite

Replay Lite is SemZero's **local, fixture/sample-based validation layer** for hidden assumptions.

It does **not** connect to a live warehouse in the OSS alpha. Replay Lite does not connect to a live warehouse, require credentials, or run production queries in the OSS alpha.

## What Replay Lite is

Replay Lite takes supplied local evidence and runs a narrow family-specific check:

- sample timestamps for temporal bucket drift
- before/after selected-row counts for incremental filter drift
- sample left/right rows for join fanout
- sampled domain values for enum closure
- sampled rows for null/default fallback
- precomputed row-scope counts for materialization cost

The demo result:

```text
Using supplied local sample evidence, 2/4 sampled rows moved reporting bucket.
```

means SemZero evaluated the sample rows in `replay_fixtures.json`. It does **not** mean SemZero queried Snowflake, BigQuery, Databricks, Postgres, or any production warehouse.

## What Replay Lite is not

Replay Lite is not:

- a live database connector
- a warehouse clone
- a production query runner
- a substitute for full data diffing
- proof that the same percentage holds across production data
- an automatic blocker

## Auth and connection story

Current OSS alpha:

```text
Warehouse auth required: no
Database connection required: no
Production query execution: no
Secrets required: no
Evidence source: local fixture/sample/precomputed file
```

Typical input:

```bash
semzero assumption-gate \
  --dbt-manifest target/manifest.json \
  --changed-file models/staging/stg_events.sql \
  --changed-diff pr.diff \
  --replay-fixtures .semzero/replay_fixtures.json
```

The receipt marks Replay Lite evidence with:

```json
{
  "evidence_source": "local_fixture_or_sample",
  "requires_live_database": false,
  "requires_credentials": false
}
```

## Why use local fixtures?

The alpha is designed to be safe and easy to adopt:

- no credentials needed
- no read access to production data
- works in normal GitHub Actions
- evidence files can be reviewed in code review
- teams can start in shadow mode

## Future connector roadmap

Managed/pro versions may add read-only connectors for:

- Snowflake query history
- BigQuery job history
- Databricks SQL/job history
- sampled before/after replay packs
- managed evidence storage

Those connectors should remain read-only, explicit, auditable, and opt-in.

## How to interpret Replay Lite

Strong wording:

```text
Replay Lite using supplied local sample evidence detected drift.
```

Overclaiming wording to avoid:

```text
SemZero queried your warehouse and proved production drift.
```

Replay Lite is evidence, not omniscience. It helps reviewers decide what to validate before merge.
