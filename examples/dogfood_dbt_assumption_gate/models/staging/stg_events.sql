select convert_timezone('UTC','America/New_York', event_ts) as event_ts, user_id, status, updated_at from raw.events
