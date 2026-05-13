from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import create_engine, inspect, text


def detect_dialect(db_url: str, explicit: str = "auto") -> str:
    if explicit and explicit != "auto":
        return explicit.lower()
    url = (db_url or "").lower()
    if url.startswith("snowflake"):
        return "snowflake"
    if url.startswith("postgresql") or url.startswith("postgres"):
        return "postgresql"
    if url.startswith("sqlite"):
        return "sqlite"
    if url.startswith("mysql"):
        return "mysql"
    if url.startswith("databricks") or url.startswith("databricks+connector"):
        return "databricks"
    if url.startswith("bigquery"):
        return "bigquery"
    if url.startswith("duckdb"):
        return "duckdb"
    if url.startswith("redshift"):
        return "redshift"
    return "unknown"


_CAPABILITY_RULES: dict[str, dict[str, Any]] = {
    "sqlite": {
        "clone_supported": True,
        "zero_copy_clone": False,
        "native_query_history": False,
        "query_history_source": "file_or_sql_assets",
        "recommended_live_mode": "clone",
        "notes": [
            "SQLite is best for local validation and smoke testing.",
            "Live warehouse history is not available natively; provide SQL files or exports.",
        ],
    },
    "postgresql": {
        "clone_supported": True,
        "zero_copy_clone": False,
        "native_query_history": True,
        "query_history_source": "pg_stat_statements",
        "recommended_live_mode": "clone",
        "notes": [
            "Template-database cloning is supported when the account can CREATE DATABASE.",
            "Install pg_stat_statements to improve workload-aware Wind Tunnel replay.",
        ],
    },
    "snowflake": {
        "clone_supported": True,
        "zero_copy_clone": True,
        "native_query_history": True,
        "query_history_source": "ACCOUNT_USAGE.QUERY_HISTORY",
        "recommended_live_mode": "clone",
        "notes": [
            "Snowflake zero-copy clones make Wind Tunnel and Chaos suitable for large warehouses.",
            "Grant read access to ACCOUNT_USAGE and clone privileges for production-grade replay.",
        ],
    },
    "databricks": {
        "clone_supported": True,
        "zero_copy_clone": True,
        "native_query_history": True,
        "query_history_source": "system.query.history",
        "recommended_live_mode": "clone",
        "notes": [
            "Databricks SHALLOW CLONE can isolate Delta tables cheaply for Wind Tunnel and Chaos.",
            "Grant access to system.query.history and Unity Catalog clone targets for production-grade replay.",
        ],
    },
    "bigquery": {
        "clone_supported": False,
        "zero_copy_clone": False,
        "native_query_history": True,
        "query_history_source": "INFORMATION_SCHEMA.JOBS_BY_PROJECT",
        "recommended_live_mode": "metadata-only",
        "notes": [
            "Prefer Change Gate plus metadata-backed Wind Tunnel selection before deeper execution.",
            "Use sampled datasets or temporary tables for mutation execution outside production.",
        ],
    },
    "mysql": {
        "clone_supported": False,
        "zero_copy_clone": False,
        "native_query_history": False,
        "query_history_source": "file_or_sql_assets",
        "recommended_live_mode": "metadata-only",
        "notes": [
            "Use Change Gate and crawl/report features first, then add cloned staging workflows.",
        ],
    },
    "duckdb": {
        "clone_supported": True,
        "zero_copy_clone": False,
        "native_query_history": False,
        "query_history_source": "file_or_sql_assets",
        "recommended_live_mode": "clone",
        "notes": [
            "DuckDB is a strong fit for sampled Wind Tunnel and local PR validation.",
        ],
    },
    "redshift": {
        "clone_supported": False,
        "zero_copy_clone": False,
        "native_query_history": True,
        "query_history_source": "STL_QUERY",
        "recommended_live_mode": "metadata-only",
        "notes": [
            "Start with Change Gate and workload-aware selection before broader replay.",
        ],
    },
    "unknown": {
        "clone_supported": False,
        "zero_copy_clone": False,
        "native_query_history": False,
        "query_history_source": "unknown",
        "recommended_live_mode": "metadata-only",
        "notes": [
            "SemZero can still run static proofing and graph-based analysis in metadata-only mode.",
        ],
    },
}


@dataclass
class LiveReadinessReport:
    db_url: str
    dialect: str
    connectivity_ok: bool
    clone_supported: bool
    zero_copy_clone: bool
    native_query_history: bool
    query_history_source: str
    recommended_live_mode: str
    table_count: int = 0
    table_preview: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    recommended_commands: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "db_url": self.db_url,
            "dialect": self.dialect,
            "connectivity_ok": self.connectivity_ok,
            "clone_supported": self.clone_supported,
            "zero_copy_clone": self.zero_copy_clone,
            "native_query_history": self.native_query_history,
            "query_history_source": self.query_history_source,
            "recommended_live_mode": self.recommended_live_mode,
            "table_count": self.table_count,
            "table_preview": self.table_preview,
            "warnings": self.warnings,
            "notes": self.notes,
            "recommended_commands": self.recommended_commands,
        }

    def save(self, path: str) -> Path:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        return out

    def render_markdown(self) -> str:
        mode = self.recommended_live_mode
        lines = [
            f"# SemZero Live Readiness — {self.dialect}",
            "",
            f"- **Connectivity:** {'OK' if self.connectivity_ok else 'FAILED'}",
            f"- **Clone support:** {'Yes' if self.clone_supported else 'No'}",
            f"- **Zero-copy clone:** {'Yes' if self.zero_copy_clone else 'No'}",
            f"- **Native query history:** {'Yes' if self.native_query_history else 'No'}",
            f"- **Recommended live mode:** `{mode}`",
            f"- **Visible tables:** {self.table_count}",
            "",
        ]
        if self.table_preview:
            lines += [
                "## Table preview",
                "",
                ", ".join(f"`{table}`" for table in self.table_preview[:10]),
                "",
            ]
        if self.warnings:
            lines += ["## Warnings", ""] + [f"- {item}" for item in self.warnings] + [""]
        if self.notes:
            lines += ["## Notes", ""] + [f"- {item}" for item in self.notes] + [""]
        if self.recommended_commands:
            lines += ["## Recommended commands", ""]
            for cmd in self.recommended_commands:
                lines += ["```bash", cmd, "```", ""]
        return "\n".join(lines).strip() + "\n"


def resolve_live_mode(
    requested_mode: str, dialect: str, clone_supported: bool
) -> tuple[bool, list[str]]:
    requested = (requested_mode or "safe").lower()
    warnings: list[str] = []
    if requested == "metadata-only":
        return True, warnings
    if requested == "clone":
        if not clone_supported:
            warnings.append(
                f"Dialect '{dialect}' has no managed clone support in this build; falling back to metadata-only."
            )
            return True, warnings
        return False, warnings
    # safe
    if clone_supported:
        return False, warnings
    warnings.append(
        f"Safe mode fell back to metadata-only because '{dialect}' clone execution is not supported here."
    )
    return True, warnings


def build_live_readiness_report(
    db_url: str,
    dialect: str = "auto",
    output_dir: str = "data",
) -> LiveReadinessReport:
    detected = detect_dialect(db_url, dialect)
    rule = dict(_CAPABILITY_RULES.get(detected, _CAPABILITY_RULES["unknown"]))
    connectivity_ok = False
    table_count = 0
    table_preview: list[str] = []
    warnings: list[str] = []

    if db_url:
        try:
            engine = create_engine(db_url, pool_pre_ping=True)
            try:
                with engine.connect() as conn:
                    conn.execute(text("SELECT 1"))
                connectivity_ok = True
                inspector = inspect(engine)
                table_names = inspector.get_table_names()
                table_count = len(table_names)
                table_preview = table_names[:10]
            finally:
                engine.dispose()
        except Exception as exc:  # pragma: no cover - runtime dependent
            warnings.append(f"Connectivity check failed: {exc}")

    dry_run, mode_warnings = resolve_live_mode(
        rule.get("recommended_live_mode", "safe"),
        detected,
        bool(rule.get("clone_supported")),
    )
    warnings.extend(mode_warnings)

    recommended_commands = [
        f"semzero doctor --db-url '{db_url}'",
        f"semzero scan --db-url '{db_url}' --label live-baseline",
        "semzero gate --drift data/drift_report.json --graph data/schema_graph.json --comment-out data/merge_comment.md",
        f"semzero wind-tunnel --db-url '{db_url}' --migration migration.sql --live-mode {'metadata-only' if dry_run else 'clone'}",
        f"semzero chaos --db-url '{db_url}' --live-mode {'metadata-only' if dry_run else 'clone'} --mutations 25",
        f"semzero premerge --graph data/schema_graph.json --drift data/drift_report.json --db-url '{db_url}'",
    ]

    if not connectivity_ok and not bool(rule.get("clone_supported")):
        recommended_commands = recommended_commands[:1]
        warnings.append(
            "SemZero can still run AST-first proofing and report generation without a live connection."
        )

    return LiveReadinessReport(
        db_url=db_url,
        dialect=detected,
        connectivity_ok=connectivity_ok,
        clone_supported=bool(rule.get("clone_supported")),
        zero_copy_clone=bool(rule.get("zero_copy_clone")),
        native_query_history=bool(rule.get("native_query_history")),
        query_history_source=str(rule.get("query_history_source", "unknown")),
        recommended_live_mode=str(rule.get("recommended_live_mode", "metadata-only")),
        table_count=table_count,
        table_preview=table_preview,
        warnings=warnings,
        notes=list(rule.get("notes", [])),
        recommended_commands=recommended_commands,
    )
