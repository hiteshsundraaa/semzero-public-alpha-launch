# SemZero Core v1.15 — Evidence Freshness and Stale-Risk Review

This release keeps SemZero core-only and non-blocking. It adds advisory freshness review for Assumption Gate receipts, high-risk findings, and accepted-risk exceptions.

## Added

- Receipt freshness states: `fresh`, `review_due`, `stale`, `unknown`.
- Dashboard freshness section with stale receipt count and review-due receipt count.
- High-risk unreviewed finding queue.
- Expired-exception high-risk tracking.
- Markdown dashboard section for stale-risk review.

## Guardrail

Freshness review does not block merges. It tells teams which receipts, exceptions, and high-risk findings should be revisited before policy promotion.

## Why it matters

This borrows a security/devops pattern: accepted risk and old evidence should expire or be reviewed. In SemZero, that means a finding accepted weeks ago remains visible as exception debt instead of disappearing.
