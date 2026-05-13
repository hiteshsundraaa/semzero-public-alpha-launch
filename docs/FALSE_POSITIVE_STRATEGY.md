# False-positive strategy

False positives are the main product risk for SemZero.

The operating rule is:

> It is better to miss a weak assumption risk than to interrupt reviewers with vague warnings.

SemZero should only comment when it can explain a concrete assumption family, why this PR triggered it, and what downstream context makes it review-worthy.

## Suppression principles

### 1. No generic SQL-risk warnings

Findings should be family-specific:

- `temporal_bucket`
- `incremental_filter`
- `join_cardinality`
- `enum_domain_closure`
- `null_default_fallback`
- `materialization_cost`

A vague `generic_sql_risk` finding is not acceptable for the public product surface.

### 2. Why-now evidence is required

Every visible finding should answer:

```text
Why did this PR trigger the finding now?
```

Good:

```text
This PR changes event_ts timezone handling while downstream SQL groups by DATE(event_ts).
```

Weak:

```text
This file contains a timestamp.
```

### 3. Downstream context raises confidence

SemZero should prioritize findings where the changed assumption has known downstream use:

```text
changed expression + downstream model/exposure + owner/business context
```

A pattern without blast radius should stay low priority or silent unless the trigger is extremely strong.

### 4. Evidence fidelity controls prominence

Findings should be ranked by evidence strength:

```text
static trigger only
static trigger + blast radius
static trigger + Replay Lite fixture
static trigger + Replay Lite + prior developer validation
```

Low-fidelity findings are advisory and should not be promoted to enforcement.

### 5. Shadow mode first

SemZero defaults to advisory/shadow mode. Teams should collect receipts and feedback before considering stricter policies.

### 6. Feedback calibration

Reviewer feedback should suppress noise over time:

- true positive
- false positive
- fixed
- accepted risk
- ignored

Recurring false-positive families should be tuned, suppressed, or kept out of policy promotion.

### 7. Negative fixtures per family

Each detector family should have true-positive, false-positive, and ambiguous fixtures before it is treated as mature.

Target validation set per family:

```text
10 true-positive cases
10 benign/false-positive cases
5 ambiguous cases
```

## Detector maturity bar

A detector should be considered public-core only if it can produce:

1. precise trigger evidence
2. concrete assumption family
3. downstream context when available
4. clear recommended validation
5. low false-positive rate on benign fixtures

## The current alpha promise

SemZero does not claim complete assumption coverage. The current goal is high-signal advisory PR review, not automatic blocking.
