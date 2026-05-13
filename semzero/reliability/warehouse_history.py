from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


@dataclass(slots=True)
class WarehouseHistoryProfile:
    engine: str = "unknown"
    model_name: str = ""
    relation_name: str = ""
    sample_count: int = 0
    avg_runtime_seconds: float | None = None
    p95_runtime_seconds: float | None = None
    avg_cost_usd: float | None = None
    avg_bytes_scanned: float | None = None
    avg_credits_used: float | None = None
    avg_dbu: float | None = None
    confidence: str = "low"
    source: str = "offline_history"
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "semzero_offline_warehouse_history_profile_v1",
            "engine": self.engine,
            "model_name": self.model_name,
            "relation_name": self.relation_name,
            "sample_count": self.sample_count,
            "avg_runtime_seconds": self.avg_runtime_seconds,
            "p95_runtime_seconds": self.p95_runtime_seconds,
            "avg_cost_usd": self.avg_cost_usd,
            "avg_bytes_scanned": self.avg_bytes_scanned,
            "avg_credits_used": self.avg_credits_used,
            "avg_dbu": self.avg_dbu,
            "confidence": self.confidence,
            "source": self.source,
            "notes": self.notes,
        }


def load_warehouse_history(path: str | Path | None) -> dict[str, dict[str, Any]]:
    """Load offline Snowflake/Databricks/dbt history exports.

    Supported shapes:
    - JSON list of rows
    - JSON object with rows/results/history keys
    - CSV with headers

    This is intentionally credential-free. It lets teams export query/job history
    and use it to calibrate cost estimates without giving SemZero live warehouse
    access.
    """
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        return {}
    rows = _load_rows(p)
    profiles: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        normalized = _normalize_row(row)
        keys = _row_keys(normalized)
        if not keys:
            continue
        for key in keys:
            profiles.setdefault(key.lower(), []).append(normalized)
    return {key: _aggregate_rows(key, values) for key, values in profiles.items()}


def profile_for_resource(
    history: dict[str, dict[str, Any]],
    *,
    unique_id: str = "",
    name: str = "",
    relation_name: str = "",
    path: str = "",
) -> dict[str, Any]:
    if not history:
        return {}
    candidates = [unique_id, name, relation_name, path]
    # Also try terminal relation/table tokens.
    for value in list(candidates):
        if value and "." in value:
            candidates.append(value.split(".")[-1])
    for candidate in candidates:
        key = str(candidate or "").strip().lower()
        if key and key in history:
            return dict(history[key])
    # Fuzzy relation/name containment fallback, but avoid returning broad matches when names are tiny.
    for candidate in candidates:
        key = str(candidate or "").strip().lower()
        if len(key) < 5:
            continue
        for hist_key, profile in history.items():
            if key in hist_key or hist_key in key:
                return dict(profile)
    return {}


def _load_rows(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    if path.suffix.lower() == ".csv":
        return list(csv.DictReader(text.splitlines()))
    try:
        payload = json.loads(text)
    except Exception:
        return []
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("rows", "results", "history", "queries", "jobs", "runs"):
            value = payload.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
        # Treat object of model -> metrics as rows.
        rows = []
        for key, value in payload.items():
            if isinstance(value, dict):
                row = dict(value)
                row.setdefault("model_name", key)
                rows.append(row)
        return rows
    return []


def _normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    lower = {str(k).lower(): v for k, v in row.items()}
    engine = str(_first(lower, "engine", "warehouse", "platform", default="") or "").lower()
    query_text = str(_first(lower, "query_text", "query", "sql", "statement", default="") or "")
    if not engine:
        if any(
            k in lower
            for k in (
                "credits_used_cloud_services",
                "credits_used",
                "bytes_scanned",
                "partitions_scanned",
            )
        ):
            engine = "snowflake"
        elif any(
            k in lower for k in ("dbu", "dbus", "dbu_hours", "run_duration", "job_id", "cluster_id")
        ):
            engine = "databricks"
        else:
            engine = "dbt"
    return {
        "engine": engine or "unknown",
        "model_name": str(
            _first(lower, "model_name", "node_name", "name", "dbt_model", "task_key", default="")
            or ""
        ),
        "unique_id": str(_first(lower, "unique_id", "node_id", default="") or ""),
        "relation_name": str(
            _first(lower, "relation_name", "table_name", "schema_table", "target_table", default="")
            or ""
        ),
        "query_text": query_text,
        "runtime_seconds": _number(
            _first(
                lower,
                "runtime_seconds",
                "execution_time",
                "execution_time_seconds",
                "total_elapsed_time",
                "duration_seconds",
                "run_duration_seconds",
            )
        ),
        "cost_usd": _number(_first(lower, "cost_usd", "estimated_cost_usd", "rough_cost_usd")),
        "bytes_scanned": _number(
            _first(lower, "bytes_scanned", "bytes_read", "read_bytes", "scan_bytes")
        ),
        "credits_used": _number(
            _first(
                lower, "credits_used", "credits", "warehouse_credits", "credits_used_cloud_services"
            )
        ),
        "dbu": _number(_first(lower, "dbu", "dbus", "dbu_hours", "dbu_cost")),
        "source_row": row,
    }


def _row_keys(row: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    for field in ("unique_id", "model_name", "relation_name"):
        value = str(row.get(field) or "").strip()
        if value:
            keys.append(value)
            if "." in value:
                keys.append(value.split(".")[-1])
    query = str(row.get("query_text") or "")
    for token in _extract_relation_tokens(query):
        keys.append(token)
        if "." in token:
            keys.append(token.split(".")[-1])
    # stable unique order
    out: list[str] = []
    seen: set[str] = set()
    for key in keys:
        key = key.strip().strip('"`')
        if key and key.lower() not in seen:
            seen.add(key.lower())
            out.append(key)
    return out


def _aggregate_rows(key: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    engines = [str(r.get("engine") or "unknown") for r in rows]
    engine = max(set(engines), key=engines.count) if engines else "unknown"
    runtimes = [
        float(r["runtime_seconds"])
        for r in rows
        if isinstance(r.get("runtime_seconds"), (int, float))
    ]
    costs = [float(r["cost_usd"]) for r in rows if isinstance(r.get("cost_usd"), (int, float))]
    bytes_scanned = [
        float(r["bytes_scanned"]) for r in rows if isinstance(r.get("bytes_scanned"), (int, float))
    ]
    credits = [
        float(r["credits_used"]) for r in rows if isinstance(r.get("credits_used"), (int, float))
    ]
    dbus = [float(r["dbu"]) for r in rows if isinstance(r.get("dbu"), (int, float))]
    confidence = "high" if len(rows) >= 10 else ("medium" if len(rows) >= 3 else "low")
    notes = ["offline export; no live warehouse credentials used"]
    if not costs and credits:
        notes.append("cost_usd absent; credits_used available for relative calibration")
    if not costs and dbus:
        notes.append("cost_usd absent; DBU metric available for relative calibration")
    profile = WarehouseHistoryProfile(
        engine=engine,
        model_name=key,
        relation_name=key,
        sample_count=len(rows),
        avg_runtime_seconds=_avg(runtimes),
        p95_runtime_seconds=_p95(runtimes),
        avg_cost_usd=_avg(costs),
        avg_bytes_scanned=_avg(bytes_scanned),
        avg_credits_used=_avg(credits),
        avg_dbu=_avg(dbus),
        confidence=confidence,
        notes=notes,
    )
    return profile.to_dict()


def _first(row: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    return default


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", ""))
    except Exception:
        return None


def _avg(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    values = sorted(values)
    idx = min(len(values) - 1, int(round((len(values) - 1) * 0.95)))
    return round(values[idx], 4)


def _extract_relation_tokens(sql: str) -> list[str]:
    if not sql:
        return []
    tokens: list[str] = []
    for match in re.finditer(
        r"\b(?:from|join|merge\s+into|update|table)\s+([\w`\"\.]+)", sql, re.I
    ):
        token = match.group(1).strip('`"')
        if token and not token.startswith("{{"):
            tokens.append(token)
    return tokens[:12]
