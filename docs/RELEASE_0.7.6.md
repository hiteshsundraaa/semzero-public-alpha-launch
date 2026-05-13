# SemZero 0.7.6 — Streaming Shadow Extension

This release adds a Kafka/streaming-oriented shadow extension so SemZero can speak directly to Confluent-style data infrastructure work.

## Added

- `semzero.integrations.streaming_gate.StreamingGate`
- mirrored implementation in `src.integrations.streaming_gate`
- new CLI command: `semzero streaming-shadow`
- example streaming fixtures under `examples/streaming/`
- tests in `tests/test_076_streaming_extension.py`

## Streaming risks covered

- removed fields used by consumers
- added required fields without defaults
- type changes across event schemas
- enum/status value removals
- event-time field changes
- lateness/watermark tolerance tightening
- partition/message key changes
- retention reduction
- Schema Registry compatibility weakening
- producer idempotence disabled
- consumer contract mismatches

## Product impact

SemZero now has a clearer story for Kafka, Confluent, streaming SQL, and real-time data teams:

> Run in shadow mode against topic/schema changes and consumer contracts to show which event-stream changes would have broken consumers, state stores, event-time windows, or replay guarantees before enforcement is enabled.

## Example

```bash
semzero streaming-shadow \
  --before examples/streaming/before_topics.json \
  --after examples/streaming/after_topics.json \
  --contracts examples/streaming/consumer_contracts.json \
  --repo stream-repo \
  --team stream-platform \
  --data-dir data
```

Outputs:

- `data/streaming_gate_result.json`
- `data/streaming_gate_report.html`
- `data/shadow_runs.jsonl`
- `data/shadow_dashboard.json`
- `data/shadow_dashboard.html`

## Honest limitations

- This is a static/shadow compatibility checker, not a live Kafka cluster inspector yet.
- It uses JSON topic/schema snapshots and consumer contracts rather than directly calling Schema Registry or Kafka Admin APIs.
- It does not replay actual Kafka messages yet; replay validation should remain a later extension.
