from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _append_jsonl(path: str | Path, payload: dict[str, Any]) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, default=str) + "\n")


def _read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    src = Path(path)
    if not src.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in src.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


@dataclass
class OverrideLedger:
    path: str = "data/override_ledger.jsonl"

    def record(
        self,
        receipt_id: str,
        target: str,
        actor: str,
        reason: str,
        verdict: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "kind": "override",
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "receipt_id": receipt_id,
            "target": target,
            "actor": actor,
            "reason": reason,
            "verdict": verdict,
            "metadata": metadata or {},
        }
        _append_jsonl(self.path, payload)
        return payload

    def load(self) -> list[dict[str, Any]]:
        return _read_jsonl(self.path)

    def summary(self) -> dict[str, Any]:
        rows = self.load()
        by_verdict: dict[str, int] = {}
        for row in rows:
            verdict = str(row.get("verdict") or "UNKNOWN")
            by_verdict[verdict] = by_verdict.get(verdict, 0) + 1
        return {
            "entry_count": len(rows),
            "latest_target": rows[-1].get("target") if rows else "",
            "by_verdict": by_verdict,
        }


@dataclass
class IncidentLedger:
    path: str = "data/incident_ledger.jsonl"

    def record(
        self,
        incident_id: str,
        target: str,
        severity: str,
        summary: str,
        linked_receipt_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "kind": "incident",
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "incident_id": incident_id,
            "target": target,
            "severity": severity,
            "summary": summary,
            "linked_receipt_id": linked_receipt_id,
            "metadata": metadata or {},
        }
        _append_jsonl(self.path, payload)
        return payload

    def load(self) -> list[dict[str, Any]]:
        return _read_jsonl(self.path)

    def summary(self) -> dict[str, Any]:
        rows = self.load()
        by_severity: dict[str, int] = {}
        linked = 0
        for row in rows:
            sev = str(row.get("severity") or "UNKNOWN")
            by_severity[sev] = by_severity.get(sev, 0) + 1
            if row.get("linked_receipt_id"):
                linked += 1
        return {
            "entry_count": len(rows),
            "linked_receipts": linked,
            "by_severity": by_severity,
        }


@dataclass
class ShadowFeedbackLedger:
    path: str = "data/shadow_feedback.jsonl"

    def record(
        self,
        receipt_id: str,
        target: str,
        actor: str,
        outcome: str,
        note: str = "",
        metadata: dict[str, Any] | None = None,
        repo: str = "",
        team: str = "",
        risk_category: str = "",
    ) -> dict[str, Any]:
        payload = {
            "kind": "shadow_feedback",
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "receipt_id": receipt_id,
            "target": target,
            "actor": actor,
            "outcome": outcome,
            "note": note,
            "repo": repo or "unknown_repo",
            "team": team or "unknown_team",
            "risk_category": risk_category or "unknown",
            "metadata": metadata or {},
        }
        _append_jsonl(self.path, payload)
        return payload

    def load(self) -> list[dict[str, Any]]:
        return _read_jsonl(self.path)

    def summary(self) -> dict[str, Any]:
        rows = self.load()
        by_outcome: dict[str, int] = {}
        for row in rows:
            outcome = str(row.get("outcome") or "UNKNOWN")
            by_outcome[outcome] = by_outcome.get(outcome, 0) + 1
        confirmed = by_outcome.get("confirmed", 0) + by_outcome.get("useful", 0)
        noisy = by_outcome.get("noisy", 0) + by_outcome.get("false_positive", 0)
        return {
            "entry_count": len(rows),
            "latest_target": rows[-1].get("target") if rows else "",
            "by_outcome": by_outcome,
            "precision_proxy": round(confirmed / max(1, confirmed + noisy), 4),
        }
