# SemZero 0.6.2 — Black Swan Stress Validation and Workload-Aware Chaos Targeting

0.6.2 deepens the validation side of SemZero rather than adding another surface feature.

## What changed
- Added a new **black_swan** demo validation profile with messier warehouse-style workflows:
  - subscription events
  - support tickets
  - refunds
  - more joins, windows, rollups, nulls, duplicate-prone session IDs, and expanded domains
- Added heavier workload coverage for black-swan stress runs.
- Tightened Chaos targeting so generic bare `id` columns are deprioritized and business-critical columns are weighted more heavily.
- Added validation tests for the new profile and CLI acceptance.

## Why this matters
SemZero should not always look perfect under synthetic validation. This release focuses on making the test estate more realistic and on surfacing the kinds of breakage that a production team would actually care about.

## Validation
- Full test suite: **174 / 174 passed**
- Black Swan validate-e2e run:
  - queries replayed: **27**
  - broken queries: **3**
  - aligned predictions: **10 / 10**
- Black Swan heavy chaos stress run:
  - mutations applied: **60**
  - workload tests run: **1120**
  - workload tests failed: **65**
  - fragility score: **95 / 100**
  - recoveries verified: **7 / 7**

See `validation_artifacts/run_0.6.2_black_swan/` for the latest receipts and reports.
