"""Precision/noise evaluation for SemZero Assumption Gate receipts.

This module is intentionally advisory-only. It borrows from security/devops
quality gates (triage queues, noisy-rule review, suppress-before-enforce
checks) but applies them to dbt/data assumption findings.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

NOISY_DISPOSITIONS = {"false_positive", "disagree"}
USEFUL_DISPOSITIONS = {"agree", "fixed"}
VALUE_DISPOSITIONS = {"fixed", "agree", "accepted_risk"}


@dataclass
class PrecisionConfig:
    receipt_dir: str | Path
    feedback_file: str | Path | None = None
    exceptions_file: str | Path | None = None
    max_blast_radius_nodes: int = 12
    min_trigger_evidence: int = 1
    enforcement_false_positive_rate_threshold: float = 0.25


class AssumptionPrecisionEvaluator:
    """Evaluate assumption findings for usefulness/noise before stricter policy.

    The evaluator does not change policy and does not block CI. It creates a
    human-readable report that tells teams which findings/families are precise,
    over-broad, missing trigger evidence, or risky to enforce.
    """

    def __init__(self, config: PrecisionConfig | None = None, **kwargs: Any) -> None:
        self.config = config or PrecisionConfig(**kwargs)

    def build(self) -> dict[str, Any]:
        receipts = list(self._load_receipts())
        feedback = self._load_feedback()
        feedback_index = self._index_feedback(feedback)
        exception_records = self._load_exceptions()

        family_rows: dict[str, dict[str, Any]] = {}
        finding_rows: list[dict[str, Any]] = []
        missing_trigger = 0
        no_blast = 0
        overbroad = 0
        enforcement_risky = 0
        useful = 0
        noisy = 0
        with_feedback = 0
        total = 0
        replay_ran_count = 0
        replay_drift_count = 0
        inferred_only_count = 0
        low_fidelity_count = 0

        for receipt in receipts:
            receipt_key = self._receipt_key(receipt)
            for finding in receipt.get("findings") or []:
                if not isinstance(finding, dict):
                    continue
                total += 1
                family = str(finding.get("family") or "unknown")
                stable_id = str(
                    finding.get("stable_id") or finding.get("id") or finding.get("legacy_id") or ""
                )
                fb = self._feedback_for_finding(feedback_index, receipt_key, finding)
                dispositions = {str(row.get("disposition") or "needs_context") for row in fb}
                if fb:
                    with_feedback += 1
                exception_state = str(
                    ((finding.get("exception") or {}) or {}).get("state") or "none"
                )
                trigger_count = len(finding.get("trigger_evidence") or [])
                blast_nodes = finding.get("blast_radius") or finding.get("blast_radius_nodes") or []
                blast_count = len(blast_nodes) if isinstance(blast_nodes, list) else 0
                cost = self._finding_cost(finding)
                monthly_cost = self._finding_monthly_cost(finding)
                risk_score = int(finding.get("risk_score") or 0)
                severity = str(finding.get("severity") or "unknown")
                business = finding.get("business_impact") or {}
                business_sev = str(business.get("highest_business_severity") or "UNKNOWN")
                control = finding.get("control_coverage") or {}
                control_status = str(control.get("status") or "unknown")
                validation_replay = finding.get("validation_replay") or {}
                replay_fidelity = finding.get("replay_fidelity") or {}
                replay_ran = bool(validation_replay.get("replay_ran"))
                replay_status = str(
                    validation_replay.get("status") or ("not_run" if not replay_ran else "unknown")
                )
                fidelity_score = replay_fidelity.get("score")
                fidelity_level = str(replay_fidelity.get("level") or "unknown")
                if replay_ran:
                    replay_ran_count += 1
                else:
                    inferred_only_count += 1
                if replay_status == "drift_detected":
                    replay_drift_count += 1
                if (isinstance(fidelity_score, (int, float)) and float(fidelity_score) < 0.5) or (
                    not replay_ran and not isinstance(fidelity_score, (int, float))
                ):
                    low_fidelity_count += 1

                flags: list[str] = []
                if trigger_count < self.config.min_trigger_evidence:
                    flags.append("missing_trigger_evidence")
                    missing_trigger += 1
                if blast_count == 0:
                    flags.append("no_blast_radius")
                    no_blast += 1
                if blast_count > self.config.max_blast_radius_nodes and business_sev in {
                    "UNKNOWN",
                    "INTERNAL_LOW",
                }:
                    flags.append("overbroad_blast_radius")
                    overbroad += 1
                if dispositions & NOISY_DISPOSITIONS:
                    flags.append("negative_developer_feedback")
                    noisy += 1
                if exception_state == "active_exception":
                    flags.append("active_exception_do_not_enforce")
                if dispositions & USEFUL_DISPOSITIONS:
                    useful += 1
                    flags.append("developer_validated")
                if cost is not None or monthly_cost is not None:
                    flags.append("cost_evidence_present")
                if business_sev not in {"UNKNOWN", "INTERNAL_LOW"}:
                    flags.append("business_critical")
                if control_status == "weak":
                    flags.append("weak_control_coverage")
                if replay_ran:
                    flags.append("replay_validated")
                else:
                    flags.append("inferred_only_no_replay")
                if replay_status == "drift_detected":
                    flags.append("replay_drift_detected")
                if isinstance(fidelity_score, (int, float)) and float(fidelity_score) < 0.5:
                    flags.append("low_replay_fidelity")

                precision_state = self._precision_state(
                    flags,
                    dispositions,
                    risk_score,
                    blast_count,
                    trigger_count,
                    exception_state,
                    replay_status,
                    replay_ran,
                    fidelity_score,
                )
                if precision_state in {"enforcement_risky", "needs_noise_review"}:
                    enforcement_risky += 1

                row = {
                    "stable_id": stable_id,
                    "legacy_id": finding.get("legacy_id"),
                    "family": family,
                    "source": finding.get("source")
                    or {
                        "unique_id": finding.get("source_resource"),
                        "path": finding.get("source_path"),
                    },
                    "severity": severity,
                    "risk_score": risk_score,
                    "confidence": finding.get("confidence"),
                    "precision_state": precision_state,
                    "flags": flags,
                    "trigger_evidence_count": trigger_count,
                    "blast_radius_count": blast_count,
                    "business_severity": business_sev,
                    "control_coverage_status": control_status,
                    "exception_state": exception_state,
                    "feedback_dispositions": sorted(dispositions),
                    "cost_exposure_usd_per_run": cost,
                    "cost_exposure_usd_per_month": monthly_cost,
                    "validation_replay_status": replay_status,
                    "validation_replay_ran": replay_ran,
                    "replay_fidelity_score": fidelity_score,
                    "replay_fidelity_level": fidelity_level,
                    "recommended_precision_action": self._recommended_action(
                        precision_state, flags
                    ),
                }
                finding_rows.append(row)

                bucket = family_rows.setdefault(
                    family,
                    {
                        "family": family,
                        "finding_count": 0,
                        "with_feedback": 0,
                        "developer_validated_count": 0,
                        "negative_feedback_count": 0,
                        "missing_trigger_evidence_count": 0,
                        "no_blast_radius_count": 0,
                        "overbroad_blast_radius_count": 0,
                        "active_exception_count": 0,
                        "cost_evidence_count": 0,
                        "business_critical_count": 0,
                        "replay_ran_count": 0,
                        "replay_drift_count": 0,
                        "inferred_only_count": 0,
                        "low_fidelity_count": 0,
                        "states": {},
                    },
                )
                bucket["finding_count"] += 1
                if fb:
                    bucket["with_feedback"] += 1
                if dispositions & USEFUL_DISPOSITIONS:
                    bucket["developer_validated_count"] += 1
                if dispositions & NOISY_DISPOSITIONS:
                    bucket["negative_feedback_count"] += 1
                if "missing_trigger_evidence" in flags:
                    bucket["missing_trigger_evidence_count"] += 1
                if "no_blast_radius" in flags:
                    bucket["no_blast_radius_count"] += 1
                if "overbroad_blast_radius" in flags:
                    bucket["overbroad_blast_radius_count"] += 1
                if exception_state == "active_exception":
                    bucket["active_exception_count"] += 1
                if cost is not None or monthly_cost is not None:
                    bucket["cost_evidence_count"] += 1
                if business_sev not in {"UNKNOWN", "INTERNAL_LOW"}:
                    bucket["business_critical_count"] += 1
                if replay_ran:
                    bucket["replay_ran_count"] += 1
                else:
                    bucket["inferred_only_count"] += 1
                if replay_status == "drift_detected":
                    bucket["replay_drift_count"] += 1
                if "low_replay_fidelity" in flags:
                    bucket["low_fidelity_count"] += 1
                bucket["states"][precision_state] = bucket["states"].get(precision_state, 0) + 1

        family_eval = [self._family_eval(row) for row in family_rows.values()]
        family_eval.sort(
            key=lambda row: (row["recommended_action"], -row["finding_count"], row["family"])
        )
        finding_rows.sort(
            key=lambda row: (
                row["precision_state"] not in {"enforcement_risky", "needs_noise_review"},
                -(row.get("risk_score") or 0),
                row.get("family") or "",
            )
        )

        return {
            "report_kind": "semzero_assumption_precision_eval_v1_22",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "receipt_dir": str(self.config.receipt_dir),
            "receipt_count": len(receipts),
            "finding_count": total,
            "with_feedback_count": with_feedback,
            "developer_validated_count": useful,
            "negative_feedback_count": noisy,
            "missing_trigger_evidence_count": missing_trigger,
            "no_blast_radius_count": no_blast,
            "overbroad_blast_radius_count": overbroad,
            "enforcement_risky_count": enforcement_risky,
            "replay_ran_count": replay_ran_count,
            "replay_drift_count": replay_drift_count,
            "inferred_only_count": inferred_only_count,
            "low_fidelity_count": low_fidelity_count,
            "replay_coverage_rate": round(replay_ran_count / total, 4) if total else 0.0,
            "feedback_coverage_rate": round(with_feedback / total, 4) if total else 0.0,
            "precision_summary": self._summary(
                total,
                with_feedback,
                missing_trigger,
                no_blast,
                overbroad,
                enforcement_risky,
                noisy,
                replay_ran_count,
                replay_drift_count,
                low_fidelity_count,
            ),
            "family_precision": family_eval,
            "finding_review_queue": finding_rows[:50],
            "guardrail": "Advisory-only. This report identifies noisy/over-broad findings before stricter policy; it does not block CI or mutate policy.",
            "next_step": "Prioritize missing-trigger and negative-feedback families before promoting any family from shadow to advisory/require-review.",
        }

    def save_json(self, output: str | Path) -> dict[str, Any]:
        payload = self.build()
        p = Path(output)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        return payload

    def save_markdown(self, output: str | Path) -> str:
        payload = self.build()
        lines = [
            "# SemZero Assumption Precision Evaluation",
            "",
            f"Generated: `{payload['generated_at']}`",
            "",
            f"- Receipts scanned: **{payload['receipt_count']}**",
            f"- Findings evaluated: **{payload['finding_count']}**",
            f"- Feedback coverage: **{round(payload['feedback_coverage_rate'] * 100, 1)}%**",
            f"- Developer-validated findings: **{payload['developer_validated_count']}**",
            f"- Negative-feedback findings: **{payload['negative_feedback_count']}**",
            f"- Missing trigger evidence: **{payload['missing_trigger_evidence_count']}**",
            f"- No blast radius: **{payload['no_blast_radius_count']}**",
            f"- Over-broad blast radius: **{payload['overbroad_blast_radius_count']}**",
            f"- Enforcement-risky findings: **{payload['enforcement_risky_count']}**",
            f"- Replay Lite coverage: **{round(payload.get('replay_coverage_rate', 0) * 100, 1)}%**",
            f"- Replay-validated drift findings: **{payload.get('replay_drift_count', 0)}**",
            f"- Inferred-only findings: **{payload.get('inferred_only_count', 0)}**",
            f"- Low-fidelity findings: **{payload.get('low_fidelity_count', 0)}**",
            "",
            "## Precision summary",
            "",
            payload.get("precision_summary") or "No summary available.",
            "",
            "## Family precision",
            "",
        ]
        for row in payload.get("family_precision") or []:
            lines.append(
                f"- `{row['family']}` → **{row['recommended_action']}**: {row['reason']} ({row['finding_count']} finding(s))"
            )
        if not payload.get("family_precision"):
            lines.append("No findings found.")
        lines += ["", "## Finding review queue", ""]
        for row in (payload.get("finding_review_queue") or [])[:15]:
            source = row.get("source") or {}
            lines.append(
                f"- `{row.get('stable_id') or row.get('legacy_id')}` `{row.get('family')}` **{row.get('precision_state')}** source `{source.get('name') or source.get('unique_id') or source.get('path')}` — {row.get('recommended_precision_action')}"
            )
        if not payload.get("finding_review_queue"):
            lines.append("No finding review queue items.")
        lines += ["", "## Guardrail", "", payload.get("guardrail", "")]
        text = "\n".join(lines) + "\n"
        p = Path(output)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return text

    def _summary(
        self,
        total: int,
        feedback: int,
        missing: int,
        no_blast: int,
        overbroad: int,
        risky: int,
        noisy: int,
        replay_ran: int,
        replay_drift: int,
        low_fidelity: int,
    ) -> str:
        if total == 0:
            return "No assumption findings found. Run the Assumption Gate on PR receipts before evaluating precision."
        warnings = []
        if feedback / max(total, 1) < 0.2:
            warnings.append("feedback coverage is low")
        if missing:
            warnings.append("some findings lack why-now trigger evidence")
        if no_blast:
            warnings.append("some findings lack downstream blast radius")
        if overbroad:
            warnings.append("some findings may be over-broad")
        if noisy:
            warnings.append("developer feedback includes noisy/false-positive signals")
        if risky:
            warnings.append("some findings should not be promoted without noise review")
        if replay_ran == 0:
            warnings.append("no findings have Replay Lite validation yet")
        elif low_fidelity:
            warnings.append("some replay/evidence fidelity is low")
        if warnings:
            return "Precision calibration needed: " + "; ".join(warnings) + "."
        return "Precision signals look healthy for shadow/advisory review; keep collecting feedback before any require-review promotion."

    def _family_eval(self, row: dict[str, Any]) -> dict[str, Any]:
        count = int(row.get("finding_count") or 0)
        negative = int(row.get("negative_feedback_count") or 0)
        missing = int(row.get("missing_trigger_evidence_count") or 0)
        no_blast = int(row.get("no_blast_radius_count") or 0)
        overbroad = int(row.get("overbroad_blast_radius_count") or 0)
        validated = int(row.get("developer_validated_count") or 0)
        active_exception = int(row.get("active_exception_count") or 0)
        cost = int(row.get("cost_evidence_count") or 0)
        business = int(row.get("business_critical_count") or 0)
        replay_ran = int(row.get("replay_ran_count") or 0)
        replay_drift = int(row.get("replay_drift_count") or 0)
        inferred_only = int(row.get("inferred_only_count") or 0)
        low_fidelity = int(row.get("low_fidelity_count") or 0)
        if replay_drift and validated and negative == 0:
            action = "advisory_candidate_replay_validated"
            reason = "Replay Lite drift plus developer validation makes this family a strong advisory candidate, not a block."
        elif inferred_only == count and count >= 2:
            action = "add_replay_fixtures_before_policy"
            reason = (
                "Findings remain inferred-only; add Replay Lite fixtures before policy promotion."
            )
        elif low_fidelity:
            action = "improve_replay_fidelity_before_policy"
            reason = "Some findings have low replay/evidence fidelity."
        elif negative >= 1 and negative / max(count, 1) >= 0.3:
            action = "tune_or_suppress_before_advisory"
            reason = "Negative/false-positive feedback is high for this family."
        elif missing or no_blast:
            action = "improve_evidence_before_policy"
            reason = "Some findings lack trigger evidence or downstream blast radius."
        elif overbroad:
            action = "tighten_blast_radius_scope"
            reason = "Some findings appear over-broad and should be scoped before promotion."
        elif active_exception:
            action = "keep_advisory_with_exception_review"
            reason = "Active exceptions exist; review accepted risk before stricter policy."
        elif validated and (cost or business):
            action = "advisory_candidate_not_blocking"
            reason = "Developer-validated findings have cost or business-critical evidence, but remain advisory-first."
        else:
            action = "continue_shadow_collect_feedback"
            reason = "More feedback is needed before policy tuning."
        return {**row, "recommended_action": action, "reason": reason}

    def _precision_state(
        self,
        flags: list[str],
        dispositions: set[str],
        risk_score: int,
        blast_count: int,
        trigger_count: int,
        exception_state: str,
        replay_status: str = "not_run",
        replay_ran: bool = False,
        fidelity_score: Any = None,
    ) -> str:
        if exception_state == "active_exception" or dispositions & {"accepted_risk"}:
            return "accepted_risk_review"
        if dispositions & NOISY_DISPOSITIONS:
            return "needs_noise_review"
        if "missing_trigger_evidence" in flags or "no_blast_radius" in flags:
            return "insufficient_evidence"
        if "overbroad_blast_radius" in flags:
            return "overbroad"
        if replay_status == "drift_detected" and dispositions & USEFUL_DISPOSITIONS:
            return "replay_validated_developer_validated"
        if replay_status == "drift_detected":
            return "replay_validated_shadow"
        if isinstance(fidelity_score, (int, float)) and float(fidelity_score) < 0.5:
            return "low_fidelity_review"
        if dispositions & USEFUL_DISPOSITIONS:
            return "developer_validated"
        if risk_score >= 80 and blast_count and trigger_count:
            return "high_signal_shadow"
        return "shadow_unvalidated"

    def _recommended_action(self, state: str, flags: list[str]) -> str:
        if state == "needs_noise_review":
            return "Review developer feedback and tune/suppress before advisory promotion."
        if state == "insufficient_evidence":
            return "Improve trigger/blast-radius evidence before treating this as policy signal."
        if state == "overbroad":
            return "Narrow blast-radius scope or add business-criticality filters."
        if state == "accepted_risk_review":
            return "Keep visible as accepted risk; review expiry/owner before policy promotion."
        if state == "replay_validated_developer_validated":
            return "Strong advisory candidate: Replay Lite drift plus developer validation; still do not hard-block automatically."
        if state == "replay_validated_shadow":
            return "Prioritize human review and feedback: Replay Lite detected drift but developer validation is still needed."
        if state == "low_fidelity_review":
            return "Improve replay fixture/evidence fidelity before treating as a policy signal."
        if state == "developer_validated":
            return "Candidate for advisory policy after more samples; do not hard-block yet."
        if state == "high_signal_shadow":
            return "Strong shadow finding; collect feedback before advisory promotion."
        return "Keep in shadow and collect feedback."

    def _load_receipts(self) -> Iterable[dict[str, Any]]:
        root = Path(self.config.receipt_dir)
        if not root.exists():
            return []
        rows = []
        for path in sorted(root.rglob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(payload, dict) and str(payload.get("receipt_kind") or "").startswith(
                "dbt_assumption_gate_v"
            ):
                payload.setdefault("_path", str(path))
                rows.append(payload)
        return rows

    def _load_feedback(self) -> list[dict[str, Any]]:
        path = (
            Path(self.config.feedback_file)
            if self.config.feedback_file
            else Path(self.config.receipt_dir) / "assumption_feedback.jsonl"
        )
        if not path.exists():
            return []
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if isinstance(row, dict):
                rows.append(row)
        return rows

    def _load_exceptions(self) -> list[dict[str, Any]]:
        path = (
            Path(self.config.exceptions_file)
            if self.config.exceptions_file
            else Path(self.config.receipt_dir) / "assumption_exceptions.jsonl"
        )
        if not path.exists():
            return []
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if isinstance(row, dict):
                rows.append(row)
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
            ids = [
                str(row.get("finding_id") or ""),
                str(row.get("stable_finding_id") or row.get("stable_id") or ""),
            ]
            for ident in ids:
                if not ident:
                    continue
                index.setdefault(f"finding::{ident}", []).append(row)
                if receipt:
                    index.setdefault(f"receipt_finding::{receipt}::{ident}", []).append(row)
        return index

    @staticmethod
    def _feedback_for_finding(
        index: dict[str, list[dict[str, Any]]], receipt_key: str, finding: dict[str, Any]
    ) -> list[dict[str, Any]]:
        ids = [
            str(finding.get("id") or ""),
            str(finding.get("stable_id") or ""),
            str(finding.get("legacy_id") or ""),
            str(finding.get("fingerprint") or ""),
        ]
        rows: list[dict[str, Any]] = []
        seen: set[int] = set()
        for ident in dict.fromkeys(i for i in ids if i):
            for key in [f"receipt_finding::{receipt_key}::{ident}", f"finding::{ident}"]:
                for row in index.get(key, []):
                    marker = id(row)
                    if marker not in seen:
                        seen.add(marker)
                        rows.append(row)
        return rows

    @staticmethod
    def _finding_cost(finding: dict[str, Any]) -> float | None:
        cost = (finding.get("cost_estimate") or {}).get("estimated_extra_cost_per_run_usd")
        return float(cost) if isinstance(cost, (int, float)) else None

    @staticmethod
    def _finding_monthly_cost(finding: dict[str, Any]) -> float | None:
        cost = (finding.get("cost_estimate") or {}).get("estimated_extra_cost_per_month_usd")
        return float(cost) if isinstance(cost, (int, float)) else None
