from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ASSUMPTION_RECEIPT_PREFIX = "dbt_assumption_gate_"
GOOD_FEEDBACK = {"agree", "fixed"}
BAD_FEEDBACK = {"disagree", "false_positive"}
ACCEPTED_RISK = {"accepted_risk"}
HIGH_BUSINESS = {"BOARD_CRITICAL", "EXEC_CRITICAL", "REVENUE_CRITICAL", "CUSTOMER_FACING"}


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _iter_receipts(receipt_dir: str | Path) -> list[dict[str, Any]]:
    root = Path(receipt_dir)
    if not root.exists():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if str(payload.get("receipt_kind", "")).startswith(ASSUMPTION_RECEIPT_PREFIX):
            payload["_receipt_path"] = str(path)
            if not payload.get("generated_at"):
                try:
                    payload["generated_at"] = datetime.fromtimestamp(
                        path.stat().st_mtime, timezone.utc
                    ).isoformat()
                except Exception:
                    pass
            rows.append(payload)
    return rows


def _load_jsonl(path: str | Path | None) -> list[dict[str, Any]]:
    if not path:
        return []
    p = Path(path)
    if not p.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except Exception:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _finding_key(finding: dict[str, Any]) -> str:
    return str(
        finding.get("stable_id") or finding.get("id") or finding.get("fingerprint") or "unknown"
    )


def _keys_for_finding(finding: dict[str, Any]) -> set[str]:
    return {
        str(x)
        for x in (
            finding.get("stable_id"),
            finding.get("id"),
            finding.get("legacy_id"),
            finding.get("fingerprint"),
        )
        if x
    }


def _feedback_index(records: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    idx: dict[str, list[dict[str, Any]]] = {}
    for row in records:
        for key in (row.get("stable_finding_id"), row.get("finding_id"), row.get("fingerprint")):
            if key:
                idx.setdefault(str(key), []).append(row)
    return idx


def _exception_matches(
    finding: dict[str, Any], receipt_path: str, records: Iterable[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    active: list[dict[str, Any]] = []
    expired: list[dict[str, Any]] = []
    stable = str(finding.get("stable_id") or finding.get("id") or "")
    family = str(finding.get("family") or "")
    source = finding.get("source") or {}
    source_id = str(source.get("unique_id") or finding.get("source_resource") or "")
    source_path = str(source.get("path") or finding.get("source_path") or "")
    fingerprint = str(finding.get("fingerprint") or "")
    now = datetime.now(timezone.utc)
    for row in records:
        scope = str(row.get("scope") or "stable_id").lower()
        value = str(row.get("value") or "")
        ok = False
        if scope == "global":
            ok = True
        elif scope == "stable_id":
            ok = value in {stable, fingerprint, str(finding.get("legacy_id") or "")}
        elif scope == "family":
            ok = value == family
        elif scope == "source":
            ok = value in {source_id, source_path}
        elif scope == "receipt":
            ok = value == receipt_path
        if not ok:
            continue
        expires = _parse_dt(row.get("expires_at"))
        item = {
            k: row.get(k, "")
            for k in ("scope", "value", "reason", "owner", "expires_at", "ticket", "action")
        }
        if expires and expires < now:
            expired.append(item)
        else:
            active.append(item)
    return active, expired


def _business_severity(finding: dict[str, Any]) -> str:
    impact = finding.get("business_impact") or {}
    if impact.get("highest_business_severity"):
        return str(impact.get("highest_business_severity"))
    for node in finding.get("blast_radius") or []:
        meta = node.get("metadata") or {}
        sev = meta.get("business_severity") or node.get("business_severity")
        if sev:
            return str(sev)
    return "UNKNOWN"


def _replay_status(finding: dict[str, Any]) -> tuple[bool, str, float]:
    replay = finding.get("validation_replay") or {}
    fidelity = finding.get("replay_fidelity") or {}
    return (
        bool(replay.get("replay_ran")),
        str(replay.get("status") or "not_run"),
        float(fidelity.get("score") or 0.0),
    )


@dataclass(slots=True)
class AssumptionDecayConfig:
    receipt_dir: str | Path = "data"
    feedback_file: str | Path | None = None
    exceptions_file: str | Path | None = None
    recurring_threshold: int = 2
    stale_days: int = 30
    review_due_days: int = 14


class AssumptionDecayTracker:
    """Track assumption fragility over time from stable IDs, receipts, feedback, replay, and exceptions.

    This is intentionally advisory-only. It does not mutate policy or block CI.
    """

    def __init__(self, config: AssumptionDecayConfig):
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

        groups: dict[str, dict[str, Any]] = {}
        now = datetime.now(timezone.utc)
        for receipt in receipts:
            rpath = str(receipt.get("_receipt_path") or "")
            rtime = _parse_dt(receipt.get("generated_at"))
            for finding in receipt.get("findings") or []:
                stable = _finding_key(finding)
                group = groups.setdefault(
                    stable,
                    {
                        "stable_id": stable,
                        "family": finding.get("family") or "unknown",
                        "occurrence_count": 0,
                        "receipt_paths": [],
                        "first_seen": None,
                        "last_seen": None,
                        "max_risk_score": 0,
                        "max_business_severity": "UNKNOWN",
                        "replay_ran_count": 0,
                        "drift_detected_count": 0,
                        "fidelity_scores": [],
                        "active_exception_count": 0,
                        "expired_exception_count": 0,
                        "feedback_count": 0,
                        "agree_count": 0,
                        "false_positive_count": 0,
                        "fixed_count": 0,
                        "accepted_risk_count": 0,
                        "business_critical_occurrences": 0,
                        "latest_summary": finding.get("summary") or finding.get("assumption") or "",
                        "latest_drift_summary": (
                            (finding.get("assumption_diff") or {}).get("drift_summary") or ""
                        ),
                    },
                )
                group["occurrence_count"] += 1
                group["receipt_paths"].append(rpath)
                if rtime:
                    iso = rtime.isoformat()
                    group["first_seen"] = (
                        min([x for x in [group["first_seen"], iso] if x])
                        if group["first_seen"]
                        else iso
                    )
                    group["last_seen"] = (
                        max([x for x in [group["last_seen"], iso] if x])
                        if group["last_seen"]
                        else iso
                    )
                group["max_risk_score"] = max(
                    int(group.get("max_risk_score") or 0), int(finding.get("risk_score") or 0)
                )
                bsev = _business_severity(finding)
                # Ordered by rough business impact.
                order = {
                    "UNKNOWN": 0,
                    "INTERNAL_LOW": 1,
                    "INTERNAL_HIGH": 2,
                    "CUSTOMER_FACING": 3,
                    "REVENUE_CRITICAL": 4,
                    "EXEC_CRITICAL": 5,
                    "BOARD_CRITICAL": 6,
                }
                if order.get(bsev, 0) > order.get(
                    str(group.get("max_business_severity") or "UNKNOWN"), 0
                ):
                    group["max_business_severity"] = bsev
                if bsev in HIGH_BUSINESS:
                    group["business_critical_occurrences"] += 1
                replay_ran, status, fidelity = _replay_status(finding)
                if replay_ran:
                    group["replay_ran_count"] += 1
                if status == "drift_detected":
                    group["drift_detected_count"] += 1
                if fidelity:
                    group["fidelity_scores"].append(fidelity)
                active, expired = _exception_matches(finding, rpath, exception_records)
                group["active_exception_count"] += len(active)
                group["expired_exception_count"] += len(expired)
                fbacks: list[dict[str, Any]] = []
                for key in _keys_for_finding(finding):
                    fbacks.extend(feedback_idx.get(key, []))
                # Dedup feedback records by JSON repr.
                seen = set()
                deduped = []
                for row in fbacks:
                    marker = json.dumps(row, sort_keys=True)
                    if marker not in seen:
                        seen.add(marker)
                        deduped.append(row)
                group["feedback_count"] += len(deduped)
                for row in deduped:
                    disp = str(row.get("disposition") or "")
                    if disp in GOOD_FEEDBACK:
                        group["agree_count"] += 1
                    if disp == "fixed":
                        group["fixed_count"] += 1
                    if disp in BAD_FEEDBACK:
                        group["false_positive_count"] += 1
                    if disp in ACCEPTED_RISK:
                        group["accepted_risk_count"] += 1

        decay_nodes: list[dict[str, Any]] = []
        family_rollup: dict[str, dict[str, Any]] = {}
        for stable, group in groups.items():
            occurrence_count = int(group["occurrence_count"])
            replay_count = int(group["replay_ran_count"])
            drift_count = int(group["drift_detected_count"])
            fp = int(group["false_positive_count"])
            feedback_count = int(group["feedback_count"])
            accepted = int(group["accepted_risk_count"])
            expired = int(group["expired_exception_count"])
            active = int(group["active_exception_count"])
            max_risk = int(group["max_risk_score"])
            business_critical = int(group["business_critical_occurrences"])
            avg_fidelity = (
                round(sum(group["fidelity_scores"]) / len(group["fidelity_scores"]), 4)
                if group["fidelity_scores"]
                else None
            )
            last_seen_dt = _parse_dt(group.get("last_seen"))
            age_days = (now - last_seen_dt).days if last_seen_dt else None

            signals: list[str] = []
            if occurrence_count >= self.config.recurring_threshold:
                signals.append("recurring_stable_assumption")
            if drift_count >= self.config.recurring_threshold:
                signals.append("recurring_replay_validated_drift")
            if active >= 2 or accepted >= 2:
                signals.append("repeated_accepted_risk")
            if expired:
                signals.append("expired_exception_debt")
            if fp >= 2:
                signals.append("repeated_false_positive_feedback")
            if feedback_count == 0 and (max_risk >= 80 or business_critical):
                signals.append("high_risk_unreviewed")
            if age_days is not None and age_days >= self.config.stale_days:
                signals.append("stale_assumption_evidence")
            elif age_days is not None and age_days >= self.config.review_due_days:
                signals.append("review_due_assumption_evidence")
            if avg_fidelity is not None and avg_fidelity < 0.5:
                signals.append("low_fidelity_history")

            decay_score = 0
            decay_score += min(occurrence_count * 8, 32)
            decay_score += min(drift_count * 12, 36)
            decay_score += min((active + accepted) * 8, 24)
            decay_score += expired * 12
            decay_score += 18 if business_critical else 0
            decay_score += 12 if max_risk >= 90 else 6 if max_risk >= 75 else 0
            decay_score += (
                10 if feedback_count == 0 and (max_risk >= 80 or business_critical) else 0
            )
            decay_score -= min(fp * 12, 36)
            decay_score = max(0, min(100, decay_score))

            if "repeated_false_positive_feedback" in signals:
                state = "tune_or_suppress"
                recommendation = "Tighten trigger/blast-radius scope or suppress this stable finding before promoting policy."
            elif "expired_exception_debt" in signals:
                state = "review_expired_exception"
                recommendation = "Review the expired accepted-risk exception before relying on this finding for policy."
            elif "recurring_replay_validated_drift" in signals and feedback_count:
                state = "decaying_high_confidence"
                recommendation = "Recurring replay-validated drift with feedback: candidate for advisory policy, not hard blocking."
            elif "recurring_replay_validated_drift" in signals:
                state = "decaying_needs_feedback"
                recommendation = (
                    "Recurring replay-validated drift needs human feedback before policy promotion."
                )
            elif "repeated_accepted_risk" in signals:
                state = "accepted_risk_debt"
                recommendation = "Accepted risk is recurring; review owner/expiry and decide whether to mitigate or renew."
            elif "high_risk_unreviewed" in signals:
                state = "needs_review"
                recommendation = (
                    "High-risk assumption has no feedback; request reviewer disposition."
                )
            elif "recurring_stable_assumption" in signals:
                state = "watch"
                recommendation = (
                    "Recurring assumption; keep in shadow/advisory and collect feedback."
                )
            else:
                state = "stable_or_insufficient_history"
                recommendation = "Insufficient decay signal; keep collecting receipts and feedback."

            row = {
                **{k: v for k, v in group.items() if k != "fidelity_scores"},
                "average_fidelity_score": avg_fidelity,
                "age_days_since_last_seen": age_days,
                "decay_score": decay_score,
                "decay_state": state,
                "decay_signals": signals,
                "recommendation": recommendation,
                "guardrail": "Decay Tracking Lite is advisory-only; it does not block or auto-change policy.",
            }
            decay_nodes.append(row)
            fam = str(group["family"])
            f = family_rollup.setdefault(
                fam,
                {
                    "family": fam,
                    "stable_assumption_count": 0,
                    "occurrence_count": 0,
                    "decay_score_max": 0,
                    "states": {},
                    "replay_validated_drift_count": 0,
                    "false_positive_count": 0,
                    "accepted_risk_count": 0,
                },
            )
            f["stable_assumption_count"] += 1
            f["occurrence_count"] += occurrence_count
            f["decay_score_max"] = max(f["decay_score_max"], decay_score)
            f["states"][state] = f["states"].get(state, 0) + 1
            f["replay_validated_drift_count"] += drift_count
            f["false_positive_count"] += fp
            f["accepted_risk_count"] += accepted

        decay_nodes.sort(
            key=lambda r: (
                int(r.get("decay_score") or 0),
                int(r.get("occurrence_count") or 0),
                int(r.get("drift_detected_count") or 0),
            ),
            reverse=True,
        )
        state_counts: dict[str, int] = {}
        signal_counts: dict[str, int] = {}
        for row in decay_nodes:
            state_counts[row["decay_state"]] = state_counts.get(row["decay_state"], 0) + 1
            for sig in row.get("decay_signals") or []:
                signal_counts[sig] = signal_counts.get(sig, 0) + 1
        return {
            "decay_kind": "semzero_assumption_decay_lite_v1_25",
            "scope": "core_data_only",
            "receipt_count": len(receipts),
            "stable_assumption_count": len(decay_nodes),
            "state_counts": dict(sorted(state_counts.items())),
            "signal_counts": dict(sorted(signal_counts.items())),
            "family_decay": sorted(
                family_rollup.values(), key=lambda r: r["decay_score_max"], reverse=True
            ),
            "top_decay_assumptions": decay_nodes[:25],
            "review_queue": [
                r
                for r in decay_nodes
                if r["decay_state"]
                in {
                    "review_expired_exception",
                    "decaying_needs_feedback",
                    "needs_review",
                    "accepted_risk_debt",
                }
            ][:25],
            "guardrail": "Assumption Decay Tracking Lite is advisory-only. It flags recurring fragility, stale evidence, accepted-risk debt, and noisy findings; it does not block or auto-change policy.",
        }

    def save_json(self, path: str | Path) -> dict[str, Any]:
        payload = self.build()
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        return payload

    def save_markdown(self, path: str | Path) -> dict[str, Any]:
        payload = self.build()
        lines = ["# SemZero Assumption Decay Tracking Lite", "", payload["guardrail"], ""]
        lines += [
            f"- Receipts: {payload['receipt_count']}",
            f"- Stable assumptions: {payload['stable_assumption_count']}",
            "",
        ]
        lines += ["## Decay states", ""]
        for state, count in payload["state_counts"].items():
            lines.append(f"- `{state}`: {count}")
        lines += ["", "## Signals", ""]
        for signal, count in payload["signal_counts"].items():
            lines.append(f"- `{signal}`: {count}")
        lines += ["", "## Top decaying assumptions", ""]
        for row in payload["top_decay_assumptions"][:10]:
            lines.append(
                f"- `{row.get('stable_id')}` · {row.get('family')} · state={row.get('decay_state')} · score={row.get('decay_score')} · occurrences={row.get('occurrence_count')} · replay_drift={row.get('drift_detected_count')} · recommendation={row.get('recommendation')}"
            )
        lines += ["", "## Review queue", ""]
        for row in payload["review_queue"][:10]:
            lines.append(
                f"- `{row.get('stable_id')}` · {row.get('decay_state')} · {row.get('recommendation')}"
            )
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return payload
