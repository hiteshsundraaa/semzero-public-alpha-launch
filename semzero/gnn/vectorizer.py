"""
vectorizer.py — Node feature vectorizer for the GNN.

Fixes over v1:
  - Expanded from 4 types to 25+ covering all common SQL dialects
  - Added _normalise_type() — strips VARCHAR(255) → VARCHAR before lookup
  - warn_unknown flag: warn or raise UnsupportedColumnTypeError
  - Full type annotations
"""

from __future__ import annotations

import logging

import torch

from ..utils.errors import UnsupportedColumnTypeError

log = logging.getLogger(__name__)

TYPE_MAP: dict[str, int] = {
    # Integer family
    "INTEGER": 0,
    "INT": 0,
    "SMALLINT": 0,
    "BIGINT": 0,
    "TINYINT": 0,
    # Float / numeric family
    "FLOAT": 1,
    "DOUBLE": 1,
    "REAL": 1,
    "NUMERIC": 1,
    "DECIMAL": 1,
    # String family
    "VARCHAR": 2,
    "TEXT": 2,
    "CHAR": 2,
    "STRING": 2,
    "NVARCHAR": 2,
    # Temporal family
    "TIMESTAMP": 3,
    "DATE": 3,
    "DATETIME": 3,
    "TIME": 3,
    "TIMESTAMPTZ": 3,
    # Boolean
    "BOOLEAN": 4,
    "BOOL": 4,
    # Semi-structured / binary
    "JSONB": 5,
    "JSON": 5,
    "ARRAY": 5,
    "BYTEA": 5,
    "BLOB": 5,
    # Fallback
    "UNKNOWN": 6,
}
NUM_TYPES = 7  # must stay in sync with TYPE_MAP values


def _normalise_type(raw_type: str) -> str:
    """
    Strip length/precision qualifiers and uppercase so that:
    'VARCHAR(255)' → 'VARCHAR', 'NUMERIC(10,2)' → 'NUMERIC'
    """
    return raw_type.split("(")[0].strip().upper()


class SchemaVectorizer:
    """
    Converts a graph node dict into a fixed-length PyTorch feature tensor.

    Feature layout (length = NUM_TYPES + 2 = 9):
      [0…6]  one-hot SQL type encoding  (7 values)
      [7]    1 if Table, else 0
      [8]    1 if Column, else 0
    """

    def __init__(self, warn_unknown: bool = True) -> None:
        """
        Args:
            warn_unknown: If True, print a warning on unknown types.
                          If False, raise UnsupportedColumnTypeError.
        """
        self.warn_unknown = warn_unknown

    def vectorize_node(self, node: dict) -> torch.Tensor:
        if not isinstance(node, dict):
            raise TypeError(f"Expected dict, got {type(node)}")

        raw_type = node.get("dtype", "UNKNOWN")
        canonical = _normalise_type(raw_type)
        type_idx = TYPE_MAP.get(canonical)

        if type_idx is None:
            if self.warn_unknown:
                log.warning(f"[SchemaVectorizer] Unknown type '{raw_type}' — mapped to UNKNOWN.")
            else:
                raise UnsupportedColumnTypeError(
                    f"Column type '{raw_type}' is not in the type map."
                )
            type_idx = TYPE_MAP["UNKNOWN"]

        type_vec = [0] * NUM_TYPES
        type_vec[type_idx] = 1

        label = node.get("label", "")
        role_vec = [1, 0] if label == "Table" else [0, 1]

        return torch.tensor(type_vec + role_vec, dtype=torch.float)

    def vectorize_batch(self, nodes: list[dict]) -> torch.Tensor:
        """Vectorize a list of nodes into a 2D tensor [N, NUM_TYPES+2]."""
        if not nodes:
            raise ValueError("nodes list must not be empty.")
        return torch.stack([self.vectorize_node(n) for n in nodes])
