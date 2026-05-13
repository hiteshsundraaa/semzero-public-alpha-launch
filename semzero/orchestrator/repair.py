"""
repair.py — Autonomous database repair and self-healing engine.

Consumes DriftEvents + ColumnMatches to autonomously generate idempotent
SQL migration scripts and dbt model patches.

Built for zero-downtime environments: all repairs are generated inside
transaction blocks, and high-severity changes require explicit manual sign-off.
"""

from __future__ import annotations

import logging
import textwrap
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Any, Dict, List

try:
    from ..crawler.drift import DriftEvent, ChangeType, Severity
except ImportError:
    from crawler.drift import DriftEvent, ChangeType, Severity

log = logging.getLogger(__name__)

# Config: At what severity do we stop auto-executing and demand a human?
AUTO_EXECUTE_MAX_SEVERITY = Severity.MEDIUM


class RepairEngineError(Exception):
    """Base exception for repair generation failures."""

    pass


class MalformedDriftEventError(RepairEngineError):
    """Raised when a DriftEvent lacks required metadata to safely generate SQL."""

    pass


class RepairStrategy(str, Enum):
    RENAME_COLUMN = "RENAME_COLUMN"
    CAST_COLUMN = "CAST_COLUMN"
    ADD_COLUMN = "ADD_COLUMN"
    DROP_COLUMN = "DROP_COLUMN"
    COALESCE_GUARD = "COALESCE_GUARD"
    DBT_PATCH = "DBT_PATCH"
    MANUAL_REQUIRED = "MANUAL_REQUIRED"


@dataclass
class RepairAction:
    drift_event: DriftEvent
    strategy: RepairStrategy
    sql: Optional[str] = None
    dbt_patch: Optional[str] = None
    approval_required: bool = True
    confidence: float = 1.0
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy": self.strategy.value,
            "severity": self.drift_event.severity.value,
            "node_id": self.drift_event.node_id,
            "approval_required": self.approval_required,
            "confidence": round(self.confidence, 4),
            "sql": self.sql,
            "dbt_patch": self.dbt_patch,
            "notes": self.notes,
        }


@dataclass
class RepairPlan:
    """
    An executable manifest of all repairs needed to resolve a DriftReport.
    Includes transaction-safe SQL rendering.
    """

    actions: List[RepairAction] = field(default_factory=list)

    @property
    def auto_executable(self) -> List[RepairAction]:
        return [a for a in self.actions if not a.approval_required]

    @property
    def needs_approval(self) -> List[RepairAction]:
        return [a for a in self.actions if a.approval_required]

    def summary(self) -> Dict[str, Any]:
        return {
            "total_actions": len(self.actions),
            "auto_executable_count": len(self.auto_executable),
            "needs_approval_count": len(self.needs_approval),
            "strategies_used": list({a.strategy.value for a in self.actions}),
        }

    def render_sql_script(self) -> str:
        """
        Renders an enterprise-grade, transaction-safe SQL migration script.
        """
        if not self.actions:
            return "-- No SQL repairs required."

        lines = [
            "-- ============================================================",
            "-- SemZero Autonomous Migration Script",
            "-- Execution Mode: TRANSACTIONAL",
            "-- Note: Review all [APPROVAL_REQUIRED] blocks before running.",
            "-- ============================================================",
            "BEGIN;\n",
        ]

        for action in self.actions:
            if action.sql:
                tag = "AUTO-EXECUTABLE" if not action.approval_required else "APPROVAL_REQUIRED"
                lines.append(f"-- [{tag}] Node: {action.drift_event.node_id}")
                lines.append(
                    f"-- Strategy: {action.strategy.value} | Confidence: {action.confidence:.0%}"
                )
                lines.append(f"-- Notes: {action.notes}")
                lines.append(textwrap.indent(action.sql.strip(), prefix="    "))
                lines.append("")

        lines.append("COMMIT;\n")
        lines.append("-- ROLLBACK; -- Uncomment and run if the above transaction fails.")

        return "\n".join(lines)


class RepairEngine:
    """
    The brain of SemZero's self-healing loop.
    Parses structural drift and outputs deterministic, safe repair plans.
    """

    def __init__(self, match_map: Optional[Dict[str, str]] = None):
        """
        Args:
            match_map: Optional mapping of {source_col: target_col} from the Semantic Matcher
                       used to confidently auto-resolve renaming vs. dropping.
        """
        self.match_map = match_map or {}

        # Map ChangeTypes to their specific handler methods
        self._handlers = {
            ChangeType.COLUMN_RENAMED: self._repair_column_rename,
            ChangeType.TYPE_CHANGED: self._repair_type_change,
            ChangeType.COLUMN_REMOVED: self._repair_column_removed,
            ChangeType.COLUMN_ADDED: self._repair_column_added,
            ChangeType.NULLABLE_CHANGED: self._repair_nullable_change,
            ChangeType.STATS_DRIFTED: self._repair_stats_drift,
            ChangeType.TABLE_RENAMED: self._repair_table_rename,
            ChangeType.TABLE_REMOVED: self._repair_table_removed,
            ChangeType.TABLE_ADDED: self._repair_table_added,
        }

    def plan(self, events: List[DriftEvent]) -> RepairPlan:
        """
        Main entry point. Generates a full RepairPlan from a list of DriftEvents.
        (Renamed from build_plan to standard 'plan' to match orchestration APIs).
        """
        plan_output = RepairPlan()

        for event in sorted(events, key=lambda e: e.severity.value):
            try:
                handler = self._handlers.get(event.change_type)
                if handler:
                    action = handler(event)
                    if action:
                        plan_output.actions.append(action)
                else:
                    log.warning(f"No repair handler registered for {event.change_type}")
            except MalformedDriftEventError as e:
                log.error(f"Failed to generate repair for {event.node_id}: {str(e)}")
            except Exception as e:
                log.error(f"Unexpected error processing {event.node_id}: {str(e)}")

        # Sort: Safe auto-executable items first, high-risk manual items last
        plan_output.actions.sort(key=lambda a: (a.approval_required, a.drift_event.severity.value))

        log.info(
            f"Generated Repair Plan: {len(plan_output.actions)} total actions "
            f"({len(plan_output.auto_executable)} auto-safe)."
        )
        return plan_output

    # ------------------------------------------------------------------ #
    #  Internal Handlers & SQL Generators                                  #
    # ------------------------------------------------------------------ #

    def _requires_approval(self, event: DriftEvent) -> bool:
        """Determines if a human must sign off based on the severity threshold."""
        severity_order = [Severity.LOW, Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL]
        try:
            return severity_order.index(event.severity) > severity_order.index(
                AUTO_EXECUTE_MAX_SEVERITY
            )
        except ValueError:
            return True  # Default to safe if severity is unrecognized

    def _extract_meta(self, event: DriftEvent, key: str, dict_key: str = "before") -> str:
        """Safely extracts metadata, raising an error if critical data is missing."""
        data = getattr(event, dict_key, {}) or {}
        val = data.get(key)
        if not val:
            # Fallback heuristics
            if key == "table":
                return event.node_id.split(".")[0]
            if key == "name":
                return event.node_id.split(".")[-1]
            raise MalformedDriftEventError(
                f"Missing critical metadata '{key}' in event {dict_key} state."
            )
        return val

    def _repair_column_rename(self, event: DriftEvent) -> RepairAction:
        table = self._extract_meta(event, "table", "before")
        old_name = self._extract_meta(event, "name", "before")

        # Check semantic matcher first, fallback to basic drift payload
        new_name = self.match_map.get(event.node_id) or self._extract_meta(event, "name", "after")
        confidence = 0.98 if event.node_id in self.match_map else 0.75

        sql = textwrap.dedent(f"""
            ALTER TABLE IF EXISTS "{table}"
            RENAME COLUMN "{old_name}" TO "{new_name}";
        """).strip()

        dbt_patch = textwrap.dedent(f"""
            # dbt schema.yml patch for model: {table}
            # ACTION REQUIRED: Update column reference
            # - name: {old_name}  <-- REMOVE
            # - name: {new_name}  <-- ADD
        """).strip()

        return RepairAction(
            drift_event=event,
            strategy=RepairStrategy.RENAME_COLUMN,
            sql=sql,
            dbt_patch=dbt_patch,
            approval_required=self._requires_approval(event),
            confidence=confidence,
            notes=f"Renaming '{old_name}' to '{new_name}'. Update downstream BI tools.",
        )

    def _repair_type_change(self, event: DriftEvent) -> RepairAction:
        table = self._extract_meta(event, "table", "before")
        col = self._extract_meta(event, "name", "before")
        old_type = self._extract_meta(event, "dtype_raw", "before")
        new_type = self._extract_meta(event, "dtype_raw", "after")

        sql = textwrap.dedent(f"""
            ALTER TABLE IF EXISTS "{table}"
            ALTER COLUMN "{col}" TYPE {new_type}
            USING "{col}"::{new_type};
        """).strip()

        return RepairAction(
            drift_event=event,
            strategy=RepairStrategy.CAST_COLUMN,
            sql=sql,
            approval_required=True,  # Type casting is always high-risk
            confidence=0.85,
            notes=f"Type cast {old_type} -> {new_type}. Warning: Verify no truncation occurs.",
        )

    def _repair_column_removed(self, event: DriftEvent) -> RepairAction:
        table = self._extract_meta(event, "table", "before")
        col = self._extract_meta(event, "name", "before")
        dtype = getattr(event, "before", {}).get("dtype", "TEXT")

        sql = textwrap.dedent(f"""
            -- SAFETY NET: Re-adding dropped column as NULL to prevent downstream pipeline crashes.
            ALTER TABLE IF EXISTS "{table}"
            ADD COLUMN IF NOT EXISTS "{col}" {dtype} DEFAULT NULL;
        """).strip()

        return RepairAction(
            drift_event=event,
            strategy=RepairStrategy.COALESCE_GUARD,
            sql=sql,
            approval_required=True,
            confidence=0.95,
            notes="CRITICAL: Column dropped upstream. Added back as a NULL stub to save pipelines.",
        )

    def _repair_column_added(self, event: DriftEvent) -> RepairAction:
        table = self._extract_meta(event, "table", "after")
        col = self._extract_meta(event, "name", "after")
        dtype = getattr(event, "after", {}).get("dtype_raw", "TEXT")

        dbt_patch = textwrap.dedent(f"""
            # dbt schema.yml patch for model: {table}
            # - name: {col}
            #   description: 'Auto-detected new column ({dtype})'
        """).strip()

        return RepairAction(
            drift_event=event,
            strategy=RepairStrategy.ADD_COLUMN,
            dbt_patch=dbt_patch,
            approval_required=False,  # Safe additive change
            confidence=1.0,
            notes=f"Additive schema evolution. Stubbed new column '{col}' for dbt.",
        )

    def _repair_nullable_change(self, event: DriftEvent) -> RepairAction:
        table = self._extract_meta(event, "table", "before")
        col = self._extract_meta(event, "name", "before")
        now_nullable = getattr(event, "after", {}).get("nullable", True)

        if now_nullable:
            sql = f'-- WARNING: Column "{table}"."{col}" is now nullable. Review downstream NOT NULL tests.'
        else:
            sql = textwrap.dedent(f"""
                -- WARNING: Upstream added a NOT NULL constraint.
                -- Before enforcing this, clean existing NULLs:
                -- UPDATE "{table}" SET "{col}" = 'DEFAULT_VAL' WHERE "{col}" IS NULL;
                -- ALTER TABLE "{table}" ALTER COLUMN "{col}" SET NOT NULL;
            """).strip()

        return RepairAction(
            drift_event=event,
            strategy=RepairStrategy.COALESCE_GUARD,
            sql=sql,
            approval_required=not now_nullable,
            confidence=0.95,
            notes="Nullable constraint shifted. Review required before enforcing strictness.",
        )

    def _repair_stats_drift(self, event: DriftEvent) -> RepairAction:
        return RepairAction(
            drift_event=event,
            strategy=RepairStrategy.MANUAL_REQUIRED,
            approval_required=False,
            confidence=1.0,
            notes="Data distribution drifted significantly. No SQL repair possible; investigate upstream data entry.",
        )

    def _repair_table_rename(self, event: DriftEvent) -> RepairAction:
        old_name = getattr(event, "before", {}).get("id", "UNKNOWN")
        new_name = getattr(event, "after", {}).get("id", "UNKNOWN")

        sql = textwrap.dedent(f"""
            ALTER TABLE IF EXISTS "{old_name}" 
            RENAME TO "{new_name}";
        """).strip()

        return RepairAction(
            drift_event=event,
            strategy=RepairStrategy.RENAME_COLUMN,
            sql=sql,
            approval_required=True,
            confidence=0.80,
            notes="High blast radius: Table renamed. Requires complete downstream refactor.",
        )

    def _repair_table_removed(self, event: DriftEvent) -> RepairAction:
        return RepairAction(
            drift_event=event,
            strategy=RepairStrategy.MANUAL_REQUIRED,
            approval_required=True,
            confidence=1.0,
            notes="CRITICAL: Entire table dropped. Manual rollback or pipeline restructuring required.",
        )

    def _repair_table_added(self, event: DriftEvent) -> RepairAction:
        table = getattr(event, "after", {}).get("id", "UNKNOWN")

        dbt_patch = textwrap.dedent(f"""
            # Add to dbt sources.yml
            #   - name: raw
            #     tables:
            #       - name: {table}
        """).strip()

        return RepairAction(
            drift_event=event,
            strategy=RepairStrategy.DBT_PATCH,
            dbt_patch=dbt_patch,
            approval_required=False,
            confidence=1.0,
            notes=f"New table '{table}' detected. Registered for dbt ingestion.",
        )
