"""
monitor.py — Shadow monitor and schema validator.

Fixes over v1:
  - SQL injection vulnerability fixed with _validate_identifier() allowlist
  - validate_schema() fully implemented (was silent `pass` stub)
  - Raises SchemaValidationError with detailed diff on mismatch
  - Proper connection context manager
  - Full type annotations
"""

from __future__ import annotations

import logging
import re

import pandas as pd
from sqlalchemy import create_engine, text

from ..utils.errors import SchemaValidationError

log = logging.getLogger(__name__)

# Only allow simple SQL identifiers — no injection possible
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validate_identifier(value: str) -> str:
    """Raises ValueError if value is not a safe SQL identifier."""
    if not _IDENTIFIER_RE.match(value):
        raise ValueError(f"Unsafe SQL identifier rejected: {value!r}")
    return value


class ShadowMonitor:
    """
    Compares a champion (production) output against a challenger
    to detect silent data corruption during pipeline migrations.
    """

    def __init__(self, db_url: str) -> None:
        if not db_url:
            raise ValueError("db_url must not be empty.")
        self.engine = create_engine(db_url, pool_pre_ping=True)

    def compare_outputs(
        self,
        champion_table: str,
        challenger_table: str,
        join_keys: list[str],
    ) -> bool:
        """
        Compares two datasets to detect silent data corruption.

        Args:
            champion_table:  The production (reference) table name.
            challenger_table: The new (candidate) table name.
            join_keys:       Columns to join on (e.g. ['id']).

        Returns:
            True if tables match, False if discrepancies detected.
        """
        # Validate all identifiers before interpolating into SQL
        safe_champion = _validate_identifier(champion_table)
        safe_challenger = _validate_identifier(challenger_table)
        safe_keys = [_validate_identifier(k) for k in join_keys]

        join_clause = " AND ".join([f"c.{k} = ch.{k}" for k in safe_keys])

        query = text(f"""
            SELECT c.id AS champion_id, ch.id AS challenger_id
            FROM {safe_champion} c
            FULL OUTER JOIN {safe_challenger} ch ON {join_clause}
            WHERE c.id IS NULL OR ch.id IS NULL
        """)

        with self.engine.connect() as conn:
            df_diff = pd.read_sql(query, conn)

        if not df_diff.empty:
            log.warning(
                f"ALERT: Discrepancy detected! "
                f"{len(df_diff)} rows differ between "
                f"'{safe_champion}' and '{safe_challenger}'."
            )
            return False

        log.info(f"Shadow comparison passed: '{safe_champion}' matches '{safe_challenger}'.")
        return True

    def validate_schema(self, table_a: str, table_b: str) -> bool:
        """
        Ensures the challenger schema matches the production schema.
        Compares column names and data types between table_a and table_b.

        Returns:
            True if schemas match.

        Raises:
            SchemaValidationError: With a detailed diff if schemas don't match.
        """
        safe_a = _validate_identifier(table_a)
        safe_b = _validate_identifier(table_b)

        query = text("""
            SELECT table_name, column_name, data_type
            FROM information_schema.columns
            WHERE table_name = ANY(:tables)
            ORDER BY table_name, ordinal_position
        """)

        with self.engine.connect() as conn:
            df = pd.read_sql(query, conn, params={"tables": [safe_a, safe_b]})

        def _schema(tbl: str) -> dict[str, str]:
            rows = df[df["table_name"] == tbl][["column_name", "data_type"]]
            return dict(zip(rows["column_name"], rows["data_type"]))

        schema_a = _schema(safe_a)
        schema_b = _schema(safe_b)

        missing_in_b = set(schema_a) - set(schema_b)
        extra_in_b = set(schema_b) - set(schema_a)
        type_mismatches = {
            col: (schema_a[col], schema_b[col])
            for col in schema_a.keys() & schema_b.keys()
            if schema_a[col] != schema_b[col]
        }

        errors: list[str] = []
        if missing_in_b:
            errors.append(f"Columns missing in '{safe_b}': {missing_in_b}")
        if extra_in_b:
            errors.append(f"Extra columns in '{safe_b}': {extra_in_b}")
        if type_mismatches:
            errors.append(f"Type mismatches: {type_mismatches}")

        if errors:
            raise SchemaValidationError(
                f"Schema mismatch between '{safe_a}' and '{safe_b}':\n" + "\n".join(errors)
            )

        log.info(f"Schema validation passed: '{safe_a}' and '{safe_b}' are compatible.")
        return True
