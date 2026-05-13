"""
chaos_engine.py — SemZero Chaos Mode. The only proactive resilience
platform in data engineering.

Core philosophy: Don't wait for pipelines to break. SemZero attacks
your schema systematically every week and tells you exactly how fragile
your data architecture is — before a SWE's migration does it for you.

What makes this different from everything else:

  SMART TARGETING — Not random mutations. We compute graph centrality
  to find the highest-risk nodes (columns that, if broken, cascade
  the furthest downstream) and attack those first. Your most fragile
  points get tested the most.

  CASCADE SIMULATION — We don't just test direct impacts. We simulate
  multi-hop failure propagation: what happens when orders.user_id breaks,
  which breaks the revenue model, which breaks the exec dashboard.

  FRAGILITY DNA — Every schema has a structural fingerprint. We score
  architectural anti-patterns: wide tables, missing constraints, high
  null rates on join columns, FK chains with no defensive SQL. This is
  independent of what dbt tests you have.

  DRIFT VELOCITY — Schemas that change fast are inherently more fragile.
  We track the rate of change from watcher history and factor it into
  the score. A schema that changed 20 times last month gets a higher
  risk multiplier than one that hasn't changed in a year.

  PROPER SCORING — In graph-only mode we score cascade depth, blast
  radius breadth, and architectural anti-patterns — not just "did a
  test fail". The score is always meaningful, whether or not dbt is
  connected.
"""

from __future__ import annotations

import json
import logging
import math
import shutil
import subprocess
import tempfile
import time
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

import networkx as nx

from ..integrations.graph_intelligence import GraphIntelligenceEngine

log = logging.getLogger(__name__)


# ── Enums ─────────────────────────────────────────────────────────────────────


class ResilienceLevel(str, Enum):
    RESILIENT = "RESILIENT"
    FRAGILE = "FRAGILE"
    CRITICAL = "CRITICAL"
    UNTESTED = "UNTESTED"


class MutationType(str, Enum):
    RENAME_COLUMN = "RENAME_COLUMN"
    REMOVE_COLUMN = "REMOVE_COLUMN"
    ADD_COLUMN = "ADD_COLUMN"
    CHANGE_DTYPE = "CHANGE_DTYPE"
    CHANGE_NULLABLE = "CHANGE_NULLABLE"
    NULL_FLOOD = "NULL_FLOOD"
    BLANK_STRING_FLOOD = "BLANK_STRING_FLOOD"
    DOMAIN_EXPANSION = "DOMAIN_EXPANSION"
    KEY_SKEW = "KEY_SKEW"
    TEMPORAL_SKEW = "TEMPORAL_SKEW"
    VOLUME_SPIKE = "VOLUME_SPIKE"
    EMPTY_TABLE = "EMPTY_TABLE"
    REMOVE_TABLE = "REMOVE_TABLE"
    RENAME_TABLE = "RENAME_TABLE"


# Severity of each mutation type (how likely it is to cause real production pain)
_MUTATION_SEVERITY = {
    MutationType.REMOVE_COLUMN: 1.0,
    MutationType.REMOVE_TABLE: 1.0,
    MutationType.EMPTY_TABLE: 0.95,
    MutationType.RENAME_COLUMN: 0.9,
    MutationType.RENAME_TABLE: 0.85,
    MutationType.CHANGE_DTYPE: 0.8,
    MutationType.NULL_FLOOD: 0.75,
    MutationType.BLANK_STRING_FLOOD: 0.72,
    MutationType.DOMAIN_EXPANSION: 0.7,
    MutationType.KEY_SKEW: 0.68,
    MutationType.TEMPORAL_SKEW: 0.65,
    MutationType.VOLUME_SPIKE: 0.6,
    MutationType.CHANGE_NULLABLE: 0.5,
    MutationType.ADD_COLUMN: 0.1,
}


# ── Fragility DNA ─────────────────────────────────────────────────────────────


@dataclass
class FragilityDNA:
    """
    Structural fingerprint of a schema's fragility.
    Detected independently of dbt tests — pure graph analysis.

    Anti-patterns that increase fragility:
      - Wide tables (>50 columns) — hard to track changes
      - High null rates on FK columns — silent data loss risk
      - Deep FK chains (>4 hops) — cascading failure risk
      - Tables with no test coverage — UNTESTED risk
      - Columns shared across >3 FK relationships — central fragility point
      - High cardinality mismatch on join columns — data integrity risk
    """

    wide_tables: list[str] = field(default_factory=list)
    nullable_fk_columns: list[str] = field(default_factory=list)
    deep_fk_chains: list[str] = field(default_factory=list)
    central_columns: list[str] = field(default_factory=list)
    high_null_join_cols: list[str] = field(default_factory=list)
    isolated_tables: list[str] = field(default_factory=list)
    anti_pattern_score: int = 0  # 0=clean, 100=severely fragile

    def to_dict(self) -> dict:
        return {
            "wide_tables": self.wide_tables,
            "nullable_fk_columns": self.nullable_fk_columns,
            "deep_fk_chains": self.deep_fk_chains,
            "central_columns": self.central_columns,
            "high_null_join_cols": self.high_null_join_cols,
            "isolated_tables": self.isolated_tables,
            "anti_pattern_score": self.anti_pattern_score,
        }


# ── Cascade analysis ──────────────────────────────────────────────────────────


@dataclass
class CascadeResult:
    """Result of simulating failure propagation from a changed node."""

    origin_node: str
    hop_1_impacted: list[str] = field(default_factory=list)
    hop_2_impacted: list[str] = field(default_factory=list)
    hop_3plus_impacted: list[str] = field(default_factory=list)
    total_impacted: int = 0
    max_depth: int = 0
    cascade_score: float = 0.0  # 0=no cascade, 1=maximum cascade

    def to_dict(self) -> dict:
        return {
            "origin": self.origin_node,
            "hop_1": len(self.hop_1_impacted),
            "hop_2": len(self.hop_2_impacted),
            "hop_3plus": len(self.hop_3plus_impacted),
            "total_impacted": self.total_impacted,
            "max_depth": self.max_depth,
            "cascade_score": round(self.cascade_score, 4),
        }


# ── Mutation result ───────────────────────────────────────────────────────────


@dataclass
class MutationResult:
    mutation_type: MutationType
    node_id: str
    detail: str
    cascade: Optional[CascadeResult] = None
    tests_run: int = 0
    tests_failed: int = 0
    tests_passed: int = 0
    failed_models: list[str] = field(default_factory=list)
    error_messages: list[str] = field(default_factory=list)
    duration_s: float = 0.0
    resilience: ResilienceLevel = ResilienceLevel.UNTESTED
    blast_score: float = 0.0  # 0-1 normalised blast severity
    targeting_reason: str = ""  # Why this node was targeted
    recovery_verified: Optional[bool] = None
    manual_backfill_required: bool = False
    recovery_notes: list[str] = field(default_factory=list)

    @property
    def failure_rate(self) -> float:
        return self.tests_failed / self.tests_run if self.tests_run else 0.0

    def to_dict(self) -> dict:
        return {
            "mutation_type": self.mutation_type.value,
            "node_id": self.node_id,
            "detail": self.detail,
            "resilience": self.resilience.value,
            "blast_score": round(self.blast_score, 4),
            "tests_run": self.tests_run,
            "tests_failed": self.tests_failed,
            "failure_rate": round(self.failure_rate, 4),
            "failed_models": self.failed_models[:10],
            "cascade": self.cascade.to_dict() if self.cascade else None,
            "duration_s": round(self.duration_s, 2),
            "targeting_reason": self.targeting_reason,
            "recovery_verified": self.recovery_verified,
            "manual_backfill_required": self.manual_backfill_required,
            "recovery_notes": self.recovery_notes[:4],
        }


# ── Pipeline fragility ────────────────────────────────────────────────────────


@dataclass
class PipelineFragility:
    model_name: str
    resilience: ResilienceLevel
    breaking_mutations: list[str] = field(default_factory=list)
    fragility_score: int = 0
    cascade_exposure: float = 0.0
    recommendation: str = ""
    auto_fix_available: bool = False

    def to_dict(self) -> dict:
        emoji = {"CRITICAL": "🔴", "FRAGILE": "⚠️", "RESILIENT": "✅", "UNTESTED": "❓"}
        return {
            "model_name": self.model_name,
            "resilience": self.resilience.value,
            "resilience_emoji": emoji.get(self.resilience.value, "❓"),
            "breaking_mutations": self.breaking_mutations,
            "fragility_score": self.fragility_score,
            "cascade_exposure": round(self.cascade_exposure, 3),
            "recommendation": self.recommendation,
            "auto_fix_available": self.auto_fix_available,
        }


# ── Chaos report ──────────────────────────────────────────────────────────────


@dataclass
class ChaosReport:
    run_id: str
    started_at: str
    completed_at: str = ""
    duration_s: float = 0.0
    mode: str = "graph"  # graph | dbt | snowflake

    mutation_results: list[MutationResult] = field(default_factory=list)
    pipeline_fragility: list[PipelineFragility] = field(default_factory=list)
    fragility_dna: Optional[FragilityDNA] = None

    fragility_score: int = 0
    fragility_grade: str = "?"
    drift_velocity: float = 0.0  # mutations/week from history
    risk_multiplier: float = 1.0  # drift_velocity increases risk

    mutations_applied: int = 0
    mutations_that_broke: int = 0
    total_tests_run: int = 0
    total_tests_failed: int = 0
    error: Optional[str] = None
    top_oncall_triggers: list[str] = field(default_factory=list)
    recommended_hardening: list[str] = field(default_factory=list)
    focus_assets: list[str] = field(default_factory=list)
    recovery_summary: dict = field(default_factory=dict)
    recovery_playbook: list[str] = field(default_factory=list)
    graph_intelligence: dict = field(default_factory=dict)
    budget_summary: dict = field(default_factory=dict)

    @property
    def critical_pipelines(self) -> list[PipelineFragility]:
        return [p for p in self.pipeline_fragility if p.resilience == ResilienceLevel.CRITICAL]

    @property
    def fragile_pipelines(self) -> list[PipelineFragility]:
        return [p for p in self.pipeline_fragility if p.resilience == ResilienceLevel.FRAGILE]

    @property
    def resilient_pipelines(self) -> list[PipelineFragility]:
        return [p for p in self.pipeline_fragility if p.resilience == ResilienceLevel.RESILIENT]

    def compute_score(self, history: Optional[list[dict]] = None) -> None:
        """
        Compute the 0-100 Fragility Score.

        Combines three signals:
          1. Mutation resilience rate (% of mutations that didn't cascade)
          2. Anti-pattern score from Fragility DNA
          3. Drift velocity multiplier from history
        """
        if not self.mutation_results:
            self.fragility_score = 0
            self.fragility_grade = "?"
            return

        # Signal 1: Mutation resilience — weighted by mutation danger type.
        #
        # Not all mutation failures are equal:
        #   REMOVE_COLUMN breaking = full danger (1.0x weight)
        #   RENAME_COLUMN breaking = full danger (1.0x weight)
        #   CHANGE_DTYPE  breaking = moderate (0.5x) — many consumers handle widening
        #   CHANGE_NULLABLE breaking = low (0.3x) — rarely causes hard failures
        #
        # This prevents soft mutations from tanking the score when more
        # mutation types are included in the run.
        _DANGER_WEIGHT = {
            "REMOVE_COLUMN": 1.0,
            "RENAME_COLUMN": 1.0,
            "CHANGE_DTYPE": 0.5,
            "CHANGE_NULLABLE": 0.3,
        }

        total_weight = 0.0
        passed_weight = 0.0
        for r in self.mutation_results:
            mt = r.mutation_type.value if r.mutation_type else "RENAME_COLUMN"
            danger = _DANGER_WEIGHT.get(mt, 1.0)
            weight = (1.0 + r.blast_score) * danger
            total_weight += weight
            if r.resilience == ResilienceLevel.RESILIENT:
                passed_weight += weight
            elif r.resilience == ResilienceLevel.FRAGILE:
                # Fragile = breaks under some conditions, not catastrophically.
                # 30% credit: better than broken (0%), worse than resilient (100%).
                # Calibrated so a "mostly fragile, nothing critical" schema
                # scores around 55-65, not 80+.
                passed_weight += weight * 0.5  # FRAGILE = 50% credit (not 30%)
            elif r.resilience == ResilienceLevel.UNTESTED:
                passed_weight += weight * 0.25

        resilience_rate = passed_weight / total_weight if total_weight else 0.5
        base_score = int(resilience_rate * 100)

        # Signal 2: Anti-pattern penalty from DNA
        dna_penalty = 0
        if self.fragility_dna:
            dna_penalty = min(25, self.fragility_dna.anti_pattern_score // 4)

        # Signal 3: Drift velocity multiplier
        # Schemas changing fast are harder to keep resilient
        velocity_penalty = 0
        if history and len(history) >= 2:
            recent_drifts = [h.get("mutations_that_broke", 0) for h in history[-4:]]
            avg_breaks = sum(recent_drifts) / len(recent_drifts)
            self.drift_velocity = avg_breaks
            if avg_breaks > 5:
                velocity_penalty = min(10, int(avg_breaks))

        raw = max(0, base_score - dna_penalty - velocity_penalty)

        # Apply risk multiplier for high-velocity schemas
        self.risk_multiplier = 1.0 + (self.drift_velocity / 20.0)
        self.fragility_score = max(0, min(100, int(raw / self.risk_multiplier)))

        self.fragility_grade = (
            "A"
            if self.fragility_score >= 90
            else "B"
            if self.fragility_score >= 80
            else "C"
            if self.fragility_score >= 70
            else "D"
            if self.fragility_score >= 60
            else "F"
        )

    def finalise_debug_summary(self) -> None:
        triggers: list[str] = []
        actions: list[str] = []
        focus: list[str] = []
        for result in sorted(
            self.mutation_results, key=lambda r: (-r.tests_failed, -r.blast_score)
        )[:6]:
            if result.tests_failed:
                triggers.append(
                    f"{result.mutation_type.value} on {result.node_id} failed {result.tests_failed}/{max(result.tests_run, 1)} checks"
                )
                focus.append(result.node_id)
                if result.targeting_reason:
                    actions.append(f"Harden {result.node_id}: {result.targeting_reason}.")
        for pipeline in self.critical_pipelines[:3]:
            actions.append(
                pipeline.recommendation or f"Stabilise {pipeline.model_name} before merge."
            )
            focus.append(pipeline.model_name)
        if self.fragility_dna:
            for col in self.fragility_dna.high_null_join_cols[:2]:
                triggers.append(f"High-null join path: {col}")
            if self.fragility_dna.nullable_fk_columns:
                actions.append(
                    "Add defensive join handling and null checks on nullable foreign-key paths."
                )
            if self.fragility_dna.deep_fk_chains:
                actions.append(
                    "Add narrower blast-radius tests around the deepest FK chains before high-risk releases."
                )
        recovered = sum(1 for r in self.mutation_results if r.recovery_verified is True)
        attempted_recovery = sum(
            1 for r in self.mutation_results if r.recovery_verified is not None
        )
        unrecovered = sum(1 for r in self.mutation_results if r.recovery_verified is False)
        manual = sum(1 for r in self.mutation_results if r.manual_backfill_required)
        recoverability_score = (
            round((recovered / attempted_recovery) * 100, 1) if attempted_recovery else 0.0
        )
        playbook: list[str] = []
        if manual:
            actions.append(
                "Stateful mutations required restore/backfill steps before workloads stabilised."
            )
            playbook.append(
                "Preserve a restore path or clone checkpoint before mutating critical tables in rollout validation."
            )
            playbook.append(
                "If recovery verification fails, require a backfill plan and owner sign-off before merge."
            )
        if unrecovered:
            playbook.append(
                "Escalate unrecovered mutations into a release blocker; they do not self-heal after bad data stops."
            )
        if attempted_recovery and not playbook:
            playbook.append(
                "Recovery verification passed; keep the replay receipt as evidence for rollback-free recovery."
            )
        self.top_oncall_triggers = list(dict.fromkeys(item for item in triggers if item))[:6]
        self.recommended_hardening = list(dict.fromkeys(item for item in actions if item))[:6]
        self.focus_assets = list(dict.fromkeys(item for item in focus if item))[:10]
        self.recovery_summary = {
            "verified_recoveries": recovered,
            "recovery_attempts": attempted_recovery,
            "recoverability_score": recoverability_score,
            "manual_backfill_required": manual,
            "unrecovered_mutations": unrecovered,
        }
        self.recovery_playbook = list(dict.fromkeys(item for item in playbook if item))[:5]

    def summary(self) -> dict:
        self.finalise_debug_summary()
        return {
            "run_id": self.run_id,
            "mode": self.mode,
            "fragility_score": self.fragility_score,
            "fragility_grade": self.fragility_grade,
            "drift_velocity": round(self.drift_velocity, 2),
            "risk_multiplier": round(self.risk_multiplier, 2),
            "mutations_applied": self.mutations_applied,
            "mutations_that_broke": self.mutations_that_broke,
            "total_tests_run": self.total_tests_run,
            "total_tests_failed": self.total_tests_failed,
            "critical_pipelines": len(self.critical_pipelines),
            "fragile_pipelines": len(self.fragile_pipelines),
            "resilient_pipelines": len(self.resilient_pipelines),
            "duration_s": round(self.duration_s, 2),
            "anti_pattern_score": self.fragility_dna.anti_pattern_score
            if self.fragility_dna
            else 0,
            "recovery_summary": self.recovery_summary,
            "recovery_playbook": self.recovery_playbook,
        }

    def to_dict(self) -> dict:
        return {
            "summary": self.summary(),
            "fragility_dna": self.fragility_dna.to_dict() if self.fragility_dna else {},
            "mutation_results": [r.to_dict() for r in self.mutation_results],
            "pipeline_fragility": [p.to_dict() for p in self.pipeline_fragility],
            "top_oncall_triggers": self.top_oncall_triggers,
            "recommended_hardening": self.recommended_hardening,
            "focus_assets": self.focus_assets,
            "recovery_summary": self.recovery_summary,
            "recovery_playbook": self.recovery_playbook,
            "graph_intelligence": self.graph_intelligence,
            "budget_summary": self.budget_summary,
            "error": self.error,
        }

    def save(self, path: str = "data/chaos_report.json") -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), indent=2, default=str))
        log.info(f"Chaos report → {p}")
        return p

    def to_pr_comment(self, limit: int = 5) -> str:
        self.finalise_debug_summary()
        lines = [
            "### 🔥 SemZero Chaos Resilience",
            "",
            f"Fragility score **{self.fragility_score}/100** ({self.fragility_grade}) · {self.mutations_that_broke} breaking mutations · {len(self.critical_pipelines)} critical pipelines",
            "",
        ]
        top = sorted(
            self.mutation_results, key=lambda r: (-r.tests_failed, -r.blast_score, r.node_id)
        )
        if top:
            lines += ["| Mutation | Node | Failed checks | Why it matters |", "|---|---|---:|---|"]
            for result in top[:limit]:
                lines.append(
                    f"| `{result.mutation_type.value}` | `{result.node_id}` | {result.tests_failed}/{result.tests_run} | {result.targeting_reason[:90]} |"
                )
        if self.pipeline_fragility:
            lines += ["", "Critical pipelines:"]
            for pipe in self.critical_pipelines[:3]:
                lines.append(f"- `{pipe.model_name}` → {pipe.recommendation}")
        return "\n".join(lines)


# ── Config ────────────────────────────────────────────────────────────────────


@dataclass
class ChaosConfig:
    db_url: str = ""
    dialect: str = "auto"
    snowflake_account: str = ""
    snowflake_user: str = ""
    snowflake_password: str = ""
    snowflake_database: str = ""
    snowflake_schema: str = "PUBLIC"
    snowflake_warehouse: str = ""
    databricks_server_hostname: str = ""
    databricks_http_path: str = ""
    databricks_token: str = ""
    databricks_catalog: str = ""
    databricks_schema: str = "default"
    databricks_clone_catalog: str = ""
    clone_prefix: str = "SEMZERO_CHAOS"
    dbt_project_path: str = ""
    dbt_target: str = "dev"
    dbt_profiles_dir: str = ""
    run_dbt_tests: bool = False
    mutation_count: int = 50
    mutation_seed: int = 42
    parallel_mutations: bool = True
    max_workers: int = 4
    data_dir: str = "data"
    generate_html: bool = True
    github_repo: str = ""
    github_token: str = ""
    slack_webhook: str = ""
    slack_channel: str = "#data-alerts"
    dry_run: bool = False
    auto_destroy_clone: bool = True
    store_path: str = "data/graph_store.db"
    history_path: str = "data/chaos_history.json"
    workload_replay: bool = True
    workload_query_source: str = "auto"
    workload_max_queries: int = 50
    workload_per_mutation_limit: int = 20
    workload_lookback_days: int = 7
    workload_query_files: list[str] = field(default_factory=list)
    workload_query_directories: list[str] = field(default_factory=list)
    workload_history_files: list[str] = field(default_factory=list)
    focus_assets: list[str] = field(default_factory=list)
    dbt_manifest_path: str = ""
    dbt_run_results_path: str = ""
    dbt_catalog_path: str = ""
    dbt_sources_path: str = ""
    openlineage_paths: list[str] = field(default_factory=list)
    airflow_paths: list[str] = field(default_factory=list)
    dagster_paths: list[str] = field(default_factory=list)
    looker_paths: list[str] = field(default_factory=list)
    montecarlo_paths: list[str] = field(default_factory=list)
    graph_intelligence_enabled: bool = True
    rgcn_model_path: str = ""
    mutation_sample_pct: float = 0.01
    null_flood_pct: float = 0.15
    temporal_skew_pct: float = 0.05
    temporal_skew_days: int = 7
    volume_spike_multiplier: int = 10
    stateful_recovery: bool = False
    recovery_replay_limit: int = 5


# ── Main engine ───────────────────────────────────────────────────────────────


class ChaosEngine:
    """
    Orchestrates a complete Chaos Mode run.

    The engine operates in three modes:
      graph  — Pure graph analysis. No real DB. Works always.
               Scores based on cascade depth + Fragility DNA.
      dbt    — Graph analysis + real dbt test execution.
               Scores based on actual test failures.
      snowflake — Full Snowflake zero-copy clone + SQL mutations + dbt tests.
      databricks — SHALLOW CLONE into an isolated schema + SQL mutations + replay.
               Most accurate. Requires Snowflake + dbt.
    """

    def __init__(self, config: ChaosConfig) -> None:
        self.config = config
        self.data_dir = Path(config.data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._history = self._load_history()
        self._dialect = self._detect_dialect()
        self._orig_engine = None
        self._workload_queries: list[dict] = []
        self._pg_clone_urls: dict[str, str] = {}
        self._sqlite_clone_paths: dict[str, str] = {}
        self._dbx_clone_urls: dict[str, str] = {}
        self._dbx_clone_meta: dict[str, dict[str, Any]] = {}

    def run(self, graph_json: Optional[dict] = None) -> ChaosReport:
        run_id = str(uuid.uuid4())[:8]
        report = ChaosReport(
            run_id=run_id,
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        start = time.time()

        self._banner(run_id)

        try:
            # Step 1: Get schema graph
            if graph_json is None:
                log.info("Step 1/7: Crawling database schema...")
                graph_json = self._crawl()
            else:
                log.info("Step 1/7: Using provided schema graph.")

            meta = graph_json.get("meta", {})
            log.info(
                f"         {meta.get('table_count', 0)} tables, {meta.get('node_count', 0)} nodes"
            )

            # Step 2: Analyse Fragility DNA
            log.info("Step 2/7: Analysing Fragility DNA...")
            report.fragility_dna = self._analyse_dna(graph_json)
            log.info(f"         Anti-pattern score: {report.fragility_dna.anti_pattern_score}/100")

            # Step 3: Smart mutation targeting
            log.info("Step 3/7: Computing smart mutation targets...")
            G, targets = self._compute_targets(graph_json)
            report.graph_intelligence = (
                GraphIntelligenceEngine(
                    graph_json,
                    enabled=self.config.graph_intelligence_enabled,
                    rgcn_model_path=self.config.rgcn_model_path,
                )
                .analyse(focus_node_ids=self.config.focus_assets)
                .to_dict()
            )
            log.info(f"         {len(targets)} high-risk nodes identified.")

            # Step 4: Generate mutation plan
            log.info("Step 4/7: Building mutation plan...")
            plan = self._build_plan(graph_json, targets, G)
            report.budget_summary = {
                "candidate_targets": len(targets),
                "selected_mutations": len(plan),
                "deferred_mutations": max(
                    0,
                    len(targets)
                    - len({item.get("node_id") for item in plan if item.get("node_id")}),
                ),
                "focus_assets": list(self.config.focus_assets)[:10],
                "compute_saved_pct": round(
                    (max(0, len(targets) - len(plan)) / len(targets)) * 100.0, 1
                )
                if targets
                else 0.0,
                "scoping_mode": "risk-ranked" if self.config.focus_assets else "risk-ranked-global",
            }
            log.info(f"         {len(plan)} mutations planned.")

            # Step 5: Prepare workload intelligence
            log.info("Step 5/7: Preparing workload intelligence...")
            self._prepare_workload(graph_json)
            report.mode = self._report_mode()

            # Step 6: Execute mutations
            log.info("Step 6/7: Executing chaos mutations...")
            results = self._execute_plan(plan, run_id, graph_json, G)
            report.mutation_results = results
            report.mutations_applied = len(results)
            report.mutations_that_broke = sum(
                1
                for r in results
                if r.resilience in (ResilienceLevel.CRITICAL, ResilienceLevel.FRAGILE)
            )
            report.total_tests_run = sum(r.tests_run for r in results)
            report.total_tests_failed = sum(r.tests_failed for r in results)

            # Step 7: Score everything
            log.info("Step 7/7: Computing fragility scores...")
            report.pipeline_fragility = self._score_pipelines(results, graph_json)
            report.compute_score(self._history)

        except Exception as e:
            log.error(f"Chaos run failed: {e}", exc_info=True)
            report.error = str(e)
        finally:
            if self._orig_engine is not None:
                try:
                    self._orig_engine.dispose()
                except Exception:
                    pass

        report.completed_at = datetime.now(timezone.utc).isoformat()
        report.duration_s = round(time.time() - start, 2)

        self._log_summary(report)
        return report

    def _detect_dialect(self) -> str:
        url = (self.config.db_url or "").lower()
        if self.config.dialect and self.config.dialect != "auto":
            return self.config.dialect.lower()
        if self.config.snowflake_account or url.startswith("snowflake"):
            return "snowflake"
        if (
            self.config.databricks_http_path
            or self.config.databricks_server_hostname
            or url.startswith("databricks")
            or url.startswith("databricks+connector")
        ):
            return "databricks"
        if url.startswith("postgresql") or url.startswith("postgres"):
            return "postgresql"
        if url.startswith("sqlite"):
            return "sqlite"
        if url.startswith("bigquery"):
            return "bigquery"
        return "graph"

    def _report_mode(self) -> str:
        if self.config.run_dbt_tests and self.config.dbt_project_path:
            return "dbt"
        if self.config.workload_replay and self.config.db_url:
            return self._dialect
        return "graph"

    def _prepare_workload(self, graph_json: dict) -> None:
        if not self.config.db_url or not self.config.workload_replay:
            self._workload_queries = []
            return
        try:
            from sqlalchemy import create_engine
            from .wind_tunnel import QueryExtractor, WindTunnelConfig

            if self._orig_engine is None:
                kwargs = {"pool_pre_ping": True}
                if self._dialect == "sqlite":
                    kwargs["connect_args"] = {"check_same_thread": False}
                self._orig_engine = create_engine(self.config.db_url, **kwargs)

            wt_config = WindTunnelConfig(
                db_url=self.config.db_url,
                max_queries=self.config.workload_max_queries,
                lookback_days=self.config.workload_lookback_days,
                query_source=self.config.workload_query_source,
                query_files=list(self.config.workload_query_files),
                query_directories=list(self.config.workload_query_directories),
                workload_history_files=list(self.config.workload_history_files),
                dbt_manifest_path=self.config.dbt_manifest_path,
                dbt_run_results_path=self.config.dbt_run_results_path,
                dbt_sources_path=self.config.dbt_sources_path,
                focus_assets=list(self.config.focus_assets),
                compare_row_counts=True,
                compare_value_fingerprints=True,
            )
            extractor = QueryExtractor(self._orig_engine, self._dialect, wt_config)
            self._workload_queries = extractor.extract(graph_json)
            log.info("         %s workload queries prepared.", len(self._workload_queries))
        except Exception as exc:
            log.warning("Workload preparation failed, falling back to graph-only chaos: %s", exc)
            self._workload_queries = []

    def _queries_for_entry(self, entry: dict) -> list[dict]:
        if not self._workload_queries:
            return []
        table = str(entry.get("table") or entry.get("node_id", "").split(".")[0]).lower()
        col = str(entry.get("col_name") or entry.get("node_id", "").split(".")[-1]).lower()
        scope_assets = {str(asset).lower() for asset in entry.get("scope_assets", []) if asset}
        ranked: list[tuple[int, dict]] = []
        for query in self._workload_queries:
            sql = str(query.get("query_text") or "").lower()
            tables = {str(t).lower() for t in query.get("tables", [])}
            columns = {str(c).lower() for c in query.get("columns", [])}
            assets = {str(a).lower() for a in query.get("assets", [])}
            score = 0
            if table and (table in tables or table in sql):
                score += 4
            if col and (col in columns or f"{table}.{col}" in assets or col in sql):
                score += 3
            scope_hits = len(scope_assets & assets) + len(scope_assets & tables)
            score += min(4, scope_hits)
            if query.get("join_count"):
                score += 1
            if query.get("is_aggregate"):
                score += 1
            if (
                score == 0
                and entry.get("type")
                in {
                    MutationType.REMOVE_TABLE,
                    MutationType.RENAME_TABLE,
                    MutationType.EMPTY_TABLE,
                    MutationType.VOLUME_SPIKE,
                }
                and table in sql
            ):
                score += 2
            if score:
                ranked.append((score, query))
        ranked.sort(key=lambda item: (-item[0], item[1].get("query_id", "")))
        picked = [q for _, q in ranked[: self.config.workload_per_mutation_limit]]
        if picked:
            return picked
        return self._workload_queries[: self.config.workload_per_mutation_limit]

    # ── Schema crawl ──────────────────────────────────────────────────────────

    def _crawl(self) -> dict:
        from ..crawler.connector_factory import get_connector
        from ..crawler.builder import SchemaGraphBuilder
        from ..crawler.graph_store import GraphStore

        connector = get_connector(self.config.db_url)
        store = GraphStore(self.config.store_path)
        builder = SchemaGraphBuilder.__new__(SchemaGraphBuilder)
        builder.connector = connector
        builder.store = store
        builder.graph = {"meta": {}, "nodes": [], "edges": []}
        return builder.build(label="chaos_baseline")

    # ── Fragility DNA analysis ────────────────────────────────────────────────

    def _analyse_dna(self, graph_json: dict) -> FragilityDNA:
        """
        Detect architectural anti-patterns that make schemas fragile.
        This runs purely on the graph — no DB connection needed.
        """
        dna = FragilityDNA()
        nodes = graph_json.get("nodes", [])
        edges = graph_json.get("edges", [])
        penalty = 0

        tables = {n["id"]: n for n in nodes if n["label"] == "Table"}
        cols = {n["id"]: n for n in nodes if n["label"] == "Column"}

        # Build adjacency for analysis
        G = nx.DiGraph()
        for n in nodes:
            G.add_node(n["id"], **n)
        for e in edges:
            G.add_edge(
                e["source"],
                e["target"],
                relation=e.get("relation", ""),
                weight=e.get("weight", 1.0),
            )

        # Anti-pattern 1: Wide tables (>30 columns)
        table_col_counts: dict[str, int] = defaultdict(int)
        for col in cols.values():
            table_col_counts[col.get("table", "")] += 1

        for table, count in table_col_counts.items():
            if count > 30:
                dna.wide_tables.append(f"{table} ({count} cols)")
                penalty += min(15, count // 3)

        # Anti-pattern 2: Nullable FK columns (silent NULL propagation)
        ref_cols: set[str] = set()
        for e in edges:
            if e.get("relation") == "REFERENCES":
                ref_cols.add(e["source"])

        for col_id in ref_cols:
            col = cols.get(col_id, {})
            if col.get("nullable", True) and not col.get("is_primary_key", False):
                dna.nullable_fk_columns.append(col_id)
                penalty += 5

        # Anti-pattern 3: Deep FK chains (>3 hops of FK dependencies)
        ref_graph = nx.DiGraph()
        for e in edges:
            if e.get("relation") == "REFERENCES":
                ref_graph.add_edge(e["source"], e["target"])

        for node in list(ref_graph.nodes()):
            try:
                path_len = nx.single_source_shortest_path_length(ref_graph, node)
                max_depth = max(path_len.values()) if path_len else 0
                if max_depth >= 3:
                    dna.deep_fk_chains.append(f"{node} (depth {max_depth})")
                    penalty += max_depth * 3
            except Exception:
                pass

        # Anti-pattern 4: High-centrality columns (break = many things fail)
        # Betweenness centrality finds columns that "bridge" the schema
        try:
            centrality = nx.betweenness_centrality(G, normalized=True)
            col_centrality = {
                nid: score for nid, score in centrality.items() if nid in cols and score > 0.1
            }
            top_central = sorted(col_centrality.items(), key=lambda x: -x[1])[:5]
            dna.central_columns = [f"{nid} ({score:.2f})" for nid, score in top_central]
            penalty += len(dna.central_columns) * 8
        except Exception:
            pass

        # Anti-pattern 5: High null rate on join columns
        for col_id in ref_cols:
            col = cols.get(col_id, {})
            null_rate = col.get("null_rate", 0.0)
            if null_rate > 0.05:  # >5% nulls on a FK column = problem
                dna.high_null_join_cols.append(f"{col_id} ({null_rate:.1%} null)")
                penalty += int(null_rate * 50)

        # Anti-pattern 6: Isolated tables (no FK connections)
        for table_id in tables:
            connected = any(
                e.get("relation") == "REFERENCES"
                and (
                    e["source"].startswith(table_id + ".") or e["target"].startswith(table_id + ".")
                )
                for e in edges
            )
            if not connected and table_col_counts.get(table_id, 0) > 2:
                dna.isolated_tables.append(table_id)
                penalty += 3  # Low penalty — sometimes intentional

        # Rebalance: isolated tables alone should not dominate the score
        table_count = max(1, len(tables))
        wide_score = min(30, len(dna.wide_tables) * 6)
        nullable_score = min(20, len(dna.nullable_fk_columns) * 4)
        chain_score = min(20, len(dna.deep_fk_chains) * 5)
        central_score = min(15, len(dna.central_columns) * 3)
        null_join_score = min(10, len(dna.high_null_join_cols) * 3)
        isolated_pct = len(dna.isolated_tables) / table_count
        isolated_score = min(5, int(isolated_pct * 10))  # cap at 5
        dna.anti_pattern_score = min(
            100,
            wide_score
            + nullable_score
            + chain_score
            + central_score
            + null_join_score
            + isolated_score,
        )
        return dna

    # ── Smart targeting ───────────────────────────────────────────────────────

    def _compute_targets(self, graph_json: dict) -> tuple[nx.DiGraph, list[dict]]:
        """
        Compute mutation targets using structural centrality plus workload heuristics.

        The goal is to spend chaos budget on the columns that data teams actually
        touch every week: join keys, high-read tables, low-quality nullable joins,
        and columns that sit in the middle of many downstream paths.
        """
        nodes = graph_json.get("nodes", [])
        edges = graph_json.get("edges", [])

        G = nx.DiGraph()
        for node in nodes:
            G.add_node(node["id"], **node)
        for edge in edges:
            G.add_edge(
                edge["source"],
                edge["target"],
                relation=edge.get("relation", ""),
                weight=edge.get("weight", 1.0),
            )

        table_nodes = {n["id"]: n for n in nodes if n.get("label") == "Table"}
        columns = [n for n in nodes if n.get("label") == "Column"]
        focus_tokens = {str(item).lower() for item in self.config.focus_assets if item}
        if focus_tokens:
            filtered = []
            for col in columns:
                table_id = str(col.get("table", col.get("id", "").split(".")[0])).lower()
                col_name = str(col.get("name", col.get("id", "").split(".")[-1])).lower()
                col_id = str(col.get("id", "")).lower()
                tokens = {table_id, col_name, col_id, f"{table_id}.{col_name}"}
                if tokens & focus_tokens or any(
                    tok.startswith(table_id + ".") for tok in focus_tokens
                ):
                    filtered.append(col)
            if filtered:
                columns = filtered
        if not columns:
            return G, []

        try:
            centrality = nx.betweenness_centrality(G, normalized=True)
        except Exception:
            centrality = {n["id"]: 0.0 for n in nodes}

        downstream: dict[str, int] = {}
        for node in nodes:
            try:
                downstream[node["id"]] = len(nx.descendants(G, node["id"]))
            except Exception:
                downstream[node["id"]] = 0
        max_downstream = max(downstream.values()) if downstream else 1

        reference_sources = {e["source"] for e in edges if e.get("relation") == "REFERENCES"}
        reference_targets = {e["target"] for e in edges if e.get("relation") == "REFERENCES"}
        graph_intelligence = GraphIntelligenceEngine(
            graph_json,
            enabled=self.config.graph_intelligence_enabled,
            rgcn_model_path=self.config.rgcn_model_path,
        ).analyse(focus_node_ids=self.config.focus_assets)

        targets: list[dict] = []
        for col in columns:
            col_id = col["id"]
            table_id = col.get("table", col_id.split(".")[0])
            table = table_nodes.get(table_id, {})
            freq = float(table.get("query_frequency", 0) or 0)
            workload_score = min(1.0, math.log1p(freq) / 7.0) if freq else 0.0
            null_penalty = min(1.0, float(col.get("null_rate", 0.0) or 0.0) * 4)
            cardinality = float(col.get("cardinality", 0.0) or 0.0)
            is_join_key = col_id in reference_sources or col_id in reference_targets
            join_bonus = 0.25 if is_join_key else 0.0
            pk_bonus = 0.2 if col.get("is_primary_key") else 0.0
            index_bonus = 0.1 if col.get("is_indexed") else 0.0
            downstream_score = downstream.get(col_id, 0) / max(max_downstream, 1)
            centrality_score = float(centrality.get(col_id, 0.0) or 0.0)
            cardinality_score = min(1.0, cardinality)
            semantic_roles = self._semantic_roles(col)
            semantic_bonus = min(0.22, 0.05 * len(semantic_roles))
            graph_signal = graph_intelligence.for_node(col_id)
            graph_bonus = min(0.22, (graph_signal.score if graph_signal else 0.0) * 0.22)
            name = str(col.get("name", col.get("id", "").split(".")[-1])).lower()
            bare_id_penalty = (
                0.12 if name == "id" and not is_join_key and not semantic_roles else 0.0
            )
            business_critical_bonus = (
                0.12
                if any(
                    tok in name
                    for tok in (
                        "amount",
                        "total",
                        "revenue",
                        "gmv",
                        "currency",
                        "campaign",
                        "severity",
                        "event_type",
                        "plan",
                        "mrr",
                        "refund",
                        "status",
                        "state",
                    )
                )
                else 0.0
            )

            risk_score = min(
                1.0,
                centrality_score * 0.23
                + downstream_score * 0.22
                + workload_score * 0.18
                + null_penalty * 0.08
                + cardinality_score * 0.04
                + join_bonus
                + pk_bonus
                + index_bonus
                + semantic_bonus
                + graph_bonus
                + business_critical_bonus
                - bare_id_penalty,
            )

            mutation_types = self._best_mutations_for_col(col, semantic_roles)
            targets.append(
                {
                    "node_id": col_id,
                    "node_data": col,
                    "risk_score": risk_score,
                    "downstream": downstream.get(col_id, 0),
                    "centrality": centrality_score,
                    "workload": workload_score,
                    "mutations": mutation_types,
                    "semantic_roles": semantic_roles,
                    "graph_intelligence_score": graph_signal.score if graph_signal else 0.0,
                    "graph_intelligence_provider": graph_signal.provider
                    if graph_signal
                    else "heuristic",
                    "reason": self._targeting_reason(
                        col,
                        centrality_score,
                        downstream.get(col_id, 0),
                        freq,
                        is_join_key,
                        semantic_roles,
                    )
                    + (f" Graph intelligence={graph_signal.score:.2f}." if graph_signal else ""),
                }
            )

        targets.sort(key=lambda item: (-item["risk_score"], -item["downstream"], item["node_id"]))
        return G, targets

    def _semantic_roles(self, col: dict) -> list[str]:
        name = str(col.get("name", col.get("id", "").split(".")[-1])).lower()
        roles: list[str] = []
        if (
            name == "id"
            or name.endswith("_id")
            or name.endswith("_ref")
            or name.endswith("_key")
            or name in {"email", "external_user_id"}
        ):
            roles.append("identity")
        if any(tok in name for tok in ("status", "state", "type", "category", "segment")):
            roles.append("domain")
        if any(
            tok in name
            for tok in ("amount", "revenue", "price", "cost", "total", "qty", "quantity")
        ):
            roles.append("metric")
        if any(tok in name for tok in ("date", "time", "_at", "ts", "created", "updated")):
            roles.append("temporal")
        if bool(col.get("is_primary_key")):
            roles.append("primary_key")
        return list(dict.fromkeys(roles))

    def _best_mutations_for_col(
        self, col: dict, semantic_roles: Optional[list[str]] = None
    ) -> list[MutationType]:
        """Choose realistic mutation types for a given column."""
        semantic_roles = semantic_roles or []
        mutations: list[MutationType] = [MutationType.RENAME_COLUMN]
        dtype = (col.get("dtype") or "UNKNOWN").upper().split("(")[0]
        is_pk = bool(col.get("is_primary_key"))
        is_indexed = bool(col.get("is_indexed"))
        nullable = bool(col.get("nullable", True))

        if not is_pk:
            mutations.append(MutationType.REMOVE_COLUMN)
        if dtype in {
            "INTEGER",
            "INT",
            "BIGINT",
            "SMALLINT",
            "FLOAT",
            "DOUBLE",
            "REAL",
            "NUMERIC",
            "DECIMAL",
            "VARCHAR",
            "TEXT",
            "TIMESTAMP",
            "DATE",
            "DATETIME",
        }:
            mutations.append(MutationType.CHANGE_DTYPE)
        if not is_indexed or nullable:
            mutations.append(MutationType.CHANGE_NULLABLE)
        if not is_pk and dtype in {"VARCHAR", "TEXT", "INTEGER", "INT", "BIGINT"}:
            mutations.append(MutationType.ADD_COLUMN)
        if not is_pk and dtype not in {"JSON", "JSONB"}:
            mutations.append(MutationType.NULL_FLOOD)
        if (
            not is_pk
            and dtype in {"VARCHAR", "TEXT", "CHAR", "STRING"}
            and ("identity" in semantic_roles or "domain" in semantic_roles)
        ):
            mutations.append(MutationType.BLANK_STRING_FLOOD)
        if "domain" in semantic_roles and dtype in {"VARCHAR", "TEXT", "CHAR", "STRING"}:
            mutations.append(MutationType.DOMAIN_EXPANSION)
        if (
            "identity" in semantic_roles
            and not is_pk
            and dtype in {"VARCHAR", "TEXT", "CHAR", "STRING", "INTEGER", "INT", "BIGINT"}
        ):
            mutations.append(MutationType.KEY_SKEW)
        if dtype in {"TIMESTAMP", "DATE", "DATETIME"} or "temporal" in semantic_roles:
            mutations.append(MutationType.TEMPORAL_SKEW)
        if "metric" in semantic_roles:
            mutations.append(MutationType.VOLUME_SPIKE)
        if "domain" in semantic_roles and not is_pk:
            mutations.append(MutationType.NULL_FLOOD)
        if "identity" in semantic_roles and not is_pk:
            mutations.append(MutationType.EMPTY_TABLE)
        return list(dict.fromkeys(mutations))

    def _targeting_reason(
        self,
        col: dict,
        centrality: float,
        downstream: int,
        query_frequency: float,
        is_join_key: bool,
        semantic_roles: Optional[list[str]] = None,
    ) -> str:
        reasons = []
        if is_join_key:
            reasons.append("join key")
        if col.get("is_primary_key"):
            reasons.append("primary key")
        if centrality > 0.1:
            reasons.append(f"high centrality ({centrality:.2f})")
        if downstream > 5:
            reasons.append(f"{downstream} downstream nodes")
        if query_frequency >= 100:
            reasons.append(f"high read frequency (~{int(query_frequency):,}/day)")
        null_rate = float(col.get("null_rate", 0.0) or 0.0)
        if null_rate > 0.1:
            reasons.append(f"high null rate ({null_rate:.1%})")
        for role in semantic_roles or []:
            if role not in {"primary_key"}:
                reasons.append(role.replace("_", " "))
        return ", ".join(dict.fromkeys(reasons)) if reasons else "structural position"

    # ── Mutation plan ─────────────────────────────────────────────────────────

    def _build_plan(
        self,
        graph_json: dict,
        targets: list[dict],
        G: nx.DiGraph,
    ) -> list[dict]:
        """Build a diverse mutation plan weighted by risk and scoped blast radius."""
        import random

        rng = random.Random(self.config.mutation_seed)
        plan: list[dict] = []
        total_risk = sum(t["risk_score"] for t in targets) or 1.0
        budgets = {
            target["node_id"]: max(
                1,
                int(round((target["risk_score"] / total_risk) * self.config.mutation_count)),
            )
            for target in targets
        }
        dtype_subs = {
            "INTEGER": ["BIGINT", "VARCHAR"],
            "INT": ["BIGINT", "VARCHAR"],
            "BIGINT": ["VARCHAR"],
            "FLOAT": ["NUMERIC", "VARCHAR"],
            "REAL": ["NUMERIC", "VARCHAR"],
            "NUMERIC": ["VARCHAR"],
            "DECIMAL": ["VARCHAR"],
            "VARCHAR": ["TEXT", "INTEGER"],
            "TEXT": ["VARCHAR", "INTEGER"],
            "TIMESTAMP": ["DATE", "VARCHAR"],
            "DATETIME": ["DATE", "VARCHAR"],
            "DATE": ["TIMESTAMP", "VARCHAR"],
        }

        def scope_assets(node_id: str, table: str) -> list[str]:
            assets = {node_id, table}
            try:
                assets.update(nx.descendants(G, node_id))
            except Exception:
                pass
            try:
                assets.update(nx.descendants(G, table))
            except Exception:
                pass
            return sorted(a for a in assets if isinstance(a, str))[:64]

        for target in targets:
            if len(plan) >= self.config.mutation_count:
                break
            col_id = target["node_id"]
            col_data = target["node_data"]
            table = col_data.get("table", col_id.split(".")[0])
            col_name = col_data.get("name", col_id.split(".")[-1])
            mutation_types = list(target["mutations"])
            rng.shuffle(mutation_types)
            budget = max(1, budgets.get(col_id, 1))
            scope = scope_assets(col_id, table)

            for mut_type in mutation_types[:budget]:
                if len(plan) >= self.config.mutation_count:
                    break
                entry: dict = {
                    "type": mut_type,
                    "node_id": col_id,
                    "table": table,
                    "col_name": col_name,
                    "col_data": col_data,
                    "risk_score": target["risk_score"],
                    "downstream": target["downstream"],
                    "reason": target["reason"],
                    "scope_assets": scope,
                }
                if mut_type == MutationType.RENAME_COLUMN:
                    suffix = "legacy" if col_data.get("is_primary_key") else "renamed"
                    entry["new_name"] = f"{col_name}_{suffix}"
                    entry["detail"] = f"{col_id} → {table}.{entry['new_name']}"
                elif mut_type == MutationType.REMOVE_COLUMN:
                    entry["detail"] = col_id
                elif mut_type == MutationType.CHANGE_DTYPE:
                    old_type = (col_data.get("dtype") or "VARCHAR").upper().split("(")[0]
                    choices = dtype_subs.get(old_type, ["VARCHAR"])
                    entry["old_type"] = old_type
                    entry["new_type"] = rng.choice(choices)
                    entry["detail"] = f"{col_id} → {entry['new_type']}"
                elif mut_type == MutationType.CHANGE_NULLABLE:
                    entry["new_nullable"] = not bool(col_data.get("nullable", True))
                    entry["detail"] = f"{col_id} nullable={entry['new_nullable']}"
                elif mut_type == MutationType.ADD_COLUMN:
                    entry["detail"] = f"{table}.{col_name}_chaos"
                elif mut_type == MutationType.NULL_FLOOD:
                    entry["sample_pct"] = max(0.01, float(self.config.null_flood_pct))
                    entry["detail"] = f"{col_id} null flood ({entry['sample_pct']:.0%})"
                elif mut_type == MutationType.BLANK_STRING_FLOOD:
                    entry["sample_pct"] = max(0.01, float(self.config.null_flood_pct))
                    entry["detail"] = f"{col_id} blank-string flood ({entry['sample_pct']:.0%})"
                elif mut_type == MutationType.DOMAIN_EXPANSION:
                    entry["sample_pct"] = max(0.01, float(self.config.null_flood_pct))
                    entry["new_domain_value"] = f"semzero_unseen_{uuid.uuid4().hex[:6]}"
                    entry["detail"] = f"{col_id} domain expansion → {entry['new_domain_value']}"
                elif mut_type == MutationType.KEY_SKEW:
                    entry["sample_pct"] = max(0.01, float(self.config.null_flood_pct))
                    entry["detail"] = f"{col_id} key skew ({entry['sample_pct']:.0%})"
                elif mut_type == MutationType.TEMPORAL_SKEW:
                    entry["sample_pct"] = max(0.01, float(self.config.temporal_skew_pct))
                    entry["skew_days"] = max(1, int(self.config.temporal_skew_days))
                    entry["detail"] = (
                        f"{col_id} late-arriving skew ({entry['sample_pct']:.0%}, -{entry['skew_days']}d)"
                    )
                plan.append(entry)

        table_nodes = [n for n in graph_json.get("nodes", []) if n.get("label") == "Table"]
        table_nodes.sort(
            key=lambda n: (
                -len(nx.descendants(G, n["id"])) if n["id"] in G else 0,
                -float(n.get("query_frequency", 0) or 0),
                n["id"],
            )
        )
        table_budget = max(1, self.config.mutation_count // 8)
        for table_node in table_nodes[:table_budget]:
            if len(plan) >= self.config.mutation_count:
                break
            table_id = table_node["id"]
            descendants = len(nx.descendants(G, table_id)) if table_id in G else 0
            risk = min(1.0, 0.35 + descendants / max(len(G.nodes), 1))
            scope = scope_assets(table_id, table_id)
            table_entries = [
                {
                    "type": MutationType.EMPTY_TABLE,
                    "node_id": table_id,
                    "table": table_id,
                    "col_name": table_id,
                    "detail": f"{table_id} zero-row extraction",
                    "risk_score": min(1.0, risk + 0.1),
                    "downstream": descendants,
                    "reason": f"table outage drill ({descendants} downstream nodes)",
                    "scope_assets": scope,
                },
                {
                    "type": MutationType.VOLUME_SPIKE,
                    "node_id": table_id,
                    "table": table_id,
                    "col_name": table_id,
                    "detail": f"{table_id} {self.config.volume_spike_multiplier}x volume surge",
                    "risk_score": risk,
                    "downstream": descendants,
                    "reason": f"throughput surge drill ({descendants} downstream nodes)",
                    "scope_assets": scope,
                    "spike_multiplier": max(2, int(self.config.volume_spike_multiplier)),
                },
            ]
            if descendants > 0:
                table_entries.append(
                    {
                        "type": MutationType.RENAME_TABLE,
                        "node_id": table_id,
                        "table": table_id,
                        "col_name": table_id,
                        "detail": f"{table_id} table resilience exercise",
                        "risk_score": min(1.0, risk + 0.05),
                        "downstream": descendants,
                        "reason": f"high table blast radius ({descendants} downstream nodes)",
                        "new_name": f"{table_id}_renamed",
                        "scope_assets": scope,
                    }
                )
            rng.shuffle(table_entries)
            for item in table_entries[:2]:
                if len(plan) >= self.config.mutation_count:
                    break
                plan.append(item)

        return plan[: self.config.mutation_count]

    # ── Environment ───────────────────────────────────────────────────────────

    def _create_env(self, run_id: str) -> str:
        if self.config.dry_run:
            return f"DRY_{run_id}"
        if self._dialect == "snowflake":
            return self._snowflake_clone(run_id)
        if self._dialect == "databricks":
            return self._databricks_clone(run_id)
        if self._dialect == "postgresql":
            return self._pg_clone(run_id)
        if self._dialect == "sqlite":
            return self._sqlite_clone(run_id)
        return f"GRAPH_{run_id}"

    def _sqlite_clone(self, run_id: str) -> str:
        import sqlite3

        source = (self.config.db_url or "").replace("sqlite:///", "").replace("sqlite://", "")
        tmp_dir = tempfile.mkdtemp(prefix="semzero_chaos_")
        clone_path = Path(tmp_dir) / f"clone_{run_id[:8]}.db"
        if source in {"", ":memory:"}:
            src = sqlite3.connect(source or ":memory:")
            dst = sqlite3.connect(str(clone_path))
            src.backup(dst)
            dst.close()
            src.close()
        else:
            shutil.copy2(Path(source), clone_path)
        clone_url = f"sqlite:///{clone_path}"
        self._sqlite_clone_paths[clone_url] = str(clone_path)
        return clone_url

    def _pg_clone(self, run_id: str) -> str:
        """Create a Postgres clone via CREATE DATABASE ... TEMPLATE."""
        from sqlalchemy import create_engine, text as _text
        from sqlalchemy.engine import make_url

        url = make_url(self.config.db_url)
        src_db = url.database
        clone_db = f"PG_{src_db}_chaos_{run_id[:6]}"

        maint_url = str(url.set(database="postgres"))
        maint_eng = create_engine(maint_url, isolation_level="AUTOCOMMIT")
        try:
            with maint_eng.connect() as conn:
                conn.execute(
                    _text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = :db AND pid <> pg_backend_pid()"
                    ),
                    {"db": src_db},
                )
                conn.execute(_text(f'CREATE DATABASE "{clone_db}" TEMPLATE "{src_db}"'))
            log.info(f"Postgres chaos clone: {clone_db}")
        finally:
            maint_eng.dispose()

        self._pg_clone_urls[clone_db] = str(url.set(database=clone_db))
        return clone_db

    def _destroy_pg_clone(self, clone: str) -> None:
        """Drop a Postgres chaos clone database."""
        from sqlalchemy import create_engine, text as _text
        from sqlalchemy.engine import make_url

        url = make_url(self.config.db_url)
        maint = create_engine(str(url.set(database="postgres")), isolation_level="AUTOCOMMIT")
        try:
            with maint.connect() as conn:
                conn.execute(
                    _text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :db"
                    ),
                    {"db": clone},
                )
                conn.execute(_text(f'DROP DATABASE IF EXISTS "{clone}"'))
            log.info(f"Postgres chaos clone dropped: {clone}")
        except Exception as e:
            log.error(f"Failed to drop Postgres clone {clone}: {e}")
        finally:
            maint.dispose()
            self._pg_clone_urls.pop(clone, None)

    def _destroy_env(self, clone: str) -> None:
        if clone.startswith("DRY_") or clone.startswith("GRAPH_"):
            return
        if clone.startswith("sqlite:///"):
            path = self._sqlite_clone_paths.pop(clone, clone.replace("sqlite:///", ""))
            try:
                Path(path).unlink(missing_ok=True)
                shutil.rmtree(str(Path(path).parent), ignore_errors=True)
            except Exception as e:
                log.error(f"Failed to destroy sqlite clone {clone}: {e}")
            return
        if clone.startswith("PG_"):
            self._destroy_pg_clone(clone)
            return
        if clone.startswith("DBX_"):
            self._destroy_databricks_clone(clone)
            return
        if self._dialect == "snowflake":
            try:
                from sqlalchemy import text as _text

                eng = self._sf_engine(self.config.snowflake_database)
                with eng.connect() as conn:
                    conn.execute(_text(f"DROP DATABASE IF EXISTS {clone}"))
                eng.dispose()
                log.info(f"Clone {clone} destroyed.")
            except Exception as e:
                log.error(f"Failed to destroy clone {clone}: {e}")

    def _snowflake_clone(self, run_id: str) -> str:
        from sqlalchemy import text as _text

        clone = f"{self.config.clone_prefix}_{run_id.upper()}"
        eng = self._sf_engine(self.config.snowflake_database)
        with eng.connect() as conn:
            conn.execute(_text(f"CREATE DATABASE {clone} CLONE {self.config.snowflake_database}"))
        eng.dispose()
        log.info(f"Snowflake zero-copy clone: {clone}")
        return clone

    def _sf_engine(self, database: str):
        from sqlalchemy import create_engine

        url = (
            f"snowflake://{self.config.snowflake_user}:"
            f"{self.config.snowflake_password}@"
            f"{self.config.snowflake_account}/{database}/"
            f"{self.config.snowflake_schema}"
        )
        if self.config.snowflake_warehouse:
            url += f"?warehouse={self.config.snowflake_warehouse}"
        return create_engine(url, pool_pre_ping=True)

    def _dbx_engine(self):
        from sqlalchemy import create_engine

        if self.config.db_url:
            return create_engine(self.config.db_url, pool_pre_ping=True)
        url = (
            f"databricks://token:{self.config.databricks_token}@{self.config.databricks_server_hostname}"
            f"?http_path={self.config.databricks_http_path}&catalog={self.config.databricks_catalog}&schema={self.config.databricks_schema}"
        )
        return create_engine(url, pool_pre_ping=True)

    def _databricks_clone(self, run_id: str) -> str:
        from sqlalchemy import text as _text

        catalog = (self.config.databricks_catalog or "").strip()
        schema = (self.config.databricks_schema or "default").strip()
        if not catalog:
            raise RuntimeError(
                "Chaos Mode requires databricks_catalog for SHALLOW CLONE execution."
            )
        clone_catalog = (self.config.databricks_clone_catalog or catalog).strip()
        clone_schema = f"{schema}_{run_id[:8].lower()}"
        clone_id = f"DBX_{clone_schema}"
        eng = self._dbx_engine()
        with eng.begin() as conn:
            conn.execute(_text(f"CREATE SCHEMA IF NOT EXISTS {clone_catalog}.{clone_schema}"))
            rows = conn.execute(_text(f"SHOW TABLES IN {catalog}.{schema}")).fetchall()
            for row in rows:
                payload = self._row_to_mapping(row)
                table_name = str(
                    payload.get("tableName")
                    or payload.get("tablename")
                    or payload.get("table_name")
                    or payload.get("name")
                    or ""
                ).strip()
                if not table_name:
                    continue
                conn.execute(
                    _text(
                        f"CREATE OR REPLACE TABLE {clone_catalog}.{clone_schema}.{table_name} "
                        f"SHALLOW CLONE {catalog}.{schema}.{table_name}"
                    )
                )
        eng.dispose()
        self._dbx_clone_urls[clone_id] = self.config.db_url
        self._dbx_clone_meta[clone_id] = {
            "catalog": clone_catalog,
            "schema": clone_schema,
            "map": {f"{catalog}.{schema}": f"{clone_catalog}.{clone_schema}", schema: clone_schema},
        }
        log.info("Databricks chaos clone schema: %s.%s", clone_catalog, clone_schema)
        return clone_id

    def _destroy_databricks_clone(self, clone: str) -> None:
        from sqlalchemy import text as _text

        meta = self._dbx_clone_meta.get(clone) or {}
        if not meta:
            return
        eng = self._dbx_engine()
        try:
            with eng.begin() as conn:
                conn.execute(
                    _text(f"DROP SCHEMA IF EXISTS {meta['catalog']}.{meta['schema']} CASCADE")
                )
            log.info("Databricks chaos clone dropped: %s.%s", meta["catalog"], meta["schema"])
        except Exception as e:
            log.error(f"Failed to drop Databricks clone {clone}: {e}")
        finally:
            eng.dispose()
            self._dbx_clone_urls.pop(clone, None)
            self._dbx_clone_meta.pop(clone, None)

    def _engine_for_clone(self, clone: str):
        from sqlalchemy import create_engine

        if clone.startswith("sqlite:///"):
            return create_engine(
                clone, connect_args={"check_same_thread": False}, pool_pre_ping=True
            )
        if clone.startswith("PG_"):
            return create_engine(self._pg_clone_urls[clone], pool_pre_ping=True)
        if clone.startswith("DBX_"):
            engine = self._dbx_engine()
            meta = self._dbx_clone_meta.get(clone) or {}
            setattr(engine, "_semzero_clone_map", meta.get("map", {}))
            setattr(
                engine,
                "_semzero_use_statements",
                [f"USE CATALOG {meta.get('catalog', '')}", f"USE SCHEMA {meta.get('schema', '')}"],
            )
            return engine
        if self._dialect == "snowflake" and clone.startswith(self.config.clone_prefix):
            return self._sf_engine(clone)
        return None

    @staticmethod
    def _row_to_mapping(row: Any) -> dict[str, Any]:
        if hasattr(row, "_mapping"):
            return {str(k): v for k, v in row._mapping.items()}
        if isinstance(row, dict):
            return row
        if isinstance(row, (list, tuple)):
            return {str(i): v for i, v in enumerate(row)}
        return {"value": row}

    # ── Mutation execution ────────────────────────────────────────────────────

    def _execute_plan(
        self,
        plan: list[dict],
        clone: str,
        graph_json: dict,
        G: nx.DiGraph,
    ) -> list[MutationResult]:

        if self.config.parallel_mutations and len(plan) > 4:
            return self._execute_parallel(plan, clone, graph_json, G)
        return self._execute_serial(plan, clone, graph_json, G)

    def _execute_serial(self, plan, clone_seed, graph_json, G) -> list[MutationResult]:
        results = []
        for i, entry in enumerate(plan):
            log.info(f"  [{i + 1:02d}/{len(plan)}] {entry['type'].value} on {entry['node_id']}")
            clone = self._create_env(f"{clone_seed}_{i}")
            try:
                result = self._execute_one(entry, clone, graph_json, G)
            finally:
                if self.config.auto_destroy_clone:
                    self._destroy_env(clone)
            results.append(result)
        return results

    def _execute_parallel(self, plan, clone_seed, graph_json, G) -> list[MutationResult]:
        """
        Run mutations in parallel for speed.
        Each mutation operates on a separate graph copy — no shared state.
        Parallel mode is kept for graph-only workloads; real clones run serially.
        """
        if self.config.db_url and self.config.workload_replay:
            return self._execute_serial(plan, clone_seed, graph_json, G)

        results: list[Optional[MutationResult]] = [None] * len(plan)

        with ThreadPoolExecutor(max_workers=self.config.max_workers) as executor:
            futures = {
                executor.submit(
                    self._execute_one, entry, f"GRAPH_{clone_seed}_{i}", graph_json, G
                ): i
                for i, entry in enumerate(plan)
            }
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    results[idx] = future.result()
                except Exception as e:
                    log.warning(f"Mutation {idx} failed: {e}")
                    entry = plan[idx]
                    results[idx] = MutationResult(
                        mutation_type=MutationType(entry["type"].value),
                        node_id=entry["node_id"],
                        detail=entry.get("detail", ""),
                        error_messages=[str(e)],
                        resilience=ResilienceLevel.UNTESTED,
                    )

        return [r for r in results if r is not None]

    def _execute_one(
        self,
        entry: dict,
        clone: str,
        graph_json: dict,
        G: nx.DiGraph,
    ) -> MutationResult:
        start = time.time()
        result = MutationResult(
            mutation_type=entry["type"],
            node_id=entry["node_id"],
            detail=entry.get("detail", ""),
            blast_score=min(1.0, entry.get("risk_score", 0.0)),
            targeting_reason=entry.get("reason", ""),
        )

        clone_engine = None
        recovery_snapshot = None
        try:
            if not clone.startswith(("GRAPH_", "DRY_")) and not self.config.dry_run:
                clone_engine = self._engine_for_clone(clone)
                if clone_engine is not None and self.config.stateful_recovery:
                    recovery_snapshot = self._prepare_recovery_snapshot(entry, clone_engine)
                self._apply_sql(entry, clone)
                if clone_engine is None:
                    clone_engine = self._engine_for_clone(clone)

            if (
                clone_engine is not None
                and self._orig_engine is not None
                and self._workload_queries
            ):
                workload_results = self._run_workload_replay(entry, clone_engine)
                result.tests_run = len(workload_results)
                result.tests_failed = sum(
                    1
                    for item in workload_results
                    if getattr(item.status, "value", item.status) != "PASSED"
                )
                result.tests_passed = sum(
                    1
                    for item in workload_results
                    if getattr(item.status, "value", item.status) == "PASSED"
                )
                result.failed_models = [
                    item.query_id
                    for item in workload_results
                    if getattr(item.status, "value", item.status) != "PASSED"
                ][:5]
                result.error_messages.extend(
                    [item.clone_error for item in workload_results if item.clone_error][:3]
                )
                if self.config.stateful_recovery and recovery_snapshot and result.tests_failed > 0:
                    recovered, notes = self._verify_stateful_recovery(
                        entry, clone_engine, recovery_snapshot
                    )
                    result.recovery_verified = recovered
                    result.manual_backfill_required = True
                    result.recovery_notes.extend(notes)

            if self.config.run_dbt_tests and self.config.dbt_project_path and result.tests_run == 0:
                test_r = self._run_dbt(clone)
                result.tests_run = test_r["total"]
                result.tests_passed = test_r["passed"]
                result.tests_failed = test_r["failed"]
                result.failed_models = test_r["failed_models"]
                result.error_messages = test_r["errors"]

            result.cascade = self._simulate_cascade(entry["node_id"], G)

            if result.tests_run == 0 and result.cascade:
                result = self._score_from_cascade(result)

        except Exception as e:
            result.error_messages.append(str(e))
            log.debug(f"Mutation error on {entry['node_id']}: {e}")
        finally:
            if clone_engine is not None:
                try:
                    clone_engine.dispose()
                except Exception:
                    pass

        result.resilience = self._classify(result)
        result.duration_s = time.time() - start
        return result

    def _run_workload_replay(self, entry: dict, clone_engine) -> list:
        from .wind_tunnel import QueryReplayer, QueryStatus, WindTunnelConfig

        queries = self._queries_for_entry(entry)
        if not queries:
            return []
        wt_config = WindTunnelConfig(
            db_url=self.config.db_url,
            row_sample_limit=250,
            query_timeout_s=15,
            compare_row_counts=True,
            compare_value_fingerprints=True,
            tolerance_pct=0.001,
        )
        replayer = QueryReplayer(wt_config)
        results = replayer.replay(queries, self._orig_engine, clone_engine)
        # Only count real breakage, not queries that already fail on the origin.
        return [r for r in results if r.status != QueryStatus.ERROR_ORIGINAL]

    def _prepare_recovery_snapshot(self, entry: dict, clone_engine) -> dict | None:
        if entry.get("type") not in {
            MutationType.NULL_FLOOD,
            MutationType.BLANK_STRING_FLOOD,
            MutationType.DOMAIN_EXPANSION,
            MutationType.KEY_SKEW,
            MutationType.TEMPORAL_SKEW,
            MutationType.VOLUME_SPIKE,
            MutationType.EMPTY_TABLE,
        }:
            return None
        table = entry.get("table")
        if not table or clone_engine.dialect.name not in {"sqlite", "postgresql", "postgres"}:
            return None
        backup_table = f"__semzero_recovery_{table}"
        from sqlalchemy import text as _text

        with clone_engine.begin() as conn:
            conn.execute(_text(f'DROP TABLE IF EXISTS "{backup_table}"'))
            conn.execute(_text(f'CREATE TABLE "{backup_table}" AS SELECT * FROM "{table}"'))
        return {"table": table, "backup_table": backup_table}

    def _verify_stateful_recovery(
        self, entry: dict, clone_engine, snapshot: dict
    ) -> tuple[bool, list[str]]:
        notes: list[str] = []
        try:
            from sqlalchemy import text as _text

            table = snapshot["table"]
            backup = snapshot["backup_table"]
            with clone_engine.begin() as conn:
                conn.execute(_text(f'DELETE FROM "{table}"'))
                conn.execute(_text(f'INSERT INTO "{table}" SELECT * FROM "{backup}"'))
            results = self._run_workload_replay(entry, clone_engine)[
                : self.config.recovery_replay_limit
            ]
            remaining = [r for r in results if getattr(r.status, "value", r.status) != "PASSED"]
            if remaining:
                notes.append(
                    f"Recovery replay still failing after restore on {table}; manual backfill or code fix required."
                )
                return False, notes
            notes.append(
                f"Recovery replay passed after restoring {table}; mutation is recoverable but requires explicit backfill/restore."
            )
            return True, notes
        except Exception as exc:
            return False, [f"Stateful recovery verification failed: {exc}"]
        finally:
            try:
                from sqlalchemy import text as _text

                with clone_engine.begin() as conn:
                    conn.execute(_text(f'DROP TABLE IF EXISTS "{snapshot.get("backup_table")}"'))
            except Exception:
                pass

    # ── Cascade simulation ────────────────────────────────────────────────────

    def _simulate_cascade(self, node_id: str, G: nx.DiGraph) -> CascadeResult:
        """
        Simulate multi-hop failure propagation from a broken node.

        Hop 1: direct downstream (immediate breakage)
        Hop 2: indirect downstream (secondary effects)
        Hop 3+: deep cascade (dashboard, ML model failures)
        """
        cascade = CascadeResult(origin_node=node_id)

        if node_id not in G:
            return cascade

        try:
            lengths = nx.single_source_shortest_path_length(G, node_id)
        except Exception:
            return cascade

        for nid, depth in lengths.items():
            if nid == node_id or depth == 0:
                continue
            if depth == 1:
                cascade.hop_1_impacted.append(nid)
            elif depth == 2:
                cascade.hop_2_impacted.append(nid)
            else:
                cascade.hop_3plus_impacted.append(nid)

        cascade.total_impacted = (
            len(cascade.hop_1_impacted)
            + len(cascade.hop_2_impacted)
            + len(cascade.hop_3plus_impacted)
        )
        cascade.max_depth = max(lengths.values()) if lengths else 0

        # Cascade score: weighted by hop depth
        # Deep cascades are much more dangerous than wide shallow ones
        total_nodes = len(G.nodes)
        if total_nodes > 1:
            weighted = (
                len(cascade.hop_1_impacted) * 1.0
                + len(cascade.hop_2_impacted) * 2.0
                + len(cascade.hop_3plus_impacted) * 3.0
            )
            cascade.cascade_score = min(1.0, weighted / (total_nodes * 2))

        return cascade

    def _score_from_cascade(self, result: MutationResult) -> MutationResult:
        """Derive synthetic failure metrics from cascade severity."""
        if not result.cascade:
            result.tests_run = 0
            return result

        cascade = result.cascade
        mutation = result.mutation_type.value if result.mutation_type else ""
        if mutation in {"REMOVE_COLUMN", "RENAME_COLUMN", "REMOVE_TABLE", "RENAME_TABLE"}:
            failing = cascade.hop_2_impacted + cascade.hop_3plus_impacted
            safe = cascade.hop_1_impacted
        elif mutation == "CHANGE_DTYPE":
            failing = (
                cascade.hop_3plus_impacted
                if cascade.max_depth >= 3 or cascade.cascade_score > 0.25
                else []
            )
            safe = cascade.hop_1_impacted + cascade.hop_2_impacted
        elif mutation == "CHANGE_NULLABLE":
            failing = (
                cascade.hop_3plus_impacted
                if cascade.max_depth >= 4 or cascade.cascade_score > 0.4
                else []
            )
            safe = cascade.hop_1_impacted + cascade.hop_2_impacted
        else:
            failing = cascade.hop_3plus_impacted if cascade.cascade_score > 0.5 else []
            safe = cascade.hop_1_impacted + cascade.hop_2_impacted

        result.tests_run = cascade.total_impacted
        result.tests_passed = len(safe)
        result.tests_failed = len(failing)
        if result.tests_failed > 0:
            result.failed_models = list(dict.fromkeys(failing))[:5]
        return result

    # ── Resilience classification ─────────────────────────────────────────────

    def _classify(self, result: MutationResult) -> ResilienceLevel:
        if result.tests_run == 0 and not result.cascade:
            return ResilienceLevel.UNTESTED

        if result.tests_run > 0 and (self.config.run_dbt_tests and self.config.dbt_project_path):
            if result.tests_failed == 0:
                return ResilienceLevel.RESILIENT
            if result.failure_rate >= 0.5:
                return ResilienceLevel.CRITICAL
            return ResilienceLevel.FRAGILE

        if result.cascade:
            score = result.cascade.cascade_score
            depth = result.cascade.max_depth
            mutation = result.mutation_type.value if result.mutation_type else ""
            if score == 0.0 and depth <= 1:
                return ResilienceLevel.RESILIENT
            if mutation in {"RENAME_COLUMN", "REMOVE_COLUMN", "REMOVE_TABLE", "RENAME_TABLE"}:
                if score > 0.25 or depth >= 4:
                    return ResilienceLevel.CRITICAL
                if score > 0.05 or depth >= 2:
                    return ResilienceLevel.FRAGILE
                return ResilienceLevel.RESILIENT
            if mutation == "CHANGE_DTYPE":
                if score > 0.35 or depth >= 3:
                    return ResilienceLevel.FRAGILE
                return ResilienceLevel.RESILIENT
            if mutation == "CHANGE_NULLABLE":
                if score > 0.5 or depth >= 4:
                    return ResilienceLevel.FRAGILE
                return ResilienceLevel.RESILIENT
            if score > 0.4 or depth >= 4:
                return ResilienceLevel.CRITICAL
            if score > 0.1 or depth >= 2:
                return ResilienceLevel.FRAGILE
            return ResilienceLevel.RESILIENT
        return ResilienceLevel.UNTESTED

    # ── Pipeline scoring ──────────────────────────────────────────────────────

    def _score_pipelines(
        self,
        results: list[MutationResult],
        graph_json: dict,
    ) -> list[PipelineFragility]:
        """
        Aggregate mutation results + cascade analysis into per-pipeline scores.
        """
        # Build pipeline impact map from cascade results
        pipeline_impact: dict[str, list[str]] = defaultdict(list)
        pipeline_cascade: dict[str, list[float]] = defaultdict(list)

        for r in results:
            if r.resilience == ResilienceLevel.RESILIENT:
                continue
            # Failed models from dbt
            for model in r.failed_models:
                pipeline_impact[model].append(r.mutation_type.value)
                pipeline_cascade[model].append(r.blast_score)

            # Deep cascade targets
            if r.cascade and r.cascade.hop_3plus_impacted:
                for nid in r.cascade.hop_3plus_impacted[:3]:
                    pipeline_impact[nid].append(r.mutation_type.value)
                    pipeline_cascade[nid].append(r.cascade.cascade_score)

        fragility_list: list[PipelineFragility] = []

        for model, breaking in pipeline_impact.items():
            unique = list(set(breaking))
            count = len(breaking)
            avg_cascade = (
                sum(pipeline_cascade[model]) / len(pipeline_cascade[model])
                if pipeline_cascade[model]
                else 0.0
            )

            if (count >= 4 and avg_cascade > 0.05) or avg_cascade > 0.5:
                level = ResilienceLevel.CRITICAL
                score = max(0, 30 - count * 5)
                rec = (
                    f"URGENT: `{model}` fails under {count} mutation type(s) "
                    f"with cascade score {avg_cascade:.2f}. "
                    f"Add defensive SQL (COALESCE, CAST guards) and dbt tests."
                )
                auto_fix = True
            elif count >= 2 or avg_cascade > 0.2:
                level = ResilienceLevel.FRAGILE
                score = 55
                rec = (
                    f"`{model}` is fragile to {', '.join(unique[:2])}. "
                    f"Add not_null and accepted_values dbt tests."
                )
                auto_fix = True
            else:
                level = ResilienceLevel.FRAGILE
                score = 72
                rec = f"`{model}` has minor fragility under {unique[0]}."
                auto_fix = False

            fragility_list.append(
                PipelineFragility(
                    model_name=model,
                    resilience=level,
                    breaking_mutations=unique,
                    fragility_score=score,
                    cascade_exposure=avg_cascade,
                    recommendation=rec,
                    auto_fix_available=auto_fix,
                )
            )

        fragility_list.sort(
            key=lambda p: (
                p.resilience != ResilienceLevel.CRITICAL,
                p.resilience != ResilienceLevel.FRAGILE,
                -p.cascade_exposure,
            )
        )
        return fragility_list

    # ── SQL mutation helpers ────────────────────────────────────────────────

    def _apply_sql(self, entry: dict, clone: str) -> None:
        engine = self._engine_for_clone(clone)
        if engine is None:
            return
        dialect = engine.dialect.name
        table = entry["table"]
        col = entry.get("col_name", table)
        stmts: list[str] = []

        if dialect == "sqlite" and entry["type"] in {
            MutationType.CHANGE_DTYPE,
            MutationType.CHANGE_NULLABLE,
            MutationType.NULL_FLOOD,
            MutationType.BLANK_STRING_FLOOD,
        }:
            self._sqlite_rebuild_table(engine, entry)
            if entry["type"] in {MutationType.CHANGE_DTYPE, MutationType.CHANGE_NULLABLE}:
                engine.dispose()
                return

        def qi(name: str) -> str:
            if dialect == "snowflake":
                return f'"{name}"'
            if dialect == "bigquery":
                return f"`{name}`"
            return f'"{name}"'

        table_ref = qi(table)
        t = entry["type"]
        if t == MutationType.RENAME_COLUMN:
            new = entry.get("new_name", f"{col}_renamed")
            stmts = [f"ALTER TABLE {table_ref} RENAME COLUMN {qi(col)} TO {qi(new)}"]
        elif t == MutationType.REMOVE_COLUMN:
            stmts = [f"ALTER TABLE {table_ref} DROP COLUMN {qi(col)}"]
        elif t == MutationType.ADD_COLUMN:
            stmts = [f"ALTER TABLE {table_ref} ADD COLUMN {qi(col + '_chaos')} VARCHAR(255)"]
        elif t == MutationType.CHANGE_DTYPE:
            new_type = entry.get("new_type", "VARCHAR")
            if dialect == "postgresql":
                stmts = [
                    f"ALTER TABLE {table_ref} ALTER COLUMN {qi(col)} TYPE {new_type} USING {qi(col)}::{new_type}"
                ]
            else:
                stmts = [f"ALTER TABLE {table_ref} ALTER COLUMN {qi(col)} TYPE {new_type}"]
        elif t == MutationType.CHANGE_NULLABLE:
            new_val = entry.get("new_nullable", True)
            if dialect in {"postgresql", "snowflake"}:
                kw = "DROP NOT NULL" if new_val else "SET NOT NULL"
                stmts = [f"ALTER TABLE {table_ref} ALTER COLUMN {qi(col)} {kw}"]
        elif t == MutationType.NULL_FLOOD:
            profile = self._table_profile(engine, table)
            if not profile:
                engine.dispose()
                return
            if dialect in {"postgresql", "snowflake"} and not profile.get(
                "column_nullable", {}
            ).get(col, True):
                stmts.append(f"ALTER TABLE {table_ref} ALTER COLUMN {qi(col)} DROP NOT NULL")
            sample = self._sample_predicate(
                engine,
                table,
                profile,
                where=f"{qi(col)} IS NOT NULL",
                pct=float(entry.get("sample_pct") or self.config.null_flood_pct),
            )
            if sample:
                stmts.append(
                    f"UPDATE {table_ref} SET {qi(col)} = NULL WHERE {qi(col)} IS NOT NULL AND {sample}"
                )
        elif t == MutationType.BLANK_STRING_FLOOD:
            profile = self._table_profile(engine, table)
            if not profile:
                engine.dispose()
                return
            sample = self._sample_predicate(
                engine,
                table,
                profile,
                where=f"{qi(col)} IS NOT NULL AND TRIM(CAST({qi(col)} AS TEXT)) <> ''",
                pct=float(entry.get("sample_pct") or self.config.null_flood_pct),
            )
            if sample:
                stmts.append(
                    f"UPDATE {table_ref} SET {qi(col)} = '' WHERE {qi(col)} IS NOT NULL AND TRIM(CAST({qi(col)} AS TEXT)) <> '' AND {sample}"
                )
        elif t == MutationType.DOMAIN_EXPANSION:
            profile = self._table_profile(engine, table)
            if not profile:
                engine.dispose()
                return
            sample = self._sample_predicate(
                engine,
                table,
                profile,
                where=f"{qi(col)} IS NOT NULL",
                pct=float(entry.get("sample_pct") or self.config.null_flood_pct),
            )
            if sample:
                new_value = str(
                    entry.get("new_domain_value") or f"semzero_unseen_{uuid.uuid4().hex[:6]}"
                )
                escaped = new_value.replace("'", "''")
                stmts.append(
                    f"UPDATE {table_ref} SET {qi(col)} = '{escaped}' WHERE {qi(col)} IS NOT NULL AND {sample}"
                )
        elif t == MutationType.KEY_SKEW:
            profile = self._table_profile(engine, table)
            if not profile:
                engine.dispose()
                return
            literal = self._dominant_literal(engine, table, col)
            sample = self._sample_predicate(
                engine,
                table,
                profile,
                where=f"{qi(col)} IS NOT NULL",
                pct=float(entry.get("sample_pct") or self.config.null_flood_pct),
            )
            if sample and literal is not None:
                stmts.append(
                    f"UPDATE {table_ref} SET {qi(col)} = {literal} WHERE {qi(col)} IS NOT NULL AND {sample}"
                )
        elif t == MutationType.TEMPORAL_SKEW:
            profile = self._table_profile(engine, table)
            sample = self._sample_predicate(
                engine,
                table,
                profile,
                where=f"{qi(col)} IS NOT NULL",
                pct=float(entry.get("sample_pct") or self.config.temporal_skew_pct),
            )
            days = max(1, int(entry.get("skew_days") or self.config.temporal_skew_days))
            if sample:
                if dialect == "sqlite":
                    expr = f"datetime({qi(col)}, '-{days} day')"
                elif dialect == "snowflake":
                    expr = f"DATEADD(day, -{days}, {qi(col)})"
                else:
                    expr = f"{qi(col)} - INTERVAL '{days} day'"
                stmts.append(
                    f"UPDATE {table_ref} SET {qi(col)} = {expr} WHERE {qi(col)} IS NOT NULL AND {sample}"
                )
        elif t == MutationType.VOLUME_SPIKE:
            profile = self._table_profile(engine, table)
            spike_sql = self._volume_spike_sql(
                engine,
                table,
                profile,
                int(entry.get("spike_multiplier") or self.config.volume_spike_multiplier),
            )
            stmts.extend(spike_sql)
        elif t == MutationType.EMPTY_TABLE:
            stmts = [f"DELETE FROM {table_ref}"]
        elif t == MutationType.REMOVE_TABLE:
            stmts = [f"DROP TABLE {table_ref}"]
        elif t == MutationType.RENAME_TABLE:
            new = entry.get("new_name", f"{table}_renamed")
            stmts = (
                [f"ALTER TABLE {table_ref} RENAME TO {qi(new)}"]
                if dialect == "sqlite"
                else [f"ALTER TABLE {table_ref} RENAME TO {qi(new)}"]
            )

        if stmts:
            from sqlalchemy import text as _text

            with engine.begin() as conn:
                for stmt in stmts:
                    conn.execute(_text(stmt))
            engine.dispose()

    def _table_profile(self, engine, table: str) -> dict:
        from sqlalchemy import inspect, text as _text

        inspector = inspect(engine)
        cols = inspector.get_columns(table)
        pk = inspector.get_pk_constraint(table).get("constrained_columns") or []
        try:
            with engine.connect() as conn:
                row_count = conn.execute(_text(f'SELECT COUNT(*) FROM "{table}"')).scalar() or 0
        except Exception:
            row_count = 0
        return {
            "columns": cols,
            "column_names": [c.get("name") for c in cols],
            "pk": pk,
            "pk_col": pk[0] if len(pk) == 1 else None,
            "column_nullable": {c.get("name"): bool(c.get("nullable", True)) for c in cols},
            "column_types": {c.get("name"): str(c.get("type") or "") for c in cols},
            "row_count": int(row_count),
        }

    def _sample_predicate(
        self, engine, table: str, profile: dict, where: str = "1=1", pct: float = 0.1
    ) -> str:
        row_count = int((profile or {}).get("row_count") or 0)
        if row_count <= 0:
            return ""
        pct = min(1.0, max(0.0, float(pct or 0.0)))
        target_rows = max(1, int(math.ceil(row_count * pct)))
        dialect = engine.dialect.name
        pk_col = (profile or {}).get("pk_col")
        if pk_col:
            return f"{self._qi_for_dialect(dialect, pk_col)} IN (SELECT {self._qi_for_dialect(dialect, pk_col)} FROM {self._qi_for_dialect(dialect, table)} WHERE {where} ORDER BY {self._qi_for_dialect(dialect, pk_col)} LIMIT {target_rows})"
        if dialect == "sqlite":
            return f"rowid IN (SELECT rowid FROM {self._qi_for_dialect(dialect, table)} WHERE {where} ORDER BY rowid LIMIT {target_rows})"
        return ""

    def _qi_for_dialect(self, dialect: str, name: str) -> str:
        if dialect == "bigquery":
            return f"`{name}`"
        return f'"{name}"'

    def _volume_spike_sql(self, engine, table: str, profile: dict, multiplier: int) -> list[str]:
        if not profile or int(profile.get("row_count") or 0) <= 0 or multiplier <= 1:
            return []
        dialect = engine.dialect.name
        qi = lambda name: self._qi_for_dialect(dialect, name)
        columns = profile.get("columns") or []
        pk_col = profile.get("pk_col")
        insert_cols: list[str] = []
        select_cols: list[str] = []
        for column in columns:
            name = column.get("name")
            if not name:
                continue
            insert_cols.append(qi(name))
            col_type = str(column.get("type") or "").upper()
            if pk_col and name == pk_col and any(token in col_type for token in ("INT", "SERIAL")):
                select_cols.append(f"NULL AS {qi(name)}")
            else:
                select_cols.append(qi(name))
        if not insert_cols or not select_cols:
            return []
        sample_pct = min(0.2, max(0.01, float(self.config.mutation_sample_pct)))
        target_rows = max(1, int(math.ceil(int(profile.get("row_count") or 0) * sample_pct)))
        statements: list[str] = []
        for _ in range(multiplier - 1):
            statements.append(
                f"INSERT INTO {qi(table)} ({', '.join(insert_cols)}) SELECT {', '.join(select_cols)} FROM {qi(table)} LIMIT {target_rows}"
            )
        return statements

    def _sqlite_rebuild_table(self, engine, entry: dict) -> None:
        from sqlalchemy import text as _text

        table = entry["table"]
        col = entry["col_name"]
        with engine.begin() as conn:
            cols = conn.execute(_text(f'PRAGMA table_info("{table}")')).fetchall()
            if not cols:
                return
            col_defs = []
            select_cols = []
            for cid, name, ctype, notnull, dflt_value, pk in cols:
                out_type = ctype
                out_notnull = bool(notnull)
                if name == col and entry["type"] == MutationType.CHANGE_DTYPE:
                    out_type = entry.get("new_type", ctype)
                if name == col and entry["type"] == MutationType.CHANGE_NULLABLE:
                    out_notnull = not bool(entry.get("new_nullable", True))
                if name == col and entry["type"] == MutationType.NULL_FLOOD:
                    out_notnull = False
                frag = f'"{name}" {out_type or "TEXT"}'
                if pk:
                    frag += " PRIMARY KEY"
                if out_notnull and not pk:
                    frag += " NOT NULL"
                if dflt_value is not None:
                    frag += f" DEFAULT {dflt_value}"
                col_defs.append(frag)
                if name == col and entry["type"] == MutationType.CHANGE_DTYPE:
                    select_cols.append(
                        f'CAST("{name}" AS {entry.get("new_type", ctype)}) AS "{name}"'
                    )
                else:
                    select_cols.append(f'"{name}"')
            temp_name = f"{table}__semzero_old"
            conn.execute(_text(f'ALTER TABLE "{table}" RENAME TO "{temp_name}"'))
            conn.execute(_text(f'CREATE TABLE "{table}" ({", ".join(col_defs)})'))
            conn.execute(
                _text(f'INSERT INTO "{table}" SELECT {", ".join(select_cols)} FROM "{temp_name}"')
            )
            conn.execute(_text(f'DROP TABLE "{temp_name}"'))

    def _revert_sql(self, entry: dict, clone: str) -> None:
        return

    # ── dbt test runner ───────────────────────────────────────────────────────

    def _run_dbt(self, clone: str) -> dict:
        cmd = [
            "dbt",
            "test",
            "--project-dir",
            self.config.dbt_project_path,
            "--target",
            self.config.dbt_target,
            "--no-version-check",
        ]
        if self.config.dbt_profiles_dir:
            cmd += ["--profiles-dir", self.config.dbt_profiles_dir]
        if clone.startswith("SEMZERO_CHAOS"):
            cmd += ["--vars", json.dumps({"chaos_database": clone})]

        try:
            r = subprocess.run(
                cmd, capture_output=True, text=True, timeout=300, cwd=self.config.dbt_project_path
            )
            return self._parse_dbt(r.stdout, r.stderr)
        except subprocess.TimeoutExpired:
            return {
                "total": 0,
                "passed": 0,
                "failed": 0,
                "failed_models": [],
                "errors": ["timeout"],
            }
        except FileNotFoundError:
            return {
                "total": 0,
                "passed": 0,
                "failed": 0,
                "failed_models": [],
                "errors": ["dbt not found"],
            }

    def _parse_dbt(self, stdout: str, stderr: str) -> dict:
        import re

        result = {"total": 0, "passed": 0, "failed": 0, "failed_models": [], "errors": []}
        failed_models: set[str] = set()

        for line in stdout.splitlines():
            if "FAIL" in line:
                result["failed"] += 1
                result["total"] += 1
                m = re.search(r"FAIL\s+(\S+)", line)
                if m:
                    failed_models.add(m.group(1))
            elif "PASS" in line:
                result["passed"] += 1
                result["total"] += 1
            # Summary line
            m = re.search(r"(\d+) passed.*?(\d+) (failed|warned)", line)
            if m:
                result["passed"] = int(m.group(1))
                result["failed"] = int(m.group(2))
                result["total"] = result["passed"] + result["failed"]

        if stderr:
            result["errors"] = [stderr[:300]]
        result["failed_models"] = list(failed_models)
        return result

    # ── History ───────────────────────────────────────────────────────────────

    def _load_history(self) -> list[dict]:
        p = Path(self.config.history_path)
        if not p.exists():
            return []
        try:
            return json.loads(p.read_text())
        except Exception:
            return []

    # ── Logging ───────────────────────────────────────────────────────────────

    def _banner(self, run_id: str) -> None:
        log.info("━" * 55)
        log.info(f"  SemZero Chaos Mode — Run {run_id}")
        log.info(f"  Mutations: {self.config.mutation_count}")
        log.info(f"  dbt:       {self.config.run_dbt_tests}")
        log.info(f"  Parallel:  {self.config.parallel_mutations}")
        log.info(f"  Dry run:   {self.config.dry_run}")
        log.info("━" * 55)

    def _log_summary(self, report: ChaosReport) -> None:
        s = report.summary()
        log.info("━" * 55)
        log.info(f"  Score:    {s['fragility_score']}/100  Grade: {s['fragility_grade']}")
        log.info(f"  Mode:     {s['mode']}")
        log.info(f"  Broke:    {s['mutations_that_broke']}/{s['mutations_applied']}")
        log.info(f"  Critical: {s['critical_pipelines']} pipelines")
        log.info(f"  DNA:      {s['anti_pattern_score']}/100 anti-pattern score")
        log.info(f"  Velocity: {s['drift_velocity']:.1f} breaks/week")
        log.info(f"  Duration: {s['duration_s']:.1f}s")
        log.info("━" * 55)
