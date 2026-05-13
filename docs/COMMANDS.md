# SemZero Commands — 0.8.0-alpha

## Hero path: dbt PR Assumption Gate

```bash
semzero init-assumption-ci --output-dir .
semzero assumption-ci --dbt-manifest target/manifest.json --base-ref origin/main
```

Core commands:

- `semzero assumption-ci` — CI wrapper and PR-summary artifact writer
- `semzero assumption-gate` — local/manual assumption analysis
- `semzero assumption-dashboard` — shadow dashboard from receipts and feedback
- `semzero assumption-feedback` — developer agreement/disagreement/fixed records
- `semzero assumption-exception` — accepted-risk exceptions with expiry
- `semzero assumption-precision-eval` — noise/usefulness evaluation
- `semzero assumption-lineage` — Assumption Lineage Lite
- `semzero assumption-decay` — Assumption Decay Tracking Lite
- `semzero assumption-memory` — Drift Memory Lite

## Experimental / legacy engine commands

`semzero gate | wind-tunnel | chaos | premerge | validate-e2e` are retained for research/legacy workflows, but the public product wedge is the focused dbt Assumption Gate.

---

# SemZero Commands (0.5.2)

SemZero now exposes **two command layers**:

- **Daily / high-level commands** for most teams
- **Expert / engine commands** for platform engineers who want direct control

The high-level layer does **not** replace the current moat. It sits on top of the existing Gate, Wind Tunnel, Chaos, AST, and validation engines.

## Daily / high-level commands

These are the commands most teams should use first.

### `semzero check`
Receipt-first status check. Reuses current SemZero evidence by default instead of rerunning expensive validation.

Examples:

```bash
semzero check
semzero check --receipt data/premerge_bundle.json
semzero check --search-dir validation_artifacts/run_phase1_0.4.0/premerge
```

### `semzero explain`
Explains why the current receipt blocked, warned, or passed.

```bash
semzero explain
semzero explain --receipt data/premerge_bundle.json
```

### `semzero assumption-gate — focused dbt hidden-assumption + blast-radius PR gate
- semzero recheck`
Runs a fresh high-level wrapper. Current version supports `premerge` and `validation` modes while preserving the underlying engine commands. It also adds `fix` for next-step / rollback guidance and lets `report` render receipt-first HTML or Markdown.

```bash
semzero assumption-gate — focused dbt hidden-assumption + blast-radius PR gate
- semzero recheck --mode premerge --graph data/schema_graph.json --drift data/drift_report.json
semzero assumption-gate — focused dbt hidden-assumption + blast-radius PR gate
- semzero recheck --mode validation --demo-pack-dir data/demo_pack --demo-profile messy
```

### `semzero compare`
Compares two receipts or report bundles to prove what changed.

```bash
semzero compare --right validation_artifacts/run_phase1_0.4.0/premerge/premerge_bundle.json
semzero compare --left data/premerge_bundle.json --right validation_artifacts/run_phase1_0.4.0/premerge/premerge_bundle.json
```

### `semzero commands`
Prints the current command surface and the docs path.

### `semzero init-ci`
Scaffolds a drop-in GitHub Action, starter environment file, and quickstart commands so teams can get to shadow-mode value quickly.

```bash
semzero init-ci --preset snowflake
semzero init-ci --preset databricks --output-dir /tmp/semzero-demo
```

## Expert / engine commands

These remain the authoritative low-level commands in the current build:

- `semzero doctor`
- `semzero scan`
- `semzero crawl`
- `semzero diff`
- `semzero blast`
- `semzero match`
- `semzero repair`
- `semzero report`
- `semzero trace`
- `semzero watch`
- `semzero history`
- `semzero gate`
- `semzero wind-tunnel`
- `semzero chaos`
- `semzero premerge`
- `semzero validate-e2e`
- `semzero ops-report`
- `semzero release-info`

## Design rules

1. **High-level by default** — teams should not need to orchestrate AST, Wind Tunnel, Chaos, and Gate manually.
2. **Receipt-first** — repeated checks should reuse valid evidence whenever possible.
3. **Drop-in CI first** — `semzero init-ci` should get a team into shadow mode with a single scaffold command and a drop-in GitHub Action.
4. **Explicit freshness** — if users want more confidence, they can recheck or compare.
5. **Moat preserved** — the expert engine commands remain available and documented.

## Current reality

In 0.4.2, the new high-level commands are a **wrapper layer over the current artifact and engine surfaces**. This keeps the product easier to use **without understating the technical depth that already exists underneath**.

## New in 0.5.1

- Composite receipts can be rendered as HTML / Markdown through `semzero report --receipt ...`.
- `semzero fix` turns the current receipt into guided next steps and rollback-aware advice.
- Wind Tunnel receipts now surface replay fidelity and row-level mismatch previews.
- Premerge bundles now reserve artifact paths for override and incident ledgers so teams can layer learning loops on top without replacing the current moat.

## New in 0.5.2

- Composite receipts can be rendered as HTML / Markdown through `semzero report --receipt ...`.
- `semzero fix` turns the current receipt into guided next steps and rollback-aware advice.
- Wind Tunnel receipts now surface replay fidelity and row-level mismatch previews.
- Premerge bundles now reserve artifact paths for override and incident ledgers so teams can layer learning loops on top without replacing the current moat.

## New in 0.5.2

- `semzero init-ci` now scaffolds a drop-in GitHub Action and starter config for fast time-to-value.
- Gate and Wind Tunnel now surface a **Pre-merge FinOps Gate** receipt that estimates avoidable warehouse spend before merge.
- Receipt-first reports and PR comments now include projected weekly compute waste and weekend savings where SemZero detects transformation-layer cost drivers.


## Shadow mode

```bash
semzero shadow --graph data/schema_graph.json --drift data/drift_report.json --db-url "$SEMZERO_DB_URL"
```

This runs the full premerge workflow with evidence collection enabled and merge blocking disabled. Use it as the default rollout posture while calibrating precision, savings estimates, and remediation quality.


## Shadow mode proofing

- `semzero shadow` — run full premerge flow in non-blocking shadow mode
- `semzero shadow-dashboard` — build the would-have-blocked / would-have-saved dashboard
- `semzero shadow-feedback` — record developer feedback for shadow calibration
- `semzero shadow-trends` — show repo/team trend history and rollout recommendations

## Shadow trends

```bash
semzero shadow-trends --data-dir data --scope repo
semzero shadow-trends --data-dir data --scope team
semzero shadow-trends --data-dir data --scope global
```

Use this after shadow runs and feedback capture to see repo/team trend history and enforcement recommendations by confidence tier.

Recommended rollout tiers:

- `TIER_0_SHADOW_ONLY`: keep observing; not enough data or precision yet.
- `TIER_1_ADVISORY`: show findings, but do not require review or block.
- `TIER_2_REQUIRE_REVIEW`: require human review for repeated/high-confidence risk classes.
- `TIER_3_SELECTIVE_BLOCK`: hard-block only calibrated, high-confidence, high-severity findings.

### `semzero streaming-shadow`

Run Kafka/topic schema and consumer-contract checks in non-blocking shadow mode.

```bash
semzero streaming-shadow \
  --before examples/streaming/before_topics.json \
  --after examples/streaming/after_topics.json \
  --contracts examples/streaming/consumer_contracts.json \
  --repo stream-repo \
  --team stream-platform \
  --data-dir data
```

Writes a streaming gate result, HTML report, and appends to the shadow dashboard ledger.


## Focused Assumption Gate commands

```bash
semzero assumption-gate   --dbt-manifest target/manifest.json   --changed-file models/staging/stg_events.sql   --mode shadow   --output data/assumption_gate_receipt.json   --comment-out data/assumption_gate_comment.md
```

Aggregates assumption-gate receipts into an assumption-first dashboard:

```bash
semzero assumption-dashboard --receipt-dir data
```

The active adapter is `dbt_assumption_gate` and the active domain is `data`. Future adapters are design constraints only, not current scope.

## Focused Assumption Gate v1.3

```bash
semzero assumption-gate \
  --dbt-manifest target/manifest.json \
  --changed-file models/staging/stg_events.sql \
  --changed-diff pr.diff \
  --output data/assumption_gate_receipt.json \
  --comment-out data/assumption_gate_comment.md
```

`--changed-diff` is optional. It can be a file path or inline diff text and is used to populate why-now trigger evidence in findings.


## SemZero Core v1.4 feedback loop

Record developer feedback for assumption-gate shadow calibration:

```bash
semzero assumption-feedback --receipt data/assumption_gate_receipt.json --finding-id AG-TEMPORAL-BUCKET-001 --disposition agree --feedback-file data/assumption_feedback.jsonl
```

Aggregate receipts and feedback:

```bash
semzero assumption-dashboard --receipt-dir data --feedback-file data/assumption_feedback.jsonl
```

This keeps the current product focused on the dbt Assumption Gate while collecting the agreement/disagreement evidence needed before advisory or require-review enforcement.


### `semzero assumption-ci` — CI wrapper for dbt Assumption Gate

Discovers changed dbt SQL/YAML files from a pull-request diff, runs the focused Assumption Gate, writes stable artifacts, and appends the PR-ready comment to the GitHub Step Summary when available.

```bash
semzero assumption-ci   --dbt-manifest target/manifest.json   --base-ref origin/main   --mode shadow   --output-dir data/semzero_assumption_gate
```

Outputs:

```text
data/semzero_assumption_gate/receipt.json
data/semzero_assumption_gate/comment.md
data/semzero_assumption_gate/changed_files.txt
data/semzero_assumption_gate/changed.diff
```

Use `--strict` only after shadow/advisory calibration.

### `semzero init-assumption-ci` — scaffold focused GitHub PR workflow

```bash
semzero init-assumption-ci --output-dir .
```

Writes:

```text
.github/workflows/semzero_assumption_gate.yml
.semzero/assumption_gate_policy.yml
```


## SemZero Core v1.7 — ROI-aware Assumption Dashboard

The focused `semzero assumption-dashboard` now includes ROI/value signals from Assumption Gate receipts and developer feedback: rough cost exposure surfaced, rough avoided cost from fixed findings, accepted-risk history, recurring assumption families, and daily feedback/finding trends. Cost numbers are directional and based on receipt estimates, not audited billing.

## Core v1.8 policy calibration

Build the assumption dashboard with policy-tuning recommendations:

```bash
semzero assumption-dashboard \
  --receipt-dir data/semzero_assumption_gate \
  --feedback-file data/semzero_assumption_gate/assumption_feedback.jsonl \
  --output data/semzero_assumption_gate/assumption_dashboard.json \
  --markdown-output data/semzero_assumption_gate/assumption_dashboard.md
```

The output includes `policy_recommendations`. These are advisory only and are not auto-applied to `.semzero/assumption_gate_policy.yml`.


### SemZero Core v1.9

Adds stable finding IDs, recurring stable-finding dashboard metrics, softer calibration readiness, and stable-ID feedback capture. See `docs/ASSUMPTION_CALIBRATION_V1_9.md`.

## Dogfood fixture runner

```bash
python scripts/run_dogfood_assumption_gate.py
```

Runs the local mini dbt fixture in `examples/dogfood_dbt_assumption_gate/` and writes scenario receipts, comments, and an aggregate assumption dashboard.


### `semzero assumption-dogfood-report`

Builds a product-demo report from the packaged dogfood Assumption Gate receipts and dashboard outputs.

```bash
semzero assumption-dogfood-report   --dogfood-dir examples/dogfood_dbt_assumption_gate   --output examples/dogfood_dbt_assumption_gate/dogfood_demo_report.json   --markdown-output examples/dogfood_dbt_assumption_gate/dogfood_demo_report.md
```


## Core v1.12 warehouse-aware cost profiles

SemZero now supports `--cost-profiles` for directional Snowflake/Databricks/dbt cost exposure without requiring warehouse credentials:

```bash
semzero assumption-ci   --dbt-manifest target/manifest.json   --base-ref origin/main   --cost-profiles .semzero/cost_profiles.yml   --output-dir data/semzero_assumption_gate
```

This adds per-run and monthly exposure estimates to receipts, PR comments, and the assumption dashboard. It also adds the `materialization_cost` assumption family for dbt full-refresh / replace-table / materialized-table cost regressions.


### `semzero assumption-gate --criticality-registry`

Optional JSON/YAML mapping that marks dbt nodes or exposures as board, executive, revenue, customer-facing, or internal assets. This enriches blast-radius output and PR comments but does not enable blocking by itself.

```bash
semzero assumption-gate \
  --dbt-manifest target/manifest.json \
  --changed-file models/staging/stg_events.sql \
  --criticality-registry .semzero/business_criticality.yml
```

### `semzero assumption-ci --criticality-registry`

The CI wrapper accepts the same registry so pull-request comments can explain business impact.

## `semzero assumption-exception`

Record a reason-required accepted-risk/suppression exception for Assumption Gate findings.

```bash
semzero assumption-exception \
  --scope stable_id \
  --value AG-INCREMENTAL-FILTER-ABC123 \
  --reason "Accepted during controlled backfill; revisit after migration." \
  --owner data-platform \
  --expires-at 2026-06-01T00:00:00+00:00 \
  --exceptions-file data/assumption_exceptions.jsonl
```

Exception scopes: `stable_id`, `family`, `source`, `receipt`, `global`.

Exceptions are advisory: they annotate receipts/comments and appear in the dashboard; they do not delete findings.


## SemZero Core v1.16 — real dbt artifact hardening

`assumption-gate` and `assumption-ci` now accept optional `--dbt-catalog`, `--dbt-run-results`, and `--project-dir` inputs. These let SemZero enrich findings with real dbt catalog, test, exposure, compiled SQL, and runtime context while keeping the product core-only and non-blocking by default.


## SemZero Core v1.17 — Offline warehouse history

`assumption-gate` and `assumption-ci` support `--warehouse-history` for offline Snowflake query-history CSV/JSON, Databricks job-run JSON, or dbt runtime exports. This calibrates rough cost estimates without live credentials. Findings remain advisory and non-blocking by default.


## SemZero Core v1.18 — Precision/noise evaluation

Adds `semzero assumption-precision-eval`, an advisory-only report that reviews assumption-gate receipts for missing trigger evidence, missing blast radius, over-broad findings, negative developer feedback, active exceptions, and developer-validated findings. This helps tune the gate before any stricter policy mode.


## SemZero Core v1.19 — Reviewer-first PR comments

Adds compact PR-comment grouping for the focused dbt Assumption Gate:

- Must review
- Useful advisory
- Accepted risk / active exceptions
- Needs feedback

The JSON receipt remains the complete evidence source; the PR comment is intentionally capped and action-oriented so reviewers do not tune it out.


## SemZero Core v1.20 — Assumption Diff + Replay Fidelity

SemZero now includes static assumption diffing and replay/evidence fidelity scoring in Assumption Gate receipts and PR comments. This explains old-vs-new assumed behavior and how much evidence SemZero had, while explicitly stating that v1.20 does not run full behavioral replay. See `docs/ASSUMPTION_DIFF_REPLAY_FIDELITY_V1_20.md`.


## SemZero Core v1.21 — Assumption Validation Replay Lite

SemZero now supports targeted local Replay Lite via `--replay-fixtures`, validating specific assumption families from supplied fixture/sample data or precomputed before/after counts. It adds `validation_replay` evidence to receipts and PR comments without full warehouse cloning or hard blocking. See `docs/ASSUMPTION_VALIDATION_REPLAY_LITE_V1_21.md`.

## SemZero Core v1.22 — Replay-aware dashboard

`semzero assumption-dashboard` now emits a `replay_aware` section showing Replay Lite coverage, drift signals, low-fidelity findings, family-level replay coverage, and a replay review queue.

`semzero assumption-precision-eval` now uses Replay Lite status and replay/evidence fidelity when classifying findings.

### `semzero assumption-lineage`

Builds Assumption Lineage Lite from assumption-gate receipts.

```bash
semzero assumption-lineage --receipt-dir data/semzero_assumption_gate
```

## `semzero assumption-decay`

Build advisory-only Assumption Decay Tracking Lite from Assumption Gate receipts,
feedback, and exceptions.

```bash
semzero assumption-decay \
  --receipt-dir data/semzero_assumption_gate \
  --feedback-file data/semzero_assumption_gate/assumption_feedback.jsonl \
  --exceptions-file data/semzero_assumption_gate/assumption_exceptions.jsonl
```

Outputs:

- `assumption_decay.json`
- `assumption_decay.md`

This command highlights recurring replay-validated drift, stale assumptions,
accepted-risk debt, expired exceptions, repeated false positives, and high-risk
unreviewed findings. It is advisory-only.


## SemZero v1.25 — Assumption Drift Memory Lite

`semzero assumption-memory` aggregates existing receipts, Replay Lite results, feedback, exceptions, and business-criticality signals into organization/team/model-level assumption memory. It is advisory-only and does not add new findings or stricter enforcement.


## First-user usability commands

```bash
semzero quickstart
semzero demo
semzero doctor-assumption-ci --repo .
```

- `quickstart` shows the shortest install/demo/CI path and detects whether the current repo has dbt and SemZero setup files.
- `demo` runs the focused killer demo when called from a SemZero source checkout; installed-package users get clone/run instructions.
- `doctor-assumption-ci` checks a dbt repository for the required files and common missing setup steps.
