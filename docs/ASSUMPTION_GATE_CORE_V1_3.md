# SemZero Assumption Gate Core v1.3

This patch keeps SemZero focused on the core product: an assumption-aware dbt PR gate.

## Scope

Built now:
- dbt Assumption Gate
- typed blast-radius nodes
- domain-neutral evidence receipts
- PR-comment output
- assumption dashboard aggregation
- deeper assumption-pattern detail

Still deferred:
- Terraform adapter
- Kubernetes adapter
- cross-domain graph
- RGCN / graph ML
- Chaos Mode
- Streaming Gate
- full Wind Tunnel
- repair automation

## New in v1.3

### Deeper temporal-boundary detection

Temporal findings now include `pattern_detail` for timezone/date-boundary risk:

```json
{
  "pattern_type": "timezone_or_date_boundary_bucket",
  "granularity": "day",
  "timezone_conversion_in_changed_context": true,
  "day_boundary_bucket": true,
  "boundary_risk": "records near midnight can move between reporting buckets"
}
```

The goal is to catch the painful class of dashboard incidents where timestamp semantics change but queries keep running.

### Stronger incremental-filter weakening detail

Incremental findings now inspect optional PR diff text for removed/widened predicates and OR expansion:

```json
{
  "pattern_type": "incremental_predicate_pruning",
  "partition_column_wrapped": true,
  "or_expansion": true,
  "predicate_removed_or_widened_in_diff": true
}
```

This turns the warning from a generic incremental-model lint into a concrete why-now finding.

### Better join fanout detail

Join findings now label whether the query aggregates after a join, has dedup hints, and has dbt uniqueness/relationship hints:

```json
{
  "pattern_type": "join_grain_or_fanout",
  "aggregate_after_join": true,
  "dedup_hint_present": false,
  "dbt_uniqueness_or_relationship_hint_present": false,
  "aggregate_after_join_without_uniqueness": true
}
```

### Enum and null semantics details

Enum/domain findings now surface literal values and missing ELSE risk. Null/default findings now surface fallback values and whether a zero/unknown fallback may hide data loss.

## Receipt kind

v1.3 receipts use:

```json
"receipt_kind": "dbt_assumption_gate_v1_3"
```

The dashboard accepts v1, v1.1, v1.2, and v1.3 receipts.

## Product rule

SemZero still emits a finding only when:

```text
assumption pattern + related changed-resource trigger + changed/downstream context = finding
```

v1.3 deepens the evidence attached to that finding. It does not add adapters or broaden product scope.
