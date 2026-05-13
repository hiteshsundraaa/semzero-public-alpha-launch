# SemZero 0.8.0-alpha — Product-Core Cleanup

This release narrows the public product surface around the dbt PR Assumption Gate.

## Hero path

- `semzero init-assumption-ci`
- `semzero assumption-ci`
- `semzero assumption-gate`
- `semzero assumption-dashboard`

## Kept in core

Strong modules retained because they improve hidden-assumption detection, validation, blast radius, receipts, PR review, calibration, cost savings, or noise control:

- Assumption extraction and assumption diffing
- Replay Lite targeted validation
- dbt manifest/catalog/run-results enrichment
- Snowflake/Databricks offline history and cost profiles
- typed evidence receipts and stable finding IDs
- feedback, exceptions, freshness, precision, dashboard calibration
- Assumption Lineage Lite, Decay Tracking Lite, Drift Memory Lite

## Experimental / legacy positioning

The broader platform surfaces remain in the repository for research and compatibility, but are not the first-time user path:

- full Wind Tunnel / warehouse replay
- Chaos Mode
- streaming shadow gate
- GNN / RGCN experiments
- broad premerge validation packs
- repair automation

## Hygiene

- Version aligned to `0.8.0a0`.
- README rewritten around the 10-minute dbt workflow.
- Generated caches/runtime outputs excluded from the release package.
- Missing legacy command-surface fixtures restored as small deterministic test fixtures.
- Full test suite verified: `235 passed`.


## 0.8.0a2 — Public-alpha trust layer

This release adds GitHub-public readiness around the dbt PR Assumption Gate:

- test, quality, and release-check GitHub workflows
- community files and issue templates
- public quickstart smoke tests
- public command-surface regression tests
- README install/status/testing sections
- release checklist
- version and dependency cleanup

The product remains alpha and shadow/advisory-first.


## 0.8.0a2 — Credibility clarification

Clarifies Replay Lite truth/auth story, false-positive suppression strategy, competitive positioning, and tagline discipline. Replay Lite is explicitly documented and emitted as local fixture/sample evidence in the OSS alpha, not live warehouse execution.
