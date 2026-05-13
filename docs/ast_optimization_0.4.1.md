# SemZero 0.4.1 — AST Optimization and Signal Hardening

## What changed

This release hardens the AST/proof surface while reducing matching overhead.

### Faster proving
- Added a module-level source parse cache keyed by `(path, mtime_ns, size)`.
- Added an inverted token index so drift events only inspect candidate sources that actually mention relevant assets.
- Added summary metrics for indexed token count, candidate source count, full-scan fallbacks, and parse cache size.

### Stronger cross-modal proof
- Added dbt YAML/schema parsing for model contracts, tests, tags, and exposures.
- Added stronger Python lineage extraction for:
  - `rename(columns=...)`
  - `assign(...)`
  - DataFrame `.query(...)` filters
  - f-string SQL detection
- Preserved existing SQL/dbt macro/Jinja proofing and macro blast-radius handling.

### Higher-value findings
- AST findings now surface dbt contract risk separately from generic filter drift.
- YAML contract/test surfaces now contribute to AST proofing and severity scoring.

## Validation
- Full test suite: `147 / 147 passed`
- Targeted AST additions validated for:
  - dbt YAML contracts and exposures
  - contract-backed AST findings
  - Python rename/assign/query lineage
  - macro blast radius and incremental SQL proofing

## Version hygiene
- Release bumped to `0.4.1`
- Release lineage preserved and extended from prior validated builds.
