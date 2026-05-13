from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class EvidenceItem:
    stage: str
    evidence_type: str
    status: str
    summary: str
    details: dict[str, Any] = field(default_factory=dict)
    observed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "evidence_type": self.evidence_type,
            "status": self.status,
            "summary": self.summary,
            "details": self.details,
            "observed": self.observed,
        }


@dataclass
class EvidenceBundle:
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    recorded_at: str = field(default_factory=_ts)
    mode: str = "safe"
    db_url: str = ""
    items: list[EvidenceItem] = field(default_factory=list)

    def add(
        self,
        stage: str,
        evidence_type: str,
        status: str,
        summary: str,
        *,
        observed: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.items.append(
            EvidenceItem(
                stage=stage,
                evidence_type=evidence_type,
                status=status,
                summary=summary,
                observed=observed,
                details=details or {},
            )
        )

    def summary(self) -> dict[str, Any]:
        inferred = sum(1 for i in self.items if not i.observed)
        observed = sum(1 for i in self.items if i.observed)
        failed = sum(1 for i in self.items if str(i.status).upper() in {"FAIL", "BLOCK", "ERROR"})
        return {
            "run_id": self.run_id,
            "recorded_at": self.recorded_at,
            "mode": self.mode,
            "db_url": self.db_url,
            "evidence_count": len(self.items),
            "inferred_count": inferred,
            "observed_count": observed,
            "failed_count": failed,
            "stages": sorted({i.stage for i in self.items}),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "recorded_at": self.recorded_at,
            "mode": self.mode,
            "db_url": self.db_url,
            "summary": self.summary(),
            "items": [i.to_dict() for i in self.items],
        }


class EvidenceStore:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, bundle: EvidenceBundle) -> Path:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(bundle.to_dict(), default=str) + "\n")
        return self.path
