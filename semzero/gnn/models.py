"""
models.py — GNN architectures for schema embedding.

Three architectures:
  SchemaGCN   — 2-layer GCN baseline. Fast, good for small graphs.
  SchemaSAGE  — GraphSAGE with edge-type conditioning.
  SchemaRGCN  — Relational GCN. Best for cross-schema column matching.
                Handles PART_OF, REFERENCES, DEPENDS_ON as separate learned
                relations. Inspired by Orvalho et al. (arXiv:2307.13014)
                which achieved 96.49% variable mapping accuracy using RGCN.

All architectures return L2-normalised embeddings so:
  cosine_similarity(a, b) == dot(a, b)

The full N×M match score matrix is then a single matrix multiply:
  scores = emb_source @ emb_target.T   # [N_src, N_tgt]
"""

from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch_geometric.nn import GCNConv, SAGEConv, RGCNConv

# Edge type registry — keep in sync with inference.py EDGE_TYPE_MAP
EDGE_TYPES = {
    "PART_OF": 0,
    "REFERENCES": 1,
    "DEPENDS_ON": 2,
    "UNKNOWN": 3,
}
NUM_EDGE_TYPES = len(EDGE_TYPES)


class SchemaGCN(nn.Module):
    """Two-layer GCN baseline."""

    def __init__(
        self,
        num_node_features: int,
        hidden_dim: int,
        out_dim: int,
        dropout: float = 0.5,
    ) -> None:
        super().__init__()
        self.conv1 = GCNConv(num_node_features, hidden_dim)
        self.conv2 = GCNConv(hidden_dim, out_dim)
        self.dropout = dropout

    def forward(
        self,
        x: torch.Tensor,
        edge_index: torch.Tensor,
        edge_type: torch.Tensor | None = None,
    ) -> torch.Tensor:
        x = F.relu(self.conv1(x, edge_index))
        x = F.dropout(x, p=self.dropout, training=self.training)
        return self.conv2(x, edge_index)


class SchemaSAGE(nn.Module):
    """GraphSAGE with manual edge-type conditioning."""

    def __init__(
        self,
        num_node_features: int,
        hidden_dim: int,
        out_dim: int,
        dropout: float = 0.4,
        num_layers: int = 3,
    ) -> None:
        super().__init__()
        self.dropout = dropout
        dims = [num_node_features] + [hidden_dim] * (num_layers - 1) + [out_dim]
        self.convs = nn.ModuleList([SAGEConv(dims[i], dims[i + 1]) for i in range(num_layers)])
        self.edge_type_emb = nn.Embedding(NUM_EDGE_TYPES, hidden_dim)
        self.norms = nn.ModuleList([nn.LayerNorm(dims[i + 1]) for i in range(num_layers - 1)])
        self.proj = nn.Linear(out_dim, out_dim, bias=False)

    def forward(
        self,
        x: torch.Tensor,
        edge_index: torch.Tensor,
        edge_type: torch.Tensor | None = None,
    ) -> torch.Tensor:
        for i, conv in enumerate(self.convs):
            x = conv(x, edge_index)
            if edge_type is not None and i < len(self.convs) - 1:
                edge_bias = self.edge_type_emb(edge_type.clamp(0, NUM_EDGE_TYPES - 1))
                agg = torch.zeros(x.size(0), edge_bias.size(1), device=x.device)
                agg.scatter_add_(0, edge_index[1].unsqueeze(1).expand_as(edge_bias), edge_bias)
                x = x + agg
            if i < len(self.norms):
                x = self.norms[i](x)
                x = F.gelu(x)
                x = F.dropout(x, p=self.dropout, training=self.training)
        return F.normalize(self.proj(x), p=2, dim=-1)


class SchemaRGCN(nn.Module):
    """
    Relational GCN for cross-schema column matching.

    The RGCN update rule per node i:
        x'_i = Θ_root·x_i + Σ_r Σ_{j∈N_r(i)} (1/|N_r(i)|)·Θ_r·x_j

    Each relation type (PART_OF, REFERENCES, DEPENDS_ON) gets its own
    weight matrix Θ_r — the model learns separate propagation rules per
    edge type rather than treating all edges identically.

    Why this beats SchemaSAGE for matching:
      SchemaSAGE manually adds an edge-type embedding as a bias after
      aggregation. RGCN learns the edge-type weighting natively as part
      of the message passing itself — structurally cleaner and more
      expressive at the same parameter count.

    Basis decomposition (use_basis=True) decomposes each Θ_r into a
    linear combination of shared basis matrices, reducing parameters
    when num_relations is large.
    """

    def __init__(
        self,
        num_node_features: int,
        hidden_dim: int,
        out_dim: int,
        num_relations: int = NUM_EDGE_TYPES,
        num_layers: int = 3,
        dropout: float = 0.4,
        use_basis: bool = True,
        num_bases: int = 4,
    ) -> None:
        super().__init__()
        if num_node_features < 1:
            raise ValueError(f"num_node_features must be >= 1")

        self.dropout = dropout
        self.num_relations = num_relations

        dims = [num_node_features] + [hidden_dim] * (num_layers - 1) + [out_dim]

        self.convs = nn.ModuleList(
            [
                RGCNConv(
                    in_channels=dims[i],
                    out_channels=dims[i + 1],
                    num_relations=num_relations,
                    num_bases=num_bases if use_basis else None,
                    aggr="mean",
                )
                for i in range(num_layers)
            ]
        )

        self.norms = nn.ModuleList([nn.LayerNorm(dims[i + 1]) for i in range(num_layers - 1)])

        self.proj = nn.Linear(out_dim, out_dim, bias=False)
        self._init_weights()

    def _init_weights(self) -> None:
        for m in self.modules():
            if isinstance(m, nn.Linear):
                nn.init.xavier_uniform_(m.weight)
                if m.bias is not None:
                    nn.init.zeros_(m.bias)

    def forward(
        self,
        x: torch.Tensor,
        edge_index: torch.Tensor,
        edge_type: torch.Tensor,
    ) -> torch.Tensor:
        """
        Args:
            x:          [N, num_node_features]
            edge_index: [2, E]
            edge_type:  [E] integer relation type per edge

        Returns:
            [N, out_dim] L2-normalised embeddings
        """
        if edge_type is None:
            raise ValueError("SchemaRGCN requires edge_type. Use create_pyg_data().")

        edge_type = edge_type.clamp(0, self.num_relations - 1)

        for i, conv in enumerate(self.convs):
            x = conv(x, edge_index, edge_type)
            if i < len(self.norms):
                x = self.norms[i](x)
                x = F.gelu(x)
                x = F.dropout(x, p=self.dropout, training=self.training)

        return F.normalize(self.proj(x), p=2, dim=-1)

    @torch.no_grad()
    def get_embeddings(
        self,
        x: torch.Tensor,
        edge_index: torch.Tensor,
        edge_type: torch.Tensor,
    ) -> torch.Tensor:
        """Inference-only — no gradient tracking."""
        self.eval()
        return self(x, edge_index, edge_type)


def compute_match_scores(
    emb_source: torch.Tensor,
    emb_target: torch.Tensor,
) -> torch.Tensor:
    """
    Full N_src × N_tgt match score matrix in one matrix multiply.

    Because embeddings are L2-normalised, dot product == cosine similarity.
    Softmax per row gives match probability distribution for each source column.

    Args:
        emb_source: [N_src, d] L2-normalised
        emb_target: [N_tgt, d] L2-normalised

    Returns:
        [N_src, N_tgt] match probabilities — scores[i,j] = P(source_i maps to target_j)
    """
    return torch.softmax(emb_source @ emb_target.T, dim=-1)


def build_model(
    architecture: str,
    num_node_features: int,
    hidden_dim: int = 128,
    out_dim: int = 64,
    **kwargs,
) -> nn.Module:
    """
    Factory. architecture: 'gcn' | 'sage' | 'rgcn' (recommended for matching)
    """
    arch = architecture.lower()
    if arch == "gcn":
        return SchemaGCN(num_node_features, hidden_dim, out_dim, **kwargs)
    if arch == "sage":
        return SchemaSAGE(num_node_features, hidden_dim, out_dim, **kwargs)
    if arch == "rgcn":
        return SchemaRGCN(num_node_features, hidden_dim, out_dim, **kwargs)
    raise ValueError(f"Unknown architecture '{architecture}'. Choose 'gcn', 'sage', or 'rgcn'.")
