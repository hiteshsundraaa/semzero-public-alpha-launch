# Using SemZero as a GitHub Action

SemZero should not require users to fork or clone the SemZero repository.

The intended alpha setup is:

```yaml
name: SemZero Assumption Gate

on:
  pull_request:
    paths:
      - "models/**/*.sql"
      - "models/**/*.yml"
      - "models/**/*.yaml"
      - "macros/**/*.sql"
      - "dbt_project.yml"
      - ".semzero/**"

jobs:
  semzero:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      pull-requests: write

    steps:
      - uses: actions/checkout@v4

      - uses: hiteshsundraaa/semzero-public-alpha-launch@<PINNED_SHA_OR_TAG>
        with:
          mode: shadow
          run-dbt-compile: "true"
          extra-pip-packages: "dbt-core dbt-duckdb"
          dbt-profiles-dir: ".semzero"
```

## Why this exists

Earlier dogfood runs required:

- a fork/clone of SemZero,
- `SEMZERO_PACKAGE_SPEC`,
- manual workflow package-source wiring,
- manual comment-posting YAML.

That is not acceptable product onboarding.

The GitHub Action wrapper makes the user workflow native:

1. checkout the user's repo,
2. call the SemZero action,
3. get a sticky PR comment.

## Pinning rule

During alpha, users should pin the action to a tag or commit SHA.

Good:

```yaml
- uses: hiteshsundraaa/semzero-public-alpha-launch@v0.8.1-alpha
```

or:

```yaml
- uses: hiteshsundraaa/semzero-public-alpha-launch@abc123def456
```

Avoid for serious dogfood:

```yaml
- uses: hiteshsundraaa/semzero-public-alpha-launch@main
```

`@main` is convenient but creates supply-chain drift: the code running in user repos changes whenever the branch moves.

## Production north star

The final product surface should be:

```yaml
- uses: semzero/assumption-gate-action@v1
  with:
    mode: shadow
```

The CLI remains the engine under the action. Users should not need to understand the CLI before first value.
