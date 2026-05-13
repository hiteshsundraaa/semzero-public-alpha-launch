select order_id, case when status = 'paid' then amount when status = 'refunded' then -amount end as net_revenue from {{ ref('stg_orders') }}
