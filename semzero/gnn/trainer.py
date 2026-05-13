"""
trainer.py — GNN training: contrastive loss (GCN/SAGE) and
cross-entropy on score matrix (SchemaRGCN).

Cross-entropy training follows Orvalho et al. (arXiv:2307.13014):
  Train on (source_graph, target_graph, ground_truth_mapping) triples.
  Loss = cross_entropy(softmax(emb_src @ emb_tgt.T)[src_col_i], tgt_col_j)
  Model learns to predict correct mappings from topology alone.
"""

from __future__ import annotations
import logging
from typing import Optional
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.optim import Adam
from torch_geometric.data import Data
from .models import compute_match_scores

log = logging.getLogger(__name__)


def get_device() -> torch.device:
    if torch.cuda.is_available():
        return torch.device("cuda")
    if torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def train_gnn(
    model: nn.Module,
    data: Data,
    pos_pairs: torch.Tensor,
    neg_pairs: torch.Tensor,
    epochs: int = 500,
    lr: float = 0.005,
) -> nn.Module:
    """Contrastive training for GCN/SAGE."""
    if pos_pairs.ndim != 2 or pos_pairs.shape[1] != 2:
        raise ValueError(f"pos_pairs must be [N,2], got {pos_pairs.shape}")
    if neg_pairs.ndim != 2 or neg_pairs.shape[1] != 2:
        raise ValueError(f"neg_pairs must be [M,2], got {neg_pairs.shape}")

    device = get_device()
    model = model.to(device)
    data = data.to(device)
    pos_pairs = pos_pairs.to(device)
    neg_pairs = neg_pairs.to(device)
    optimizer = Adam(model.parameters(), lr=lr)
    edge_type = getattr(data, "edge_type", None)
    model.train()

    for epoch in range(epochs):
        optimizer.zero_grad(set_to_none=True)
        emb = model(data.x, data.edge_index, edge_type)
        pos_loss = F.cosine_embedding_loss(
            emb[pos_pairs[:, 0]], emb[pos_pairs[:, 1]], torch.ones(len(pos_pairs), device=device)
        )
        neg_loss = F.cosine_embedding_loss(
            emb[neg_pairs[:, 0]], emb[neg_pairs[:, 1]], -torch.ones(len(neg_pairs), device=device)
        )
        loss = pos_loss + neg_loss
        loss.backward()
        optimizer.step()
        if epoch % 50 == 0:
            log.info(f"Epoch {epoch:03d} | Loss: {loss.item():.4f}")

    log.info("Contrastive training complete.")
    return model


def train_rgcn(
    model: nn.Module,
    source_data: Data,
    target_data: Data,
    ground_truth: dict[int, int],
    epochs: int = 100,
    lr: float = 0.001,
    weight_decay: float = 1e-4,
) -> nn.Module:
    """
    Cross-entropy training for SchemaRGCN on a single schema pair.

    ground_truth: {source_col_node_idx: target_col_node_idx}
    """
    if not ground_truth:
        raise ValueError("ground_truth must not be empty.")

    device = get_device()
    model = model.to(device)
    source_data = source_data.to(device)
    target_data = target_data.to(device)

    src_indices = torch.tensor(list(ground_truth.keys()), dtype=torch.long, device=device)
    tgt_labels = torch.tensor(list(ground_truth.values()), dtype=torch.long, device=device)
    optimizer = Adam(model.parameters(), lr=lr, weight_decay=weight_decay)
    model.train()

    for epoch in range(epochs):
        optimizer.zero_grad(set_to_none=True)
        emb_src = model(source_data.x, source_data.edge_index, source_data.edge_type)
        emb_tgt = model(target_data.x, target_data.edge_index, target_data.edge_type)
        logits = emb_src @ emb_tgt.T
        loss = F.cross_entropy(logits[src_indices], tgt_labels)
        loss.backward()
        optimizer.step()

        if epoch % 10 == 0:
            with torch.no_grad():
                acc = (logits[src_indices].argmax(dim=-1) == tgt_labels).float().mean()
            log.info(f"Epoch {epoch:03d} | Loss: {loss.item():.4f} | Acc: {acc:.2%}")

    log.info("RGCN training complete.")
    return model


def train_rgcn_on_pairs(
    model: nn.Module,
    pairs: list,
    node_to_idx_fn,
    epochs_per_pair: int = 20,
    lr: float = 0.001,
) -> nn.Module:
    """
    Train SchemaRGCN on a list of MutationPairs from schema_mutator.py.
    Each pair is one training example with known ground truth mapping.
    """
    device = get_device()
    model = model.to(device)
    optimizer = Adam(model.parameters(), lr=lr, weight_decay=1e-4)
    log.info(f"Training on {len(pairs)} mutation pairs.")

    for i, pair in enumerate(pairs):
        try:
            src_data, src_idx = node_to_idx_fn(pair.original_graph)
            tgt_data, tgt_idx = node_to_idx_fn(pair.mutated_graph)
        except Exception as e:
            log.warning(f"Pair {i}: skipped — {e}")
            continue

        src_data = src_data.to(device)
        tgt_data = tgt_data.to(device)

        gt: dict[int, int] = {
            src_idx[o]: tgt_idx[m]
            for o, m in pair.ground_truth.items()
            if o in src_idx and m in tgt_idx
        }
        if not gt:
            continue

        src_t = torch.tensor(list(gt.keys()), dtype=torch.long, device=device)
        tgt_t = torch.tensor(list(gt.values()), dtype=torch.long, device=device)

        model.train()
        for _ in range(epochs_per_pair):
            optimizer.zero_grad(set_to_none=True)
            emb_s = model(src_data.x, src_data.edge_index, src_data.edge_type)
            emb_t = model(tgt_data.x, tgt_data.edge_index, tgt_data.edge_type)
            loss = F.cross_entropy((emb_s @ emb_t.T)[src_t], tgt_t)
            loss.backward()
            optimizer.step()

        if i % 10 == 0:
            with torch.no_grad():
                acc = ((emb_s @ emb_t.T)[src_t].argmax(-1) == tgt_t).float().mean()
            log.info(f"Pair {i:04d}/{len(pairs)} | Loss: {loss.item():.4f} | Acc: {acc:.2%}")

    log.info("Training on mutation pairs complete.")
    return model
