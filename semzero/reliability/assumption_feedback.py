from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

VALID_DISPOSITIONS = {
    "agree",
    "disagree",
    "false_positive",
    "false_negative",
    "needs_context",
    "fixed",
    "accepted_risk",
}
AGREE_DISPOSITIONS = {"agree", "fixed"}
DISAGREE_DISPOSITIONS = {"disagree", "false_positive"}


@dataclass(slots=True)
class AssumptionFeedbackRecord:
    """Developer feedback linked to an Assumption Gate receipt/finding.

    The record is deliberately JSONL-friendly so teams can start in CI/shadow mode
    without a database. It can later be backed by a real receipt store without
    changing the dashboard contract.
    """

    receipt: str
    disposition: str
    finding_id: str = ""
    stable_finding_id: str = ""
    family: str = ""
    reviewer: str = ""
    comment: str = ""
    pr: str = ""
    repository: str = ""
    created_at: str = ""
    source: str = "cli"
    metadata: dict[str, Any] | None = None

    def normalized(self) -> "AssumptionFeedbackRecord":
        disposition = normalize_disposition(self.disposition)
        created_at = self.created_at or datetime.now(timezone.utc).isoformat()
        return AssumptionFeedbackRecord(
            receipt=self.receipt,
            disposition=disposition,
            finding_id=self.finding_id,
            stable_finding_id=self.stable_finding_id,
            family=self.family,
            reviewer=self.reviewer,
            comment=self.comment,
            pr=self.pr,
            repository=self.repository,
            created_at=created_at,
            source=self.source or "cli",
            metadata=self.metadata or {},
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self.normalized())
        if not payload.get("metadata"):
            payload.pop("metadata", None)
        return payload


def normalize_disposition(value: str) -> str:
    normalized = (value or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "yes": "agree",
        "correct": "agree",
        "valid": "agree",
        "no": "disagree",
        "wrong": "disagree",
        "fp": "false_positive",
        "falsepositive": "false_positive",
        "fn": "false_negative",
        "falsenegative": "false_negative",
        "needs_more_context": "needs_context",
        "mitigated": "fixed",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in VALID_DISPOSITIONS:
        raise ValueError(
            f"Unsupported disposition: {value!r}. Expected one of: {', '.join(sorted(VALID_DISPOSITIONS))}"
        )
    return normalized


def append_feedback(record: AssumptionFeedbackRecord, feedback_file: str | Path) -> dict[str, Any]:
    path = Path(feedback_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = record.to_dict()
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")
    return payload


def load_feedback(feedback_file: str | Path) -> list[dict[str, Any]]:
    path = Path(feedback_file)
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except Exception:
            continue
        if isinstance(payload, dict):
            disposition = payload.get("disposition")
            try:
                payload["disposition"] = normalize_disposition(str(disposition))
            except ValueError:
                payload["disposition"] = "needs_context"
            rows.append(payload)
    return rows


def summarize_feedback(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    records = list(records)
    disposition_counts: dict[str, int] = {}
    finding_counts: dict[str, int] = {}
    receipt_counts: dict[str, int] = {}
    for row in records:
        disposition = str(row.get("disposition") or "needs_context")
        disposition_counts[disposition] = disposition_counts.get(disposition, 0) + 1
        finding_id = str(row.get("stable_finding_id") or row.get("finding_id") or "")
        if finding_id:
            finding_counts[finding_id] = finding_counts.get(finding_id, 0) + 1
        receipt = str(row.get("receipt") or "")
        if receipt:
            receipt_counts[receipt] = receipt_counts.get(receipt, 0) + 1
    agree = sum(disposition_counts.get(k, 0) for k in AGREE_DISPOSITIONS)
    disagree = sum(disposition_counts.get(k, 0) for k in DISAGREE_DISPOSITIONS)
    total = len(records)
    return {
        "feedback_count": total,
        "developer_agreement_count": agree,
        "developer_disagreement_count": disagree,
        "developer_agreement_rate": round(agree / total, 4) if total else None,
        "developer_disagreement_rate": round(disagree / total, 4) if total else None,
        "false_positive_count": disposition_counts.get("false_positive", 0),
        "false_negative_count": disposition_counts.get("false_negative", 0),
        "fixed_count": disposition_counts.get("fixed", 0),
        "accepted_risk_count": disposition_counts.get("accepted_risk", 0),
        "needs_context_count": disposition_counts.get("needs_context", 0),
        "disposition_counts": dict(sorted(disposition_counts.items())),
        "most_feedback_findings": sorted(
            finding_counts.items(), key=lambda item: (-item[1], item[0])
        )[:10],
        "receipts_with_feedback": len(receipt_counts),
    }
