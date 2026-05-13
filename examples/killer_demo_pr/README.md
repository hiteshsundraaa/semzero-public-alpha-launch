# Killer demo PR: timezone change that silently shifts revenue days

This demo is the shortest way to understand SemZero.

The PR looks harmless: a staging model converts `event_ts` from UTC into local time.
The SQL still runs. The schema still looks compatible. But a downstream finance model groups revenue by `date(event_ts)`, which silently assumes the timestamp maps to the same reporting day.

SemZero catches the hidden assumption before merge.

## Run it

From the repo root:

```bash
python scripts/run_killer_demo.py
```

Generated files:

```text
examples/killer_demo_pr/output/receipt.json
examples/killer_demo_pr/output/comment.md
```

## What the demo contains

```text
before/models/staging/stg_events.sql     # old timestamp behavior
after/models/staging/stg_events.sql      # PR changes timezone behavior
models/marts/finance_daily_revenue.sql   # downstream SQL assumes date(event_ts)
target/manifest.json                     # tiny dbt manifest with exposure lineage
pr.diff                                  # the PR diff SemZero inspects
replay_fixtures/replay_lite_samples.json # local sample rows for Replay Lite
```

## Expected result

```text
SemZero found 1 hidden assumption.
Family: temporal_bucket
Replay Lite: drift_detected
Using supplied local sample evidence, 2/4 sampled rows moved reporting bucket
Blast radius: executive_revenue_dashboard
Verdict: REQUIRE_REVIEW in shadow mode
```

## Why this matters

A normal schema diff can say: `event_ts` is still a timestamp.

SemZero says: downstream SQL was silently assuming `date(event_ts)` still means the same reporting day. That is the product wedge.
