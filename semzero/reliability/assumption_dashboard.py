from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from semzero.reliability.assumption_exceptions import (
    load_exceptions,
    summarize_exceptions,
    match_exception,
)
from semzero.reliability.assumption_freshness import receipt_freshness, finding_review_state

ASSUMPTION_RECEIPT_KINDS = {
    "dbt_assumption_gate_v1",
    "dbt_assumption_gate_v1_1",
    "dbt_assumption_gate_v1_2",
    "dbt_assumption_gate_v1_3",
    "dbt_assumption_gate_v1_4",
    "dbt_assumption_gate_v1_5",
    "dbt_assumption_gate_v1_6",
    "dbt_assumption_gate_v1_7",
    "dbt_assumption_gate_v1_8",
    "dbt_assumption_gate_v1_9",
    "dbt_assumption_gate_v1_10",
    "dbt_assumption_gate_v1_11",
    "dbt_assumption_gate_v1_12",
    "dbt_assumption_gate_v1_13",
    "dbt_assumption_gate_v1_14",
    "dbt_assumption_gate_v1_15",
    "dbt_assumption_gate_v1_16",
    "dbt_assumption_gate_v1_17",
    "dbt_assumption_gate_v1_18",
    "dbt_assumption_gate_v1_19",
    "dbt_assumption_gate_v1_20",
    "dbt_assumption_gate_v1_21",
    "dbt_assumption_gate_v1_22",
    "dbt_assumption_gate_v1_23",
    "dbt_assumption_gate_v1_24",
    "dbt_assumption_gate_v1_25",
}
REVIEW_VERDICTS = {"REQUIRE_REVIEW", "BLOCK"}
AGREE_OR_VALUE_DISPOSITIONS = {"agree", "fixed"}
FIXED_DISPOSITIONS = {"fixed"}
ACCEPTED_RISK_DISPOSITIONS = {"accepted_risk"}
SEVERITY_ORDER = {"unknown": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


@dataclass(slots=True)
class AssumptionDashboard:
    receipt_dir: str = "data"
    feedback_file: str = ""
    exceptions_file: str = ""

    def build(self) -> dict[str, Any]:
        receipts = list(self._load_receipts())
        feedback_records = self._load_feedback_records()
        exception_records = load_exceptions(
            self.exceptions_file or Path(self.receipt_dir) / "assumption_exceptions.jsonl"
        )
        exception_summary = summarize_exceptions(exception_records)
        feedback_summary = self._feedback_summary_from_records(feedback_records)
        feedback_index = self._index_feedback(feedback_records)

        family_counts: dict[str, int] = {}
        severity_counts: dict[str, int] = {}
        domain_counts: dict[str, int] = {}
        adapter_counts: dict[str, int] = {}
        verdict_counts: dict[str, int] = {}
        recurring_family_receipts: dict[str, set[str]] = {}
        family_cost_exposure: dict[str, float] = {}
        family_feedback: dict[str, dict[str, int]] = {}
        severity_cost_exposure: dict[str, float] = {}
        daily_receipt_counts: dict[str, int] = {}
        daily_finding_counts: dict[str, int] = {}
        daily_feedback_counts: dict[str, int] = {}
        blast_nodes: dict[str, dict[str, Any]] = {}
        cost_total = 0.0
        cost_monthly_total = 0.0
        has_cost = False
        has_monthly_cost = False
        estimated_validated_cost = 0.0
        estimated_validated_monthly_cost = 0.0
        estimated_avoided_cost = 0.0
        estimated_avoided_monthly_cost = 0.0
        estimated_accepted_risk_cost = 0.0
        estimated_accepted_risk_monthly_cost = 0.0
        fixed_finding_count = 0
        accepted_risk_finding_count = 0
        findings_with_feedback = 0
        top_findings: list[dict[str, Any]] = []
        stable_findings: dict[str, dict[str, Any]] = {}
        business_severity_counts: dict[str, int] = {}
        control_coverage_counts: dict[str, int] = {}
        exception_match_counts: dict[str, int] = {}
        active_exception_finding_count = 0
        expired_exception_finding_count = 0
        freshness_counts: dict[str, int] = {}
        stale_receipt_count = 0
        review_due_receipt_count = 0
        high_risk_unreviewed_count = 0
        stale_unreviewed_high_risk_count = 0
        expired_exception_high_risk_count = 0
        freshness_review_items: list[dict[str, Any]] = []
        replay_ran_count = 0
        replay_drift_count = 0
        replay_no_drift_count = 0
        replay_not_run_count = 0
        replay_low_fidelity_count = 0
        replay_status_counts: dict[str, int] = {}
        replay_fidelity_level_counts: dict[str, int] = {}
        replay_fidelity_total = 0.0
        replay_fidelity_scored_count = 0
        replay_family_stats: dict[str, dict[str, Any]] = {}
        replay_review_items: list[dict[str, Any]] = []

        for row in feedback_records:
            day = _day(row.get("created_at"))
            if day:
                daily_feedback_counts[day] = daily_feedback_counts.get(day, 0) + 1

        for receipt in receipts:
            receipt_key = self._receipt_key(receipt)
            receipt_fresh = receipt_freshness(receipt)
            fstate = str(receipt_fresh.get("state") or "unknown")
            freshness_counts[fstate] = freshness_counts.get(fstate, 0) + 1
            if fstate == "stale":
                stale_receipt_count += 1
            elif fstate == "review_due":
                review_due_receipt_count += 1
            receipt_day = _day(receipt.get("generated_at"))
            if receipt_day:
                daily_receipt_counts[receipt_day] = daily_receipt_counts.get(receipt_day, 0) + 1
            verdict = str(receipt.get("verdict") or "ALLOW")
            verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1
            adapter = str(receipt.get("adapter") or "unknown")
            domain = str(receipt.get("domain") or "unknown")
            adapter_counts[adapter] = adapter_counts.get(adapter, 0) + 1
            domain_counts[domain] = domain_counts.get(domain, 0) + 1

            for finding in receipt.get("findings") or []:
                if not isinstance(finding, dict):
                    continue
                family = str(finding.get("family") or "unknown")
                severity = str(finding.get("severity") or "unknown")
                family_counts[family] = family_counts.get(family, 0) + 1
                severity_counts[severity] = severity_counts.get(severity, 0) + 1
                biz = finding.get("business_impact") or {}
                biz_sev = str(biz.get("highest_business_severity") or "UNKNOWN")
                business_severity_counts[biz_sev] = business_severity_counts.get(biz_sev, 0) + 1
                control = finding.get("control_coverage") or {}
                control_status = str(control.get("status") or "unknown")
                control_coverage_counts[control_status] = (
                    control_coverage_counts.get(control_status, 0) + 1
                )
                validation_replay = finding.get("validation_replay") or {}
                replay_fidelity = finding.get("replay_fidelity") or {}
                replay_status = str(
                    validation_replay.get("status")
                    or ("not_run" if not validation_replay.get("replay_ran") else "unknown")
                )
                replay_status_counts[replay_status] = replay_status_counts.get(replay_status, 0) + 1
                replay_ran = bool(validation_replay.get("replay_ran"))
                if replay_ran:
                    replay_ran_count += 1
                else:
                    replay_not_run_count += 1
                if replay_status == "drift_detected":
                    replay_drift_count += 1
                elif replay_status in {"no_drift_detected", "no_drift"}:
                    replay_no_drift_count += 1
                fidelity_level = str(replay_fidelity.get("level") or "unknown")
                replay_fidelity_level_counts[fidelity_level] = (
                    replay_fidelity_level_counts.get(fidelity_level, 0) + 1
                )
                fidelity_score = replay_fidelity.get("score")
                if isinstance(fidelity_score, (int, float)):
                    replay_fidelity_total += float(fidelity_score)
                    replay_fidelity_scored_count += 1
                    if float(fidelity_score) < 0.5:
                        replay_low_fidelity_count += 1
                elif not replay_ran:
                    replay_low_fidelity_count += 1
                replay_bucket = replay_family_stats.setdefault(
                    family,
                    {
                        "family": family,
                        "finding_count": 0,
                        "replay_ran_count": 0,
                        "drift_detected_count": 0,
                        "not_run_count": 0,
                        "low_fidelity_count": 0,
                        "fidelity_scores": [],
                    },
                )
                replay_bucket["finding_count"] += 1
                if replay_ran:
                    replay_bucket["replay_ran_count"] += 1
                else:
                    replay_bucket["not_run_count"] += 1
                if replay_status == "drift_detected":
                    replay_bucket["drift_detected_count"] += 1
                if isinstance(fidelity_score, (int, float)):
                    replay_bucket["fidelity_scores"].append(float(fidelity_score))
                    if float(fidelity_score) < 0.5:
                        replay_bucket["low_fidelity_count"] += 1
                elif not replay_ran:
                    replay_bucket["low_fidelity_count"] += 1
                exception = finding.get("exception") or match_exception(
                    finding, receipt_key, exception_records
                )
                exception_state = str(exception.get("state") or "none")
                exception_match_counts[exception_state] = (
                    exception_match_counts.get(exception_state, 0) + 1
                )
                if exception_state == "active_exception":
                    active_exception_finding_count += 1
                elif exception_state == "expired_exception":
                    expired_exception_finding_count += 1
                recurring_family_receipts.setdefault(family, set()).add(receipt_key)
                if receipt_day:
                    daily_finding_counts[receipt_day] = daily_finding_counts.get(receipt_day, 0) + 1

                cost = _finding_cost(finding)
                monthly_cost = _finding_monthly_cost(finding)
                if cost is not None:
                    has_cost = True
                    cost_total += cost
                    family_cost_exposure[family] = family_cost_exposure.get(family, 0.0) + cost
                    severity_cost_exposure[severity] = (
                        severity_cost_exposure.get(severity, 0.0) + cost
                    )
                if monthly_cost is not None:
                    has_monthly_cost = True
                    cost_monthly_total += monthly_cost

                stable_id = str(finding.get("stable_id") or finding.get("id") or "")
                legacy_id = str(finding.get("legacy_id") or "")
                finding_feedback = self._feedback_for_finding(feedback_index, receipt_key, finding)
                review_state = finding_review_state(finding, receipt, finding_feedback, exception)
                if review_state.get("needs_review"):
                    high_risk_unreviewed_count += 1
                if review_state.get("stale_unreviewed"):
                    stale_unreviewed_high_risk_count += 1
                if exception_state == "expired_exception" and review_state.get("high_risk"):
                    expired_exception_high_risk_count += 1
                if (
                    replay_status == "drift_detected"
                    or not replay_ran
                    or fidelity_level.startswith("low")
                    or (isinstance(fidelity_score, (int, float)) and float(fidelity_score) < 0.5)
                ):
                    replay_review_items.append(
                        {
                            "stable_id": stable_id,
                            "legacy_id": legacy_id,
                            "family": family,
                            "severity": severity,
                            "risk_score": finding.get("risk_score"),
                            "replay_status": replay_status,
                            "replay_ran": replay_ran,
                            "fidelity_score": fidelity_score,
                            "fidelity_level": fidelity_level,
                            "validation_summary": validation_replay.get("summary"),
                            "source": finding.get("source")
                            or {
                                "unique_id": finding.get("source_resource"),
                                "path": finding.get("source_path"),
                            },
                            "business_impact": finding.get("business_impact") or {},
                        }
                    )
                if review_state.get("needs_review") or exception_state == "expired_exception":
                    freshness_review_items.append(
                        {
                            "stable_id": stable_id,
                            "legacy_id": legacy_id,
                            "family": family,
                            "severity": severity,
                            "risk_score": finding.get("risk_score"),
                            "source": finding.get("source")
                            or {
                                "unique_id": finding.get("source_resource"),
                                "path": finding.get("source_path"),
                            },
                            "review_state": review_state,
                            "exception_state": exception_state,
                            "business_impact": finding.get("business_impact") or {},
                        }
                    )
                stable_bucket = stable_findings.setdefault(
                    stable_id or legacy_id or f"{family}:{finding.get('source_resource')}",
                    {
                        "stable_id": stable_id,
                        "legacy_id": legacy_id,
                        "family": family,
                        "source": finding.get("source")
                        or {
                            "unique_id": finding.get("source_resource"),
                            "path": finding.get("source_path"),
                        },
                        "occurrence_count": 0,
                        "receipt_count": 0,
                        "receipt_keys": set(),
                        "feedback_count": 0,
                        "dispositions": {},
                        "risk_score_max": 0,
                        "severity_max": severity,
                        "cost_exposure_usd_per_run": 0.0,
                        "cost_exposure_usd_per_month": 0.0,
                        "confidence_values": {},
                    },
                )
                stable_bucket["occurrence_count"] += 1
                stable_bucket["receipt_keys"].add(receipt_key)
                stable_bucket["receipt_count"] = len(stable_bucket["receipt_keys"])
                stable_bucket["risk_score_max"] = max(
                    int(stable_bucket.get("risk_score_max") or 0),
                    int(finding.get("risk_score") or 0),
                )
                if SEVERITY_ORDER.get(severity, 0) > SEVERITY_ORDER.get(
                    str(stable_bucket.get("severity_max") or "unknown"), 0
                ):
                    stable_bucket["severity_max"] = severity
                if cost is not None:
                    stable_bucket["cost_exposure_usd_per_run"] += cost
                if monthly_cost is not None:
                    stable_bucket["cost_exposure_usd_per_month"] += monthly_cost
                conf = str(finding.get("confidence") or "unknown")
                stable_bucket["confidence_values"][conf] = (
                    stable_bucket["confidence_values"].get(conf, 0) + 1
                )
                if finding_feedback:
                    findings_with_feedback += 1
                    family_bucket = family_feedback.setdefault(
                        family,
                        {
                            "agree": 0,
                            "disagree": 0,
                            "false_positive": 0,
                            "fixed": 0,
                            "accepted_risk": 0,
                            "needs_context": 0,
                        },
                    )
                    dispositions = {
                        str(row.get("disposition") or "needs_context") for row in finding_feedback
                    }
                    for disposition in dispositions:
                        if disposition in family_bucket:
                            family_bucket[disposition] += 1
                        stable_bucket["dispositions"][disposition] = (
                            stable_bucket["dispositions"].get(disposition, 0) + 1
                        )
                    stable_bucket["feedback_count"] += len(finding_feedback)
                    if cost is not None and dispositions & AGREE_OR_VALUE_DISPOSITIONS:
                        estimated_validated_cost += cost
                    if monthly_cost is not None and dispositions & AGREE_OR_VALUE_DISPOSITIONS:
                        estimated_validated_monthly_cost += monthly_cost
                    if cost is not None and dispositions & FIXED_DISPOSITIONS:
                        estimated_avoided_cost += cost
                    if monthly_cost is not None and dispositions & FIXED_DISPOSITIONS:
                        estimated_avoided_monthly_cost += monthly_cost
                    if cost is not None and dispositions & ACCEPTED_RISK_DISPOSITIONS:
                        estimated_accepted_risk_cost += cost
                    if monthly_cost is not None and dispositions & ACCEPTED_RISK_DISPOSITIONS:
                        estimated_accepted_risk_monthly_cost += monthly_cost
                    if dispositions & FIXED_DISPOSITIONS:
                        fixed_finding_count += 1
                    if dispositions & ACCEPTED_RISK_DISPOSITIONS:
                        accepted_risk_finding_count += 1

                for node in finding.get("blast_radius") or []:
                    if not isinstance(node, dict):
                        continue
                    key = str(node.get("unique_id") or node.get("name") or node)
                    if not key:
                        continue
                    existing = blast_nodes.setdefault(
                        key,
                        {
                            "node": node,
                            "finding_count": 0,
                            "families": {},
                            "cost_exposure_usd_per_run": 0.0,
                            "cost_exposure_usd_per_month": 0.0,
                        },
                    )
                    existing["finding_count"] += 1
                    existing["families"][family] = existing["families"].get(family, 0) + 1
                    if cost is not None:
                        existing["cost_exposure_usd_per_run"] += cost
                    if monthly_cost is not None:
                        existing["cost_exposure_usd_per_month"] += monthly_cost

                top_findings.append(
                    {
                        "id": finding.get("id"),
                        "stable_id": finding.get("stable_id") or finding.get("id"),
                        "legacy_id": finding.get("legacy_id"),
                        "fingerprint": finding.get("fingerprint"),
                        "family": family,
                        "severity": severity,
                        "domain": finding.get("domain", domain),
                        "adapter": finding.get("adapter", adapter),
                        "assumption": finding.get("assumption"),
                        "trigger": finding.get("trigger"),
                        "source": finding.get("source")
                        or {
                            "unique_id": finding.get("source_resource"),
                            "path": finding.get("source_path"),
                        },
                        "blast_radius_count": len(finding.get("blast_radius") or []),
                        "recommended_check": finding.get("recommended_check"),
                        "confidence": finding.get("confidence"),
                        "risk_score": finding.get("risk_score"),
                        "cost_exposure_usd_per_run": cost,
                        "cost_exposure_usd_per_month": monthly_cost,
                        "feedback_count": len(finding_feedback),
                        "feedback_dispositions": sorted(
                            {
                                str(row.get("disposition") or "needs_context")
                                for row in finding_feedback
                            }
                        ),
                        "validation_replay_status": replay_status,
                        "validation_replay_ran": replay_ran,
                        "replay_fidelity_score": fidelity_score,
                        "replay_fidelity_level": fidelity_level,
                    }
                )

        review_count = sum(v for k, v in verdict_counts.items() if k in REVIEW_VERDICTS)
        run_count = len(receipts)
        top_findings = sorted(
            top_findings,
            key=lambda row: (
                -(int(row.get("risk_score") or 0)),
                -(row.get("blast_radius_count") or 0),
                str(row.get("family") or ""),
            ),
        )[:10]

        roi = {
            "estimated_cost_exposure_usd_per_run": round(cost_total, 2) if has_cost else None,
            "estimated_cost_exposure_usd_per_month": round(cost_monthly_total, 2)
            if has_monthly_cost
            else None,
            "estimated_validated_cost_exposure_usd_per_run": round(estimated_validated_cost, 2)
            if has_cost
            else None,
            "estimated_validated_cost_exposure_usd_per_month": round(
                estimated_validated_monthly_cost, 2
            )
            if has_monthly_cost
            else None,
            "estimated_avoided_cost_usd_per_run": round(estimated_avoided_cost, 2)
            if has_cost
            else None,
            "estimated_avoided_cost_usd_per_month": round(estimated_avoided_monthly_cost, 2)
            if has_monthly_cost
            else None,
            "estimated_accepted_risk_cost_usd_per_run": round(estimated_accepted_risk_cost, 2)
            if has_cost
            else None,
            "estimated_accepted_risk_cost_usd_per_month": round(
                estimated_accepted_risk_monthly_cost, 2
            )
            if has_monthly_cost
            else None,
            "fixed_finding_count": fixed_finding_count,
            "accepted_risk_finding_count": accepted_risk_finding_count,
            "findings_with_feedback": findings_with_feedback,
            "cost_method_note": "Directional only: sums finding-level rough cost estimates from Assumption Gate receipts; avoided cost is counted only for findings marked fixed.",
        }
        recurring = [
            {
                "family": family,
                "finding_count": family_counts.get(family, 0),
                "receipt_count": len(receipts_for_family),
                "cost_exposure_usd_per_run": round(family_cost_exposure.get(family, 0.0), 2)
                if family in family_cost_exposure
                else None,
                "feedback": family_feedback.get(family, {}),
            }
            for family, receipts_for_family in recurring_family_receipts.items()
        ]
        recurring.sort(
            key=lambda row: (
                -(row["finding_count"] or 0),
                -(row["receipt_count"] or 0),
                row["family"],
            )
        )

        stable_rows = self._stable_finding_rows(stable_findings)
        calibration_readiness = self._calibration_readiness(
            run_count=run_count,
            feedback_summary=feedback_summary,
            policy_recommendations=None,
            stable_rows=stable_rows,
            roi=roi,
        )

        policy_recommendations = self._policy_recommendations(
            run_count=run_count,
            family_counts=family_counts,
            family_feedback=family_feedback,
            family_cost_exposure=family_cost_exposure,
            recurring=recurring,
            feedback_summary=feedback_summary,
            roi=roi,
            stable_rows=stable_rows,
        )
        calibration_readiness = self._calibration_readiness(
            run_count=run_count,
            feedback_summary=feedback_summary,
            policy_recommendations=policy_recommendations,
            stable_rows=stable_rows,
            roi=roi,
        )
        replay_family_rows = []
        for fam, row in replay_family_stats.items():
            scores = row.pop("fidelity_scores", [])
            row = dict(row)
            row["average_fidelity_score"] = round(sum(scores) / len(scores), 4) if scores else None
            row["replay_coverage_rate"] = round(
                row.get("replay_ran_count", 0) / max(row.get("finding_count", 0), 1), 4
            )
            row["drift_detection_rate"] = (
                round(
                    row.get("drift_detected_count", 0) / max(row.get("replay_ran_count", 0), 1), 4
                )
                if row.get("replay_ran_count")
                else None
            )
            replay_family_rows.append(row)
        replay_family_rows.sort(
            key=lambda r: (
                -(r.get("drift_detected_count") or 0),
                -(r.get("finding_count") or 0),
                str(r.get("family")),
            )
        )
        replay_aware = {
            "kind": "semzero_replay_aware_dashboard_v1",
            "finding_count": sum(family_counts.values()),
            "replay_ran_count": replay_ran_count,
            "replay_not_run_count": replay_not_run_count,
            "drift_detected_count": replay_drift_count,
            "no_drift_detected_count": replay_no_drift_count,
            "low_fidelity_count": replay_low_fidelity_count,
            "replay_coverage_rate": round(replay_ran_count / max(sum(family_counts.values()), 1), 4)
            if family_counts
            else 0.0,
            "drift_detection_rate": round(replay_drift_count / max(replay_ran_count, 1), 4)
            if replay_ran_count
            else None,
            "average_fidelity_score": round(replay_fidelity_total / replay_fidelity_scored_count, 4)
            if replay_fidelity_scored_count
            else None,
            "status_counts": dict(sorted(replay_status_counts.items())),
            "fidelity_level_counts": dict(sorted(replay_fidelity_level_counts.items())),
            "family_replay": replay_family_rows[:10],
            "review_queue": sorted(
                replay_review_items,
                key=lambda row: (
                    row.get("replay_status") != "drift_detected",
                    -(int(row.get("risk_score") or 0)),
                    row.get("family") or "",
                ),
            )[:20],
            "trust_note": self._replay_trust_note(
                replay_ran_count,
                replay_drift_count,
                replay_not_run_count,
                replay_low_fidelity_count,
                replay_fidelity_scored_count,
            ),
            "guardrail": "Replay-aware dashboarding is advisory-only. Replay Lite validates targeted assumptions from supplied fixtures/samples; it is not full warehouse replay.",
        }

        return {
            "dashboard_kind": "semzero_assumption_dashboard_v1_25",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "receipt_dir": str(self.receipt_dir),
            "run_count": run_count,
            "assumption_finding_count": sum(family_counts.values()),
            "would_require_review_count": review_count,
            "would_require_review_rate": round(review_count / run_count, 4) if run_count else 0.0,
            "family_counts": dict(
                sorted(family_counts.items(), key=lambda item: (-item[1], item[0]))
            ),
            "severity_counts": severity_counts,
            "business_severity_counts": business_severity_counts,
            "control_coverage_counts": control_coverage_counts,
            "exception_match_counts": exception_match_counts,
            "exceptions": {
                **exception_summary,
                "active_exception_finding_count": active_exception_finding_count,
                "expired_exception_finding_count": expired_exception_finding_count,
                "expired_exception_high_risk_count": expired_exception_high_risk_count,
            },
            "freshness": {
                "kind": "semzero_evidence_freshness_v1",
                "receipt_freshness_counts": dict(sorted(freshness_counts.items())),
                "stale_receipt_count": stale_receipt_count,
                "review_due_receipt_count": review_due_receipt_count,
                "high_risk_unreviewed_count": high_risk_unreviewed_count,
                "stale_unreviewed_high_risk_count": stale_unreviewed_high_risk_count,
                "expired_exception_high_risk_count": expired_exception_high_risk_count,
                "review_queue": sorted(
                    freshness_review_items,
                    key=lambda row: (
                        row.get("review_state", {}).get("stale_unreviewed") is not True,
                        -(int(row.get("risk_score") or 0)),
                        row.get("family") or "",
                    ),
                )[:20],
                "guardrail": "Freshness is advisory. Stale receipts and expired exceptions should be reviewed before policy promotion; they do not create hard blocks.",
            },
            "replay_aware": replay_aware,
            "domain_counts": domain_counts,
            "adapter_counts": adapter_counts,
            "verdict_counts": verdict_counts,
            "estimated_extra_cost_per_run_usd_total": round(cost_total, 2) if has_cost else None,
            "estimated_extra_cost_per_month_usd_total": round(cost_monthly_total, 2)
            if has_monthly_cost
            else None,
            "roi": roi,
            "recurring_assumption_families": recurring[:10],
            "recurring_stable_findings": stable_rows[:10],
            "stable_finding_count": len(stable_rows),
            "calibration_readiness": calibration_readiness,
            "policy_recommendations": policy_recommendations,
            "trend": {
                "daily_receipt_counts": dict(sorted(daily_receipt_counts.items())),
                "daily_finding_counts": dict(sorted(daily_finding_counts.items())),
                "daily_feedback_counts": dict(sorted(daily_feedback_counts.items())),
            },
            "top_blast_radius_nodes": self._sorted_blast_nodes(blast_nodes),
            "top_findings": top_findings,
            "feedback": feedback_summary,
            "developer_agreement_rate": feedback_summary.get("developer_agreement_rate"),
            "developer_disagreement_rate": feedback_summary.get("developer_disagreement_rate"),
            "trust_note": self._trust_note(run_count, feedback_summary, roi),
        }

    def save_json(self, output: str | Path) -> dict[str, Any]:
        payload = self.build()
        p = Path(output)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        return payload

    def save_markdown(self, output: str | Path) -> str:
        payload = self.build()
        roi = payload.get("roi") or {}
        lines = [
            "# SemZero Assumption Dashboard",
            "",
            f"Generated: `{payload['generated_at']}`",
            "",
            f"- Runs scanned: **{payload['run_count']}**",
            f"- Assumption findings: **{payload['assumption_finding_count']}**",
            f"- Would require review: **{payload['would_require_review_count']}**",
        ]
        if roi.get("estimated_cost_exposure_usd_per_run") is not None:
            monthly = roi.get("estimated_cost_exposure_usd_per_month")
            suffix = f" / **${monthly}/month**" if monthly is not None else ""
            lines.append(
                f"- Rough cost exposure surfaced: **${roi['estimated_cost_exposure_usd_per_run']}/run**{suffix}"
            )
        if roi.get("estimated_avoided_cost_usd_per_run") is not None:
            monthly = roi.get("estimated_avoided_cost_usd_per_month")
            suffix = f" / **${monthly}/month**" if monthly is not None else ""
            lines.append(
                f"- Rough avoided cost from fixed findings: **${roi['estimated_avoided_cost_usd_per_run']}/run**{suffix}"
            )
        lines.append(f"- Fixed findings: **{roi.get('fixed_finding_count', 0)}**")
        lines.append(f"- Accepted-risk findings: **{roi.get('accepted_risk_finding_count', 0)}**")
        if payload.get("business_severity_counts"):
            lines.append(
                f"- Business-critical findings: **{payload.get('business_severity_counts')}**"
            )
        if payload.get("control_coverage_counts"):
            lines.append(f"- Control coverage: **{payload.get('control_coverage_counts')}**")
        freshness = payload.get("freshness") or {}
        if freshness:
            lines.append(
                f"- Stale/review-due receipts: **{freshness.get('stale_receipt_count', 0)} stale**, **{freshness.get('review_due_receipt_count', 0)} review due**"
            )
            lines.append(
                f"- High-risk findings needing review: **{freshness.get('high_risk_unreviewed_count', 0)}**"
            )
        feedback = payload.get("feedback") or {}
        lines.append(f"- Developer feedback records: **{feedback.get('feedback_count', 0)}**")
        if feedback.get("developer_agreement_rate") is not None:
            lines.append(
                f"- Developer agreement rate: **{round(feedback['developer_agreement_rate'] * 100, 1)}%**"
            )
        lines += ["", "## ROI / value signals", ""]
        lines.append(roi.get("cost_method_note") or "Directional cost exposure only.")
        if roi.get("estimated_validated_cost_exposure_usd_per_run") is not None:
            monthly = roi.get("estimated_validated_cost_exposure_usd_per_month")
            suffix = f" / **${monthly}/month**" if monthly is not None else ""
            lines.append(
                f"- Validated cost exposure: **${roi['estimated_validated_cost_exposure_usd_per_run']}/run**{suffix}"
            )
        if roi.get("estimated_accepted_risk_cost_usd_per_run") is not None:
            monthly = roi.get("estimated_accepted_risk_cost_usd_per_month")
            suffix = f" / **${monthly}/month**" if monthly is not None else ""
            lines.append(
                f"- Accepted-risk cost exposure: **${roi['estimated_accepted_risk_cost_usd_per_run']}/run**{suffix}"
            )
        lines += ["", "## Top recurring assumption families", ""]
        recurring = payload.get("recurring_assumption_families") or []
        if recurring:
            for row in recurring:
                cost = row.get("cost_exposure_usd_per_run")
                cost_note = f", ${cost}/run surfaced" if cost is not None else ""
                lines.append(
                    f"- `{row['family']}`: {row['finding_count']} finding(s) across {row['receipt_count']} receipt(s){cost_note}"
                )
        else:
            lines.append("No recurring assumption families found.")
        lines += ["", "## Most-exposed blast-radius nodes", ""]
        for row in payload.get("top_blast_radius_nodes") or []:
            node = row["node"]
            cost = row.get("cost_exposure_usd_per_run")
            cost_note = f", ${cost}/run" if cost else ""
            lines.append(
                f"- `{node.get('name')}` ({node.get('node_type') or node.get('type')}, {node.get('domain')}): {row['finding_count']} finding(s){cost_note}"
            )
        if not payload.get("top_blast_radius_nodes"):
            lines.append("No downstream blast-radius nodes found.")
        lines += ["", "## Stable recurring findings", ""]
        stable_rows = payload.get("recurring_stable_findings") or []
        if stable_rows:
            for row in stable_rows[:8]:
                source = row.get("source") or {}
                lines.append(
                    f"- `{row.get('stable_id')}` `{row.get('family')}`: {row.get('occurrence_count')} occurrence(s), {row.get('feedback_count')} feedback record(s), source `{source.get('name') or source.get('unique_id')}`"
                )
        else:
            lines.append("No stable recurring finding IDs yet.")
        lines += ["", "## Calibration readiness", ""]
        readiness = payload.get("calibration_readiness") or {}
        lines.append(f"- State: **{readiness.get('state', 'unknown')}**")
        lines.append(f"- Reason: {readiness.get('reason', '')}")
        lines += ["", "## Policy calibration recommendations", ""]
        policy = payload.get("policy_recommendations") or {}
        lines.append(policy.get("summary") or "No policy recommendation available yet.")
        for row in policy.get("family_recommendations") or []:
            lines.append(
                f"- `{row['family']}` → **{row['recommended_policy_action']}**: {row['reason']}"
            )
        if not policy.get("family_recommendations"):
            lines.append("No family-level recommendation yet.")
        lines += ["", "## Feedback", ""]
        if feedback.get("feedback_count", 0):
            for disposition, count in (feedback.get("disposition_counts") or {}).items():
                lines.append(f"- `{disposition}`: {count}")
        else:
            lines.append("No developer feedback records found yet.")
        freshness = payload.get("freshness") or {}
        if freshness:
            lines += ["", "## Freshness / stale-risk review", ""]
            lines.append(f"- Receipt freshness: `{freshness.get('receipt_freshness_counts', {})}`")
            lines.append(
                f"- High-risk unreviewed findings: **{freshness.get('high_risk_unreviewed_count', 0)}**"
            )
            lines.append(
                f"- Stale high-risk unreviewed findings: **{freshness.get('stale_unreviewed_high_risk_count', 0)}**"
            )
            lines.append(
                f"- Expired-exception high-risk findings: **{freshness.get('expired_exception_high_risk_count', 0)}**"
            )
            queue = freshness.get("review_queue") or []
            if queue:
                lines.append("")
                lines.append("Review queue:")
                for row in queue[:8]:
                    source = row.get("source") or {}
                    state = row.get("review_state") or {}
                    lines.append(
                        f"- `{row.get('stable_id') or row.get('legacy_id')}` `{row.get('family')}` source `{source.get('name') or source.get('unique_id')}` — {state.get('reason')}"
                    )
            else:
                lines.append("No stale high-risk review queue items found.")
        trend = payload.get("trend") or {}
        if trend.get("daily_finding_counts"):
            lines += ["", "## Daily finding trend", ""]
            for day, count in trend["daily_finding_counts"].items():
                lines.append(f"- `{day}`: {count} finding(s)")
        lines += ["", "## Trust note", "", payload.get("trust_note", "")]
        text = "\n".join(lines) + "\n"
        p = Path(output)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return text

    @staticmethod
    def _policy_recommendations(
        *,
        run_count: int,
        family_counts: dict[str, int],
        family_feedback: dict[str, dict[str, int]],
        family_cost_exposure: dict[str, float],
        recurring: list[dict[str, Any]],
        feedback_summary: dict[str, Any],
        roi: dict[str, Any],
        stable_rows: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Turn shadow feedback into policy-tuning advice without changing policy.

        This is intentionally advisory. SemZero should not silently promote a family
        from shadow to require-review based on a dashboard build. Teams use this to
        decide which policy thresholds deserve human review.
        """
        family_rows: list[dict[str, Any]] = []
        require_review_candidates: list[str] = []
        advisory_candidates: list[str] = []
        suppress_or_lower_candidates: list[str] = []
        accepted_risk_review_candidates: list[str] = []
        keep_shadow_candidates: list[str] = []

        total_feedback = int(feedback_summary.get("feedback_count") or 0)
        stable_rows = stable_rows or []
        calibrated_stable_count = sum(
            1 for row in stable_rows if int(row.get("feedback_count") or 0) >= 2
        )
        for row in recurring:
            family = str(row.get("family") or "unknown")
            finding_count = int(family_counts.get(family, row.get("finding_count") or 0) or 0)
            feedback = family_feedback.get(family, {}) or {}
            agree = int(feedback.get("agree") or 0)
            fixed = int(feedback.get("fixed") or 0)
            disagree = int(feedback.get("disagree") or 0)
            false_positive = int(feedback.get("false_positive") or 0)
            accepted = int(feedback.get("accepted_risk") or 0)
            needs_context = int(feedback.get("needs_context") or 0)
            family_feedback_count = (
                agree + fixed + disagree + false_positive + accepted + needs_context
            )
            positive = agree + fixed
            negative = disagree + false_positive
            agreement_rate = (
                round(positive / family_feedback_count, 4) if family_feedback_count else None
            )
            false_positive_rate = (
                round(false_positive / family_feedback_count, 4) if family_feedback_count else None
            )
            cost = family_cost_exposure.get(family)

            if (
                false_positive_rate is not None
                and false_positive_rate >= 0.4
                and family_feedback_count >= 2
            ):
                action = "lower_severity_or_suppress_candidate"
                reason = "Developer feedback indicates a high false-positive rate for this family."
                suppress_or_lower_candidates.append(family)
            elif (
                fixed >= 1
                and cost
                and cost > 0
                and family_feedback_count >= 3
                and (false_positive_rate or 0) <= 0.25
            ):
                action = "require_review_candidate"
                reason = "Multiple feedback signals include fixed directional-cost findings with low false-positive feedback; consider require-review after human policy review."
                require_review_candidates.append(family)
            elif fixed >= 1 and cost and cost > 0:
                action = "advisory_candidate"
                reason = "A fixed finding with directional cost exposure exists, but sample size is still too small for stricter enforcement."
                advisory_candidates.append(family)
            elif family_feedback_count < 2 or run_count < 10:
                action = "keep_shadow_collect_feedback"
                reason = "Calibration sample is still small for this family."
                keep_shadow_candidates.append(family)
            elif false_positive_rate is not None and false_positive_rate >= 0.4:
                action = "lower_severity_or_suppress_candidate"
                reason = "Developer feedback indicates a high false-positive rate for this family."
                suppress_or_lower_candidates.append(family)
            elif accepted >= 2 and accepted >= positive:
                action = "accepted_risk_policy_review_candidate"
                reason = "This family is repeatedly accepted as risk; review whether policy should require explicit owner sign-off."
                accepted_risk_review_candidates.append(family)
            elif (
                fixed >= 1
                and cost
                and cost > 0
                and family_feedback_count >= 3
                and (false_positive_rate or 0) <= 0.25
            ):
                action = "require_review_candidate"
                reason = "Multiple feedback signals include fixed directional-cost findings with low false-positive feedback; consider require-review after human policy review."
                require_review_candidates.append(family)
            elif fixed >= 1 and cost and cost > 0:
                action = "advisory_candidate"
                reason = "A fixed finding with directional cost exposure exists, but sample size is still too small for stricter enforcement."
                advisory_candidates.append(family)
            elif agreement_rate is not None and agreement_rate >= 0.7 and finding_count >= 3:
                action = "advisory_candidate"
                reason = (
                    "Developer agreement is high enough to surface this family more prominently."
                )
                advisory_candidates.append(family)
            else:
                action = "keep_shadow_collect_feedback"
                reason = "Signal is mixed or sparse; keep collecting feedback before enforcement."
                keep_shadow_candidates.append(family)

            family_rows.append(
                {
                    "family": family,
                    "finding_count": finding_count,
                    "feedback_count": family_feedback_count,
                    "agreement_rate": agreement_rate,
                    "false_positive_rate": false_positive_rate,
                    "fixed_count": fixed,
                    "accepted_risk_count": accepted,
                    "cost_exposure_usd_per_run": round(float(cost), 2)
                    if cost is not None
                    else None,
                    "recommended_policy_action": action,
                    "reason": reason,
                }
            )

        priority = {
            "require_review_candidate": 0,
            "accepted_risk_policy_review_candidate": 1,
            "lower_severity_or_suppress_candidate": 2,
            "advisory_candidate": 3,
            "keep_shadow_collect_feedback": 4,
        }
        family_rows.sort(
            key=lambda r: (
                priority.get(str(r.get("recommended_policy_action")), 9),
                -(r.get("finding_count") or 0),
                str(r.get("family")),
            )
        )

        if not run_count:
            summary = "No receipts found; no policy recommendation can be made."
        elif total_feedback < max(5, run_count // 4):
            summary = "Feedback coverage is still thin; use these as shadow-mode tuning hints, not enforcement decisions."
        elif require_review_candidates:
            summary = "Some families have enough positive/fixed signal to review for require-review policy thresholds."
        elif advisory_candidates:
            summary = "Some families look ready for stronger advisory surfacing, but not require-review yet."
        else:
            summary = "Keep policy in shadow/advisory calibration until stronger agreement or fixed-finding evidence accumulates."

        return {
            "kind": "semzero_policy_calibration_v1",
            "auto_applied": False,
            "summary": summary,
            "require_review_candidates": require_review_candidates,
            "advisory_candidates": advisory_candidates,
            "lower_severity_or_suppress_candidates": suppress_or_lower_candidates,
            "accepted_risk_policy_review_candidates": accepted_risk_review_candidates,
            "keep_shadow_collect_feedback": keep_shadow_candidates,
            "family_recommendations": family_rows[:10],
            "calibrated_stable_finding_count": calibrated_stable_count,
            "guardrail": "Recommendations are advisory only. Require-review suggestions need human review, enough feedback, low false-positive signal, and stable recurring finding IDs.",
            "suggested_next_step": "Prefer advisory-mode tuning first; update .semzero/assumption_gate_policy.yml only after reviewing stable finding recurrence, family-level feedback, false positives, and fixed/accepted-risk history.",
        }

    @staticmethod
    def _stable_finding_rows(stable_findings: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for row in stable_findings.values():
            copy = dict(row)
            copy.pop("receipt_keys", None)
            copy["dispositions"] = dict(sorted((copy.get("dispositions") or {}).items()))
            copy["confidence_values"] = dict(sorted((copy.get("confidence_values") or {}).items()))
            copy["cost_exposure_usd_per_run"] = round(
                float(copy.get("cost_exposure_usd_per_run") or 0.0), 2
            )
            copy["cost_exposure_usd_per_month"] = round(
                float(copy.get("cost_exposure_usd_per_month") or 0.0), 2
            )
            rows.append(copy)
        return sorted(
            rows,
            key=lambda r: (
                -(r.get("feedback_count") or 0),
                -(r.get("occurrence_count") or 0),
                -(r.get("risk_score_max") or 0),
                str(r.get("stable_id") or ""),
            ),
        )

    @staticmethod
    def _calibration_readiness(
        *,
        run_count: int,
        feedback_summary: dict[str, Any],
        policy_recommendations: dict[str, Any] | None,
        stable_rows: list[dict[str, Any]],
        roi: dict[str, Any],
    ) -> dict[str, Any]:
        feedback_count = int(feedback_summary.get("feedback_count") or 0)
        agreement = feedback_summary.get("developer_agreement_rate")
        fp = int(feedback_summary.get("false_positive_count") or 0)
        recurring_with_feedback = sum(
            1
            for row in stable_rows
            if int(row.get("feedback_count") or 0) > 0
            and int(row.get("occurrence_count") or 0) >= 1
        )
        require_candidates = (policy_recommendations or {}).get("require_review_candidates") or []
        advisory_candidates = (policy_recommendations or {}).get("advisory_candidates") or []
        if run_count < 20 or feedback_count < 5:
            state = "shadow_only"
            reason = "Calibration sample is still small; collect more PR receipts and developer feedback."
        elif fp >= max(2, feedback_count // 3):
            state = "tune_noise_before_enforcement"
            reason = (
                "False-positive feedback is material; tune noisy families before stricter modes."
            )
        elif (
            require_candidates
            and recurring_with_feedback >= 2
            and (agreement is not None and agreement >= 0.7)
        ):
            state = "require_review_candidate_human_review_required"
            reason = "Some recurring stable findings have positive feedback; human policy review is still required before strict mode."
        elif advisory_candidates or (agreement is not None and agreement >= 0.6):
            state = "advisory_candidate"
            reason = "Signals support stronger advisory surfacing, but not strict enforcement yet."
        else:
            state = "continue_shadow_calibration"
            reason = "Signal is mixed or sparse; keep shadow/advisory calibration."
        return {
            "state": state,
            "reason": reason,
            "run_count": run_count,
            "feedback_count": feedback_count,
            "recurring_stable_findings_with_feedback": recurring_with_feedback,
            "developer_agreement_rate": agreement,
            "false_positive_count": fp,
            "fixed_finding_count": int(roi.get("fixed_finding_count") or 0),
            "guardrail": "This is not enforcement. It is a calibration readiness hint for humans reviewing policy.",
        }

    def _load_feedback_records(self) -> list[dict[str, Any]]:
        if not self.feedback_file:
            candidate = Path(self.receipt_dir) / "assumption_feedback.jsonl"
        else:
            candidate = Path(self.feedback_file)
        try:
            from .assumption_feedback import load_feedback

            return load_feedback(candidate)
        except Exception:
            return []

    def _feedback_summary_from_records(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        try:
            from .assumption_feedback import summarize_feedback

            return summarize_feedback(records)
        except Exception:
            return {
                "feedback_count": 0,
                "developer_agreement_rate": None,
                "developer_disagreement_rate": None,
                "disposition_counts": {},
            }

    def _feedback_summary(self) -> dict[str, Any]:
        return self._feedback_summary_from_records(self._load_feedback_records())

    @staticmethod
    def _replay_trust_note(
        replay_ran: int, drift: int, not_run: int, low_fidelity: int, scored: int
    ) -> str:
        if replay_ran == 0:
            return "No Replay Lite validation has run yet; treat findings as inferred static/history evidence."
        if drift and low_fidelity == 0:
            return "Replay Lite found drift signals with no low-fidelity flags; prioritize these for human review, not automatic blocking."
        if drift:
            return "Replay Lite found drift signals, but some findings still have low or incomplete fidelity; review fixtures and evidence coverage."
        if not_run > replay_ran:
            return "Some findings remain inferred-only; add Replay Lite fixtures for critical recurring families."
        return "Replay Lite coverage exists but has not detected drift in the current receipt set; keep collecting replay and feedback evidence."

    @staticmethod
    def _trust_note(
        run_count: int, feedback_summary: dict[str, Any], roi: dict[str, Any] | None = None
    ) -> str:
        roi = roi or {}
        feedback_count = int(feedback_summary.get("feedback_count") or 0)
        agreement = feedback_summary.get("developer_agreement_rate")
        fixed = int(roi.get("fixed_finding_count") or 0)
        avoided = roi.get("estimated_avoided_cost_usd_per_run")
        if run_count < 20:
            return "Calibration sample is still small; remain in shadow mode until more PR receipts accumulate."
        if feedback_count < max(5, run_count // 4):
            return "Receipts exist, but developer feedback coverage is still thin; keep collecting agreement/disagreement before enforcement."
        if fixed and avoided:
            return "Shadow evidence now includes fixed findings with directional avoided-cost signal; advisory mode may be reasonable for high-confidence, recurring families."
        if agreement is not None and agreement >= 0.7 and run_count >= 30:
            return "Shadow evidence is becoming actionable; advisory mode may be reasonable for high-confidence findings."
        return "Continue shadow/advisory calibration; use feedback to tune noisy assumption families before requiring review."

    def _load_receipts(self) -> Iterable[dict[str, Any]]:
        root = Path(self.receipt_dir)
        if not root.exists():
            return []
        rows: list[dict[str, Any]] = []
        for path in sorted(root.rglob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if (
                isinstance(payload, dict)
                and payload.get("receipt_kind") in ASSUMPTION_RECEIPT_KINDS
            ):
                payload.setdefault("_path", str(path))
                rows.append(payload)
        return rows

    @staticmethod
    def _receipt_key(receipt: dict[str, Any]) -> str:
        return str(
            receipt.get("_path")
            or receipt.get("receipt_id")
            or receipt.get("run_id")
            or receipt.get("generated_at")
            or ""
        )

    @staticmethod
    def _index_feedback(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        index: dict[str, list[dict[str, Any]]] = {}
        for row in records:
            receipt = str(row.get("receipt") or "")
            finding = str(row.get("finding_id") or "")
            stable = str(row.get("stable_finding_id") or row.get("stable_id") or "")
            keys = []
            for ident in [finding, stable]:
                if ident:
                    keys.append(f"finding::{ident}")
                if receipt and ident:
                    keys.append(f"receipt_finding::{receipt}::{ident}")
            if receipt:
                keys.append(f"receipt::{receipt}")
            for key in keys:
                index.setdefault(key, []).append(row)
        return index

    @staticmethod
    def _feedback_for_finding(
        index: dict[str, list[dict[str, Any]]], receipt_key: str, finding: dict[str, Any]
    ) -> list[dict[str, Any]]:
        identifiers = [
            str(finding.get("id") or ""),
            str(finding.get("stable_id") or ""),
            str(finding.get("legacy_id") or ""),
            str(finding.get("fingerprint") or ""),
        ]
        rows: list[dict[str, Any]] = []
        seen: set[int] = set()
        keys = []
        for ident in dict.fromkeys(i for i in identifiers if i):
            keys.extend([f"receipt_finding::{receipt_key}::{ident}", f"finding::{ident}"])
        for key in keys:
            for row in index.get(key, []):
                marker = id(row)
                if marker not in seen:
                    seen.add(marker)
                    rows.append(row)
        return rows

    @staticmethod
    def _sorted_blast_nodes(blast_nodes: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for row in blast_nodes.values():
            copy = dict(row)
            copy["families"] = dict(
                sorted((copy.get("families") or {}).items(), key=lambda item: (-item[1], item[0]))
            )
            copy["cost_exposure_usd_per_run"] = round(
                float(copy.get("cost_exposure_usd_per_run") or 0.0), 2
            )
            copy["cost_exposure_usd_per_month"] = round(
                float(copy.get("cost_exposure_usd_per_month") or 0.0), 2
            )
            rows.append(copy)
        return sorted(
            rows,
            key=lambda row: (
                -row["finding_count"],
                -float(row.get("cost_exposure_usd_per_run") or 0.0),
                str(row["node"].get("name", "")),
            ),
        )[:10]


def _finding_cost(finding: dict[str, Any]) -> float | None:
    cost = (finding.get("cost_estimate") or {}).get("estimated_extra_cost_per_run_usd")
    if isinstance(cost, (int, float)):
        return float(cost)
    return None


def _finding_monthly_cost(finding: dict[str, Any]) -> float | None:
    cost = (finding.get("cost_estimate") or {}).get("estimated_extra_cost_per_month_usd")
    if isinstance(cost, (int, float)):
        return float(cost)
    return None


def _day(value: Any) -> str:
    if not value:
        return ""
    text = str(value)
    if len(text) >= 10:
        return text[:10]
    return ""
