# Category Strategy: Assumption-Aware PR Review

SemZero should own one narrow category first:

> PR review for hidden assumptions in dbt changes.

The product should not lead as a broad data reliability platform. The first wedge is the pull request moment: a SQL/model change is about to merge, and existing CI/schema checks cannot tell reviewers what downstream SQL was silently assuming.

## What SemZero should be best at

1. High-precision dbt assumption detection.
2. Reviewer-native PR comments.
3. Concrete trigger evidence.
4. dbt blast radius through models, exposures, owners, and tags.
5. Replay Lite validation when sample evidence is available.
6. Typed receipts that make findings auditable.
7. Calibration through feedback, exceptions, and shadow-mode history.

## What to avoid

- generic data quality positioning
- generic observability positioning
- leading with AI or platform language
- weak detector breadth over precision
- auto-blocking by default
- automatic SQL repair in the public alpha path

## Core category sentence

> Your schema diff says what changed. SemZero says what downstream SQL was silently assuming.
