# Compiler-Grade Lineage Core — 0.6.0

SemZero 0.6.0 introduces a new compiler-style lineage kernel for the SQL/dbt path.

## What changed
- Added `semzero.integrations.compiler_lineage.SQLCompilerLineage`
- Builds exact derivation graphs for:
  - top-level `SELECT` projections
  - nested CTE chains
  - alias resolution for `FROM` / `JOIN`
  - dbt `ref()` / `source()` / `this`
  - macro-definition and macro-call capture
  - filter-column propagation through `WHERE` / `HAVING` / `QUALIFY` / `ON`
- Emits provenance classes per output column:
  - `exact`
  - `exact+inferred`
  - `inferred`
  - `constant`
  - `wildcard`
- Integrates exact lineage hits into AST proofing, Gate evidence, and receipts.

## Why this matters
The previous AST layer was fast and operationally useful, but mostly evidence-driven.
0.6.0 moves the SQL/dbt path closer to compiler-grade behavior by deriving exact output-to-source column relationships instead of only matching references heuristically.

## Performance posture
This was designed to stay pre-merge friendly:
- cached compilation via `lru_cache`
- incremental source scanning still handled by the AST prover's token index + parse cache
- exact lineage only deepens the SQL/dbt path; other languages still use the lighter cross-modal evidence layer

## Honest limitation
This is a true semantic jump for **SQL/dbt**, but not yet a universal compiler-grade lineage engine for:
- Python interprocedural dataframe flows
- TS/JS/Prisma type-resolution-backed lineage
- full warehouse-dialect semantic normalization

The current state is best described as:
- **compiler-grade SQL/dbt lineage core**
- **hybrid cross-modal lineage stack overall**
