# SemZero Core v1.20 — Assumption Diff + Replay Fidelity

This release keeps SemZero core-only and non-blocking. It adds two trust-oriented layers to the focused dbt Assumption Gate:

1. **Assumption diffing** — static old-vs-new assumption drift summaries from PR diff context.
2. **Replay/evidence fidelity scoring** — an honest score describing how much evidence SemZero had and whether behavioral replay actually ran.

No full Wind Tunnel / behavioral replay is executed in v1.20. The fidelity score is explicitly an evidence-quality and replay-readiness score, not proof that semantic drift occurred.

## Why this matters

Assumption Gate should answer more than “what pattern was found?” It should explain:

- what assumption used to appear true,
- what assumption the PR may be changing,
- what validation should be run next,
- how trustworthy the available evidence is.

This moves SemZero toward the top-tier loop:

```text
extract assumptions
→ diff assumptions
→ validate assumptions
→ measure semantic drift
→ explain blast radius
→ emit trust receipt
```

v1.20 implements the diff/fidelity layers before full validation replay.

## New receipt fields

Each finding now includes:

```json
{
  "assumption_diff": {
    "kind": "semzero_assumption_diff_v1",
    "drift_type": "incremental_predicate_selectivity",
    "old_assumption": "Incremental predicate stayed selective and preserved pruning.",
    "new_assumption": "Predicate may be widened, wrapped, OR-expanded, or less selective.",
    "drift_summary": "Incremental boundary may select more rows or reduce pruning.",
    "has_explicit_before_after_diff": true,
    "advisory_note": "Assumption diff is static PR-context evidence, not behavioral replay."
  },
  "replay_fidelity": {
    "kind": "semzero_replay_fidelity_v1",
    "score": 0.72,
    "level": "medium_static_fidelity",
    "basis": ["compiled SQL available", "downstream blast radius attached"],
    "limitations": ["No before/after output replay was run in this version."],
    "replay_ran": false,
    "next_validation": "Compare rows selected by old vs new incremental predicates and estimate scan multiplier."
  }
}
```

## New receipt summaries

Receipt summaries now include:

- `assumption_diff_summary`
- `replay_fidelity_summary`

These are used in the PR comment and dashboard/reporting path.

## Reviewer comment behavior

The compact PR comment now surfaces:

- assumption drift summary,
- evidence fidelity score,
- whether replay actually ran,
- next reviewer validation check.

This is intentionally honest: v1.20 can say evidence is strong or weak, but it does not pretend to have replayed outputs.

## Scope guardrail

v1.20 is still advisory/non-blocking. It does not add hard merge blocking, full warehouse replay, cross-domain adapters, RGCN, Chaos Mode, or repair automation.
