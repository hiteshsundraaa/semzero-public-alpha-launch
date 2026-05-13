# Real Shadow Pilot Case Study Template

Use this for each real PR during an external or internal SemZero shadow pilot.

## Context

- Company/team:
- Repo:
- PR number/link:
- Stack: dbt/Snowflake, dbt/Databricks, Kafka, other
- SemZero mode: shadow only / advisory / require-review / selective block

## PR summary

- What changed?
- Was the change planned?
- Was there a migration ticket or rollout plan?

## What a data diff would show

- No diff / small diff / large noisy diff / schema-visible diff
- Would the diff alone explain the risk? yes / no / partial

## SemZero finding

- Recommended verdict:
- Risk categories:
- Confidence:
- Affected assets:
- Evidence chain:
  1.
  2.
  3.

## Developer feedback

- Outcome: confirmed / useful / noisy / false_positive / fixed / expected
- Notes from engineer:
- Final PR action:

## Result

- Was the finding useful?
- Was it too noisy?
- Did it catch something ordinary tests/diffs did not explain?
- Should this category move toward advisory/review/block?

## Follow-up

- Product fix needed:
- Policy threshold adjustment:
- Additional adapter evidence needed:
