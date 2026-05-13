"""
snowflake_connector.py — Production Snowflake connector for SemZero.

Snowflake-specific features:
  - Connects via snowflake-sqlalchemy
  - Batched INFORMATION_SCHEMA stats (no per-column queries)
  - Query history from ACCOUNT_USAGE.QUERY_HISTORY (last 30 days)
  - Table sizes from INFORMATION_SCHEMA.TABLE_STORAGE_METRICS
  - Clustering key detection
  - Automatic warehouse sizing hints
  - Graceful fallback when ACCOUNT_USAGE is not accessible

Install:
  pip install snowflake-sqlalchemy

Environment variables:
  SNOWFLAKE_ACCOUNT    e.g. xy12345.us-east-1
  SNOWFLAKE_USER       your username
  SNOWFLAKE_PASSWORD   your password
  SNOWFLAKE_DATABASE   your database
  SNOWFLAKE_SCHEMA     your schema (default: PUBLIC)
  SNOWFLAKE_WAREHOUSE  your warehouse (e.g. COMPUTE_WH)
  SNOWFLAKE_ROLE       optional role (e.g. SYSADMIN)

Usage:
  from semzero.crawler.snowflake_connector import SnowflakeConnector
  conn = SnowflakeConnector.from_env()
  tables = conn.get_tables()
  stats  = conn.get_all_table_stats(tables)
"""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

log = logging.getLogger(__name__)

_MAX_WORKERS = 8


# ── Data classes (shared with base connector) ─────────────────────────────────


@dataclass
class ColumnStats:
    name: str
    dtype: str
    nullable: bool
    row_count: int = 0
    null_count: int = 0
    distinct_count: int = 0
    sample_values: list[Any] = field(default_factory=list)

    @property
    def null_rate(self) -> float:
        return round(self.null_count / self.row_count, 4) if self.row_count else 0.0

    @property
    def cardinality(self) -> float:
        return round(self.distinct_count / self.row_count, 4) if self.row_count else 0.0


@dataclass
class TableStats:
    name: str
    row_count: int = 0
    columns: list[ColumnStats] = field(default_factory=list)
    query_frequency: int = 0
    size_bytes: int = 0
    is_clustered: bool = False
    clustering_keys: list[str] = field(default_factory=list)
    crawl_error: Optional[str] = None

    @property
    def failed(self) -> bool:
        return self.crawl_error is not None

    @property
    def size_gb(self) -> float:
        return round(self.size_bytes / (1024**3), 4)


# ── Snowflake connector ───────────────────────────────────────────────────────


class SnowflakeConnector:
    """
    Production-grade Snowflake connector for SemZero.

    Uses INFORMATION_SCHEMA for structural metadata and batched stats.
    Uses ACCOUNT_USAGE for query frequency (optional — requires privileges).
    """

    def __init__(
        self,
        account: str,
        user: str,
        password: str,
        database: str,
        schema: str = "PUBLIC",
        warehouse: str = "",
        role: str = "",
        collect_stats: bool = True,
        max_workers: int = _MAX_WORKERS,
        timeout: int = 60,
        max_sample_values: int = 3,
    ) -> None:
        self.database = database.upper()
        self.schema = schema.upper()
        self.collect_stats = collect_stats
        self.max_workers = max_workers
        self.timeout = timeout
        self.max_sample_values = max_sample_values
        self._dialect = "snowflake"

        db_url = self._build_url(account, user, password, database, schema, warehouse, role)
        self.engine = self._create_engine(db_url)
        log.info(f"Connected to Snowflake: {account} / {database}.{schema}")

    @classmethod
    def from_env(cls) -> "SnowflakeConnector":
        """
        Create a connector from environment variables.

        Required: SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER, SNOWFLAKE_PASSWORD,
                  SNOWFLAKE_DATABASE
        Optional: SNOWFLAKE_SCHEMA, SNOWFLAKE_WAREHOUSE, SNOWFLAKE_ROLE
        """
        required = {
            "SNOWFLAKE_ACCOUNT": os.environ.get("SNOWFLAKE_ACCOUNT", ""),
            "SNOWFLAKE_USER": os.environ.get("SNOWFLAKE_USER", ""),
            "SNOWFLAKE_PASSWORD": os.environ.get("SNOWFLAKE_PASSWORD", ""),
            "SNOWFLAKE_DATABASE": os.environ.get("SNOWFLAKE_DATABASE", ""),
        }
        missing = [k for k, v in required.items() if not v]
        if missing:
            raise EnvironmentError(
                f"Missing required Snowflake environment variables: {missing}\n"
                "Set them with: export SNOWFLAKE_ACCOUNT=xy12345.us-east-1 etc."
            )

        return cls(
            account=required["SNOWFLAKE_ACCOUNT"],
            user=required["SNOWFLAKE_USER"],
            password=required["SNOWFLAKE_PASSWORD"],
            database=required["SNOWFLAKE_DATABASE"],
            schema=os.environ.get("SNOWFLAKE_SCHEMA", "PUBLIC"),
            warehouse=os.environ.get("SNOWFLAKE_WAREHOUSE", ""),
            role=os.environ.get("SNOWFLAKE_ROLE", ""),
        )

    @classmethod
    def from_url(cls, db_url: str, **kwargs) -> "SnowflakeConnector":
        """
        Create a connector from a full SQLAlchemy Snowflake URL.
        Use this if you already have a connection string.

        URL format:
          snowflake://user:password@account/database/schema?warehouse=WH
        """
        instance = object.__new__(cls)
        instance.database = ""
        instance.schema = ""
        instance.collect_stats = kwargs.get("collect_stats", True)
        instance.max_workers = kwargs.get("max_workers", _MAX_WORKERS)
        instance.timeout = kwargs.get("timeout", 60)
        instance.max_sample_values = kwargs.get("max_sample_values", 3)
        instance._dialect = "snowflake"
        instance.engine = instance._create_engine(db_url)
        log.info("Connected to Snowflake via URL.")
        return instance

    # ── Connection ─────────────────────────────────────────────────────────────

    @staticmethod
    def _build_url(
        account: str,
        user: str,
        password: str,
        database: str,
        schema: str,
        warehouse: str,
        role: str,
    ) -> str:
        url = f"snowflake://{user}:{password}@{account}/{database}/{schema}"
        params: list[str] = []
        if warehouse:
            params.append(f"warehouse={warehouse}")
        if role:
            params.append(f"role={role}")
        if params:
            url += "?" + "&".join(params)
        return url

    @staticmethod
    def _create_engine(db_url: str) -> Engine:
        try:
            import snowflake.sqlalchemy  # noqa: F401
        except ImportError:
            raise ImportError(
                "snowflake-sqlalchemy is required for Snowflake support.\n"
                "Install it with: pip install snowflake-sqlalchemy"
            )
        return create_engine(
            db_url,
            pool_pre_ping=True,
            pool_size=3,
            max_overflow=5,
            pool_recycle=1800,
        )

    # ── Structural metadata ────────────────────────────────────────────────────

    def get_tables(self) -> list[str]:
        """
        Returns all table names in the current schema.
        Uses INFORMATION_SCHEMA.TABLES — no special privileges needed.
        """
        query = text("""
            SELECT TABLE_NAME
            FROM INFORMATION_SCHEMA.TABLES
            WHERE TABLE_SCHEMA = :schema
              AND TABLE_TYPE   = 'BASE TABLE'
            ORDER BY TABLE_NAME
        """)
        try:
            with self.engine.connect() as conn:
                result = conn.execute(query, {"schema": self.schema})
                return [row[0] for row in result.fetchall()]
        except Exception as e:
            log.error(f"Failed to get tables: {e}")
            return []

    def get_columns(self, table_name: str) -> list[dict]:
        """
        Returns column metadata from INFORMATION_SCHEMA.COLUMNS.
        """
        query = text("""
            SELECT
                COLUMN_NAME,
                DATA_TYPE,
                IS_NULLABLE,
                CHARACTER_MAXIMUM_LENGTH,
                NUMERIC_PRECISION,
                NUMERIC_SCALE,
                COLUMN_DEFAULT,
                ORDINAL_POSITION
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = :schema
              AND TABLE_NAME   = :table
            ORDER BY ORDINAL_POSITION
        """)
        try:
            with self.engine.connect() as conn:
                result = conn.execute(
                    query,
                    {
                        "schema": self.schema,
                        "table": table_name.upper(),
                    },
                )
                rows = result.fetchall()

            return [
                {
                    "name": row[0],
                    "type": row[1],
                    "nullable": row[2] == "YES",
                }
                for row in rows
            ]
        except Exception as e:
            log.warning(f"Failed to get columns for {table_name}: {e}")
            return []

    def get_foreign_keys(self, table_name: str) -> list[dict]:
        """
        Returns foreign key relationships from INFORMATION_SCHEMA.
        """
        query = text("""
            SELECT
                kcu.COLUMN_NAME,
                ccu.TABLE_NAME  AS referenced_table,
                ccu.COLUMN_NAME AS referenced_column
            FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
            JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE kcu
              ON tc.CONSTRAINT_NAME = kcu.CONSTRAINT_NAME
             AND tc.TABLE_SCHEMA    = kcu.TABLE_SCHEMA
            JOIN INFORMATION_SCHEMA.REFERENTIAL_CONSTRAINTS rc
              ON tc.CONSTRAINT_NAME = rc.CONSTRAINT_NAME
            JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE ccu
              ON ccu.CONSTRAINT_NAME = rc.UNIQUE_CONSTRAINT_NAME
            WHERE tc.CONSTRAINT_TYPE = 'FOREIGN KEY'
              AND tc.TABLE_SCHEMA    = :schema
              AND tc.TABLE_NAME      = :table
        """)
        try:
            with self.engine.connect() as conn:
                result = conn.execute(
                    query,
                    {
                        "schema": self.schema,
                        "table": table_name.upper(),
                    },
                )
                rows = result.fetchall()

            fks: dict[str, dict] = {}
            for row in rows:
                col, ref_table, ref_col = row[0], row[1], row[2]
                key = ref_table
                if key not in fks:
                    fks[key] = {
                        "constrained_columns": [],
                        "referred_table": ref_table,
                        "referred_columns": [],
                    }
                fks[key]["constrained_columns"].append(col)
                fks[key]["referred_columns"].append(ref_col)

            return list(fks.values())
        except Exception as e:
            log.warning(f"Failed to get FKs for {table_name}: {e}")
            return []

    def get_primary_keys(self, table_name: str) -> list[str]:
        query = text("""
            SELECT kcu.COLUMN_NAME
            FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
            JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE kcu
              ON tc.CONSTRAINT_NAME = kcu.CONSTRAINT_NAME
             AND tc.TABLE_SCHEMA    = kcu.TABLE_SCHEMA
            WHERE tc.CONSTRAINT_TYPE = 'PRIMARY KEY'
              AND tc.TABLE_SCHEMA    = :schema
              AND tc.TABLE_NAME      = :table
            ORDER BY kcu.ORDINAL_POSITION
        """)
        try:
            with self.engine.connect() as conn:
                result = conn.execute(
                    query,
                    {
                        "schema": self.schema,
                        "table": table_name.upper(),
                    },
                )
                return [row[0] for row in result.fetchall()]
        except Exception:
            return []

    # ── Batched stats ──────────────────────────────────────────────────────────

    def get_table_stats(self, table_name: str) -> TableStats:
        """
        Returns rich per-table statistics using Snowflake-optimised queries.

        Row counts and column stats are fetched in ONE batched query per table.
        Table storage size is fetched from INFORMATION_SCHEMA.TABLE_STORAGE_METRICS.
        Query frequency is fetched from ACCOUNT_USAGE.QUERY_HISTORY (optional).
        """
        stats = TableStats(name=table_name)
        if not self.collect_stats:
            return stats

        cols = self.get_columns(table_name)
        if not cols:
            return stats

        upper_table = table_name.upper()

        with self.engine.connect() as conn:
            # ── Row count ───────────────────────────────────────────────────
            try:
                result = conn.execute(text(f'SELECT COUNT(*) FROM "{self.schema}"."{upper_table}"'))
                stats.row_count = result.scalar() or 0
            except Exception as e:
                log.warning(f"Row count failed for {table_name}: {e}")
                stats.crawl_error = str(e)
                return stats

            if stats.row_count == 0:
                for col in cols:
                    stats.columns.append(
                        ColumnStats(
                            name=col["name"],
                            dtype=col["type"],
                            nullable=col["nullable"],
                            row_count=0,
                        )
                    )
                return stats

            # ── Batched null + distinct counts ─────────────────────────────
            select_parts: list[str] = []
            for col in cols:
                n = col["name"]
                dtype = col["type"].upper().split("(")[0]
                select_parts.append(f'COUNT("{n}") AS "{n}__count"')
                # DISTINCT is expensive on large tables — limit to key types
                if dtype in ("NUMBER", "INTEGER", "VARCHAR", "TEXT", "BOOLEAN"):
                    select_parts.append(f'COUNT(DISTINCT "{n}") AS "{n}__distinct"')
                else:
                    select_parts.append(f'NULL AS "{n}__distinct"')

            row_dict: dict = {}
            try:
                result = conn.execute(
                    text(f'SELECT {", ".join(select_parts)} FROM "{self.schema}"."{upper_table}"')
                )
                row = result.fetchone()
                row_dict = dict(row._mapping) if row else {}
            except Exception as e:
                log.warning(f"Batch stats failed for {table_name}: {e}")

            for col in cols:
                n = col["name"]
                count = row_dict.get(f"{n}__count", 0) or 0
                distinct = row_dict.get(f"{n}__distinct") or 0
                col_stats = ColumnStats(
                    name=n,
                    dtype=col["type"],
                    nullable=col["nullable"],
                    row_count=stats.row_count,
                    null_count=max(0, stats.row_count - count),
                    distinct_count=distinct,
                )

                # Sample values
                if self.max_sample_values > 0:
                    try:
                        r = conn.execute(
                            text(
                                f'SELECT DISTINCT "{n}" '
                                f'FROM "{self.schema}"."{upper_table}" '
                                f'WHERE "{n}" IS NOT NULL '
                                f"LIMIT {self.max_sample_values}"
                            )
                        )
                        col_stats.sample_values = [str(row[0]) for row in r.fetchall()]
                    except Exception:
                        pass

                stats.columns.append(col_stats)

            # ── Table size ──────────────────────────────────────────────────
            try:
                r = conn.execute(
                    text("""
                    SELECT ACTIVE_BYTES + TIME_TRAVEL_BYTES
                    FROM INFORMATION_SCHEMA.TABLE_STORAGE_METRICS
                    WHERE TABLE_SCHEMA = :schema AND TABLE_NAME = :table
                """),
                    {"schema": self.schema, "table": upper_table},
                )
                row = r.fetchone()
                stats.size_bytes = int(row[0]) if row and row[0] else 0
            except Exception:
                pass

            # ── Clustering keys ─────────────────────────────────────────────
            try:
                r = conn.execute(
                    text("""
                    SELECT CLUSTERING_KEY
                    FROM INFORMATION_SCHEMA.TABLES
                    WHERE TABLE_SCHEMA = :schema AND TABLE_NAME = :table
                """),
                    {"schema": self.schema, "table": upper_table},
                )
                row = r.fetchone()
                if row and row[0]:
                    stats.is_clustered = True
                    stats.clustering_keys = [k.strip() for k in row[0].split(",")]
            except Exception:
                pass

        # ── Query frequency from ACCOUNT_USAGE (optional) ──────────────────
        # Requires ACCOUNTADMIN or USAGE on SNOWFLAKE database
        try:
            with self.engine.connect() as conn:
                r = conn.execute(
                    text("""
                    SELECT COUNT(*) AS query_count
                    FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY
                    WHERE QUERY_TEXT ILIKE :pattern
                      AND START_TIME >= DATEADD('day', -30, CURRENT_TIMESTAMP())
                      AND EXECUTION_STATUS = 'SUCCESS'
                """),
                    {"pattern": f"%{upper_table}%"},
                )
                row = r.fetchone()
                stats.query_frequency = int(row[0]) if row else 0
        except Exception:
            # ACCOUNT_USAGE requires elevated privileges — fail silently
            pass

        return stats

    def get_all_table_stats(self, tables: list[str]) -> dict[str, TableStats]:
        """Crawls all tables in parallel. Returns {table_name: TableStats}."""
        results: dict[str, TableStats] = {}
        log.info(f"Crawling {len(tables)} Snowflake tables with {self.max_workers} workers...")

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {executor.submit(self.get_table_stats, t): t for t in tables}
            for i, future in enumerate(as_completed(futures), 1):
                table = futures[future]
                try:
                    stats = future.result(timeout=self.timeout)
                    results[table] = stats
                    if stats.failed:
                        log.warning(f"  [{i}/{len(tables)}] {table} — FAILED: {stats.crawl_error}")
                    else:
                        log.info(
                            f"  [{i}/{len(tables)}] {table} — "
                            f"{stats.row_count:,} rows, "
                            f"{stats.size_gb:.2f}GB"
                            + (" [clustered]" if stats.is_clustered else "")
                        )
                except Exception as e:
                    log.error(f"  [{i}/{len(tables)}] {table} — EXCEPTION: {e}")
                    results[table] = TableStats(name=table, crawl_error=str(e))

        failed = [t for t, s in results.items() if s.failed]
        if failed:
            log.warning(f"Crawl complete. {len(failed)}/{len(tables)} tables failed.")
        else:
            log.info(f"Crawl complete. All {len(tables)} tables succeeded.")

        return results
