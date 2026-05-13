from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import networkx as nx


@dataclass
class GraphNodeSignal:
    node_id: str
    score: float
    provider: str
    heuristic_score: float
    rgcn_score: Optional[float] = None
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "score": round(self.score, 4),
            "provider": self.provider,
            "heuristic_score": round(self.heuristic_score, 4),
            "rgcn_score": None if self.rgcn_score is None else round(self.rgcn_score, 4),
            "reasons": self.reasons[:5],
        }


@dataclass
class GraphIntelligenceReport:
    provider: str
    enabled: bool
    model_path: str = ""
    status: str = "heuristic"
    warnings: list[str] = field(default_factory=list)
    nodes: list[GraphNodeSignal] = field(default_factory=list)

    def for_node(self, node_id: str) -> Optional[GraphNodeSignal]:
        for item in self.nodes:
            if item.node_id == node_id:
                return item
        return None

    def top_nodes(self, limit: int = 8) -> list[GraphNodeSignal]:
        return sorted(self.nodes, key=lambda item: (-item.score, item.node_id))[:limit]

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "provider": self.provider,
            "status": self.status,
            "model_path": self.model_path,
            "warnings": self.warnings[:5],
            "top_nodes": [item.to_dict() for item in self.top_nodes()],
        }


class GraphIntelligenceEngine:
    """Graph-native prioritization for Gate/Wind/Chaos.

    Uses heuristics by default and upgrades to an optional RGCN inference path when
    torch/torch-geometric are available and a model checkpoint is provided.
    The main product promise is stable end-to-end execution, so the RGCN path is
    strictly additive and never required for correctness.
    """

    def __init__(
        self,
        graph_json: dict,
        *,
        enabled: bool = True,
        rgcn_model_path: str = "",
    ) -> None:
        self.graph_json = graph_json or {"nodes": [], "edges": []}
        self.enabled = enabled
        self.rgcn_model_path = rgcn_model_path or ""

    def analyse(self, focus_node_ids: Optional[list[str]] = None) -> GraphIntelligenceReport:
        if not self.enabled:
            return GraphIntelligenceReport(provider="disabled", enabled=False, status="disabled")

        report = self._heuristic_report(focus_node_ids=focus_node_ids)
        if self.rgcn_model_path:
            rgcn_scores, warnings = self._compute_rgcn_scores()
            report.warnings.extend(warnings)
            if rgcn_scores:
                report.provider = "rgcn"
                report.status = "rgcn"
                report.model_path = self.rgcn_model_path
                for node in report.nodes:
                    rgcn_score = rgcn_scores.get(node.node_id)
                    if rgcn_score is None:
                        continue
                    node.rgcn_score = rgcn_score
                    node.score = round(min(1.0, node.heuristic_score * 0.55 + rgcn_score * 0.45), 4)
                    node.provider = "rgcn"
                    node.reasons.append("RGCN relational signal elevated this node")
            elif warnings:
                report.status = "heuristic-fallback"
                report.model_path = self.rgcn_model_path
        return report

    def _heuristic_report(
        self, focus_node_ids: Optional[list[str]] = None
    ) -> GraphIntelligenceReport:
        nodes = self.graph_json.get("nodes", [])
        edges = self.graph_json.get("edges", [])
        graph = nx.DiGraph()
        for node in nodes:
            graph.add_node(node.get("id"), **node)
        for edge in edges:
            graph.add_edge(
                edge.get("source"), edge.get("target"), relation=edge.get("relation", "")
            )

        centrality = (
            nx.betweenness_centrality(graph, normalized=True) if graph.number_of_nodes() else {}
        )
        descendant_counts: dict[str, int] = {}
        for node_id in graph.nodes:
            try:
                descendant_counts[node_id] = len(nx.descendants(graph, node_id))
            except Exception:
                descendant_counts[node_id] = 0
        max_desc = max(descendant_counts.values()) if descendant_counts else 1
        ref_sources = {e.get("source") for e in edges if e.get("relation") == "REFERENCES"}
        ref_targets = {e.get("target") for e in edges if e.get("relation") == "REFERENCES"}
        focus = {str(item).lower() for item in (focus_node_ids or []) if item}

        signals: list[GraphNodeSignal] = []
        for node in nodes:
            node_id = str(node.get("id", ""))
            if not node_id:
                continue
            if (
                focus
                and node_id.lower() not in focus
                and not any(node_id.lower().startswith(tok + ".") for tok in focus)
            ):
                table_id = str(node.get("table", node_id.split(".")[0])).lower()
                if table_id not in focus:
                    continue
            label = str(node.get("label", ""))
            if label not in {"Column", "Table"}:
                continue
            reasons: list[str] = []
            cent = float(centrality.get(node_id, 0.0) or 0.0)
            desc = descendant_counts.get(node_id, 0) / max(max_desc, 1)
            in_deg = min(1.0, graph.in_degree(node_id) / 6.0) if graph.has_node(node_id) else 0.0
            out_deg = min(1.0, graph.out_degree(node_id) / 6.0) if graph.has_node(node_id) else 0.0
            ref_bonus = 0.18 if node_id in ref_sources or node_id in ref_targets else 0.0
            nullable_penalty = min(0.12, float(node.get("null_rate", 0.0) or 0.0) * 1.8)
            semantic_bonus = 0.0
            name = str(node.get("name", node_id.split(".")[-1])).lower()
            if name == "id" or name.endswith("_id"):
                semantic_bonus += 0.12
                reasons.append("Identity/join-key style column")
            if any(tok in name for tok in ("status", "state", "type", "category", "segment")):
                semantic_bonus += 0.08
                reasons.append("Domain-defining column")
            if any(tok in name for tok in ("date", "time", "_at", "ts", "created", "updated")):
                semantic_bonus += 0.08
                reasons.append("Temporal logic surface")
            if any(
                tok in name
                for tok in ("amount", "revenue", "price", "cost", "total", "qty", "quantity")
            ):
                semantic_bonus += 0.07
                reasons.append("Metric-bearing field")
            if cent > 0.05:
                reasons.append("High graph betweenness / bridge pressure")
            if desc > 0.25:
                reasons.append("Large downstream blast radius potential")
            if ref_bonus:
                reasons.append("Foreign-key/reference participation")
            score = min(
                1.0,
                cent * 0.32
                + desc * 0.26
                + in_deg * 0.08
                + out_deg * 0.07
                + ref_bonus
                + nullable_penalty
                + semantic_bonus,
            )
            signals.append(
                GraphNodeSignal(
                    node_id=node_id,
                    score=score,
                    provider="heuristic",
                    heuristic_score=score,
                    reasons=reasons[:5],
                )
            )
        return GraphIntelligenceReport(
            provider="heuristic", enabled=True, status="heuristic", nodes=signals
        )

    def _compute_rgcn_scores(self) -> tuple[dict[str, float], list[str]]:
        warnings: list[str] = []
        model_path = Path(self.rgcn_model_path)
        if not model_path.exists():
            return {}, [f"RGCN checkpoint not found: {model_path}"]
        try:
            import torch
            from ..gnn.inference import create_pyg_data
            from ..gnn.models import build_model
        except Exception as exc:  # pragma: no cover - dependency path
            return {}, [f"RGCN dependencies unavailable: {exc}"]

        try:  # pragma: no cover - optional path
            checkpoint = torch.load(model_path, map_location="cpu")
            state = checkpoint.get("state_dict", checkpoint)
            meta = checkpoint.get("meta", {}) if isinstance(checkpoint, dict) else {}
            hidden_dim = int(meta.get("hidden_dim") or meta.get("hidden_channels") or 128)
            out_dim = int(meta.get("out_dim") or meta.get("embedding_dim") or 64)
            data, node_to_idx = create_pyg_data(self.graph_json)
            model = build_model(
                "rgcn",
                num_node_features=int(data.num_node_features),
                hidden_dim=hidden_dim,
                out_dim=out_dim,
            )
            model.load_state_dict(state, strict=False)
            model.eval()
            embeddings = model.get_embeddings(data.x, data.edge_index, data.edge_type)
            edge_index = data.edge_index
            idx_to_node = {idx: node_id for node_id, idx in node_to_idx.items()}
            scores: dict[str, float] = {}
            for idx in range(int(embeddings.shape[0])):
                node_id = idx_to_node[idx]
                neighbors = set()
                if edge_index.numel() > 0:
                    src_mask = edge_index[0] == idx
                    tgt_mask = edge_index[1] == idx
                    neighbors.update(edge_index[1][src_mask].tolist())
                    neighbors.update(edge_index[0][tgt_mask].tolist())
                if not neighbors:
                    scores[node_id] = 0.0
                    continue
                sims = []
                current = embeddings[idx]
                for nbr in neighbors:
                    sims.append(float((current * embeddings[nbr]).sum().item()))
                mean_sim = sum(sims) / max(len(sims), 1)
                scores[node_id] = max(
                    0.0, min(1.0, (1.0 - mean_sim) * 0.5 + min(1.0, len(neighbors) / 8.0) * 0.5)
                )
            return scores, warnings
        except Exception as exc:  # pragma: no cover - optional path
            return {}, [f"RGCN inference failed, using heuristic graph signals instead: {exc}"]
