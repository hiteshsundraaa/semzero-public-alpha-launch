<!-- semzero-assumption-gate -->
## SemZero Assumption Gate

**Verdict:** `REQUIRE_REVIEW` · **Mode:** `shadow` · **Findings:** `1`
**Changed dbt resources:** `1` · **Blast-radius resources:** `2`
**Business impact:** This finding reaches board critical assets: Executive Revenue Dashboard.
**Evidence fidelity:** average `0.82` · replay ran for `1` finding(s)
**Validation replay lite:** `1` replay(s), `1` drift signal(s)
**Assumption diffing:** `1` finding(s) have explicit before/after PR context

### Reviewer summary

- **Must review:** `1`
- **Useful advisory:** `0`
- **Accepted risk / active exceptions:** `0`
- **Needs feedback:** `1`

### Must review

1. **Temporal Bucket** — `critical` · confidence `high` · risk `100/100`
   - **Why now:** a/models/staging/stg_events.sql +++ b/models/staging/stg_events.sql @@ -1,6 +1,6 @@ select event_id, user_id, - event_ts, + convert_timezone('UTC', 'America/New
   - **Assumption drift:** Daily/hourly bucket meaning may differ before vs after this PR.
   - **Evidence fidelity:** `0.82 (high_static_history_fidelity)` · replay ran: `True`
   - **Validation replay:** `drift_detected` · Using supplied local sample evidence, 2/4 sampled rows moved reporting bucket under the supplied timezone/date-boundary replay.
   - **Blast radius:** dbt_exposure `executive_revenue_dashboard` (BOARD_CRITICAL)
   - **Business:** `BOARD_CRITICAL` · **Control coverage:** `weak` · **Detector:** `timezone_or_date_boundary_bucket`
   - **Stable ID:** `AG-TEMPORAL-BUCKET-8F338A2696`
   - **Reviewer check:** Run a before/after bucket comparison by day and timezone for the affected timestamp over a recent representative window, especially midnight-boundary records.

### Needs feedback

Please mark the reviewed finding(s) as `agree`, `fixed`, `accepted_risk`, or `false_positive` so the shadow dashboard can calibrate. Sample stable IDs: `AG-TEMPORAL-BUCKET-8F338A2696`.

_Full evidence is preserved in the JSON receipt. This compact comment is grouped for reviewer action, not exhaustive evidence display._