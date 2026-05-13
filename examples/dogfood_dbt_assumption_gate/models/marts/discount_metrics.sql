select date(updated_at) as day, sum(coalesce(discount_amount, 0)) as discount_total from {{ ref('stg_orders') }} group by 1
