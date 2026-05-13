from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from html import escape


DEFAULT_RECEIPT_CANDIDATES = [
    "data/semzero_receipt.json",
    "data/premerge_bundle.json",
    "data/validation_premerge_bundle.json",
    "data/validation_report.json",
    "data/gate_result.json",
    "data/simulation_receipt.json",
    "data/chaos_report.json",
]


@dataclass(frozen=True)
class ReceiptSummary:
    kind: str
    path: str
    verdict: str
    freshness: str
    age_hours: float | None
    confidence: str
    evidence_completeness: str
    summary: dict[str, Any]
    payload: dict[str, Any]
    artifact_paths: dict[str, str]


def _load_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _save_json(data: dict[str, Any], path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def _parse_dt(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _age_hours(dt: datetime | None) -> float | None:
    if not dt:
        return None
    return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0)


def _freshness_label(age_hours: float | None, stale_after_hours: float) -> str:
    if age_hours is None:
        return "unknown"
    if age_hours <= max(stale_after_hours * 0.33, 1.0):
        return "fresh"
    if age_hours <= stale_after_hours:
        return "aging"
    return "stale"


def detect_receipt_kind(payload: dict[str, Any]) -> str:
    if {"gate_result", "wind_tunnel_receipt", "chaos_report"}.intersection(payload.keys()):
        return "premerge_bundle"
    if "summary" in payload and "scenarios" in payload:
        return "validation_report"
    if "verdict" in payload and "assessments" in payload:
        return "gate_result"
    if "queries_replayed" in payload and "verdict" in payload:
        return "wind_tunnel_receipt"
    if "summary" in payload and "mutation_results" in payload:
        return "chaos_report"
    if "reports" in payload and "summary" in payload and "verdict" in payload:
        return "composite_receipt"
    return "generic_json"


def _build_composite_from_reports(
    search_dir: str | Path = "data",
) -> tuple[dict[str, Any], str] | tuple[None, None]:
    base = Path(search_dir)
    if not base.exists():
        return None, None
    reports: dict[str, Any] = {}
    artifacts: dict[str, str] = {}
    mapping = {
        "gate_result": "gate_result.json",
        "wind_tunnel_receipt": "simulation_receipt.json",
        "chaos_report": "chaos_report.json",
        "drift_report": "drift_report.json",
        "validation_report": "validation_report.json",
        "override_ledger": "override_ledger.jsonl",
        "incident_ledger": "incident_ledger.jsonl",
    }
    for key, filename in mapping.items():
        path = base / filename
        if path.exists():
            if path.suffix.lower() == ".jsonl":
                reports[key] = {"entry_count": len(path.read_text(encoding="utf-8").splitlines())}
            else:
                reports[key] = _load_json(path)
            artifacts[key] = str(path)
    if not reports:
        return None, None
    gate = reports.get("gate_result", {})
    wind = reports.get("wind_tunnel_receipt", {})
    chaos = reports.get("chaos_report", {})
    validation = reports.get("validation_report", {})
    verdict = (
        gate.get("verdict")
        or validation.get("summary", {}).get("gate_verdict")
        or wind.get("verdict")
        or chaos.get("summary", {}).get("fragility_grade")
        or "UNKNOWN"
    )
    composite = {
        "receipt_id": f"composite::{base.resolve()}",
        "kind": "composite_receipt",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "verdict": verdict,
        "reports": reports,
        "report_status": {k: {"path": v, "status": "present"} for k, v in artifacts.items()},
        "artifact_paths": artifacts,
        "summary": {
            "blocked_by": gate.get("blocked_by", []),
            "review_reasons": gate.get("review_reasons", []),
            "queries_replayed": wind.get(
                "queries_replayed", validation.get("summary", {}).get("queries_replayed", 0)
            ),
            "queries_broken": wind.get(
                "queries_broken", validation.get("summary", {}).get("queries_broken", 0)
            ),
            "mutations_that_broke": chaos.get("summary", {}).get(
                "mutations_that_broke", validation.get("summary", {}).get("mutations_that_broke", 0)
            ),
            "reliability_score": gate.get(
                "reliability_score", validation.get("summary", {}).get("reliability_score")
            ),
        },
    }
    return composite, str(base / "semzero_receipt.json")


def autodetect_receipt_path(search_dir: str | Path = "data") -> str | None:
    base = Path(search_dir)
    candidates: list[Path] = []
    for candidate in DEFAULT_RECEIPT_CANDIDATES:
        path = Path(candidate)
        if not path.is_absolute():
            path = base / path.name if base.name != "data" else path
        if path.exists():
            candidates.append(path)
    if candidates:
        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return str(candidates[0])
    return None


def load_or_autodetect_receipt(
    receipt_path: str | None = None,
    search_dir: str | Path = "data",
    write_composite_to: str | None = None,
) -> ReceiptSummary:
    resolved = receipt_path or autodetect_receipt_path(search_dir)
    payload: dict[str, Any]
    path_label: str
    if resolved:
        payload = _load_json(resolved)
        path_label = str(resolved)
    else:
        composite, default_out = _build_composite_from_reports(search_dir)
        if not composite:
            raise FileNotFoundError(
                f"No SemZero receipt or report artifacts found under '{search_dir}'. "
                "Run premerge, validate-e2e, gate, wind-tunnel, or chaos first."
            )
        payload = composite
        path_label = default_out
        if write_composite_to:
            _save_json(composite, write_composite_to)
            path_label = write_composite_to
    summary = summarize_receipt(payload, path_label)
    if write_composite_to and summary.kind == "composite_receipt":
        _save_json(payload, write_composite_to)
        summary = summarize_receipt(payload, write_composite_to)
    return summary


def summarize_receipt(
    payload: dict[str, Any], path: str, stale_after_hours: float = 12.0
) -> ReceiptSummary:
    kind = detect_receipt_kind(payload)
    artifact_paths: dict[str, str] = {}
    summary: dict[str, Any] = {}
    verdict = "UNKNOWN"
    timestamp = None
    confidence = "medium"
    completeness = "partial"

    if kind == "premerge_bundle":
        gate = payload.get("gate_result") or {}
        wind = payload.get("wind_tunnel_receipt") or {}
        chaos = payload.get("chaos_report") or {}
        evidence = payload.get("evidence_summary") or {}
        artifact_paths = payload.get("artifact_paths") or {}
        verdict = str(gate.get("verdict") or wind.get("verdict") or "UNKNOWN")
        timestamp = _parse_dt(
            gate.get("evaluated_at") or wind.get("completed_at") or evidence.get("recorded_at")
        )
        completeness = "full" if gate and wind and chaos else "partial"
        confidence_score = wind.get("confidence_score")
        if isinstance(confidence_score, (int, float)):
            confidence = (
                "high"
                if confidence_score >= 0.9
                else "medium"
                if confidence_score >= 0.7
                else "low"
            )
        summary = {
            "root_cause": (
                gate.get("blocked_by")
                or gate.get("review_reasons")
                or ["No explicit reason recorded"]
            )[0],
            "blast_radius": gate.get("total_blast_radius", 0),
            "queries_replayed": wind.get("queries_replayed", 0),
            "queries_broken": wind.get("queries_broken", 0),
            "queries_mismatch": wind.get("queries_mismatch", 0),
            "mutations_applied": chaos.get("summary", {}).get("mutations_applied", 0),
            "mutations_that_broke": chaos.get("summary", {}).get("mutations_that_broke", 0),
            "recommended_action": gate.get("blocked_by", [])[:2]
            or gate.get("review_reasons", [])[:2],
        }
    elif kind == "validation_report":
        summ = payload.get("summary") or {}
        verdict = str(summ.get("gate_verdict") or "UNKNOWN")
        timestamp = _parse_dt(summ.get("generated_at") or payload.get("generated_at"))
        completeness = "full" if summ.get("scenario_count") else "partial"
        aligned = summ.get("aligned_predictions") or 0
        ground = summ.get("scenarios_with_ground_truth") or 0
        ratio = (aligned / ground) if ground else 0.0
        confidence = "high" if ratio >= 0.95 else "medium" if ratio >= 0.75 else "low"
        summary = {
            "root_cause": summ.get("root_cause", "Validation harness summary"),
            "queries_replayed": summ.get("queries_replayed", 0),
            "queries_broken": summ.get("queries_broken", 0),
            "mutations_that_broke": summ.get("mutations_that_broke", 0),
            "prediction_alignment": f"{aligned}/{ground}",
            "reliability_score": summ.get("reliability_score"),
        }
    elif kind == "gate_result":
        verdict = str(payload.get("verdict") or "UNKNOWN")
        timestamp = _parse_dt(payload.get("evaluated_at"))
        completeness = "gate_only"
        confidence = "high" if verdict in {"SAFE", "BLOCK"} else "medium"
        summary = {
            "root_cause": (
                payload.get("blocked_by")
                or payload.get("review_reasons")
                or ["No explicit reason recorded"]
            )[0],
            "blast_radius": payload.get("total_blast_radius", 0),
            "assessments": len(payload.get("assessments") or []),
        }
    elif kind == "wind_tunnel_receipt":
        verdict = str(payload.get("verdict") or "UNKNOWN")
        timestamp = _parse_dt(payload.get("completed_at") or payload.get("started_at"))
        completeness = "wind_tunnel_only"
        score = payload.get("confidence_score")
        confidence = "high" if isinstance(score, (int, float)) and score >= 0.9 else "medium"
        summary = {
            "queries_replayed": payload.get("queries_replayed", 0),
            "queries_broken": payload.get("queries_broken", 0),
            "queries_mismatch": payload.get("queries_mismatch", 0),
            "root_cause": (
                payload.get("semantic_risks")
                or [{"description": "No semantic risk summary recorded"}]
            )[0].get("description"),
        }
    elif kind == "chaos_report":
        chaos_summary = payload.get("summary") or {}
        verdict = str(chaos_summary.get("fragility_grade") or "UNKNOWN")
        timestamp = _parse_dt(chaos_summary.get("generated_at") or payload.get("generated_at"))
        completeness = "chaos_only"
        score = chaos_summary.get("fragility_score", 0)
        confidence = "high" if isinstance(score, (int, float)) and score >= 80 else "medium"
        summary = {
            "mutations_applied": chaos_summary.get("mutations_applied", 0),
            "mutations_that_broke": chaos_summary.get("mutations_that_broke", 0),
            "fragility_score": score,
            "root_cause": (
                (payload.get("recommended_hardening") or ["No hardening recommendation recorded"])[
                    0
                ]
            ),
        }
    elif kind == "composite_receipt":
        verdict = str(payload.get("verdict") or "UNKNOWN")
        timestamp = _parse_dt(payload.get("generated_at"))
        artifact_paths = payload.get("artifact_paths") or {}
        completeness = "composite"
        confidence = "medium"
        summary = payload.get("summary") or {}
        summary.setdefault(
            "root_cause",
            (
                (
                    summary.get("blocked_by")
                    or summary.get("review_reasons")
                    or ["Composite receipt from separate reports"]
                )[0]
            ),
        )
    else:
        verdict = str(payload.get("verdict") or "UNKNOWN")
        timestamp = _parse_dt(payload.get("generated_at") or payload.get("created_at"))
        summary = {"root_cause": "Generic JSON receipt"}

    age = _age_hours(timestamp)
    freshness = _freshness_label(age, stale_after_hours)
    return ReceiptSummary(
        kind=kind,
        path=path,
        verdict=verdict,
        freshness=freshness,
        age_hours=age,
        confidence=confidence,
        evidence_completeness=completeness,
        summary=summary,
        payload=payload,
        artifact_paths=artifact_paths,
    )


def render_receipt_markdown(summary: ReceiptSummary) -> str:
    lines = [
        "# SemZero Receipt",
        "",
        f"- Verdict: **{summary.verdict}**",
        f"- Kind: `{summary.kind}`",
        f"- Confidence: **{summary.confidence}**",
        f"- Freshness: **{summary.freshness}**",
        f"- Evidence completeness: **{summary.evidence_completeness}**",
        f"- Source: `{summary.path}`",
        "",
    ]
    root = summary.summary.get("root_cause")
    if root:
        lines += ["## Why", "", f"- {root}", ""]
    if summary.summary:
        lines += ["## Summary", ""]
        for key, value in summary.summary.items():
            if key == "root_cause" or value in (None, "", [], {}):
                continue
            lines.append(f"- **{key.replace('_', ' ').title()}**: {value}")
        lines.append("")
    if summary.artifact_paths:
        lines += ["## Linked artifacts", ""]
        for key, value in sorted(summary.artifact_paths.items()):
            lines.append(f"- `{key}` → `{value}`")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def render_receipt_html(summary: ReceiptSummary) -> str:
    badges = [
        f"<span class='badge verdict'>{escape(summary.verdict)}</span>",
        f"<span class='badge'>{escape(summary.confidence)}</span>",
        f"<span class='badge'>{escape(summary.freshness)}</span>",
        f"<span class='badge'>{escape(summary.evidence_completeness)}</span>",
    ]
    summary_items = "".join(
        f"<li><strong>{escape(key.replace('_', ' ').title())}</strong>: {escape(str(value))}</li>"
        for key, value in summary.summary.items()
        if key != "root_cause" and value not in (None, "", [], {})
    )
    artifact_items = "".join(
        f"<li><code>{escape(key)}</code> → <code>{escape(value)}</code></li>"
        for key, value in sorted(summary.artifact_paths.items())
    )
    return f"""<html><head><meta charset='utf-8'><title>SemZero Receipt</title><style>body{{font-family:Inter,Arial,sans-serif;max-width:960px;margin:32px auto;padding:0 20px;background:#f8fafc;color:#0f172a}}.card{{background:white;border:1px solid #e2e8f0;border-radius:18px;padding:22px;box-shadow:0 10px 24px rgba(15,23,42,.06)}}.badges{{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0 18px}}.badge{{background:#eef2ff;color:#3730a3;border-radius:999px;padding:6px 10px;font-size:12px;font-weight:700}}.badge.verdict{{background:#ecfeff;color:#155e75}}ul{{line-height:1.55}}code{{background:#f1f5f9;padding:2px 6px;border-radius:6px}}</style></head><body><section class='card'><h1>SemZero Receipt</h1><div class='badges'>{"".join(badges)}</div><p><strong>Source:</strong> <code>{escape(summary.path)}</code></p><h2>Why</h2><p>{escape(str(summary.summary.get("root_cause") or "No explicit root cause recorded."))}</p><h2>Summary</h2><ul>{summary_items or "<li>No structured summary fields recorded.</li>"}</ul><h2>Linked artifacts</h2><ul>{artifact_items or "<li>No linked artifacts recorded.</li>"}</ul></section></body></html>"""


def save_composite_receipt(
    search_dir: str | Path = "data", output: str | Path = "data/semzero_receipt.json"
) -> str:
    composite, _ = _build_composite_from_reports(search_dir)
    if not composite:
        raise FileNotFoundError(f"No SemZero report artifacts found under '{search_dir}'.")
    _save_json(composite, output)
    return str(output)


def compare_receipts(left: ReceiptSummary, right: ReceiptSummary) -> dict[str, Any]:
    verdict_changed = left.verdict != right.verdict
    changed_fields: list[str] = []
    interesting = [
        (
            "queries_replayed",
            left.summary.get("queries_replayed"),
            right.summary.get("queries_replayed"),
        ),
        ("queries_broken", left.summary.get("queries_broken"), right.summary.get("queries_broken")),
        (
            "queries_mismatch",
            left.summary.get("queries_mismatch"),
            right.summary.get("queries_mismatch"),
        ),
        (
            "mutations_that_broke",
            left.summary.get("mutations_that_broke"),
            right.summary.get("mutations_that_broke"),
        ),
        ("blast_radius", left.summary.get("blast_radius"), right.summary.get("blast_radius")),
    ]
    deltas: dict[str, dict[str, Any]] = {}
    for field, lhs, rhs in interesting:
        if lhs != rhs:
            changed_fields.append(field)
            deltas[field] = {"left": lhs, "right": rhs}
    return {
        "left_path": left.path,
        "right_path": right.path,
        "left_kind": left.kind,
        "right_kind": right.kind,
        "left_verdict": left.verdict,
        "right_verdict": right.verdict,
        "verdict_changed": verdict_changed,
        "freshness_changed": left.freshness != right.freshness,
        "changed_fields": changed_fields,
        "deltas": deltas,
        "left_root_cause": left.summary.get("root_cause"),
        "right_root_cause": right.summary.get("root_cause"),
        "left_confidence": left.confidence,
        "right_confidence": right.confidence,
    }
