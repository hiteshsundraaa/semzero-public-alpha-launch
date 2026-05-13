"""
drift.py — Schema drift detection engine.

Compares two graph snapshots and classifies every change with a
severity level and timestamp. Core trigger for the repair loop,
Change Gate, and RCA agent.

Change types and severity:
  COLUMN_REMOVED     CRITICAL — breaks downstream immediately
  TABLE_REMOVED      CRITICAL — breaks downstream immediately
  TYPE_CHANGED       HIGH     — likely breaks consumers
  COLUMN_RENAMED     HIGH     — likely breaks consumers
  TABLE_RENAMED      HIGH     — likely breaks consumers
  NULLABLE_CHANGED   MEDIUM   — may cause silent corruption
  STATS_DRIFTED      MEDIUM   — data quality issue
  COLUMN_ADDED       LOW      — additive, usually safe
  TABLE_ADDED        LOW      — additive, usually safe

Improvements over v1:
  - changed_at timestamp on every event (required by RCA recency scoring)
  - DriftVelocity tracking — rate of change per table over time
  - Improved rename detection: fingerprint similarity + type + cardinality
  - Configurable STATS_DRIFT_THRESHOLD
  - DriftReport.diff_id — stable hash for deduplication in watcher
  - Type narrowing detection (separate from generic TYPE_CHANGED)
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


class ChangeType(str, Enum):
    COLUMN_ADDED = "COLUMN_ADDED"
    COLUMN_REMOVED = "COLUMN_REMOVED"
    COLUMN_RENAMED = "COLUMN_RENAMED"
    TYPE_CHANGED = "TYPE_CHANGED"
    TYPE_NARROWING = "TYPE_NARROWING"  # Specific subtype: dangerous narrowing
    NULLABLE_CHANGED = "NULLABLE_CHANGED"
    TABLE_ADDED = "TABLE_ADDED"
    TABLE_REMOVED = "TABLE_REMOVED"
    TABLE_RENAMED = "TABLE_RENAMED"
    STATS_DRIFTED = "STATS_DRIFTED"


class Severity(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


_SEVERITY: dict[ChangeType, Severity] = {
    ChangeType.COLUMN_REMOVED: Severity.CRITICAL,
    ChangeType.TABLE_REMOVED: Severity.CRITICAL,
    ChangeType.TYPE_NARROWING: Severity.HIGH,
    ChangeType.TYPE_CHANGED: Severity.HIGH,
    ChangeType.COLUMN_RENAMED: Severity.HIGH,
    ChangeType.TABLE_RENAMED: Severity.HIGH,
    ChangeType.NULLABLE_CHANGED: Severity.MEDIUM,
    ChangeType.STATS_DRIFTED: Severity.MEDIUM,
    ChangeType.COLUMN_ADDED: Severity.LOW,
    ChangeType.TABLE_ADDED: Severity.LOW,
}

# Type families for narrowing detection
_TYPE_FAMILY: dict[str, str] = {
    "INTEGER": "INTEGER",
    "INT": "INTEGER",
    "SMALLINT": "INTEGER",
    "TINYINT": "INTEGER",
    "BIGINT": "INTEGER",
    "FLOAT": "FLOAT",
    "DOUBLE": "FLOAT",
    "REAL": "FLOAT",
    "NUMERIC": "FLOAT",
    "DECIMAL": "FLOAT",
    "VARCHAR": "STRING",
    "TEXT": "STRING",
    "CHAR": "STRING",
    "NVARCHAR": "STRING",
    "STRING": "STRING",
    "TIMESTAMP": "TEMPORAL",
    "DATE": "TEMPORAL",
    "DATETIME": "TEMPORAL",
    "TIMESTAMPTZ": "TEMPORAL",
    "TIME": "TEMPORAL",
    "BOOLEAN": "BOOLEAN",
    "BOOL": "BOOLEAN",
    "JSONB": "SEMI",
    "JSON": "SEMI",
    "ARRAY": "SEMI",
    "BYTEA": "BINARY",
    "BLOB": "BINARY",
}

# Type transitions that are dangerous (narrowing)
_NARROWING_PAIRS: set[tuple[str, str]] = {
    ("INTEGER", "FLOAT"),  # Precision loss
    ("BIGINT", "INTEGER"),  # Overflow risk
    ("BIGINT", "SMALLINT"),
    ("INTEGER", "SMALLINT"),
    ("STRING", "INTEGER"),  # Semantic change
    ("STRING", "FLOAT"),
    ("TEMPORAL", "STRING"),  # Semantic change
    ("TEMPORAL", "INTEGER"),
    ("FLOAT", "INTEGER"),  # Truncation
}

_STATS_DRIFT_THRESHOLD = 0.15


@dataclass
class DriftEvent:
    change_type: ChangeType
    severity: Severity
    node_id: str
    before: Optional[dict] = None
    after: Optional[dict] = None
    detail: str = ""
    changed_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return {
            "change_type": self.change_type.value,
            "severity": self.severity.value,
            "node_id": self.node_id,
            "before": self.before,
            "after": self.after,
            "detail": self.detail,
            "changed_at": self.changed_at,
        }


@dataclass
class DriftVelocity:
    """Rate of change for a table — used by Chaos Mode risk multiplier."""

    table_id: str
    changes_7d: int = 0
    changes_30d: int = 0
    last_changed_at: str = ""
    hottest_col: str = ""  # Column changed most often


@dataclass
class DriftReport:
    before_snapshot: str
    after_snapshot: str
    detected_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    events: list[DriftEvent] = field(default_factory=list)
    velocity: list[DriftVelocity] = field(default_factory=list)
    diff_id: str = ""  # Stable hash for dedup in watcher

    def __post_init__(self):
        if not self.diff_id:
            self.diff_id = self._compute_diff_id()

    def _compute_diff_id(self) -> str:
        """Stable hash of this diff — used by watcher for PR deduplication."""
        key = json.dumps(sorted(e.node_id + e.change_type.value for e in self.events))
        return hashlib.sha256(key.encode()).hexdigest()[:12]

    @property
    def critical(self) -> list[DriftEvent]:
        return [e for e in self.events if e.severity == Severity.CRITICAL]

    @property
    def high(self) -> list[DriftEvent]:
        return [e for e in self.events if e.severity == Severity.HIGH]

    @property
    def is_clean(self) -> bool:
        return len(self.events) == 0

    def summary(self) -> dict:
        counts: dict[str, int] = {}
        for e in self.events:
            counts[e.severity.value] = counts.get(e.severity.value, 0) + 1
        return {
            "total_changes": len(self.events),
            "by_severity": counts,
            "is_clean": self.is_clean,
            "diff_id": self.diff_id,
        }

    def to_dict(self) -> dict:
        return {
            "before_snapshot": self.before_snapshot,
            "after_snapshot": self.after_snapshot,
            "detected_at": self.detected_at,
            "diff_id": self.diff_id,
            "summary": self.summary(),
            "events": [e.to_dict() for e in self.events],
            "velocity": [self._vel_dict(v) for v in self.velocity],
        }

    @staticmethod
    def _vel_dict(v: DriftVelocity) -> dict:
        return {
            "table_id": v.table_id,
            "changes_7d": v.changes_7d,
            "changes_30d": v.changes_30d,
            "last_changed_at": v.last_changed_at,
            "hottest_col": v.hottest_col,
        }

    def save(self, filepath: str = "data/drift_report.json") -> Path:
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, default=str))
        log.info(f"Drift report saved → {path}")
        return path


class SchemaDriftDetector:
    """
    Diffs two schema graph snapshots and emits typed, severity-scored
    DriftEvents. Pure — no side effects, no DB connections.

    Improvements over v1:
      - Type narrowing detection (separate CRITICAL/HIGH classification)
      - Rename detection uses fingerprint similarity + structural matching
      - DriftVelocity computed per table
      - changed_at on every event for RCA recency scoring
      - Configurable thresholds
    """

    def __init__(
        self,
        stats_drift_threshold: float = _STATS_DRIFT_THRESHOLD,
        rename_cardinality_tolerance: float = 0.05,
    ) -> None:
        self.stats_drift_threshold = stats_drift_threshold
        self.rename_cardinality_tolerance = rename_cardinality_tolerance

    def diff(
        self,
        before: dict,
        after: dict,
        before_label: str = "before",
        after_label: str = "after",
    ) -> DriftReport:
        report = DriftReport(
            before_snapshot=before_label,
            after_snapshot=after_label,
        )
        now = datetime.now(timezone.utc).isoformat()

        before_tables = {n["id"]: n for n in before.get("nodes", []) if n.get("label") == "Table"}
        after_tables = {n["id"]: n for n in after.get("nodes", []) if n.get("label") == "Table"}
        before_cols = {n["id"]: n for n in before.get("nodes", []) if n.get("label") == "Column"}
        after_cols = {n["id"]: n for n in after.get("nodes", []) if n.get("label") == "Column"}

        # ── Tables added ──────────────────────────────────────────────────────
        for tbl_id in set(after_tables) - set(before_tables):
            report.events.append(
                DriftEvent(
                    change_type=ChangeType.TABLE_ADDED,
                    severity=_SEVERITY[ChangeType.TABLE_ADDED],
                    node_id=tbl_id,
                    after=after_tables[tbl_id],
                    detail=f"New table '{tbl_id}' appeared.",
                    changed_at=now,
                )
            )

        # ── Tables removed or renamed ─────────────────────────────────────────
        for tbl_id in set(before_tables) - set(after_tables):
            renamed_to = self._find_renamed_table(
                before_tables[tbl_id], before_cols, after_tables, after_cols
            )
            if renamed_to:
                report.events.append(
                    DriftEvent(
                        change_type=ChangeType.TABLE_RENAMED,
                        severity=_SEVERITY[ChangeType.TABLE_RENAMED],
                        node_id=tbl_id,
                        before=before_tables[tbl_id],
                        after=after_tables[renamed_to],
                        detail=(f"Table '{tbl_id}' may have been renamed to '{renamed_to}'."),
                        changed_at=now,
                    )
                )
            else:
                report.events.append(
                    DriftEvent(
                        change_type=ChangeType.TABLE_REMOVED,
                        severity=_SEVERITY[ChangeType.TABLE_REMOVED],
                        node_id=tbl_id,
                        before=before_tables[tbl_id],
                        detail=f"Table '{tbl_id}' was dropped.",
                        changed_at=now,
                    )
                )

        # ── Column-level changes ──────────────────────────────────────────────
        for tbl_id in set(before_tables) & set(after_tables):
            b_tbl_cols = {n["name"]: n for n in before_cols.values() if n.get("table") == tbl_id}
            a_tbl_cols = {n["name"]: n for n in after_cols.values() if n.get("table") == tbl_id}

            # Added
            for col_name in set(a_tbl_cols) - set(b_tbl_cols):
                report.events.append(
                    DriftEvent(
                        change_type=ChangeType.COLUMN_ADDED,
                        severity=_SEVERITY[ChangeType.COLUMN_ADDED],
                        node_id=f"{tbl_id}.{col_name}",
                        after=a_tbl_cols[col_name],
                        detail=(f"Column '{col_name}' added to '{tbl_id}'."),
                        changed_at=now,
                    )
                )

            # Removed or renamed
            for col_name in set(b_tbl_cols) - set(a_tbl_cols):
                bc = b_tbl_cols[col_name]
                renamed_to = self._find_renamed_column(bc, a_tbl_cols)
                if renamed_to:
                    report.events.append(
                        DriftEvent(
                            change_type=ChangeType.COLUMN_RENAMED,
                            severity=_SEVERITY[ChangeType.COLUMN_RENAMED],
                            node_id=f"{tbl_id}.{col_name}",
                            before=bc,
                            after=a_tbl_cols[renamed_to],
                            detail=(
                                f"Column '{tbl_id}.{col_name}' may have been renamed "
                                f"to '{tbl_id}.{renamed_to}'."
                            ),
                            changed_at=now,
                        )
                    )
                else:
                    report.events.append(
                        DriftEvent(
                            change_type=ChangeType.COLUMN_REMOVED,
                            severity=_SEVERITY[ChangeType.COLUMN_REMOVED],
                            node_id=f"{tbl_id}.{col_name}",
                            before=bc,
                            detail=(f"Column '{col_name}' was dropped from '{tbl_id}'."),
                            changed_at=now,
                        )
                    )

            # Changed (same name, different properties)
            for col_name in set(b_tbl_cols) & set(a_tbl_cols):
                bc = b_tbl_cols[col_name]
                ac = a_tbl_cols[col_name]
                col_id = f"{tbl_id}.{col_name}"

                if bc.get("dtype") != ac.get("dtype"):
                    # Detect type narrowing specifically
                    b_fam = _TYPE_FAMILY.get(str(bc.get("dtype", "")).upper().split("(")[0], "")
                    a_fam = _TYPE_FAMILY.get(str(ac.get("dtype", "")).upper().split("(")[0], "")
                    is_narrowing = (b_fam, a_fam) in _NARROWING_PAIRS

                    ct = ChangeType.TYPE_NARROWING if is_narrowing else ChangeType.TYPE_CHANGED
                    sev = Severity.HIGH

                    report.events.append(
                        DriftEvent(
                            change_type=ct,
                            severity=sev,
                            node_id=col_id,
                            before=bc,
                            after=ac,
                            detail=(
                                f"{'Narrowing: ' if is_narrowing else ''}Type changed "
                                f"{bc.get('dtype')} → {ac.get('dtype')} on '{col_id}'."
                            ),
                            changed_at=now,
                        )
                    )

                if bc.get("nullable") != ac.get("nullable"):
                    report.events.append(
                        DriftEvent(
                            change_type=ChangeType.NULLABLE_CHANGED,
                            severity=_SEVERITY[ChangeType.NULLABLE_CHANGED],
                            node_id=col_id,
                            before=bc,
                            after=ac,
                            detail=(
                                f"Nullability: nullable={bc.get('nullable')} → "
                                f"{ac.get('nullable')} on '{col_id}'."
                            ),
                            changed_at=now,
                        )
                    )

                for stat in ("null_rate", "cardinality"):
                    bv = bc.get(stat, 0.0) or 0.0
                    av = ac.get(stat, 0.0) or 0.0
                    if abs(av - bv) > self.stats_drift_threshold:
                        report.events.append(
                            DriftEvent(
                                change_type=ChangeType.STATS_DRIFTED,
                                severity=_SEVERITY[ChangeType.STATS_DRIFTED],
                                node_id=col_id,
                                before=bc,
                                after=ac,
                                detail=(
                                    f"Statistical drift on '{col_id}': "
                                    f"{stat} changed {bv:.1%} → {av:.1%}."
                                ),
                                changed_at=now,
                            )
                        )

        # ── Drift velocity per table ──────────────────────────────────────────
        report.velocity = self._compute_velocity(report.events)
        report.diff_id = report._compute_diff_id()

        log.info(
            f"Drift detection: {len(report.events)} changes "
            f"({len(report.critical)} CRITICAL, {len(report.high)} HIGH) "
            f"diff_id={report.diff_id}"
        )
        return report

    # ── Rename detection ──────────────────────────────────────────────────────

    def _find_renamed_column(
        self,
        before_col: dict,
        after_cols: dict[str, dict],
    ) -> Optional[str]:
        """
        Match a missing column to a new column in the same table.

        A column is likely renamed when the candidate shares:
          - Same dtype family
          - Same nullability
          - Similar cardinality (within tolerance)
          - Neither side is a primary key (prevents stock→id false positive)
          - Fingerprint similarity (structural match) preferred
        """
        candidates: list[tuple[float, str]] = []

        for name, ac in after_cols.items():
            # Hard gates
            if ac.get("is_primary_key") or before_col.get("is_primary_key"):
                continue
            if before_col.get("dtype") != ac.get("dtype"):
                continue
            if before_col.get("nullable") != ac.get("nullable"):
                continue

            # Cardinality similarity
            bc_card = before_col.get("cardinality", 0.0) or 0.0
            ac_card = ac.get("cardinality", 0.0) or 0.0
            card_diff = abs(bc_card - ac_card)
            if card_diff > self.rename_cardinality_tolerance:
                continue

            # Fingerprint match bonus
            score = 1.0 - card_diff
            if before_col.get("fingerprint") and before_col.get("fingerprint") == ac.get(
                "fingerprint"
            ):
                score += 0.5  # Near-perfect match

            candidates.append((score, name))

        if not candidates:
            return None

        # Return highest-scoring candidate
        candidates.sort(reverse=True)
        return candidates[0][1]

    def _find_renamed_table(
        self,
        before_table: dict,
        before_cols: dict,
        after_tables: dict,
        after_cols: dict,
    ) -> Optional[str]:
        """
        Table is likely renamed if a new table shares 80%+ column fingerprints.
        """
        before_tbl_id = before_table["id"]
        before_fps = frozenset(
            n["fingerprint"]
            for n in before_cols.values()
            if n.get("table") == before_tbl_id and n.get("fingerprint")
        )
        if len(before_fps) < 3:  # Need at least 3 matching cols for confidence
            return None

        for tbl_id, tbl in after_tables.items():
            after_fps = frozenset(
                n["fingerprint"]
                for n in after_cols.values()
                if n.get("table") == tbl_id and n.get("fingerprint")
            )
            overlap = len(before_fps & after_fps) / max(len(before_fps), 1)
            if overlap >= 0.8:
                return tbl_id

        return None

    # ── Velocity ──────────────────────────────────────────────────────────────

    @staticmethod
    def _compute_velocity(events: list[DriftEvent]) -> list[DriftVelocity]:
        """
        Compute per-table drift velocity from this diff.
        In production, the watcher accumulates this across ticks.
        Here we just count changes per table from this diff.
        """
        table_changes: dict[str, list[str]] = {}
        for ev in events:
            tbl = ev.node_id.split(".")[0] if "." in ev.node_id else ev.node_id
            table_changes.setdefault(tbl, []).append(ev.node_id)

        velocity: list[DriftVelocity] = []
        for tbl, cols in table_changes.items():
            # Most-changed column
            col_counts: dict[str, int] = {}
            for c in cols:
                col_counts[c] = col_counts.get(c, 0) + 1
            hottest = max(col_counts, key=lambda k: col_counts[k]) if col_counts else ""

            velocity.append(
                DriftVelocity(
                    table_id=tbl,
                    changes_7d=len(cols),
                    changes_30d=len(cols),
                    last_changed_at=datetime.now(timezone.utc).isoformat(),
                    hottest_col=hottest,
                )
            )

        return sorted(velocity, key=lambda v: -v.changes_7d)
