# SemZero 0.8.0 Alpha — First-User CI Hardening Pass

## What was hardened

This pass fixes the real first-user GitHub Actions failures found while dogfooding SemZero in a clean dbt-style repository.

The old generated workflow assumed the target repository was also the SemZero source package. That is not how real users will install the product. A customer repository may contain only `dbt_project.yml`, `models/`, `.semzero/`, and `.github/`, with no `pyproject.toml`, `setup.py`, or `requirements.txt`.

## Bugs fixed

### 1. Removed unsafe default pip cache

`actions/setup-python@v5` previously used `cache: pip`. In a minimal dbt repo, GitHub Actions fails when it cannot find `requirements.txt` or `pyproject.toml`.

The generated workflow now uses Python setup without pip caching by default.

### 2. Separated SemZero installation from customer repository installation

The generated workflow no longer assumes `pip install -e .` is always valid.

It now installs SemZero from a configurable repository variable:

```text
SEMZERO_PACKAGE_SPEC
```

Example value for alpha dogfooding:

```text
git+https://github.com/hiteshsundraaa/semzero-public-alpha-launch.git@main
```

If `SEMZERO_PACKAGE_SPEC` is not configured, the workflow only falls back to `pip install -e .` when the checked-out repository actually looks like a Python package by containing `pyproject.toml` or `setup.py`.

If neither condition is true, the workflow fails early with a clear GitHub Actions error explaining how to configure SemZero installation.

### 3. Guarded optional project dependency installation

The generated workflow still installs `requirements.txt` when present, but does not require it.

It still supports:

```text
SEMZERO_EXTRA_PIP_PACKAGES
```

for adapters such as dbt packages.

### 4. Added dbt manifest preflight

The workflow now checks whether `target/manifest.json` exists before running `semzero assumption-ci`.

If the manifest is missing, it tries `dbt deps` and `dbt compile` only when `dbt` is installed. If the manifest is still missing, it exits with a clear error:

```text
SemZero could not find target/manifest.json.
Run dbt compile in CI, commit/provide a manifest artifact, or install the dbt adapter via SEMZERO_EXTRA_PIP_PACKAGES.
```

This prevents the first user from seeing a confusing downstream SemZero failure.

### 5. Preserved prior GitHub-script newline fix

The sticky PR comment JavaScript still uses escaped newline strings:

```js
const artifactNote = '\n\n---\nSemZero ran in **shadow mode**. Evidence artifacts are attached to this workflow run.';
const finalBody = `${marker}\n${body}${artifactNote}`;
```

## Tests added/updated

The regression tests now verify that the generated Assumption Gate workflow:

- does not include `cache: pip`,
- includes `SEMZERO_PACKAGE_SPEC`,
- only falls back to `pip install -e .` behind a Python-project guard,
- has a clear missing-manifest error,
- checks whether `dbt` exists before trying to compile,
- preserves the fixed sticky-comment JavaScript escaping.

## Validation

Full local suite passes:

```text
249 passed in 21.60s
```

## Remaining first-user limitation

A real clean external repo still needs one of these:

1. `SEMZERO_PACKAGE_SPEC` pointing to a Git URL, PyPI package, internal wheel, or private package source; or
2. a local SemZero checkout where `pip install -e .` is valid.

For the current alpha dogfood, set this repository variable in the test repo:

```text
SEMZERO_PACKAGE_SPEC = git+https://github.com/hiteshsundraaa/semzero-public-alpha-launch.git@main
```

Once SemZero is published to PyPI or an internal package registry, that value should become the normal package/version spec.
