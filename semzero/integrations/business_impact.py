"""
business_impact.py — Business Impact Scorer for schema changes.

Enriches blast radius with business context so CTOs and VPs see:
  "This change threatens a CFO dashboard, 2 RevOps models, and 1 board metric."

Not: "3 downstream nodes affected."

Seven severity levels:
  BOARD_CRITICAL    — board-level metrics, investor dashboards
  EXEC_CRITICAL     — C-suite reporting, OKR dashboards
  REVENUE_CRITICAL  — direct revenue pipelines, billing, payments
  CUSTOMER_FACING   — customer-visible data, SLAs, NPS
  INTERNAL_HIGH     — ops metrics, internal analytics
  INTERNAL_LOW      — supporting tables, staging, raw data
  UNKNOWN           — no context available

Usage:
  scorer = BusinessImpactScorer(graph_json, criticality_registry)
  result = scorer.score(blast_radius_report)
  print(result.executive_summary)
  # "This threatens a CFO dashboard, 2 RevOps models, and 1 board metric."
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path

log = logging.getLogger(__name__)


# ── Severity levels ───────────────────────────────────────────────────────────

SEVERITY_ORDER = [
    "BOARD_CRITICAL",
    "EXEC_CRITICAL",
    "REVENUE_CRITICAL",
    "CUSTOMER_FACING",
    "INTERNAL_HIGH",
    "INTERNAL_LOW",
    "UNKNOWN",
]

# Pattern → severity mapping (checked against table/column names)
_PATTERN_SEVERITY: list[tuple[list[str], str]] = [
    (["board", "investor", "kpi_board", "exec_dash"], "BOARD_CRITICAL"),
    (["cfo", "ceo", "cto", "vp_", "okr", "quarterly", "annual_report"], "EXEC_CRITICAL"),
    (
        [
            "revenue",
            "mrr",
            "arr",
            "billing",
            "payment",
            "invoice",
            "churn",
            "ltv",
            "subscription",
            "transaction",
        ],
        "REVENUE_CRITICAL",
    ),
    (["customer", "user_facing", "nps", "csat", "sla", "public_api"], "CUSTOMER_FACING"),
    (["ops", "internal", "analytics", "reporting", "pipeline", "dbt_"], "INTERNAL_HIGH"),
    (["staging", "raw", "temp", "log", "event", "audit", "debug"], "INTERNAL_LOW"),
]


# ── Data classes ──────────────────────────────────────────────────────────────


@dataclass
class NodeImpact:
    node_id: str
    severity: str
    reason: str
    business_label: str = ""  # e.g. "CFO dashboard", "RevOps model"


@dataclass
class ImpactResult:
    changed_node_id: str
    highest_severity: str
    impacted_nodes: list[NodeImpact] = field(default_factory=list)
    executive_summary: str = ""
    slack_message: str = ""
    total_impacted: int = 0
    board_critical: int = 0
    exec_critical: int = 0
    revenue_critical: int = 0
    customer_facing: int = 0

    def to_dict(self) -> dict:
        return {
            "changed_node_id": self.changed_node_id,
            "highest_severity": self.highest_severity,
            "executive_summary": self.executive_summary,
            "total_impacted": self.total_impacted,
            "board_critical": self.board_critical,
            "exec_critical": self.exec_critical,
            "revenue_critical": self.revenue_critical,
            "customer_facing": self.customer_facing,
            "impacted_nodes": [
                {
                    "node_id": n.node_id,
                    "severity": n.severity,
                    "reason": n.reason,
                    "label": n.business_label,
                }
                for n in self.impacted_nodes
            ],
        }

    def for_slack(self) -> dict:
        """Format as Slack Block Kit message."""
        emoji = {
            "BOARD_CRITICAL": "🔴🔴",
            "EXEC_CRITICAL": "🔴",
            "REVENUE_CRITICAL": "⚠️",
            "CUSTOMER_FACING": "⚠️",
            "INTERNAL_HIGH": "ℹ️",
            "INTERNAL_LOW": "ℹ️",
            "UNKNOWN": "❓",
        }.get(self.highest_severity, "❓")

        return {
            "blocks": [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": f"{emoji} Business Impact: {self.highest_severity}",
                    },
                },
                {"type": "section", "text": {"type": "mrkdwn", "text": self.executive_summary}},
            ]
        }


# ── Criticality Registry ──────────────────────────────────────────────────────


class CriticalityRegistry:
    """
    Optional JSON registry that maps table/node IDs to explicit business labels.

    Example registry JSON:
    {
        "revenue_daily": {"severity": "BOARD_CRITICAL", "label": "CFO daily revenue"},
        "orders":        {"severity": "REVENUE_CRITICAL", "label": "order pipeline"},
        "users":         {"severity": "CUSTOMER_FACING",  "label": "customer records"}
    }
    """

    def __init__(self, registry: dict | None = None) -> None:
        self._reg: dict[str, dict] = registry or {}

    @classmethod
    def from_file(cls, path: str) -> "CriticalityRegistry":
        p = Path(path)
        if not p.exists():
            log.warning(f"Criticality registry not found: {path}")
            return cls({})
        return cls(json.loads(p.read_text()))

    @classmethod
    def generate_default(cls, graph_json: dict) -> "CriticalityRegistry":
        """
        Auto-populate from graph using pattern recognition.
        Returns a registry that can be saved and human-edited.
        """
        reg: dict[str, dict] = {}
        for node in graph_json.get("nodes", []):
            nid = node["id"]
            label = node.get("label", "")
            if label not in ("Table", "Column"):
                continue
            severity, reason = _infer_severity(nid)
            biz_label = _infer_business_label(nid)
            reg[nid] = {
                "severity": severity,
                "label": biz_label,
                "reason": reason,
                "auto": True,
            }
        return cls(reg)

    def get(self, node_id: str) -> Optional[dict]:
        # Try exact match first, then table prefix
        if node_id in self._reg:
            return self._reg[node_id]
        table = node_id.split(".")[0] if "." in node_id else node_id
        return self._reg.get(table)

    def save(self, path: str) -> Path:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(self._reg, indent=2))
        return out


# ── Scorer ────────────────────────────────────────────────────────────────────


class BusinessImpactScorer:
    """
    Scores the business impact of a schema change using:
      1. Explicit criticality registry (if provided)
      2. Pattern-based inference from node names
    """

    def __init__(
        self,
        graph_json: dict,
        registry: Optional[CriticalityRegistry] = None,
    ) -> None:
        self.graph = graph_json
        self.registry = registry or CriticalityRegistry.generate_default(graph_json)

    def score(self, blast_report: dict) -> ImpactResult:
        """
        Score business impact from a BlastRadiusReport dict.
        """
        changed = blast_report.get("summary", {}).get("changed_node", "unknown")
        impacted_nodes_raw = blast_report.get("impacted_nodes", [])

        node_impacts: list[NodeImpact] = []
        severity_counts: dict[str, int] = {s: 0 for s in SEVERITY_ORDER}

        for raw in impacted_nodes_raw:
            nid = raw.get("node_id", "")
            reg_entry = self.registry.get(nid)

            if reg_entry:
                severity = reg_entry.get("severity", "UNKNOWN")
                biz_label = reg_entry.get("label", nid)
                reason = reg_entry.get("reason", "In criticality registry")
            else:
                severity, reason = _infer_severity(nid)
                biz_label = _infer_business_label(nid)

            node_impacts.append(
                NodeImpact(
                    node_id=nid,
                    severity=severity,
                    reason=reason,
                    business_label=biz_label,
                )
            )
            severity_counts[severity] = severity_counts.get(severity, 0) + 1

        # Determine highest severity
        highest = "UNKNOWN"
        for sev in SEVERITY_ORDER:
            if severity_counts.get(sev, 0) > 0:
                highest = sev
                break

        result = ImpactResult(
            changed_node_id=changed,
            highest_severity=highest,
            impacted_nodes=sorted(node_impacts, key=lambda n: SEVERITY_ORDER.index(n.severity)),
            total_impacted=len(node_impacts),
            board_critical=severity_counts.get("BOARD_CRITICAL", 0),
            exec_critical=severity_counts.get("EXEC_CRITICAL", 0),
            revenue_critical=severity_counts.get("REVENUE_CRITICAL", 0),
            customer_facing=severity_counts.get("CUSTOMER_FACING", 0),
        )

        result.executive_summary = self._build_summary(result)
        result.slack_message = result.for_slack().__str__()

        log.info(
            f"BusinessImpact: {changed} → {highest} "
            f"({result.board_critical} board, {result.revenue_critical} revenue)"
        )
        return result

    def _build_summary(self, result: ImpactResult) -> str:
        """
        Build a one-sentence executive summary.
        Example: "This threatens a CFO dashboard, 2 RevOps models, and 1 board metric."
        """
        if not result.impacted_nodes:
            return "No downstream business impact detected."

        parts: list[str] = []

        if result.board_critical:
            labels = [
                n.business_label for n in result.impacted_nodes if n.severity == "BOARD_CRITICAL"
            ][:2]
            parts.append(_pluralise(labels, "board metric"))

        if result.exec_critical:
            labels = [
                n.business_label for n in result.impacted_nodes if n.severity == "EXEC_CRITICAL"
            ][:2]
            parts.append(_pluralise(labels, "exec dashboard"))

        if result.revenue_critical:
            labels = [
                n.business_label for n in result.impacted_nodes if n.severity == "REVENUE_CRITICAL"
            ][:2]
            parts.append(_pluralise(labels, "revenue pipeline"))

        if result.customer_facing:
            n = result.customer_facing
            parts.append(f"{n} customer-facing {'model' if n == 1 else 'models'}")

        if not parts:
            return (
                f"Change to {result.changed_node_id} affects "
                f"{result.total_impacted} downstream nodes — low business severity."
            )

        return "This threatens " + _join_parts(parts) + "."


# ── Helpers ───────────────────────────────────────────────────────────────────


def _infer_severity(node_id: str) -> tuple[str, str]:
    """Pattern-match node ID against severity patterns."""
    nid_lower = node_id.lower()
    for patterns, severity in _PATTERN_SEVERITY:
        if any(p in nid_lower for p in patterns):
            matched = next(p for p in patterns if p in nid_lower)
            return severity, f"Pattern match: '{matched}' in node name"
    return "UNKNOWN", "No pattern match"


def _infer_business_label(node_id: str) -> str:
    """Convert a node ID to a human-readable business label."""
    name = node_id.split(".")[-1] if "." in node_id else node_id
    # Convert snake_case to readable
    words = name.replace("_", " ").split()
    return " ".join(w.capitalize() for w in words)


def _pluralise(labels: list[str], fallback: str) -> str:
    if not labels:
        return fallback
    if len(labels) == 1:
        return f"a {labels[0]}"
    return f"{labels[0]} and {len(labels) - 1} other {fallback}{'s' if len(labels) > 2 else ''}"


def _join_parts(parts: list[str]) -> str:
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return f"{parts[0]} and {parts[1]}"
    return ", ".join(parts[:-1]) + f", and {parts[-1]}"
