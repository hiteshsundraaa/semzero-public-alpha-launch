from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class CalibrationSummary:
    total_runs: int = 0
    block_rate: float = 0.0
    review_rate: float = 0.0
    high_oncall_rate: float = 0.0
    average_reliability_score: float = 100.0
    recent_failure_modes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_runs": self.total_runs,
            "block_rate": round(self.block_rate, 3),
            "review_rate": round(self.review_rate, 3),
            "high_oncall_rate": round(self.high_oncall_rate, 3),
            "average_reliability_score": round(self.average_reliability_score, 2),
            "recent_failure_modes": self.recent_failure_modes,
        }


class ReliabilityCalibrationStore:
    def __init__(self, path: str) -> None:
        self.path = Path(path)

    def record(self, gate_result: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(_thin_record(gate_result), default=str) + "\n")

    def load_summary(self) -> CalibrationSummary:
        if not self.path.exists():
            return CalibrationSummary()
        records = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except Exception:
                continue
        if not records:
            return CalibrationSummary()
        blocks = sum(1 for r in records if r.get("verdict") == "BLOCK")
        reviews = sum(1 for r in records if r.get("verdict") == "NEEDS_REVIEW")
        high = sum(1 for r in records if r.get("oncall_risk") == "HIGH")
        avg_score = sum(float(r.get("reliability_score") or 0) for r in records) / len(records)
        modes = []
        for record in records[-20:]:
            modes.extend(record.get("failure_modes") or [])
        return CalibrationSummary(
            total_runs=len(records),
            block_rate=blocks / len(records),
            review_rate=reviews / len(records),
            high_oncall_rate=high / len(records),
            average_reliability_score=avg_score,
            recent_failure_modes=list(dict.fromkeys(modes))[:10],
        )


def _thin_record(gate_result: dict[str, Any]) -> dict[str, Any]:
    assessments = gate_result.get("assessments") or []
    modes = []
    for item in assessments:
        modes.extend(item.get("predicted_failure_modes") or [])
    return {
        "evaluated_at": gate_result.get("evaluated_at"),
        "verdict": gate_result.get("verdict"),
        "reliability_score": gate_result.get("reliability_score"),
        "oncall_risk": gate_result.get("oncall_risk"),
        "total_blast_radius": gate_result.get("total_blast_radius"),
        "failure_modes": list(dict.fromkeys(modes))[:8],
    }
