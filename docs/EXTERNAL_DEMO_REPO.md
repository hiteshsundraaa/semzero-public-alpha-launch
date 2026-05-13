# External Demo Repository

SemZero should be tested in two places:

1. The SemZero product repo itself.
2. A separate sample dbt repository that behaves like a user repo.

Recommended demo repo:

```text
https://github.com/hiteshsundraaa/semzero-demo-dbt
```

The demo repo should contain a tiny dbt project and a pull request that changes timestamp semantics while downstream finance SQL still groups by `date(event_ts)`.

## Why this matters

The external demo proves that SemZero works outside its own repository:

- install from GitHub
- run inside a different repo
- read a dbt manifest
- inspect PR diff
- produce a receipt
- produce a PR-style comment
- upload artifacts

## Suggested launch flow

1. Push `hiteshsundraaa/semzero`.
2. Push `hiteshsundraaa/semzero-demo-dbt`.
3. Create a branch in the demo repo that applies `pr.diff`.
4. Open a PR.
5. Confirm SemZero produces an advisory comment/artifact.
6. Link the demo PR from the SemZero README once public.
