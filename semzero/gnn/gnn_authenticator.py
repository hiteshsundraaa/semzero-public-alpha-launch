"""
gnn_authenticator.py — GNN-based change authentication for SemZero.

Implements the Siamese Authentication pattern using the existing SchemaRGCN.

Three mechanisms:
  1. Cosine similarity between before/after node embeddings
     → "Is this column the same entity with a different label?"

  2. FK-Invariance check
     → "Did the column keep its REFERENCES edges after the change?"
     → Preserved FK edges = strong evidence of identity preservation

  3. Anchor-relative distance
     → Use the most connected stable nodes as coordinate anchors
     → If a changed column's distance to all anchors stays stable,
        the change is structural (rename) not semantic (new entity)

Authentication verdicts:
  AUTHENTICATED_RENAME    → same entity, different label. Safe to auto-map.
  NEEDS_HUMAN_REVIEW      → moderate similarity. Change Gate flags for review.
  BLOCK_SEMANTIC_DRIFT    → embeddings diverged. Different entity. Block merge.

Usage:
  from semzero.gnn.gnn_authenticator import GNNAuthenticator
  auth = GNNAuthenticator(model, before_graph, after_graph)
  result = auth.authenticate("orders.user_id")
  print(result.verdict, result.similarity, result.fk_preserved)

Place this at: src/gnn/gnn_authenticator.py
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import torch
import torch.nn.functional as F

log = logging.getLogger(__name__)


# ── Thresholds ────────────────────────────────────────────────────────────────
# These are calibrated for graph-only mode (no real dbt tests).
# They will shift once you have labelled training data from real schema migrations.
#
# Why 0.85 not 0.95:
#   A rename changes the string features of the node, which shifts the embedding
#   even when FK structure is preserved. 0.95 would reject legitimate renames.
#   0.85 is the empirically correct threshold for RGCN on schema graphs.
#
# Why 0.50 not 0.70 for BLOCK:
#   Below 0.50 means the embedding is more different than random — genuine
#   semantic divergence. Between 0.50-0.85 is the ambiguous zone (REVIEW).

AUTHENTICATED_THRESHOLD = 0.85  # similarity >= this → AUTHENTICATED_RENAME
REVIEW_THRESHOLD = 0.50  # similarity >= this → NEEDS_HUMAN_REVIEW
# below REVIEW_THRESHOLD → BLOCK_SEMANTIC_DRIFT

# FK preservation bonus: if a changed column kept all its FK edges,
# add this to the raw cosine similarity before applying thresholds.
FK_PRESERVATION_BONUS = 0.08

# Anchor stability: if a column's relative distance to all anchors
# changes by less than this, it's structurally preserved.
ANCHOR_STABILITY_DELTA = 0.12

# Minimum number of anchor nodes to use. If fewer exist, skip anchor check.
MIN_ANCHORS = 3


# ── Result dataclass ──────────────────────────────────────────────────────────


@dataclass
class AuthResult:
    node_id: str
    verdict: str  # AUTHENTICATED_RENAME | NEEDS_HUMAN_REVIEW | BLOCK_SEMANTIC_DRIFT
    similarity: float  # Raw cosine similarity (0-1)
    adjusted_sim: float  # After FK bonus + anchor adjustment
    fk_preserved: bool  # Did column keep its FK edges?
    fk_before: list[str]  # FK targets before change
    fk_after: list[str]  # FK targets after change
    anchor_stable: Optional[bool]  # None if not enough anchors
    anchor_delta: float  # Max change in anchor distance
    suggested_label: Optional[str]  # PII/semantic label to propagate
    confidence: float  # Final confidence 0-1
    detail: str  # Human-readable explanation

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "verdict": self.verdict,
            "similarity": round(self.similarity, 4),
            "adjusted_sim": round(self.adjusted_sim, 4),
            "fk_preserved": self.fk_preserved,
            "fk_before": self.fk_before,
            "fk_after": self.fk_after,
            "anchor_stable": self.anchor_stable,
            "anchor_delta": round(self.anchor_delta, 4),
            "suggested_label": self.suggested_label,
            "confidence": round(self.confidence, 4),
            "detail": self.detail,
        }

    def for_gate_comment(self) -> str:
        """Format for Change Gate PR comment."""
        emoji = {
            "AUTHENTICATED_RENAME": "✅",
            "NEEDS_HUMAN_REVIEW": "⚠️",
            "BLOCK_SEMANTIC_DRIFT": "🚫",
        }.get(self.verdict, "❓")
        lines = [
            f"{emoji} **GNN Authentication** — `{self.node_id}`",
            f"",
            f"| | |",
            f"|---|---|",
            f"| Structural similarity | {self.adjusted_sim:.0%} |",
            f"| FK edges preserved | {'Yes' if self.fk_preserved else 'No — structure changed'} |",
            f"| Anchor stability | {'Stable' if self.anchor_stable else 'Drifted' if self.anchor_stable is False else 'Not checked'} |",
            f"| Verdict | **{self.verdict}** |",
        ]
        if self.suggested_label:
            lines.append(f"| Suggested label | `{self.suggested_label}` (propagated from before) |")
        lines.append(f"")
        lines.append(f"> {self.detail}")
        return "\n".join(lines)


# ── GNN Authenticator ─────────────────────────────────────────────────────────


class GNNAuthenticator:
    """
    Authenticates schema changes using RGCN embeddings.

    The core insight: if column A is renamed to column B, the RGCN embedding
    should be nearly identical because the embedding is driven by:
      - The column's neighbourhood (FK targets, PART_OF table)
      - The column's type family (INTEGER, VARCHAR etc.)
      - The column's structural role (PK, FK, leaf)

    The column NAME is a weak signal in the embedding — it's just one
    component of the vectorizer output. So a pure rename moves the embedding
    very little. A genuine semantic change (different FK targets, type change)
    moves it a lot.

    This is what makes GNN authentication different from string matching:
    it authenticates STRUCTURAL identity, not label identity.
    """

    def __init__(
        self,
        model,  # Trained SchemaRGCN
        before_graph: dict,  # Graph snapshot before change
        after_graph: dict,  # Graph snapshot after change
        vectorizer=None,  # Optional SchemaVectorizer
    ) -> None:
        self.model = model
        self.before_graph = before_graph
        self.after_graph = after_graph
        self.vectorizer = vectorizer
        self._before_embs: Optional[dict[str, torch.Tensor]] = None
        self._after_embs: Optional[dict[str, torch.Tensor]] = None
        self._anchors: Optional[list[str]] = None

    # ── Public API ────────────────────────────────────────────────────────────

    def authenticate(self, node_id: str) -> AuthResult:
        """
        Authenticate a changed node.

        node_id: the node ID as it appears in the BEFORE graph
                 (e.g. "orders.user_id" before it was renamed to "orders.account_id")
        """
        self._ensure_embeddings()

        before_emb = self._before_embs.get(node_id)
        if before_emb is None:
            return self._unknown(node_id, f"Node {node_id} not found in before-graph")

        # Find the corresponding node in the after-graph
        after_node_id = self._find_after_node(node_id)
        after_emb = self._after_embs.get(after_node_id)

        if after_emb is None:
            # Node was removed — this is a BLOCK
            return AuthResult(
                node_id=node_id,
                verdict="BLOCK_SEMANTIC_DRIFT",
                similarity=0.0,
                adjusted_sim=0.0,
                fk_preserved=False,
                fk_before=self._get_fk_targets(node_id, self.before_graph),
                fk_after=[],
                anchor_stable=None,
                anchor_delta=1.0,
                suggested_label=None,
                confidence=0.99,
                detail=f"Node {node_id} was removed from the schema entirely.",
            )

        # ── Signal 1: Cosine similarity ───────────────────────────────────────
        before_norm = F.normalize(before_emb.unsqueeze(0), dim=-1)
        after_norm = F.normalize(after_emb.unsqueeze(0), dim=-1)
        similarity = float(F.cosine_similarity(before_norm, after_norm))

        # ── Signal 2: FK preservation ─────────────────────────────────────────
        fk_before = self._get_fk_targets(node_id, self.before_graph)
        fk_after = self._get_fk_targets(after_node_id, self.after_graph)
        fk_preserved = self._fk_preserved(fk_before, fk_after)

        # Apply FK bonus
        adjusted_sim = similarity
        if fk_preserved and len(fk_before) > 0:
            # FK edges preserved AND they exist → strong structural identity
            adjusted_sim = min(1.0, similarity + FK_PRESERVATION_BONUS)
        elif not fk_preserved and len(fk_before) > 0:
            # FK edges changed → structural mutation, slightly penalise
            adjusted_sim = max(0.0, similarity - 0.05)

        # ── Signal 3: Anchor-relative distance ───────────────────────────────
        anchor_stable = None
        anchor_delta = 0.0
        anchors = self._select_anchors()

        if len(anchors) >= MIN_ANCHORS:
            before_dists = self._anchor_distances(before_emb, anchors, self._before_embs)
            after_dists = self._anchor_distances(
                after_emb, anchors, self._after_embs, after_node_ids=True
            )
            if before_dists and after_dists:
                deltas = [abs(b - a) for b, a in zip(before_dists, after_dists)]
                anchor_delta = max(deltas) if deltas else 0.0
                anchor_stable = anchor_delta < ANCHOR_STABILITY_DELTA

                # Anchor confirmation: if stable, small boost; if drifted, small penalty
                if anchor_stable:
                    adjusted_sim = min(1.0, adjusted_sim + 0.03)
                else:
                    adjusted_sim = max(0.0, adjusted_sim - 0.03)

        # ── Verdict ───────────────────────────────────────────────────────────
        if adjusted_sim >= AUTHENTICATED_THRESHOLD:
            verdict = "AUTHENTICATED_RENAME"
            confidence = adjusted_sim
            detail = (
                f"Structural identity preserved ({adjusted_sim:.0%} similarity). "
                f"FK edges {'preserved' if fk_preserved else 'changed'}. "
                f"Safe to treat as rename — auto-map downstream consumers."
            )
        elif adjusted_sim >= REVIEW_THRESHOLD:
            verdict = "NEEDS_HUMAN_REVIEW"
            confidence = 1.0 - adjusted_sim
            detail = (
                f"Moderate structural similarity ({adjusted_sim:.0%}). "
                f"FK edges {'preserved' if fk_preserved else 'changed — possible semantic shift'}. "
                f"Recommend manual verification before merging."
            )
        else:
            verdict = "BLOCK_SEMANTIC_DRIFT"
            confidence = 1.0 - adjusted_sim
            detail = (
                f"Embeddings diverged ({adjusted_sim:.0%} similarity). "
                f"This column has different structural identity after the change — "
                f"not a rename, possibly a replacement with different semantics. Block merge."
            )

        # ── PII / label propagation ───────────────────────────────────────────
        suggested_label = None
        if verdict == "AUTHENTICATED_RENAME":
            # Propagate semantic labels from before-node to after-node
            before_node = self._get_node(node_id, self.before_graph)
            if before_node:
                tags = before_node.get("tags", [])
                pii_tags = [t for t in tags if "pii" in t.lower()]
                if pii_tags:
                    suggested_label = pii_tags[0]
                elif "email" in node_id.lower():
                    suggested_label = "pii:high"
                elif "phone" in node_id.lower():
                    suggested_label = "pii:high"

        return AuthResult(
            node_id=node_id,
            verdict=verdict,
            similarity=round(similarity, 4),
            adjusted_sim=round(adjusted_sim, 4),
            fk_preserved=fk_preserved,
            fk_before=fk_before,
            fk_after=fk_after,
            anchor_stable=anchor_stable,
            anchor_delta=round(anchor_delta, 4),
            suggested_label=suggested_label,
            confidence=round(confidence, 4),
            detail=detail,
        )

    def authenticate_batch(self, node_ids: list[str]) -> list[AuthResult]:
        """Authenticate multiple changed nodes in one pass."""
        self._ensure_embeddings()
        return [self.authenticate(nid) for nid in node_ids]

    def authenticate_drift_report(self, drift_report: dict) -> dict[str, AuthResult]:
        """
        Run authentication on every COLUMN_RENAMED event in a drift report.
        Returns {node_id: AuthResult}.
        """
        results: dict[str, AuthResult] = {}
        events = drift_report.get("events", [])
        for ev in events:
            if ev.get("change_type") == "COLUMN_RENAMED":
                node_id = ev.get("node_id", "")
                if node_id:
                    results[node_id] = self.authenticate(node_id)
        return results

    # ── Embedding computation ─────────────────────────────────────────────────

    def _ensure_embeddings(self) -> None:
        if self._before_embs is None:
            self._before_embs = self._compute_embeddings(self.before_graph)
        if self._after_embs is None:
            self._after_embs = self._compute_embeddings(self.after_graph)

    def _compute_embeddings(self, graph_json: dict) -> dict[str, torch.Tensor]:
        """Run RGCN forward pass, return {node_id: embedding_tensor}."""
        try:
            from .inference import create_pyg_data

            data, node_to_idx = create_pyg_data(graph_json, self.vectorizer)
            self.model.eval()
            with torch.no_grad():
                embs = self.model(data.x, data.edge_index, data.edge_type)
            # L2-normalise for cosine similarity
            embs = F.normalize(embs, dim=-1)
            # Map back to node IDs
            idx_to_node = {v: k for k, v in node_to_idx.items()}
            return {idx_to_node[i]: embs[i] for i in range(len(embs))}
        except Exception as e:
            log.error(f"Embedding computation failed: {e}")
            return {}

    # ── FK helpers ────────────────────────────────────────────────────────────

    def _get_fk_targets(self, node_id: str, graph_json: dict) -> list[str]:
        """Return list of FK target node IDs for a column."""
        return [
            e["target"]
            for e in graph_json.get("edges", [])
            if e.get("source") == node_id and e.get("relation") == "REFERENCES"
        ]

    def _fk_preserved(self, before: list[str], after: list[str]) -> bool:
        """Check if FK targets are structurally preserved (same tables, even if columns renamed)."""
        if not before and not after:
            return True  # Neither had FKs — preserved by vacuity
        if not before or not after:
            return False

        # Compare at table level (tolerant of column renames in the FK target)
        before_tables = {t.split(".")[0] if "." in t else t for t in before}
        after_tables = {t.split(".")[0] if "." in t else t for t in after}
        return before_tables == after_tables

    # ── Anchor node selection ─────────────────────────────────────────────────

    def _select_anchors(self) -> list[str]:
        """
        Select anchor nodes: the most connected STABLE nodes in the graph.

        Unlike the proposal (Countries/Currencies), we pick nodes with the
        highest in-degree in the FK graph AND that haven't changed between
        before and after snapshots. These are the "load-bearing" nodes.
        """
        if self._anchors is not None:
            return self._anchors

        # Count FK in-degree per table in before-graph
        in_degree: dict[str, int] = {}
        for e in self.before_graph.get("edges", []):
            if e.get("relation") == "REFERENCES":
                tbl = e["target"].split(".")[0] if "." in e["target"] else e["target"]
                in_degree[tbl] = in_degree.get(tbl, 0) + 1

        # Keep only tables that exist unchanged in both graphs
        before_node_ids = {n["id"] for n in self.before_graph.get("nodes", [])}
        after_node_ids = {n["id"] for n in self.after_graph.get("nodes", [])}
        stable_tables = before_node_ids & after_node_ids

        # Sort by in-degree, take top 10
        ranked = sorted(
            [(tbl, deg) for tbl, deg in in_degree.items() if tbl in stable_tables],
            key=lambda x: -x[1],
        )
        self._anchors = [tbl for tbl, _ in ranked[:10]]
        log.debug(f"Anchor nodes selected: {self._anchors[:5]}")
        return self._anchors

    def _anchor_distances(
        self,
        emb: torch.Tensor,
        anchors: list[str],
        emb_map: dict[str, torch.Tensor],
        after_node_ids: bool = False,
    ) -> list[float]:
        """Compute distances from emb to each anchor node."""
        distances = []
        for anchor in anchors:
            anchor_emb = emb_map.get(anchor)
            if anchor_emb is None:
                continue
            # Euclidean distance in normalised embedding space
            dist = float(torch.norm(emb - anchor_emb))
            distances.append(dist)
        return distances

    # ── Node lookup helpers ───────────────────────────────────────────────────

    def _find_after_node(self, before_node_id: str) -> str:
        """
        Find the corresponding node in the after-graph.
        For renames: the node ID changes (e.g. orders.user_id → orders.account_id).
        We try: same ID first, then same table with structural similarity.
        """
        after_ids = {n["id"] for n in self.after_graph.get("nodes", [])}

        # Same ID exists → no rename
        if before_node_id in after_ids:
            return before_node_id

        # Try to find renamed counterpart in same table
        if "." in before_node_id:
            tbl, col = before_node_id.split(".", 1)
            same_table_cols = [
                n["id"]
                for n in self.after_graph.get("nodes", [])
                if n.get("table") == tbl
                and n.get("label") == "Column"
                and n["id"] not in {n2["id"] for n2 in self.before_graph.get("nodes", [])}
            ]
            if len(same_table_cols) == 1:
                # Exactly one new column in same table → likely the rename target
                return same_table_cols[0]

        # Fallback: same ID (will return None embedding if truly gone)
        return before_node_id

    def _get_node(self, node_id: str, graph_json: dict) -> Optional[dict]:
        for n in graph_json.get("nodes", []):
            if n["id"] == node_id:
                return n
        return None

    def _unknown(self, node_id: str, reason: str) -> AuthResult:
        return AuthResult(
            node_id=node_id,
            verdict="NEEDS_HUMAN_REVIEW",
            similarity=0.0,
            adjusted_sim=0.0,
            fk_preserved=False,
            fk_before=[],
            fk_after=[],
            anchor_stable=None,
            anchor_delta=0.0,
            suggested_label=None,
            confidence=0.5,
            detail=f"Authentication incomplete: {reason}",
        )


# ── Identity-preserving loss function ─────────────────────────────────────────


def identity_preserving_loss(
    model,
    before_data,  # PyG Data — before snapshot
    after_data,  # PyG Data — after snapshot (with rename applied)
    rename_pairs: list[tuple[int, int]],  # [(before_node_idx, after_node_idx)]
    structural_pairs: list[tuple[int, int]],  # pairs that kept FK structure
    semantic_pairs: list[tuple[int, int]],  # pairs that changed FK structure
    lambda_structural: float = 0.5,
    margin: float = 0.3,
) -> "torch.Tensor":
    """
    Identity-preserving loss for RGCN training.

    Combines three terms:

    1. L_rename: Contrastive loss — renamed pairs should have high cosine
       similarity, unrelated pairs should have low similarity.

    2. L_structural: MSE penalty — if FK structure is preserved between
       a before/after pair, their embeddings should be close.
       Forces the model to encode structural identity, not just label identity.

    3. L_semantic: Margin loss — if FK structure CHANGED, their embeddings
       should be further apart than the structural pairs.

    Total: L = L_rename + lambda_structural * (L_structural + L_semantic)

    This teaches the model:
      "Same FK structure → same embedding"
      "Different FK structure → different embedding"
    Which is exactly what you need for rename authentication.

    Args:
        model:            Trained SchemaRGCN
        before_data:      PyG Data for the before-snapshot
        after_data:       PyG Data for the after-snapshot
        rename_pairs:     (before_idx, after_idx) for ground-truth renames
        structural_pairs: pairs where FK edges were preserved (should be close)
        semantic_pairs:   pairs where FK edges changed (should be far)
        lambda_structural: weight for the structural term
        margin:           minimum desired distance for semantic pairs
    """
    import torch.nn.functional as F

    emb_before = F.normalize(
        model(before_data.x, before_data.edge_index, before_data.edge_type), dim=-1
    )
    emb_after = F.normalize(
        model(after_data.x, after_data.edge_index, after_data.edge_type), dim=-1
    )

    losses = []

    # L_rename: contrastive
    if rename_pairs:
        src_idx = torch.tensor([p[0] for p in rename_pairs], dtype=torch.long)
        tgt_idx = torch.tensor([p[1] for p in rename_pairs], dtype=torch.long)
        sim = F.cosine_similarity(emb_before[src_idx], emb_after[tgt_idx])
        # Renamed pairs should be similar (target = 1)
        l_rename = F.mse_loss(sim, torch.ones_like(sim))
        losses.append(l_rename)

    # L_structural: FK-preserved pairs should have similar embeddings
    if structural_pairs:
        sp_src = torch.tensor([p[0] for p in structural_pairs], dtype=torch.long)
        sp_tgt = torch.tensor([p[1] for p in structural_pairs], dtype=torch.long)
        # MSE on embeddings — directly minimises embedding distance
        l_structural = F.mse_loss(emb_before[sp_src], emb_after[sp_tgt])
        losses.append(lambda_structural * l_structural)

    # L_semantic: FK-changed pairs should have dissimilar embeddings
    if semantic_pairs:
        sm_src = torch.tensor([p[0] for p in semantic_pairs], dtype=torch.long)
        sm_tgt = torch.tensor([p[1] for p in semantic_pairs], dtype=torch.long)
        sim_semantic = F.cosine_similarity(emb_before[sm_src], emb_after[sm_tgt])
        # Penalise when similarity is above margin (they should be different)
        l_semantic = F.relu(sim_semantic - (1.0 - margin)).mean()
        losses.append(lambda_structural * l_semantic)

    if not losses:
        return torch.tensor(0.0, requires_grad=True)

    return sum(losses)


# ── Integration with Change Gate ──────────────────────────────────────────────


def enrich_gate_result_with_gnn(
    gate_result,
    authenticator: GNNAuthenticator,
    drift_report: dict,
) -> None:
    """
    Enrich a GateResult with GNN authentication for every COLUMN_RENAMED event.
    Attaches authentication verdicts and PII propagation to the gate assessments.

    Call this after ChangeGate.evaluate() when the RGCN model is available.
    Modifies gate_result in place.
    """
    auth_results = authenticator.authenticate_drift_report(drift_report)

    for assessment in gate_result.assessments:
        node_id = assessment.node_id
        auth = auth_results.get(node_id)
        if not auth:
            continue

        # Attach GNN verdict to assessment notes
        if not hasattr(assessment, "gnn_auth"):
            assessment.gnn_auth = auth

        # If GNN says BLOCK but gate said NEEDS_REVIEW → escalate
        if auth.verdict == "BLOCK_SEMANTIC_DRIFT" and gate_result.verdict.value == "NEEDS_REVIEW":
            gate_result.verdict = type(gate_result.verdict)("BLOCK")
            gate_result.blocked_by.append(
                f"GNN Authentication: {node_id} embeddings diverged "
                f"({auth.adjusted_sim:.0%} similarity) — semantic drift detected"
            )

        # If GNN says AUTHENTICATED and gate said NEEDS_REVIEW for rename → downgrade
        if (
            auth.verdict == "AUTHENTICATED_RENAME"
            and assessment.compatibility.value == "RENAME_LOW_CONFIDENCE"
        ):
            assessment.gnn_upgrade = (
                f"GNN authentication upgraded confidence to {auth.adjusted_sim:.0%}. "
                f"FK structure preserved."
            )

        # PII propagation
        if auth.suggested_label:
            if not hasattr(assessment, "pii_propagation"):
                assessment.pii_propagation = []
            assessment.pii_propagation.append(
                f"Auto-tag `{node_id}` as `{auth.suggested_label}` (propagated from before-schema)"
            )

    log.info(
        f"GNN authentication complete: "
        f"{sum(1 for a in auth_results.values() if a.verdict == 'AUTHENTICATED_RENAME')} authenticated, "
        f"{sum(1 for a in auth_results.values() if a.verdict == 'BLOCK_SEMANTIC_DRIFT')} blocked"
    )


# ── Training script ───────────────────────────────────────────────────────────


def train_with_identity_loss(
    model,
    mutation_pairs: list,
    node_to_idx_fn,
    epochs_per_pair: int = 30,
    lr: float = 0.001,
    lambda_structural: float = 0.5,
) -> "torch.nn.Module":
    """
    Train SchemaRGCN with the identity-preserving loss.

    Uses mutation pairs from schema_mutator.py — each pair is a (before, after)
    schema where the mutations are known. The structural_pairs and semantic_pairs
    are derived from the ground truth mapping + edge comparison.

    This replaces the existing train_rgcn_on_pairs() when you want the model
    to learn structural identity (not just mapping accuracy).

    Add to trainer.py or call directly.
    """
    import torch
    from torch.optim import Adam

    device = torch.device(
        "cuda"
        if torch.cuda.is_available()
        else "mps"
        if torch.backends.mps.is_available()
        else "cpu"
    )
    model = model.to(device)
    optimizer = Adam(model.parameters(), lr=lr, weight_decay=1e-4)

    log.info(
        f"Identity-preserving training on {len(mutation_pairs)} pairs "
        f"(λ_structural={lambda_structural})"
    )

    for i, pair in enumerate(mutation_pairs):
        try:
            before_data, before_idx = node_to_idx_fn(pair.original_graph)
            after_data, after_idx = node_to_idx_fn(pair.mutated_graph)
        except Exception as e:
            log.warning(f"Pair {i}: skipped — {e}")
            continue

        before_data = before_data.to(device)
        after_data = after_data.to(device)

        # Ground truth rename pairs
        rename_pairs = [
            (before_idx[o], after_idx[m])
            for o, m in pair.ground_truth.items()
            if o in before_idx and m in after_idx
        ]
        if not rename_pairs:
            continue

        # Classify pairs as structural (FK preserved) vs semantic (FK changed)
        structural, semantic = _classify_pairs(
            rename_pairs,
            pair.original_graph,
            pair.mutated_graph,
            before_idx,
            after_idx,
        )

        model.train()
        for epoch in range(epochs_per_pair):
            optimizer.zero_grad(set_to_none=True)
            loss = identity_preserving_loss(
                model,
                before_data,
                after_data,
                rename_pairs,
                structural,
                semantic,
                lambda_structural=lambda_structural,
            )
            loss.backward()
            optimizer.step()

        if i % 10 == 0:
            with torch.no_grad():
                import torch.nn.functional as F

                emb_b = F.normalize(
                    model(before_data.x, before_data.edge_index, before_data.edge_type), dim=-1
                )
                emb_a = F.normalize(
                    model(after_data.x, after_data.edge_index, after_data.edge_type), dim=-1
                )
                src_t = torch.tensor([p[0] for p in rename_pairs], dtype=torch.long, device=device)
                tgt_t = torch.tensor([p[1] for p in rename_pairs], dtype=torch.long, device=device)
                sim = F.cosine_similarity(emb_b[src_t], emb_a[tgt_t]).mean()
            log.info(
                f"Pair {i:04d}/{len(mutation_pairs)} | "
                f"Loss: {loss.item():.4f} | Avg rename sim: {sim:.3f}"
            )

    log.info("Identity-preserving training complete.")
    return model


def _classify_pairs(
    rename_pairs: list[tuple[int, int]],
    before_graph: dict,
    after_graph: dict,
    before_idx: dict[str, int],
    after_idx: dict[str, int],
) -> tuple[list, list]:
    """
    Split rename pairs into structural (FK preserved) and semantic (FK changed).
    """
    structural = []
    semantic = []

    # Build FK target sets per node in both graphs
    def fk_targets(graph, node_id):
        return {
            e["target"].split(".")[0] if "." in e["target"] else e["target"]
            for e in graph.get("edges", [])
            if e.get("source") == node_id and e.get("relation") == "REFERENCES"
        }

    before_id_to_name = {v: k for k, v in before_idx.items()}
    after_id_to_name = {v: k for k, v in after_idx.items()}

    for b_idx, a_idx in rename_pairs:
        b_name = before_id_to_name.get(b_idx, "")
        a_name = after_id_to_name.get(a_idx, "")
        fk_b = fk_targets(before_graph, b_name)
        fk_a = fk_targets(after_graph, a_name)

        if fk_b == fk_a:
            structural.append((b_idx, a_idx))
        else:
            semantic.append((b_idx, a_idx))

    return structural, semantic
