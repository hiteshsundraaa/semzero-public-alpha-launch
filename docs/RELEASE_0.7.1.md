# SemZero 0.7.1 — Moat Hardening, Contract Pressure, and End-to-End Stress Validation

0.7.1 hardens the blue-ocean core of SemZero rather than widening the surface area.

## What changed

### 1. Assumption Gate hardening
- Added stronger extraction for grain/dedup, freshness windows, CASE/status mappings, null-check logic, and time-window assumptions.
- Added per-finding severity, consumer-surface classification, and contract hints.
- Added summary-level risk scoring, critical-finding rollups, and explicit contract recommendations.

### 2. Merge gate hardening
- Fixed finalization order so FinOps receipts are built before reliability and on-call risk are scored.
- Reliability now penalizes assumption risk score and critical assumption density, not just raw finding count.
- Execution plans now surface:
  - `contract_updates_required`
  - `contract_recommendations`
  - `priority_assumption_nodes`
  - `targeted_test_modes`
  - `assumption_risk_score`
- Assumption-heavy changes now escalate toward targeted replay / regime testing more aggressively.

### 3. FinOps moat hardening
- Added deeper static anti-pattern coverage for:
  - UNION ALL fan-in
  - deep CTE stacks
  - join-then-dedup shapes
  - expensive random/sample patterns
  - regex-heavy multi-join pipelines
  - semi-structured explode paths
- Runtime query FinOps estimation now also prices union fan-in, join-then-dedup, and explode-heavy paths.

## Validation

### Automated tests
- `pytest -q`
- Result: **181 passed**

### Stress validation loops
Ran `validate-e2e` twice each on:
- `finance`
- `messy`
- `chaos_labyrinth`
- `black_swan`

Observed results:
- finance: 10/10 aligned predictions, 18 queries replayed, 1 broken, 1 mismatch, recoverability 100
- messy: 10/10 aligned predictions, 18 queries replayed, 1 broken, 1 mismatch, recoverability 100
- chaos_labyrinth: 10/10 aligned predictions, 22 queries replayed, 2 broken, 1 mismatch, recoverability 100
- black_swan: 10/10 aligned predictions, 27 queries replayed, 3 broken, 0 mismatch, recoverability 100

Artifacts:
- `validation_artifacts/run_0.7.1_moat_hardening/`

## Honest limitations
- Assumption extraction is still heuristic and source-pattern based.
- FinOps is still heuristic-first rather than warehouse-native exact costing.
- Wind Tunnel targeting is stronger via execution-plan pressure, but not yet a fully separate targeting engine.

0.7.1 is a depth release: it makes SemZero more confident as a merge-control product and less comparable to a generic diff/testing tool.
