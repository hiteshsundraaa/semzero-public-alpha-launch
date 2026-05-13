# SemZero Assumption Gate CI v1.5

This release packages the focused dbt Assumption Gate for pull-request use.

It keeps scope core-only:

- dbt Assumption Gate
- blast-radius binding
- typed evidence receipts
- PR-ready Markdown comment
- GitHub Step Summary
- optional sticky PR comment workflow

It does **not** add Terraform, Kubernetes, cross-domain adapters, RGCN, Chaos Mode, Streaming Gate, full Wind Tunnel, or repair automation.

## Commands

```bash
semzero assumption-ci \
  --dbt-manifest target/manifest.json \
  --base-ref origin/main \
  --mode shadow \
  --output-dir data/semzero_assumption_gate
```

Artifacts:

```text
data/semzero_assumption_gate/receipt.json
data/semzero_assumption_gate/comment.md
data/semzero_assumption_gate/changed_files.txt
data/semzero_assumption_gate/changed.diff
```

## Scaffold workflow

```bash
semzero init-assumption-ci --output-dir .
```

This writes:

```text
.github/workflows/semzero_assumption_gate.yml
.semzero/assumption_gate_policy.yml
```

## GitHub Action

A reusable composite action is included at:

```text
.github/actions/semzero-assumption-gate/action.yml
```

Use it after installing SemZero and building `target/manifest.json`.

## Strict mode

By default, `assumption-ci` is shadow-friendly and exits zero even when the verdict is `REQUIRE_REVIEW`.

Use:

```bash
semzero assumption-ci --strict ...
```

only after the team has collected enough shadow feedback.
