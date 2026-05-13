# SemZero 0.7.10 — Calibration and Test Stability

This release intentionally avoids broad feature expansion. It focuses on a trust blocker exposed by the Datafold-differentiation benchmark: **expected or planned migrations were being over-downgraded to `ALLOW`**.

## Main changes

- Added `semzero-lab` as a separate internal monolithic CLI.
- Added Datafold-differentiation benchmark generation/evaluation commands.
- Added explicit expected-migration calibration rules.
- Added benchmark reports that highlight synthetic limitations and exact verdict calibration.
- Added pilot case-study and external pilot checklist templates.

## Expected-migration rule

Expected does **not** mean safe.

Planned risky changes should be routed as:

| Case | Recommended verdict |
|---|---|
| Planned migration with strong rollout evidence | `ADVISORY` |
| Planned migration with incomplete rollout evidence | `REQUIRE_REVIEW` |
| Planned migration with unresolved consumer/contract break | `REQUIRE_REVIEW` or `BLOCK` candidate |
| No risk signal | `ALLOW` |

## Validation summary

Validated directly in this release:

- `semzero-lab --version`
- benchmark generation
- benchmark run
- benchmark evaluation
- tabular feature export
- graph export
- targeted lab pytest
- targeted streaming/shadow pytest after dependency install

Synthetic benchmark results are calibration signals only. They are not real-world accuracy proof.
