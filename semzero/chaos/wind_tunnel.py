"""
wind_tunnel.py — SemZero Migration Wind Tunnel.

"We validated your migration against 500 real production queries before you merged."

No other tool does this:
  - Monte Carlo:        monitors data values, not schema changes
  - Datafold:           compares data, not query execution
  - dbt:                runs models, not arbitrary SQL
  - Great Expectations: validates data, not queries
  - Every observability tool: reactive, never proactive

What the Wind Tunnel does:
  1. Clones the database  (SQLite copy / Postgres TEMPLATE / Snowflake CLONE)
  2. Applies the proposed migration SQL to the clone only
  3. Extracts representative queries (pg_stat_statements / ACCOUNT_USAGE / synthetic)
  4. Replays every query against both original and clone
  5. Compares results: execution success, row counts, column sets
  6. Analyses migration SQL for semantic risks (type narrowing, NOT NULL traps)
  7. Generates a SimulationReceipt and posts it to the PR
  8. Destroys the clone — production untouched

Supported dialects (zero config required):
  sqlite     — file copy, works out of the box for dev/test
  postgresql — CREATE DATABASE ... TEMPLATE ..., needs psycopg2-binary
  snowflake  — zero-copy CLONE, needs snowflake-sqlalchemy
  databricks — SHALLOW CLONE into an isolated schema, needs databricks-sql-connector

Usage:
    from semzero.chaos.wind_tunnel import MigrationWindTunnel, WindTunnelConfig

    config  = WindTunnelConfig(db_url="sqlite:///mydb.db")
    tunnel  = MigrationWindTunnel(config)
    receipt = tunnel.run(
        migration_sql="ALTER TABLE orders DROP COLUMN legacy_id;",
        graph_json=graph,
    )
    print(receipt.to_pr_comment())
    receipt.save("data/simulation_receipt.json")

CLI:
    semzero wind-tunnel \\
        --db-url postgresql://user:pw@host/db \\
        --migration migrations/v2.sql
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from semzero.integrations.finops_gate import estimate_query_finops

from ..integrations.ecosystem import EcosystemContext
from ..integrations.graph_intelligence import GraphIntelligenceEngine

log = logging.getLogger(__name__)


# ── Enums ──────────────────────────────────────────────────────────────────────


class QueryStatus(str, Enum):
    PASSED = "PASSED"
    BROKEN = "BROKEN"  # errored on clone, not on original
    ROW_MISMATCH = "ROW_MISMATCH"  # different row counts
    SCHEMA_CHANGED = "SCHEMA_CHANGED"  # result set columns changed
    ERROR_BOTH = "ERROR_BOTH"  # bad query — failed on both
    ERROR_ORIGINAL = "ERROR_ORIGINAL"  # only original failed (unusual)


class TunnelVerdict(str, Enum):
    SAFE = "SAFE"  # 100% pass rate
    SAFE_WITH_PATCHES = "SAFE_WITH_PATCHES"  # ≥95% pass, patches available
    BLOCKED = "BLOCKED"  # <95% pass — do not merge
    NO_QUERIES = "NO_QUERIES"  # no queries to replay
    ERROR = "ERROR"  # tunnel itself failed


_VERDICT_EMOJI = {
    TunnelVerdict.SAFE: "✅",
    TunnelVerdict.SAFE_WITH_PATCHES: "⚠️",
    TunnelVerdict.BLOCKED: "🚫",
    TunnelVerdict.NO_QUERIES: "ℹ️",
    TunnelVerdict.ERROR: "❓",
}


# ── Data classes ───────────────────────────────────────────────────────────────


@dataclass
class QueryResult:
    """Result of replaying one query against original and clone."""

    query_id: str
    query_text: str
    query_hash: str
    status: QueryStatus = QueryStatus.PASSED

    original_rows: Optional[int] = None
    clone_rows: Optional[int] = None
    original_cols: list[str] = field(default_factory=list)
    clone_cols: list[str] = field(default_factory=list)
    original_error: Optional[str] = None
    clone_error: Optional[str] = None
    duration_ms: float = 0.0
    affected_cols: list[str] = field(default_factory=list)
    row_delta: int = 0
    row_diff_summary: dict = field(default_factory=dict)
    original_sample_rows: list[dict[str, Any]] = field(default_factory=list)
    clone_sample_rows: list[dict[str, Any]] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.status == QueryStatus.PASSED

    def to_dict(self) -> dict:
        return {
            "query_id": self.query_id,
            "query_hash": self.query_hash,
            "query_preview": self.query_text[:120].replace("\n", " "),
            "status": self.status.value,
            "original_rows": self.original_rows,
            "clone_rows": self.clone_rows,
            "row_delta": self.row_delta,
            "added_cols": sorted(set(self.clone_cols or []) - set(self.original_cols or [])),
            "removed_cols": sorted(set(self.original_cols or []) - set(self.clone_cols or [])),
            "original_error": self.original_error,
            "clone_error": (self.clone_error or "")[:300],
            "duration_ms": round(self.duration_ms, 1),
            "affected_cols": self.affected_cols,
            "row_diff_summary": self.row_diff_summary,
            "original_sample_rows": self.original_sample_rows,
            "clone_sample_rows": self.clone_sample_rows,
        }


@dataclass
class SemanticRisk:
    """A semantic risk identified by analysing migration SQL."""

    risk_type: str  # TYPE_NARROWING | NOT_NULL_TRAP | FK_DROP | DATA_LOSS
    severity: str  # CRITICAL | HIGH | MEDIUM
    column: str
    description: str
    suggestion: str = ""

    def to_dict(self) -> dict:
        return {
            "risk_type": self.risk_type,
            "severity": self.severity,
            "column": self.column,
            "description": self.description,
            "suggestion": self.suggestion,
        }


@dataclass
class SimulationReceipt:
    """
    The simulation receipt attached to every PR that runs through the Wind Tunnel.
    This is the artefact that makes SemZero tangible to engineers.
    """

    run_id: str
    clone_name: str
    migration_summary: str
    db_dialect: str
    started_at: str
    completed_at: str = ""
    duration_s: float = 0.0

    queries_replayed: int = 0
    queries_passed: int = 0
    queries_broken: int = 0
    queries_mismatch: int = 0

    broken_queries: list[QueryResult] = field(default_factory=list)
    mismatch_queries: list[QueryResult] = field(default_factory=list)
    sample_passed: list[QueryResult] = field(default_factory=list)
    semantic_risks: list[SemanticRisk] = field(default_factory=list)

    confidence_score: float = 0.0
    verdict: TunnelVerdict = TunnelVerdict.ERROR
    patches_available: list[str] = field(default_factory=list)
    error: Optional[str] = None

    migration_applied: bool = False
    clone_created: bool = False
    debug_focus_assets: list[str] = field(default_factory=list)
    top_failure_modes: list[str] = field(default_factory=list)
    suggested_debug_steps: list[str] = field(default_factory=list)
    replay_scope_summary: dict = field(default_factory=dict)
    historical_queries_replayed: int = 0
    synthetic_queries_replayed: int = 0
    future_queries_generated: int = 0
    query_mix_summary: dict = field(default_factory=dict)
    prevention_summary: list[str] = field(default_factory=list)
    compute_cost_risk: float = 0.0
    compute_cost_notes: list[str] = field(default_factory=list)
    plan_risk_summary: dict = field(default_factory=dict)
    top_expensive_queries: list[dict] = field(default_factory=list)
    regime_scenarios: list[str] = field(default_factory=list)
    ecosystem_context: dict = field(default_factory=dict)
    graph_intelligence: dict = field(default_factory=dict)
    replay_budget_summary: dict = field(default_factory=dict)
    replay_fidelity_score: float = 0.0
    finops_summary: dict = field(default_factory=dict)

    def compute_confidence(self) -> None:
        if self.queries_replayed == 0:
            self.verdict = TunnelVerdict.NO_QUERIES
            self.confidence_score = 0.0
            return

        pass_rate = self.queries_passed / self.queries_replayed
        self.confidence_score = round(pass_rate * 100, 1)

        if pass_rate == 1.0:
            self.verdict = TunnelVerdict.SAFE
        elif pass_rate >= 0.95:
            self.verdict = TunnelVerdict.SAFE_WITH_PATCHES
        else:
            self.verdict = TunnelVerdict.BLOCKED

    def finalise_debug_summary(self) -> None:
        focus_assets: list[str] = []
        failure_modes: list[str] = []
        debug_steps: list[str] = []
        touched_queries = self.broken_queries + self.mismatch_queries
        for query in touched_queries:
            focus_assets.extend(query.affected_cols[:4])
            if query.clone_error:
                failure_modes.append((query.clone_error or "")[:120])
            elif query.row_delta:
                failure_modes.append(
                    f"Row-count drift detected in {query.query_id} ({query.row_delta:+,})"
                )
        for risk in self.semantic_risks:
            focus_assets.append(risk.column)
            failure_modes.append(f"{risk.risk_type}: {risk.description}")
            if risk.suggestion:
                debug_steps.append(risk.suggestion)
        if self.broken_queries:
            debug_steps.append(
                "Start with the broken queries and patch the first removed/renamed column they still reference."
            )
        if self.mismatch_queries:
            debug_steps.append(
                "Compare before/after row counts and predicates to isolate silent semantic drift before merge."
            )
        if not debug_steps and self.verdict == TunnelVerdict.SAFE_WITH_PATCHES:
            debug_steps.append(
                "Address the suggested patches, then rerun the scoped replay before merge."
            )
        elif not debug_steps and self.verdict == TunnelVerdict.SAFE:
            debug_steps.append(
                "Merge is safe at the current evidence level; keep the replay receipt for release review."
            )
        self.debug_focus_assets = list(dict.fromkeys(a for a in focus_assets if a))[:10]
        self.top_failure_modes = list(dict.fromkeys(m for m in failure_modes if m))[:6]
        self.suggested_debug_steps = list(dict.fromkeys(s for s in debug_steps if s))[:6]
        prevented = []
        if self.queries_broken:
            prevented.append(
                f"Prevented {len(self.broken_queries)} hard query failures before merge."
            )
        if self.queries_mismatch:
            prevented.append(
                f"Prevented {len(self.mismatch_queries)} silent mismatches from reaching production."
            )
        if self.semantic_risks:
            prevented.append(
                f"Surfaced {len(self.semantic_risks)} semantic risks for rollout review."
            )
        self.prevention_summary = prevented[:4]
        budget = self.replay_budget_summary or {}
        candidate = float(budget.get("candidate_queries") or self.queries_replayed or 0)
        selected = float(budget.get("selected_queries") or self.queries_replayed or 0)
        focus = float(budget.get("focus_hit_rate") or budget.get("focus_hit_coverage") or 100.0)
        if candidate > 0 and selected > 0:
            coverage = min(selected / candidate, 1.0)
            self.replay_fidelity_score = round(
                min(100.0, (0.55 * coverage + 0.45 * (focus / 100.0)) * 100.0), 1
            )
        elif self.queries_replayed:
            self.replay_fidelity_score = 100.0
        self.replay_scope_summary = {
            "queries_with_failures": len(self.broken_queries),
            "queries_with_mismatch": len(self.mismatch_queries),
            "focus_assets": self.debug_focus_assets,
            "semantic_risk_count": len(self.semantic_risks),
        }
        self.query_mix_summary = {
            "historical_queries": self.historical_queries_replayed,
            "synthetic_queries": self.synthetic_queries_replayed,
            "future_queries": self.future_queries_generated,
            "regimes": self.regime_scenarios,
        }

    def to_dict(self) -> dict:
        self.finalise_debug_summary()
        return {
            "run_id": self.run_id,
            "clone_name": self.clone_name,
            "db_dialect": self.db_dialect,
            "migration_summary": self.migration_summary,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_s": round(self.duration_s, 1),
            "queries_replayed": self.queries_replayed,
            "queries_passed": self.queries_passed,
            "queries_broken": self.queries_broken,
            "queries_mismatch": self.queries_mismatch,
            "confidence_score": self.confidence_score,
            "verdict": self.verdict.value,
            "patches_available": self.patches_available,
            "migration_applied": self.migration_applied,
            "clone_created": self.clone_created,
            "broken_queries": [q.to_dict() for q in self.broken_queries[:10]],
            "mismatch_queries": [q.to_dict() for q in self.mismatch_queries[:5]],
            "semantic_risks": [r.to_dict() for r in self.semantic_risks],
            "debug_focus_assets": self.debug_focus_assets,
            "top_failure_modes": self.top_failure_modes,
            "suggested_debug_steps": self.suggested_debug_steps,
            "replay_scope_summary": self.replay_scope_summary,
            "historical_queries_replayed": self.historical_queries_replayed,
            "synthetic_queries_replayed": self.synthetic_queries_replayed,
            "future_queries_generated": self.future_queries_generated,
            "query_mix_summary": self.query_mix_summary,
            "prevention_summary": self.prevention_summary,
            "compute_cost_risk": round(self.compute_cost_risk, 2),
            "compute_cost_notes": self.compute_cost_notes,
            "plan_risk_summary": self.plan_risk_summary,
            "top_expensive_queries": self.top_expensive_queries,
            "regime_scenarios": self.regime_scenarios,
            "ecosystem_context": self.ecosystem_context,
            "graph_intelligence": self.graph_intelligence,
            "replay_budget_summary": self.replay_budget_summary,
            "finops_summary": self.finops_summary,
            "error": self.error,
        }

    def save(self, path: str = "data/simulation_receipt.json") -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), indent=2, default=str))
        log.info(f"Simulation receipt → {p}")
        return p

    def to_pr_comment(self) -> str:
        """Format the receipt as a GitHub PR comment block."""
        self.finalise_debug_summary()
        emoji = _VERDICT_EMOJI.get(self.verdict, "❓")

        title_map = {
            TunnelVerdict.SAFE: f"Migration validated — {self.queries_passed}/{self.queries_replayed} queries passed",
            TunnelVerdict.SAFE_WITH_PATCHES: f"Safe with patches — {self.queries_broken} "
            f"quer{'y' if self.queries_broken == 1 else 'ies'} need fixing",
            TunnelVerdict.BLOCKED: f"Migration blocked — {self.queries_broken} "
            f"quer{'y' if self.queries_broken == 1 else 'ies'} break",
            TunnelVerdict.NO_QUERIES: "No historical queries found — synthetic replay used",
            TunnelVerdict.ERROR: "Wind Tunnel could not complete",
        }
        title = title_map.get(self.verdict, "Simulation complete")

        lines = [
            f"### {emoji} SemZero Migration Wind Tunnel — {title}",
            "",
            "| | |",
            "|---|---|",
            f"| **Clone** | `{self.clone_name}` (destroyed after simulation) |",
            f"| **Dialect** | `{self.db_dialect}` |",
            f"| **Duration** | {self.duration_s:.0f}s |",
            f"| **Queries replayed** | {self.queries_replayed} |",
            f"| **Confidence** | {self.confidence_score}% |",
        ]
        finops = self.finops_summary or {}
        if finops.get("projected_weekly_cost_usd"):
            lines += [
                f"| **Projected compute waste avoided** | ${float(finops.get('projected_weekly_cost_usd', 0.0)):,.0f} / week |",
                f"| **FinOps confidence** | {finops.get('confidence', 'medium')} |",
            ]
        lines += [
            "",
            "### Query Results",
            "",
            "| Status | Count | % |",
            "|--------|-------|---|",
            f"| ✅ Passed | {self.queries_passed} "
            f"| {self.queries_passed / max(self.queries_replayed, 1) * 100:.1f}% |",
        ]
        if self.queries_broken > 0:
            lines.append(
                f"| 🚫 Broken | {self.queries_broken} "
                f"| {self.queries_broken / max(self.queries_replayed, 1) * 100:.1f}% |"
            )
        if self.queries_mismatch > 0:
            lines.append(
                f"| ⚠️ Row mismatch | {self.queries_mismatch} "
                f"| {self.queries_mismatch / max(self.queries_replayed, 1) * 100:.1f}% |"
            )
        lines.append("")

        # Broken queries
        if self.broken_queries:
            lines += [
                "### 🚫 Broken Queries",
                "",
                "These queries will fail after this migration merges:",
                "",
            ]
            for i, q in enumerate(self.broken_queries[:5], 1):
                preview = q.query_text[:100].replace("\n", " ")
                cols = ", ".join(f"`{c}`" for c in q.affected_cols[:3])
                err = (q.clone_error or "unknown error")[:120]
                lines += [
                    f"**{i}. `{q.query_id}`**",
                    "```sql",
                    f"{preview}{'...' if len(q.query_text) > 100 else ''}",
                    "```",
                    f"Error: `{err}`",
                ]
                if cols:
                    lines.append(f"Affected columns: {cols}")
                lines.append("")

        # Row mismatches
        if self.mismatch_queries:
            lines += [
                "### ⚠️ Row Count Mismatches",
                "",
                "| Query | Before | After | Delta |",
                "|-------|--------|-------|-------|",
            ]
            for q in self.mismatch_queries[:5]:
                orig = q.original_rows or 0
                clone = q.clone_rows or 0
                delta = clone - orig
                lines.append(f"| `{q.query_id}` | {orig:,} | {clone:,} | `{delta:+,}` |")
            lines.append("")

        # Semantic risks
        if self.semantic_risks:
            lines += ["### ⚠️ Semantic Risk Analysis", ""]
            for risk in self.semantic_risks:
                lines.append(
                    f"- **{risk.risk_type}** on `{risk.column}` "
                    f"({risk.severity}): {risk.description}"
                )
                if risk.suggestion:
                    lines.append(f"  _Suggestion: {risk.suggestion}_")
            lines.append("")

        # Patches
        if self.patches_available:
            lines += [
                "### ⚡ Auto-Generated Patches",
                "",
                "SemZero has generated patches for broken queries:",
                "",
            ]
            for p in self.patches_available:
                lines.append(f"- {p}")
            lines.append("")

        if self.suggested_debug_steps:
            lines += ["### 🧭 Debug next steps", ""]
            lines.extend(f"- {step}" for step in self.suggested_debug_steps[:4])
            if self.debug_focus_assets:
                lines.append("")
                lines.append(
                    "Focus first on: "
                    + ", ".join(f"`{asset}`" for asset in self.debug_focus_assets[:6])
                )
                lines.append("")

        # Error detail
        if self.error and self.verdict == TunnelVerdict.ERROR:
            lines += [
                "### ℹ️ Error Detail",
                "",
                f"> {self.error[:400]}",
                "",
            ]

        lines += [
            "---",
            f"*SemZero Migration Wind Tunnel · Run `{self.run_id}` · "
            "[What is this?](https://github.com/semzero)*",
        ]
        return "\n".join(lines)


# ── Config ─────────────────────────────────────────────────────────────────────


@dataclass
class WindTunnelConfig:
    """
    Configuration for the Wind Tunnel.

    Minimal setup (SQLite):
        WindTunnelConfig(db_url="sqlite:///mydb.db")

    Postgres:
        WindTunnelConfig(db_url="postgresql://user:pw@host/db")

    Snowflake (native CLONE):
        WindTunnelConfig(
            db_url="",
            snowflake_account="myaccount",
            snowflake_user="myuser",
            snowflake_password="mypassword",
            snowflake_database="PROD",
            snowflake_warehouse="COMPUTE_WH",
        )
    """

    db_url: str = ""
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
    databricks_query_history_table: str = "system.query.history"
    clone_prefix: str = "SEMZERO_WT"

    pg_clone_suffix: str = "_semzero_wt"

    max_queries: int = 500
    lookback_days: int = 7
    query_timeout_s: int = 30
    row_sample_limit: int = 1000
    query_source: str = (
        "auto"  # auto | pg_stat | snowflake | databricks | bigquery | synthetic | provided
    )
    provided_queries: list[str] = field(default_factory=list)
    query_files: list[str] = field(default_factory=list)
    query_directories: list[str] = field(default_factory=list)
    workload_history_files: list[str] = field(default_factory=list)
    dbt_manifest_path: str = ""
    dbt_run_results_path: str = ""
    dbt_sources_path: str = ""
    dbt_catalog_path: str = ""
    openlineage_paths: list[str] = field(default_factory=list)
    airflow_paths: list[str] = field(default_factory=list)
    dagster_paths: list[str] = field(default_factory=list)
    looker_paths: list[str] = field(default_factory=list)
    montecarlo_paths: list[str] = field(default_factory=list)
    focus_assets: list[str] = field(default_factory=list)
    focus_changed_assets: bool = True
    synthetic_future_enabled: bool = True
    synthetic_future_max_queries: int = 12
    regime_switching_enabled: bool = True
    regime_names: list[str] = field(default_factory=lambda: ["quarter_end", "backfill_window"])
    explain_plan_enabled: bool = True
    graph_intelligence_enabled: bool = True
    rgcn_model_path: str = ""
    bigquery_region: str = "region-us"
    bigquery_project: str = ""

    compare_row_counts: bool = True
    tolerance_pct: float = 0.001
    compare_value_fingerprints: bool = True
    fingerprint_row_limit: int = 25
    allow_added_columns: bool = False

    run_semantic_analysis: bool = True

    data_dir: str = "data"
    post_to_pr: bool = True
    github_token: str = ""
    github_repo: str = ""

    dry_run: bool = False
    auto_destroy_clone: bool = True


class CloneManager:
    """
    Creates and destroys database clones.

    SQLite   → file copy (instant, works everywhere)
    Postgres → CREATE DATABASE clone TEMPLATE original
    Snowflake→ CREATE DATABASE clone CLONE original (zero-copy)
    Databricks→ SHALLOW CLONE Delta tables into an isolated schema
    """

    def __init__(self, config: WindTunnelConfig, dialect: str, run_id: str) -> None:
        self.config = config
        self.dialect = dialect
        self.run_id = run_id
        self._clone_path: Optional[str] = None  # SQLite only
        self._clone_dbname: Optional[str] = None  # Postgres / Snowflake
        self._clone_schema: Optional[str] = None  # Databricks
        self._clone_catalog: Optional[str] = None  # Databricks
        self._tmp_dir: Optional[str] = None

    def create(self, orig_engine: Any) -> Any:
        """
        Create a clone and return a SQLAlchemy engine pointing to it.
        Raises RuntimeError if the dialect is not supported.
        """
        if self.config.dry_run:
            log.info("Wind Tunnel: dry_run=True — skipping real clone")
            return orig_engine  # reuse original in dry-run

        if self.dialect == "sqlite":
            return self._clone_sqlite(orig_engine)
        if self.dialect in ("postgresql", "postgres"):
            return self._clone_postgres(orig_engine)
        if self.dialect == "snowflake":
            return self._clone_snowflake()
        if self.dialect == "databricks":
            return self._clone_databricks(orig_engine)

        raise RuntimeError(
            f"Wind Tunnel clone not supported for dialect '{self.dialect}'. "
            f"Supported: sqlite, postgresql, snowflake, databricks. "
            f"Set dry_run=True for synthetic-only replay on other dialects."
        )

    def destroy(self, clone_engine: Any) -> None:
        """Tear down the clone. Always called in finally block."""
        if self.config.dry_run:
            return
        try:
            if self.dialect == "sqlite":
                if clone_engine:
                    clone_engine.dispose()
                if self._clone_path:
                    Path(self._clone_path).unlink(missing_ok=True)
                if self._tmp_dir:
                    shutil.rmtree(self._tmp_dir, ignore_errors=True)
                log.info("Wind Tunnel: SQLite clone removed.")

            elif self.dialect in ("postgresql", "postgres"):
                self._drop_postgres(clone_engine)

            elif self.dialect == "snowflake":
                self._drop_snowflake(clone_engine)

            elif self.dialect == "databricks":
                self._drop_databricks(clone_engine)
        except Exception as exc:
            log.warning(f"Wind Tunnel clone cleanup failed (non-fatal): {exc}")

    # ── SQLite ────────────────────────────────────────────────────────────────

    def _clone_sqlite(self, engine: Any) -> Any:
        from sqlalchemy import create_engine as ce

        url_str = str(engine.url)
        db_path_str = url_str.replace("sqlite:///", "").replace("sqlite://", "")

        self._tmp_dir = tempfile.mkdtemp(prefix="semzero_wt_")
        clone_path = Path(self._tmp_dir) / f"clone_{self.run_id[:8]}.db"
        self._clone_path = str(clone_path)

        if db_path_str in ("", ":memory:"):
            # In-memory: use sqlite3 backup API
            import sqlite3

            raw = engine.raw_connection()
            dst = sqlite3.connect(str(clone_path))
            raw.backup(dst)
            dst.close()
            raw.close()
        else:
            src = Path(db_path_str)
            if not src.exists():
                raise FileNotFoundError(f"Wind Tunnel: SQLite source not found: {src}")
            shutil.copy2(src, clone_path)

        log.info(f"Wind Tunnel: SQLite clone → {clone_path}")
        return ce(
            f"sqlite:///{clone_path}",
            connect_args={"check_same_thread": False},
        )

    # ── Postgres ──────────────────────────────────────────────────────────────

    def _clone_postgres(self, engine: Any) -> Any:
        from sqlalchemy import create_engine as ce, text

        url = engine.url
        src_db = url.database
        clone_db = f"{src_db}{self.config.pg_clone_suffix}_{self.run_id[:6]}"
        self._clone_dbname = clone_db

        # Connect to postgres maintenance DB (required for CREATE DATABASE)
        maint_url = url.set(database="postgres")
        maint_eng = ce(
            maint_url.render_as_string(hide_password=False),
            isolation_level="AUTOCOMMIT",
        )
        try:
            with maint_eng.connect() as conn:
                # Drop any stale clone with same name
                conn.execute(text(f'DROP DATABASE IF EXISTS "{clone_db}"'))
                # Terminate open connections on src so TEMPLATE copy can proceed
                conn.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) "
                        "FROM pg_stat_activity "
                        "WHERE datname = :db AND pid <> pg_backend_pid()"
                    ),
                    {"db": src_db},
                )
                conn.execute(text(f'CREATE DATABASE "{clone_db}" TEMPLATE "{src_db}"'))
            log.info(f"Wind Tunnel: Postgres clone → {clone_db}")
        finally:
            maint_eng.dispose()

        clone_url = url.set(database=clone_db)
        return ce(
            clone_url.render_as_string(hide_password=False),
            pool_pre_ping=True,
            pool_size=3,
        )

    def _drop_postgres(self, clone_engine: Any) -> None:
        from sqlalchemy import create_engine as ce, text

        if clone_engine:
            clone_engine.dispose()
        url = clone_engine.url if clone_engine else None
        if not url or not self._clone_dbname:
            return
        maint_url = url.set(database="postgres")
        maint_eng = ce(
            maint_url.render_as_string(hide_password=False),
            isolation_level="AUTOCOMMIT",
        )
        try:
            with maint_eng.connect() as conn:
                conn.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) "
                        "FROM pg_stat_activity "
                        f"WHERE datname = '{self._clone_dbname}'"
                    )
                )
                conn.execute(text(f'DROP DATABASE IF EXISTS "{self._clone_dbname}"'))
            log.info(f"Wind Tunnel: Postgres clone dropped → {self._clone_dbname}")
        finally:
            maint_eng.dispose()

    # ── Snowflake ─────────────────────────────────────────────────────────────

    def _clone_snowflake(self) -> Any:
        from sqlalchemy import text

        c = self.config
        clone = f"{c.clone_prefix}_{self.run_id[:8].upper()}"
        self._clone_dbname = clone
        src_eng = self._sf_engine(c.snowflake_database)
        try:
            with src_eng.connect() as conn:
                conn.execute(text(f"CREATE DATABASE {clone} CLONE {c.snowflake_database}"))
            log.info(f"Wind Tunnel: Snowflake zero-copy clone → {clone}")
        finally:
            src_eng.dispose()
        return self._sf_engine(clone)

    def _drop_snowflake(self, clone_engine: Any) -> None:
        from sqlalchemy import text

        if clone_engine:
            clone_engine.dispose()
        if not self._clone_dbname:
            return
        eng = self._sf_engine(self.config.snowflake_database)
        try:
            with eng.connect() as conn:
                conn.execute(text(f"DROP DATABASE IF EXISTS {self._clone_dbname}"))
            log.info(f"Wind Tunnel: Snowflake clone dropped → {self._clone_dbname}")
        finally:
            eng.dispose()

    # ── Databricks ───────────────────────────────────────────────────────────

    def _clone_databricks(self, engine: Any) -> Any:
        from sqlalchemy import create_engine as ce, text

        catalog = (self.config.databricks_catalog or "").strip()
        schema = (self.config.databricks_schema or "default").strip()
        if not catalog:
            raise RuntimeError(
                "Wind Tunnel: databricks_catalog is required for SHALLOW CLONE replay."
            )

        clone_catalog = (self.config.databricks_clone_catalog or catalog).strip()
        clone_schema = f"{schema}_{self.run_id[:8].lower()}"
        self._clone_catalog = clone_catalog
        self._clone_schema = clone_schema

        tables: list[str] = []
        with engine.begin() as conn:
            conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {clone_catalog}.{clone_schema}"))
            rows = conn.execute(text(f"SHOW TABLES IN {catalog}.{schema}")).fetchall()
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
                tables.append(table_name)
                conn.execute(
                    text(
                        f"CREATE OR REPLACE TABLE {clone_catalog}.{clone_schema}.{table_name} "
                        f"SHALLOW CLONE {catalog}.{schema}.{table_name}"
                    )
                )
        log.info(
            "Wind Tunnel: Databricks SHALLOW CLONE schema → %s.%s (%s table(s))",
            clone_catalog,
            clone_schema,
            len(tables),
        )
        clone_engine = ce(engine.url.render_as_string(hide_password=False), pool_pre_ping=True)
        setattr(
            clone_engine,
            "_semzero_clone_map",
            {f"{catalog}.{schema}": f"{clone_catalog}.{clone_schema}", schema: clone_schema},
        )
        setattr(
            clone_engine,
            "_semzero_use_statements",
            [f"USE CATALOG {clone_catalog}", f"USE SCHEMA {clone_schema}"],
        )
        setattr(
            clone_engine,
            "_semzero_clone_assets",
            [f"{clone_catalog}.{clone_schema}.{table}" for table in tables],
        )
        return clone_engine

    def _drop_databricks(self, clone_engine: Any) -> None:
        from sqlalchemy import text

        if not clone_engine or not self._clone_catalog or not self._clone_schema:
            return
        try:
            with clone_engine.begin() as conn:
                conn.execute(
                    text(
                        f"DROP SCHEMA IF EXISTS {self._clone_catalog}.{self._clone_schema} CASCADE"
                    )
                )
            log.info(
                "Wind Tunnel: Databricks clone schema dropped → %s.%s",
                self._clone_catalog,
                self._clone_schema,
            )
        finally:
            clone_engine.dispose()

    @staticmethod
    def _row_to_mapping(row: Any) -> dict[str, Any]:
        if hasattr(row, "_mapping"):
            return {str(k): v for k, v in row._mapping.items()}
        if isinstance(row, dict):
            return row
        if isinstance(row, (list, tuple)):
            return {str(i): v for i, v in enumerate(row)}
        return {"value": row}

    def _sf_engine(self, database: str) -> Any:
        from sqlalchemy import create_engine as ce

        c = self.config
        url = (
            f"snowflake://{c.snowflake_user}:{c.snowflake_password}"
            f"@{c.snowflake_account}/{database}/{c.snowflake_schema}"
        )
        if c.snowflake_warehouse:
            url += f"?warehouse={c.snowflake_warehouse}"
        return ce(url, pool_pre_ping=True, pool_size=2)


# ── Migration applicator ───────────────────────────────────────────────────────


class MigrationApplicator:
    """
    Applies migration SQL to a cloned database.
    Supports two input modes:
      - raw SQL string (one or more statements separated by `;`)
      - drift_report dict (SemZero drift format → generates DDL automatically)
    """

    def apply_sql(self, engine: Any, migration_sql: str) -> Optional[str]:
        """
        Execute raw migration SQL on engine.
        Returns error string on failure, None on success.
        """
        from sqlalchemy import text

        stmts = [s.strip() for s in migration_sql.split(";") if s.strip()]
        if not stmts:
            return "Empty migration SQL"
        try:
            with engine.begin() as conn:
                for stmt in stmts:
                    conn.execute(text(stmt))
            return None
        except Exception as exc:
            return str(exc)

    def apply_drift_report(
        self,
        engine: Any,
        drift_report: dict,
        dialect: str,
        schema: str = "",
    ) -> tuple[int, list[str]]:
        """
        Translate a SemZero drift report into DDL and apply it.
        Returns (statements_applied, errors).
        """
        from sqlalchemy import text

        stmts = self._drift_to_ddl(drift_report, dialect, schema)
        errors: list[str] = []
        applied = 0
        try:
            with engine.begin() as conn:
                for stmt in stmts:
                    try:
                        conn.execute(text(stmt))
                        applied += 1
                        log.debug(f"Applied: {stmt[:80]}")
                    except Exception as exc:
                        log.warning(f"Stmt failed (continuing): {exc}")
                        errors.append(str(exc))
        except Exception as exc:
            errors.append(str(exc))
        return applied, errors

    def _drift_to_ddl(self, drift_report: dict, dialect: str, schema_prefix: str) -> list[str]:
        stmts: list[str] = []
        sfx = f'"{schema_prefix}".' if schema_prefix else ""

        for event in drift_report.get("events", []):
            ct = event.get("change_type", "")
            node_id = event.get("node_id", "")
            before = event.get("before") or {}
            after = event.get("after") or {}

            if "." not in node_id:
                continue
            table, col = node_id.split(".", 1)

            if ct == "COLUMN_RENAMED":
                detail = event.get("detail", "")
                new_col = re.search(r"renamed to '[^.]+\.(\w+)'", detail)
                if new_col:
                    nc = new_col.group(1)
                    if dialect == "snowflake":
                        stmts.append(f'ALTER TABLE {sfx}"{table}" RENAME COLUMN "{col}" TO "{nc}"')
                    elif dialect in ("postgresql", "postgres"):
                        stmts.append(f'ALTER TABLE {sfx}"{table}" RENAME COLUMN "{col}" TO "{nc}"')
                    else:
                        stmts.append(f'ALTER TABLE "{table}" RENAME COLUMN "{col}" TO "{nc}"')

            elif ct == "COLUMN_REMOVED":
                stmts.append(f'ALTER TABLE {sfx}"{table}" DROP COLUMN IF EXISTS "{col}"')

            elif ct == "TYPE_CHANGED":
                dtype = after.get("dtype", "VARCHAR")
                if dialect == "snowflake":
                    stmts.append(f'ALTER TABLE {sfx}"{table}" ALTER COLUMN "{col}" TYPE {dtype}')
                elif dialect in ("postgresql", "postgres"):
                    stmts.append(
                        f'ALTER TABLE {sfx}"{table}" '
                        f'ALTER COLUMN "{col}" TYPE {dtype} USING "{col}"::{dtype}'
                    )

            elif ct == "NULLABLE_CHANGED":
                new_null = after.get("nullable", True)
                kw = "DROP NOT NULL" if new_null else "SET NOT NULL"
                if dialect not in ("sqlite",):
                    stmts.append(f'ALTER TABLE {sfx}"{table}" ALTER COLUMN "{col}" {kw}')

            elif ct == "COLUMN_ADDED":
                dtype = after.get("dtype", "VARCHAR")
                nullable = "" if after.get("nullable", True) else " NOT NULL"
                stmts.append(f'ALTER TABLE {sfx}"{table}" ADD COLUMN "{col}" {dtype}{nullable}')

        return stmts


# ── Query extractor ────────────────────────────────────────────────────────────


class QueryPlanRiskEstimator:
    def __init__(self, engine: Any = None, dialect: str = "", enabled: bool = True) -> None:
        self.engine = engine
        self.dialect = dialect
        self.enabled = enabled
        self.last_summary: dict[str, Any] = {}
        self.last_top_queries: list[dict[str, Any]] = []
        self.last_finops_summary: dict[str, Any] = {}

    def estimate(self, queries: list[dict]) -> tuple[float, list[str]]:
        scores: list[float] = []
        notes: list[str] = []
        top_queries: list[dict[str, Any]] = []
        aggregate = {
            "queries_analysed": 0,
            "full_scan_signals": 0,
            "join_signals": 0,
            "sort_signals": 0,
            "window_signals": 0,
            "aggregate_signals": 0,
        }
        for query in queries[:20]:
            sql = str(query.get("query_text") or "")
            lowered = sql.lower()
            score = 0.0
            reasons: list[str] = []
            join_count = lowered.count(" join ")
            if join_count:
                score += join_count * 12
                aggregate["join_signals"] += join_count
                reasons.append(f"{join_count} join(s)")
            group_count = lowered.count(" group by ")
            if group_count:
                score += group_count * 6
                aggregate["aggregate_signals"] += group_count
                reasons.append("grouped aggregation")
            order_count = lowered.count(" order by ")
            if order_count:
                score += order_count * 4
                aggregate["sort_signals"] += order_count
                reasons.append("sort/order")
            if " distinct " in lowered:
                score += 5
                reasons.append("distinct deduplication")
            window_count = lowered.count(" over (")
            if window_count:
                score += window_count * 8
                aggregate["window_signals"] += window_count
                reasons.append("window function")
            if " union " in lowered:
                score += 5
                reasons.append("union merge")
            if any(tok in lowered for tok in ("sum(", "avg(", "count(", "min(", "max(")):
                score += 4
                reasons.append("aggregate function")
            if self.enabled and self.engine is not None:
                plan = self._explain(sql)
                score += plan["score"]
                for key in aggregate:
                    if key in plan["summary"]:
                        aggregate[key] += plan["summary"][key]
                reasons.extend(plan["reasons"])
                notes.extend(plan["notes"])
            if score >= 18:
                qid = str(query.get("query_id") or "query")
                notes.append(f"{qid}: compute-heavy pattern score {score:.0f}")
                top_queries.append(
                    {
                        "query_id": qid,
                        "score": round(score, 2),
                        "reasons": list(dict.fromkeys(reasons))[:5],
                    }
                )
            scores.append(score)
            aggregate["queries_analysed"] += 1
        self.last_top_queries = sorted(top_queries, key=lambda x: x["score"], reverse=True)[:6]
        self.last_summary = aggregate
        projected_run_cost = 0.0
        projected_weekly_cost = 0.0
        driver_notes: list[str] = []
        for query in queries[:20]:
            sql = str(query.get("query_text") or "")
            qid = str(query.get("query_id") or "query")
            qscore = next((item["score"] for item in top_queries if item["query_id"] == qid), 0.0)
            run_cost, weekly_cost, reasons = estimate_query_finops(
                sql, qscore, query.get("calls"), str(query.get("source") or "")
            )
            projected_run_cost += run_cost
            projected_weekly_cost += weekly_cost
            if reasons and weekly_cost >= 5.0:
                driver_notes.append(f"{qid}: {'; '.join(reasons[:3])} → ≈ ${weekly_cost:,.0f}/week")
        confidence = (
            "high"
            if any(
                str(q.get("source") or "").startswith(
                    ("pg_stat", "snowflake", "databricks", "bigquery", "history:")
                )
                for q in queries
            )
            else "medium"
        )
        self.last_finops_summary = {
            "source": "warehouse_history" if confidence == "high" else "heuristic_query_mix",
            "confidence": confidence,
            "projected_run_cost_usd": round(projected_run_cost, 2),
            "projected_weekly_cost_usd": round(projected_weekly_cost, 2),
            "projected_monthly_cost_usd": round(projected_weekly_cost * 4.3, 2),
            "blocked_weekend_waste_usd": round(projected_weekly_cost * (2 / 7), 2),
            "drivers": self.last_top_queries[:5],
            "notes": list(dict.fromkeys(driver_notes + notes))[:8],
        }
        if not scores:
            return 0.0, []
        avg = sum(scores) / len(scores)
        return min(100.0, round(avg, 2)), list(dict.fromkeys(notes))[:8]

    def _explain(self, sql: str) -> dict[str, Any]:
        payload = {"score": 0.0, "notes": [], "reasons": [], "summary": {}}
        try:
            from sqlalchemy import text as _text

            with self.engine.connect() as conn:
                if self.dialect == "sqlite":
                    rows = conn.execute(_text(f"EXPLAIN QUERY PLAN {sql}")).fetchall()
                    lines = [" ".join(str(part) for part in row) for row in rows]
                    scans = sum(1 for row in lines if "scan" in row.lower())
                    temp = sum(1 for row in lines if "temp b-tree" in row.lower())
                    searches = sum(1 for row in lines if "search" in row.lower())
                    payload["score"] += scans * 7 + temp * 5 + max(0, len(lines) - searches) * 1.0
                    if scans:
                        payload["reasons"].append(f"{scans} full scan plan signal(s)")
                        payload["notes"].append(
                            f"EXPLAIN plan shows {scans} scan step(s); consider filters or indexes before merge."
                        )
                    if temp:
                        payload["reasons"].append("temp sort/spill")
                        payload["notes"].append(
                            "EXPLAIN plan uses temporary B-tree operations that can inflate compute on larger datasets."
                        )
                    payload["summary"] = {"full_scan_signals": scans, "sort_signals": temp}
                elif self.dialect in ("postgresql", "postgres"):
                    plan = conn.execute(_text(f"EXPLAIN (FORMAT JSON) {sql}"))
                    raw = plan.scalar()
                    import json as _json

                    if isinstance(raw, str):
                        data = _json.loads(raw)
                    else:
                        data = raw
                    node = data[0].get("Plan", {}) if isinstance(data, list) and data else {}
                    summary = self._summarise_pg_plan(node)
                    payload["summary"] = summary
                    payload["score"] += (
                        summary["full_scan_signals"] * 8
                        + summary["join_signals"] * 6
                        + summary["sort_signals"] * 4
                    )
                    if summary["full_scan_signals"]:
                        payload["reasons"].append(f"{summary['full_scan_signals']} seq scan(s)")
                    if summary["join_signals"]:
                        payload["reasons"].append(f"{summary['join_signals']} join operator(s)")
                    if summary["sort_signals"]:
                        payload["reasons"].append(
                            f"{summary['sort_signals']} sort/materialize operator(s)"
                        )
                    if summary["rows_estimate"] > 100000:
                        payload["score"] += 6
                        payload["reasons"].append(f"rows estimate {summary['rows_estimate']:,}")
                        payload["notes"].append(
                            f"EXPLAIN estimates {summary['rows_estimate']:,} rows; validate filters before merge."
                        )
        except Exception:
            return payload
        return payload

    def _summarise_pg_plan(self, node: dict[str, Any]) -> dict[str, Any]:
        summary = {"full_scan_signals": 0, "join_signals": 0, "sort_signals": 0, "rows_estimate": 0}

        def walk(current: dict[str, Any]) -> None:
            if not isinstance(current, dict):
                return
            node_type = str(current.get("Node Type", ""))
            summary["rows_estimate"] = max(
                summary["rows_estimate"], int(current.get("Plan Rows") or 0)
            )
            if "Seq Scan" in node_type:
                summary["full_scan_signals"] += 1
            if "Join" in node_type or node_type in {"Nested Loop", "Hash Join", "Merge Join"}:
                summary["join_signals"] += 1
            if node_type in {
                "Sort",
                "Materialize",
                "Unique",
                "Aggregate",
                "GroupAggregate",
                "HashAggregate",
            }:
                summary["sort_signals"] += 1
            for child in current.get("Plans", []) or []:
                walk(child)

        walk(node)
        return summary


class QueryExtractor:
    """
    Extract representative queries from real workload sources when available,
    then fall back to synthetic workload generation.
    """

    def __init__(self, engine: Any, dialect: str, config: WindTunnelConfig) -> None:
        self.engine = engine
        self.dialect = dialect
        self.config = config
        self.last_budget_summary: dict[str, Any] = {}

    def extract(
        self,
        graph_json: Optional[dict] = None,
        drift_report: Optional[dict] = None,
        graph_intelligence: Optional[dict] = None,
    ) -> list[dict]:
        queries: list[dict] = []
        ecosystem = EcosystemContext.load(
            dbt_manifest_path=self.config.dbt_manifest_path,
            dbt_catalog_path=self.config.dbt_catalog_path,
            dbt_run_results_path=self.config.dbt_run_results_path,
            dbt_sources_path=self.config.dbt_sources_path,
            openlineage_paths=list(self.config.openlineage_paths),
            airflow_paths=list(self.config.airflow_paths),
            dagster_paths=list(self.config.dagster_paths),
            looker_paths=list(self.config.looker_paths),
            montecarlo_paths=list(self.config.montecarlo_paths),
        )

        if self.config.provided_queries:
            for i, item in enumerate(self.config.provided_queries):
                if isinstance(item, dict):
                    qtext = str(item.get("query_text") or "").strip()
                    if not qtext:
                        continue
                    payload = dict(item)
                    payload.setdefault("query_id", f"USR_{i:04d}")
                    payload.setdefault("rows", 0)
                    payload.setdefault("source", "provided")
                    payload["query_text"] = qtext
                    queries.append(payload)
                else:
                    qtext = str(item or "").strip()
                    if qtext:
                        queries.append(
                            {
                                "query_id": f"USR_{i:04d}",
                                "query_text": qtext,
                                "rows": 0,
                                "source": "provided",
                            }
                        )
            log.info("Wind Tunnel: %s provided queries loaded", len(queries))

        file_queries = self._from_query_files()
        if file_queries:
            queries.extend(file_queries)
            log.info("Wind Tunnel: %s queries loaded from SQL files", len(file_queries))

        history_queries = self._from_history_files()
        if history_queries:
            queries.extend(history_queries)
            log.info(
                "Wind Tunnel: %s queries loaded from workload history files", len(history_queries)
            )

        artifact_queries = self._from_dbt_artifacts()
        if artifact_queries:
            queries.extend(artifact_queries)
            log.info("Wind Tunnel: %s queries loaded from dbt artifacts", len(artifact_queries))

        if self.dialect in ("postgresql", "postgres") and self.config.query_source in (
            "auto",
            "pg_stat",
        ):
            queries.extend(self._from_pg_stat())
        elif self.dialect == "snowflake" and self.config.query_source in ("auto", "snowflake"):
            queries.extend(self._from_snowflake_account_usage())
        elif self.dialect == "databricks" and self.config.query_source in ("auto", "databricks"):
            queries.extend(self._from_databricks_system_history())
        elif self.dialect == "bigquery" and self.config.query_source in ("auto", "bigquery"):
            queries.extend(self._from_bigquery_jobs())

        if not queries or self.config.query_source == "synthetic":
            log.info("Wind Tunnel: using synthetic workload coverage")
            queries.extend(self._synthetic(graph_json, drift_report))

        queries.extend(self._from_openlineage_and_looker(ecosystem))

        if self.config.synthetic_future_enabled and graph_json and drift_report:
            queries.extend(self._synthetic_future(graph_json, drift_report))

        queries = self._sanitise(queries)
        focus_assets = self._derive_focus_assets(drift_report) + ecosystem.focus_assets()
        candidate_count = len(queries)
        queries = self._rank_queries(
            queries, focus_assets, graph_intelligence=graph_intelligence or {}
        )
        selected_queries = queries[: self.config.max_queries]
        focus_tokens = {str(asset).lower() for asset in focus_assets if asset}
        focus_hits = 0
        for query in selected_queries:
            query.setdefault("ecosystem_focus_assets", ecosystem.focus_assets()[:10])
            sql = str(query.get("query_text") or "").lower()
            query["priority_score"] = round(float(query.get("priority_score") or 0.0), 2)
            query["focus_hit"] = (
                any(token in sql for token in focus_tokens) if focus_tokens else False
            )
            if query["focus_hit"]:
                focus_hits += 1
        deferred = max(0, candidate_count - len(selected_queries))
        self.last_budget_summary = {
            "candidate_queries": candidate_count,
            "selected_queries": len(selected_queries),
            "deferred_queries": deferred,
            "focus_hit_queries": focus_hits,
            "focus_hit_rate": round((focus_hits / len(selected_queries)) * 100.0, 1)
            if selected_queries
            else 0.0,
            "compute_saved_pct": round((deferred / candidate_count) * 100.0, 1)
            if candidate_count
            else 0.0,
            "scoping_mode": "risk-ranked",
        }
        queries = selected_queries

        if not queries:
            log.warning(
                "Wind Tunnel: no queries available. Pass graph_json, provide query files, or set provided_queries."
            )
        else:
            log.info(
                "Wind Tunnel: %s queries ready for replay (dialect=%s, source=%s)",
                len(queries),
                self.dialect,
                self.config.query_source,
            )
        return queries

    def _from_openlineage_and_looker(self, ecosystem: EcosystemContext) -> list[dict]:
        queries: list[dict] = []
        for asset in ecosystem.openlineage.focus_assets()[:10]:
            if asset:
                queries.append(
                    {
                        "query_id": f"OL_{hashlib.md5(asset.encode()).hexdigest()[:8]}",
                        "query_text": f"SELECT * FROM {self._ident(asset)} LIMIT 50",
                        "rows": 50,
                        "source": "openlineage.synthetic",
                    }
                )
        for asset in ecosystem.looker.impacted_assets()[:10]:
            if asset:
                queries.append(
                    {
                        "query_id": f"LOOKER_{hashlib.md5(asset.encode()).hexdigest()[:8]}",
                        "query_text": f"SELECT COUNT(*) FROM {self._ident(asset)}",
                        "rows": 1,
                        "source": "looker.synthetic",
                    }
                )
        return queries

    def _from_query_files(self) -> list[dict]:
        paths: list[Path] = []
        for file_path in self.config.query_files:
            p = Path(file_path)
            if p.exists() and p.is_file():
                paths.append(p)
        for directory in self.config.query_directories:
            d = Path(directory)
            if d.exists() and d.is_dir():
                paths.extend(sorted(d.rglob("*.sql")))

        results: list[dict] = []
        for path in paths:
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                content = path.read_text(encoding="latin-1")
            for idx, stmt in enumerate(self._split_sql(content)):
                results.append(
                    {
                        "query_id": f"FILE_{path.stem}_{idx:03d}",
                        "query_text": stmt,
                        "rows": 0,
                        "source": str(path),
                    }
                )
        return results

    def _from_history_files(self) -> list[dict]:
        records: list[dict] = []
        for raw_path in self.config.workload_history_files:
            path = Path(raw_path)
            if not path.exists() or not path.is_file():
                continue
            suffix = path.suffix.lower()
            try:
                if suffix == ".jsonl":
                    for idx, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
                        line = line.strip()
                        if not line:
                            continue
                        payload = json.loads(line)
                        sql = (
                            payload.get("query")
                            or payload.get("query_text")
                            or payload.get("sql")
                            or payload.get("statement")
                        )
                        if not sql:
                            continue
                        records.append(
                            {
                                "query_id": str(
                                    payload.get("query_id")
                                    or payload.get("id")
                                    or f"HIST_{path.stem}_{idx:04d}"
                                ),
                                "query_text": str(sql),
                                "rows": int(payload.get("rows") or payload.get("row_count") or 0),
                                "source": f"history:{path.name}",
                            }
                        )
                elif suffix == ".csv":
                    import csv

                    with path.open("r", encoding="utf-8", newline="") as handle:
                        reader = csv.DictReader(handle)
                        for idx, row in enumerate(reader):
                            sql = (
                                row.get("query")
                                or row.get("query_text")
                                or row.get("sql")
                                or row.get("statement")
                            )
                            if not sql:
                                continue
                            records.append(
                                {
                                    "query_id": str(
                                        row.get("query_id")
                                        or row.get("id")
                                        or f"HIST_{path.stem}_{idx:04d}"
                                    ),
                                    "query_text": str(sql),
                                    "rows": int(row.get("rows") or row.get("row_count") or 0),
                                    "source": f"history:{path.name}",
                                }
                            )
                else:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(payload, dict):
                        payload = (
                            payload.get("queries")
                            or payload.get("items")
                            or payload.get("records")
                            or []
                        )
                    if not isinstance(payload, list):
                        continue
                    for idx, row in enumerate(payload):
                        if not isinstance(row, dict):
                            continue
                        sql = (
                            row.get("query")
                            or row.get("query_text")
                            or row.get("sql")
                            or row.get("statement")
                        )
                        if not sql:
                            continue
                        records.append(
                            {
                                "query_id": str(
                                    row.get("query_id")
                                    or row.get("id")
                                    or f"HIST_{path.stem}_{idx:04d}"
                                ),
                                "query_text": str(sql),
                                "rows": int(row.get("rows") or row.get("row_count") or 0),
                                "source": f"history:{path.name}",
                            }
                        )
            except Exception as exc:
                log.debug("Failed to parse workload history %s: %s", path, exc)
        return records

    def _from_dbt_artifacts(self) -> list[dict]:
        manifest_path = (
            Path(self.config.dbt_manifest_path) if self.config.dbt_manifest_path else None
        )
        run_results_path = (
            Path(self.config.dbt_run_results_path) if self.config.dbt_run_results_path else None
        )
        queries: list[dict] = []
        ecosystem = EcosystemContext.load(
            dbt_manifest_path=self.config.dbt_manifest_path,
            dbt_catalog_path=self.config.dbt_catalog_path,
            dbt_run_results_path=self.config.dbt_run_results_path,
            dbt_sources_path=self.config.dbt_sources_path,
            openlineage_paths=list(self.config.openlineage_paths),
            airflow_paths=list(self.config.airflow_paths),
            dagster_paths=list(self.config.dagster_paths),
            looker_paths=list(self.config.looker_paths),
            montecarlo_paths=list(self.config.montecarlo_paths),
        )

        manifest = {}
        if manifest_path and manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception as exc:
                log.debug("Failed to parse dbt manifest %s: %s", manifest_path, exc)

        run_results = {}
        if run_results_path and run_results_path.exists():
            try:
                run_results = json.loads(run_results_path.read_text(encoding="utf-8"))
            except Exception as exc:
                log.debug("Failed to parse dbt run_results %s: %s", run_results_path, exc)

        favoured_ids: set[str] = set()
        if isinstance(run_results, dict):
            for idx, row in enumerate(run_results.get("results", []) or []):
                if isinstance(row, dict) and row.get("unique_id"):
                    favoured_ids.add(str(row["unique_id"]))

        nodes = {}
        if isinstance(manifest, dict):
            nodes.update(manifest.get("nodes", {}) or {})
            nodes.update(manifest.get("sources", {}) or {})

        for unique_id, node in nodes.items():
            if not isinstance(node, dict):
                continue
            resource_type = str(node.get("resource_type") or "")
            if resource_type and resource_type not in {
                "model",
                "analysis",
                "snapshot",
                "seed",
                "source",
            }:
                continue
            sql = (
                node.get("compiled_sql")
                or node.get("compiled_code")
                or node.get("raw_sql")
                or node.get("raw_code")
            )
            if not sql:
                continue
            source = "dbt.manifest.hot" if unique_id in favoured_ids else "dbt.manifest"
            queries.append(
                {
                    "query_id": str(unique_id),
                    "query_text": str(sql),
                    "rows": 0,
                    "source": source,
                }
            )
        return queries

    def _from_pg_stat(self) -> list[dict]:
        from sqlalchemy import text

        sql = """
            SELECT COALESCE(queryid::text, md5(query)) AS query_id,
                   query,
                   calls,
                   rows
            FROM pg_stat_statements
            WHERE query ILIKE 'SELECT%'
              AND calls > 0
              AND query NOT ILIKE '%pg_stat_statements%'
              AND query NOT ILIKE '%information_schema%'
              AND query NOT ILIKE '%pg_catalog%'
            ORDER BY calls DESC, total_exec_time DESC NULLS LAST
            LIMIT :lim
        """
        try:
            with self.engine.connect() as conn:
                rows = conn.execute(text(sql), {"lim": self.config.max_queries * 3}).fetchall()
        except Exception as exc:
            log.debug("pg_stat_statements unavailable: %s", exc)
            return []
        return [
            {
                "query_id": str(row[0]),
                "query_text": row[1],
                "rows": row[3] or 0,
                "source": "pg_stat_statements",
            }
            for row in rows
        ]

    def _from_snowflake_account_usage(self) -> list[dict]:
        from sqlalchemy import text

        sql = f"""
            SELECT QUERY_ID, QUERY_TEXT, COALESCE(ROWS_PRODUCED, 0) AS ROWS_PRODUCED
            FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY
            WHERE START_TIME >= DATEADD('day', -{self.config.lookback_days}, CURRENT_TIMESTAMP())
              AND EXECUTION_STATUS = 'SUCCESS'
              AND QUERY_TYPE = 'SELECT'
              AND QUERY_TEXT IS NOT NULL
              AND QUERY_TEXT NOT ILIKE '%ACCOUNT_USAGE%'
            ORDER BY START_TIME DESC
            LIMIT {max(self.config.max_queries * 3, 50)}
        """
        try:
            with self.engine.connect() as conn:
                rows = conn.execute(text(sql)).fetchall()
        except Exception as exc:
            log.debug("Snowflake QUERY_HISTORY unavailable: %s", exc)
            return []
        return [
            {
                "query_id": str(row[0]),
                "query_text": row[1],
                "rows": row[2] or 0,
                "source": "snowflake.query_history",
            }
            for row in rows
        ]

    def _from_bigquery_jobs(self) -> list[dict]:
        from sqlalchemy import text

        sql = f"""
            SELECT job_id, query, COALESCE(total_bytes_processed, 0) AS bytes_processed
            FROM `{region}`.INFORMATION_SCHEMA.JOBS_BY_PROJECT
            WHERE creation_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {max(self.config.lookback_days, 1)} DAY)
              AND state = 'DONE'
              AND job_type = 'QUERY'
              AND error_result IS NULL
              AND STARTS_WITH(UPPER(TRIM(query)), 'SELECT')
            ORDER BY creation_time DESC
            LIMIT {max(self.config.max_queries * 3, 50)}
        """
        try:
            with self.engine.connect() as conn:
                rows = conn.execute(text(sql)).fetchall()
        except Exception as exc:
            log.debug("BigQuery JOBS history unavailable: %s", exc)
            return []
        return [
            {
                "query_id": str(row[0]),
                "query_text": row[1],
                "rows": 0,
                "source": "bigquery.jobs",
            }
            for row in rows
        ]

    def _from_databricks_system_history(self) -> list[dict]:
        if self.engine is None:
            return []
        table = self.config.databricks_query_history_table or "system.query.history"
        sql = f"""
            SELECT
                statement_id,
                statement_text,
                total_duration_ms
            FROM {table}
            WHERE statement_text IS NOT NULL
              AND LOWER(statement_text) LIKE 'select%'
              AND start_time >= current_timestamp() - INTERVAL {int(self.config.lookback_days)} DAYS
            ORDER BY start_time DESC
            LIMIT {int(self.config.max_queries) * 4}
        """
        queries: list[dict] = []
        try:
            from sqlalchemy import text

            with self.engine.connect() as conn:
                rows = conn.execute(text(sql)).fetchall()
            for row in rows:
                payload = CloneManager._row_to_mapping(row)
                stmt = str(payload.get("statement_text") or "").strip()
                if not stmt or not re.match(r"^\s*(SELECT|WITH)\b", stmt, re.IGNORECASE):
                    continue
                query_id = str(payload.get("statement_id") or f"DBX_{len(queries):04d}")
                duration = float(payload.get("total_duration_ms") or 0.0)
                queries.append(
                    {
                        "query_id": query_id,
                        "query_text": stmt,
                        "rows": 0,
                        "source": "databricks.history",
                        "duration_ms": duration,
                    }
                )
        except Exception as exc:
            log.info("Wind Tunnel: Databricks query history unavailable: %s", exc)
        return queries

    def _synthetic(
        self,
        graph_json: Optional[dict],
        drift_report: Optional[dict] = None,
    ) -> list[dict]:
        queries: list[dict] = []
        ecosystem = EcosystemContext.load(
            dbt_manifest_path=self.config.dbt_manifest_path,
            dbt_catalog_path=self.config.dbt_catalog_path,
            dbt_run_results_path=self.config.dbt_run_results_path,
            dbt_sources_path=self.config.dbt_sources_path,
            openlineage_paths=list(self.config.openlineage_paths),
            airflow_paths=list(self.config.airflow_paths),
            dagster_paths=list(self.config.dagster_paths),
            looker_paths=list(self.config.looker_paths),
            montecarlo_paths=list(self.config.montecarlo_paths),
        )
        if not graph_json:
            return queries

        nodes = graph_json.get("nodes", [])
        edges = graph_json.get("edges", [])
        tables = [n["id"] for n in nodes if n.get("label") == "Table"]
        col_map: dict[str, list[dict]] = {}
        fk_edges: list[dict] = []
        for n in nodes:
            if n.get("label") == "Column":
                col_map.setdefault(n.get("table", ""), []).append(n)
        for edge in edges:
            if edge.get("relation") == "REFERENCES":
                fk_edges.append(edge)

        focus_assets = self._derive_focus_assets(drift_report)
        focus_tables = {asset.split(".")[0] for asset in focus_assets if asset}
        ordered_tables = [t for t in tables if t in focus_tables] + [
            t for t in tables if t not in focus_tables
        ]

        def qid(sql: str) -> str:
            return "SYN_" + hashlib.md5(sql.encode()).hexdigest()[:8]

        def q(sql: str, rows: int = 100) -> dict:
            return {"query_id": qid(sql), "query_text": sql, "rows": rows, "source": "synthetic"}

        for table in ordered_tables[:40]:
            cols = sorted(
                col_map.get(table, []),
                key=lambda c: (
                    f"{table}.{c.get('name', '')}" not in focus_assets,
                    not c.get("is_primary_key", False),
                    -float(c.get("cardinality", 0.0) or 0.0),
                    c.get("name", ""),
                ),
            )
            if not cols:
                continue

            queries.append(q(f"SELECT * FROM {self._ident(table)} LIMIT 100"))
            queries.append(q(f"SELECT COUNT(*) AS row_count FROM {self._ident(table)}", 1))

            numeric_cols = [
                c
                for c in cols
                if c.get("dtype") in {"INTEGER", "FLOAT", "NUMERIC", "DECIMAL", "REAL", "DOUBLE"}
            ]
            temporal_cols = [
                c for c in cols if c.get("dtype") in {"TIMESTAMP", "DATE", "DATETIME", "TIME"}
            ]
            categorical_cols = [
                c for c in cols if c.get("dtype") in {"VARCHAR", "TEXT", "CHAR", "STRING"}
            ]

            for col in cols[: min(6, len(cols))]:
                name = col["name"]
                queries.append(
                    q(f"SELECT {self._ident(name)} FROM {self._ident(table)} LIMIT 50", 50)
                )
                queries.append(
                    q(
                        f"SELECT COUNT(*) AS total_rows, COUNT({self._ident(name)}) AS non_null_rows "
                        f"FROM {self._ident(table)}",
                        1,
                    )
                )

            for col in categorical_cols[:2]:
                name = col["name"]
                queries.append(
                    q(
                        f"SELECT {self._ident(name)}, COUNT(*) AS rows "
                        f"FROM {self._ident(table)} "
                        f"GROUP BY {self._ident(name)} "
                        f"ORDER BY rows DESC LIMIT 20",
                        20,
                    )
                )

            for col in numeric_cols[:2]:
                name = col["name"]
                queries.append(
                    q(
                        f"SELECT COUNT(*) AS rows, AVG({self._ident(name)}) AS avg_value, "
                        f"MIN({self._ident(name)}) AS min_value, MAX({self._ident(name)}) AS max_value "
                        f"FROM {self._ident(table)}",
                        1,
                    )
                )

            for col in temporal_cols[:1]:
                name = col["name"]
                queries.append(
                    q(
                        f"SELECT {self._ident(name)}, COUNT(*) AS rows "
                        f"FROM {self._ident(table)} "
                        f"GROUP BY {self._ident(name)} "
                        f"ORDER BY {self._ident(name)} DESC LIMIT 20",
                        20,
                    )
                )

        for edge in fk_edges[:40]:
            src_parts = edge.get("source", "").split(".")
            tgt_parts = edge.get("target", "").split(".")
            if len(src_parts) != 2 or len(tgt_parts) != 2:
                continue
            src_table, src_col = src_parts
            tgt_table, tgt_col = tgt_parts
            queries.append(
                q(
                    f"SELECT a.*, b.* FROM {self._ident(src_table)} a "
                    f"LEFT JOIN {self._ident(tgt_table)} b "
                    f"ON a.{self._ident(src_col)} = b.{self._ident(tgt_col)} LIMIT 50",
                    50,
                )
            )
            queries.append(
                q(
                    f"SELECT COUNT(*) AS orphan_rows FROM {self._ident(src_table)} a "
                    f"LEFT JOIN {self._ident(tgt_table)} b "
                    f"ON a.{self._ident(src_col)} = b.{self._ident(tgt_col)} "
                    f"WHERE b.{self._ident(tgt_col)} IS NULL",
                    1,
                )
            )

        deduped = self._sanitise(queries)
        log.info("Wind Tunnel: %s synthetic queries generated", len(deduped))
        return deduped

    def _synthetic_future(
        self,
        graph_json: Optional[dict],
        drift_report: Optional[dict] = None,
    ) -> list[dict]:
        if not graph_json or not drift_report:
            return []
        nodes = graph_json.get("nodes", [])
        node_by_id = {str(n.get("id")): n for n in nodes}
        by_table: dict[str, list[dict]] = {}
        for node in nodes:
            if node.get("label") == "Column":
                by_table.setdefault(
                    str(node.get("table") or str(node.get("id", "")).split(".")[0]), []
                ).append(node)
        queries: list[dict] = []
        ecosystem = EcosystemContext.load(
            dbt_manifest_path=self.config.dbt_manifest_path,
            dbt_catalog_path=self.config.dbt_catalog_path,
            dbt_run_results_path=self.config.dbt_run_results_path,
            dbt_sources_path=self.config.dbt_sources_path,
            openlineage_paths=list(self.config.openlineage_paths),
            airflow_paths=list(self.config.airflow_paths),
            dagster_paths=list(self.config.dagster_paths),
            looker_paths=list(self.config.looker_paths),
            montecarlo_paths=list(self.config.montecarlo_paths),
        )

        def q(sql: str, rows: int = 50, kind: str = "future") -> dict:
            return {
                "query_id": "FUT_" + hashlib.md5(sql.encode()).hexdigest()[:8],
                "query_text": sql,
                "rows": rows,
                "source": f"synthetic.{kind}",
            }

        regime_names = (
            list(self.config.regime_names) if self.config.regime_switching_enabled else []
        )
        for event in drift_report.get("events", [])[
            : max(1, self.config.synthetic_future_max_queries)
        ]:
            node_id = str(event.get("node_id") or "")
            if not node_id:
                continue
            table = node_id.split(".")[0]
            col = (
                node_id.split(".")[-1]
                if "." in node_id
                else str(
                    (event.get("after") or {}).get("name")
                    or (event.get("before") or {}).get("name")
                    or ""
                )
            )
            cols = by_table.get(table, [])
            temporal = [
                c
                for c in cols
                if any(
                    tok in str(c.get("name", "")).lower() for tok in ("date", "time", "_at", "ts")
                )
            ]
            metrics = [
                c
                for c in cols
                if any(
                    tok in str(c.get("name", "")).lower()
                    for tok in ("amount", "revenue", "price", "total", "cost")
                )
            ]
            statuses = [
                c
                for c in cols
                if any(
                    tok in str(c.get("name", "")).lower()
                    for tok in ("status", "state", "type", "category")
                )
            ]

            if col:
                queries.append(
                    q(
                        f"SELECT COUNT({self._ident(col)}) AS non_null_rows, COUNT(*) AS total_rows FROM {self._ident(table)}",
                        1,
                        "future.contract",
                    )
                )
                queries.append(
                    q(
                        f"SELECT COUNT(DISTINCT {self._ident(col)}) AS distinct_values FROM {self._ident(table)}",
                        1,
                        "future.contract",
                    )
                )
                if "quarter_end" in regime_names:
                    queries.append(
                        q(
                            f"SELECT {self._ident(col)}, COUNT(*) AS rows FROM {self._ident(table)} GROUP BY {self._ident(col)} ORDER BY rows DESC LIMIT 20",
                            20,
                            "regime.quarter_end",
                        )
                    )
            if temporal:
                tcol = temporal[0]["name"]
                queries.append(
                    q(
                        f"SELECT DATE({self._ident(tcol)}) AS bucket_day, COUNT(*) AS rows FROM {self._ident(table)} "
                        f"GROUP BY DATE({self._ident(tcol)}) ORDER BY bucket_day DESC LIMIT 30",
                        30,
                        "future.temporal",
                    )
                )
                queries.append(
                    q(
                        f"SELECT COUNT(*) AS trailing_rows FROM {self._ident(table)} WHERE {self._ident(tcol)} >= DATE('now', '-7 day')",
                        1,
                        "future.temporal",
                    )
                )
                if "backfill_window" in regime_names:
                    queries.append(
                        q(
                            f"SELECT DATE({self._ident(tcol)}) AS bucket_day, COUNT(*) AS rows FROM {self._ident(table)} WHERE {self._ident(tcol)} IS NOT NULL GROUP BY DATE({self._ident(tcol)}) ORDER BY bucket_day ASC LIMIT 90",
                            90,
                            "regime.backfill_window",
                        )
                    )
            if metrics and temporal:
                mcol = metrics[0]["name"]
                tcol = temporal[0]["name"]
                queries.append(
                    q(
                        f"SELECT DATE({self._ident(tcol)}) AS bucket_day, SUM({self._ident(mcol)}) AS metric_sum FROM {self._ident(table)} "
                        f"GROUP BY DATE({self._ident(tcol)}) ORDER BY bucket_day DESC LIMIT 14",
                        14,
                        "future.metric",
                    )
                )
            elif metrics:
                mcol = metrics[0]["name"]
                queries.append(
                    q(
                        f"SELECT SUM({self._ident(mcol)}) AS metric_sum, AVG({self._ident(mcol)}) AS metric_avg FROM {self._ident(table)}",
                        1,
                        "future.metric",
                    )
                )
            if statuses and metrics:
                scol = statuses[0]["name"]
                mcol = metrics[0]["name"]
                queries.append(
                    q(
                        f"SELECT {self._ident(scol)}, SUM({self._ident(mcol)}) AS metric_sum FROM {self._ident(table)} "
                        f"GROUP BY {self._ident(scol)} ORDER BY metric_sum DESC LIMIT 20",
                        20,
                        "future.domain",
                    )
                )
            for edge in graph_json.get("edges", []):
                if edge.get("relation") != "REFERENCES":
                    continue
                src = str(edge.get("source") or "")
                tgt = str(edge.get("target") or "")
                if not src.startswith(table + ".") and not tgt.startswith(table + "."):
                    continue
                try:
                    src_table, src_col = src.split(".", 1)
                    tgt_table, tgt_col = tgt.split(".", 1)
                except ValueError:
                    continue
                queries.append(
                    q(
                        f"SELECT COUNT(*) AS orphan_rows FROM {self._ident(src_table)} a LEFT JOIN {self._ident(tgt_table)} b "
                        f"ON a.{self._ident(src_col)} = b.{self._ident(tgt_col)} WHERE b.{self._ident(tgt_col)} IS NULL",
                        1,
                        "future.join",
                    )
                )
        deduped = self._sanitise(queries)
        ranked = self._rank_queries(deduped, self._derive_focus_assets(drift_report))
        return ranked[: self.config.synthetic_future_max_queries]

    def _derive_focus_assets(self, drift_report: Optional[dict]) -> list[str]:
        assets = list(self.config.focus_assets)
        if self.config.focus_changed_assets and drift_report:
            for event in drift_report.get("events", []):
                node_id = event.get("node_id", "")
                if node_id:
                    assets.append(node_id)
                    assets.append(node_id.split(".")[0])
        return list(dict.fromkeys(a for a in assets if a))

    def _rank_queries(
        self,
        queries: list[dict],
        focus_assets: list[str],
        graph_intelligence: Optional[dict] = None,
    ) -> list[dict]:
        if not queries:
            return []
        focus_tokens = {asset.lower() for asset in focus_assets}
        focus_tables = {asset.split(".")[0].lower() for asset in focus_assets if "." in asset}
        graph_nodes = {
            str(item.get("node_id", "")).lower(): float(item.get("score", 0.0) or 0.0)
            for item in (graph_intelligence or {}).get("top_nodes", [])
        }
        graph_tables = {
            node.split(".")[0]: score for node, score in graph_nodes.items() if "." in node
        }

        def score(query: dict) -> tuple[float, int, int, int, int, int, str]:
            sql = re.sub(r"\s+", " ", (query.get("query_text") or "").lower())
            asset_hits = sum(1 for token in focus_tokens if token and token in sql)
            table_hits = sum(1 for token in focus_tables if token and token in sql)
            join_bonus = 1 if " join " in sql else 0
            agg_bonus = (
                1 if any(k in sql for k in (" count(", " avg(", " sum(", " group by ")) else 0
            )
            source = str(query.get("source") or "")
            source_bonus = 0
            if (
                source.startswith("pg_stat")
                or source.startswith("snowflake")
                or source.startswith("databricks")
                or source.startswith("bigquery")
            ):
                source_bonus = 3
            elif source.startswith("history:"):
                source_bonus = 2
            elif source.startswith("dbt.") or source.startswith("provided"):
                source_bonus = 1
            calls_bonus = min(
                3,
                int(
                    (query.get("calls") or 0)
                    and 1 + ((query.get("calls") or 0) > 10) + ((query.get("calls") or 0) > 100)
                ),
            )
            graph_bonus = 0.0
            for token, value in graph_nodes.items():
                if token and token in sql:
                    graph_bonus = max(graph_bonus, value)
            for token, value in graph_tables.items():
                if token and token in sql:
                    graph_bonus = max(graph_bonus, value * 0.8)
            query["priority_score"] = round(
                (graph_bonus * 100.0)
                + asset_hits * 18
                + table_hits * 10
                + join_bonus * 6
                + (agg_bonus + source_bonus) * 5
                + calls_bonus * 4,
                2,
            )
            return (
                graph_bonus,
                asset_hits,
                table_hits,
                join_bonus,
                agg_bonus + source_bonus,
                calls_bonus,
                sql,
            )

        return sorted(queries, key=score, reverse=True)

    def _sanitise(self, queries: list[dict]) -> list[dict]:
        seen: set[str] = set()
        result: list[dict] = []
        skip = re.compile(
            r"^\s*(CREATE|DROP|ALTER|INSERT|UPDATE|DELETE|MERGE|TRUNCATE|GRANT|"
            r"REVOKE|USE|SHOW|DESC|DESCRIBE|EXPLAIN|SET|CALL|EXECUTE)",
            re.IGNORECASE,
        )
        for query in queries:
            txt = self._strip_comments((query.get("query_text") or "").strip())
            if not txt:
                continue
            if "{{" in txt or "{%" in txt:
                continue
            if skip.match(txt):
                continue
            if not re.match(r"^\s*(SELECT|WITH)\b", txt, re.IGNORECASE):
                continue
            key = re.sub(r"\s+", " ", txt).lower().rstrip(";")
            if key in seen:
                continue
            seen.add(key)
            clone = dict(query)
            clone["query_text"] = txt.rstrip(";")
            intel = self._extract_query_assets(txt)
            clone["tables"] = sorted(intel["tables"])
            clone["columns"] = sorted(intel["columns"])
            clone["assets"] = sorted(intel["assets"])
            clone["join_count"] = intel["join_count"]
            clone["is_aggregate"] = intel["is_aggregate"]
            result.append(clone)
        return result

    @staticmethod
    def _extract_query_assets(sql: str) -> dict[str, Any]:
        compact = QueryExtractor._strip_comments(sql)
        tables: set[str] = set()
        columns: set[str] = set()
        assets: set[str] = set()

        for token in re.findall(r'\b(?:FROM|JOIN)\s+([`"\w\.]+)', compact, re.IGNORECASE):
            ident = QueryExtractor._clean_identifier(token)
            if ident:
                tables.add(ident.split(".")[-1])
                assets.add(ident.split(".")[-1])

        for tbl, col in re.findall(r"([A-Za-z_][\w]*)\.([A-Za-z_][\w]*)", compact):
            table = QueryExtractor._clean_identifier(tbl)
            column = QueryExtractor._clean_identifier(col)
            if not table or not column:
                continue
            tables.add(table)
            columns.add(column)
            assets.add(table)
            assets.add(f"{table}.{column}")

        selected = re.search(r"^\s*SELECT\s+(.*?)\s+FROM\s", compact, re.IGNORECASE)
        if selected:
            for token in re.split(r",\s*", selected.group(1)):
                for col in re.findall(r"([A-Za-z_][\w]*)", token):
                    col_l = col.lower()
                    if col_l in {
                        "select",
                        "as",
                        "distinct",
                        "count",
                        "sum",
                        "avg",
                        "min",
                        "max",
                        "case",
                        "when",
                        "then",
                        "else",
                        "end",
                    }:
                        continue
                    if col_l in tables:
                        continue
                    columns.add(col)

        for col in re.findall(
            r"\b(?:WHERE|AND|OR|GROUP\s+BY|ORDER\s+BY|PARTITION\s+BY|ON)\b[^;]*?([A-Za-z_][\w]*)",
            compact,
            re.IGNORECASE,
        ):
            col_l = col.lower()
            if col_l not in {"select", "from", "join", "and", "or", "on", "by", "asc", "desc"}:
                columns.add(col)

        assets.update(columns)
        return {
            "tables": tables,
            "columns": columns,
            "assets": assets,
            "join_count": len(re.findall(r"\bJOIN\b", compact, re.IGNORECASE)),
            "is_aggregate": bool(
                re.search(r"\b(COUNT|SUM|AVG|MIN|MAX)\s*\(", compact, re.IGNORECASE)
                or re.search(r"\bGROUP\s+BY\b", compact, re.IGNORECASE)
            ),
        }

    @staticmethod
    def _clean_identifier(value: str) -> str:
        cleaned = value.strip().strip('`"')
        cleaned = cleaned.split()[-1]
        if "." in cleaned:
            cleaned = cleaned.split(".")[-1]
        return cleaned

    @staticmethod
    def _split_sql(content: str) -> list[str]:
        return [
            segment.strip() for segment in re.split(r";\s*(?:\n|$)", content) if segment.strip()
        ]

    @staticmethod
    def _strip_comments(sql: str) -> str:
        sql = re.sub(r"--.*?$", "", sql, flags=re.M)
        sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.S)
        return re.sub(r"\s+", " ", sql).strip()

    def _ident(self, value: str) -> str:
        if self.dialect == "bigquery":
            return f"`{value}`"
        return f'"{value}"'


class QueryReplayer:
    """Replay queries against original and cloned environments and compare behavior."""

    def __init__(self, config: WindTunnelConfig) -> None:
        self.config = config

    def replay(
        self,
        queries: list[dict],
        orig_engine: Any,
        clone_engine: Any,
    ) -> list[QueryResult]:
        results: list[QueryResult] = []
        for i, query in enumerate(queries):
            qid = query.get("query_id", f"Q{i:04d}")
            qtext = query.get("query_text", "")
            qhash = hashlib.md5(qtext.encode()).hexdigest()[:8]
            results.append(self._replay_one(qid, qtext, qhash, orig_engine, clone_engine))
            if (i + 1) % 50 == 0:
                passed = sum(1 for item in results if item.status == QueryStatus.PASSED)
                log.info("  Wind Tunnel progress: %s/%s (%s passed)", i + 1, len(queries), passed)
        return results

    def _replay_one(
        self,
        qid: str,
        qtext: str,
        qhash: str,
        orig_engine: Any,
        clone_engine: Any,
    ) -> QueryResult:
        result = QueryResult(query_id=qid, query_text=qtext, query_hash=qhash)
        started = time.monotonic()

        orig = self._execute(orig_engine, qtext)
        clone = self._execute(clone_engine, qtext)

        result.original_rows = orig["row_count"]
        result.original_cols = orig["columns"]
        result.original_error = orig["error"]
        result.clone_rows = clone["row_count"]
        result.clone_cols = clone["columns"]
        result.clone_error = clone["error"]
        result.original_sample_rows = orig.get("sample_rows") or []
        result.clone_sample_rows = clone.get("sample_rows") or []
        result.duration_ms = (time.monotonic() - started) * 1000
        result._orig_fingerprint = orig["fingerprint"]
        result._clone_fingerprint = clone["fingerprint"]
        return self._classify(result)

    def _execute(self, engine: Any, qtext: str) -> dict[str, Any]:
        from sqlalchemy import text

        rewritten = self._rewrite_for_clone(engine, qtext)
        safe_sql = self._inject_limit(rewritten, self.config.row_sample_limit)
        payload: dict[str, Any] = {
            "row_count": None,
            "columns": [],
            "error": None,
            "fingerprint": None,
            "sample_rows": [],
        }
        try:
            with engine.connect() as conn:
                for stmt in getattr(engine, "_semzero_use_statements", []) or []:
                    conn.execute(text(stmt))
                if engine.dialect.name in ("postgresql", "postgres"):
                    conn.execute(
                        text(f"SET statement_timeout = '{self.config.query_timeout_s * 1000}'")
                    )
                cursor = conn.execute(text(safe_sql))
                rows = cursor.fetchall()
                payload["row_count"] = len(rows)
                payload["columns"] = list(cursor.keys())
                columns = list(cursor.keys())
                payload["sample_rows"] = [
                    {str(columns[idx]): row[idx] for idx in range(min(len(columns), len(row)))}
                    for row in rows[: min(3, len(rows))]
                ]
                if self.config.compare_value_fingerprints and self._should_compare_values(
                    qtext, len(rows)
                ):
                    preview = [tuple(row) for row in rows[: self.config.fingerprint_row_limit]]
                    payload["fingerprint"] = hashlib.sha256(
                        json.dumps(preview, default=str).encode()
                    ).hexdigest()[:16]
                return payload
        except Exception as exc:
            payload["error"] = str(exc)[:300]
            return payload

    @staticmethod
    def _rewrite_for_clone(engine: Any, sql: str) -> str:
        mapping = getattr(engine, "_semzero_clone_map", None) or {}
        if not mapping:
            return sql
        rewritten = sql
        for source, target in sorted(mapping.items(), key=lambda item: len(item[0]), reverse=True):
            if not source or not target:
                continue
            rewritten = re.sub(rf"(?<![A-Za-z0-9_]){re.escape(source)}(?=\.)", target, rewritten)
            rewritten = rewritten.replace(f"`{source}`", f"`{target}`")
            rewritten = rewritten.replace(f'"{source}"', f'"{target}"')
        return rewritten

    def _classify(self, result: QueryResult) -> QueryResult:
        orig_err = bool(result.original_error)
        clone_err = bool(result.clone_error)
        if orig_err and clone_err:
            result.status = QueryStatus.ERROR_BOTH
            return result
        if orig_err:
            result.status = QueryStatus.ERROR_ORIGINAL
            return result
        if clone_err:
            result.status = QueryStatus.BROKEN
            result.affected_cols = self._find_col_refs(result.query_text)
            return result

        orig_set = set(result.original_cols or [])
        clone_set = set(result.clone_cols or [])
        removed = orig_set - clone_set
        added = clone_set - orig_set
        if removed:
            result.status = QueryStatus.SCHEMA_CHANGED
            result.affected_cols = sorted(removed)
            return result
        if added and not self.config.allow_added_columns:
            result.status = QueryStatus.SCHEMA_CHANGED
            result.affected_cols = sorted(added)
            return result

        original_rows = result.original_rows or 0
        clone_rows = result.clone_rows or 0
        result.row_delta = clone_rows - original_rows
        if self.config.compare_row_counts and original_rows > 0:
            delta_pct = abs(result.row_delta) / max(original_rows, 1)
            if delta_pct > self.config.tolerance_pct:
                result.status = QueryStatus.ROW_MISMATCH
                result.row_diff_summary = self._build_row_diff_summary(result)
                return result

        if (
            self.config.compare_value_fingerprints
            and getattr(result, "_orig_fingerprint", None)
            and getattr(result, "_clone_fingerprint", None)
            and getattr(result, "_orig_fingerprint", None)
            != getattr(result, "_clone_fingerprint", None)
        ):
            result.status = QueryStatus.ROW_MISMATCH
            result.affected_cols = self._find_col_refs(result.query_text)
            result.row_diff_summary = self._build_row_diff_summary(result)
            return result

        result.status = QueryStatus.PASSED
        return result

    @staticmethod
    def _build_row_diff_summary(result: QueryResult) -> dict[str, Any]:
        orig = result.original_sample_rows or []
        clone = result.clone_sample_rows or []
        if not orig and not clone:
            return {}
        changed_cols: list[str] = []
        shared = set(orig[0].keys()) & set(clone[0].keys()) if orig and clone else set()
        for col in sorted(shared):
            left = [row.get(col) for row in orig]
            right = [row.get(col) for row in clone]
            if left != right:
                changed_cols.append(col)
        return {
            "original_preview_rows": len(orig),
            "clone_preview_rows": len(clone),
            "changed_columns": changed_cols[:8],
            "original_preview_hash": hashlib.md5(
                json.dumps(orig, default=str).encode()
            ).hexdigest()[:10]
            if orig
            else "",
            "clone_preview_hash": hashlib.md5(json.dumps(clone, default=str).encode()).hexdigest()[
                :10
            ]
            if clone
            else "",
        }

    @staticmethod
    def _should_compare_values(query_text: str, row_count: int) -> bool:
        sql = query_text.lower()
        if row_count == 0:
            return False
        if row_count <= 5:
            return True
        if " order by " in sql:
            return True
        if any(
            token in sql for token in (" count(", " avg(", " sum(", " min(", " max(", " group by ")
        ):
            return True
        return False

    @staticmethod
    def _inject_limit(sql: str, limit: int) -> str:
        stripped = sql.rstrip().rstrip(";")
        if limit <= 0:
            return stripped
        if re.search(r"\bLIMIT\b", stripped, re.IGNORECASE):
            return stripped
        return f"{stripped} LIMIT {limit}"

    @staticmethod
    def _find_col_refs(query_text: str) -> list[str]:
        patterns = [
            r'"?(\w+)"?\s*(?:=|IS\s+NULL|IS\s+NOT\s+NULL|LIKE|IN\s*\()',
            r'GROUP\s+BY\s+"?(\w+)"?',
        ]
        hits: list[str] = []
        for pattern in patterns:
            hits.extend(re.findall(pattern, query_text, re.IGNORECASE))
        return list(dict.fromkeys(hits))[:5]


class SemanticAnalyser:
    """Identify migration patterns that often cause production incidents."""

    def analyse(
        self,
        migration_sql: str,
        graph_json: Optional[dict] = None,
    ) -> list[SemanticRisk]:
        risks: list[SemanticRisk] = []
        statements = [
            statement.strip() for statement in migration_sql.split(";") if statement.strip()
        ]
        for statement in statements:
            risks += self._check_not_null_trap(statement)
            risks += self._check_type_narrowing(statement)
            risks += self._check_fk_drop(statement, graph_json)
            risks += self._check_data_loss(statement)
            risks += self._check_drop_table(statement)
            risks += self._check_unbounded_update(statement)
        return risks

    def _check_not_null_trap(self, stmt: str) -> list[SemanticRisk]:
        match = re.search(
            r"ADD\s+(?:COLUMN\s+)?[`\"]?(\w+)[`\"]?\s+\w+.*?NOT\s+NULL", stmt, re.IGNORECASE
        )
        if match and "DEFAULT" not in stmt.upper():
            col = match.group(1)
            return [
                SemanticRisk(
                    risk_type="NOT_NULL_TRAP",
                    severity="CRITICAL",
                    column=col,
                    description=f"Column `{col}` added as NOT NULL with no DEFAULT. Existing INSERT paths usually fail immediately.",
                    suggestion=f"Roll out `{col}` as nullable first or add a DEFAULT before enforcing NOT NULL.",
                )
            ]
        if re.search(r"SET\s+NOT\s+NULL", stmt, re.IGNORECASE):
            col_match = re.search(r"ALTER\s+(?:COLUMN\s+)?[`\"]?(\w+)[`\"]?", stmt, re.IGNORECASE)
            col = col_match.group(1) if col_match else "unknown"
            return [
                SemanticRisk(
                    risk_type="NOT_NULL_TRAP",
                    severity="HIGH",
                    column=col,
                    description=f"Column `{col}` is becoming NOT NULL. Any existing NULL rows will make the migration fail.",
                    suggestion=f"Check and backfill NULLs before adding the NOT NULL constraint on `{col}`.",
                )
            ]
        return []

    def _check_type_narrowing(self, stmt: str) -> list[SemanticRisk]:
        match = re.search(
            r"ALTER\s+(?:COLUMN\s+)?[`\"]?(\w+)[`\"]?\s+(?:TYPE|SET\s+DATA\s+TYPE)\s+(\w+(?:\(\d+(?:,\d+)?\))?)",
            stmt,
            re.IGNORECASE,
        )
        if not match:
            return []
        col = match.group(1)
        new_type = match.group(2).upper()
        risky_prefixes = ("SMALLINT", "INTEGER", "INT", "DATE", "BOOLEAN", "CHAR", "VARCHAR(")
        if new_type.startswith(risky_prefixes):
            return [
                SemanticRisk(
                    risk_type="TYPE_NARROWING",
                    severity="HIGH",
                    column=col,
                    description=f"Column `{col}` is being narrowed/cast to `{new_type}`. Existing values can truncate or fail cast validation.",
                    suggestion=f"Backfill into a shadow column and validate casts before changing `{col}` to `{new_type}`.",
                )
            ]
        return []

    def _check_fk_drop(self, stmt: str, graph_json: Optional[dict]) -> list[SemanticRisk]:
        if not graph_json:
            return []
        match = re.search(r"DROP\s+(?:COLUMN\s+)?[`\"]?(\w+)[`\"]?", stmt, re.IGNORECASE)
        if not match:
            return []
        col_name = match.group(1)
        refs = [
            edge
            for edge in graph_json.get("edges", [])
            if edge.get("relation") == "REFERENCES"
            and edge.get("target", "").endswith(f".{col_name}")
        ]
        if refs:
            return [
                SemanticRisk(
                    risk_type="FK_DROP",
                    severity="CRITICAL",
                    column=col_name,
                    description=f"Column `{col_name}` is referenced by {len(refs)} foreign-key path(s). Dropping it will cascade into downstream joins.",
                    suggestion="Update or remove dependent foreign keys and joins before dropping the referenced column.",
                )
            ]
        return []

    def _check_data_loss(self, stmt: str) -> list[SemanticRisk]:
        match = re.search(r"TRUNCATE\s+(?:TABLE\s+)?[`\"]?(\w+)", stmt, re.IGNORECASE)
        if match:
            table = match.group(1)
            return [
                SemanticRisk(
                    risk_type="DATA_LOSS",
                    severity="CRITICAL",
                    column=table,
                    description=f"TRUNCATE TABLE `{table}` permanently removes all rows in one step.",
                    suggestion=f"Archive `{table}` before truncating it, or replace TRUNCATE with an explicit staged backfill plan.",
                )
            ]
        return []

    def _check_drop_table(self, stmt: str) -> list[SemanticRisk]:
        match = re.search(
            r"DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?[`\"]?(\w+)[`\"]?", stmt, re.IGNORECASE
        )
        if not match:
            return []
        table = match.group(1)
        return [
            SemanticRisk(
                risk_type="DROP_TABLE",
                severity="CRITICAL",
                column=table,
                description=f"Dropping table `{table}` removes every downstream reader at once.",
                suggestion=f"Deprecate `{table}` behind a compatibility view or staged alias before dropping it.",
            )
        ]

    def _check_unbounded_update(self, stmt: str) -> list[SemanticRisk]:
        if re.search(r"^\s*UPDATE\b", stmt, re.IGNORECASE) and " WHERE " not in stmt.upper():
            return [
                SemanticRisk(
                    risk_type="UNBOUNDED_UPDATE",
                    severity="MEDIUM",
                    column="*",
                    description="UPDATE statement has no WHERE clause. This often rewrites every row and changes semantics unexpectedly.",
                    suggestion="Use a staged backfill with explicit predicates or guard it behind validation queries.",
                )
            ]
        return []


class PatchGenerator:
    """Generates SQL patches for broken queries where possible."""

    def generate(
        self,
        broken_queries: list[QueryResult],
        drift_report: dict,
    ) -> list[str]:
        patches: list[str] = []
        rename_map = self._build_rename_map(drift_report)

        for q in broken_queries:
            if rename_map:
                patched = q.query_text
                for old, new in rename_map.items():
                    patched = re.sub(rf"\b{re.escape(old)}\b", new, patched, flags=re.IGNORECASE)
                if patched != q.query_text:
                    patches.append(
                        f"Auto-patched `{q.query_id}`: renamed "
                        + ", ".join(f"`{o}` → `{n}`" for o, n in rename_map.items())
                    )
                    continue
            # Manual fallback
            err = (q.clone_error or "unknown error")[:80]
            patches.append(f"Manual review required for `{q.query_id}`: {err}")
        return patches

    def _build_rename_map(self, drift_report: dict) -> dict[str, str]:
        rename_map: dict[str, str] = {}
        for event in drift_report.get("events", []):
            if event.get("change_type") == "COLUMN_RENAMED":
                detail = event.get("detail", "")
                node_id = event.get("node_id", "")
                old_col = node_id.split(".")[-1] if "." in node_id else ""
                m = re.search(r"renamed to '[\w.]+\.(\w+)'", detail)
                new_col = m.group(1) if m else ""
                after = event.get("after") or {}
                new_col = new_col or after.get("name", "")
                if old_col and new_col:
                    rename_map[old_col] = new_col
        return rename_map


# ── Main engine ────────────────────────────────────────────────────────────────


class MigrationWindTunnel:
    """
    Orchestrates a complete Wind Tunnel simulation.

    Steps:
      1. Detect dialect
      2. Clone the database
      3. Apply migration to clone
      4. Extract query sample
      5. Replay queries on original vs clone
      6. Semantic risk analysis
      7. Generate patches for broken queries
      8. Compute confidence score and verdict
      9. Persist receipt and optionally post to PR
     10. Destroy clone

    Works out of the box with:
      - SQLite   (zero config, just needs a file path)
      - Postgres (needs psycopg2-binary)
      - Snowflake(needs snowflake-sqlalchemy)
    """

    def __init__(self, config: WindTunnelConfig) -> None:
        self.config = config
        self._dialect = self._detect_dialect()
        log.info(f"Wind Tunnel ready (dialect={self._dialect})")

    def run(
        self,
        migration_sql: str = "",
        drift_report: Optional[dict] = None,
        graph_json: Optional[dict] = None,
        pr_number: Optional[int] = None,
    ) -> SimulationReceipt:
        """
        Run a full Wind Tunnel simulation.

        Provide either:
          migration_sql  — raw DDL string (ALTER TABLE ...)
          drift_report   — SemZero drift format (auto-generates DDL)

        graph_json improves synthetic query generation and semantic analysis.
        """
        if not migration_sql and not drift_report:
            raise ValueError("Provide migration_sql or drift_report.")

        run_id = str(uuid.uuid4())[:8]
        summary = (
            self._summarise_sql(migration_sql)
            if migration_sql
            else self._summarise_drift(drift_report or {})
        )

        receipt = SimulationReceipt(
            run_id=run_id,
            clone_name=f"DRY_{run_id}" if self.config.dry_run else "",
            migration_summary=summary,
            db_dialect=self._dialect,
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        start = time.time()
        self._banner(run_id, summary)

        orig_engine = None
        clone_engine = None
        clone_mgr = CloneManager(self.config, self._dialect, run_id)

        try:
            # ── Build original engine ────────────────────────────────────────
            orig_engine = self._build_orig_engine()

            # ── Step 1: Semantic analysis (runs always, before any DB ops) ──
            if self.config.run_semantic_analysis:
                log.info("Wind Tunnel [1/6]: Semantic risk analysis…")
                sql_to_analyse = migration_sql or self._drift_to_sql_preview(drift_report or {})
                analyser = SemanticAnalyser()
                receipt.semantic_risks = analyser.analyse(sql_to_analyse, graph_json)
                if receipt.semantic_risks:
                    log.warning(f"  {len(receipt.semantic_risks)} semantic risk(s) detected")

            # ── Step 2: Clone ────────────────────────────────────────────────
            log.info("Wind Tunnel [2/6]: Creating database clone…")
            try:
                clone_engine = clone_mgr.create(orig_engine)
                receipt.clone_name = clone_mgr._clone_dbname or clone_mgr._clone_path or run_id
                receipt.clone_created = True
            except Exception as exc:
                receipt.error = f"Clone failed: {exc}"
                receipt.verdict = TunnelVerdict.ERROR
                return receipt

            # ── Step 3: Apply migration ──────────────────────────────────────
            if not self.config.dry_run:
                log.info("Wind Tunnel [3/6]: Applying migration to clone…")
                applicator = MigrationApplicator()
                if migration_sql:
                    err = applicator.apply_sql(clone_engine, migration_sql)
                    if err:
                        receipt.error = f"Migration failed to apply: {err}"
                        receipt.verdict = TunnelVerdict.BLOCKED
                        # Don't return — still have semantic risks to report
                        receipt.compute_confidence()
                        return receipt
                elif drift_report:
                    _, errs = applicator.apply_drift_report(
                        clone_engine, drift_report, self._dialect
                    )
                    if errs:
                        log.warning(f"  {len(errs)} migration stmt(s) failed: {errs[0]}")
                receipt.migration_applied = True
            else:
                log.info("Wind Tunnel [3/6]: dry_run — skipping migration apply")
                receipt.migration_applied = False

            # ── Step 3: Extract queries ──────────────────────────────────────
            log.info("Wind Tunnel [4/6]: Extracting query sample…")
            extractor = QueryExtractor(orig_engine, self._dialect, self.config)
            # Always pass an effective graph — fall back to drift-derived graph
            _gj = graph_json or (self._graph_from_drift(drift_report) if drift_report else None)
            graph_intelligence = (
                GraphIntelligenceEngine(
                    _gj or {"nodes": [], "edges": []},
                    enabled=self.config.graph_intelligence_enabled,
                    rgcn_model_path=self.config.rgcn_model_path,
                ).analyse(focus_node_ids=list(self.config.focus_assets))
                if _gj
                else None
            )
            queries = extractor.extract(
                _gj,
                drift_report=drift_report,
                graph_intelligence=graph_intelligence.to_dict() if graph_intelligence else None,
            )
            estimator = QueryPlanRiskEstimator(
                orig_engine, self._dialect, enabled=self.config.explain_plan_enabled
            )
            receipt.compute_cost_risk, receipt.compute_cost_notes = estimator.estimate(queries)
            receipt.plan_risk_summary = estimator.last_summary
            receipt.top_expensive_queries = estimator.last_top_queries
            receipt.finops_summary = estimator.last_finops_summary
            receipt.replay_budget_summary = getattr(extractor, "last_budget_summary", {})
            receipt.queries_replayed = len(queries)
            receipt.historical_queries_replayed = sum(
                1
                for q in queries
                if str(q.get("source", "")).startswith(
                    (
                        "pg_stat",
                        "snowflake",
                        "databricks",
                        "bigquery",
                        "history:",
                        "provided",
                        "dbt.",
                        "/",
                    )
                )
            )
            receipt.future_queries_generated = sum(
                1 for q in queries if str(q.get("source", "")).startswith("synthetic.future")
            )
            receipt.synthetic_queries_replayed = sum(
                1 for q in queries if str(q.get("source", "")).startswith("synthetic")
            )
            receipt.regime_scenarios = sorted(
                {
                    str(q.get("source", "")).split("synthetic.")[-1]
                    for q in queries
                    if str(q.get("source", "")).startswith("synthetic.regime")
                }
            )
            if queries:
                receipt.ecosystem_context = {
                    "focus_assets": list(
                        dict.fromkeys(
                            a for q in queries for a in q.get("ecosystem_focus_assets", [])
                        )
                    )[:10]
                }
            if graph_intelligence:
                receipt.graph_intelligence = graph_intelligence.to_dict()

            if not queries:
                log.warning("  No queries found — Wind Tunnel will use NO_QUERIES verdict")

            # ── Step 4: Replay ───────────────────────────────────────────────
            if queries and not self.config.dry_run:
                log.info(f"Wind Tunnel [5/6]: Replaying {len(queries)} queries…")
                replayer = QueryReplayer(self.config)
                results = replayer.replay(queries, orig_engine, clone_engine)
            else:
                results = []

            receipt.queries_passed = sum(1 for r in results if r.passed)
            receipt.queries_broken = sum(1 for r in results if r.status == QueryStatus.BROKEN)
            receipt.queries_mismatch = sum(
                1
                for r in results
                if r.status in (QueryStatus.ROW_MISMATCH, QueryStatus.SCHEMA_CHANGED)
            )
            receipt.broken_queries = [r for r in results if r.status == QueryStatus.BROKEN][:10]
            receipt.mismatch_queries = [
                r
                for r in results
                if r.status in (QueryStatus.ROW_MISMATCH, QueryStatus.SCHEMA_CHANGED)
            ][:5]
            receipt.sample_passed = [r for r in results if r.passed][:3]

            # ── Step 5: (Semantic analysis already completed in step 1) ──────

            # ── Step 6: Patches + confidence ────────────────────────────────
            log.info("Wind Tunnel [6/6]: Computing confidence + generating patches…")
            if receipt.broken_queries:
                dr = drift_report or {}
                receipt.patches_available = PatchGenerator().generate(receipt.broken_queries, dr)
            receipt.compute_confidence()

        except Exception as exc:
            log.error(f"Wind Tunnel run failed: {exc}", exc_info=True)
            receipt.error = str(exc)
            receipt.verdict = TunnelVerdict.ERROR

        finally:
            if clone_engine is not None and not self.config.dry_run:
                if self.config.auto_destroy_clone:
                    log.info("Wind Tunnel: cleaning up clone…")
                    clone_mgr.destroy(clone_engine)
                else:
                    log.info("Wind Tunnel: keeping clone for manual inspection.")
            if orig_engine is not None:
                orig_engine.dispose()

        receipt.completed_at = datetime.now(timezone.utc).isoformat()
        receipt.duration_s = round(time.time() - start, 1)
        self._log_summary(receipt)

        # Save
        Path(self.config.data_dir).mkdir(parents=True, exist_ok=True)
        receipt.save(f"{self.config.data_dir}/simulation_receipt.json")

        # Post to PR
        if self.config.post_to_pr and pr_number and self.config.github_token:
            self._post_to_pr(receipt, pr_number)

        return receipt

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _detect_dialect(self) -> str:
        url = (self.config.db_url or "").lower()
        if url.startswith("sqlite"):
            return "sqlite"
        if url.startswith("postgresql") or url.startswith("postgres"):
            return "postgresql"
        if url.startswith("snowflake"):
            return "snowflake"
        if url.startswith("databricks") or url.startswith("databricks+connector"):
            return "databricks"
        if url.startswith("mysql"):
            return "mysql"
        if url.startswith("bigquery"):
            return "bigquery"
        if self.config.snowflake_account:
            return "snowflake"
        if self.config.databricks_http_path or self.config.databricks_server_hostname:
            return "databricks"
        return "unknown"

    def _build_orig_engine(self) -> Any:
        from sqlalchemy import create_engine as ce

        if self._dialect == "snowflake" and not self.config.db_url:
            c = self.config
            url = (
                f"snowflake://{c.snowflake_user}:{c.snowflake_password}"
                f"@{c.snowflake_account}/{c.snowflake_database}/{c.snowflake_schema}"
            )
            if c.snowflake_warehouse:
                url += f"?warehouse={c.snowflake_warehouse}"
            return ce(url, pool_pre_ping=True, pool_size=2)
        if self._dialect == "databricks" and not self.config.db_url:
            c = self.config
            url = (
                f"databricks://token:{c.databricks_token}@{c.databricks_server_hostname}"
                f"?http_path={c.databricks_http_path}&catalog={c.databricks_catalog}&schema={c.databricks_schema}"
            )
            return ce(url, pool_pre_ping=True, pool_size=2)

        kw: dict = {"pool_pre_ping": True}
        if "sqlite" in self.config.db_url:
            kw["connect_args"] = {"check_same_thread": False}
        return ce(self.config.db_url, **kw)

    def _summarise_sql(self, sql: str) -> str:
        stmts = [s.strip()[:60].replace("\n", " ") for s in sql.split(";") if s.strip()]
        if not stmts:
            return "Empty migration"
        result = stmts[0]
        if len(stmts) > 1:
            result += f" (+{len(stmts) - 1} more)"
        return result

    def _summarise_drift(self, drift: dict) -> str:
        events = drift.get("events", [])
        if not events:
            return "No changes"
        types = [e.get("change_type", "").replace("_", " ") for e in events[:3]]
        suffix = f" (+{len(events) - 3} more)" if len(events) > 3 else ""
        return ", ".join(types) + suffix

    def _drift_to_sql_preview(self, drift: dict) -> str:
        lines = []
        for e in drift.get("events", [])[:5]:
            ct = e.get("change_type", "")
            node_id = e.get("node_id", "")
            after = e.get("after") or {}
            if "." in node_id:
                tbl, col = node_id.split(".", 1)
                if ct == "COLUMN_REMOVED":
                    lines.append(f'ALTER TABLE "{tbl}" DROP COLUMN "{col}";')
                elif ct == "COLUMN_ADDED":
                    dtype = after.get("dtype", "VARCHAR")
                    null = "" if after.get("nullable", True) else " NOT NULL"
                    lines.append(f'ALTER TABLE "{tbl}" ADD COLUMN "{col}" {dtype}{null};')
                elif ct == "TYPE_CHANGED":
                    lines.append(
                        f'ALTER TABLE "{tbl}" ALTER COLUMN "{col}" TYPE {after.get("dtype", "VARCHAR")};'
                    )
                elif ct == "NULLABLE_CHANGED" and not after.get("nullable", True):
                    lines.append(f'ALTER TABLE "{tbl}" ALTER COLUMN "{col}" SET NOT NULL;')
        return "\n".join(lines)

    def _post_to_pr(self, receipt: SimulationReceipt, pr_number: int) -> None:
        try:
            import requests as req

            comment = receipt.to_pr_comment()
            headers = {
                "Authorization": f"Bearer {self.config.github_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
            url = (
                f"https://api.github.com/repos/{self.config.github_repo}"
                f"/issues/{pr_number}/comments"
            )
            r = req.post(url, headers=headers, json={"body": comment}, timeout=15)
            if r.status_code == 201:
                log.info(f"Simulation receipt posted to PR #{pr_number}")
            else:
                log.warning(f"PR comment failed: {r.status_code} — {r.text[:200]}")
        except Exception as exc:
            log.error(f"Failed to post PR receipt: {exc}")

    def _graph_from_drift(self, drift_report: dict) -> dict:
        """
        Build a minimal graph_json from a drift report when no full graph
        is available. Enough to drive synthetic query generation.
        """
        nodes: list[dict] = []
        seen_tables: set[str] = set()
        for ev in drift_report.get("events", []):
            nid = ev.get("node_id", "")
            if "." not in nid:
                continue
            tbl, col = nid.split(".", 1)
            if tbl not in seen_tables:
                seen_tables.add(tbl)
                nodes.append({"id": tbl, "label": "Table", "name": tbl})
            before = ev.get("before") or {}
            nodes.append(
                {
                    "id": nid,
                    "label": "Column",
                    "name": col,
                    "table": tbl,
                    "dtype": before.get("dtype", "VARCHAR"),
                    "nullable": before.get("nullable", True),
                    "is_primary_key": False,
                }
            )
        return {"nodes": nodes, "edges": []}

    def _banner(self, run_id: str, summary: str) -> None:
        log.info("━" * 55)
        log.info(f"  SemZero Migration Wind Tunnel — Run {run_id}")
        log.info(f"  Dialect:  {self._dialect}")
        log.info(f"  Dry run:  {self.config.dry_run}")
        log.info(f"  Migration: {summary[:60]}")
        log.info(f"  Max queries: {self.config.max_queries}")
        log.info("━" * 55)

    def _log_summary(self, receipt: SimulationReceipt) -> None:
        v = receipt.verdict.value if isinstance(receipt.verdict, TunnelVerdict) else receipt.verdict
        log.info("━" * 55)
        log.info(f"  Verdict:    {v}")
        log.info(f"  Confidence: {receipt.confidence_score}%")
        log.info(f"  Replayed:   {receipt.queries_replayed}")
        log.info(f"  Passed:     {receipt.queries_passed}")
        log.info(f"  Broken:     {receipt.queries_broken}")
        log.info(f"  Mismatch:   {receipt.queries_mismatch}")
        log.info(f"  Duration:   {receipt.duration_s:.1f}s")
        if receipt.semantic_risks:
            log.info(f"  Risks:      {len(receipt.semantic_risks)} semantic risk(s)")
        log.info("━" * 55)
