# Public Alpha Launch Notes

## Positioning

SemZero is an alpha dbt PR Assumption Gate.

Use this headline:

> SemZero — PR review for hidden assumptions in dbt changes.

## Recommended public wording

SemZero comments on dbt pull requests when SQL/model changes may silently violate downstream assumptions, such as temporal buckets, incremental filters, join cardinality, enum closure, null fallback, or materialization cost.

## Alpha boundaries

Recommended:

- shadow mode in CI
- advisory PR comments
- artifact review
- manual approval for risky findings

Not recommended yet:

- automatic PR blocking in production
- automatic SQL repair
- unsupervised warehouse changes

## Repositories

Product repo:

```text
https://github.com/hiteshsundraaa/semzero
```

External demo repo:

```text
https://github.com/hiteshsundraaa/semzero-demo-dbt
```

## First release tag

```bash
git tag v0.8.0-alpha.1
git push origin v0.8.0-alpha.1
```
