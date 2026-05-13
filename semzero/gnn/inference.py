"""
inference.py — Converts schema graph JSON to PyG Data for GNN inference.

Now produces edge_type tensor required by SchemaRGCN.
The edge_type maps each edge to an integer relation index:
  PART_OF    → 0
  REFERENCES → 1
  DEPENDS_ON → 2
  UNKNOWN    → 3
"""

from __future__ import annotations

import logging
from typing import Optional

import torch
from torch_geometric.data import Data

from .vectorizer import SchemaVectorizer
from .models import EDGE_TYPES, NUM_EDGE_TYPES

log = logging.getLogger(__name__)

# Map relation strings to integer indices
EDGE_TYPE_MAP: dict[str, int] = {
    "PART_OF": 0,
    "REFERENCES": 1,
    "DEPENDS_ON": 2,
}
_UNKNOWN_TYPE = NUM_EDGE_TYPES - 1


def create_pyg_data(
    graph_json: dict,
    vectorizer: Optional[SchemaVectorizer] = None,
) -> tuple[Data, dict[str, int]]:
    """
    Converts a schema graph dict into a PyTorch Geometric Data object.

    Returns Data with:
      x          — node feature matrix [N, num_features]
      edge_index — graph connectivity   [2, E]
      edge_type  — relation type per edge [E] — required by SchemaRGCN

    Args:
        graph_json:  Output of SchemaGraphBuilder.build()
        vectorizer:  Optional SchemaVectorizer. Creates default if not provided.

    Returns:
        Tuple of (Data, node_to_idx) where node_to_idx maps node ID strings
        to integer row indices in the feature matrix.
    """
    nodes = graph_json.get("nodes", [])
    edges = graph_json.get("edges", [])

    if not nodes:
        raise ValueError("graph_json contains no nodes — cannot create PyG Data.")

    if vectorizer is None:
        vectorizer = SchemaVectorizer()

    # Map node IDs to 0..N-1 indices
    node_to_idx: dict[str, int] = {node["id"]: i for i, node in enumerate(nodes)}

    # Feature matrix [N, num_features]
    x = torch.stack([vectorizer.vectorize_node(n) for n in nodes])

    # Edge index and edge types
    edge_list: list[list[int]] = []
    type_list: list[int] = []
    skipped = 0

    for edge in edges:
        src = edge.get("source", "")
        tgt = edge.get("target", "")

        if src not in node_to_idx or tgt not in node_to_idx:
            skipped += 1
            continue

        edge_list.append([node_to_idx[src], node_to_idx[tgt]])
        relation = edge.get("relation", "UNKNOWN")
        type_list.append(EDGE_TYPE_MAP.get(relation, _UNKNOWN_TYPE))

    if skipped:
        log.warning(f"Skipped {skipped} edges with unknown node IDs.")

    if edge_list:
        edge_index = torch.tensor(edge_list, dtype=torch.long).t().contiguous()
        edge_type = torch.tensor(type_list, dtype=torch.long)
    else:
        edge_index = torch.zeros((2, 0), dtype=torch.long)
        edge_type = torch.zeros(0, dtype=torch.long)

    data = Data(x=x, edge_index=edge_index, edge_type=edge_type)

    log.info(
        f"PyG Data: {data.num_nodes} nodes, {data.num_edges} edges, "
        f"{data.num_node_features} features, "
        f"{NUM_EDGE_TYPES} relation types."
    )
    return data, node_to_idx
