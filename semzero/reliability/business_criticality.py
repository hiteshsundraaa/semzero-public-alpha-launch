from __future__ import annotations

import json
from pathlib import Path
from typing import Any

BUSINESS_SEVERITY_ORDER = [
    "BOARD_CRITICAL",
    "EXEC_CRITICAL",
    "REVENUE_CRITICAL",
    "CUSTOMER_FACING",
    "INTERNAL_HIGH",
    "INTERNAL_LOW",
    "UNKNOWN",
]

BUSINESS_SEVERITY_RANK = {
    name: len(BUSINESS_SEVERITY_ORDER) - idx for idx, name in enumerate(BUSINESS_SEVERITY_ORDER)
}

_PATTERN_SEVERITY: list[tuple[list[str], str, str]] = [
    (
        ["board", "investor", "kpi_board", "board_pack"],
        "BOARD_CRITICAL",
        "board/investor reporting keyword",
    ),
    (
        ["cfo", "ceo", "cto", "vp_", "exec", "executive", "okr", "quarterly", "annual_report"],
        "EXEC_CRITICAL",
        "executive reporting keyword",
    ),
    (
        [
            "revenue",
            "mrr",
            "arr",
            "billing",
            "payment",
            "invoice",
            "refund",
            "churn",
            "ltv",
            "subscription",
            "transaction",
        ],
        "REVENUE_CRITICAL",
        "revenue/billing keyword",
    ),
    (
        ["customer", "user_facing", "nps", "csat", "sla", "public_api"],
        "CUSTOMER_FACING",
        "customer-facing keyword",
    ),
    (
        ["ops", "internal", "analytics", "reporting", "pipeline", "mart"],
        "INTERNAL_HIGH",
        "internal analytics/ops keyword",
    ),
    (
        ["staging", "raw", "temp", "log", "event", "audit", "debug"],
        "INTERNAL_LOW",
        "staging/raw/supporting-data keyword",
    ),
]


def load_criticality_registry(path: str | Path | None) -> dict[str, Any]:
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(str(p))
    text = p.read_text(encoding="utf-8")
    if p.suffix.lower() in {".yml", ".yaml"}:
        try:
            import yaml  # type: ignore

            payload = yaml.safe_load(text) or {}
            return payload if isinstance(payload, dict) else {}
        except Exception:
            # Simple JSON fallback for minimal environments.
            return json.loads(text)
    return json.loads(text)


def infer_business_criticality(
    node_id: str,
    name: str = "",
    node_type: str = "",
    path: str = "",
    owner: str = "",
    registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    registry = registry or {}
    candidates = [node_id, name, path]
    # registry supports either {nodes:{...}} or flat {...}
    nodes = registry.get("nodes") if isinstance(registry.get("nodes"), dict) else registry
    for key in candidates:
        if key and isinstance(nodes.get(key), dict):
            raw = nodes[key]
            severity = str(raw.get("business_severity") or raw.get("severity") or "UNKNOWN").upper()
            if severity not in BUSINESS_SEVERITY_RANK:
                severity = "UNKNOWN"
            return {
                "business_severity": severity,
                "business_label": str(
                    raw.get("label") or raw.get("business_label") or name or node_id
                ),
                "business_reason": str(raw.get("reason") or "explicit criticality registry"),
                "criticality_source": "registry",
                "business_rank": BUSINESS_SEVERITY_RANK.get(severity, 0),
            }
    blob = " ".join(str(x or "") for x in (node_id, name, node_type, path, owner)).lower()
    for patterns, severity, reason in _PATTERN_SEVERITY:
        for pat in patterns:
            if pat in blob:
                return {
                    "business_severity": severity,
                    "business_label": _label(name or node_id),
                    "business_reason": f"{reason}: '{pat}'",
                    "criticality_source": "keyword_inference",
                    "business_rank": BUSINESS_SEVERITY_RANK.get(severity, 0),
                }
    return {
        "business_severity": "UNKNOWN",
        "business_label": _label(name or node_id),
        "business_reason": "no explicit registry or business keyword match",
        "criticality_source": "unknown",
        "business_rank": 0,
    }


def summarize_business_impact(findings: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {sev: 0 for sev in BUSINESS_SEVERITY_ORDER}
    labels: list[str] = []
    top_nodes: list[dict[str, Any]] = []
    highest = "UNKNOWN"
    for finding in findings:
        nodes = finding.get("blast_radius") or []
        for node in nodes:
            sev = str(node.get("business_severity") or "UNKNOWN")
            if sev not in counts:
                sev = "UNKNOWN"
            counts[sev] += 1
            if sev != "UNKNOWN" and len(labels) < 5:
                labels.append(
                    str(
                        node.get("business_label")
                        or node.get("name")
                        or node.get("unique_id")
                        or sev
                    )
                )
                top_nodes.append(node)
    for sev in BUSINESS_SEVERITY_ORDER:
        if counts.get(sev, 0):
            highest = sev
            break
    return {
        "highest_business_severity": highest,
        "business_severity_counts": counts,
        "business_critical_node_count": sum(
            counts[s]
            for s in ("BOARD_CRITICAL", "EXEC_CRITICAL", "REVENUE_CRITICAL", "CUSTOMER_FACING")
        ),
        "top_business_labels": labels,
        "top_business_nodes": top_nodes[:5],
        "summary": _summary_sentence(highest, labels, counts),
    }


def _summary_sentence(highest: str, labels: list[str], counts: dict[str, int]) -> str:
    if highest == "UNKNOWN":
        return "No high-business-critical downstream assets were identified."
    if labels:
        prefix = ", ".join(labels[:3])
        more = "" if len(labels) <= 3 else f" and {len(labels) - 3} more"
        return f"This finding reaches {highest.lower().replace('_', ' ')} assets: {prefix}{more}."
    return f"This finding reaches {highest.lower().replace('_', ' ')} downstream assets."


def _label(value: str) -> str:
    name = str(value or "").split(".")[-1].replace("_", " ").strip()
    return " ".join(w.capitalize() for w in name.split()) or "Unknown"
