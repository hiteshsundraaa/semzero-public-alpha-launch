# SemZero 0.7.5 — Shadow Trends and Enforcement Recommendations

This release turns shadow mode from a single-run proof artifact into a rollout-readiness system.

## Added

- Repo-level and team-level shadow trend aggregation.
- Weekly and monthly shadow trend history.
- Confidence-tier enforcement recommendations:
  - `TIER_0_SHADOW_ONLY`
  - `TIER_1_ADVISORY`
  - `TIER_2_REQUIRE_REVIEW`
  - `TIER_3_SELECTIVE_BLOCK`
- Suggested policy slices for hard-block, require-review, and advisory rollout.
- `semzero shadow-trends` CLI command for quick terminal review.
- `--repo` and `--team` metadata on `semzero shadow` / `semzero premerge`.
- `--repo`, `--team`, and `--risk-category` metadata on `semzero shadow-feedback`.
- Dashboard HTML now includes enforcement recommendations plus repo/team trend tables.

## Why it matters

SemZero can now start in shadow mode, collect real evidence, and then recommend which repos or teams are ready for advisory mode, review-required mode, or selective blocking. This makes enforcement gradual, calibrated, and trust-building instead of abrupt.

## Validation notes

Focused syntax and dashboard aggregation validation was performed with isolated Python loading to avoid optional dependency import side effects in the local container. Shadow dashboard logic was exercised against multi-run repo/team fixtures including feedback-backed precision and enforcement tier recommendations.
