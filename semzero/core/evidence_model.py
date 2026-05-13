from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

SEVERITY_RANK = {"low": 1, "medium": 2, "high": 3, "critical": 4}
VERDICT_RANK = {"ALLOW": 1, "ADVISORY": 2, "REQUIRE_REVIEW": 3, "BLOCK": 4}


@dataclass(slots=True)
class BlastRadiusNode:
    """Typed graph node used by all SemZero domain adapters.

    The current v1 adapter emits dbt/data nodes only, but this shape is domain-neutral
    so later Terraform/Kubernetes/app adapters can attach infra and service nodes without
    changing receipt consumers.
    """

    node_type: str
    name: str
    unique_id: str = ""
    domain: str = "data"
    path: str = ""
    owner: str = ""
    criticality: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "node_type": self.node_type,
            "type": self.node_type,  # backwards-compatible alias for older comments/tests
            "name": self.name,
            "domain": self.domain,
        }
        if self.unique_id:
            payload["unique_id"] = self.unique_id
        if self.path:
            payload["path"] = self.path
        if self.owner:
            payload["owner"] = self.owner
        if self.criticality:
            payload["criticality"] = self.criticality
        if self.metadata:
            payload["metadata"] = self.metadata
        return payload


@dataclass(slots=True)
class EvidenceFinding:
    id: str
    domain: str
    adapter: str
    family: str
    severity: str
    assumption: str
    trigger: str
    why_it_matters: str
    source: BlastRadiusNode
    evidence_excerpt: str
    changed_resources: list[BlastRadiusNode] = field(default_factory=list)
    blast_radius: list[BlastRadiusNode] = field(default_factory=list)
    recommended_check: str = ""
    cost_estimate: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "domain": self.domain,
            "adapter": self.adapter,
            "family": self.family,
            "severity": self.severity,
            "assumption": self.assumption,
            "trigger": self.trigger,
            "why_it_matters": self.why_it_matters,
            "source": self.source.to_dict(),
            # Backwards-compatible fields used by the first v1 command/comment renderer.
            "source_resource": self.source.unique_id or self.source.name,
            "source_path": self.source.path,
            "evidence_excerpt": self.evidence_excerpt[:500],
            "changed_resources": [node.to_dict() for node in self.changed_resources],
            "blast_radius": [node.to_dict() for node in self.blast_radius],
            "recommended_check": self.recommended_check,
            "cost_estimate": self.cost_estimate,
            "tags": self.tags,
        }


@dataclass(slots=True)
class GateReceipt:
    receipt_kind: str
    semzero_version: str
    mode: str
    verdict: str
    generated_at: str
    adapter: str
    domain: str
    findings: list[EvidenceFinding]
    changed_files: list[str] = field(default_factory=list)
    changed_resources: list[BlastRadiusNode] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def now(
        cls,
        *,
        receipt_kind: str,
        semzero_version: str,
        mode: str,
        verdict: str,
        adapter: str,
        domain: str,
        findings: list[EvidenceFinding],
        changed_files: list[str] | None = None,
        changed_resources: list[BlastRadiusNode] | None = None,
        summary: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "GateReceipt":
        return cls(
            receipt_kind=receipt_kind,
            semzero_version=semzero_version,
            mode=mode,
            verdict=verdict,
            generated_at=datetime.now(timezone.utc).isoformat(),
            adapter=adapter,
            domain=domain,
            findings=findings,
            changed_files=changed_files or [],
            changed_resources=changed_resources or [],
            summary=summary or {},
            metadata=metadata or {},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "receipt_kind": self.receipt_kind,
            "semzero_version": self.semzero_version,
            "mode": self.mode,
            "verdict": self.verdict,
            "generated_at": self.generated_at,
            "adapter": self.adapter,
            "domain": self.domain,
            "metadata": self.metadata,
            "changed_files": self.changed_files,
            "changed_resources": [node.to_dict() for node in self.changed_resources],
            "summary": self.summary,
            "findings": [finding.to_dict() for finding in self.findings],
        }


def normalize_verdict(verdict: str) -> str:
    value = (verdict or "ALLOW").upper().replace("-", "_").replace(" ", "_")
    aliases = {
        "REVIEW": "REQUIRE_REVIEW",
        "WARN": "ADVISORY",
        "WARNING": "ADVISORY",
        "PASS": "ALLOW",
        "SAFE": "ALLOW",
    }
    return aliases.get(value, value if value in VERDICT_RANK else "ALLOW")
