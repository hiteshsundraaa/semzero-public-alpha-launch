select date(event_ts) as revenue_day, status, count(*) as orders, sum(amount) as revenue from {{ ref('stg_events') }} where status in ('paid','refunded') group by 1,2
