select
  date(event_ts) as reporting_day,
  sum(amount_usd) as revenue_usd,
  count(*) as event_count
from {{ ref('stg_events') }}
group by 1
