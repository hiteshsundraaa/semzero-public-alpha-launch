# SemZero demo walkthrough

This walkthrough shows the core SemZero workflow without GitHub setup.

## Story

A dbt PR changes timestamp handling in `models/staging/stg_events.sql`:

```diff
-  event_ts,
+  convert_timezone('UTC', 'America/New_York', event_ts) as event_ts,
```

The change looks safe because the column remains a timestamp and SQL still compiles.

But downstream finance SQL contains:

```sql
select
  date(event_ts) as reporting_day,
  sum(amount_usd) as revenue_usd
from analytics.stg_events
group by 1
```

That downstream model silently assumes `date(event_ts)` maps records to the same reporting day before and after the PR.

## Run the demo

```bash
python scripts/run_killer_demo.py
```

## What SemZero does

```text
dbt PR diff
→ temporal-bucket assumption extraction
→ assumption diff
→ Replay Lite validation
→ dbt exposure blast radius
→ trust receipt
→ PR-style comment
```

## Expected CLI output

```text
SemZero killer demo PR
=======================
Verdict: REQUIRE_REVIEW
Findings: 1
Family: temporal_bucket
Severity: critical · risk 100/100
Replay Lite: drift_detected
Replay summary: Using supplied local sample evidence, 2/4 sampled rows moved reporting bucket under the supplied timezone/date-boundary replay.
Blast radius:
- executive_revenue_dashboard (dbt_exposure)
Generated:
- examples/killer_demo_pr/output/receipt.json
- examples/killer_demo_pr/output/comment.md
```

## Expected PR comment excerpt

The generated comment is stored at:

```text
examples/killer_demo_pr/output/comment.md
```

It shows:

```text
Temporal Bucket — critical · confidence high · risk 100/100
Assumption drift: Daily/hourly reporting bucket meaning may differ before vs after this PR.
Validation replay: drift_detected · Using supplied local sample evidence, 2/4 sampled rows moved reporting bucket...
Blast radius: executive_revenue_dashboard
Reviewer check: Run a before/after bucket comparison by day and timezone...
```

## Why this is the SemZero wedge

A schema diff tells reviewers what changed.

SemZero tells reviewers what downstream SQL was silently assuming.

That is why the public product is now focused on the dbt PR Assumption Gate rather than a broad reliability platform.
