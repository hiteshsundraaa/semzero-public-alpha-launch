"""
connector_factory.py — Auto-detects dialect and returns the right connector.

Usage:
  from semzero.crawler.connector_factory import get_connector
  connector = get_connector("postgresql://...")  # returns DatabaseConnector
  connector = get_connector("snowflake://...")   # returns SnowflakeConnector
  connector = get_connector(dialect="snowflake") # reads from env vars

Snowflake env vars:
  SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER, SNOWFLAKE_PASSWORD,
  SNOWFLAKE_DATABASE, SNOWFLAKE_SCHEMA, SNOWFLAKE_WAREHOUSE
"""

from __future__ import annotations

import logging
from typing import Union

log = logging.getLogger(__name__)


def get_connector(
    db_url: str = "",
    dialect: str = "",
    **kwargs,
) -> Union["DatabaseConnector", "SnowflakeConnector"]:  # noqa: F821
    """
    Returns the appropriate connector based on the db_url or dialect.

    Args:
        db_url:  SQLAlchemy connection URL. Auto-detects dialect from prefix.
        dialect: Explicit dialect override ('snowflake', 'postgresql', etc.)
        **kwargs: Passed to the connector constructor.

    Returns:
        DatabaseConnector for Postgres/SQLite/MySQL/BigQuery
        SnowflakeConnector for Snowflake
    """
    detected = dialect.lower() if dialect else _detect_dialect(db_url)

    if detected == "snowflake":
        from .snowflake_connector import SnowflakeConnector

        if db_url and db_url.startswith("snowflake://"):
            log.info("Using SnowflakeConnector (from URL)")
            return SnowflakeConnector.from_url(db_url, **kwargs)
        else:
            log.info("Using SnowflakeConnector (from env vars)")
            return SnowflakeConnector.from_env(**kwargs)
    else:
        from .connectors import DatabaseConnector

        if not db_url:
            raise ValueError(
                "db_url is required for non-Snowflake dialects. "
                "Set SEMZERO_DB_URL or pass --db-url."
            )
        log.info(f"Using DatabaseConnector for dialect: {detected}")
        return DatabaseConnector(db_url, **kwargs)


def _detect_dialect(db_url: str) -> str:
    """Infers dialect from the URL prefix."""
    if not db_url:
        return "unknown"
    url = db_url.lower()
    if url.startswith("snowflake"):
        return "snowflake"
    if url.startswith("postgresql"):
        return "postgresql"
    if url.startswith("postgres"):
        return "postgresql"
    if url.startswith("sqlite"):
        return "sqlite"
    if url.startswith("mysql"):
        return "mysql"
    if url.startswith("bigquery"):
        return "bigquery"
    return "unknown"
