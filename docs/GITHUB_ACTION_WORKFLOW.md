# GitHub Action workflow

SemZero's first production-facing path is the focused dbt PR Assumption Gate.

## Generate the workflow

```bash
semzero init-assumption-ci --output-dir .
```

This writes:

```text
.github/workflows/semzero_assumption_gate.yml
.semzero/assumption_gate_policy.yml
.semzero/replay_fixtures.example.json
.semzero/cost_profiles.example.yml
.semzero/business_criticality.example.yml
.semzero/assumption_exceptions.example.jsonl
```

## Default behavior

The generated workflow runs in `shadow` mode by default. It comments on the PR and uploads evidence artifacts, but it does not block merges unless you explicitly enable strict behavior later.

## Evidence artifacts

The workflow uploads:

```text
data/semzero_assumption_gate/receipt.json
data/semzero_assumption_gate/comment.md
data/semzero_assumption_gate/changed_files.txt
data/semzero_assumption_gate/changed.diff
```

The PR comment is sticky: each run updates the existing SemZero comment instead of spamming the pull request.

## Optional evidence inputs

The workflow automatically uses optional files when present:

```text
.semzero/cost_profiles.yml
.semzero/warehouse_history.csv
.semzero/business_criticality.yml
.semzero/replay_fixtures.json
.semzero/assumption_exceptions.jsonl
target/catalog.json
target/run_results.json
```

These inputs improve cost context, business criticality, Replay Lite validation, exception handling, and artifact fidelity. They do not add new detector families.

## Recommended adoption path

1. Run in shadow mode for several PRs.
2. Collect feedback with `semzero assumption-feedback`.
3. Track accepted risk with expiring exceptions.
4. Review the dashboard and precision report.
5. Only then consider stricter policy behavior.
