# SemZero 0.6.0 — Compiler Lineage Core

## Headline
0.6.0 adds a compiler-style SQL/dbt lineage core and wires exact provenance into AST proofing receipts.

## Major additions
- `compiler_lineage.py` semantic kernel
- exact lineage pairs surfaced in AST assets
- proof findings now include:
  - `exact_lineage_hits`
  - `lineage_provenance`
- proof summary now reports exact-lineage-backed findings

## Validation
- full suite: 167 / 167 passed
- validate-e2e rerun on large messy demo pack
- premerge rerun with Gate + Wind Tunnel + Chaos

## Output artifacts
See `validation_artifacts/run_0.6.0_compiler_lineage/` for the latest validation run.
