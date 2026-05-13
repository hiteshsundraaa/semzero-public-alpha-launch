"""
schema_mutator.py — Synthetic training data generator for SchemaRGCN.

Inspired by Orvalho et al. (arXiv:2307.13014) who generated training pairs
by mutating correct programs to create buggy ones with known ground truth.

Applied to schemas: take a real crawled schema graph, apply mutations
(renames, type changes, additions, removals) and you get pairs of schemas
with known ground truth column mappings — exactly what the RGCN needs
for contrastive training.

After crawling 10 real databases you can generate thousands of training
pairs synthetically, solving the cold-start training data problem.

Usage:
  from semzero.gnn.schema_mutator import SchemaMutator
  mutator  = SchemaMutator(original_graph)
  pairs    = mutator.generate_pairs(n=50)
  # pairs[i] = (mutated_graph, ground_truth_mapping)
  # ground_truth_mapping = {source_col_id: target_col_id}

Training:
  Use pairs to train SchemaRGCN with cross-entropy loss on the
  softmax score matrix (same approach as the paper).
"""

from __future__ import annotations

import copy
import json
import logging
import random
import re
import string
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# Probability of applying each mutation type per pair generation
_MUTATION_PROBS = {
    "rename_column": 0.60,  # Most common real-world drift
    "rename_table": 0.15,
    "add_column": 0.50,
    "remove_column": 0.20,
    "change_nullable": 0.25,
    "change_dtype": 0.15,
}

# Safe dtype substitutions that preserve semantics
_DTYPE_SUBSTITUTIONS = {
    "INTEGER": ["BIGINT", "SMALLINT"],
    "BIGINT": ["INTEGER"],
    "VARCHAR": ["TEXT", "NVARCHAR"],
    "TEXT": ["VARCHAR"],
    "FLOAT": ["NUMERIC", "DECIMAL"],
    "NUMERIC": ["FLOAT", "DECIMAL"],
}

# Common column name patterns for realistic renames
_RENAME_PATTERNS = [
    ("{name}", "{name}_id"),
    ("{name}", "{name}_key"),
    ("{name}", "{name}_legacy"),
    ("{name}", "old_{name}"),
    ("customer_{name}", "client_{name}"),
    ("user_{name}", "account_{name}"),
    ("created_at", "created_timestamp"),
    ("updated_at", "updated_timestamp"),
    ("email", "email_address"),
    ("phone", "phone_number"),
    ("id", "identifier"),
    ("name", "full_name"),
]


@dataclass
class MutationPair:
    """One training example: original schema → mutated schema with ground truth."""

    original_graph: dict
    mutated_graph: dict
    ground_truth: dict[str, str]  # {original_col_id: mutated_col_id}
    mutations_applied: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "mutations_applied": self.mutations_applied,
            "ground_truth_count": len(self.ground_truth),
            "ground_truth": self.ground_truth,
        }


class SchemaMutator:
    """
    Generates synthetic training pairs by mutating a real schema graph.

    Each pair has:
      - original_graph: the crawled schema
      - mutated_graph:  the same schema with some changes applied
      - ground_truth:   the known column mapping between them

    The RGCN is trained to predict ground_truth from the graph structure
    alone — ignoring column names entirely (same as the paper's approach).
    """

    def __init__(
        self,
        original_graph: dict,
        seed: int = 42,
    ) -> None:
        self.original_graph = original_graph
        self.rng = random.Random(seed)

        # Build column index for fast lookup
        self._tables = [n for n in original_graph["nodes"] if n["label"] == "Table"]
        self._cols = [n for n in original_graph["nodes"] if n["label"] == "Column"]

        if not self._cols:
            raise ValueError("Graph has no column nodes — cannot generate mutations.")

        log.info(
            f"SchemaMutator initialised: {len(self._tables)} tables, {len(self._cols)} columns."
        )

    def generate_pairs(self, n: int = 100) -> list[MutationPair]:
        """
        Generate n mutation pairs.

        Each call applies a random combination of mutations to a fresh
        copy of the original graph.
        """
        pairs: list[MutationPair] = []
        for i in range(n):
            pair = self._generate_one()
            if pair:
                pairs.append(pair)

        log.info(f"Generated {len(pairs)} mutation pairs.")
        return pairs

    def _generate_one(self) -> Optional[MutationPair]:
        """Apply a random combination of mutations and return the pair."""
        mutated = copy.deepcopy(self.original_graph)
        nodes = {n["id"]: n for n in mutated["nodes"]}
        edges = mutated["edges"]

        # ground_truth[original_col_id] = mutated_col_id
        ground_truth: dict[str, str] = {
            n["id"]: n["id"] for n in mutated["nodes"] if n["label"] == "Column"
        }
        mutations_applied: list[str] = []

        # Apply mutations probabilistically
        cols = [n for n in mutated["nodes"] if n["label"] == "Column"]

        if self.rng.random() < _MUTATION_PROBS["rename_column"] and cols:
            count = self.rng.randint(1, max(1, len(cols) // 5))
            targets = self.rng.sample(cols, min(count, len(cols)))
            for col in targets:
                old_id = col["id"]
                old_name = col.get("name", old_id.split(".")[-1])
                new_name = self._rename(old_name)
                if new_name == old_name:
                    continue

                table = col.get("table", old_id.split(".")[0])
                new_id = f"{table}.{new_name}"

                # Update the node
                col["id"] = new_id
                col["name"] = new_name

                # Update all edges referencing this column
                for edge in edges:
                    if edge["source"] == old_id:
                        edge["source"] = new_id
                    if edge["target"] == old_id:
                        edge["target"] = new_id

                # Update ground truth — old_id now maps to new_id in mutated
                # Find the original column that this mutation started from
                for orig_id, mut_id in list(ground_truth.items()):
                    if mut_id == old_id:
                        ground_truth[orig_id] = new_id
                        break

                mutations_applied.append(f"RENAME_COLUMN: {old_id} → {new_id}")

        if self.rng.random() < _MUTATION_PROBS["add_column"] and self._tables:
            table_node = self.rng.choice(self._tables)
            table_name = table_node["id"]
            new_col_name = f"added_{self._random_suffix()}"
            new_col_id = f"{table_name}.{new_col_name}"
            dtype = self.rng.choice(["VARCHAR", "INTEGER", "BOOLEAN", "TIMESTAMP"])

            new_node = {
                "id": new_col_id,
                "label": "Column",
                "table": table_name,
                "name": new_col_name,
                "dtype": dtype,
                "dtype_raw": dtype,
                "nullable": True,
                "is_primary_key": False,
                "is_indexed": False,
                "null_rate": 0.0,
                "cardinality": 0.0,
                "sample_values": [],
                "fingerprint": self._random_suffix(),
            }
            mutated["nodes"].append(new_node)
            mutated["edges"].append(
                {
                    "source": new_col_id,
                    "target": table_name,
                    "relation": "PART_OF",
                    "weight": 1.0,
                }
            )
            # No ground truth entry — this column has no original counterpart
            mutations_applied.append(f"ADD_COLUMN: {new_col_id}")

        if self.rng.random() < _MUTATION_PROBS["remove_column"]:
            removable = [
                n
                for n in mutated["nodes"]
                if n["label"] == "Column" and not n.get("is_primary_key")
            ]
            if removable:
                col = self.rng.choice(removable)
                col_id = col["id"]
                mutated["nodes"] = [n for n in mutated["nodes"] if n["id"] != col_id]
                mutated["edges"] = [
                    e for e in mutated["edges"] if e["source"] != col_id and e["target"] != col_id
                ]
                # Remove from ground truth — dropped columns have no mapping
                for orig_id in [k for k, v in ground_truth.items() if v == col_id]:
                    del ground_truth[orig_id]
                mutations_applied.append(f"REMOVE_COLUMN: {col_id}")

        if self.rng.random() < _MUTATION_PROBS["change_nullable"]:
            non_pk_cols = [
                n
                for n in mutated["nodes"]
                if n["label"] == "Column" and not n.get("is_primary_key")
            ]
            if non_pk_cols:
                col = self.rng.choice(non_pk_cols)
                col["nullable"] = not col.get("nullable", True)
                mutations_applied.append(f"CHANGE_NULLABLE: {col['id']}")

        if self.rng.random() < _MUTATION_PROBS["change_dtype"]:
            typed_cols = [
                n
                for n in mutated["nodes"]
                if n["label"] == "Column"
                and n.get("dtype") in _DTYPE_SUBSTITUTIONS
                and not n.get("is_primary_key")
            ]
            if typed_cols:
                col = self.rng.choice(typed_cols)
                subs = _DTYPE_SUBSTITUTIONS.get(col["dtype"], [])
                new_type = self.rng.choice(subs) if subs else col["dtype"]
                col["dtype"] = new_type
                col["dtype_raw"] = new_type
                mutations_applied.append(f"CHANGE_DTYPE: {col['id']} → {new_type}")

        if not mutations_applied:
            return None

        # Update graph metadata
        mutated["meta"] = {
            **self.original_graph.get("meta", {}),
            "node_count": len(mutated["nodes"]),
            "edge_count": len(mutated["edges"]),
            "is_mutated": True,
            "mutations": mutations_applied,
        }

        return MutationPair(
            original_graph=self.original_graph,
            mutated_graph=mutated,
            ground_truth=ground_truth,
            mutations_applied=mutations_applied,
        )

    def _rename(self, name: str) -> str:
        """Apply a realistic rename pattern to a column name."""
        for pattern, replacement in _RENAME_PATTERNS:
            src_re = pattern.format(name="(.+)")
            if re.fullmatch(src_re, name):
                new_name = replacement.format(name=name)
                if new_name != name:
                    return new_name
        # Fallback: append a suffix
        return f"{name}_{self._random_suffix()}"

    def _random_suffix(self, length: int = 4) -> str:
        return "".join(self.rng.choices(string.ascii_lowercase, k=length))

    def save_pairs(self, pairs: list[MutationPair], output_dir: str = "data/training") -> Path:
        """Save all pairs to disk for offline training."""
        path = Path(output_dir)
        path.mkdir(parents=True, exist_ok=True)

        for i, pair in enumerate(pairs):
            (path / f"pair_{i:04d}_original.json").write_text(
                json.dumps(pair.original_graph, indent=2, default=str)
            )
            (path / f"pair_{i:04d}_mutated.json").write_text(
                json.dumps(pair.mutated_graph, indent=2, default=str)
            )
            (path / f"pair_{i:04d}_ground_truth.json").write_text(
                json.dumps(pair.ground_truth, indent=2)
            )

        manifest = [p.to_dict() for p in pairs]
        (path / "manifest.json").write_text(json.dumps(manifest, indent=2))

        log.info(f"Saved {len(pairs)} training pairs to {path}")
        return path
