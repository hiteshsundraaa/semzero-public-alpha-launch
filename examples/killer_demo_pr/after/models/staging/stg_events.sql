select
  event_id,
  user_id,
  convert_timezone('UTC', 'America/New_York', event_ts) as event_ts,
  amount_usd
from raw.events
