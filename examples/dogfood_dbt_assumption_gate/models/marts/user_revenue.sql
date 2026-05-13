select u.account_id, sum(o.amount) as revenue from {{ ref('stg_orders') }} o join {{ ref('dim_accounts') }} u on o.account_id = u.account_id group by 1
