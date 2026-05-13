# SemZero 0.3.11 Patch Log

## Live SQL validation hardening pass

### Added
- PostgreSQL-backed validation-pack support inside `semzero validate-e2e` via `--demo-backend postgres`.
- New live-validation options for `validate-e2e`:
  - `--demo-backend [sqlite|postgres]`
  - `--source-schema`
- New `build_live_postgres_validation_pack(...)` helper for seeding a heavy PostgreSQL validation corpus with:
  - `users`
  - `orders`
  - `order_rollup`
  - `events`
- PostgreSQL shadow-schema execution for the hardest runtime scenarios so validation can stay destructive-safe while still using a real live SQL engine.
- Regression tests for the new live Postgres validation scaffolding and CLI exposure.

### Improved
- `blank_string_fanout` validation now runs on PostgreSQL live-validation paths instead of downgrading to SQLite-only.
- `incremental_ghost` validation now runs on PostgreSQL live-validation paths using cloned shadow tables and `ON CONFLICT` incremental replay.
- Validation metrics now include stronger runtime comparison context such as join row multipliers for blank-string fan-out.
- The live validation harness is now much closer to a real heavy-database prototype flow instead of only a local SQLite truth harness.

### Why this matters
This pass moves SemZero closer to real product validation on large SQL systems by making the validation harness usable against:
- large local SQLite packs for fast truth checks
- live PostgreSQL prototype databases for heavier end-to-end runtime validation

That means you can now validate more of the real question:
- did SemZero predict the failure correctly?
- did the runtime actually fail or silently drift?
- did the incremental state stay corrupted?
- did join-key blank normalization create a real fan-out explosion?

### Validation
- Full suite passed: **126/126**.
- `validate-e2e` smoke run passed end to end after the live-validation patch.

---

# SemZero 0.3.10 Patch Log

## Validation and edge-case hardening pass

### Added
- New `semzero validate-e2e` command for end-to-end technical validation against live/demo SQL databases.
- Built-in large SQLite validation pack generator for reproducible runtime validation of:
  - silent truncation / string-length narrowing
  - domain / enum drift
  - timezone / temporal boundary drift
  - blank-string join fan-out
  - incremental ghost / delete-blind incremental state corruption
- New validation report outputs in JSON, Markdown, and HTML that compare predicted vs actual behavior.
- `BLANK_STRING_FLOOD` chaos mutation for join-key/identity/domain text fields.

### Improved
- PreGate now treats `VARCHAR(255) -> VARCHAR(50)`-style narrowing as a first-class truncation risk instead of a generic same-family type change.
- PreGate now treats timezone-aware -> timezone-naive casts as semantic breaks and surfaces temporal guidance.
- AST proofing now marks incremental logic paths, and Gate execution recommendations can require incremental state-reconciliation checks.
- Data-regression recommendations now call out domain / enum drift when new values are introduced into historically constrained columns.

### Validation
- Full suite passed: **124/124**.
- `semzero validate-e2e --demo-pack-dir ...` runs end to end and emits JSON/Markdown/HTML validation artifacts.

---
# SemZero 0.3.9 Patch Log

## Scope of this pass
This pass upgraded the existing reliability/testing wedge without widening the product category.

Primary objectives:
- wire graph-native intelligence into the live reliability loop
- use existing RGCN assets where available instead of leaving them off to the side
- keep the new graph layer optional so installs stay stable without torch/pyg
- improve prioritization and debug visibility across Gate, Wind Tunnel, and Chaos

## What was added

### 1. Graph intelligence engine
Added `src/integrations/graph_intelligence.py` as a graph-native signal layer.

It provides:
- heuristic graph risk scoring by default
- optional RGCN inference when a checkpoint and dependencies are available
- per-node prioritization with reasons
- a single report object reusable by Gate, Wind Tunnel, and Chaos

### 2. Optional RGCN integration into the core loop
SemZero was already shipping RGCN code for schema matching/authentication, but it was not participating in the main reliability workflow.

Now, when an RGCN checkpoint is supplied, SemZero can use it as an additive signal for:
- Gate risk ranking
- Wind Tunnel workload prioritization
- Chaos target prioritization

If no checkpoint/dependencies are present, SemZero automatically falls back to heuristic graph intelligence.

### 3. Change Gate graph-native risk context
Change Gate now records per-assessment:
- `graph_risk_score`
- `graph_risk_reasons`

And emits top-level `graph_intelligence` summary data into gate results.

This is also folded into:
- reliability scoring
- on-call risk
- execution recommendations
- priority node ordering for triage and replay

### 4. Wind Tunnel graph-ranked replay scope
Wind Tunnel now receives graph intelligence during query extraction/ranking.

This improves:
- prioritization of graph-fragile assets
- relevance of replay ordering
- exported report visibility into graph-ranked focus

### 5. Chaos graph-native targeting
Chaos targeting now includes:
- `graph_intelligence_score`
- `graph_intelligence_provider`
- graph-aware targeting reasons

This keeps mutations focused on the structurally riskiest assets instead of relying only on centrality/workload heuristics.

### 6. Report visibility
Unified reports now include graph-intelligence sections so teams can see:
- which assets were structurally prioritized
- whether heuristic or RGCN signals were used
- why certain nodes were escalated first

### 7. CLI support
Added optional `--rgcn-model` support to:
- `semzero gate`
- `semzero wind-tunnel`
- `semzero chaos`
- `semzero premerge`

This makes the graph layer usable end-to-end instead of requiring internal wiring.

## Validation
Validated locally after the patch set:
- full suite passed: **120/120**
- compile checks passed on modified modules
- CLI version aligned to **0.3.9**

## What this improves in practice
- better priority ordering for triage, replay, and chaos
- stronger use of the graph/RGCN work already present in the repo
- no hard runtime dependency on torch/pyg for normal installs
- better debug context in exported reports

## What is still not fully proven here
- live RGCN inference with a real trained checkpoint in a production environment
- calibration of the graph-intelligence weighting against large real-world incident histories

---

# SemZero 0.3.7 Patch Log

## Scope of this pass
This pass deepened SemZero's current reliability/testing wedge instead of widening it into generic mapping or merge automation.

Primary objectives:
- strengthen the pre-merge reliability loop
- add native ecosystem intelligence where it directly improves reliability decisions
- make reports more useful for operators and reviewers
- keep clone/test costs bounded while improving realism

## What was added

### 1. Fuller ecosystem-native ingestion
Added lightweight native ingestion for:
- dbt artifacts: `manifest.json`, `catalog.json`, `run_results.json`, source freshness exports
- OpenLineage JSON / JSONL events
- Airflow DAG metadata exports
- Dagster asset-check exports
- LookML / Looker downstream references

These feed SemZero's reliability flow as context, not as a new product category.

### 2. Calibration memory
Added a file-backed calibration store that records prior gate runs and summarizes:
- total runs
- block rate
- review rate
- high on-call rate
- recent failure modes

This is used to enrich reporting and sets up later probabilistic calibration without forcing a learned model into the decision path yet.

### 3. Iron Gate policy output
Strengthened Change Gate with a dedicated Iron Gate status object:
- merge-block state
- context name for status checks
- cost-threshold enforcement
- optional review-block policy
- downstream business/consumption risk consideration

The GitHub status-check path now uses the Iron Gate state when posting CI status.

### 4. Compute-cost risk in Wind Tunnel
Added bounded compute-risk estimation using:
- SQL-shape heuristics
- optional SQLite `EXPLAIN QUERY PLAN` enrichment
- top heavy-query notes in the receipt/report

This gives Wind Tunnel a practical `compute_cost_risk` output without requiring warehouse-specific vendor SDKs in local mode.

### 5. Regime-switching future workload generation
Extended Wind Tunnel future-workload generation with regime-aware scenarios:
- `quarter_end`
- `backfill_window`

These are bounded and purpose-driven rather than broad synthetic query spam.

### 6. Stateful Chaos recovery verification
Added stateful recovery checks for row-level chaos mutations by:
- snapshotting affected tables in the clone
- replaying workloads after restore
- recording whether manual restore/backfill was required

This moves Chaos closer to answering:
- did it break?
- did it recover?
- would ops need to intervene?

### 7. Better exported reports
Unified reports now include:
- ecosystem context
- calibration memory summary
- compute-cost risk
- regime scenario visibility
- recovery verification summary

This makes exported reports more understandable for data teams and closer to an operator runbook.

### 8. CLI support for the new context
Extended CLI workflows so teams can actually use the new capabilities without custom wiring:
- Gate
- Wind Tunnel
- Chaos
- Premerge

New inputs include dbt/catalog/openlineage/airflow/dagster/looker paths plus stateful/regime toggles where relevant.

## Files added
- `src/integrations/ecosystem.py`
- `src/integrations/calibration.py`
- wrapper exports under `semzero/integrations/`
- `tests/test_ecosystem_integrations.py`

## Files materially changed
- `src/integrations/change_gate.py`
- `src/chaos/wind_tunnel.py`
- `src/chaos/chaos_engine.py`
- `src/reliability/premerge.py`
- `src/reporting/live_report.py`
- `src/cli.py`
- `pyproject.toml`

## Validation
Validated locally after the patch set:
- full suite passed: **113/113**
- compile checks passed on modified modules
- CLI version aligned to **0.3.7**

## What this improves in practice
- more accurate pre-merge recommendations from real project artifacts
- better downstream/context awareness without turning SemZero into a catalog platform
- stronger report quality for debugging and approval workflows
- better visibility into expensive query patterns before merge
- more realistic Chaos output focused on recovery, not just breakage

## What is still not fully proven here
Not fully live-validated in this container against real remote systems:
- live GitHub App / GitLab App merge blocking beyond API-compatible status posting
- live Snowflake / BigQuery plan extraction at scale
- live Airflow metadata DB ingestion
- live Dagster GraphQL / API ingestion
- live Looker API ingestion beyond file-based LookML parsing

These paths were added in a way that is usable and testable locally first, but not overclaimed as fully production-proven across all vendor environments.

## 0.3.8 — reliability hardening pass

Added in this pass:
- richer **Iron Gate** status payloads for GitHub/GitLab-style merge blocking workflows
- stronger **query-plan / compute-cost risk** analysis using SQL pattern scoring plus EXPLAIN-plan signals for SQLite/Postgres
- `plan_risk_summary` and `top_expensive_queries` in Wind Tunnel receipts
- richer **stateful Chaos recovery** outputs with recoverability score, unrecovered mutation counts, and a recovery playbook
- a redesigned **Unified Ops Report** with proper HTML cards, Iron Gate visibility, ecosystem/calibration context, compute-cost guidance, and recovery guidance

Validation:
- full suite passed: **116/116**
- release version aligned to **0.3.8**

Why this matters:
- merge blocking is easier to wire into real workflows
- compute-heavy regressions are easier to spot before merge
- recovery/backfill expectations are clearer for data ops teams
- exported reports are much more useful as runbooks instead of raw receipts


## 0.3.12 — expanded validation matrix and messy-data coverage

Added in this pass:
- broadened the end-to-end validation harness from a narrow five-scenario pack into a wider hazard matrix covering:
  - `rename_breakage`
  - `nullability_hardening`
  - `numeric_precision_narrowing`
  - `distribution_drift`
  - plus the existing truncation / domain / temporal / blank-string fan-out / incremental ghost paths
- extended demo/live validation packs with more expensive workflow classes and messy data traits:
  - nullable contact fields
  - finance-style `NUMERIC(18,4)` payment columns
  - distribution-sensitive nullable adjustment columns
  - additional workload queries over payments and joins
  - cross-modal proof assets in SQL, Prisma, and TypeScript
- added a `demo_profile` concept for validation packs (`standard`, `messy`, `finance`) so SemZero can be exercised against dirtier schema/data conditions without changing the core workflow
- added `xlarge` validation scale support for heavier prototype datasets
- strengthened PreGate’s type classification to catch numeric precision/scale narrowing (`NUMERIC(18,4)` → `NUMERIC(10,2)`) as a first-class narrowing hazard
- improved the validation report summary with failed-scenario visibility, so missed hazard families are obvious enough to drive rule rewrites
- kept the workflow end-to-end in loop: Gate → Wind Tunnel → Chaos → predicted-vs-actual scorecard

Files materially changed:
- `src/reliability/validation.py`
- `src/integrations/change_gate.py`
- `src/cli.py`
- `tests/test_validation_harness.py`
- `tests/test_live_postgres_validation.py`
- `pyproject.toml`

Validation:
- targeted validation tests passed after the expansion
- full suite passed: **127/127**
- CLI `validate-e2e` smoke run passed with the wider validation pack and replayed real queries end to end

Why this matters:
- SemZero can now test more of the schema classes and drift patterns that show up in painful daily warehouse work, not just a small demo set
- the validation harness is better at surfacing silent semantic failures like finance precision loss, nullability hardening, and distribution drift
- the pack is closer to a truth harness for rewriting rules when SemZero misses a real edge case

## 0.3.13 — safe-loop + AST/reporting hardening

- changed `scripts/test_full_loop.py` so **safe mode** now halts before live apply when Gate/Wind Tunnel block a change
- added explicit `--mode safe|validation-apply` to the full-loop harness so controlled apply/revert remains available for lab proofing
- upgraded the unified ops report to emphasise **cross-modal AST mapping proof**, including source-path findings and downstream references
- added a **visual query / error map** to the HTML ops report so teams can see source references, broken queries, and impacted nodes at a glance
- added optional Monte Carlo-style observability context parsing in ecosystem ingestion so external observability exports can enrich focus assets when provided
- kept the existing Gate -> Wind Tunnel -> Chaos loop intact while making the operator surface clearer and safer for messy workflow validation


## 0.3.14 — workflow/report rewrite + connector hardening

What changed:
- strengthened the unified ops report so it explains the full control loop more clearly:
  - problem/solution section
  - execution/isolation proof section
  - stronger AST mapping proof visibility
  - richer visual query/error/impacted-node map in HTML and Markdown
- kept the existing loop intact while making it easier to understand:
  - PreGate → Wind Tunnel → Chaos → report
  - explicit proof that Wind Tunnel ran against an isolated clone/shadow environment before live apply
- exposed Monte Carlo-style observability context through the main reliability surfaces instead of leaving it disconnected:
  - CLI options for `chaos`, `wind-tunnel`, `gate`, and `premerge`
  - config pass-through into Gate / Wind Tunnel / Premerge ecosystem context
- preserved the old code and behavior rather than replacing the stack with a new narrow path

Why it was added:
- make the product easier to validate in real messy workflows
- make AST mapping a visible proof surface, not a hidden internal feature
- make HTML exports usable as runbooks during triage and review
- keep SemZero focused on real data-engineering workflows (dbt + Airflow + Snowflake + observability context) without turning it into a connector zoo

What I did not overclaim:
- this pass improves workflow clarity, reporting, and connector plumbing, but it does not claim full live vendor hardening across every warehouse/orchestrator combination
- the graph/report/connector logic compiles cleanly, but full runtime validation in this environment still depends on the user’s installed runtime packages and live systems

## 0.3.15 — hardening + evidence-first workflow

What changed:
- added a new reliability evidence layer in `src/reliability/evidence.py`
  - records which findings were **inferred** vs **observed**
  - stores run ids, stages, failures, and evidence counts
  - writes both per-run JSON and append-only JSONL history
- upgraded `PremergeWorkflow` to emit a real evidence ledger alongside the existing Gate / Wind Tunnel / Chaos artifacts
- added **shadow mode** to premerge so SemZero can collect evidence without enabling merge-block enforcement in the bundle/report
- strengthened the unified ops report with an **Evidence ledger** section so teams can see what was actually observed in replay/mutation runs versus what was inferred statically
- promoted graph output into the report with an explicit **Graph intelligence** section and graph-ranked scope visibility
- expanded the validation harness with a new `ast_cross_modal_truth` scenario so AST mapping is validated as a first-class reliability surface rather than treated as assumed-good
- wired validation runs to use shadow-mode evidence collection so every validation pass now leaves a real evidence trail

Why it was added:
- reduce fake confidence by making the product show exactly what it proved and how
- turn AST mapping + graph intelligence into audited proof surfaces instead of only hidden ranking signals
- make messy workflow validation easier to trust because inferred and observed evidence are clearly separated
- keep the current stack usable and safe while making it more production-like and evidence-backed

Internal validation performed:
- `python -m compileall src scripts`
- `pytest -q`
- all tests passed in this environment: **127/127**
