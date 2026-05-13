# First 10 Minutes with SemZero

SemZero is easiest to understand if you run one local demo first, then install the GitHub Action in a dbt repo.

## 1. Run the killer demo

From a SemZero source checkout:

```bash
pip install -e ".[dev]"
semzero demo
```

Expected result:

```text
SemZero found 1 hidden assumption.
Family: temporal_bucket
Replay Lite: drift_detected
Using supplied local sample evidence, 2/4 sampled rows moved reporting bucket
Blast radius: executive_revenue_dashboard
```

## 2. Add SemZero to a dbt repo

```bash
semzero init-assumption-ci --output-dir .
semzero doctor-assumption-ci --repo .
```

Commit the generated workflow and config:

```bash
git add .github/workflows/semzero_assumption_gate.yml .semzero/
git commit -m "Add SemZero assumption gate"
```

Open a PR that changes dbt SQL. SemZero runs in shadow mode and writes an advisory PR comment.

## 3. Manual local run

```bash
semzero assumption-ci \
  --dbt-manifest target/manifest.json \
  --base-ref origin/main
```

If optional evidence files exist under `.semzero/`, the generated workflow picks them up automatically.

## 4. Setup checks

Use this command when something fails:

```bash
semzero doctor-assumption-ci --repo .
```

It checks for:

- `dbt_project.yml`
- `target/manifest.json`
- generated SemZero workflow
- `.semzero/` config
- git repository state
- optional Replay Lite / cost / business-criticality inputs

