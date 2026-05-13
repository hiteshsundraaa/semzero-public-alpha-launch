# SemZero Core v1.14 — Assumption Exceptions / Accepted Risk

This release adds an advisory-only exception workflow for the focused dbt Assumption Gate.

## Why

Teams need a safe way to say: “we saw this risk, we accept it temporarily, and we know when to revisit it.”

SemZero exceptions are deliberately different from deleting or hiding findings:

- findings remain in receipts
- exceptions require a reason
- exceptions can expire
- dashboards track active and expired exception debt
- PR comments show exception state
- exceptions do not create hard blocks

## Record an exception

```bash
semzero assumption-exception \
  --scope stable_id \
  --value AG-INCREMENTAL-FILTER-ABC123 \
  --reason "Accepted during controlled backfill; revisit after migration." \
  --owner data-platform \
  --expires-at 2026-06-01T00:00:00+00:00 \
  --ticket DATA-1842 \
  --exceptions-file data/assumption_exceptions.jsonl
```

Supported scopes:

- `stable_id`
- `family`
- `source`
- `receipt`
- `global`

## Use exceptions in the gate

```bash
semzero assumption-gate \
  --dbt-manifest target/manifest.json \
  --changed-file models/marts/incremental_events.sql \
  --exceptions-file data/assumption_exceptions.jsonl \
  --output data/assumption_gate_receipt.json \
  --comment-out data/assumption_gate_comment.md
```

Matching findings get an `exception` object such as:

```json
{
  "state": "active_exception",
  "active": [{"scope": "stable_id", "reason": "Accepted during controlled backfill"}],
  "expired": []
}
```

## Dashboard exception tracking

```bash
semzero assumption-dashboard \
  --receipt-dir data \
  --exceptions-file data/assumption_exceptions.jsonl
```

The dashboard reports:

- active exception records
- expired exception records
- findings matched by active exceptions
- findings matched by expired exceptions
- exceptions expiring within 14 days

## Guardrail

Exceptions are advisory accepted-risk records. They annotate findings for calibration; they do not delete evidence or automatically suppress all future enforcement.
