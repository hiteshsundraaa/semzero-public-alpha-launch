# SemZero 0.7.11 — dbt Assumption Gate v1

SemZero v1 is now centered on a narrower wedge:

> Catch the hidden SQL assumptions inside dbt PRs before they become broken dashboards, inflated metrics, or surprise warehouse bills.

This release intentionally does **not** expand the full platform surface. It adds a focused `semzero assumption-gate` path that reads a dbt `manifest.json`, maps changed dbt files to downstream resources, scans for trigger-linked hidden assumptions, and writes two artifacts:

1. `assumption_gate_receipt.json` — machine-readable evidence receipt.
2. `assumption_gate_comment.md` — GitHub/CI-ready PR comment.

## Command

```bash
semzero assumption-gate \
  --dbt-manifest target/manifest.json \
  --changed-file models/staging/stg_events.sql \
  --mode shadow \
  --output data/assumption_gate_receipt.json \
  --comment-out data/assumption_gate_comment.md
```

For CI systems that expose a newline/comma separated file list:

```bash
semzero assumption-gate \
  --dbt-manifest target/manifest.json \
  --changed-files "$CHANGED_FILES" \
  --mode shadow
```

Optional cost input:

```bash
semzero assumption-gate \
  --dbt-manifest target/manifest.json \
  --changed-file models/marts/events_incremental.sql \
  --table-sizes table_sizes.json
```

`table_sizes.json` can map either model names or dbt unique IDs to GB estimates:

```json
{
  "incremental_events": {"gb": 500},
  "model.analytics.fact_events": {"gb": 1200}
}
```

## What v1 detects

The first focused pattern library covers five families:

| Family | Hidden assumption | Example risk |
|---|---|---|
| `temporal_bucket` | Timestamp-to-date logic produces stable reporting buckets. | `DATE(event_ts)` silently shifts revenue across days after a timezone change. |
| `incremental_filter` | Incremental predicates bound scanned rows and preserve partition pruning. | `DATE(updated_at) >= ...` causes a much larger warehouse scan. |
| `join_cardinality` | Join keys preserve uniqueness/grain and do not fan out metrics. | A changed join key duplicates revenue rows. |
| `enum_domain_closure` | Status/type filters cover the full valid domain. | A new status is excluded from finance metrics. |
| `null_default_fallback` | Null/default fallback has stable business meaning. | `COALESCE(amount, 0)` hides upstream data loss. |

## Noise-control rule

The gate does **not** report every SQL pattern it sees. A finding requires:

```text
assumption pattern + related PR trigger + changed/downstream resource context
```

This is the core product discipline. `DATE(event_ts)` alone is not enough. `DATE(event_ts)` downstream of a PR that changes timestamp/timezone semantics is enough.

## Receipt shape

Receipts are assumption-first:

```json
{
  "receipt_kind": "dbt_assumption_gate_v1",
  "mode": "shadow",
  "verdict": "REQUIRE_REVIEW",
  "summary": {
    "finding_count": 3,
    "families": {"temporal_bucket": 1},
    "blast_radius_resource_count": 4
  },
  "findings": [
    {
      "family": "temporal_bucket",
      "severity": "critical",
      "assumption": "Timestamp-to-date logic produces stable reporting buckets across this change.",
      "trigger": "A related timestamp/timezone/date-bucketing change is present in the PR.",
      "blast_radius": [
        {"type": "exposure", "name": "executive_revenue_dashboard"}
      ],
      "recommended_check": "Run a before/after bucket comparison by day..."
    }
  ]
}
```

## Current limitations

- This is a deterministic v1 scanner, not ML.
- It relies on dbt manifest lineage quality.
- It gives rough cost estimates only when table-size input is supplied.
- It does not auto-fix code.
- It does not run full warehouse replay.
- The correct first deployment mode is `shadow` or `advisory`, not hard blocking.
