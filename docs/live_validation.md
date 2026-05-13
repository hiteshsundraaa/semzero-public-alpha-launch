# Live SQL Validation Quickstart

SemZero can now validate against a **heavy live PostgreSQL prototype database** without mutating the source tables directly.

## What it does

`semzero validate-e2e` can now:
- seed a PostgreSQL validation pack into a target schema/database
- crawl the schema and build the graph
- run PreGate, Wind Tunnel, and Chaos
- execute the hardest validation scenarios inside an isolated **shadow schema**
- export JSON, Markdown, and HTML reports comparing **predicted vs actual** behavior

## Recommended setup

Use a **dedicated PostgreSQL validation database** or a disposable clone/subscriber.
Do **not** point the demo pack seeding flow at shared production schemas.

## Example

```bash
export SEMZERO_DB_URL='postgresql+psycopg://user:password@host:5432/semzero_validation'

semzero validate-e2e \
  --db-url "$SEMZERO_DB_URL" \
  --demo-pack-dir data/live_validation_pack \
  --demo-backend postgres \
  --source-schema public \
  --demo-scale large \
  --output data/live_validation_report.json \
  --markdown-out data/live_validation_report.md \
  --html-out data/live_validation_report.html
```

## What gets validated

The built-in live validation pack currently focuses on five failure families:

- `silent_truncation`
- `domain_enum_drift`
- `temporal_timezone_mismatch`
- `blank_string_fanout`
- `incremental_ghost`

## Important notes

- PostgreSQL validation uses **shadow schemas** for destructive runtime checks.
- The live validation pack assumes the target database is dedicated to validation or otherwise safe to reseed.
- The harness currently gives the strongest runtime realism on PostgreSQL and SQLite in this repo. Snowflake zero-copy clone support and Databricks SHALLOW CLONE/query-history support are wired into the runtime, but validating those paths here still requires real warehouse credentials and connector access.
