# SemZero 0.8.0-alpha

[![Tests](https://github.com/hirreshsundra3/semzero/actions/workflows/test.yml/badge.svg)](https://github.com/hirreshsundra3/semzero/actions/workflows/test.yml)
[![Quality](https://github.com/hirreshsundra3/semzero/actions/workflows/quality.yml/badge.svg)](https://github.com/hirreshsundra3/semzero/actions/workflows/quality.yml)
[![PyPI](https://img.shields.io/pypi/v/semzero.svg)](https://pypi.org/project/semzero/)
[![Python](https://img.shields.io/pypi/pyversions/semzero.svg)](https://pypi.org/project/semzero/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**SemZero is a dbt PR Assumption Gate.**

Your schema diff tells you what changed. SemZero tells you what downstream SQL was silently assuming before that change merges.

The 0.8.0-alpha public product surface is intentionally narrow: a workflow-native PR reviewer for hidden dbt assumptions, blast radius, Replay Lite validation, cost exposure, and auditable receipts.


## Install

From PyPI, once published:

```bash
pip install semzero
```

From source:

```bash
git clone https://github.com/hirreshsundra3/semzero.git
cd semzero
pip install -e .
```

For development:

```bash
pip install -e ".[dev]"
pytest
```

## Quickstart

New user path:

```bash
semzero quickstart
semzero demo
semzero doctor-assumption-ci --repo .
```

See [`docs/FIRST_10_MINUTES.md`](docs/FIRST_10_MINUTES.md) and [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md).

In a dbt repo:

```bash
semzero init-assumption-ci --output-dir .
git add .github/workflows/semzero_assumption_gate.yml .semzero/
git commit -m "Add SemZero assumption gate"
```

Open a PR that changes dbt SQL. SemZero will run in shadow mode and post an advisory PR comment.

## 10-minute workflow

```bash
pip install -e .
semzero init-assumption-ci --output-dir .
```

Then open a dbt PR. The GitHub workflow runs:

```bash
semzero assumption-ci \
  --dbt-manifest target/manifest.json \
  --base-ref origin/main \
  --policy .semzero/assumption_gate_policy.yml
```

SemZero writes:

- a compact sticky PR comment
- `receipt.json` with typed evidence
- `changed.diff` and `changed_files.txt`
- optional dashboard, precision, lineage, decay, and memory reports

## What it catches

Core assumption families:

- **Temporal bucket drift** — timezone/date-boundary changes behind `DATE(event_ts)` or `DATE_TRUNC`
- **Incremental filter weakening** — less selective predicates, partition-pruning regressions, or cost exposure
- **Join cardinality drift** — one-to-many fanout and aggregate inflation risks
- **Enum/domain closure drift** — `CASE`/`IN` logic that assumes a closed status domain
- **Null/default fallback drift** — `COALESCE` and fallback logic that can hide missingness
- **Materialization cost drift** — incremental models becoming full rebuild/full-refresh paths

## Hero commands

```bash
semzero assumption-ci
semzero assumption-gate
semzero assumption-dashboard
semzero assumption-feedback
semzero assumption-exception
semzero assumption-precision-eval
semzero assumption-lineage
semzero assumption-decay
semzero assumption-memory
```


### GitHub PR workflow

Generate the workflow and starter `.semzero/` config:

```bash
semzero init-assumption-ci --output-dir .
```

The generated workflow runs in shadow mode, posts one sticky PR comment, uploads `receipt.json`/`comment.md` as artifacts, and automatically uses optional `.semzero/` evidence files when present. See [`docs/GITHUB_ACTION_WORKFLOW.md`](docs/GITHUB_ACTION_WORKFLOW.md).

## Killer demo

Run the focused first-user demo:

```bash
python scripts/run_killer_demo.py
```

The demo shows one painful dbt PR:

```diff
-  event_ts,
+  convert_timezone('UTC', 'America/New_York', event_ts) as event_ts,
```

The schema still looks compatible and the SQL still runs, but downstream finance SQL groups by `DATE(event_ts)`. SemZero detects that hidden temporal-bucket assumption, runs Replay Lite on supplied local sample boundary rows, attaches executive-dashboard blast radius, and writes:

```text
examples/killer_demo_pr/output/receipt.json
examples/killer_demo_pr/output/comment.md
```

Expected summary:

```text
SemZero found 1 hidden assumption.
Family: temporal_bucket
Replay Lite: drift_detected
Using supplied local sample evidence, 2/4 sampled rows moved reporting bucket
Blast radius: executive_revenue_dashboard
```

Walkthrough: [`docs/DEMO_WALKTHROUGH.md`](docs/DEMO_WALKTHROUGH.md).

The broader dogfood pack is still available for all six assumption families:

```bash
python scripts/run_dogfood_assumption_gate.py
```

## Kept in the product core

These modules are retained because they strengthen the dbt PR Assumption Gate:

- assumption extraction and assumption diffing
- dbt manifest/catalog/run-results ingestion
- blast radius through dbt lineage and exposures
- Replay Lite targeted validation
- Snowflake/Databricks offline history and cost profiles
- typed JSON receipts with stable finding IDs
- GitHub PR comments and `assumption-ci`
- feedback, exceptions, freshness, precision, and dashboard calibration
- Assumption Lineage Lite, Decay Tracking Lite, and Drift Memory Lite

## Experimental / legacy surfaces

Broader platform modules are retained for research and future-product work, but they are not the first-time user path:

- full Wind Tunnel / warehouse replay
- Chaos Mode
- streaming shadow gate
- GNN / RGCN experiments
- broad premerge validation packs
- repair automation
- Slack and broad platform integrations

See [`docs/EXPERIMENTAL.md`](docs/EXPERIMENTAL.md).



## OSS, managed, and enterprise boundaries

SemZero OSS is designed to be genuinely useful for local and single-repository dbt PR assumption review. The open-source core includes the CLI, GitHub Action scaffold, assumption detection, receipts, Replay Lite from local fixtures, basic reports, and examples.

Future managed or enterprise offerings should focus on operating SemZero at scale: hosted multi-repo dashboards, centralized evidence storage, managed warehouse-history connectors, SSO/RBAC, audit logs, policy rollout, enterprise support, and private deployment.

See [`docs/OSS_AND_ENTERPRISE.md`](docs/OSS_AND_ENTERPRISE.md).

## External demo repository

After publishing this repo, create a separate demo dbt repository to prove SemZero works outside its own codebase:

```text
https://github.com/hiteshsundraaa/semzero-demo-dbt
```

A ready-to-upload demo repository is provided as a separate release artifact. It contains a tiny dbt project and a pull request scenario where timestamp semantics change while downstream finance SQL still assumes `date(event_ts)` is the reporting day.

See [`docs/EXTERNAL_DEMO_REPO.md`](docs/EXTERNAL_DEMO_REPO.md).


## Credibility notes

Before public use, read the alpha credibility docs:

- [Replay Lite truth/auth story](docs/REPLAY_LITE.md) — Replay Lite uses supplied local fixtures/samples in OSS; it does not require warehouse credentials.
- [False-positive strategy](docs/FALSE_POSITIVE_STRATEGY.md) — SemZero optimizes for precise, family-specific findings rather than broad noisy warnings.
- [Competitive positioning](docs/COMPETITIVE_POSITIONING.md) — SemZero is an assumption-aware PR review layer, not a replacement for data diffing or observability.
- [Naming and tagline](docs/NAMING_AND_TAGLINE.md) — keep the product anchored to “PR review for hidden assumptions in dbt changes.”

## Status

SemZero is currently `0.8.0-alpha` / `0.8.0a2`.

Recommended use:

- shadow mode in CI
- advisory PR comments
- artifact review
- manual approval for risky findings

Not recommended yet:

- automatic PR blocking in production
- automatic SQL repair
- unsupervised warehouse changes

## Testing

SemZero has four testing layers:

1. **Unit tests** for assumption extraction, policy evaluation, receipts, and CLI behavior.
2. **Golden demo tests** that verify the killer dbt PR example still produces the expected finding.
3. **Packaging tests** that build the wheel and install it in a clean virtual environment.
4. **GitHub workflow tests** that verify the generated PR workflow and artifact paths remain stable.

Run locally:

```bash
pip install -e ".[dev]"
pytest
ruff check .
mypy semzero semzero_lab
```

Run the first-user smoke test:

```bash
python scripts/run_killer_demo.py
semzero init-assumption-ci --output-dir /tmp/semzero-test
```

Release checklist: [`docs/RELEASE_CHECKLIST.md`](docs/RELEASE_CHECKLIST.md).

## Adoption posture

SemZero is shadow/advisory-first. It should earn trust through receipts, feedback, Replay Lite validation, and dashboard calibration before any team considers strict enforcement.

## Version

`0.8.0a2` / 0.8.0-alpha public-readiness trust layer.

## Package layout note

`semzero/` is the canonical implementation package. `src/` is kept only as a small legacy compatibility shim for older imports such as `src.integrations.*`; new code should import `semzero.*` directly. The old duplicated implementation tree has been removed from the release package.

