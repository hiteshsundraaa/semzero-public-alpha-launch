from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .assumption_decay import (
    _iter_receipts,
    _load_jsonl,
    _feedback_index,
    _keys_for_finding,
    _exception_matches,
    _business_severity,
    _replay_status,
)

ASSUMPTION_RECEIPT_PREFIX = "dbt_assumption_gate_"
GOOD_FEEDBACK = {"agree", "fixed"}
BAD_FEEDBACK = {"disagree", "false_positive"}
ACCEPTED_RISK = {"accepted_risk"}


def _norm(value: Any, fallback: str = "unknown") -> str:
    text = str(value or "").strip()
    return text if text else fallback


def _source_node(finding: dict[str, Any]) -> dict[str, Any]:
    src = finding.get("source") or finding.get("source_node") or {}
    if not isinstance(src, dict):
        src = {}
    return src


def _source_name(finding: dict[str, Any]) -> str:
    src = _source_node(finding)
    return _norm(
        src.get("name")
        or src.get("unique_id")
        or finding.get("source_resource")
        or finding.get("source_path")
    )


def _source_unique_id(finding: dict[str, Any]) -> str:
    src = _source_node(finding)
    return _norm(
        src.get("unique_id")
        or src.get("name")
        or finding.get("source_resource")
        or finding.get("source_path")
    )


def _owner_candidates_from_node(node: dict[str, Any]) -> list[str]:
    out: list[str] = []
    meta = node.get("metadata") or {}
    for key in ("owner", "owners", "team", "teams", "group"):
        value = node.get(key) or meta.get(key)
        if isinstance(value, list):
            out.extend(str(x) for x in value if x)
        elif value:
            out.append(str(value))
    return out


def _owners_for_finding(finding: dict[str, Any]) -> list[str]:
    owners: list[str] = []
    owners.extend(_owner_candidates_from_node(_source_node(finding)))
    for node in finding.get("blast_radius") or []:
        if isinstance(node, dict):
            owners.extend(_owner_candidates_from_node(node))
    # lightweight fallback from business labels / paths. Keep unknown rather than over-infer.
    dedup: list[str] = []
    for owner in owners:
        owner = owner.strip()
        if owner and owner not in dedup:
            dedup.append(owner)
    return dedup or ["unknown"]


def _stable_id(finding: dict[str, Any]) -> str:
    return _norm(finding.get("stable_id") or finding.get("id") or finding.get("fingerprint"))


def _feedback_for_finding(
    finding: dict[str, Any], feedback_idx: dict[str, list[dict[str, Any]]]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in _keys_for_finding(finding):
        rows.extend(feedback_idx.get(key, []))
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for row in rows:
        marker = json.dumps(row, sort_keys=True)
        if marker not in seen:
            seen.add(marker)
            deduped.append(row)
    return deduped


def _new_bucket(kind: str, name: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "name": name,
        "finding_count": 0,
        "receipt_count": 0,
        "families": {},
        "drift_detected_count": 0,
        "replay_ran_count": 0,
        "high_business_count": 0,
        "max_business_severity": "UNKNOWN",
        "max_risk_score": 0,
        "cost_exposure_usd_per_month": 0.0,
        "feedback_count": 0,
        "agree_or_fixed_count": 0,
        "false_positive_count": 0,
        "accepted_risk_count": 0,
        "active_exception_count": 0,
        "expired_exception_count": 0,
        "stable_ids": [],
        "receipt_paths": [],
    }


def _severity_rank(value: str) -> int:
    order = {
        "UNKNOWN": 0,
        "INTERNAL_LOW": 1,
        "INTERNAL_HIGH": 2,
        "CUSTOMER_FACING": 3,
        "REVENUE_CRITICAL": 4,
        "EXEC_CRITICAL": 5,
        "BOARD_CRITICAL": 6,
    }
    return order.get(value, 0)


def _monthly_cost(finding: dict[str, Any]) -> float:
    for container in (
        finding.get("cost_estimate"),
        finding.get("cost"),
        finding.get("warehouse_cost"),
    ):
        if isinstance(container, dict):
            for key in (
                "estimated_extra_cost_per_month_usd",
                "estimated_cost_exposure_usd_per_month",
                "monthly_exposure_usd",
            ):
                try:
                    return float(container.get(key) or 0.0)
                except Exception:
                    pass
    try:
        return float(finding.get("estimated_extra_cost_per_month_usd") or 0.0)
    except Exception:
        return 0.0


def _update_bucket(
    bucket: dict[str, Any],
    finding: dict[str, Any],
    receipt_path: str,
    feedback: list[dict[str, Any]],
    active_ex: list[dict[str, Any]],
    expired_ex: list[dict[str, Any]],
) -> None:
    family = _norm(finding.get("family"))
    stable = _stable_id(finding)
    bucket["finding_count"] += 1
    bucket["families"][family] = bucket["families"].get(family, 0) + 1
    if receipt_path and receipt_path not in bucket["receipt_paths"]:
        bucket["receipt_paths"].append(receipt_path)
        bucket["receipt_count"] += 1
    if stable not in bucket["stable_ids"]:
        bucket["stable_ids"].append(stable)
    replay_ran, status, _fidelity = _replay_status(finding)
    if replay_ran:
        bucket["replay_ran_count"] += 1
    if status == "drift_detected":
        bucket["drift_detected_count"] += 1
    bsev = _business_severity(finding)
    if _severity_rank(bsev) >= _severity_rank("CUSTOMER_FACING"):
        bucket["high_business_count"] += 1
    if _severity_rank(bsev) > _severity_rank(str(bucket.get("max_business_severity") or "UNKNOWN")):
        bucket["max_business_severity"] = bsev
    try:
        bucket["max_risk_score"] = max(
            int(bucket.get("max_risk_score") or 0), int(finding.get("risk_score") or 0)
        )
    except Exception:
        pass
    bucket["cost_exposure_usd_per_month"] = round(
        float(bucket.get("cost_exposure_usd_per_month") or 0.0) + _monthly_cost(finding), 4
    )
    bucket["active_exception_count"] += len(active_ex)
    bucket["expired_exception_count"] += len(expired_ex)
    bucket["feedback_count"] += len(feedback)
    for row in feedback:
        disp = str(row.get("disposition") or "")
        if disp in GOOD_FEEDBACK:
            bucket["agree_or_fixed_count"] += 1
        if disp in BAD_FEEDBACK:
            bucket["false_positive_count"] += 1
        if disp in ACCEPTED_RISK:
            bucket["accepted_risk_count"] += 1


def _finalize_bucket(bucket: dict[str, Any]) -> dict[str, Any]:
    finding_count = max(int(bucket.get("finding_count") or 0), 1)
    feedback_count = int(bucket.get("feedback_count") or 0)
    drift_rate = float(bucket.get("drift_detected_count") or 0) / finding_count
    fp_rate = (
        float(bucket.get("false_positive_count") or 0) / feedback_count if feedback_count else 0.0
    )
    validated_rate = (
        float(bucket.get("agree_or_fixed_count") or 0) / feedback_count if feedback_count else 0.0
    )
    memory_score = 0
    memory_score += min(int(bucket.get("finding_count") or 0) * 5, 25)
    memory_score += min(int(bucket.get("drift_detected_count") or 0) * 8, 32)
    memory_score += 14 if int(bucket.get("high_business_count") or 0) else 0
    memory_score += 12 if float(bucket.get("cost_exposure_usd_per_month") or 0.0) > 0 else 0
    memory_score += min(int(bucket.get("agree_or_fixed_count") or 0) * 8, 24)
    memory_score += min(int(bucket.get("accepted_risk_count") or 0) * 5, 15)
    memory_score += min(int(bucket.get("expired_exception_count") or 0) * 10, 20)
    memory_score -= min(int(bucket.get("false_positive_count") or 0) * 12, 30)
    memory_score = max(0, min(100, memory_score))

    if fp_rate >= 0.5 and feedback_count >= 2:
        pattern = "noisy_pattern"
        recommendation = "Treat this as a tuning target before advisory promotion; tighten trigger or blast-radius scope."
    elif int(bucket.get("expired_exception_count") or 0):
        pattern = "expired_risk_memory"
        recommendation = (
            "Review expired accepted-risk memory before relying on this pattern for policy."
        )
    elif drift_rate >= 0.5 and int(bucket.get("agree_or_fixed_count") or 0):
        pattern = "validated_recurring_drift"
        recommendation = "Strong advisory candidate: recurring drift with human validation; keep non-blocking until more history accumulates."
    elif drift_rate >= 0.5:
        pattern = "recurring_drift_needs_feedback"
        recommendation = "Recurring replay drift needs reviewer feedback before policy promotion."
    elif int(bucket.get("accepted_risk_count") or 0) >= 2:
        pattern = "accepted_risk_memory"
        recommendation = "Accepted risk is recurring; review whether this should become a documented control or mitigation plan."
    elif feedback_count == 0 and int(bucket.get("max_risk_score") or 0) >= 80:
        pattern = "high_risk_unvalidated"
        recommendation = (
            "Request developer feedback; do not promote this pattern without validation."
        )
    else:
        pattern = "watch"
        recommendation = "Keep collecting receipts, replay evidence, and feedback."
    return {
        **bucket,
        "family_mix": dict(
            sorted(bucket.get("families", {}).items(), key=lambda kv: (-kv[1], kv[0]))
        ),
        "drift_rate": round(drift_rate, 4),
        "false_positive_rate": round(fp_rate, 4),
        "validated_feedback_rate": round(validated_rate, 4),
        "memory_score": memory_score,
        "memory_pattern": pattern,
        "recommendation": recommendation,
        "guardrail": "Drift Memory Lite is advisory aggregation only; it does not create new findings, block merges, or auto-change policy.",
    }


@dataclass(slots=True)
class AssumptionMemoryConfig:
    receipt_dir: str | Path = "data"
    feedback_file: str | Path | None = None
    exceptions_file: str | Path | None = None


class AssumptionMemoryBuilder:
    """Build organization/team/model-level drift memory from existing SemZero receipts.

    This intentionally does not run new detectors. It only aggregates existing evidence so
    broader memory cannot degrade detector accuracy.
    """

    def __init__(self, config: AssumptionMemoryConfig):
        self.config = config

    def build(self) -> dict[str, Any]:
        receipts = _iter_receipts(self.config.receipt_dir)
        feedback_file = (
            self.config.feedback_file or Path(self.config.receipt_dir) / "assumption_feedback.jsonl"
        )
        exceptions_file = (
            self.config.exceptions_file
            or Path(self.config.receipt_dir) / "assumption_exceptions.jsonl"
        )
        feedback_records = _load_jsonl(feedback_file)
        exception_records = _load_jsonl(exceptions_file)
        feedback_idx = _feedback_index(feedback_records)

        org = _new_bucket("organization", "all")
        by_family: dict[str, dict[str, Any]] = {}
        by_source: dict[str, dict[str, Any]] = {}
        by_owner: dict[str, dict[str, Any]] = {}
        by_business: dict[str, dict[str, Any]] = {}

        for receipt in receipts:
            rpath = str(receipt.get("_receipt_path") or "")
            for finding in receipt.get("findings") or []:
                feedback = _feedback_for_finding(finding, feedback_idx)
                active, expired = _exception_matches(finding, rpath, exception_records)
                family = _norm(finding.get("family"))
                source = _source_unique_id(finding)
                bsev = _business_severity(finding)
                _update_bucket(org, finding, rpath, feedback, active, expired)
                _update_bucket(
                    by_family.setdefault(family, _new_bucket("family", family)),
                    finding,
                    rpath,
                    feedback,
                    active,
                    expired,
                )
                _update_bucket(
                    by_source.setdefault(source, _new_bucket("source_resource", source)),
                    finding,
                    rpath,
                    feedback,
                    active,
                    expired,
                )
                _update_bucket(
                    by_business.setdefault(bsev, _new_bucket("business_severity", bsev)),
                    finding,
                    rpath,
                    feedback,
                    active,
                    expired,
                )
                for owner in _owners_for_finding(finding):
                    _update_bucket(
                        by_owner.setdefault(owner, _new_bucket("owner_or_team", owner)),
                        finding,
                        rpath,
                        feedback,
                        active,
                        expired,
                    )

        def top(rows: Iterable[dict[str, Any]], limit: int = 25) -> list[dict[str, Any]]:
            final = [_finalize_bucket(r) for r in rows]
            return sorted(
                final,
                key=lambda r: (
                    r.get("memory_score", 0),
                    r.get("finding_count", 0),
                    r.get("drift_detected_count", 0),
                ),
                reverse=True,
            )[:limit]

        org_final = _finalize_bucket(org)
        family_rows = top(by_family.values())
        source_rows = top(by_source.values())
        owner_rows = top(by_owner.values())
        business_rows = top(by_business.values())
        watchlist = [
            r
            for r in (family_rows + source_rows + owner_rows)
            if r.get("memory_pattern")
            in {
                "validated_recurring_drift",
                "recurring_drift_needs_feedback",
                "expired_risk_memory",
                "accepted_risk_memory",
                "noisy_pattern",
                "high_risk_unvalidated",
            }
        ]
        watchlist = sorted(
            watchlist,
            key=lambda r: (r.get("memory_score", 0), r.get("finding_count", 0)),
            reverse=True,
        )[:25]
        return {
            "memory_kind": "semzero_assumption_drift_memory_lite_v1_25",
            "scope": "core_data_only_existing_evidence",
            "receipt_count": len(receipts),
            "finding_count": org.get("finding_count", 0),
            "organization_memory": org_final,
            "top_family_memory": family_rows,
            "top_source_memory": source_rows,
            "top_owner_or_team_memory": owner_rows,
            "business_severity_memory": business_rows,
            "memory_watchlist": watchlist,
            "novelty_note": "Drift Memory Lite borrows risk-memory ideas from cybersec/devops but applies them only to existing data-assumption evidence: teams/models/families with recurring drift, accepted-risk debt, stale exceptions, or noise.",
            "accuracy_guardrail": "This report does not add detectors or emit new findings. It aggregates existing receipts, replay results, feedback, and exceptions, so broader memory does not reduce Assumption Gate precision.",
            "suggested_next_step": "Use the watchlist in weekly data-platform review; keep enforcement non-blocking unless replay and developer feedback are strong.",
        }

    def save_json(self, path: str | Path) -> dict[str, Any]:
        payload = self.build()
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        return payload

    def save_markdown(self, path: str | Path) -> dict[str, Any]:
        payload = self.build()
        lines = [
            "# SemZero Assumption Drift Memory Lite",
            "",
            payload["accuracy_guardrail"],
            "",
            payload["novelty_note"],
            "",
        ]
        org = payload.get("organization_memory") or {}
        lines += [
            "## Organization memory",
            "",
            f"- Receipts: {payload.get('receipt_count', 0)}",
            f"- Findings: {payload.get('finding_count', 0)}",
            f"- Memory score: {org.get('memory_score', 0)}",
            f"- Pattern: `{org.get('memory_pattern', 'watch')}`",
            f"- Drift rate: {org.get('drift_rate', 0)}",
            f"- False-positive rate: {org.get('false_positive_rate', 0)}",
            "",
        ]
        for title, key in (
            ("Family memory", "top_family_memory"),
            ("Source/model memory", "top_source_memory"),
            ("Owner/team memory", "top_owner_or_team_memory"),
            ("Business severity memory", "business_severity_memory"),
        ):
            lines += [f"## {title}", ""]
            for row in payload.get(key, [])[:10]:
                lines.append(
                    f"- `{row.get('name')}` · pattern={row.get('memory_pattern')} · score={row.get('memory_score')} · findings={row.get('finding_count')} · drift={row.get('drift_detected_count')} · fp={row.get('false_positive_count')} · recommendation={row.get('recommendation')}"
                )
            lines.append("")
        lines += ["## Memory watchlist", ""]
        for row in payload.get("memory_watchlist", [])[:15]:
            lines.append(
                f"- `{row.get('kind')}:{row.get('name')}` · {row.get('memory_pattern')} · score={row.get('memory_score')} · {row.get('recommendation')}"
            )
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return payload
