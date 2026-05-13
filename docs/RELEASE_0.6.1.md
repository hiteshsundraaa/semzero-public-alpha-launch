# SemZero 0.6.1 — Phase 2B Lineage, FinOps, and Contract Hardening

0.6.1 deepens the pre-merge control plane in four directions:

- stronger static and replay-aware FinOps receipts
- stricter contract enforcement for privacy and freshness-sensitive assets
- compiler-style Python dataframe lineage for impacted-zone analysis
- a stronger cross-modal provenance graph summary across SQL/dbt + Python proof surfaces

## Validation

- Full test suite: 172 / 172 passed
- Large messy `chaos_labyrinth` validation pack rerun end to end
- Additional 40-mutation Chaos stress run executed against the generated SQLite cloneable demo estate

See `validation_artifacts/run_0.6.1_phase2b/` for the latest receipts and reports.
