"""
change_gate.py — SemZero Pre-Merge Change Gate.

The feature that makes SemZero a mandatory part of every data migration.

When a developer opens a PR touching a migration file, SemZero:
  1. Parses the migration to extract proposed schema changes
  2. Runs the Compatibility Oracle to classify each change into one of 8 types
  3. Computes blast radius against the live warehouse graph
  4. Issues a SAFE / NEEDS_REVIEW / BLOCK verdict
  5. Runs Wind Tunnel replay if enabled (clone → apply → replay queries)
  6. Posts the verdict + simulation receipt to the PR as a comment
  7. Sets a CI status check that can gate merge

Compatibility Oracle change types:
  ADDITIVE_SAFE         — new nullable column, safe always
  ADDITIVE_BREAKING     — new NOT NULL column without default, breaks INSERTs
  RENAME_HIGH_CONFIDENCE— rename with >80% structural match, small blast radius
  RENAME_LOW_CONFIDENCE — rename with uncertain mapping
  DESTRUCTIVE_DELETE    — column/table dropped, breaks consumers
  TYPE_WIDENING         — INT→BIGINT, VARCHAR→TEXT, always safe
  TYPE_NARROWING        — BIGINT→INT, VARCHAR→DATE, dangerous
  NULLABLE_HARDENING    — nullable→not null, rollout-sensitive
  SEMANTIC_BREAKING     — type valid but semantic meaning changed (cardinality collapse, etc)

Verdicts:
  SAFE         — all changes are ADDITIVE_SAFE or TYPE_WIDENING
  NEEDS_REVIEW — any RENAME, NULLABLE_HARDENING, or ADDITIVE_BREAKING
  BLOCK        — any DESTRUCTIVE_DELETE, TYPE_NARROWING, or SEMANTIC_BREAKING

Usage:
    gate   = ChangeGate(graph_json, GateConfig(github_token="...", github_repo="..."))
    result = gate.evaluate(drift_report)

    # Optionally run Wind Tunnel (requires db_url)
    if gate_config.run_wind_tunnel:
        result = gate.run_wind_tunnel(result, migration_sql="ALTER TABLE ...")

    gate.post_to_pr(result, pr_number=42)
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

from .calibration import ReliabilityCalibrationStore
from .graph_intelligence import GraphIntelligenceEngine
from .ecosystem import EcosystemContext
from .finops_gate import FinOpsChangeAnalyser
from .assumption_gate import AssumptionGate

log = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"


# ── Enums ──────────────────────────────────────────────────────────────────────


class CompatibilityType(str, Enum):
    ADDITIVE_SAFE = "ADDITIVE_SAFE"
    ADDITIVE_BREAKING = "ADDITIVE_BREAKING"
    RENAME_HIGH_CONFIDENCE = "RENAME_HIGH_CONFIDENCE"
    RENAME_LOW_CONFIDENCE = "RENAME_LOW_CONFIDENCE"
    DESTRUCTIVE_DELETE = "DESTRUCTIVE_DELETE"
    TYPE_WIDENING = "TYPE_WIDENING"
    TYPE_NARROWING = "TYPE_NARROWING"
    NULLABLE_HARDENING = "NULLABLE_HARDENING"
    DATA_REGRESSION = "DATA_REGRESSION"
    SEMANTIC_BREAKING = "SEMANTIC_BREAKING"


class Verdict(str, Enum):
    SAFE = "SAFE"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    BLOCK = "BLOCK"


_VERDICT_EMOJI = {
    Verdict.SAFE: "✅",
    Verdict.NEEDS_REVIEW: "⚠️",
    Verdict.BLOCK: "🚫",
}

_COMPAT_EMOJI = {
    CompatibilityType.ADDITIVE_SAFE: "✅",
    CompatibilityType.ADDITIVE_BREAKING: "⚠️",
    CompatibilityType.RENAME_HIGH_CONFIDENCE: "🔄",
    CompatibilityType.RENAME_LOW_CONFIDENCE: "⚠️",
    CompatibilityType.DESTRUCTIVE_DELETE: "🚫",
    CompatibilityType.TYPE_WIDENING: "✅",
    CompatibilityType.TYPE_NARROWING: "🚫",
    CompatibilityType.NULLABLE_HARDENING: "⚠️",
    CompatibilityType.DATA_REGRESSION: "⚠️",
    CompatibilityType.SEMANTIC_BREAKING: "🚫",
}

_BLOCK_TYPES = {
    CompatibilityType.DESTRUCTIVE_DELETE,
    CompatibilityType.TYPE_NARROWING,
    CompatibilityType.SEMANTIC_BREAKING,
}

_REVIEW_TYPES = {
    CompatibilityType.RENAME_HIGH_CONFIDENCE,
    CompatibilityType.RENAME_LOW_CONFIDENCE,
    CompatibilityType.ADDITIVE_BREAKING,
    CompatibilityType.NULLABLE_HARDENING,
    CompatibilityType.DATA_REGRESSION,
}

# Safe type promotions — always backwards-compatible
_TYPE_WIDENINGS = {
    ("INTEGER", "BIGINT"),
    ("INT", "BIGINT"),
    ("SMALLINT", "INTEGER"),
    ("SMALLINT", "BIGINT"),
    ("VARCHAR", "TEXT"),
    ("FLOAT", "DOUBLE"),
    ("FLOAT", "NUMERIC"),
    ("NUMERIC", "DOUBLE"),
    ("CHAR", "VARCHAR"),
    ("CHAR", "TEXT"),
}

# Dangerous type demotions
_TYPE_NARROWINGS = {
    ("BIGINT", "INTEGER"),
    ("BIGINT", "SMALLINT"),
    ("INTEGER", "SMALLINT"),
    ("TEXT", "VARCHAR"),
    ("VARCHAR", "DATE"),
    ("VARCHAR", "TIMESTAMP"),
    ("VARCHAR", "INTEGER"),
    ("DOUBLE", "FLOAT"),
    ("NUMERIC", "INTEGER"),
    ("TIMESTAMP", "DATE"),
    ("TIMESTAMP", "VARCHAR"),
}


# ── Data classes ───────────────────────────────────────────────────────────────


@dataclass
class ChangeAssessment:
    """Assessment of a single schema change event."""

    node_id: str
    change_type: str
    compatibility: CompatibilityType
    confidence: float
    blast_radius: int
    cascade_score: float
    affected_assets: list[str] = field(default_factory=list)
    query_impact: str = ""
    recommendation: str = ""
    auto_patchable: bool = False
    rollout_strategy: list[str] = field(default_factory=list)
    contract_violations: list[str] = field(default_factory=list)
    predicted_failure_modes: list[str] = field(default_factory=list)
    estimated_backfill_cost_usd: float = 0.0
    proof_evidence: list[dict] = field(default_factory=list)
    graph_risk_score: float = 0.0
    graph_risk_reasons: list[str] = field(default_factory=list)
    assumption_risks: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "change_type": self.change_type,
            "compatibility": self.compatibility.value,
            "compatibility_emoji": _COMPAT_EMOJI.get(self.compatibility, "❓"),
            "confidence": round(self.confidence, 3),
            "blast_radius": self.blast_radius,
            "cascade_score": round(self.cascade_score, 3),
            "affected_assets": self.affected_assets,
            "query_impact": self.query_impact,
            "recommendation": self.recommendation,
            "auto_patchable": self.auto_patchable,
            "rollout_strategy": self.rollout_strategy,
            "contract_violations": self.contract_violations,
            "predicted_failure_modes": self.predicted_failure_modes,
            "estimated_backfill_cost_usd": round(self.estimated_backfill_cost_usd, 2),
            "proof_evidence": self.proof_evidence[:5],
            "graph_risk_score": round(self.graph_risk_score, 3),
            "graph_risk_reasons": self.graph_risk_reasons[:4],
            "assumption_risks": self.assumption_risks[:5],
        }


@dataclass
class GateResult:
    """Full result of evaluating a migration through the Change Gate."""

    pr_number: Optional[int]
    pr_repo: str
    evaluated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    verdict: Verdict = Verdict.SAFE
    assessments: list[ChangeAssessment] = field(default_factory=list)
    blocked_by: list[str] = field(default_factory=list)
    review_reasons: list[str] = field(default_factory=list)
    total_blast_radius: int = 0
    total_estimated_backfill_cost_usd: float = 0.0
    patch_prs_opened: list[str] = field(default_factory=list)
    simulation_summary: str = ""  # Populated by run_wind_tunnel()
    proof_bundle: dict = field(default_factory=dict)
    wind_tunnel_receipt: dict = field(default_factory=dict)
    chaos_report: dict = field(default_factory=dict)
    reliability_score: float = 100.0
    oncall_risk: str = "LOW"
    next_actions: list[str] = field(default_factory=list)
    recommended_execution: dict = field(default_factory=dict)
    ecosystem_context: dict = field(default_factory=dict)
    calibration_summary: dict = field(default_factory=dict)
    iron_gate: dict = field(default_factory=dict)
    graph_intelligence: dict = field(default_factory=dict)
    finops_summary: dict = field(default_factory=dict)
    assumption_summary: dict = field(default_factory=dict)
    decision_summary: dict = field(default_factory=dict)
    risk_register: list[dict] = field(default_factory=list)
    remediation_blueprints: list[dict] = field(default_factory=list)
    savings_ledger: dict = field(default_factory=dict)
    gate_id: str = field(
        default_factory=lambda: hashlib.sha256(
            datetime.now(timezone.utc).isoformat().encode()
        ).hexdigest()[:8]
    )

    @property
    def blocking_assessments(self) -> list[ChangeAssessment]:
        return [a for a in self.assessments if a.compatibility in _BLOCK_TYPES]

    @property
    def review_assessments(self) -> list[ChangeAssessment]:
        return [a for a in self.assessments if a.compatibility in _REVIEW_TYPES]

    @property
    def safe_assessments(self) -> list[ChangeAssessment]:
        return [
            a
            for a in self.assessments
            if a.compatibility not in _BLOCK_TYPES and a.compatibility not in _REVIEW_TYPES
        ]

    def to_dict(self) -> dict:
        return {
            "gate_id": self.gate_id,
            "pr_number": self.pr_number,
            "pr_repo": self.pr_repo,
            "evaluated_at": self.evaluated_at,
            "verdict": self.verdict.value,
            "verdict_emoji": _VERDICT_EMOJI[self.verdict],
            "assessments": [a.to_dict() for a in self.assessments],
            "blocked_by": self.blocked_by,
            "review_reasons": self.review_reasons,
            "total_blast_radius": self.total_blast_radius,
            "total_estimated_backfill_cost_usd": round(self.total_estimated_backfill_cost_usd, 2),
            "patch_prs_opened": self.patch_prs_opened,
            "simulation_summary": self.simulation_summary,
            "proof_bundle": self.proof_bundle,
            "wind_tunnel_receipt": self.wind_tunnel_receipt,
            "chaos_report": self.chaos_report,
            "reliability_score": round(self.reliability_score, 1),
            "oncall_risk": self.oncall_risk,
            "next_actions": self.next_actions,
            "recommended_execution": self.recommended_execution,
            "ecosystem_context": self.ecosystem_context,
            "calibration_summary": self.calibration_summary,
            "iron_gate": self.iron_gate,
            "graph_intelligence": self.graph_intelligence,
            "finops_summary": self.finops_summary,
            "assumption_summary": self.assumption_summary,
            "decision_summary": self.decision_summary,
            "risk_register": self.risk_register,
            "remediation_blueprints": self.remediation_blueprints,
            "savings_ledger": self.savings_ledger,
        }

    def save(self, path: str = "data/gate_result.json") -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), indent=2))
        return p


# ── Config ─────────────────────────────────────────────────────────────────────


@dataclass
class GateConfig:
    # GitHub
    github_token: str = ""
    github_repo: str = ""
    data_owner_team: str = ""

    # Verdict thresholds
    block_on_destructive: bool = True
    block_on_narrowing: bool = True
    require_review_rename: bool = True
    require_review_nullable: bool = True
    strict_mode: bool = False
    max_safe_blast_radius: int = 1
    max_review_blast_radius: int = 3
    high_usage_query_threshold: int = 1000
    medium_usage_query_threshold: int = 100
    allow_defaulted_not_null_additions: bool = True
    max_null_rate_increase: float = 0.05
    max_cardinality_drop: float = 0.35
    block_on_data_regression: bool = False
    base_backfill_cost_usd: float = 15.0
    downstream_node_cost_usd: float = 12.5
    high_usage_cost_multiplier: float = 1.7
    private_table_cost_multiplier: float = 1.6
    strict_contract_cost_multiplier: float = 1.25

    # AST-first proofing
    proof_enabled: bool = True
    proof_source_paths: list[str] = field(default_factory=list)
    proof_max_files: int = 200
    proof_boundary_hops: int = 1

    # Graph intelligence / optional RGCN
    graph_intelligence_enabled: bool = True
    rgcn_model_path: str = ""
    graph_intelligence_top_k: int = 8

    # Ecosystem-native hooks
    dbt_manifest_path: str = ""
    dbt_catalog_path: str = ""
    dbt_run_results_path: str = ""
    dbt_sources_path: str = ""
    openlineage_paths: list[str] = field(default_factory=list)
    airflow_paths: list[str] = field(default_factory=list)
    dagster_paths: list[str] = field(default_factory=list)
    looker_paths: list[str] = field(default_factory=list)
    montecarlo_paths: list[str] = field(default_factory=list)

    # Calibration + Iron Gate
    calibration_store_path: str = "data/calibration_history.jsonl"
    iron_gate_block_review: bool = False
    iron_gate_cost_threshold_usd: float = 150.0
    iron_gate_context: str = "semzero/iron-gate"

    # Auto-patching
    auto_patch_consumers: bool = True

    # Wind Tunnel integration
    run_wind_tunnel: bool = False
    db_url: str = ""
    wind_tunnel_max_queries: int = 100
    chaos_default_mutation_budget: int = 12
    wind_tunnel_query_timeout: int = 15
    wind_tunnel_dry_run: bool = False
    wind_tunnel_live_mode: str = "safe"
    wind_tunnel_keep_clone: bool = False

    # Output
    data_dir: str = "data"


# ── Rollout playbooks# ── Rollout playbooks ──────────────────────────────────────────────────────────

_ROLLOUT_PLAYBOOKS: dict[CompatibilityType, list[str]] = {
    CompatibilityType.RENAME_HIGH_CONFIDENCE: [
        "1. Add new column with target name (dual-write phase)",
        "2. Backfill new column from old column",
        "3. Update all downstream consumers (SemZero can patch these)",
        "4. Monitor — confirm old column read traffic drops to zero",
        "5. Deprecate old column (add comment/tag in schema)",
        "6. Drop old column after N-day grace period",
    ],
    CompatibilityType.RENAME_LOW_CONFIDENCE: [
        "1. Confirm rename intent with data owner",
        "2. Identify all consumers manually (blast radius attached below)",
        "3. Follow standard rename playbook after confirmation",
        "4. Consider keeping old column as alias during transition",
    ],
    CompatibilityType.TYPE_NARROWING: [
        "1. Add shadow column with the new type",
        "2. Validate: SELECT COUNT(*) WHERE CAST(old_col AS new_type) fails",
        "3. If <0.1% failures: proceed. If >0.1%: stop and investigate",
        "4. Patch all consumers expecting old type",
        "5. Flip reads to new column, then drop old column",
    ],
    CompatibilityType.NULLABLE_HARDENING: [
        "1. Check current null rate: SELECT COUNT(*) WHERE col IS NULL",
        "2. Backfill nulls with a safe default if null_rate > 0%",
        "3. Add NOT NULL constraint only after null_rate reaches 0%",
        "4. Update all INSERTs to always provide this column",
        "5. Add a dbt not_null test to catch future regressions",
    ],
    CompatibilityType.TYPE_WIDENING: [
        "1. Apply the type widening — this is backwards-compatible",
        "2. Update any consumers with strict type assertions (ORM models, etc.)",
        "3. Run dbt tests to confirm no unexpected failures",
    ],
    CompatibilityType.ADDITIVE_SAFE: [
        "1. Add the column — safe to merge",
        "2. Document in dbt schema.yml",
        "3. Add not_null or accepted_values test if appropriate",
    ],
    CompatibilityType.ADDITIVE_BREAKING: [
        "1. Add a DEFAULT value to the NOT NULL column, OR",
        "2. Add as nullable first → backfill → then add NOT NULL constraint",
        "3. Update all upstream INSERT statements",
        "4. Verify no ETL jobs will fail on insert before merging",
    ],
    CompatibilityType.DESTRUCTIVE_DELETE: [
        "1. STOP — confirm drop is intentional with the data owner",
        "2. Check blast radius: all downstream consumers listed below",
        "3. Archive data before dropping: INSERT INTO archive SELECT ...",
        "4. Update all downstream consumers (SemZero patches available)",
        "5. Add deprecation notice 30 days before actual drop",
        "6. Monitor for any consumer still reading the column",
    ],
    CompatibilityType.SEMANTIC_BREAKING: [
        "1. STOP — this change alters the meaning of data, not just its structure",
        "2. Identify all consumers that depend on the current semantics",
        "3. Coordinate with all data consumers before applying any change",
        "4. Consider adding a new column with the new semantics alongside the old",
        "5. Migrate consumers to the new column before removing the old one",
        "6. Add dbt documentation and tests to make the new semantics explicit",
    ],
    CompatibilityType.DATA_REGRESSION: [
        "1. Compare before/after null-rate, cardinality, and sample values",
        "2. Confirm whether the drift is expected backfill noise or a true regression",
        "3. Add a freshness or quality check before the next rollout",
        "4. Merge only after the owner signs off on the observed data-shape change",
    ],
}


# ── Compatibility Oracle ───────────────────────────────────────────────────────


class CompatibilityOracle:
    """Classify schema changes using structural, semantic, and rollout-aware heuristics."""

    def __init__(self, config: Optional[GateConfig] = None) -> None:
        self.config = config or GateConfig()

    def classify(
        self,
        event: dict,
        graph_json: dict,
        blast_report: Optional[dict] = None,
    ) -> CompatibilityType:
        change_type = event.get("change_type", "")
        before = event.get("before") or {}
        after = event.get("after") or {}
        blast_count = (blast_report or {}).get("summary", {}).get("total_impacted", 0)

        if change_type in {"COLUMN_REMOVED", "TABLE_REMOVED"}:
            return CompatibilityType.DESTRUCTIVE_DELETE
        if change_type == "TYPE_NARROWING":
            return CompatibilityType.TYPE_NARROWING

        if change_type == "COLUMN_ADDED":
            if after.get("nullable", True):
                return CompatibilityType.ADDITIVE_SAFE
            default_present = after.get("default") not in (None, "", "NULL")
            generated = bool(after.get("generated") or after.get("identity"))
            return (
                CompatibilityType.ADDITIVE_SAFE
                if default_present or generated
                else CompatibilityType.ADDITIVE_BREAKING
            )
        if change_type == "TABLE_ADDED":
            return CompatibilityType.ADDITIVE_SAFE

        if change_type in {"COLUMN_RENAMED", "TABLE_RENAMED"}:
            detail = (event.get("detail") or "").lower()
            high_confidence = (
                blast_count <= 3
                or "renamed to" in detail
                or self._rename_similarity(before, after, event) >= 0.75
            )
            return (
                CompatibilityType.RENAME_HIGH_CONFIDENCE
                if high_confidence
                else CompatibilityType.RENAME_LOW_CONFIDENCE
            )

        if change_type == "TYPE_CHANGED":
            old_raw = before.get("dtype", "UNKNOWN")
            new_raw = after.get("dtype", "UNKNOWN")
            old_type = self._normalise_type(old_raw)
            new_type = self._normalise_type(new_raw)
            if self._is_timezone_boundary_change(old_raw, new_raw):
                return CompatibilityType.SEMANTIC_BREAKING
            if self._is_varchar_length_narrowing(old_raw, new_raw):
                return CompatibilityType.TYPE_NARROWING
            if self._is_numeric_precision_narrowing(old_raw, new_raw):
                return CompatibilityType.TYPE_NARROWING
            if self._is_semantic_break(before, after):
                return CompatibilityType.SEMANTIC_BREAKING
            if old_type == new_type:
                return CompatibilityType.ADDITIVE_SAFE
            if (old_type, new_type) in _TYPE_WIDENINGS:
                return CompatibilityType.TYPE_WIDENING
            if (old_type, new_type) in _TYPE_NARROWINGS:
                return CompatibilityType.TYPE_NARROWING
            if self._family(old_type) != self._family(new_type):
                return CompatibilityType.SEMANTIC_BREAKING
            return CompatibilityType.TYPE_NARROWING

        if change_type == "NULLABLE_CHANGED":
            was_nullable = before.get("nullable", True)
            is_nullable = after.get("nullable", True)
            if was_nullable and not is_nullable:
                null_rate = before.get("null_rate")
                default_present = after.get("default") not in (None, "", "NULL")
                generated = bool(after.get("generated") or after.get("identity"))
                null_rate_known_zero = null_rate is not None and float(null_rate or 0) == 0.0
                if (null_rate_known_zero and blast_count <= 1) or default_present or generated:
                    return CompatibilityType.ADDITIVE_SAFE
                return CompatibilityType.NULLABLE_HARDENING
            return CompatibilityType.ADDITIVE_SAFE

        if change_type == "STATS_DRIFTED":
            return self._classify_stats_regression(before, after)

        return CompatibilityType.ADDITIVE_SAFE

    def _classify_stats_regression(self, before: dict, after: dict) -> CompatibilityType:
        old_null = float(before.get("null_rate", 0) or 0)
        new_null = float(after.get("null_rate", 0) or 0)
        old_card = float(before.get("cardinality", 0) or 0)
        new_card = float(after.get("cardinality", 0) or 0)

        null_delta = new_null - old_null
        card_drop = old_card - new_card
        severe = (
            null_delta >= self.config.max_null_rate_increase
            or card_drop >= self.config.max_cardinality_drop
        )
        samples_before = {str(v).lower() for v in before.get("sample_values", []) if v is not None}
        samples_after = {str(v).lower() for v in after.get("sample_values", []) if v is not None}
        semantic_flip = bool(
            samples_before and samples_after and not (samples_before & samples_after)
        )
        additive_domain_growth = bool(
            samples_before and samples_after and samples_before < samples_after
        )
        domain_like = bool(
            any(
                token in key.lower()
                for key in list(before.keys()) + list(after.keys())
                for token in {"domain", "enum", "status", "state", "category", "type"}
            )
            or any(
                any(
                    token in sample
                    for token in {
                        "active",
                        "paused",
                        "archived",
                        "pending",
                        "done",
                        "failed",
                        "complete",
                        "cancel",
                    }
                )
                for sample in samples_before | samples_after
            )
        )
        if severe or semantic_flip or (domain_like and additive_domain_growth):
            return CompatibilityType.DATA_REGRESSION
        return CompatibilityType.ADDITIVE_SAFE

    @staticmethod
    def _varchar_length(value: str) -> int | None:
        text = str(value or "")
        match = re.search(r"(?:VAR)?CHAR\s*\((\d+)\)", text, re.IGNORECASE)
        return int(match.group(1)) if match else None

    def _is_varchar_length_narrowing(self, before_dtype: str, after_dtype: str) -> bool:
        old_type = self._normalise_type(before_dtype)
        new_type = self._normalise_type(after_dtype)
        if old_type not in {"VARCHAR", "CHAR", "TEXT", "STRING", "NVARCHAR"} or new_type not in {
            "VARCHAR",
            "CHAR",
            "TEXT",
            "STRING",
            "NVARCHAR",
        }:
            return False
        old_len = self._varchar_length(before_dtype)
        new_len = self._varchar_length(after_dtype)
        return old_len is not None and new_len is not None and new_len < old_len

    @staticmethod
    def _numeric_precision(value: str) -> tuple[int | None, int | None]:
        text = str(value or "")
        match = re.search(r"(?:NUMERIC|DECIMAL)\s*\((\d+)\s*,\s*(\d+)\)", text, re.IGNORECASE)
        if not match:
            return None, None
        return int(match.group(1)), int(match.group(2))

    def _is_numeric_precision_narrowing(self, before_dtype: str, after_dtype: str) -> bool:
        old_type = self._normalise_type(before_dtype)
        new_type = self._normalise_type(after_dtype)
        if old_type not in {"NUMERIC", "DECIMAL"} or new_type not in {"NUMERIC", "DECIMAL"}:
            return False
        old_precision, old_scale = self._numeric_precision(before_dtype)
        new_precision, new_scale = self._numeric_precision(after_dtype)
        if new_precision is None or new_scale is None:
            return False
        if old_precision is None or old_scale is None:
            return False
        return new_precision < old_precision or new_scale < old_scale

    @staticmethod
    def _is_timezone_boundary_change(before_dtype: str, after_dtype: str) -> bool:
        before = str(before_dtype or "").upper()
        after = str(after_dtype or "").upper()
        tz_before = any(
            token in before for token in ("TIMESTAMPTZ", "TIMESTAMP_TZ", "TIMESTAMP WITH TIME ZONE")
        )
        tz_after = any(
            token in after for token in ("TIMESTAMPTZ", "TIMESTAMP_TZ", "TIMESTAMP WITH TIME ZONE")
        )
        ntz_before = any(
            token in before for token in ("TIMESTAMP_NTZ", "TIMESTAMP WITHOUT TIME ZONE")
        ) or ("TIMESTAMP" in before and not tz_before)
        ntz_after = any(
            token in after for token in ("TIMESTAMP_NTZ", "TIMESTAMP WITHOUT TIME ZONE")
        ) or ("TIMESTAMP" in after and not tz_after)
        return (tz_before and ntz_after) or (ntz_before and tz_after)

    def _is_semantic_break(self, before: dict, after: dict) -> bool:
        old_card = float(before.get("cardinality", 0) or 0)
        new_card = float(after.get("cardinality", 0) or 0)
        old_null = float(before.get("null_rate", 0) or 0)
        new_null = float(after.get("null_rate", 0) or 0)
        if old_card > 0.5 and new_card < 0.1:
            return True
        if (
            old_card > 0
            and new_card > 0
            and (new_card / max(old_card, 1e-9) >= 2.2 or new_card / max(old_card, 1e-9) <= 0.35)
        ):
            return True
        if old_null <= 0.05 and new_null >= 0.3:
            return True

        before_dtype = str(before.get("dtype", ""))
        after_dtype = str(after.get("dtype", ""))
        old_family = self._family(before_dtype)
        new_family = self._family(after_dtype)
        dangerous_crosses = {
            ("VARCHAR", "NUMERIC"),
            ("VARCHAR", "BOOLEAN"),
            ("TIMESTAMP", "VARCHAR"),
            ("TIMESTAMP", "NUMERIC"),
            ("NUMERIC", "BOOLEAN"),
        }
        if (old_family, new_family) in dangerous_crosses:
            return True
        if self._is_timezone_boundary_change(before_dtype, after_dtype):
            return True

        old_samples = {str(v).lower() for v in before.get("sample_values", []) if v is not None}
        new_samples = {str(v).lower() for v in after.get("sample_values", []) if v is not None}
        if (
            old_samples
            and new_samples
            and not (old_samples & new_samples)
            and old_family == new_family
        ):
            return True
        enum_like = max(len(old_samples), len(new_samples)) <= 8 and any(
            len(v) <= 32 for v in (old_samples | new_samples)
        )
        if enum_like and old_samples and new_samples and old_samples != new_samples:
            if len(old_samples.symmetric_difference(new_samples)) >= 2:
                return True
        temporal_like = (
            any("t" in v and ":" in v for v in old_samples | new_samples)
            or old_family == "TIMESTAMP"
            or new_family == "TIMESTAMP"
        )
        if (
            temporal_like
            and old_samples
            and new_samples
            and old_samples != new_samples
            and old_family == new_family
        ):
            return True
        return False

    @staticmethod
    def _normalise_type(raw: str) -> str:
        return (raw or "UNKNOWN").upper().split("(")[0]

    @staticmethod
    def _rename_similarity(before: dict, after: dict, event: dict) -> float:
        old_name = (before.get("name") or event.get("node_id", "").split(".")[-1]).lower()
        new_name = (after.get("name") or "").lower()
        if not old_name or not new_name:
            return 0.0
        old_tokens = set(re.split(r"[_\W]+", old_name)) - {""}
        new_tokens = set(re.split(r"[_\W]+", new_name)) - {""}
        if not old_tokens or not new_tokens:
            return 0.0
        return len(old_tokens & new_tokens) / max(len(old_tokens | new_tokens), 1)

    @staticmethod
    def _family(t: str) -> str:
        t = (t or "").upper().split("(")[0]
        if t in {
            "INTEGER",
            "INT",
            "BIGINT",
            "SMALLINT",
            "NUMERIC",
            "DECIMAL",
            "FLOAT",
            "DOUBLE",
            "REAL",
        }:
            return "NUMERIC"
        if t in {"VARCHAR", "TEXT", "CHAR", "STRING", "NVARCHAR"}:
            return "VARCHAR"
        if t in {"TIMESTAMP", "DATE", "DATETIME", "TIME", "TIMESTAMPTZ"}:
            return "TIMESTAMP"
        if t in {"BOOLEAN", "BOOL"}:
            return "BOOLEAN"
        return "OTHER"


# ── Change Gate# ── Change Gate ────────────────────────────────────────────────────────────────


class ChangeGate:
    """
    Pre-merge Change Gate.

    Evaluates a drift report and produces a SAFE / NEEDS_REVIEW / BLOCK verdict
    with full context, rollout playbooks, and optional Wind Tunnel simulation.
    """

    def __init__(self, graph_json: dict, config: GateConfig) -> None:
        self.graph_json = graph_json
        self.config = config
        self.oracle = CompatibilityOracle(config)
        self._table_contracts: dict[str, object] = {}
        self._column_contracts: dict[str, object] = {}
        try:
            from .contract_compiler import ContractCompiler

            bundle = ContractCompiler(graph_json).compile()
            self._table_contracts = {table.table_id: table for table in bundle.tables}
            self._column_contracts = {
                f"{table.table_id}.{column.name}": column
                for table in bundle.tables
                for column in table.columns
            }
        except Exception as exc:
            log.debug("Contract inference unavailable: %s", exc)
        self._ecosystem_context = EcosystemContext.load(
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
        self._calibration_store = ReliabilityCalibrationStore(self.config.calibration_store_path)
        self._calibration_summary = self._calibration_store.load_summary()
        self._graph_intelligence = GraphIntelligenceEngine(
            graph_json,
            enabled=self.config.graph_intelligence_enabled,
            rgcn_model_path=self.config.rgcn_model_path,
        ).analyse()
        self._finops_analyser = FinOpsChangeAnalyser(self.config.proof_source_paths)
        self._assumption_gate = AssumptionGate(self.config.proof_source_paths)

    # ── Main evaluation ───────────────────────────────────────────────────────

    def evaluate(
        self,
        drift_report: dict,
        blast_reports: Optional[dict[str, dict]] = None,
        pr_number: Optional[int] = None,
    ) -> GateResult:
        """
        Evaluate a drift report through the Change Gate.

        Args:
            drift_report:  SchemaDriftDetector.diff().to_dict()
            blast_reports: Optional pre-computed {node_id: blast_report}
            pr_number:     GitHub PR number (for posting results)

        Returns:
            GateResult with full verdict and assessments
        """
        result = GateResult(
            pr_number=pr_number,
            pr_repo=self.config.github_repo,
        )

        events = drift_report.get("events", [])
        if not events:
            result.verdict = Verdict.SAFE
            return result

        if blast_reports is None:
            blast_reports = self._compute_blast_reports(events)

        proof_bundle = self._run_ast_proof(drift_report)
        if proof_bundle:
            result.proof_bundle = proof_bundle.to_dict()
        assumption_summary = self._assumption_gate.analyse(self.graph_json, drift_report)
        result.assumption_summary = assumption_summary.to_dict()

        ecosystem_payload = self._ecosystem_context.to_dict()
        if any(ecosystem_payload.get(k) for k in ("focus_assets",)):
            result.ecosystem_context = ecosystem_payload
        result.graph_intelligence = self._graph_intelligence.to_dict()

        for event in events:
            node_id = event.get("node_id", "")
            blast_report = blast_reports.get(node_id, {})
            compat = self.oracle.classify(event, self.graph_json, blast_report)

            blast_summary = blast_report.get("summary", {})
            blast_radius = blast_summary.get("total_impacted", 0)
            cascade_score = blast_summary.get("cascade_score", 0.0)
            affected_assets = [n["node_id"] for n in blast_report.get("impacted_nodes", [])[:8]]
            affected_assets = self._augment_with_ecosystem_assets(node_id, affected_assets)

            contract_violations = self._contract_violations(node_id, compat, event)
            predicted_failures = self._predict_failure_modes(
                node_id, compat, affected_assets, event
            )
            assumption_hits = [item.to_dict() for item in assumption_summary.for_node(node_id)[:5]]
            estimated_cost = self._estimate_backfill_cost_usd(node_id, compat, blast_radius, event)
            proof_evidence = (
                [finding.to_dict() for finding in proof_bundle.for_node(node_id)[:4]]
                if proof_bundle
                else []
            )
            graph_signal = self._graph_intelligence.for_node(node_id)
            if assumption_hits:
                contract_violations = (
                    contract_violations
                    + [
                        f"Undocumented downstream assumption ({item.get('assumption_type', 'ASSUMPTION').replace('_', ' ').title()}) still references `{Path(item.get('source_path', 'unknown')).name}`."
                        for item in assumption_hits[:3]
                    ]
                )[:6]
                predicted_failures = (
                    predicted_failures
                    + [
                        item.get(
                            "reason",
                            "Downstream undocumented assumptions can silently invalidate results.",
                        )
                        for item in assumption_hits[:3]
                    ]
                )[:6]
                estimated_cost = round(
                    estimated_cost * (1 + min(0.25, 0.05 * len(assumption_hits))), 2
                )

            if proof_evidence:
                predicted_failures = (
                    predicted_failures
                    + [item.get("expected_failure_mode", "") for item in proof_evidence]
                )[:5]
                contract_violations = (
                    contract_violations
                    + [
                        f"Direct source reference in `{Path(item.get('asset_path', 'unknown')).name}` still points at the changed asset."
                        for item in proof_evidence[:2]
                    ]
                )[:5]
                affected_assets = list(
                    dict.fromkeys(
                        affected_assets
                        + [
                            hit
                            for item in proof_evidence
                            for hit in (
                                item.get("direct_hits", []) + item.get("downstream_hits", [])
                            )
                        ]
                    )
                )[:8]
                estimated_cost = round(
                    estimated_cost * (1 + min(0.35, 0.08 * len(proof_evidence))), 2
                )

            confidence = self._confidence(compat, blast_report)
            if assumption_hits:
                contract_violations = (
                    contract_violations
                    + [
                        f"Undocumented downstream assumption ({item.get('assumption_type', 'ASSUMPTION').replace('_', ' ').title()}) still references `{Path(item.get('source_path', 'unknown')).name}`."
                        for item in assumption_hits[:3]
                    ]
                )[:6]
                predicted_failures = (
                    predicted_failures
                    + [
                        item.get(
                            "reason",
                            "Downstream undocumented assumptions can silently invalidate results.",
                        )
                        for item in assumption_hits[:3]
                    ]
                )[:6]
                estimated_cost = round(
                    estimated_cost * (1 + min(0.25, 0.05 * len(assumption_hits))), 2
                )

            if proof_evidence:
                confidence = min(
                    0.99,
                    max(
                        confidence,
                        max(float(item.get("confidence", 0.0)) for item in proof_evidence),
                    ),
                )
            if graph_signal and graph_signal.score >= 0.55:
                confidence = min(0.99, confidence + min(0.08, graph_signal.score * 0.08))

            recommendation = self._recommendation(compat, event, blast_radius)
            if proof_evidence and proof_evidence[0].get("suggested_fix"):
                recommendation = proof_evidence[0]["suggested_fix"]

            assessment = ChangeAssessment(
                node_id=node_id,
                change_type=event.get("change_type", ""),
                compatibility=compat,
                confidence=confidence,
                blast_radius=blast_radius,
                cascade_score=cascade_score,
                affected_assets=affected_assets,
                query_impact=self._query_impact(node_id),
                recommendation=recommendation,
                auto_patchable=self._is_auto_patchable(compat),
                rollout_strategy=_ROLLOUT_PLAYBOOKS.get(compat, []),
                contract_violations=contract_violations,
                predicted_failure_modes=predicted_failures,
                estimated_backfill_cost_usd=estimated_cost,
                proof_evidence=proof_evidence,
                graph_risk_score=graph_signal.score if graph_signal else 0.0,
                graph_risk_reasons=list(graph_signal.reasons[:4]) if graph_signal else [],
                assumption_risks=assumption_hits,
            )
            result.assessments.append(assessment)
            result.total_blast_radius = max(result.total_blast_radius, blast_radius)
            result.total_estimated_backfill_cost_usd += estimated_cost

        result.verdict, result.blocked_by, result.review_reasons = self._compute_verdict(
            result.assessments
        )
        self._finalise_result(result)
        self._calibration_store.record(result.to_dict())
        log.info(
            f"Gate verdict: {result.verdict.value} | "
            f"{len(result.blocking_assessments)} blocking, "
            f"{len(result.review_assessments)} review, "
            f"{len(result.safe_assessments)} safe"
        )
        return result

    # ── Wind Tunnel integration ───────────────────────────────────────────────

    def _finalise_result(self, result: GateResult) -> None:
        result.finops_summary = self._build_finops_summary(result)
        result.reliability_score = self._compute_reliability_score(result)
        result.oncall_risk = self._compute_oncall_risk(result)
        result.recommended_execution = self._build_execution_recommendation(result)
        result.decision_summary = self._build_decision_summary(result)
        result.risk_register = self._build_risk_register(result)
        result.remediation_blueprints = self._build_remediation_blueprints(result)
        result.savings_ledger = self._build_savings_ledger(result)
        result.next_actions = self._build_next_actions(result)
        result.calibration_summary = self._calibration_summary.to_dict()
        result.graph_intelligence = self._graph_intelligence.to_dict()
        result.iron_gate = self._build_iron_gate(result)

    def _compute_reliability_score(self, result: GateResult) -> float:
        score = 100.0
        score -= len(result.blocking_assessments) * 28.0
        score -= len(result.review_assessments) * 10.0
        score -= min(20.0, result.total_blast_radius * 2.5)
        score -= min(18.0, result.total_estimated_backfill_cost_usd / 35.0)
        proof_findings = (
            (result.proof_bundle or {}).get("findings", [])
            if isinstance(result.proof_bundle, dict)
            else []
        )
        proof_count = len(proof_findings)
        score -= min(10.0, proof_count * 2.0)
        score -= min(
            6.0,
            sum(
                1
                for item in proof_findings
                if item.get("language") in {"typescript", "javascript", "prisma"}
            )
            * 1.5,
        )
        graph_penalty = max(
            (float(a.graph_risk_score or 0.0) for a in result.assessments), default=0.0
        )
        score -= min(9.0, graph_penalty * 10.0)
        if result.wind_tunnel_receipt:
            score -= min(
                20.0, float(result.wind_tunnel_receipt.get("queries_broken", 0) or 0) * 5.0
            )
            score -= min(
                8.0, float(result.wind_tunnel_receipt.get("queries_mismatch", 0) or 0) * 2.0
            )
        if result.chaos_report:
            summary = (
                result.chaos_report.get("summary", {})
                if isinstance(result.chaos_report, dict)
                else {}
            )
            score -= min(12.0, float(summary.get("mutations_that_broke", 0) or 0) * 1.5)
        finops = result.finops_summary or {}
        score -= min(8.0, float(finops.get("projected_weekly_cost_usd", 0.0) or 0.0) / 250.0)
        assumption_summary = (
            (result.assumption_summary or {}) if isinstance(result.assumption_summary, dict) else {}
        )
        assumption_count = len(assumption_summary.get("findings", []))
        assumption_risk_score = float(assumption_summary.get("risk_score", 0.0) or 0.0)
        critical_assumptions = len(assumption_summary.get("critical_findings", []))
        score -= min(
            10.0,
            assumption_count * 0.75 + assumption_risk_score / 18.0 + critical_assumptions * 1.2,
        )
        if result.verdict == Verdict.BLOCK:
            score -= 8.0
        elif result.verdict == Verdict.NEEDS_REVIEW:
            score -= 4.0
        return max(0.0, min(100.0, score))

    def _compute_oncall_risk(self, result: GateResult) -> str:
        if result.verdict == Verdict.BLOCK and (
            result.total_blast_radius >= 3 or result.total_estimated_backfill_cost_usd >= 75
        ):
            return "HIGH"
        ecosystem_assets = (
            (result.ecosystem_context or {}).get("focus_assets", [])
            if isinstance(result.ecosystem_context, dict)
            else []
        )
        top_graph_score = max(
            (float(a.graph_risk_score or 0.0) for a in result.assessments), default=0.0
        )
        assumption_summary = (
            (result.assumption_summary or {}) if isinstance(result.assumption_summary, dict) else {}
        )
        critical_assumptions = len(assumption_summary.get("critical_findings", []))
        projected_weekly_cost = float(
            (result.finops_summary or {}).get("projected_weekly_cost_usd", 0.0) or 0.0
        )
        if (
            result.reliability_score < 45
            or result.total_estimated_backfill_cost_usd >= 120
            or len(ecosystem_assets) >= 8
            or top_graph_score >= 0.72
            or critical_assumptions >= 3
            or projected_weekly_cost >= 180.0
        ):
            return "HIGH"
        if (
            result.verdict == Verdict.NEEDS_REVIEW
            or result.reliability_score < 75
            or result.total_blast_radius >= 2
        ):
            return "MEDIUM"
        return "LOW"

    def _build_execution_recommendation(self, result: GateResult) -> dict:
        reasons: list[str] = []
        scope_assets: list[str] = []
        priority_nodes: list[str] = []
        risk_terms = {
            "null",
            "join",
            "temporal",
            "late-arriving",
            "mismatch",
            "semantic",
            "cardinality",
            "incremental",
            "delete",
            "duplicate",
            "reconciliation",
            "timezone",
            "truncat",
            "grain",
            "bucket",
            "status",
            "freshness",
        }
        should_run_wind_tunnel = False
        should_run_chaos = False
        require_future_workload = False
        scope_tables: list[str] = []
        ecosystem_focus = (
            (result.ecosystem_context or {}).get("focus_assets", [])
            if isinstance(result.ecosystem_context, dict)
            else []
        )

        for assessment in result.assessments:
            priority_nodes.append(assessment.node_id)
            scope_assets.extend([assessment.node_id, *assessment.affected_assets])
            scope_tables.append(assessment.node_id.split(".")[0])
            failure_blob = " ".join(assessment.predicted_failure_modes).lower()
            if (
                assessment.compatibility in _BLOCK_TYPES
                or assessment.blast_radius > 0
                or assessment.proof_evidence
            ):
                should_run_wind_tunnel = True
            if assessment.graph_risk_score >= 0.55:
                should_run_wind_tunnel = True
                reasons.append(
                    f"Graph intelligence ranked `{assessment.node_id}` as structurally fragile ({assessment.graph_risk_score:.2f})"
                )
                scope_assets.extend([assessment.node_id, *assessment.affected_assets[:3]])
            if any(term in failure_blob for term in risk_terms) or assessment.compatibility in {
                CompatibilityType.DATA_REGRESSION,
                CompatibilityType.SEMANTIC_BREAKING,
            }:
                should_run_chaos = True
                require_future_workload = True
            if assessment.contract_violations:
                reasons.append(f"{assessment.node_id} threatens an inferred consumer contract")
            if assessment.proof_evidence:
                reasons.append(f"Direct source references still point at {assessment.node_id}")
                evidence_ops = {
                    op for item in assessment.proof_evidence for op in item.get("operations", [])
                }
                if any(
                    item.get("language") in {"typescript", "javascript", "prisma"}
                    for item in assessment.proof_evidence
                ):
                    require_future_workload = True
                    reasons.append(
                        f"Application-layer schema references also need pre-release replay for {assessment.node_id}"
                    )
                if any(item.get("filters") for item in assessment.proof_evidence):
                    should_run_wind_tunnel = True
                    should_run_chaos = True
                    require_future_workload = True
                    reasons.append(
                        f"Hardcoded downstream filters still target `{assessment.node_id}` and need domain/regime replay"
                    )
                if "incremental" in evidence_ops:
                    should_run_wind_tunnel = True
                    should_run_chaos = True
                    require_future_workload = True
                    reasons.append(
                        f"Incremental logic references `{assessment.node_id}` and needs state-reconciliation testing"
                    )
        scope_assets.extend(ecosystem_focus[:10])
        if (result.ecosystem_context or {}).get("dagster", {}).get("failing_assets"):
            should_run_chaos = True
            reasons.append("Existing failing asset checks warrant stateful recovery testing")
        if (result.ecosystem_context or {}).get("airflow", {}).get("temporal_paths"):
            require_future_workload = True
            reasons.append(
                "Airflow schedules indicate temporal paths that need regime-aware replay"
            )
        if result.verdict == Verdict.BLOCK:
            reasons.append("Blocking changes should be replayed before merge")
        elif result.verdict == Verdict.NEEDS_REVIEW:
            reasons.append("Review-only changes still need pre-release evidence")
        if not self.config.db_url:
            should_run_wind_tunnel = False
            should_run_chaos = False
            reasons.append("No live DB URL configured; stay in proof/report mode")

        ranked_priority = sorted(
            dict.fromkeys(priority_nodes),
            key=lambda node_id: (
                -(
                    self._graph_intelligence.for_node(node_id).score
                    if self._graph_intelligence.for_node(node_id)
                    else 0.0
                )
            ),
        )
        top_graph_nodes = [
            item.node_id
            for item in self._graph_intelligence.top_nodes(self.config.graph_intelligence_top_k)
        ]
        priority_nodes = list(dict.fromkeys(ranked_priority + top_graph_nodes))
        assumption_summary = (
            (result.assumption_summary or {}) if isinstance(result.assumption_summary, dict) else {}
        )
        assumption_findings = assumption_summary.get("findings", [])
        assumption_revalidation_required = bool(assumption_findings)
        assumption_risk_score = float(assumption_summary.get("risk_score", 0.0) or 0.0)
        critical_assumptions = assumption_summary.get("critical_findings", []) or []
        contract_recommendations = assumption_summary.get("contract_recommendations", []) or []
        undocumented_assumption_types = sorted(
            {
                item.get("assumption_type", "")
                for item in assumption_findings
                if item.get("assumption_type")
            }
        )
        incremental_state_checks_required = any(
            "incremental" in " ".join(a.predicted_failure_modes).lower()
            or any(
                "incremental" in " ".join(item.get("operations", [])) for item in a.proof_evidence
            )
            for a in result.assessments
        )
        domain_filter_drift_required = any(
            any(item.get("filters") for item in a.proof_evidence) for a in result.assessments
        )
        top_graph_score = max(
            (float(a.graph_risk_score or 0.0) for a in result.assessments), default=0.0
        )
        high_risk_nodes = sum(
            1
            for a in result.assessments
            if a.compatibility in _BLOCK_TYPES or float(a.graph_risk_score or 0.0) >= 0.55
        )
        wind_budget = 0
        if should_run_wind_tunnel:
            wind_budget = 12
            wind_budget += min(36, max(result.total_blast_radius, len(result.assessments)) * 4)
            if require_future_workload:
                wind_budget += 10
            if domain_filter_drift_required:
                wind_budget += 10
            if incremental_state_checks_required:
                wind_budget += 8
            if result.verdict == Verdict.BLOCK:
                wind_budget += 14
            elif result.verdict == Verdict.NEEDS_REVIEW:
                wind_budget += 6
            if top_graph_score >= 0.72:
                wind_budget += 8
            wind_budget = max(8, min(self.config.wind_tunnel_max_queries, wind_budget))
        chaos_budget = 0
        if should_run_chaos:
            chaos_budget = 4
            chaos_budget += min(6, high_risk_nodes)
            chaos_budget += min(6, result.total_blast_radius)
            if require_future_workload:
                chaos_budget += 2
            if domain_filter_drift_required:
                chaos_budget += 2
            if incremental_state_checks_required:
                chaos_budget += 2
            if result.verdict == Verdict.BLOCK:
                chaos_budget += 4
            chaos_budget = max(4, min(self.config.chaos_default_mutation_budget * 2, chaos_budget))
        baseline_wind_budget = max(24, int(self.config.wind_tunnel_max_queries or 0))
        baseline_chaos_budget = max(6, int(self.config.chaos_default_mutation_budget or 0))
        wind_saved_queries = (
            max(0, baseline_wind_budget - wind_budget)
            if should_run_wind_tunnel
            else baseline_wind_budget
        )
        chaos_saved_mutations = (
            max(0, baseline_chaos_budget - chaos_budget)
            if should_run_chaos
            else baseline_chaos_budget
        )
        per_run_compute_minutes_saved = round(
            wind_saved_queries * 0.35 + chaos_saved_mutations * 1.2, 1
        )
        per_run_review_minutes_saved = round(
            min(45.0, len(set(scope_assets)) * 1.4 + len(reasons) * 1.1), 1
        )
        scope_reduction_pct = 0.0
        if baseline_wind_budget:
            scope_reduction_pct += max(0.0, (wind_saved_queries / baseline_wind_budget) * 100.0)
        if baseline_chaos_budget:
            scope_reduction_pct += max(0.0, (chaos_saved_mutations / baseline_chaos_budget) * 100.0)
        scope_reduction_pct = round(
            scope_reduction_pct / (2 if should_run_chaos or should_run_wind_tunnel else 1), 1
        )
        finops_summary = self._build_finops_summary(result)
        if float(finops_summary.get("projected_weekly_cost_usd", 0.0) or 0.0) >= 100.0:
            reasons.append(
                f"Transformation-layer FinOps scan projects ${float(finops_summary.get('projected_weekly_cost_usd', 0.0)):,.0f}/week of avoidable compute waste"
            )
        contract_updates_required = bool(contract_recommendations or critical_assumptions)
        targeted_test_modes: list[str] = []
        if (
            domain_filter_drift_required
            or "DOMAIN_FILTER_ASSUMPTION" in undocumented_assumption_types
        ):
            targeted_test_modes.append("domain_regime_replay")
        if (
            incremental_state_checks_required
            or "INCREMENTAL_STATE_ASSUMPTION" in undocumented_assumption_types
        ):
            targeted_test_modes.append("incremental_state_reconciliation")
        if any(
            t in undocumented_assumption_types
            for t in ["GRAIN_OR_DEDUP_ASSUMPTION", "JOIN_CARDINALITY_ASSUMPTION"]
        ):
            targeted_test_modes.append("grain_and_join_cardinality")
        if any(
            t in undocumented_assumption_types
            for t in [
                "TEMPORAL_BUCKETING_ASSUMPTION",
                "TEMPORAL_COMPLETENESS_ASSUMPTION",
                "FRESHNESS_WINDOW_ASSUMPTION",
            ]
        ):
            targeted_test_modes.append("temporal_regime_replay")
        if should_run_chaos and "stateful_recovery" not in targeted_test_modes:
            targeted_test_modes.append("stateful_recovery")
        priority_assumption_nodes = [
            item.get("node_id")
            for item in assumption_summary.get("top_nodes", [])
            if item.get("node_id")
        ]
        if assumption_revalidation_required:
            reasons.append(
                "Undocumented downstream assumptions were detected in proof sources and need explicit revalidation before merge"
            )
            if assumption_risk_score >= 18.0:
                should_run_chaos = should_run_chaos or bool(self.config.db_url)
                require_future_workload = True
                reasons.append(
                    "Assumption Gate found elevated semantic/operational risk, so SemZero is escalating into targeted replay and regime testing"
                )
            if not should_run_wind_tunnel and self.config.db_url:
                should_run_wind_tunnel = True
                wind_budget = max(
                    wind_budget,
                    min(self.config.wind_tunnel_max_queries, 10 + len(assumption_findings) * 3),
                )
        return {
            "run_wind_tunnel": should_run_wind_tunnel,
            "run_chaos": should_run_chaos,
            "scope_assets": sorted(dict.fromkeys(a for a in scope_assets if a))[:24],
            "scope_tables": sorted(dict.fromkeys(t for t in scope_tables if t))[:12],
            "priority_nodes": priority_nodes[:12],
            "future_workload_required": require_future_workload,
            "debug_report_sections": [
                "gate",
                "wind_tunnel",
                "chaos",
                "runbook",
                "ecosystem",
                "roi",
            ],
            "regime_replay_required": require_future_workload,
            "stateful_recovery_required": should_run_chaos,
            "incremental_state_checks_required": incremental_state_checks_required,
            "domain_filter_drift_required": domain_filter_drift_required,
            "assumption_revalidation_required": assumption_revalidation_required,
            "contract_updates_required": contract_updates_required,
            "undocumented_assumption_types": undocumented_assumption_types[:8],
            "contract_recommendations": contract_recommendations[:8],
            "priority_assumption_nodes": priority_assumption_nodes[:8],
            "targeted_test_modes": targeted_test_modes[:8],
            "assumption_risk_score": round(assumption_risk_score, 2),
            "wind_tunnel_query_budget": wind_budget,
            "chaos_mutation_budget": chaos_budget,
            "scope_reduction_pct": scope_reduction_pct,
            "estimated_compute_minutes_saved_per_run": per_run_compute_minutes_saved,
            "estimated_review_minutes_saved_per_run": per_run_review_minutes_saved,
            "run_finops_review": bool(finops_summary.get("drivers")),
            "projected_weekly_compute_waste_usd": round(
                float(finops_summary.get("projected_weekly_cost_usd", 0.0) or 0.0), 2
            ),
            "projected_weekend_waste_usd": round(
                float(finops_summary.get("blocked_weekend_waste_usd", 0.0) or 0.0), 2
            ),
            "baseline_wind_tunnel_budget": baseline_wind_budget,
            "baseline_chaos_budget": baseline_chaos_budget,
            "reasons": reasons[:8],
        }

    def _build_finops_summary(self, result: GateResult) -> dict:
        summary = self._finops_analyser.analyse(
            focus_assets=[a.node_id for a in result.assessments]
        )
        payload = summary.to_dict()
        runtime = result.wind_tunnel_receipt or {}
        runtime_finops = runtime.get("finops_summary") if isinstance(runtime, dict) else None
        if runtime_finops:
            payload["runtime_validation"] = runtime_finops
            payload["confidence"] = runtime_finops.get(
                "confidence", payload.get("confidence", "medium")
            )
            payload["projected_weekly_cost_usd"] = round(
                max(
                    float(payload.get("projected_weekly_cost_usd", 0.0) or 0.0),
                    float(runtime_finops.get("projected_weekly_cost_usd", 0.0) or 0.0),
                ),
                2,
            )
            payload["projected_monthly_cost_usd"] = round(
                max(
                    float(payload.get("projected_monthly_cost_usd", 0.0) or 0.0),
                    float(runtime_finops.get("projected_monthly_cost_usd", 0.0) or 0.0),
                ),
                2,
            )
            payload["projected_weekly_dbu"] = round(
                max(
                    float(payload.get("projected_weekly_dbu", 0.0) or 0.0),
                    float(runtime_finops.get("projected_weekly_dbu", 0.0) or 0.0),
                ),
                2,
            )
            payload["blocked_weekend_waste_usd"] = round(
                max(
                    float(payload.get("blocked_weekend_waste_usd", 0.0) or 0.0),
                    float(runtime_finops.get("blocked_weekend_waste_usd", 0.0) or 0.0),
                ),
                2,
            )
        recompute_radius = max(
            result.total_blast_radius,
            len({asset for a in result.assessments for asset in a.affected_assets}),
        )
        payload["recompute_radius"] = max(
            int(payload.get("recompute_radius", 0) or 0), recompute_radius
        )
        if recompute_radius:
            multiplier = 1.0 + min(1.2, recompute_radius * 0.06)
            payload["projected_weekly_cost_usd"] = round(
                float(payload.get("projected_weekly_cost_usd", 0.0) or 0.0) * multiplier, 2
            )
            payload["projected_monthly_cost_usd"] = round(
                float(payload.get("projected_monthly_cost_usd", 0.0) or 0.0) * multiplier, 2
            )
            payload["blocked_weekend_waste_usd"] = round(
                float(payload.get("blocked_weekend_waste_usd", 0.0) or 0.0) * multiplier, 2
            )
            payload.setdefault("notes", []).append(
                f"Estimated recompute radius spans {recompute_radius} downstream asset(s); cost receipts were widened to reflect chain recomputation risk."
            )
        if result.verdict == Verdict.BLOCK:
            payload["blocked_by_gate"] = True
            payload["estimated_savings_usd"] = payload.get("blocked_weekend_waste_usd", 0.0)
        else:
            payload["blocked_by_gate"] = False
            payload["estimated_savings_usd"] = 0.0
        if payload.get("drivers"):
            payload.setdefault("notes", []).append(
                "SemZero FinOps Gate turned transformation-layer cost risks into a merge-time receipt before warehouse compute was burned."
            )
        return payload

    def _build_decision_summary(self, result: GateResult) -> dict:
        assumption_summary = (
            (result.assumption_summary or {}) if isinstance(result.assumption_summary, dict) else {}
        )
        finops_summary = (
            (result.finops_summary or {}) if isinstance(result.finops_summary, dict) else {}
        )
        receipt = result.wind_tunnel_receipt or {}
        chaos = result.chaos_report or {}
        evidence_counts = {
            "blocking_findings": len(result.blocking_assessments),
            "review_findings": len(result.review_assessments),
            "proof_references": len(
                (result.proof_bundle or {}).get("findings", [])
                if isinstance(result.proof_bundle, dict)
                else []
            ),
            "assumption_findings": int(assumption_summary.get("finding_count", 0) or 0),
            "finops_drivers": len(finops_summary.get("drivers", []) or []),
            "broken_queries": int(receipt.get("queries_broken", 0) or 0),
            "chaos_breaks": int(
                ((chaos.get("summary") or {}) if isinstance(chaos, dict) else {}).get(
                    "mutations_that_broke", 0
                )
                or 0
            ),
        }
        categories = []
        if any(a.compatibility == CompatibilityType.SEMANTIC_BREAKING for a in result.assessments):
            categories.append("semantic")
        if evidence_counts["assumption_findings"]:
            categories.append("assumptions")
        if (
            evidence_counts["broken_queries"]
            or evidence_counts["chaos_breaks"]
            or result.oncall_risk == "HIGH"
        ):
            categories.append("operational")
        if (
            float(finops_summary.get("projected_weekly_cost_usd", 0.0) or 0.0) > 0
            or evidence_counts["finops_drivers"]
        ):
            categories.append("financial")
        primary_reason = "Merge is safe at the current evidence level."
        if result.verdict == Verdict.BLOCK:
            primary_reason = "SemZero found enough evidence to block this merge until the highest-risk break paths are fixed or explicitly staged."
        elif result.verdict == Verdict.NEEDS_REVIEW:
            primary_reason = "SemZero found material merge risk that still needs human review, scoped replay, or contract updates before approval."
        highlights = []
        if categories:
            mapping = {
                "semantic": "Semantic break evidence suggests downstream meaning or comparability would change.",
                "assumptions": "Undocumented downstream assumptions were detected and should be promoted into explicit contracts or rollout notes.",
                "operational": "Runtime evidence points to fragile or already-broken paths under replay or chaos conditions.",
                "financial": "The FinOps Gate found compute-waste risk substantial enough to justify pre-merge intervention.",
            }
            highlights.extend(mapping[c] for c in categories[:4])
        if result.total_blast_radius:
            highlights.append(
                f"Blast radius reaches {result.total_blast_radius} downstream layer(s)."
            )
        if result.total_estimated_backfill_cost_usd:
            highlights.append(
                f"Rollback or backfill exposure is estimated at about ${result.total_estimated_backfill_cost_usd:,.0f}."
            )
        return {
            "verdict_label": result.verdict.value,
            "primary_reason": primary_reason,
            "risk_categories": categories,
            "confidence": "high"
            if result.reliability_score <= 55 or result.blocking_assessments
            else ("medium" if result.review_assessments else "moderate"),
            "evidence_counts": evidence_counts,
            "highlights": highlights[:8],
            "what_to_do_next": list(result.next_actions[:5]),
        }

    def _build_risk_register(self, result: GateResult) -> list[dict]:
        risks: list[dict] = []
        for assessment in sorted(
            result.assessments,
            key=lambda a: (
                a.compatibility not in _BLOCK_TYPES,
                -(a.graph_risk_score or 0.0),
                -a.confidence,
                -a.blast_radius,
            ),
        ):
            categories = []
            if assessment.compatibility == CompatibilityType.SEMANTIC_BREAKING:
                categories.append("semantic")
            if assessment.assumption_risks:
                categories.append("assumptions")
            if assessment.graph_risk_score >= 0.55 or assessment.blast_radius > 0:
                categories.append("operational")
            if assessment.estimated_backfill_cost_usd >= 35:
                categories.append("financial")
            if not categories:
                categories.append("structural")
            severity = (
                "critical"
                if assessment.compatibility in _BLOCK_TYPES
                else (
                    "high"
                    if assessment.compatibility in _REVIEW_TYPES
                    or assessment.graph_risk_score >= 0.7
                    else "medium"
                )
            )
            risks.append(
                {
                    "node_id": assessment.node_id,
                    "severity": severity,
                    "categories": categories,
                    "compatibility": assessment.compatibility.value,
                    "confidence": round(float(assessment.confidence), 3),
                    "blast_radius": assessment.blast_radius,
                    "estimated_backfill_cost_usd": round(
                        float(assessment.estimated_backfill_cost_usd or 0.0), 2
                    ),
                    "why_it_matters": (
                        assessment.predicted_failure_modes
                        or assessment.contract_violations
                        or [assessment.query_impact or "Downstream consumers are exposed."]
                    )[0],
                    "evidence": {
                        "proof_references": len(assessment.proof_evidence or []),
                        "assumption_findings": len(assessment.assumption_risks or []),
                        "graph_risk_score": round(float(assessment.graph_risk_score or 0.0), 3),
                    },
                    "affected_assets": list(assessment.affected_assets[:6]),
                    "recommended_fix": assessment.recommendation,
                }
            )
        finops = result.finops_summary or {}
        if finops.get("drivers"):
            top = (finops.get("drivers") or [{}])[0] or {}
            risks.append(
                {
                    "node_id": top.get("location") or "finops_gate",
                    "severity": "high"
                    if float(finops.get("projected_weekly_cost_usd", 0.0) or 0.0) >= 150
                    else "medium",
                    "categories": ["financial"],
                    "compatibility": "FINOPS_WASTE",
                    "confidence": 0.8,
                    "blast_radius": int(finops.get("recompute_radius", 0) or 0),
                    "estimated_backfill_cost_usd": round(
                        float(finops.get("blocked_weekend_waste_usd", 0.0) or 0.0), 2
                    ),
                    "why_it_matters": top.get("reason")
                    or "Projected transformation-layer compute waste is material before merge.",
                    "evidence": {
                        "drivers": len(finops.get("drivers", []) or []),
                        "projected_weekly_cost_usd": round(
                            float(finops.get("projected_weekly_cost_usd", 0.0) or 0.0), 2
                        ),
                    },
                    "affected_assets": list(
                        (result.recommended_execution or {}).get("scope_assets", [])[:6]
                    ),
                    "recommended_fix": "Rewrite or stage the highest-cost transform before merge and re-run the FinOps Gate receipt.",
                }
            )
        return risks[:12]

    def _build_remediation_blueprints(self, result: GateResult) -> list[dict]:
        blueprints: list[dict] = []
        exec_plan = result.recommended_execution or {}
        for assessment in result.blocking_assessments + result.review_assessments:
            validation_steps = []
            if exec_plan.get("run_wind_tunnel"):
                validation_steps.append("Re-run Wind Tunnel on the scoped assets after the fix.")
            if exec_plan.get("run_chaos"):
                validation_steps.append(
                    "Re-run targeted Chaos to prove fragile downstream paths no longer break."
                )
            if assessment.assumption_risks:
                validation_steps.append(
                    "Convert the highest-risk downstream assumptions into explicit tests, contracts, or rollout notes."
                )
            if any(item.get("filters") for item in assessment.proof_evidence):
                validation_steps.append(
                    "Update downstream hardcoded filters or status mappings that still assume the old behavior."
                )
            if not validation_steps:
                validation_steps.append(
                    "Re-run the SemZero gate after applying the smallest safe change."
                )
            categories = []
            if assessment.compatibility == CompatibilityType.SEMANTIC_BREAKING:
                categories.append("semantic")
            if assessment.assumption_risks:
                categories.append("assumptions")
            if assessment.graph_risk_score >= 0.55 or assessment.blast_radius > 0:
                categories.append("operational")
            if assessment.estimated_backfill_cost_usd >= 35:
                categories.append("financial")
            if not categories:
                categories.append("structural")
            blueprints.append(
                {
                    "node_id": assessment.node_id,
                    "categories": categories,
                    "root_cause": (
                        assessment.contract_violations
                        or assessment.predicted_failure_modes
                        or [assessment.change_type or assessment.compatibility.value]
                    )[0],
                    "smallest_safe_change": assessment.recommendation
                    or "Preserve backwards-compatible semantics, then stage the rollout.",
                    "confidence": "high"
                    if assessment.compatibility in _BLOCK_TYPES or assessment.confidence >= 0.85
                    else "medium",
                    "auto_open_pr_candidate": bool(
                        assessment.auto_patchable
                        or assessment.compatibility
                        in {
                            CompatibilityType.ADDITIVE_BREAKING,
                            CompatibilityType.NULLABLE_HARDENING,
                        }
                    ),
                    "validation_steps": validation_steps[:5],
                }
            )
        finops = result.finops_summary or {}
        if finops.get("drivers"):
            blueprints.append(
                {
                    "node_id": "finops_gate",
                    "categories": ["financial"],
                    "root_cause": (finops.get("drivers") or [{}])[0].get("reason")
                    or "A transformation-layer cost anti-pattern is projected to waste warehouse compute.",
                    "smallest_safe_change": "Reduce scan width, filter earlier, or stage the backfill before merge; then compare the new FinOps receipt against this one.",
                    "confidence": "high"
                    if float(finops.get("projected_weekly_cost_usd", 0.0) or 0.0) >= 120
                    else "medium",
                    "auto_open_pr_candidate": False,
                    "validation_steps": [
                        "Re-run the FinOps Gate and confirm projected weekly cost and weekend waste both drop.",
                        "Keep the cheaper plan as the new baseline in the savings ledger.",
                    ],
                }
            )
        return blueprints[:12]

    def _build_savings_ledger(self, result: GateResult) -> dict:
        finops = result.finops_summary or {}
        projected_weekly_cost = round(float(finops.get("projected_weekly_cost_usd", 0.0) or 0.0), 2)
        projected_monthly_cost = round(
            float(finops.get("projected_monthly_cost_usd", 0.0) or 0.0), 2
        )
        prevented_spend = round(float(finops.get("estimated_savings_usd", 0.0) or 0.0), 2)
        if result.verdict != Verdict.BLOCK:
            prevented_spend = round(
                max(
                    prevented_spend,
                    float(finops.get("blocked_weekend_waste_usd", 0.0) or 0.0) * 0.0,
                ),
                2,
            )
        recurring_patterns = []
        for driver in finops.get("drivers", []) or []:
            kind = str(driver.get("kind") or "").strip()
            if kind and kind not in recurring_patterns:
                recurring_patterns.append(kind)
        return {
            "gate_id": result.gate_id,
            "blocked_by_gate": bool(finops.get("blocked_by_gate")),
            "projected_weekly_cost_usd": projected_weekly_cost,
            "projected_monthly_cost_usd": projected_monthly_cost,
            "projected_weekend_waste_usd": round(
                float(finops.get("blocked_weekend_waste_usd", 0.0) or 0.0), 2
            ),
            "estimated_savings_usd": prevented_spend,
            "review_minutes_saved_per_run": round(
                float(
                    (result.recommended_execution or {}).get(
                        "estimated_review_minutes_saved_per_run", 0.0
                    )
                    or 0.0
                ),
                2,
            ),
            "compute_minutes_saved_per_run": round(
                float(
                    (result.recommended_execution or {}).get(
                        "estimated_compute_minutes_saved_per_run", 0.0
                    )
                    or 0.0
                ),
                2,
            ),
            "recurring_waste_patterns": recurring_patterns[:8],
            "summary": (
                f"SemZero projected ${projected_weekly_cost:,.0f}/week of compute risk and prevented about ${prevented_spend:,.0f} immediately by stopping this merge."
                if bool(finops.get("blocked_by_gate"))
                else f"SemZero projected ${projected_weekly_cost:,.0f}/week of compute risk and recorded it for remediation before it becomes a recurring spend leak."
            ),
        }

    def _build_iron_gate(self, result: GateResult) -> dict:
        should_fail = result.verdict == Verdict.BLOCK
        if self.config.iron_gate_block_review and result.verdict == Verdict.NEEDS_REVIEW:
            should_fail = True
        if result.total_estimated_backfill_cost_usd >= self.config.iron_gate_cost_threshold_usd:
            should_fail = True
        state = (
            "failure"
            if should_fail
            else ("pending" if result.verdict == Verdict.NEEDS_REVIEW else "success")
        )
        reasons = []
        if result.verdict == Verdict.BLOCK:
            reasons.append("blocking compatibility findings")
        if result.verdict == Verdict.NEEDS_REVIEW and self.config.iron_gate_block_review:
            reasons.append("review findings exceed Iron Gate policy")
        if result.total_estimated_backfill_cost_usd >= self.config.iron_gate_cost_threshold_usd:
            reasons.append("estimated rollback/backfill cost exceeds threshold")
        if (result.ecosystem_context or {}).get("looker", {}).get("impacted_assets"):
            reasons.append("business-facing consumption assets are in blast radius")
        github_payload = {
            "context": self.config.iron_gate_context,
            "state": state,
            "description": "; ".join(reasons[:3]) or "SemZero Iron Gate evaluation completed",
        }
        gitlab_state = (
            "failed"
            if should_fail
            else ("running" if result.verdict == Verdict.NEEDS_REVIEW else "success")
        )
        gitlab_payload = {
            "name": self.config.iron_gate_context,
            "state": gitlab_state,
            "target_url": "",
            "description": "; ".join(reasons[:3]) or "SemZero Iron Gate evaluation completed",
        }
        return {
            "context": self.config.iron_gate_context,
            "state": state,
            "should_block_merge": should_fail,
            "reasons": reasons[:5],
            "status_payloads": {
                "github": github_payload,
                "gitlab": gitlab_payload,
            },
        }

    def _augment_with_ecosystem_assets(self, node_id: str, affected_assets: list[str]) -> list[str]:
        assets = list(affected_assets)
        table_id = node_id.split(".")[0] if "." in node_id else node_id
        ecosystem = self._ecosystem_context.to_dict()
        looker = ecosystem.get("looker", {})
        dagster = ecosystem.get("dagster", {})
        for item in looker.get("impacted_assets", []):
            if table_id in item or node_id in item:
                assets.append(f"looker:{item}")
        for item in dagster.get("failing_assets", []):
            if table_id in item or node_id in item:
                assets.append(f"dagster:{item}")
        for item in ecosystem.get("dbt", {}).get("hot_assets", []):
            if table_id in item or node_id in item:
                assets.append(f"dbt:{item}")
        return list(dict.fromkeys(a for a in assets if a))[:10]

    def _build_next_actions(self, result: GateResult) -> list[str]:
        actions: list[str] = []
        for assessment in result.blocking_assessments + result.review_assessments:
            if assessment.recommendation:
                actions.append(assessment.recommendation)
            if assessment.rollout_strategy:
                actions.extend(assessment.rollout_strategy[:2])
            for item in assessment.proof_evidence[:1]:
                fix = item.get("suggested_fix")
                if fix:
                    actions.append(str(fix))
        exec_plan = result.recommended_execution or {}
        if exec_plan.get("assumption_revalidation_required"):
            actions.append(
                "Review and codify the undocumented downstream assumptions SemZero found before allowing the merge."
            )
        for hint in exec_plan.get("contract_recommendations", [])[:2]:
            actions.append(str(hint))
        for blueprint in (result.remediation_blueprints or [])[:2]:
            smallest = str(blueprint.get("smallest_safe_change", "")).strip()
            if smallest:
                actions.append(smallest)
        if exec_plan.get("run_finops_review"):
            actions.append(
                "Review the SemZero FinOps Gate receipt and rewrite the highest-cost transform before merge."
            )
        if exec_plan.get("run_wind_tunnel"):
            actions.append("Run Wind Tunnel on the scoped assets before approving the merge.")
        if exec_plan.get("run_chaos"):
            actions.append(
                "Run targeted Chaos on the scoped assets to catch silent downstream regressions."
            )
        if any(a.graph_risk_score >= 0.55 for a in result.assessments):
            actions.append(
                "Use the graph-ranked priority nodes first during triage; they are the most likely structural failure amplifiers."
            )
        if exec_plan.get("future_workload_required"):
            actions.append(
                "Include SemZero synthetic future-workload checks before merge to catch unreplayed edge cases."
            )
        if exec_plan.get("incremental_state_checks_required"):
            actions.append(
                "Run SemZero incremental state-reconciliation checks to prove deletes, duplicates, and late arrivals do not corrupt retained state."
            )
        if exec_plan.get("undocumented_assumption_types"):
            actions.append(
                "Promote the highest-risk downstream assumptions into explicit contracts, tests, or rollout notes so future merges stop relying on tribal knowledge."
            )
        if not actions:
            actions.append(
                "Merge is safe at the current evidence level; keep monitoring the affected outputs after deploy."
            )
        deduped: list[str] = []
        seen = set()
        for action in actions:
            key = action.strip().lower()
            if key and key not in seen:
                deduped.append(action.strip())
                seen.add(key)
        return deduped[:8]

    def run_wind_tunnel(
        self,
        result: GateResult,
        migration_sql: str = "",
        drift_report: Optional[dict] = None,
        graph_json: Optional[dict] = None,
    ) -> GateResult:
        """
        Run Wind Tunnel replay and attach the simulation receipt to the gate result.
        Call this after evaluate() when the verdict is not SAFE, or always for
        high-risk migrations.

        Args:
            result:        GateResult from evaluate()
            migration_sql: Raw migration DDL string
            drift_report:  SemZero drift format (alternative to raw SQL)
            graph_json:    Schema graph (improves synthetic query generation)

        Returns:
            Same GateResult with simulation_summary populated.
        """
        if not self.config.run_wind_tunnel:
            log.info("Wind Tunnel disabled in config (run_wind_tunnel=False)")
            return result

        if not self.config.db_url:
            log.warning("Wind Tunnel skipped: GateConfig.db_url is not set")
            result.simulation_summary = (
                "### ℹ️ Wind Tunnel Not Configured\n\n"
                "> Set `db_url` in GateConfig to enable simulation replay."
            )
            return result

        try:
            try:
                from ..chaos.wind_tunnel import MigrationWindTunnel, WindTunnelConfig
            except ImportError:
                from chaos.wind_tunnel import MigrationWindTunnel, WindTunnelConfig

            log.info("Gate: launching Wind Tunnel simulation…")
            try:
                from ..utils.live_readiness import build_live_readiness_report, resolve_live_mode
            except ImportError:
                from utils.live_readiness import build_live_readiness_report, resolve_live_mode

            readiness = build_live_readiness_report(self.config.db_url)
            dry_run, mode_warnings = resolve_live_mode(
                self.config.wind_tunnel_live_mode,
                readiness.dialect,
                readiness.clone_supported,
            )
            if self.config.wind_tunnel_dry_run:
                dry_run = True

            wt_config = WindTunnelConfig(
                db_url=self.config.db_url,
                max_queries=self.config.wind_tunnel_max_queries,
                query_timeout_s=self.config.wind_tunnel_query_timeout,
                dry_run=dry_run,
                auto_destroy_clone=not self.config.wind_tunnel_keep_clone,
                data_dir=self.config.data_dir,
                post_to_pr=False,  # Gate handles PR posting
            )
            tunnel = MigrationWindTunnel(wt_config)
            receipt = tunnel.run(
                migration_sql=migration_sql,
                drift_report=drift_report,
                graph_json=graph_json or self.graph_json,
            )

            result.simulation_summary = receipt.to_pr_comment()
            result.wind_tunnel_receipt = receipt.to_dict()
            for warning in mode_warnings:
                result.review_reasons.append(warning)

            # Escalate: if Wind Tunnel finds hard breakage on NEEDS_REVIEW → BLOCK
            try:
                from ..chaos.wind_tunnel import TunnelVerdict
            except ImportError:
                from chaos.wind_tunnel import TunnelVerdict
            if receipt.verdict == TunnelVerdict.BLOCKED and result.verdict == Verdict.NEEDS_REVIEW:
                log.warning("Wind Tunnel found broken queries — escalating verdict to BLOCK")
                result.verdict = Verdict.BLOCK
                result.blocked_by.append(
                    f"Wind Tunnel: {receipt.queries_broken} quer"
                    f"{'y' if receipt.queries_broken == 1 else 'ies'} "
                    f"fail after migration (confidence {receipt.confidence_score}%)"
                )

            receipt.save(f"{self.config.data_dir}/wind_tunnel_{result.gate_id}.json")
            log.info(
                f"Wind Tunnel complete: {receipt.verdict.value} | "
                f"{receipt.queries_broken} broken, "
                f"{receipt.queries_mismatch} row-mismatch"
            )

        except Exception as exc:
            log.error(f"Wind Tunnel failed (non-fatal): {exc}", exc_info=True)
            result.simulation_summary = (
                f"### ❓ Wind Tunnel — Could Not Complete\n\n> {str(exc)[:400]}"
            )
        return result

    # ── GitHub integration ────────────────────────────────────────────────────

    def post_to_pr(self, result: GateResult) -> bool:
        """Post verdict as PR comment and set CI status check."""
        if not self.config.github_token or not self.config.github_repo:
            log.warning("GitHub not configured — skipping PR post.")
            return False
        if result.pr_number is None:
            log.warning("No PR number — skipping PR post.")
            return False

        try:
            comment_body = self._build_pr_comment(result)
            self._post_comment(result.pr_number, comment_body)
            self._set_status_check(result)
            if result.verdict != Verdict.SAFE and self.config.data_owner_team:
                self._request_team_review(result.pr_number, self.config.data_owner_team)
            self._add_labels(result)
            log.info(f"Gate result posted to PR #{result.pr_number}: {result.verdict.value}")
            return True
        except Exception as exc:
            log.error(f"Failed to post gate result: {exc}", exc_info=True)
            return False

    def open_patch_prs(self, result: GateResult, repair_plan: dict) -> list[str]:
        """Open consumer patch PRs for auto-patchable assessments."""
        patchable = [a for a in result.assessments if a.auto_patchable]
        if not patchable:
            return []
        try:
            from .github_pr import PRBot

            patch_events = [
                {
                    "change_type": a.change_type,
                    "severity": "LOW",
                    "node_id": a.node_id,
                    "detail": a.recommendation,
                }
                for a in patchable
            ]
            patch_drift = {
                "detected_at": result.evaluated_at,
                "summary": {"total_changes": len(patch_events), "is_clean": False},
                "events": patch_events,
            }
            bot = PRBot(repo=self.config.github_repo, token=self.config.github_token)
            pr_result = bot.open_pr(patch_drift, repair_plan, "")
            if pr_result.success:
                result.patch_prs_opened.append(pr_result.pr_url)
                return [pr_result.pr_url]
        except Exception as exc:
            log.error(f"Consumer patch PR failed: {exc}")
        return []

    # ── PR comment builder ────────────────────────────────────────────────────

    def _build_pr_comment(self, result: GateResult) -> str:
        from .pr_comments import MergeCommentRenderer

        renderer = MergeCommentRenderer()
        return renderer.render(
            result,
            wind_tunnel_receipt=result.wind_tunnel_receipt or None,
            chaos_report=result.chaos_report or None,
        )

    # ── GitHub API helpers ────────────────────────────────────────────────────

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.config.github_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _post_comment(self, pr_number: int, body: str) -> None:
        import requests

        existing = self._find_existing_comment(pr_number)
        if existing:
            requests.patch(
                f"{GITHUB_API}/repos/{self.config.github_repo}/issues/comments/{existing}",
                headers=self._headers(),
                json={"body": body},
                timeout=15,
            )
        else:
            requests.post(
                f"{GITHUB_API}/repos/{self.config.github_repo}/issues/{pr_number}/comments",
                headers=self._headers(),
                json={"body": body},
                timeout=15,
            )

    def _find_existing_comment(self, pr_number: int) -> Optional[int]:
        import requests

        r = requests.get(
            f"{GITHUB_API}/repos/{self.config.github_repo}/issues/{pr_number}/comments",
            headers=self._headers(),
            timeout=15,
        )
        if r.status_code != 200:
            return None
        for comment in r.json():
            body = comment.get("body", "")
            if "SemZero Change Gate" in body or "SemZero Merge Comment" in body:
                return comment["id"]
        return None

    def _set_status_check(self, result: GateResult) -> None:
        import requests

        state_map = {
            Verdict.SAFE: "success",
            Verdict.NEEDS_REVIEW: "pending",
            Verdict.BLOCK: "failure",
        }
        iron_gate = result.iron_gate or {}
        desc_map = {
            Verdict.SAFE: "All schema changes are safe to merge",
            Verdict.NEEDS_REVIEW: "Data Platform review required before merge",
            Verdict.BLOCK: f"Breaking changes — {len(result.blocked_by)} issue(s)",
        }
        pr_resp = requests.get(
            f"{GITHUB_API}/repos/{self.config.github_repo}/pulls/{result.pr_number}",
            headers=self._headers(),
            timeout=15,
        )
        if pr_resp.status_code != 200:
            return
        sha = pr_resp.json().get("head", {}).get("sha", "")
        if not sha:
            return
        requests.post(
            f"{GITHUB_API}/repos/{self.config.github_repo}/statuses/{sha}",
            headers=self._headers(),
            json={
                "state": iron_gate.get("state") or state_map[result.verdict],
                "description": (iron_gate.get("reasons") or [desc_map[result.verdict]])[0][:140],
                "context": iron_gate.get("context") or "semzero/change-gate",
                "target_url": (
                    f"https://github.com/{self.config.github_repo}/pull/{result.pr_number}"
                ),
            },
            timeout=15,
        )

    def _request_team_review(self, pr_number: int, team_slug: str) -> None:
        import requests

        requests.post(
            f"{GITHUB_API}/repos/{self.config.github_repo}/pulls/{pr_number}/requested_reviewers",
            headers=self._headers(),
            json={"team_reviewers": [team_slug]},
            timeout=15,
        )

    def _add_labels(self, result: GateResult) -> None:
        import requests

        label_map = {
            Verdict.SAFE: ["semzero/safe"],
            Verdict.NEEDS_REVIEW: ["semzero/needs-review", "semzero/drift"],
            Verdict.BLOCK: ["semzero/high-risk", "semzero/drift"],
        }
        colours = {
            "semzero/safe": "22c55e",
            "semzero/needs-review": "f59e0b",
            "semzero/high-risk": "ef4444",
            "semzero/drift": "6366f1",
        }
        labels = label_map.get(result.verdict, [])
        for name in labels:
            requests.post(
                f"{GITHUB_API}/repos/{self.config.github_repo}/labels",
                headers=self._headers(),
                json={"name": name, "color": colours.get(name, "64748b")},
                timeout=10,
            )
        if labels:
            requests.post(
                f"{GITHUB_API}/repos/{self.config.github_repo}/issues/{result.pr_number}/labels",
                headers=self._headers(),
                json={"labels": labels},
                timeout=15,
            )

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _run_ast_proof(self, drift_report: dict):
        if not self.config.proof_enabled:
            return None
        source_paths = [str(p) for p in self.config.proof_source_paths if p]
        if not source_paths:
            return None
        try:
            from .ast_proofing import ASTChangeProver

            prover = ASTChangeProver(
                self.graph_json,
                source_paths=source_paths,
                max_files=self.config.proof_max_files,
                boundary_hops=self.config.proof_boundary_hops,
            )
            return prover.prove(drift_report)
        except Exception as exc:
            log.warning("AST-first proofing failed: %s", exc)
            return None

    def _compute_blast_reports(self, events: list[dict]) -> dict[str, dict]:
        try:
            from ..analytics.impact import BlastRadiusAnalyzer
            from ..utils.errors import UnknownNodeError
        except ImportError:
            from analytics.impact import BlastRadiusAnalyzer
            from utils.errors import UnknownNodeError
        reports: dict[str, dict] = {}
        try:
            analyzer = BlastRadiusAnalyzer(self.graph_json)
            for event in events:
                node_id = event.get("node_id", "")
                if not node_id:
                    continue
                try:
                    report = analyzer.analyze(node_id)
                    reports[node_id] = report.to_dict()
                except (UnknownNodeError, Exception):
                    reports[node_id] = {"summary": {}, "impacted_nodes": []}
        except Exception as exc:
            log.warning(f"Blast report computation failed: {exc}")
        return reports

    def _table_for_node(self, node_id: str):
        table_id = node_id.split(".")[0] if "." in node_id else node_id
        return self._table_contracts.get(table_id)

    def _column_for_node(self, node_id: str):
        return self._column_contracts.get(node_id)

    def _query_frequency(self, node_id: str) -> int:
        nodes = {n["id"]: n for n in self.graph_json.get("nodes", [])}
        table_id = node_id.split(".")[0] if "." in node_id else node_id
        table = nodes.get(table_id, {})
        return int(table.get("query_frequency", 0) or 0)

    def _contract_violations(
        self, node_id: str, compat: CompatibilityType, event: dict
    ) -> list[str]:
        violations: list[str] = []
        table_contract = self._table_for_node(node_id)
        column_contract = self._column_for_node(node_id)
        if compat == CompatibilityType.DESTRUCTIVE_DELETE:
            violations.append("Removes a published schema surface consumed downstream.")
        if (
            compat in {CompatibilityType.TYPE_NARROWING, CompatibilityType.SEMANTIC_BREAKING}
            and column_contract is not None
        ):
            violations.append(
                f"Downstream contract expects `{column_contract.dtype}` semantics for `{column_contract.name}`."
            )
        if (
            compat == CompatibilityType.NULLABLE_HARDENING
            and column_contract is not None
            and "not_null" in getattr(column_contract, "tests", [])
        ):
            violations.append(
                f"Existing contract already treats `{column_contract.name}` as not-null; writers must be updated before merge."
            )
        if compat == CompatibilityType.DATA_REGRESSION and column_contract is not None:
            if "not_null" in getattr(column_contract, "tests", []):
                violations.append(
                    f"Quality contract for `{column_contract.name}` would fail under the observed null/cardinality drift."
                )
            if "unique" in getattr(column_contract, "tests", []):
                violations.append(
                    f"Uniqueness expectations on `{column_contract.name}` are at risk under the observed drift."
                )
        if (
            column_contract is not None
            and getattr(column_contract, "pii_tags", None)
            and compat != CompatibilityType.ADDITIVE_SAFE
        ):
            violations.append(
                f"`{column_contract.name}` carries privacy tags ({', '.join(column_contract.pii_tags)}); changes require stewardship review before merge."
            )
        if (
            table_contract is not None
            and getattr(table_contract, "strictness", "") == "STRICT"
            and compat != CompatibilityType.ADDITIVE_SAFE
        ):
            violations.append(
                f"Parent table `{table_contract.table_id}` is inferred as STRICT and should not change without rollout controls."
            )
        if (
            table_contract is not None
            and getattr(table_contract, "sla_freshness", "") in {"1 hour", "1 day"}
            and compat
            in {
                CompatibilityType.DESTRUCTIVE_DELETE,
                CompatibilityType.SEMANTIC_BREAKING,
                CompatibilityType.DATA_REGRESSION,
            }
        ):
            violations.append(
                f"`{table_contract.table_id}` carries a freshness SLA of {table_contract.sla_freshness}; this change needs replay and contract sign-off."
            )
        return violations[:5]

    def _predict_failure_modes(
        self, node_id: str, compat: CompatibilityType, affected_assets: list[str], event: dict
    ) -> list[str]:
        failures: list[str] = []
        asset_preview = ", ".join(f"`{a}`" for a in affected_assets[:3])
        if compat == CompatibilityType.DESTRUCTIVE_DELETE:
            failures.append(
                "Hard SQL compilation/runtime failures in downstream models selecting the removed asset."
            )
        elif compat == CompatibilityType.TYPE_NARROWING:
            failures.append(
                "Cast, aggregation, and serialization failures where consumers still expect the wider/original type."
            )
        elif compat == CompatibilityType.NULLABLE_HARDENING:
            failures.append(
                "Insert / merge paths can fail immediately if any writer still omits this field."
            )
        elif compat == CompatibilityType.DATA_REGRESSION:
            failures.append(
                "Silent inner-join row loss and alert noise if null-rate or cardinality has regressed."
            )
        elif compat == CompatibilityType.SEMANTIC_BREAKING:
            failures.append(
                "Dashboards and KPI models can stay green while returning different business meaning."
            )
            old_dtype = str((event.get("before") or {}).get("dtype", ""))
            new_dtype = str((event.get("after") or {}).get("dtype", ""))
            if self.oracle._is_timezone_boundary_change(old_dtype, new_dtype):
                failures.append(
                    "Timezone-aware to timezone-naive casts can shift daily aggregates and duplicate or drop region-sensitive records."
                )
        elif compat in {
            CompatibilityType.RENAME_HIGH_CONFIDENCE,
            CompatibilityType.RENAME_LOW_CONFIDENCE,
        }:
            failures.append(
                "Column-resolution failures until readers dual-read or compatibility aliases are in place."
            )
        if asset_preview:
            failures.append(f"Most likely first-order impact lands on {asset_preview}.")
        detail = (event.get("detail") or "")[:140]
        if detail and compat == CompatibilityType.TYPE_NARROWING:
            failures.append(f"Change detail indicates cast-sensitive rollout: `{detail}`.")
        return failures[:4]

    def _estimate_backfill_cost_usd(
        self, node_id: str, compat: CompatibilityType, blast_radius: int, event: dict
    ) -> float:
        cost = self.config.base_backfill_cost_usd + (
            blast_radius * self.config.downstream_node_cost_usd
        )
        freq = self._query_frequency(node_id)
        if freq >= self.config.high_usage_query_threshold:
            cost *= self.config.high_usage_cost_multiplier
        table_contract = self._table_for_node(node_id)
        if table_contract is not None and getattr(table_contract, "criticality", "") == "PRIVATE":
            cost *= self.config.private_table_cost_multiplier
        if table_contract is not None and getattr(table_contract, "strictness", "") == "STRICT":
            cost *= self.config.strict_contract_cost_multiplier
        if compat in {CompatibilityType.DESTRUCTIVE_DELETE, CompatibilityType.SEMANTIC_BREAKING}:
            cost *= 1.5
        elif compat in {CompatibilityType.TYPE_NARROWING, CompatibilityType.DATA_REGRESSION}:
            cost *= 1.25
        before = event.get("before") or {}
        after = event.get("after") or {}
        null_delta = max(
            0.0, float(after.get("null_rate", 0) or 0) - float(before.get("null_rate", 0) or 0)
        )
        if null_delta:
            cost *= 1 + min(0.5, null_delta)
        return round(cost, 2)

    def _compute_verdict(
        self, assessments: list[ChangeAssessment]
    ) -> tuple[Verdict, list[str], list[str]]:
        blocked_by: list[str] = []
        review_reasons: list[str] = []
        for assessment in assessments:
            if assessment.compatibility in _BLOCK_TYPES and self._should_block(assessment):
                blocked_by.append(
                    f"`{assessment.node_id}`: {assessment.compatibility.value} — {assessment.recommendation[:100]}"
                )
            elif assessment.compatibility in _REVIEW_TYPES:
                review_reasons.append(f"`{assessment.node_id}`: {assessment.compatibility.value}")
        if blocked_by:
            return Verdict.BLOCK, blocked_by, review_reasons
        if review_reasons:
            return Verdict.NEEDS_REVIEW, blocked_by, review_reasons
        return Verdict.SAFE, [], []

    def _should_block(self, assessment: ChangeAssessment) -> bool:
        compat = assessment.compatibility
        if compat == CompatibilityType.DESTRUCTIVE_DELETE:
            return self.config.block_on_destructive
        if compat == CompatibilityType.TYPE_NARROWING:
            return self.config.block_on_narrowing
        if compat == CompatibilityType.DATA_REGRESSION:
            return self.config.block_on_data_regression or self.config.strict_mode
        return compat == CompatibilityType.SEMANTIC_BREAKING

    def _confidence(self, compat: CompatibilityType, blast_report: dict) -> float:
        blast_summary = blast_report.get("summary", {})
        blast_radius = blast_summary.get("total_impacted", 0)
        cascade = float(blast_summary.get("cascade_score", 0.0) or 0.0)
        base = {
            CompatibilityType.ADDITIVE_SAFE: 0.97,
            CompatibilityType.TYPE_WIDENING: 0.95,
            CompatibilityType.ADDITIVE_BREAKING: 0.88,
            CompatibilityType.NULLABLE_HARDENING: 0.84,
            CompatibilityType.RENAME_HIGH_CONFIDENCE: 0.82,
            CompatibilityType.RENAME_LOW_CONFIDENCE: 0.62,
            CompatibilityType.TYPE_NARROWING: 0.93,
            CompatibilityType.DESTRUCTIVE_DELETE: 0.96,
            CompatibilityType.DATA_REGRESSION: 0.8,
            CompatibilityType.SEMANTIC_BREAKING: 0.76,
        }.get(compat, 0.8)
        penalty = min(0.18, blast_radius * 0.015 + cascade * 0.1)
        return max(0.3, round(base - penalty, 3))

    def _is_auto_patchable(self, compat: CompatibilityType) -> bool:
        return (
            compat
            in {
                CompatibilityType.RENAME_HIGH_CONFIDENCE,
                CompatibilityType.ADDITIVE_SAFE,
                CompatibilityType.TYPE_WIDENING,
            }
            and self.config.auto_patch_consumers
        )

    def _recommendation(self, compat: CompatibilityType, event: dict, blast_radius: int) -> str:
        node_id = event.get("node_id", "")
        after = event.get("after") or {}
        before = event.get("before") or {}
        if compat == CompatibilityType.ADDITIVE_BREAKING:
            if after.get("default") not in (None, "", "NULL"):
                return f"`{node_id}` is NOT NULL but has a DEFAULT. Verify backfill and write paths, then merge with review."
            return f"`{node_id}` is a NOT NULL addition without a DEFAULT. Roll it out as nullable/backfilled first."
        if compat == CompatibilityType.NULLABLE_HARDENING:
            null_rate = before.get("null_rate")
            if null_rate is not None:
                return f"`{node_id}` is becoming NOT NULL. Current null_rate={null_rate:.1%}; backfill NULLs before merge."
            return f"`{node_id}` is becoming NOT NULL. Verify existing NULLs and update insert paths before merging."
        if compat == CompatibilityType.DESTRUCTIVE_DELETE:
            return f"Dropping `{node_id}` removes an interface used by {blast_radius} downstream asset(s). Keep a compatibility alias or staged deprecation window."
        if compat == CompatibilityType.TYPE_NARROWING:
            old_dtype = str(before.get("dtype", ""))
            new_dtype = str(after.get("dtype", ""))
            if self.oracle._is_varchar_length_narrowing(old_dtype, new_dtype):
                return f"`{node_id}` narrows string length ({old_dtype} → {new_dtype}). Validate silent truncation against production-length values before merge."
            return f"`{node_id}` is being narrowed. Validate cast failures and truncate risk before applying the migration."
        if compat == CompatibilityType.SEMANTIC_BREAKING:
            old_dtype = str(before.get("dtype", ""))
            new_dtype = str(after.get("dtype", ""))
            if self.oracle._is_timezone_boundary_change(old_dtype, new_dtype):
                return f"`{node_id}` crosses a timezone boundary ({old_dtype} → {new_dtype}). Keep UTC semantics explicit and replay temporal aggregates before merge."
            return f"`{node_id}` changes semantic meaning, not just structure. Introduce a new field/versioned contract rather than replacing in place."
        if compat == CompatibilityType.DATA_REGRESSION:
            old_null = float(before.get("null_rate", 0) or 0)
            new_null = float(after.get("null_rate", 0) or 0)
            old_card = float(before.get("cardinality", 0) or 0)
            new_card = float(after.get("cardinality", 0) or 0)
            before_values = {
                str(v).lower() for v in before.get("sample_values", []) if v is not None
            }
            after_values = {str(v).lower() for v in after.get("sample_values", []) if v is not None}
            added_values = sorted(after_values - before_values)
            if added_values:
                return f"`{node_id}` now includes new domain values ({', '.join(added_values[:4])}). Review hardcoded downstream filters and KPI bucketing before merge."
            return (
                f"`{node_id}` shows data-shape drift (null_rate {old_null:.1%}→{new_null:.1%}, "
                f"cardinality {old_card:.1%}→{new_card:.1%}). Review freshness/backfill quality before merge."
            )
        if compat == CompatibilityType.RENAME_HIGH_CONFIDENCE:
            return f"`{node_id}` looks like a safe rename, but use dual-read or aliasing until downstream traffic on the old name reaches zero."
        if compat == CompatibilityType.RENAME_LOW_CONFIDENCE:
            return f"`{node_id}` looks like a rename with weak confidence. Confirm intent with the owner and patch consumers explicitly."
        if compat == CompatibilityType.TYPE_WIDENING:
            return f"`{node_id}` is a widening change. Merge is generally safe, but update strict schemas/ORM models that pin exact types."
        return "Safe additive change. Document it and add coverage where appropriate."

    def _query_impact(self, node_id: str) -> str:
        nodes = {n["id"]: n for n in self.graph_json.get("nodes", [])}
        table_id = node_id.split(".")[0] if "." in node_id else node_id
        table = nodes.get(table_id, {})
        freq = int(table.get("query_frequency", 0) or 0)
        if freq == 0:
            return "No query history available"
        if freq >= self.config.high_usage_query_threshold:
            return f"HIGH usage — ~{freq:,} queries/day on parent table"
        if freq >= self.config.medium_usage_query_threshold:
            return f"MEDIUM usage — ~{freq:,} queries/day on parent table"
        return f"LOW usage — ~{freq:,} queries/day on parent table"
