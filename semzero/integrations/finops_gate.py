from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

SQL_FILE_EXTS = {".sql", ".sql.j2", ".jinja", ".ddl", ".dml"}
TEXT_FILE_EXTS = {".sql", ".py", ".yml", ".yaml", ".jinja", ".sql.j2", ".ddl", ".dml", ".md"}


@dataclass(frozen=True)
class FinOpsSignal:
    kind: str
    severity: str
    detail: str
    estimated_run_cost_usd: float = 0.0
    estimated_weekly_cost_usd: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "severity": self.severity,
            "detail": self.detail,
            "estimated_run_cost_usd": round(self.estimated_run_cost_usd, 2),
            "estimated_weekly_cost_usd": round(self.estimated_weekly_cost_usd, 2),
        }


@dataclass
class FinOpsSummary:
    source: str = "heuristic"
    confidence: str = "medium"
    projected_run_cost_usd: float = 0.0
    projected_weekly_cost_usd: float = 0.0
    projected_monthly_cost_usd: float = 0.0
    projected_weekly_dbu: float = 0.0
    blocked_weekend_waste_usd: float = 0.0
    recompute_radius: int = 0
    scope_assets: list[str] = field(default_factory=list)
    drivers: list[FinOpsSignal] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "confidence": self.confidence,
            "projected_run_cost_usd": round(self.projected_run_cost_usd, 2),
            "projected_weekly_cost_usd": round(self.projected_weekly_cost_usd, 2),
            "projected_monthly_cost_usd": round(self.projected_monthly_cost_usd, 2),
            "projected_weekly_dbu": round(self.projected_weekly_dbu, 2),
            "blocked_weekend_waste_usd": round(self.blocked_weekend_waste_usd, 2),
            "recompute_radius": int(self.recompute_radius),
            "scope_assets": list(dict.fromkeys(self.scope_assets))[:12],
            "drivers": [item.to_dict() for item in self.drivers[:8]],
            "notes": list(dict.fromkeys(self.notes))[:8],
        }


class FinOpsChangeAnalyser:
    """Cheap static FinOps pass for PR / pre-merge surfaces.

    This is intentionally heuristic-first: it turns transformation anti-patterns
    into explicit cost receipts before warehouse compute is burned.
    """

    def __init__(self, source_paths: Iterable[str] | None = None) -> None:
        self.source_paths = [str(p) for p in (source_paths or []) if str(p)]

    def analyse(self, focus_assets: Iterable[str] | None = None) -> FinOpsSummary:
        summary = FinOpsSummary(source="static_pr_scan", confidence="medium")
        scope_assets = [str(item) for item in (focus_assets or []) if str(item)]
        summary.scope_assets = scope_assets[:12]
        drivers: list[FinOpsSignal] = []
        files_scanned = 0

        for path_str in self.source_paths:
            path = Path(path_str)
            if not path.exists():
                continue
            if path.is_dir():
                candidates = sorted(
                    p
                    for p in path.rglob("*")
                    if p.is_file() and any(str(p).endswith(ext) for ext in TEXT_FILE_EXTS)
                )
            else:
                candidates = [path]
            for candidate in candidates[:250]:
                try:
                    text = candidate.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue
                files_scanned += 1
                drivers.extend(self._signals_for_text(text, candidate))

        if not drivers:
            summary.notes.append(
                "No obvious transformation-layer compute anti-patterns were detected in the current proof paths."
            )
            return summary

        summary.drivers = drivers[:10]
        summary.projected_run_cost_usd = round(
            sum(item.estimated_run_cost_usd for item in drivers), 2
        )
        summary.projected_weekly_cost_usd = round(
            sum(item.estimated_weekly_cost_usd for item in drivers), 2
        )
        summary.projected_monthly_cost_usd = round(summary.projected_weekly_cost_usd * 4.3, 2)
        summary.projected_weekly_dbu = round(summary.projected_weekly_cost_usd / 0.55, 2)
        summary.blocked_weekend_waste_usd = round(summary.projected_weekly_cost_usd * (2 / 7), 2)
        summary.recompute_radius = len(scope_assets)
        if any(item.severity == "high" for item in drivers):
            summary.confidence = "high"
        summary.notes.append(
            f"Scanned {files_scanned} code/config asset(s) for transformation-layer compute waste before merge."
        )
        if scope_assets:
            summary.notes.append(
                "Scoped around " + ", ".join(f"`{item}`" for item in scope_assets[:6]) + "."
            )
        return summary

    def _signals_for_text(self, text: str, path: Path) -> list[FinOpsSignal]:
        lowered = re.sub(r"\s+", " ", text.lower())
        signals: list[FinOpsSignal] = []
        basename = path.name
        join_count = lowered.count(" join ")
        star_count = lowered.count("select *") + lowered.count(".*")
        if "select *" in lowered:
            signals.append(
                FinOpsSignal(
                    "SELECT_STAR",
                    "medium",
                    f"{basename} uses SELECT * which propagates wide rows through downstream transforms.",
                    18.0 + star_count * 3.0,
                    126.0 + star_count * 21.0,
                )
            )
        if "cross join" in lowered or re.search(r"join\s+[a-z_][\w$]*\s+on\s+1\s*=\s*1", lowered):
            signals.append(
                FinOpsSignal(
                    "CARTESIAN_JOIN",
                    "high",
                    f"{basename} contains a cartesian-style join path that can explode warehouse spend.",
                    55.0,
                    385.0,
                )
            )
        if join_count >= 3:
            cost = 16.0 + (join_count - 3) * 6.0
            signals.append(
                FinOpsSignal(
                    "FANOUT_JOIN",
                    "high",
                    f"{basename} contains {join_count} joins; review fanout and selective filters before merge.",
                    cost,
                    cost * 7,
                )
            )
        if (
            ("group by" in lowered or "order by" in lowered)
            and "where" not in lowered
            and "limit" not in lowered
        ):
            signals.append(
                FinOpsSignal(
                    "UNBOUNDED_AGGREGATION",
                    "medium",
                    f"{basename} sorts or aggregates without an obvious selective predicate.",
                    14.0,
                    98.0,
                )
            )
        if (
            "materialized='table'" in lowered
            or 'materialized="table"' in lowered
            or "--full-refresh" in lowered
            or "full_refresh" in lowered
            or "create or replace table" in lowered
        ):
            signals.append(
                FinOpsSignal(
                    "FULL_REFRESH_PATH",
                    "high",
                    f"{basename} appears to rebuild full history instead of using incremental semantics.",
                    42.0,
                    294.0,
                )
            )
        if (
            "is_incremental()" not in lowered and ("{{ ref(" in lowered or "merge into" in lowered)
        ) or ("dateadd(" in lowered and "where" not in lowered):
            signals.append(
                FinOpsSignal(
                    "MISSING_INCREMENTAL_FILTER",
                    "high",
                    f"{basename} references dbt/warehouse transform semantics without an incremental boundary filter.",
                    28.0,
                    196.0,
                )
            )
        cte_count = lowered.count("with ") + lowered.count("), ") + lowered.count(", ")
        union_all_count = lowered.count(" union all ")
        if "lateral flatten" in lowered or "json_each(" in lowered or "unnest(" in lowered:
            if "where" not in lowered:
                signals.append(
                    FinOpsSignal(
                        "UNBOUNDED_FLATTEN",
                        "medium",
                        f"{basename} explodes semi-structured data without an obvious pre-filter.",
                        20.0,
                        140.0,
                    )
                )
        if ("partition by" not in lowered and "row_number()" in lowered) or (
            "qualify" in lowered and "partition by" not in lowered
        ):
            signals.append(
                FinOpsSignal(
                    "UNPARTITIONED_WINDOW",
                    "medium",
                    f"{basename} uses a global window/qualify path that may scan far more data than intended.",
                    16.0,
                    112.0,
                )
            )
        if (
            "merge into" in lowered
            and "when matched" in lowered
            and "when not matched" in lowered
            and "incremental_predicates" not in lowered
            and "where" not in lowered
        ):
            signals.append(
                FinOpsSignal(
                    "UNBOUNDED_MERGE",
                    "high",
                    f"{basename} merges history without obvious target pruning, which can multiply compute on every run.",
                    38.0,
                    266.0,
                )
            )
        if union_all_count >= 2:
            cost = 14.0 + union_all_count * 4.0
            signals.append(
                FinOpsSignal(
                    "UNION_ALL_FANIN",
                    "medium",
                    f"{basename} chains {union_all_count} UNION ALL branches; review whether wide historical scans or duplicate amplification are being introduced.",
                    cost,
                    cost * 7,
                )
            )
        if cte_count >= 4:
            cost = 12.0 + max(0, cte_count - 4) * 3.5
            signals.append(
                FinOpsSignal(
                    "DEEP_CTE_STACK",
                    "medium",
                    f"{basename} builds a deep CTE stack that can hide repeated scans and force optimizer spill paths.",
                    cost,
                    cost * 7,
                )
            )
        if "join" in lowered and "distinct" in lowered:
            signals.append(
                FinOpsSignal(
                    "JOIN_THEN_DEDUP",
                    "high",
                    f"{basename} joins and then de-duplicates, which often signals fanout-driven spend or unstable grain.",
                    24.0,
                    168.0,
                )
            )
        if "order by random()" in lowered or "sample (" in lowered or "tablesample" in lowered:
            signals.append(
                FinOpsSignal(
                    "EXPENSIVE_SAMPLING",
                    "medium",
                    f"{basename} uses random/sample operators that can force broad scans and shuffle-heavy execution.",
                    18.0,
                    126.0,
                )
            )
        if (
            "regexp_" in lowered or "regexp_replace" in lowered or "regexp_extract" in lowered
        ) and join_count >= 2:
            signals.append(
                FinOpsSignal(
                    "HEAVY_REGEX_PIPELINE",
                    "medium",
                    f"{basename} mixes regex-heavy transforms with multi-join paths, increasing CPU-bound warehouse cost.",
                    16.0,
                    112.0,
                )
            )
        return signals


def estimate_query_finops(
    query_text: str,
    score: float,
    calls: int | float | None = None,
    source: str = "",
) -> tuple[float, float, list[str]]:
    lowered = re.sub(r"\s+", " ", (query_text or "").lower())
    reasons: list[str] = []
    estimated_run_cost = 0.02 + max(0.0, score) * 0.004
    if re.search(r"\bselect\s+\*\b", lowered):
        estimated_run_cost += 0.03
        reasons.append("SELECT * expansion")
    join_count = lowered.count(" join ")
    if join_count:
        estimated_run_cost += join_count * 0.02
        reasons.append(f"{join_count} join(s)")
    if "group by" in lowered or "order by" in lowered:
        estimated_run_cost += 0.015
        reasons.append("global sort/aggregation")
    if "distinct " in lowered:
        estimated_run_cost += 0.01
        reasons.append("distinct de-duplication")
    if "over (" in lowered:
        estimated_run_cost += 0.015
        reasons.append("window operator")
    if (
        "--full-refresh" in lowered
        or "full_refresh" in lowered
        or "create or replace table" in lowered
    ):
        estimated_run_cost += 0.08
        reasons.append("full refresh / replace-table path")
    if "cross join" in lowered:
        estimated_run_cost += 0.09
        reasons.append("cartesian join")
    if lowered.count(" union all ") >= 2:
        estimated_run_cost += min(0.08, lowered.count(" union all ") * 0.015)
        reasons.append("union-all fan-in")
    if ("merge into" in lowered and "where" not in lowered) or (
        "is_incremental()" not in lowered and "{{ ref(" in lowered
    ):
        estimated_run_cost += 0.05
        reasons.append("unbounded incremental / merge path")
    if ("row_number()" in lowered or "qualify " in lowered) and "partition by" not in lowered:
        estimated_run_cost += 0.025
        reasons.append("global window/qualify")
    if "join" in lowered and "distinct" in lowered:
        estimated_run_cost += 0.025
        reasons.append("join then deduplicate")
    if (
        "lateral flatten" in lowered or "json_each(" in lowered or "unnest(" in lowered
    ) and "where" not in lowered:
        estimated_run_cost += 0.03
        reasons.append("semi-structured explode path")
    frequency = max(1.0, float(calls or 1.0))
    if source.startswith(("pg_stat", "snowflake", "databricks", "bigquery", "history:")):
        multiplier = min(80.0, math.log10(frequency + 1.0) * 16.0 + 1.0)
    else:
        multiplier = min(8.0, math.log10(frequency + 1.0) * 4.0 + 1.0)
    weekly = estimated_run_cost * multiplier * 7.0
    return round(estimated_run_cost, 4), round(weekly, 2), reasons
