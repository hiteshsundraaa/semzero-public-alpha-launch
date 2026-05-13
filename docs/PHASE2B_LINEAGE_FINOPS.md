# Phase 2B — Python Lineage + FinOps + Contract Hardening

## What changed

SemZero now keeps the fast indexed AST shell but adds an exact-er compiler-style dataframe lineage pass for the Python path.

The current strategy is:

1. cheap candidate narrowing
2. exact SQL/dbt lineage where impacted-zone precision matters
3. compiler-style Python dataframe lineage for read_sql/rename/assign/query/merge flows
4. provenance classes surfaced into receipts instead of silently mixing exact and inferred edges

## Why it matters

This improves pre-merge decisions in the surfaces where data teams actually work:

- dbt / SQL transformations
- pandas-style reconciliation and enrichment code
- stale filters and contract-sensitive downstream readers

## FinOps Gate additions

The FinOps Gate now highlights more merge-time cost drivers, including:

- select-star propagation
- cartesian/fanout joins
- unpartitioned qualify / window paths
- unbounded merge / incremental flows
- recompute radius amplification across downstream assets

## Contract enforcement additions

Contract enforcement now escalates for:

- privacy-tagged columns
- freshness-sensitive tables
- strict tables with destructive or semantic changes

## Validation profile

`chaos_labyrinth` extends the demo validation pack with:

- an additional sessions table
- wider join chains
- duplicate-prone session identifiers
- more irregular null / ended_at / campaign patterns
- Python and SQL proof assets that exercise cross-modal provenance
