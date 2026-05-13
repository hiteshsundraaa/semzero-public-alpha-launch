# SemZero Core v1.16 — Real dbt Artifact Hardening

This release keeps SemZero core-only and improves precision by reading more real dbt artifacts before making assumption-gate findings.

## New optional inputs

```bash
semzero assumption-gate \
  --dbt-manifest target/manifest.json \
  --dbt-catalog target/catalog.json \
  --dbt-run-results target/run_results.json \
  --project-dir . \
  --changed-file models/marts/incremental_events.sql
```

The same options are available on `semzero assumption-ci`.

## What SemZero uses them for

- `manifest.json`: canonical dbt graph, dependencies, exposures, tests, owners, tags, config, raw/compiled SQL.
- `catalog.json`: column metadata, relation metadata, and catalog stats where available.
- `run_results.json`: model status, execution time, adapter response, and basic runtime context.
- `compiled_path`: if `compiled_sql` is absent in the manifest, SemZero attempts to read compiled SQL from the dbt project/target directory.

## Why this matters

The Assumption Gate is only as precise as its context. v1.16 improves:

- blast-radius quality from real exposures/tests;
- join-cardinality confidence from dbt test resources;
- cost-risk credibility from runtime/catalog context;
- PR explanations with artifact coverage metadata;
- demo and shadow-pilot readiness without requiring live warehouse credentials.

## Guardrails

This still does not add Terraform, Kubernetes, full Wind Tunnel, RGCN, Chaos Mode, Streaming Gate, or repair automation. It only strengthens the dbt Assumption Gate core.
