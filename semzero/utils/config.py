"""
config.py — Centralized configuration management.

All settings are read from environment variables with sensible defaults.
Never hardcode credentials — use a .env file locally or secrets manager in prod.

Usage:
    from semzero.utils.config import cfg
    print(cfg.github_token)
    print(cfg.snowflake_account)
"""

from __future__ import annotations

import os
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, default))
    except (ValueError, TypeError):
        return default


def _env_bool(key: str, default: bool = False) -> bool:
    val = os.environ.get(key, "").lower()
    if val in ("1", "true", "yes"):
        return True
    if val in ("0", "false", "no"):
        return False
    return default


@dataclass
class SemZeroConfig:
    # ── Database connections ────────────────────────────────────────────
    db_url: str = field(default_factory=lambda: _env("SEMZERO_DB_URL"))
    snowflake_account: str = field(default_factory=lambda: _env("SNOWFLAKE_ACCOUNT"))
    snowflake_user: str = field(default_factory=lambda: _env("SNOWFLAKE_USER"))
    snowflake_password: str = field(default_factory=lambda: _env("SNOWFLAKE_PASSWORD"))
    snowflake_database: str = field(default_factory=lambda: _env("SNOWFLAKE_DATABASE"))
    snowflake_schema: str = field(default_factory=lambda: _env("SNOWFLAKE_SCHEMA", "PUBLIC"))
    snowflake_warehouse: str = field(default_factory=lambda: _env("SNOWFLAKE_WAREHOUSE"))

    # ── GitHub integration ──────────────────────────────────────────────
    github_token: str = field(default_factory=lambda: _env("SEMZERO_GITHUB_TOKEN"))
    github_repo: str = field(default_factory=lambda: _env("SEMZERO_GITHUB_REPO"))
    github_base_branch: str = field(default_factory=lambda: _env("SEMZERO_GITHUB_BASE", "main"))
    github_reviewers: str = field(default_factory=lambda: _env("SEMZERO_REVIEWERS", ""))

    # ── Slack integration ───────────────────────────────────────────────
    slack_webhook_url: str = field(default_factory=lambda: _env("SEMZERO_SLACK_WEBHOOK"))
    slack_channel: str = field(
        default_factory=lambda: _env("SEMZERO_SLACK_CHANNEL", "#data-alerts")
    )

    # ── Crawler settings ────────────────────────────────────────────────
    crawl_batch_size: int = field(default_factory=lambda: _env_int("SEMZERO_BATCH_SIZE", 50))
    crawl_timeout: int = field(default_factory=lambda: _env_int("SEMZERO_TIMEOUT", 30))
    collect_stats: bool = field(default_factory=lambda: _env_bool("SEMZERO_STATS", True))
    max_sample_values: int = field(default_factory=lambda: _env_int("SEMZERO_SAMPLES", 3))

    # ── Watcher / scheduler ─────────────────────────────────────────────
    watch_interval: int = field(default_factory=lambda: _env_int("SEMZERO_INTERVAL", 3600))

    # ── Repair / PR settings ────────────────────────────────────────────
    auto_map_threshold: float = 0.80
    pr_confidence_min: float = 0.80
    pr_batch_window: int = field(default_factory=lambda: _env_int("SEMZERO_PR_BATCH_WINDOW", 30))

    # ── Storage ─────────────────────────────────────────────────────────
    data_dir: Path = field(default_factory=lambda: Path(_env("SEMZERO_DATA_DIR", "data")))
    graph_store_path: Path = field(
        default_factory=lambda: Path(_env("SEMZERO_GRAPH_STORE", "data/graph_store.db"))
    )

    # ── Logging ─────────────────────────────────────────────────────────
    log_level: str = field(default_factory=lambda: _env("SEMZERO_LOG_LEVEL", "INFO"))

    def __post_init__(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.graph_store_path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def github_reviewer_list(self) -> list[str]:
        if not self.github_reviewers:
            return []
        return [r.strip() for r in self.github_reviewers.split(",") if r.strip()]

    @property
    def snowflake_url(self) -> str:
        if not self.snowflake_account:
            return ""
        return (
            f"snowflake://{self.snowflake_user}:{self.snowflake_password}"
            f"@{self.snowflake_account}/{self.snowflake_database}"
            f"?warehouse={self.snowflake_warehouse}&schema={self.snowflake_schema}"
        )

    def validate(self) -> list[str]:
        """Returns a list of missing required config items."""
        issues = []
        if not self.db_url and not self.snowflake_account:
            issues.append("No database URL configured (SEMZERO_DB_URL or SNOWFLAKE_* vars)")
        if not self.github_token:
            issues.append("SEMZERO_GITHUB_TOKEN not set — PR bot will not work")
        if not self.github_repo:
            issues.append("SEMZERO_GITHUB_REPO not set — PR bot will not work")
        return issues

    def setup_logging(self) -> None:
        logging.basicConfig(
            level=getattr(logging, self.log_level.upper(), logging.INFO),
            format="%(asctime)s  %(name)-30s  %(levelname)-8s  %(message)s",
            datefmt="%H:%M:%S",
        )


# Singleton
cfg = SemZeroConfig()
