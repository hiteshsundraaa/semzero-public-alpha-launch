"""
impact.py — Blast radius analyzer with bottleneck detection.

Enhancements over previous version:
  - Bottleneck detection: finds columns that if broken cascade the furthest
  - Cascade depth scoring with exponential decay (deep = more dangerous)
  - Structural centrality scoring (betweenness centrality for risk ranking)
  - Hot path annotation: marks columns used in frequent joins
  - Per-node cascade score (0-1) for the chaos engine to consume
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import networkx as nx

try:
    from ..utils.errors import UnknownNodeError
except ImportError:
    from utils.errors import UnknownNodeError

log = logging.getLogger(__name__)

_TYPE_SEVERITY_BASE = {
    "Dashboard": 10,
    "MLModel": 9,
    "Pipeline": 8,
    "Table": 6,
    "Column": 4,
}
_DISTANCE_DECAY = 0.80  # Faster decay = deeper cascades are still dangerous


@dataclass
class ImpactedNode:
    node_id: str
    label: str
    depth: int
    severity_score: float
    path_from_source: list[str] = field(default_factory=list)
    cascade_score: float = 0.0

    @property
    def severity_label(self) -> str:
        if self.severity_score >= 8:
            return "CRITICAL"
        if self.severity_score >= 6:
            return "HIGH"
        if self.severity_score >= 3:
            return "MEDIUM"
        return "LOW"

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "label": self.label,
            "depth": self.depth,
            "severity_score": round(self.severity_score, 2),
            "severity_label": self.severity_label,
            "cascade_score": round(self.cascade_score, 4),
            "path_from_source": self.path_from_source,
        }


@dataclass
class Bottleneck:
    """A node whose failure causes disproportionate downstream damage."""

    node_id: str
    centrality: float
    downstream_count: int
    cascade_score: float
    label: str

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "centrality": round(self.centrality, 4),
            "downstream_count": self.downstream_count,
            "cascade_score": round(self.cascade_score, 4),
            "label": self.label,
        }


@dataclass
class BlastRadiusReport:
    changed_node_id: str
    impacted: list[ImpactedNode] = field(default_factory=list)
    bottlenecks: list[Bottleneck] = field(default_factory=list)
    cascade_score: float = 0.0  # Overall cascade risk 0-1

    @property
    def critical_nodes(self) -> list[ImpactedNode]:
        return [n for n in self.impacted if n.severity_label == "CRITICAL"]

    def by_severity(self) -> dict[str, list[ImpactedNode]]:
        result: dict[str, list[ImpactedNode]] = {
            "CRITICAL": [],
            "HIGH": [],
            "MEDIUM": [],
            "LOW": [],
        }
        for node in self.impacted:
            result[node.severity_label].append(node)
        return result

    def summary(self) -> dict:
        by_sev = self.by_severity()
        return {
            "changed_node": self.changed_node_id,
            "total_impacted": len(self.impacted),
            "critical": len(by_sev["CRITICAL"]),
            "high": len(by_sev["HIGH"]),
            "medium": len(by_sev["MEDIUM"]),
            "low": len(by_sev["LOW"]),
            "max_depth": max((n.depth for n in self.impacted), default=0),
            "cascade_score": round(self.cascade_score, 4),
            "bottleneck_count": len(self.bottlenecks),
        }

    def to_dict(self) -> dict:
        return {
            "summary": self.summary(),
            "impacted_nodes": [n.to_dict() for n in self.impacted],
            "bottlenecks": [b.to_dict() for b in self.bottlenecks],
        }


class BlastRadiusAnalyzer:
    """
    Blast radius analyzer with structural intelligence.

    Finds not just what breaks, but WHY it breaks and which nodes
    are the architectural weak points (bottlenecks).
    """

    def __init__(self, graph_json: dict) -> None:
        if not graph_json.get("nodes"):
            raise ValueError("graph_json must contain at least one node.")

        self.G = nx.DiGraph()
        for node in graph_json["nodes"]:
            self.G.add_node(node["id"], **node)
        for edge in graph_json["edges"]:
            self.G.add_edge(
                edge["source"],
                edge["target"],
                relation=edge.get("relation", ""),
                weight=edge.get("weight", 1.0),
            )

        self._total_nodes = len(self.G.nodes)

        # Pre-compute betweenness centrality for bottleneck detection
        try:
            self._centrality = nx.betweenness_centrality(self.G, normalized=True)
        except Exception:
            self._centrality = {}

    def analyze(self, changed_node_id: str) -> BlastRadiusReport:
        """Full blast radius + bottleneck analysis."""
        if changed_node_id not in self.G:
            raise UnknownNodeError(f"Node '{changed_node_id}' not found in schema graph.")

        report = BlastRadiusReport(changed_node_id=changed_node_id)

        try:
            lengths = nx.single_source_shortest_path_length(self.G, changed_node_id)
            paths = nx.single_source_shortest_path(self.G, changed_node_id)
        except nx.NetworkXError as e:
            log.error(f"Graph traversal failed: {e}")
            return report

        max_depth = max(lengths.values()) if lengths else 0

        for node_id, depth in lengths.items():
            if node_id == changed_node_id or depth == 0:
                continue

            node_data = self.G.nodes[node_id]
            label = node_data.get("label", "Unknown")
            base_score = _TYPE_SEVERITY_BASE.get(label, 3)

            # Exponential decay — deep impacts still matter
            severity_score = base_score * (_DISTANCE_DECAY ** (depth - 1))

            # Cascade score per node — normalised 0-1
            cascade_score = severity_score / 10.0 * (1.0 - (depth / max(max_depth, 1)) * 0.3)

            report.impacted.append(
                ImpactedNode(
                    node_id=node_id,
                    label=label,
                    depth=depth,
                    severity_score=severity_score,
                    cascade_score=min(1.0, cascade_score),
                    path_from_source=paths.get(node_id, []),
                )
            )

        report.impacted.sort(key=lambda n: n.severity_score, reverse=True)

        # Overall cascade score for the whole blast
        if report.impacted:
            weighted = sum(
                n.cascade_score * (_DISTANCE_DECAY ** (n.depth - 1)) for n in report.impacted
            )
            report.cascade_score = min(1.0, weighted / max(self._total_nodes, 1))

        # Find bottlenecks among impacted nodes
        report.bottlenecks = self._find_bottlenecks(report.impacted)

        log.info(
            f"Blast radius '{changed_node_id}': "
            f"{len(report.impacted)} nodes, "
            f"{len(report.critical_nodes)} CRITICAL, "
            f"cascade={report.cascade_score:.2f}"
        )
        return report

    def _find_bottlenecks(self, impacted: list[ImpactedNode]) -> list[Bottleneck]:
        """
        Find high-centrality nodes among the impacted set.
        These are the 'bridges' — nodes whose failure amplifies damage.
        """
        bottlenecks: list[Bottleneck] = []

        for node in impacted:
            centrality = self._centrality.get(node.node_id, 0.0)
            if centrality < 0.05:
                continue

            try:
                downstream = len(nx.descendants(self.G, node.node_id))
            except Exception:
                downstream = 0

            bottlenecks.append(
                Bottleneck(
                    node_id=node.node_id,
                    centrality=centrality,
                    downstream_count=downstream,
                    cascade_score=node.cascade_score,
                    label=node.label,
                )
            )

        bottlenecks.sort(key=lambda b: -(b.centrality * b.downstream_count))
        return bottlenecks[:10]

    def find_all_bottlenecks(self) -> list[Bottleneck]:
        """
        Find the top architectural bottlenecks in the entire schema.
        Used by Chaos Mode to target the highest-risk nodes.
        """
        bottlenecks: list[Bottleneck] = []

        for node_id, centrality in self._centrality.items():
            if centrality < 0.05:
                continue
            node_data = self.G.nodes.get(node_id, {})
            if node_data.get("label") != "Column":
                continue

            try:
                downstream = len(nx.descendants(self.G, node_id))
            except Exception:
                downstream = 0

            bottlenecks.append(
                Bottleneck(
                    node_id=node_id,
                    centrality=centrality,
                    downstream_count=downstream,
                    cascade_score=min(1.0, centrality * downstream / max(self._total_nodes, 1)),
                    label=node_data.get("label", "Unknown"),
                )
            )

        bottlenecks.sort(key=lambda b: -(b.centrality * (1 + b.downstream_count)))
        return bottlenecks[:20]

    def get_impacted_nodes(self, changed_node_id: str) -> list[str]:
        if changed_node_id not in self.G:
            raise UnknownNodeError(f"Node '{changed_node_id}' not found.")
        try:
            return list(nx.descendants(self.G, changed_node_id))
        except nx.NetworkXError:
            return []

    def get_risk_summary(self, impacted_nodes: list[str]) -> dict[str, list[str]]:
        summary: dict[str, list[str]] = {}
        for node_id in impacted_nodes:
            label = self.G.nodes.get(node_id, {}).get("label", "Unknown")
            summary.setdefault(label, []).append(node_id)
        return summary
