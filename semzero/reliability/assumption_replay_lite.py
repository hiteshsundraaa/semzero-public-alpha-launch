from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class ReplayLiteResult:
    family: str
    status: str
    drift_metric: float | None
    drift_unit: str
    summary: str
    evidence: dict[str, Any]
    limitations: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "semzero_assumption_validation_replay_lite_v1",
            "family": self.family,
            "status": self.status,
            "replay_ran": True,
            "drift_metric": self.drift_metric,
            "drift_unit": self.drift_unit,
            "summary": self.summary,
            "evidence_source": "local_fixture_or_sample",
            "requires_live_database": False,
            "requires_credentials": False,
            "evidence": self.evidence,
            "limitations": self.limitations,
            "honesty_note": "Replay Lite is a targeted local assumption check from supplied fixture/sample data; it does not connect to a live warehouse, require credentials, clone production data, or run production queries.",
        }


def load_replay_fixtures(path: str | Path | None) -> dict[str, Any]:
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _family_payload(
    fixtures: dict[str, Any], family: str, resource_name: str = "", resource_id: str = ""
) -> dict[str, Any]:
    if not fixtures:
        return {}
    families = fixtures.get("families") if isinstance(fixtures.get("families"), dict) else fixtures
    payload = families.get(family) if isinstance(families, dict) else None
    if not isinstance(payload, dict):
        return {}
    # Optional resource-specific override.
    by_resource = payload.get("resources")
    if isinstance(by_resource, dict):
        for key in (resource_id, resource_name):
            if key and isinstance(by_resource.get(key), dict):
                merged = dict(payload)
                merged.update(by_resource[key])
                return merged
    return payload


def run_replay_lite(
    fixtures: dict[str, Any], family: str, resource_name: str = "", resource_id: str = ""
) -> dict[str, Any]:
    payload = _family_payload(fixtures, family, resource_name, resource_id)
    if not payload:
        return {
            "kind": "semzero_assumption_validation_replay_lite_v1",
            "family": family,
            "status": "not_run",
            "replay_ran": False,
            "summary": "No Replay Lite fixture/sample data was supplied for this assumption family.",
            "evidence_source": "none",
            "requires_live_database": False,
            "requires_credentials": False,
            "limitations": ["No local replay fixture matched this finding."],
        }
    try:
        if family == "temporal_bucket":
            return _temporal(payload).to_dict()
        if family == "incremental_filter":
            return _incremental(payload).to_dict()
        if family == "join_cardinality":
            return _join(payload).to_dict()
        if family == "enum_domain_closure":
            return _enum(payload).to_dict()
        if family == "null_default_fallback":
            return _null(payload).to_dict()
        if family == "materialization_cost":
            return _materialization(payload).to_dict()
    except Exception as exc:
        return {
            "kind": "semzero_assumption_validation_replay_lite_v1",
            "family": family,
            "status": "error",
            "replay_ran": False,
            "summary": f"Replay Lite fixture could not be evaluated: {exc}",
            "evidence_source": "local_fixture_or_sample",
            "requires_live_database": False,
            "requires_credentials": False,
            "limitations": ["Fixture evaluation failed; treat replay evidence as unavailable."],
        }
    return {
        "kind": "semzero_assumption_validation_replay_lite_v1",
        "family": family,
        "status": "unsupported_family",
        "replay_ran": False,
        "summary": "Replay Lite does not yet support this assumption family.",
        "evidence_source": "none",
        "requires_live_database": False,
        "requires_credentials": False,
        "limitations": ["Unsupported Replay Lite family."],
    }


def _parse_ts(value: str) -> datetime:
    txt = str(value).replace("Z", "+00:00")
    dt = datetime.fromisoformat(txt)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _temporal(payload: dict[str, Any]) -> ReplayLiteResult:
    rows = payload.get("events") or payload.get("rows") or []
    offset_hours = float(
        payload.get("after_timezone_offset_hours", payload.get("timezone_offset_hours", 0))
    )
    moved = 0
    total = 0
    for row in rows:
        ts = row.get("event_ts") or row.get("timestamp") or row.get("event_time")
        if not ts:
            continue
        total += 1
        before = _parse_ts(ts).astimezone(timezone.utc).date()
        after = (_parse_ts(ts).astimezone(timezone.utc) + timedelta(hours=offset_hours)).date()
        if before != after:
            moved += 1
    pct = (moved / total * 100.0) if total else 0.0
    status = "drift_detected" if moved else "no_drift_detected"
    return ReplayLiteResult(
        "temporal_bucket",
        status,
        round(pct, 4),
        "percent_rows_moved_bucket",
        f"Using supplied local sample evidence, {moved}/{total} sampled rows moved reporting bucket under the supplied timezone/date-boundary replay.",
        {"sample_rows": total, "moved_rows": moved, "offset_hours": offset_hours},
        ["Uses supplied sample rows, not full production data."],
    )


def _incremental(payload: dict[str, Any]) -> ReplayLiteResult:
    old_selected = float(payload.get("old_selected_rows", 0))
    new_selected = float(payload.get("new_selected_rows", 0))
    if not old_selected and payload.get("rows"):
        # Optional simple mode: fixture supplies booleans old_select/new_select per row.
        rows = payload.get("rows") or []
        old_selected = sum(1 for r in rows if r.get("old_select"))
        new_selected = sum(1 for r in rows if r.get("new_select"))
    ratio = (
        (new_selected / old_selected) if old_selected else (float("inf") if new_selected else 1.0)
    )
    status = "drift_detected" if ratio > 1.5 else "no_material_drift_detected"
    return ReplayLiteResult(
        "incremental_filter",
        status,
        None if ratio == float("inf") else round(ratio, 4),
        "new_vs_old_selected_row_ratio",
        f"Using supplied local sample/precomputed evidence, Replay Lite selected {new_selected:g} rows after vs {old_selected:g} before for the incremental predicate.",
        {
            "old_selected_rows": old_selected,
            "new_selected_rows": new_selected,
            "selection_ratio": None if ratio == float("inf") else ratio,
        },
        [
            "Selection counts come from supplied fixture/sample or precomputed local replay, not warehouse query profile."
        ],
    )


def _join(payload: dict[str, Any]) -> ReplayLiteResult:
    left = payload.get("left") or payload.get("orders") or []
    right = payload.get("right") or payload.get("dimension") or []
    left_key = payload.get("left_key", "key")
    right_key = payload.get("right_key", "key")
    counts: dict[Any, int] = {}
    for r in right:
        counts[r.get(right_key)] = counts.get(r.get(right_key), 0) + 1
    joined = 0
    for row in left:
        joined += counts.get(row.get(left_key), 0)
    before = len(left)
    ratio = (joined / before) if before else 1.0
    status = "drift_detected" if ratio > 1.05 else "no_fanout_detected"
    return ReplayLiteResult(
        "join_cardinality",
        status,
        round(ratio, 4),
        "join_output_to_left_row_ratio",
        f"Using supplied local sample evidence, Replay Lite join produced {joined} rows from {before} left rows.",
        {
            "left_rows": before,
            "right_rows": len(right),
            "joined_rows": joined,
            "fanout_ratio": ratio,
        },
        ["Join check uses supplied sample rows and key names, not full production distribution."],
    )


def _enum(payload: dict[str, Any]) -> ReplayLiteResult:
    values = payload.get("values") or payload.get("statuses") or []
    handled = set(payload.get("handled_values") or [])
    unhandled = [v for v in values if v not in handled]
    pct = (len(unhandled) / len(values) * 100.0) if values else 0.0
    status = "drift_detected" if unhandled else "no_unhandled_values_detected"
    return ReplayLiteResult(
        "enum_domain_closure",
        status,
        round(pct, 4),
        "percent_unhandled_values",
        f"Using supplied local sample evidence, Replay Lite found {len(unhandled)}/{len(values)} sample domain values not covered by the supplied mapping/filter.",
        {
            "sample_values": len(values),
            "handled_values": sorted(handled),
            "unhandled_values": unhandled[:20],
        },
        [
            "Domain coverage is based on supplied sampled values/mapping, not exhaustive production domain analysis."
        ],
    )


def _null(payload: dict[str, Any]) -> ReplayLiteResult:
    rows = payload.get("rows") or []
    field = payload.get("field", "value")
    nulls = sum(1 for r in rows if r.get(field) is None)
    pct = (nulls / len(rows) * 100.0) if rows else 0.0
    status = "drift_detected" if nulls else "no_masked_nulls_detected"
    return ReplayLiteResult(
        "null_default_fallback",
        status,
        round(pct, 4),
        "percent_rows_masked_by_fallback",
        f"Using supplied local sample evidence, Replay Lite found {nulls}/{len(rows)} sampled rows where `{field}` would be masked by fallback logic.",
        {
            "sample_rows": len(rows),
            "field": field,
            "masked_null_rows": nulls,
            "fallback_value": payload.get("fallback_value", 0),
        },
        ["Null masking check uses supplied sample rows, not production null-rate history."],
    )


def _materialization(payload: dict[str, Any]) -> ReplayLiteResult:
    old_rows = float(payload.get("old_processed_rows", 0))
    new_rows = float(payload.get("new_processed_rows", 0))
    ratio = (new_rows / old_rows) if old_rows else (float("inf") if new_rows else 1.0)
    status = "drift_detected" if ratio > 2.0 else "no_material_drift_detected"
    return ReplayLiteResult(
        "materialization_cost",
        status,
        None if ratio == float("inf") else round(ratio, 4),
        "new_vs_old_processed_row_ratio",
        f"Using supplied local fixture/precomputed evidence, Replay Lite materialization scope processed {new_rows:g} rows after vs {old_rows:g} before.",
        {
            "old_processed_rows": old_rows,
            "new_processed_rows": new_rows,
            "scope_ratio": None if ratio == float("inf") else ratio,
        },
        [
            "Materialization scope is based on supplied fixture/precomputed counts, not a live warehouse run."
        ],
    )
