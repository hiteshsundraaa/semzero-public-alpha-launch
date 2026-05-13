# Competitive positioning

SemZero is not trying to be a better data diff, a replacement observability platform, or a generic lineage viewer.

SemZero's wedge is:

> PR review for hidden assumptions in dbt changes.

## Adjacent categories

| Category | Primary question |
|---|---|
| Data diffing | What output data changed? |
| Lineage | What depends on what? |
| Observability | What broke or drifted in production? |
| Data contracts | What explicit expectations were declared? |
| SemZero | What hidden downstream assumption did this PR risk violating before merge? |

## Versus data diffing

A data diff can show:

```text
daily_revenue changed by 2.8%
```

SemZero should explain:

```text
This changed because the PR altered the reporting-day assumption behind DATE(event_ts).
```

Data diff is evidence. Assumption classification is interpretation.

SemZero can use data diff, Replay Lite, or warehouse-history evidence as inputs, but its product layer is the assumption-aware review action:

```text
diff/replay evidence → assumption family → blast radius → review recommendation
```

## Versus observability

Observability usually starts after production drift, alerting, or breakage.

SemZero starts before merge:

```text
This PR looks schema-compatible, but it changes an assumption downstream assets depend on.
```

## Versus lineage-only tools

Lineage tells reviewers where a change may propagate.

SemZero adds why the propagation matters:

- temporal bucket meaning changed
- join grain may fan out
- incremental predicate may select too much or too little
- enum mapping may be incomplete
- null fallback may change metric semantics
- materialization scope may become expensive

## Versus data contracts

Data contracts encode known expectations.

SemZero's long-term opportunity is to discover implicit expectations that were never written down and suggest promoting them into tests/contracts.

## Positioning line

Use:

```text
SemZero is an assumption-aware PR review layer. It explains which hidden assumption changed, who depends on it, what evidence exists, and what validation the reviewer should run before merge.
```

Avoid:

```text
SemZero replaces data diffing, observability, or lineage tools.
```
