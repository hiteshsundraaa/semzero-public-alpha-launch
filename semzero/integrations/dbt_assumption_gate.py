from __future__ import annotations

import json
import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from semzero.core.evidence_model import BlastRadiusNode, EvidenceFinding, GateReceipt
from semzero.reliability.business_criticality import (
    infer_business_criticality,
    summarize_business_impact,
    load_criticality_registry,
)
from semzero.reliability.assumption_exceptions import (
    load_exceptions,
    match_exception,
    summarize_exceptions,
)
from semzero.reliability.warehouse_history import load_warehouse_history, profile_for_resource
from semzero.reliability.assumption_replay_lite import load_replay_fixtures, run_replay_lite

TEMPORAL_PATTERN = re.compile(
    r"\b(?:date|date_trunc|timestamp_trunc|cast)\s*\(\s*([^\)]+?(?:_at|_ts|time|timestamp|date)[^\)]*)\)",
    re.I,
)
DATE_TRUNC_PATTERN = re.compile(
    r"\b(?:date_trunc|timestamp_trunc)\s*\(\s*['\"]?(day|week|month|hour)['\"]?\s*,\s*([^\)]+)\)",
    re.I,
)
JOIN_PATTERN = re.compile(
    r"\bjoin\s+([\w\.\{\}\(\)'\"\s-]+?)\s+(?:as\s+)?(\w+)?\s*\bon\b\s+([^;\n]+)", re.I
)
JOIN_ON_EQUALS_PATTERN = re.compile(r"([\w\.]+)\s*=\s*([\w\.]+)", re.I)
INCREMENTAL_PATTERN = re.compile(
    r"\bis_incremental\s*\(|materialized\s*=\s*['\"]incremental['\"]|\bmerge\s+into\b", re.I
)
WHERE_PREDICATE_PATTERN = re.compile(
    r"\bwhere\b([\s\S]{0,700}?)(?:\bgroup\s+by\b|\border\s+by\b|\blimit\b|$)", re.I
)
PARTITION_WRAPPER_PATTERN = re.compile(
    r"\b(?:date|cast|to_date|date_trunc|timestamp_trunc)\s*\(\s*([\w\.]+)", re.I
)
ENUM_FILTER_PATTERN = re.compile(
    r"\b([\w\.]*(?:status|state|type|category|kind|code))\s+(?:in\s*\([^\)]*\)|=\s*['\"][^'\"]+['\"])",
    re.I,
)
CASE_PATTERN = re.compile(r"\bcase\b([\s\S]{0,600}?)\bend\b", re.I)
COALESCE_PATTERN = re.compile(r"\b(?:coalesce|ifnull|nvl)\s*\(\s*([\w\.]+)\s*,\s*([^\)]+)\)", re.I)
NULL_TEST_PATTERN = re.compile(r"\b([\w\.]+)\s+is\s+(?:not\s+)?null\b", re.I)
TIMEZONE_TRIGGER = re.compile(
    r"\b(timezone|time\s*zone|at\s+time\s+zone|convert_timezone|utc|localtime|timestamp_tz|timestamp_ntz|event_ts|event_time|occurred_at)\b",
    re.I,
)
INCREMENTAL_TRIGGER = re.compile(
    r"\b(is_incremental|updated_at|loaded_at|event_ts|event_time|_dbt_max_partition|last_run|date\s*\(|date_trunc|to_date|partition|cluster_by)\b",
    re.I,
)
JOIN_TRIGGER = re.compile(
    r"\b(join|user_id|account_id|customer_id|order_id|id\s*=|unique|relationship|grain|dedup|row_number)\b",
    re.I,
)
ENUM_TRIGGER = re.compile(
    r"\b(status|state|enum|case\s+when|else|null|paid|refund|active|cancel)\b", re.I
)
NULL_TRIGGER = re.compile(r"\b(coalesce|ifnull|nvl|null|not\s+null|default|nullable|blank)\b", re.I)

TIMEZONE_FUNC_PATTERN = re.compile(r"\b(convert_timezone|at\s+time\s+zone|timezone)\b", re.I)
TEMPORAL_BOUNDARY_TOKEN_PATTERN = re.compile(
    r"\b(date\s*\(|date_trunc|timestamp_trunc|cast\s*\([^\)]*\bas\s+date|utc|localtime|timestamp_tz|timestamp_ntz)\b",
    re.I,
)
DIFF_REMOVED_LINE_PATTERN = re.compile(r"^\-([^\-].*)$", re.M)
DIFF_ADDED_LINE_PATTERN = re.compile(r"^\+([^\+].*)$", re.M)
OR_EXPANSION_PATTERN = re.compile(r"\bor\b", re.I)
INEQUALITY_PREDICATE_PATTERN = re.compile(
    r"(?:updated_at|loaded_at|event_ts|event_time|_dbt_max_partition|last_run)\s*(?:>=|>|between)",
    re.I,
)
AGGREGATE_AFTER_JOIN_PATTERN = re.compile(r"\b(sum|count|avg)\s*\([^\)]*\)", re.I)
DEDUP_PATTERN = re.compile(r"\b(row_number\s*\(|qualify\b|distinct\b|group\s+by\b)", re.I)
CASE_STATUS_PATTERN = re.compile(
    r"\bcase\b[\s\S]{0,700}?\bwhen\b[\s\S]{0,700}?\b(status|state|type|category|kind|code)\b[\s\S]{0,700}?\bend\b",
    re.I,
)
ELSE_PATTERN = re.compile(r"\belse\b", re.I)
ZERO_FALLBACK_PATTERN = re.compile(
    r"\b(?:coalesce|ifnull|nvl)\s*\(\s*([\w\.]+)\s*,\s*(0|0\.0|false|\'unknown\'|\'none\'|\'\'|\"\")\s*\)",
    re.I,
)
FULL_REFRESH_PATTERN = re.compile(
    r"\b(full_refresh|--full-refresh|create\s+or\s+replace\s+table|materialized\s*[:=]\s*[\'\"]?table)\b",
    re.I,
)

FAMILY_ORDER = {
    "temporal_bucket": 0,
    "incremental_filter": 1,
    "join_cardinality": 2,
    "enum_domain_closure": 3,
    "null_default_fallback": 4,
    "materialization_cost": 5,
}

SEVERITY_RANK = {"low": 1, "medium": 2, "high": 3, "critical": 4}

TRIGGER_PATTERNS = {
    "temporal_bucket": [
        re.compile(
            r"\b(convert_timezone|at\s+time\s+zone|timezone|timestamp_tz|timestamp_ntz|utc|localtime|event_ts|event_time|occurred_at|date_trunc|date\s*\()\b",
            re.I,
        ),
    ],
    "incremental_filter": [
        re.compile(
            r"\b(is_incremental|updated_at|loaded_at|_dbt_max_partition|last_run|partition|cluster_by|date\s*\(|date_trunc|to_date)\b",
            re.I,
        ),
    ],
    "join_cardinality": [
        re.compile(
            r"\b(join|user_id|account_id|customer_id|order_id|id\s*=|unique|relationship|relationships|grain|dedup|row_number)\b",
            re.I,
        ),
    ],
    "enum_domain_closure": [
        re.compile(
            r"\b(status|state|enum|case\s+when|else|null|paid|refund|active|cancel|domain)\b", re.I
        ),
    ],
    "null_default_fallback": [
        re.compile(r"\b(coalesce|ifnull|nvl|null|not\s+null|default|nullable|blank)\b", re.I),
    ],
    "materialization_cost": [
        re.compile(
            r"\b(materialized\s*[:=]\s*[\'\"]?(table|incremental|view)|full_refresh|--full-refresh|create\s+or\s+replace\s+table|merge\s+into)\b",
            re.I,
        ),
    ],
}


@dataclass(slots=True)
class DbtResource:
    unique_id: str
    name: str
    resource_type: str
    original_file_path: str = ""
    depends_on: list[str] = field(default_factory=list)
    raw_sql: str = ""
    compiled_sql: str = ""
    columns: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)
    owner: str = ""
    compiled_path: str = ""
    relation_name: str = ""
    tags: list[str] = field(default_factory=list)
    config: dict[str, Any] = field(default_factory=dict)
    artifact_meta: dict[str, Any] = field(default_factory=dict)
    runtime: dict[str, Any] = field(default_factory=dict)

    @property
    def sql(self) -> str:
        return self.compiled_sql or self.raw_sql or ""

    @property
    def display_name(self) -> str:
        return self.name or self.unique_id


@dataclass(slots=True)
class AssumptionFindingV1:
    finding_id: str
    family: str
    severity: str
    assumption: str
    trigger: str
    why_it_matters: str
    source_resource: str
    source_path: str
    evidence_excerpt: str
    changed_resources: list[str]
    blast_radius: list[dict[str, str]]
    recommended_check: str
    cost_estimate: dict[str, Any] = field(default_factory=dict)
    trigger_evidence: list[str] = field(default_factory=list)
    confidence: str = "medium"
    noise_controls: list[str] = field(default_factory=list)
    risk_score: int = 0
    pattern_detail: dict[str, Any] = field(default_factory=dict)
    detector_version: str = "assumption_gate_core_v1_25"
    assumption_diff: dict[str, Any] = field(default_factory=dict)
    replay_fidelity: dict[str, Any] = field(default_factory=dict)
    stable_id: str = ""
    fingerprint: str = ""
    business_impact: dict[str, Any] = field(default_factory=dict)
    control_coverage: dict[str, Any] = field(default_factory=dict)
    incident_chain: list[dict[str, Any]] = field(default_factory=list)
    exception: dict[str, Any] = field(default_factory=dict)
    validation_replay: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        source_node = {
            "node_type": "dbt_resource",
            "type": "dbt_resource",
            "name": self.source_resource.split(".")[-1]
            if self.source_resource
            else self.source_path,
            "unique_id": self.source_resource,
            "domain": "data",
            "path": self.source_path,
        }
        return {
            "id": self.stable_id or self.finding_id,
            "stable_id": self.stable_id or self.finding_id,
            "legacy_id": self.finding_id,
            "fingerprint": self.fingerprint,
            "domain": "data",
            "adapter": "dbt_assumption_gate",
            "family": self.family,
            "severity": self.severity,
            "assumption": self.assumption,
            "trigger": self.trigger,
            "why_it_matters": self.why_it_matters,
            "source": {k: v for k, v in source_node.items() if v},
            "source_resource": self.source_resource,
            "source_path": self.source_path,
            "evidence_excerpt": self.evidence_excerpt[:500],
            "changed_resources": self.changed_resources,
            "blast_radius": self.blast_radius,
            "recommended_check": self.recommended_check,
            "cost_estimate": self.cost_estimate,
            "trigger_evidence": self.trigger_evidence[:5],
            "confidence": self.confidence,
            "noise_controls": self.noise_controls,
            "risk_score": self.risk_score,
            "pattern_detail": self.pattern_detail,
            "business_impact": self.business_impact,
            "control_coverage": self.control_coverage,
            "incident_chain": self.incident_chain,
            "exception": self.exception,
            "assumption_diff": self.assumption_diff,
            "replay_fidelity": self.replay_fidelity,
            "validation_replay": self.validation_replay,
            "detector_version": self.detector_version,
        }


@dataclass(slots=True)
class AssumptionGateReceiptV1:
    semzero_version: str
    mode: str
    verdict: str
    generated_at: str
    dbt_manifest_path: str
    changed_files: list[str]
    changed_resources: list[dict[str, str]]
    findings: list[AssumptionFindingV1]
    summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "receipt_kind": "dbt_assumption_gate_v1_25",
            "schema_version": "semzero.evidence.v1",
            "adapter": "dbt_assumption_gate",
            "domain": "data",
            "semzero_version": self.semzero_version,
            "mode": self.mode,
            "verdict": self.verdict,
            "generated_at": self.generated_at,
            "metadata": {
                "dbt_manifest_path": self.dbt_manifest_path,
                "adapter_scope": "core_data_only_v1_25",
                "future_adapter_note": "Receipt uses typed domain-neutral nodes so infra/app adapters can be added later without changing consumers.",
            },
            "dbt_manifest_path": self.dbt_manifest_path,
            "changed_files": self.changed_files,
            "changed_resources": self.changed_resources,
            "summary": self.summary,
            "findings": [finding.to_dict() for finding in self.findings],
        }


class DbtAssumptionGate:
    """Focused v1 Assumption Gate for dbt PRs.

    This gate intentionally does not try to be a whole data platform. It detects
    hidden behavioral assumptions in downstream dbt SQL that become risky when a
    PR changes related model SQL/schema files. Findings are only emitted when a
    known assumption pattern is connected to a changed resource or its blast
    radius; this keeps noise low enough for CI/shadow-mode adoption.
    """

    def __init__(
        self,
        manifest_path: str | Path,
        table_sizes: dict[str, Any] | None = None,
        cost_profiles: dict[str, Any] | None = None,
        criticality_registry: dict[str, Any] | None = None,
        exceptions: list[dict[str, Any]] | None = None,
        catalog_path: str | Path | None = None,
        run_results_path: str | Path | None = None,
        project_dir: str | Path | None = None,
        warehouse_history: dict[str, Any] | None = None,
        replay_fixtures: dict[str, Any] | None = None,
    ) -> None:
        self.manifest_path = Path(manifest_path)
        self.project_dir = Path(project_dir) if project_dir else self.manifest_path.parent.parent
        self.table_sizes = table_sizes or {}
        self.cost_profiles = cost_profiles or {}
        self.warehouse_history = warehouse_history or {}
        self.replay_fixtures = replay_fixtures or {}
        self.criticality_registry = criticality_registry or {}
        self.exceptions = exceptions or []
        payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        self.resources = self._load_resources(payload)
        self.artifact_warnings: list[str] = []
        self._enrich_from_compiled_paths()
        if catalog_path:
            self._enrich_from_catalog(catalog_path)
        if run_results_path:
            self._enrich_from_run_results(run_results_path)
        self.children = self._build_children(self.resources)
        self.tests_by_parent = self._build_tests_by_parent(self.resources)

    @staticmethod
    def _load_resources(payload: dict[str, Any]) -> dict[str, DbtResource]:
        resources: dict[str, DbtResource] = {}
        for section in ("nodes", "sources", "exposures"):
            for unique_id, raw in (payload.get(section) or {}).items():
                if not isinstance(raw, dict):
                    continue
                depends = raw.get("depends_on") or {}
                depends_nodes = depends.get("nodes") if isinstance(depends, dict) else []
                meta = raw.get("meta") or {}
                owner = ""
                if isinstance(raw.get("owner"), dict):
                    owner = str(raw["owner"].get("name") or raw["owner"].get("email") or "")
                elif isinstance(meta.get("owner"), str):
                    owner = meta.get("owner", "")
                resources[str(unique_id)] = DbtResource(
                    unique_id=str(unique_id),
                    name=str(raw.get("name") or unique_id),
                    resource_type=str(raw.get("resource_type") or section.rstrip("s")),
                    original_file_path=str(raw.get("original_file_path") or raw.get("path") or ""),
                    depends_on=[str(x) for x in (depends_nodes or [])],
                    raw_sql=str(raw.get("raw_sql") or raw.get("raw_code") or ""),
                    compiled_sql=str(raw.get("compiled_sql") or raw.get("compiled_code") or ""),
                    columns=raw.get("columns") or {},
                    meta=meta if isinstance(meta, dict) else {},
                    owner=owner,
                    compiled_path=str(
                        raw.get("compiled_path") or raw.get("compiled_file_path") or ""
                    ),
                    relation_name=".".join(
                        str(raw.get(k) or "").strip()
                        for k in ("database", "schema", "alias")
                        if raw.get(k)
                    )
                    or str(raw.get("relation_name") or raw.get("alias") or ""),
                    tags=[str(x) for x in (raw.get("tags") or [])],
                    config=raw.get("config") if isinstance(raw.get("config"), dict) else {},
                )
        return resources

    def _enrich_from_compiled_paths(self) -> None:
        """Best-effort compiled SQL fallback for real dbt projects.

        dbt manifests sometimes omit compiled_sql in slim artifacts or fixtures but
        include compiled_path. Reading that file makes the Assumption Gate behave
        more like it will in a real repo without requiring warehouse access.
        """
        for resource in self.resources.values():
            if resource.compiled_sql or not resource.compiled_path:
                continue
            candidates = [
                Path(resource.compiled_path),
                self.project_dir / resource.compiled_path,
                self.manifest_path.parent / resource.compiled_path,
            ]
            for candidate in candidates:
                try:
                    if candidate.exists() and candidate.is_file():
                        resource.compiled_sql = candidate.read_text(
                            encoding="utf-8", errors="ignore"
                        )[:200000]
                        resource.artifact_meta["compiled_sql_source"] = str(candidate)
                        break
                except Exception as exc:
                    self.artifact_warnings.append(
                        f"compiled_path_read_failed:{resource.unique_id}:{exc}"
                    )
                    break

    def _enrich_from_catalog(self, catalog_path: str | Path) -> None:
        """Load dbt catalog.json metadata: column types/stats and relation shape."""
        try:
            payload = json.loads(Path(catalog_path).read_text(encoding="utf-8"))
        except Exception as exc:
            self.artifact_warnings.append(f"catalog_load_failed:{catalog_path}:{exc}")
            return
        for section in ("nodes", "sources"):
            for unique_id, raw in (payload.get(section) or {}).items():
                res = self.resources.get(str(unique_id))
                if not res or not isinstance(raw, dict):
                    continue
                cols = raw.get("columns") if isinstance(raw.get("columns"), dict) else {}
                if cols:
                    merged = dict(res.columns or {})
                    for name, col in cols.items():
                        if isinstance(col, dict):
                            existing = (
                                merged.get(name) if isinstance(merged.get(name), dict) else {}
                            )
                            existing = dict(existing)
                            existing.update(
                                {
                                    k: v
                                    for k, v in col.items()
                                    if k in {"type", "index", "comment", "stats"}
                                }
                            )
                            merged[name] = existing
                    res.columns = merged
                meta = res.artifact_meta.setdefault("catalog", {})
                for key in ("metadata", "stats", "generated_at"):
                    if key in raw:
                        meta[key] = raw.get(key)

    def _enrich_from_run_results(self, run_results_path: str | Path) -> None:
        """Load dbt run_results.json timing/status metadata for cost/calibration context."""
        try:
            payload = json.loads(Path(run_results_path).read_text(encoding="utf-8"))
        except Exception as exc:
            self.artifact_warnings.append(f"run_results_load_failed:{run_results_path}:{exc}")
            return
        for raw in payload.get("results") or []:
            if not isinstance(raw, dict):
                continue
            uid = str(raw.get("unique_id") or "")
            res = self.resources.get(uid)
            if not res:
                continue
            res.runtime = {
                "status": raw.get("status"),
                "execution_time": raw.get("execution_time"),
                "adapter_response": raw.get("adapter_response") or {},
                "thread_id": raw.get("thread_id"),
            }

    @staticmethod
    def _build_children(resources: dict[str, DbtResource]) -> dict[str, set[str]]:
        children: dict[str, set[str]] = {key: set() for key in resources}
        for unique_id, resource in resources.items():
            for parent in resource.depends_on:
                children.setdefault(parent, set()).add(unique_id)
        return children

    @staticmethod
    def _build_tests_by_parent(resources: dict[str, DbtResource]) -> dict[str, list[DbtResource]]:
        tests: dict[str, list[DbtResource]] = {}
        for resource in resources.values():
            if resource.resource_type not in {
                "test",
                "unit_test",
            } and not resource.unique_id.startswith("test."):
                continue
            for parent in resource.depends_on:
                tests.setdefault(parent, []).append(resource)
        return tests

    def run(
        self, changed_files: Iterable[str], mode: str = "shadow", changed_diff: str = ""
    ) -> AssumptionGateReceiptV1:
        changed_files_norm = [self._norm_path(path) for path in changed_files if str(path).strip()]
        changed_ids = self._changed_resource_ids(changed_files_norm)
        if not changed_ids:
            # Fallback: no exact manifest path match. Treat all SQL models touched by filename stem as changed.
            stems = {Path(path).stem.lower() for path in changed_files_norm}
            changed_ids = {
                rid
                for rid, resource in self.resources.items()
                if resource.resource_type in {"model", "source"} and resource.name.lower() in stems
            }
        blast_ids = self._transitive_children(changed_ids)
        scan_ids = sorted(changed_ids | blast_ids)
        self._current_changed_diff = changed_diff or ""
        trigger_report = self._collect_triggers(
            changed_ids, changed_files_norm, changed_diff=changed_diff
        )
        self._current_trigger_report = trigger_report
        findings = self._scan_assumptions(scan_ids, changed_ids, blast_ids, trigger_report)
        findings = self._deduplicate_findings(findings)
        self._apply_exceptions(findings, receipt_key="pending")
        verdict = self._verdict(findings)
        changed_resources = [
            self._resource_ref(self.resources[rid])
            for rid in sorted(changed_ids)
            if rid in self.resources
        ]
        summary = self._summary(findings, changed_ids, blast_ids, trigger_report)
        return AssumptionGateReceiptV1(
            semzero_version=self._version(),
            mode=mode,
            verdict=verdict,
            generated_at=datetime.now(timezone.utc).isoformat(),
            dbt_manifest_path=str(self.manifest_path),
            changed_files=changed_files_norm,
            changed_resources=changed_resources,
            findings=findings,
            summary=summary,
        )

    def _changed_resource_ids(self, changed_files: list[str]) -> set[str]:
        changed = set()
        changed_suffixes = {path.lower() for path in changed_files}
        for unique_id, resource in self.resources.items():
            path = self._norm_path(resource.original_file_path).lower()
            if not path:
                continue
            if path in changed_suffixes or any(
                path.endswith(item) or item.endswith(path) for item in changed_suffixes
            ):
                changed.add(unique_id)
        return changed

    def _transitive_children(self, roots: set[str], max_depth: int = 8) -> set[str]:
        seen: set[str] = set()
        frontier = set(roots)
        for _ in range(max_depth):
            nxt: set[str] = set()
            for node in frontier:
                for child in self.children.get(node, set()):
                    if child not in seen and child not in roots:
                        seen.add(child)
                        nxt.add(child)
            if not nxt:
                break
            frontier = nxt
        return seen

    def _collect_triggers(
        self, changed_ids: set[str], changed_files: list[str], changed_diff: str = ""
    ) -> dict[str, dict[str, Any]]:
        text_parts: list[str] = []
        if changed_diff:
            text_parts.append(str(changed_diff))
        for rid in changed_ids:
            resource = self.resources.get(rid)
            if resource:
                text_parts.extend([resource.original_file_path, resource.name, resource.sql])
        for file_path in changed_files:
            path = Path(file_path)
            if path.exists() and path.is_file():
                try:
                    text_parts.append(path.read_text(encoding="utf-8", errors="ignore")[:8000])
                except Exception:
                    pass
        text = "\n".join(text_parts)
        report: dict[str, dict[str, Any]] = {}
        for family, patterns in TRIGGER_PATTERNS.items():
            excerpts: list[str] = []
            for pattern in patterns:
                for match in pattern.finditer(text):
                    excerpts.append(self._line_excerpt(text, match.start(), radius=120))
                    if len(excerpts) >= 5:
                        break
                if len(excerpts) >= 5:
                    break
            report[family] = {"active": bool(excerpts), "evidence": excerpts}
        return report

    def _scan_assumptions(
        self,
        scan_ids: list[str],
        changed_ids: set[str],
        blast_ids: set[str],
        triggers: dict[str, dict[str, Any]],
    ) -> list[AssumptionFindingV1]:
        findings: list[AssumptionFindingV1] = []
        for rid in scan_ids:
            resource = self.resources.get(rid)
            if not resource:
                continue
            sql = resource.sql
            if not sql:
                continue
            downstream = self._blast_radius_refs(rid)
            affected_by = sorted(changed_ids & self._ancestors_or_self(rid)) or sorted(changed_ids)
            is_downstream = rid in blast_ids
            if triggers.get("temporal_bucket", {}).get("active"):
                findings.extend(
                    self._temporal_findings(resource, affected_by, downstream, is_downstream)
                )
            if (
                triggers.get("incremental_filter", {}).get("active")
                or INCREMENTAL_PATTERN.search(
                    "\n".join([resource.compiled_sql or "", resource.raw_sql or ""])
                )
                or "is_incremental"
                in "\n".join([resource.compiled_sql or "", resource.raw_sql or ""]).lower()
            ):
                findings.extend(
                    self._incremental_findings(resource, affected_by, downstream, is_downstream)
                )
            if triggers.get("join_cardinality", {}).get("active"):
                findings.extend(
                    self._join_findings(resource, affected_by, downstream, is_downstream)
                )
            if triggers.get("enum_domain_closure", {}).get("active"):
                findings.extend(
                    self._enum_findings(resource, affected_by, downstream, is_downstream)
                )
            if triggers.get("null_default_fallback", {}).get("active"):
                findings.extend(
                    self._null_findings(resource, affected_by, downstream, is_downstream)
                )
            if triggers.get("materialization_cost", {}).get("active"):
                findings.extend(
                    self._materialization_findings(resource, affected_by, downstream, is_downstream)
                )
        return findings

    def _temporal_findings(
        self,
        resource: DbtResource,
        changed: list[str],
        blast: list[dict[str, str]],
        is_downstream: bool,
    ) -> list[AssumptionFindingV1]:
        out: list[AssumptionFindingV1] = []
        matches = list(TEMPORAL_PATTERN.finditer(resource.sql)) + list(
            DATE_TRUNC_PATTERN.finditer(resource.sql)
        )
        for match in matches:
            excerpt = self._line_excerpt(resource.sql, match.start())
            detail = self._temporal_pattern_detail(resource.sql, excerpt)
            base = (
                "critical"
                if detail.get("timezone_conversion_in_changed_context")
                or detail.get("day_boundary_bucket")
                else "high"
            )
            severity = self._severity(resource, is_downstream, base=base)
            out.append(
                self._finding(
                    resource=resource,
                    family="temporal_bucket",
                    severity=severity,
                    assumption="Timestamp-to-date logic produces stable reporting buckets across this change.",
                    trigger="A related timestamp/timezone/date-bucketing change is present in the PR.",
                    why="Events near day boundaries can silently move between reporting days, changing dashboards without query failures.",
                    excerpt=excerpt,
                    changed=changed,
                    blast=blast,
                    recommended="Run a before/after bucket comparison by day and timezone for the affected timestamp over a recent representative window, especially midnight-boundary records.",
                    pattern_detail=detail,
                )
            )
        return out

    def _incremental_findings(
        self,
        resource: DbtResource,
        changed: list[str],
        blast: list[dict[str, str]],
        is_downstream: bool,
    ) -> list[AssumptionFindingV1]:
        sql = "\n".join([resource.compiled_sql or "", resource.raw_sql or ""]) or resource.sql
        if not INCREMENTAL_PATTERN.search(sql) and "is_incremental" not in sql.lower():
            return []
        where = WHERE_PREDICATE_PATTERN.search(sql)
        excerpt = self._line_excerpt(sql, where.start() if where else 0) if where else sql[:240]
        detail = self._incremental_pattern_detail(sql, excerpt)
        wrapped = bool(detail.get("partition_column_wrapped"))
        estimate = self._cost_estimate(resource, wrapped)
        base = (
            "critical"
            if detail.get("predicate_removed_or_widened_in_diff")
            else ("high" if wrapped or detail.get("or_expansion") else "medium")
        )
        severity = self._severity(resource, is_downstream, base=base)
        return [
            self._finding(
                resource=resource,
                family="incremental_filter",
                severity=severity,
                assumption="The incremental predicate bounds scanned rows and preserves partition pruning.",
                trigger="A related incremental/partition predicate change is present in the PR.",
                why="Weakening, removing, OR-expanding, or wrapping the incremental filter can turn a bounded incremental run into a large scan or missed-update bug.",
                excerpt=excerpt,
                changed=changed,
                blast=blast,
                recommended="Compare rows scanned and rows updated before/after this PR; avoid wrapping partition columns in DATE/CAST and verify the predicate still prunes the same partition/window.",
                cost_estimate=estimate,
                pattern_detail=detail,
            )
        ]

    def _join_findings(
        self,
        resource: DbtResource,
        changed: list[str],
        blast: list[dict[str, str]],
        is_downstream: bool,
    ) -> list[AssumptionFindingV1]:
        out: list[AssumptionFindingV1] = []
        unique_tests = self._has_uniqueness_hint(resource)
        sql = resource.sql
        for match in JOIN_PATTERN.finditer(sql):
            clause = match.group(0)
            if not JOIN_ON_EQUALS_PATTERN.search(clause):
                continue
            detail = self._join_pattern_detail(sql, clause, unique_tests)
            base = (
                "medium"
                if unique_tests
                else (
                    "critical" if detail.get("aggregate_after_join_without_uniqueness") else "high"
                )
            )
            severity = self._severity(resource, is_downstream, base=base)
            out.append(
                self._finding(
                    resource=resource,
                    family="join_cardinality",
                    severity=severity,
                    assumption="The joined key has stable grain or uniqueness, so the join will not fan out metrics.",
                    trigger="A related join key, grain, dedup, or uniqueness-sensitive change is present in the PR.",
                    why="If the right-side key is no longer unique, counts and revenue-like aggregates can inflate silently.",
                    excerpt=self._line_excerpt(resource.sql, match.start()),
                    changed=changed,
                    blast=blast,
                    recommended="Verify a dbt unique/relationship test exists for the joined key, or add an explicit dedup CTE before aggregating revenue/count metrics.",
                    pattern_detail=detail,
                )
            )
        return out

    def _enum_findings(
        self,
        resource: DbtResource,
        changed: list[str],
        blast: list[dict[str, str]],
        is_downstream: bool,
    ) -> list[AssumptionFindingV1]:
        out: list[AssumptionFindingV1] = []
        for match in ENUM_FILTER_PATTERN.finditer(resource.sql):
            out.append(
                self._finding(
                    resource=resource,
                    family="enum_domain_closure",
                    severity=self._severity(resource, is_downstream, base="medium"),
                    assumption="Status/type filters enumerate the complete valid domain.",
                    trigger="A related status/state/domain mapping change is present in the PR.",
                    why="New valid enum values can be silently excluded from metrics when filters or CASE branches assume a closed domain.",
                    excerpt=self._line_excerpt(resource.sql, match.start()),
                    changed=changed,
                    blast=blast,
                    recommended="Check whether new status/state values need an ELSE branch, explicit mapping row, or dashboard filter update.",
                )
            )
        for match in CASE_PATTERN.finditer(resource.sql):
            block = match.group(0)
            if re.search(r"\b(status|state|type|category)\b", block, re.I) and not re.search(
                r"\belse\b", block, re.I
            ):
                out.append(
                    self._finding(
                        resource=resource,
                        family="enum_domain_closure",
                        severity=self._severity(resource, is_downstream, base="medium"),
                        assumption="CASE mapping covers every relevant status/type value.",
                        trigger="A related status/state/domain mapping change is present in the PR.",
                        why="CASE expressions without ELSE can silently map new values to NULL or drop them from downstream metrics.",
                        excerpt=self._line_excerpt(resource.sql, match.start()),
                        changed=changed,
                        blast=blast,
                        recommended="Add an explicit ELSE branch or update the domain mapping for any newly valid states.",
                    )
                )
        return out

    def _null_findings(
        self,
        resource: DbtResource,
        changed: list[str],
        blast: list[dict[str, str]],
        is_downstream: bool,
    ) -> list[AssumptionFindingV1]:
        out: list[AssumptionFindingV1] = []
        for pattern, assumption in (
            (COALESCE_PATTERN, "Fallback value has the same business meaning as missing data."),
            (NULL_TEST_PATTERN, "Null presence/absence remains a valid business signal."),
        ):
            for match in pattern.finditer(resource.sql):
                excerpt = self._line_excerpt(resource.sql, match.start())
                detail = self._null_pattern_detail(match.group(0), excerpt)
                base = "high" if detail.get("zero_or_unknown_fallback") else "medium"
                out.append(
                    self._finding(
                        resource=resource,
                        family="null_default_fallback",
                        severity=self._severity(resource, is_downstream, base=base),
                        assumption=assumption,
                        trigger="A related nullability/default/coalesce change is present in the PR.",
                        why="Changed null/default behavior can hide upstream data loss, convert missingness into zero/unknown, or alter metrics without causing a hard query failure.",
                        excerpt=excerpt,
                        changed=changed,
                        blast=blast,
                        recommended="Compare null rates and fallback-value rates before/after; confirm zero/unknown/default still has the intended business meaning.",
                        pattern_detail=detail,
                    )
                )
        return out

    def _materialization_findings(
        self,
        resource: DbtResource,
        changed: list[str],
        blast: list[dict[str, str]],
        is_downstream: bool,
    ) -> list[AssumptionFindingV1]:
        sql = "\n".join(
            [
                resource.raw_sql or "",
                resource.compiled_sql or "",
                json.dumps(resource.meta, default=str),
            ]
        )
        diff = getattr(self, "_current_changed_diff", "") or ""
        combined = sql + "\n" + diff
        if not FULL_REFRESH_PATTERN.search(combined):
            return []
        excerpt = self._line_excerpt(combined, FULL_REFRESH_PATTERN.search(combined).start())
        detail = {
            "pattern_type": "dbt_materialization_or_full_refresh_cost",
            "full_refresh_or_replace_table": bool(
                re.search(
                    r"full_refresh|--full-refresh|create\s+or\s+replace\s+table", combined, re.I
                )
            ),
            "materialized_table_hint": bool(
                re.search(r"materialized\s*[:=]\s*[\'\"]?table", combined, re.I)
            ),
            "incremental_hint_present": bool(
                re.search(r"is_incremental|materialized\s*[:=]\s*[\'\"]?incremental", sql, re.I)
            ),
        }
        estimate = self._cost_estimate(
            resource, wrapped_partition=False, risk_kind="materialization_cost"
        )
        base = "critical" if detail["full_refresh_or_replace_table"] else "high"
        return [
            self._finding(
                resource=resource,
                family="materialization_cost",
                severity=self._severity(resource, is_downstream, base=base),
                assumption="The model materialization remains bounded and does not rebuild full history unexpectedly.",
                trigger="A related materialization/full-refresh/replace-table change is present in the PR.",
                why="Changing incremental behavior or introducing full-refresh/replace-table paths can rebuild large models and trigger Snowflake warehouse or Databricks cluster cost spikes.",
                excerpt=excerpt,
                changed=changed,
                blast=blast,
                recommended="Confirm the materialization change is intentional; compare expected rows rebuilt, runtime, and warehouse/cluster cost before merge.",
                cost_estimate=estimate,
                pattern_detail=detail,
            )
        ]

    def _finding(
        self,
        resource: DbtResource,
        family: str,
        severity: str,
        assumption: str,
        trigger: str,
        why: str,
        excerpt: str,
        changed: list[str],
        blast: list[dict[str, str]],
        recommended: str,
        cost_estimate: dict[str, Any] | None = None,
        pattern_detail: dict[str, Any] | None = None,
    ) -> AssumptionFindingV1:
        pattern_detail = pattern_detail or {}
        legacy_id = f"AG-{family.upper().replace('_', '-')}-001"
        fingerprint = self._finding_fingerprint(family, resource, excerpt, pattern_detail)
        stable_id = f"AG-{family.upper().replace('_', '-')}-{fingerprint[:10].upper()}"
        validation_replay = self._validation_replay(family, resource)
        return AssumptionFindingV1(
            finding_id=legacy_id,
            stable_id=stable_id,
            fingerprint=fingerprint,
            family=family,
            severity=severity,
            assumption=assumption,
            trigger=trigger,
            why_it_matters=why,
            source_resource=resource.unique_id,
            source_path=resource.original_file_path,
            evidence_excerpt=excerpt.strip(),
            changed_resources=changed,
            blast_radius=blast,
            recommended_check=recommended,
            cost_estimate=cost_estimate or {},
            trigger_evidence=(
                getattr(self, "_current_trigger_report", {}).get(family, {}) or {}
            ).get("evidence", []),
            confidence=self._confidence(family, resource, blast, cost_estimate or {}),
            noise_controls=self._noise_controls(family, blast),
            risk_score=self._risk_score(severity, blast, cost_estimate or {}),
            pattern_detail=pattern_detail,
            business_impact=self._finding_business_impact(blast),
            control_coverage=self._control_coverage(
                resource, family, cost_estimate or {}, pattern_detail
            ),
            incident_chain=self._incident_chain(resource, family, blast),
            assumption_diff=self._assumption_diff(family, resource, excerpt, pattern_detail),
            replay_fidelity=self._replay_fidelity(
                family, resource, blast, cost_estimate or {}, pattern_detail, validation_replay
            ),
            validation_replay=validation_replay,
        )

    def _assumption_diff(
        self, family: str, resource: DbtResource, excerpt: str, pattern_detail: dict[str, Any]
    ) -> dict[str, Any]:
        """Describe old-vs-new assumption drift from PR diff context without claiming replay.

        This is intentionally lightweight: it compares removed/added diff lines and
        maps them to assumption-family semantics. It makes the receipt explain how
        the assumed behavior appears to change, even before behavioral replay exists.
        """
        diff = getattr(self, "_current_changed_diff", "") or ""
        removed_lines = [
            m.group(1).strip()
            for m in DIFF_REMOVED_LINE_PATTERN.finditer(diff)
            if m.group(1).strip()
        ]
        added_lines = [
            m.group(1).strip() for m in DIFF_ADDED_LINE_PATTERN.finditer(diff) if m.group(1).strip()
        ]
        removed_text = "\n".join(removed_lines)
        added_text = "\n".join(added_lines)
        drift_type = "assumption_context_changed"
        old_assumption = (
            "Previous downstream SQL assumption inferred from existing model/query patterns."
        )
        new_assumption = "Changed PR context may alter the operational meaning of that assumption."
        drift_summary = "Assumption-relevant context changed; validate before policy promotion."
        if family == "temporal_bucket":
            drift_type = "temporal_bucket_semantics"
            old_assumption = (
                "Timestamp-to-date bucketing remained stable across reporting boundaries."
            )
            new_assumption = "Timestamp/timezone/date-bucketing semantics may move records across reporting boundaries."
            drift_summary = "Daily/hourly bucket meaning may differ before vs after this PR."
        elif family == "incremental_filter":
            drift_type = "incremental_predicate_selectivity"
            old_assumption = "Incremental predicate stayed selective and preserved pruning."
            new_assumption = "Predicate may be widened, wrapped, OR-expanded, or less selective."
            drift_summary = "Incremental boundary may select more rows or reduce pruning."
        elif family == "join_cardinality":
            drift_type = "join_grain_or_uniqueness"
            old_assumption = "Join keys preserved intended grain and did not amplify rows."
            new_assumption = "Changed join/key context may weaken uniqueness or increase fanout."
            drift_summary = "Join output cardinality may drift even if the query still runs."
        elif family == "enum_domain_closure":
            drift_type = "domain_mapping_coverage"
            old_assumption = "CASE/IN logic covered the meaningful domain values."
            new_assumption = "New or changed values may be unhandled or silently excluded."
            drift_summary = "Semantic meaning of domain values may drift from mapping/filter logic."
        elif family == "null_default_fallback":
            drift_type = "missingness_semantics"
            old_assumption = "Null/default fallback preserved intended missingness semantics."
            new_assumption = "Missing values may be hidden as zero/unknown/default."
            drift_summary = "Null/default meaning may drift without query failure."
        elif family == "materialization_cost":
            drift_type = "bounded_compute_assumption"
            old_assumption = "Model build remained bounded by incremental/materialized behavior."
            new_assumption = (
                "Full-refresh/replace-table/materialization change may rebuild more history."
            )
            drift_summary = (
                "Compute scope may drift from bounded incremental work to broader rebuild."
            )
        return {
            "kind": "semzero_assumption_diff_v1",
            "drift_type": drift_type,
            "old_assumption": old_assumption,
            "new_assumption": new_assumption,
            "drift_summary": drift_summary,
            "removed_context": removed_lines[:5],
            "added_context": added_lines[:5],
            "has_explicit_before_after_diff": bool(removed_lines and added_lines),
            "pattern_type": pattern_detail.get("pattern_type") or family,
            "source_resource": resource.unique_id,
            "evidence_excerpt": excerpt[:280],
            "advisory_note": "Assumption diff is static PR-context evidence, not behavioral replay.",
        }

    def _validation_replay(self, family: str, resource: DbtResource) -> dict[str, Any]:
        """Run targeted local assumption validation when fixture/sample data is supplied.

        This is intentionally not full Wind Tunnel replay. It validates the
        specific assumption family with small supplied samples/precomputed counts
        so CI can attach behavioral evidence without warehouse credentials.
        """
        return run_replay_lite(
            self.replay_fixtures,
            family,
            resource_name=resource.name,
            resource_id=resource.unique_id,
        )

    def _replay_fidelity(
        self,
        family: str,
        resource: DbtResource,
        blast: list[dict[str, Any]],
        cost_estimate: dict[str, Any],
        pattern_detail: dict[str, Any],
        validation_replay: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Score evidence/replay fidelity honestly before full Wind Tunnel Lite exists."""
        score = 0.20
        basis: list[str] = []
        limitations: list[str] = []
        replay_ran = bool((validation_replay or {}).get("replay_ran"))
        if replay_ran:
            score += 0.18
            basis.append("Assumption Validation Replay Lite ran for this finding")
        else:
            limitations.append("No before/after output replay was run for this finding.")
        if resource.compiled_sql:
            score += 0.16
            basis.append("compiled SQL available")
        elif resource.raw_sql:
            score += 0.08
            basis.append("raw SQL available but compiled SQL missing")
        if (resource.artifact_meta or {}).get("catalog"):
            score += 0.10
            basis.append("dbt catalog metadata available")
        if resource.runtime:
            score += 0.10
            basis.append("dbt run_results runtime context available")
        if blast:
            score += 0.12
            basis.append("downstream blast radius attached")
        if cost_estimate.get("history_calibrated"):
            score += 0.14
            basis.append("offline warehouse history matched this model")
        elif cost_estimate.get("estimated_extra_cost_per_run_usd") is not None:
            score += 0.08
            basis.append("cost profile/table-size estimate available")
        trigger_evidence = (getattr(self, "_current_trigger_report", {}).get(family, {}) or {}).get(
            "evidence", []
        )
        if trigger_evidence:
            score += 0.10
            basis.append("why-now trigger evidence available")
        if pattern_detail:
            score += 0.06
            basis.append("family-specific detector detail available")
        if (resource.artifact_meta or {}).get("catalog") is None:
            limitations.append("No dbt catalog metadata for this resource.")
        if not resource.runtime:
            limitations.append("No dbt run_results runtime context for this resource.")
        if not cost_estimate.get("history_calibrated"):
            limitations.append("No offline warehouse-history calibration matched this finding.")
        score = max(0.0, min(0.95, score))
        if score >= 0.78:
            level = "high_static_history_fidelity"
        elif score >= 0.55:
            level = "medium_static_fidelity"
        else:
            level = "low_static_fidelity"
        return {
            "kind": "semzero_replay_fidelity_v1",
            "score": round(score, 2),
            "level": level,
            "coverage": level.replace("_", " "),
            "basis": basis,
            "limitations": limitations[:6],
            "replay_ran": replay_ran,
            "validation_replay_status": (validation_replay or {}).get("status", "not_run"),
            "next_validation": self._replay_next_validation(family),
            "honesty_note": "Fidelity reflects available static/history evidence plus targeted Replay Lite when supplied; Replay Lite is not a full warehouse clone.",
        }

    @staticmethod
    def _replay_next_validation(family: str) -> str:
        return {
            "temporal_bucket": "Run before/after bucket counts by date/timezone and inspect boundary-moving rows.",
            "incremental_filter": "Compare rows selected by old vs new incremental predicates and estimate scan multiplier.",
            "join_cardinality": "Compare join amplification ratio and aggregate deltas before/after.",
            "enum_domain_closure": "Count unhandled/new domain values and CASE/IN exclusion deltas.",
            "null_default_fallback": "Compare null rate and fallback-masked values before/after.",
            "materialization_cost": "Compare rows rebuilt/runtime for bounded incremental vs full rebuild path.",
        }.get(family, "Run targeted before/after semantic validation.")

    @staticmethod
    def _finding_fingerprint(
        family: str, resource: DbtResource, excerpt: str, pattern_detail: dict[str, Any]
    ) -> str:
        pattern_type = str((pattern_detail or {}).get("pattern_type") or family)
        normalized_excerpt = re.sub(r"\s+", " ", (excerpt or "").strip().lower())[:220]
        raw = "|".join([family, resource.unique_id, pattern_type, normalized_excerpt])
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def _temporal_pattern_detail(self, sql: str, excerpt: str) -> dict[str, Any]:
        changed = getattr(self, "_current_changed_diff", "") or ""
        trigger_text = "\n".join((changed, excerpt))
        granularity = None
        m = DATE_TRUNC_PATTERN.search(excerpt)
        if m:
            granularity = m.group(1).lower()
        return {
            "pattern_type": "timezone_or_date_boundary_bucket",
            "granularity": granularity
            or ("day" if re.search(r"\bdate\s*\(", excerpt, re.I) else None),
            "timezone_conversion_in_changed_context": bool(
                TIMEZONE_FUNC_PATTERN.search(trigger_text)
            ),
            "day_boundary_bucket": bool(TEMPORAL_BOUNDARY_TOKEN_PATTERN.search(trigger_text)),
            "boundary_risk": "records near midnight can move between reporting buckets",
        }

    def _incremental_pattern_detail(self, sql: str, excerpt: str) -> dict[str, Any]:
        diff = getattr(self, "_current_changed_diff", "") or ""
        removed = "\n".join(m.group(1) for m in DIFF_REMOVED_LINE_PATTERN.finditer(diff))
        added = "\n".join(m.group(1) for m in DIFF_ADDED_LINE_PATTERN.finditer(diff))
        predicate_removed_or_widened = bool(
            removed
            and INEQUALITY_PREDICATE_PATTERN.search(removed)
            and (
                not INEQUALITY_PREDICATE_PATTERN.search(added) or OR_EXPANSION_PATTERN.search(added)
            )
        )
        return {
            "pattern_type": "incremental_predicate_pruning",
            "partition_column_wrapped": bool(PARTITION_WRAPPER_PATTERN.search(excerpt)),
            "or_expansion": bool(
                OR_EXPANSION_PATTERN.search(excerpt) or OR_EXPANSION_PATTERN.search(added)
            ),
            "predicate_removed_or_widened_in_diff": predicate_removed_or_widened,
            "removed_predicate_excerpt": self._line_excerpt(removed, 0) if removed else "",
            "added_predicate_excerpt": self._line_excerpt(added, 0) if added else "",
        }

    def _join_pattern_detail(self, sql: str, clause: str, unique_tests: bool) -> dict[str, Any]:
        return {
            "pattern_type": "join_grain_or_fanout",
            "equality_join": bool(JOIN_ON_EQUALS_PATTERN.search(clause)),
            "aggregate_after_join": bool(AGGREGATE_AFTER_JOIN_PATTERN.search(sql)),
            "dedup_hint_present": bool(DEDUP_PATTERN.search(sql)),
            "dbt_uniqueness_or_relationship_hint_present": bool(unique_tests),
            "aggregate_after_join_without_uniqueness": bool(
                AGGREGATE_AFTER_JOIN_PATTERN.search(sql) and not unique_tests
            ),
        }

    def _enum_pattern_detail(self, excerpt: str, has_else: bool) -> dict[str, Any]:
        values = re.findall(r"['\"]([^'\"]+)['\"]", excerpt)
        return {
            "pattern_type": "closed_domain_filter_or_mapping",
            "literal_values_seen": values[:12],
            "has_else_branch": has_else,
            "unhandled_value_risk": not has_else,
        }

    def _null_pattern_detail(self, matched: str, excerpt: str) -> dict[str, Any]:
        zero_unknown = bool(
            ZERO_FALLBACK_PATTERN.search(matched) or ZERO_FALLBACK_PATTERN.search(excerpt)
        )
        fallback = ""
        m = COALESCE_PATTERN.search(matched) or COALESCE_PATTERN.search(excerpt)
        if m:
            fallback = m.group(2).strip()[:80]
        return {
            "pattern_type": "null_default_or_missingness_semantics",
            "fallback_value": fallback,
            "zero_or_unknown_fallback": zero_unknown,
            "null_rate_check_required": True,
        }

    def _severity(self, resource: DbtResource, is_downstream: bool, base: str) -> str:
        rank = SEVERITY_RANK[base]
        biz = self._business_criticality_for(resource)
        if biz.get("business_severity") in {
            "BOARD_CRITICAL",
            "EXEC_CRITICAL",
            "REVENUE_CRITICAL",
            "CUSTOMER_FACING",
        }:
            rank += 1
        elif (
            resource.resource_type == "exposure"
            or "finance" in resource.name.lower()
            or "executive" in resource.name.lower()
        ):
            rank += 1
        if is_downstream:
            rank += 1
        rank = min(rank, 4)
        return {v: k for k, v in SEVERITY_RANK.items()}[rank]

    def _blast_radius_refs(self, rid: str) -> list[dict[str, str]]:
        refs = []
        for child_id in sorted(self._transitive_children({rid}, max_depth=4)):
            child = self.resources.get(child_id)
            if child:
                refs.append(self._resource_ref(child))
            if len(refs) >= 20:
                break
        return refs

    def _ancestors_or_self(self, rid: str, max_depth: int = 8) -> set[str]:
        ancestors = {rid}
        frontier = {rid}
        parent_map = {key: set(res.depends_on) for key, res in self.resources.items()}
        for _ in range(max_depth):
            nxt = set()
            for node in frontier:
                nxt |= parent_map.get(node, set())
            nxt -= ancestors
            if not nxt:
                break
            ancestors |= nxt
            frontier = nxt
        return ancestors

    def _resource_ref(self, resource: DbtResource) -> dict[str, str]:
        biz = self._business_criticality_for(resource)
        criticality = (
            "high"
            if biz.get("business_severity")
            in {"BOARD_CRITICAL", "EXEC_CRITICAL", "REVENUE_CRITICAL", "CUSTOMER_FACING"}
            else ""
        )
        node = BlastRadiusNode(
            node_type=f"dbt_{resource.resource_type}",
            name=resource.display_name,
            unique_id=resource.unique_id,
            domain="data",
            path=resource.original_file_path,
            owner=resource.owner,
            criticality=criticality,
            metadata={
                "dbt_resource_type": resource.resource_type,
                "tags": resource.tags,
                "relation_name": resource.relation_name,
                "runtime": resource.runtime,
                "artifact_meta": resource.artifact_meta,
                **biz,
            },
        )
        payload = node.to_dict()
        payload.update({k: v for k, v in biz.items() if k != "business_rank"})
        return payload

    def _business_criticality_for(self, resource: DbtResource) -> dict[str, Any]:
        return infer_business_criticality(
            node_id=resource.unique_id,
            name=resource.display_name,
            node_type=resource.resource_type,
            path=resource.original_file_path,
            owner=resource.owner,
            registry=self.criticality_registry,
        )

    def _has_uniqueness_hint(self, resource: DbtResource) -> bool:
        test_blob = "\n".join(
            (test.name + " " + test.raw_sql + " " + test.compiled_sql).lower()
            for test in self.tests_by_parent.get(resource.unique_id, [])
        )
        blob = (
            json.dumps(resource.columns, default=str).lower()
            + "\n"
            + resource.sql.lower()
            + "\n"
            + test_blob
        )
        return any(
            token in blob
            for token in (
                "unique",
                "relationships",
                "relationship",
                "dbt_utils.unique_combination_of_columns",
                "row_number()",
            )
        )

    def _cost_estimate(
        self, resource: DbtResource, wrapped_partition: bool, risk_kind: str = "incremental_filter"
    ) -> dict[str, Any]:
        raw_size = (
            self.table_sizes.get(resource.name) or self.table_sizes.get(resource.unique_id) or {}
        )
        profile = self._cost_profile_for(resource)
        history = profile_for_resource(
            self.warehouse_history,
            unique_id=resource.unique_id,
            name=resource.name,
            relation_name=resource.relation_name,
            path=resource.original_file_path,
        )
        if isinstance(raw_size, (int, float)):
            gb = float(raw_size)
        elif isinstance(raw_size, dict):
            gb = float(
                raw_size.get("gb") or raw_size.get("size_gb") or raw_size.get("table_size_gb") or 0
            )
        else:
            gb = 0.0
        if not gb:
            gb = (
                float(profile.get("table_size_gb") or profile.get("gb") or 0)
                if isinstance(profile, dict)
                else 0.0
            )
        if not gb and isinstance(history, dict) and history.get("avg_bytes_scanned"):
            gb = round(float(history.get("avg_bytes_scanned")) / (1024.0**3), 4)

        engine = str(
            (profile.get("engine") if isinstance(profile, dict) else "")
            or history.get("engine")
            or "generic_dbt"
        ).lower()
        frequency = self._monthly_frequency(
            profile.get("run_frequency")
            or profile.get("frequency")
            or profile.get("run_frequency_per_month")
            if isinstance(profile, dict)
            else None
        )
        multiplier = (
            10.0
            if (wrapped_partition and engine in {"snowflake", "databricks"})
            else (8.0 if wrapped_partition else 3.0)
        )
        if risk_kind == "materialization_cost":
            multiplier = max(multiplier, 12.0 if engine in {"snowflake", "databricks"} else 6.0)

        base_per_run = (
            profile.get("rough_cost_per_run_usd") or profile.get("cost_per_run_usd")
            if isinstance(profile, dict)
            else None
        )
        history_cost = history.get("avg_cost_usd") if isinstance(history, dict) else None
        if base_per_run is not None:
            dollars = round(float(base_per_run) * max(0.5, multiplier - 1), 2)
            method = "cost_profile_per_run_multiplier"
        elif history_cost is not None:
            dollars = round(float(history_cost) * max(0.5, multiplier - 1), 2)
            method = "offline_history_per_run_multiplier"
        else:
            rate = (
                profile.get("rough_cost_per_tb_scanned_usd") if isinstance(profile, dict) else None
            )
            if rate is None:
                rate = 24.0 if engine == "snowflake" else (18.0 if engine == "databricks" else 20.0)
            dollars = round((gb / 1024.0) * multiplier * float(rate), 2) if gb else None
            method = "table_size_scan_multiplier"
        monthly = round(dollars * frequency, 2) if dollars is not None and frequency else None
        engine_notes = {
            "snowflake": "Snowflake: review query history, micro-partition pruning, warehouse runtime, bytes scanned, and credits for the changed model.",
            "databricks": "Databricks: review job run history, Delta file pruning, Spark scan/shuffle metrics, DBU/runtime, and MERGE bounds.",
            "dbt": "dbt: review materialization, incremental predicate selectivity, run_results timing, and downstream model fanout.",
            "generic_dbt": "dbt: review materialization, incremental predicate selectivity, run_results timing, and downstream model fanout.",
        }
        confidence = "low"
        if history:
            confidence = "high" if history.get("confidence") == "high" else "medium"
        elif wrapped_partition or profile:
            confidence = "medium"
        return {
            "kind": "warehouse_aware_rough_cost_risk",
            "risk_kind": risk_kind,
            "engine": engine,
            "method": method,
            "confidence": confidence,
            "scan_multiplier": f"~{int(multiplier)}x",
            "table_size_gb": gb or None,
            "run_frequency_per_month": frequency,
            "estimated_extra_cost_per_run_usd": dollars,
            "estimated_extra_cost_per_month_usd": monthly,
            "warehouse_history": history or None,
            "history_calibrated": bool(history),
            "engine_note": engine_notes.get(engine, engine_notes["generic_dbt"]),
            "note": "Directional estimate only; offline query/job history improves calibration but remains advisory until validated against billing.",
        }

    def _cost_profile_for(self, resource: DbtResource) -> dict[str, Any]:
        profiles = self.cost_profiles or {}
        if not isinstance(profiles, dict):
            return {}
        models = profiles.get("models") if isinstance(profiles.get("models"), dict) else profiles
        candidates = [
            resource.unique_id,
            resource.name,
            resource.original_file_path,
            resource.display_name,
        ]
        for key in candidates:
            if key and isinstance(models.get(key), dict):
                return dict(models[key])
        return {}

    @staticmethod
    def _monthly_frequency(value: Any) -> float:
        if value is None or value == "":
            return 1.0
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value).strip().lower()
        mapping = {"hourly": 24 * 30, "daily": 30, "weekly": 4.3, "monthly": 1, "nightly": 30}
        if text in mapping:
            return float(mapping[text])
        try:
            return float(text)
        except Exception:
            return 1.0

    def _finding_business_impact(self, blast: list[dict[str, str]]) -> dict[str, Any]:
        return summarize_business_impact([{"blast_radius": blast}])

    def _control_coverage(
        self,
        resource: DbtResource,
        family: str,
        cost_estimate: dict[str, Any],
        pattern_detail: dict[str, Any],
    ) -> dict[str, Any]:
        present: list[str] = []
        missing: list[str] = []
        if self.tests_by_parent.get(resource.unique_id) or self._has_uniqueness_hint(resource):
            present.append("dbt test or uniqueness/relationship hint")
        elif family == "join_cardinality":
            missing.append("dbt unique/relationship test for join grain")
        if cost_estimate.get("estimated_extra_cost_per_month_usd") is not None:
            present.append("warehouse-aware cost profile")
        elif family in {"incremental_filter", "materialization_cost"}:
            missing.append("cost profile for monthly exposure calibration")
        if pattern_detail.get("dedup_hint_present"):
            present.append("dedup hint in SQL")
        if family == "enum_domain_closure" and not pattern_detail.get("has_else_branch", True):
            missing.append("explicit ELSE branch for new domain values")
        if family == "null_default_fallback":
            missing.append("before/after null-rate control check")
        if family == "temporal_bucket":
            missing.append("before/after temporal bucket comparison")
        status = "covered" if present and not missing else ("partial" if present else "weak")
        return {
            "kind": "assumption_control_coverage_v1",
            "status": status,
            "present_controls": present,
            "missing_controls": missing,
            "experimental_note": "Cybersec/DevOps-inspired control coverage: advisory-only, used to improve calibration without blocking.",
        }

    def _incident_chain(
        self, resource: DbtResource, family: str, blast: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        chain = [
            {
                "step": 1,
                "kind": "changed_or_scanned_resource",
                "node": resource.unique_id,
                "label": resource.display_name,
            },
            {"step": 2, "kind": "hidden_assumption", "family": family},
        ]
        for idx, node in enumerate(blast[:4], start=3):
            chain.append(
                {
                    "step": idx,
                    "kind": "blast_radius_node",
                    "node": node.get("unique_id") or node.get("name"),
                    "business_severity": node.get("business_severity", "UNKNOWN"),
                }
            )
        return chain

    def _apply_exceptions(self, findings: list[AssumptionFindingV1], receipt_key: str = "") -> None:
        for finding in findings:
            finding.exception = (
                match_exception(finding.to_dict(), receipt_key, self.exceptions)
                if self.exceptions
                else {
                    "kind": "semzero_assumption_exception_match_v1",
                    "state": "none",
                    "active": [],
                    "expired": [],
                    "advisory_note": "No exception ledger supplied.",
                }
            )
            if finding.exception.get("state") == "active_exception":
                finding.noise_controls.append(
                    "active accepted-risk/suppression exception exists; finding remains visible for audit but should not be escalated without human review"
                )
            elif finding.exception.get("state") == "expired_exception":
                finding.noise_controls.append(
                    "expired exception matched this finding; review whether risk is still accepted"
                )

    def _exception_summary(self, findings: list[AssumptionFindingV1]) -> dict[str, Any]:
        active = sum(1 for f in findings if (f.exception or {}).get("state") == "active_exception")
        expired = sum(
            1 for f in findings if (f.exception or {}).get("state") == "expired_exception"
        )
        return {
            "active_exception_match_count": active,
            "expired_exception_match_count": expired,
            "supplied_exception_count": len(self.exceptions),
            "guardrail": "Exceptions are advisory accepted-risk/suppression annotations; findings remain in the receipt.",
        }

    def _deduplicate_findings(
        self, findings: list[AssumptionFindingV1]
    ) -> list[AssumptionFindingV1]:
        dedup: dict[tuple[str, str, str], AssumptionFindingV1] = {}
        for finding in findings:
            key = (
                finding.family,
                finding.source_resource,
                finding.fingerprint or finding.evidence_excerpt,
            )
            existing = dedup.get(key)
            if not existing or SEVERITY_RANK[finding.severity] > SEVERITY_RANK[existing.severity]:
                dedup[key] = finding
        return sorted(
            dedup.values(),
            key=lambda f: (
                -SEVERITY_RANK[f.severity],
                FAMILY_ORDER.get(f.family, 99),
                f.source_resource,
                f.evidence_excerpt,
            ),
        )

    def _verdict(self, findings: list[AssumptionFindingV1]) -> str:
        if any(f.severity == "critical" for f in findings):
            return "REQUIRE_REVIEW"
        if any(f.severity == "high" for f in findings):
            return "REQUIRE_REVIEW"
        if findings:
            return "ADVISORY"
        return "ALLOW"

    def _summary(
        self,
        findings: list[AssumptionFindingV1],
        changed_ids: set[str],
        blast_ids: set[str],
        trigger_report: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        family_counts: dict[str, int] = {}
        severity_counts: dict[str, int] = {}
        cost_total = 0.0
        cost_monthly_total = 0.0
        has_cost = False
        has_monthly_cost = False
        risk_score_total = 0
        confidence_counts: dict[str, int] = {}
        for f in findings:
            family_counts[f.family] = family_counts.get(f.family, 0) + 1
            severity_counts[f.severity] = severity_counts.get(f.severity, 0) + 1
            risk_score_total += f.risk_score
            confidence_counts[f.confidence] = confidence_counts.get(f.confidence, 0) + 1
            cost = (
                f.cost_estimate.get("estimated_extra_cost_per_run_usd") if f.cost_estimate else None
            )
            if isinstance(cost, (int, float)):
                has_cost = True
                cost_total += float(cost)
            monthly_cost = (
                f.cost_estimate.get("estimated_extra_cost_per_month_usd")
                if f.cost_estimate
                else None
            )
            if isinstance(monthly_cost, (int, float)):
                has_monthly_cost = True
                cost_monthly_total += float(monthly_cost)
        findings_dict = [f.to_dict() for f in findings]
        business_impact = summarize_business_impact(findings_dict)
        control_counts: dict[str, int] = {}
        for f in findings:
            status = str((f.control_coverage or {}).get("status") or "unknown")
            control_counts[status] = control_counts.get(status, 0) + 1
        return {
            "finding_count": len(findings),
            "families": family_counts,
            "severity_counts": severity_counts,
            "changed_resource_count": len(changed_ids),
            "blast_radius_resource_count": len(blast_ids),
            "estimated_extra_cost_per_run_usd": round(cost_total, 2) if has_cost else None,
            "estimated_extra_cost_per_month_usd": round(cost_monthly_total, 2)
            if has_monthly_cost
            else None,
            "risk_score_total": risk_score_total,
            "confidence_counts": confidence_counts,
            "active_triggers": {
                k: v.get("evidence", [])[:3]
                for k, v in (trigger_report or {}).items()
                if v.get("active")
            },
            "top_findings": findings_dict[:5],
            "business_impact": business_impact,
            "control_coverage_counts": control_counts,
            "exception_summary": self._exception_summary(findings),
            "assumption_diff_summary": self._assumption_diff_summary(findings),
            "replay_fidelity_summary": self._replay_fidelity_summary(findings),
            "validation_replay_summary": self._validation_replay_summary(findings),
            "domains": {"data": len(findings)},
            "adapters": {"dbt_assumption_gate": len(findings)},
            "design_scope": "core_only_adapter_ready",
            "detector_version": "assumption_gate_core_v1_25",
            "dbt_artifact_context": self._artifact_context_summary(),
        }

    def _assumption_diff_summary(self, findings: list[AssumptionFindingV1]) -> dict[str, Any]:
        by_type: dict[str, int] = {}
        explicit = 0
        for f in findings:
            d = f.assumption_diff or {}
            typ = str(d.get("drift_type") or "unknown")
            by_type[typ] = by_type.get(typ, 0) + 1
            if d.get("has_explicit_before_after_diff"):
                explicit += 1
        return {
            "kind": "semzero_assumption_diff_summary_v1",
            "finding_count": len(findings),
            "explicit_before_after_diff_count": explicit,
            "drift_type_counts": by_type,
            "note": "Static assumption diffing explains old-vs-new assumed behavior; behavioral replay is not yet run.",
        }

    def _validation_replay_summary(self, findings: list[AssumptionFindingV1]) -> dict[str, Any]:
        by_status: dict[str, int] = {}
        by_family: dict[str, int] = {}
        drift_detected = 0
        replay_ran = 0
        examples: list[dict[str, Any]] = []
        for f in findings:
            vr = f.validation_replay or {}
            status = str(vr.get("status") or "not_run")
            by_status[status] = by_status.get(status, 0) + 1
            if vr.get("replay_ran"):
                replay_ran += 1
                by_family[f.family] = by_family.get(f.family, 0) + 1
            if status == "drift_detected":
                drift_detected += 1
                if len(examples) < 5:
                    examples.append(
                        {
                            "stable_id": f.stable_id,
                            "family": f.family,
                            "summary": vr.get("summary"),
                            "drift_metric": vr.get("drift_metric"),
                            "drift_unit": vr.get("drift_unit"),
                        }
                    )
        return {
            "kind": "semzero_assumption_validation_replay_lite_summary_v1",
            "finding_count": len(findings),
            "replay_ran_count": replay_ran,
            "drift_detected_count": drift_detected,
            "status_counts": by_status,
            "family_replay_counts": by_family,
            "examples": examples,
            "honesty_note": "Replay Lite validates targeted assumptions from supplied local fixture/sample data; it is not full warehouse replay.",
        }

    def _replay_fidelity_summary(self, findings: list[AssumptionFindingV1]) -> dict[str, Any]:
        if not findings:
            return {
                "kind": "semzero_replay_fidelity_summary_v1",
                "average_score": None,
                "level_counts": {},
                "replay_ran_count": 0,
            }
        scores = []
        levels: dict[str, int] = {}
        replay_ran = 0
        for f in findings:
            rf = f.replay_fidelity or {}
            if isinstance(rf.get("score"), (int, float)):
                scores.append(float(rf["score"]))
            level = str(rf.get("level") or "unknown")
            levels[level] = levels.get(level, 0) + 1
            if rf.get("replay_ran"):
                replay_ran += 1
        avg = round(sum(scores) / len(scores), 2) if scores else None
        return {
            "kind": "semzero_replay_fidelity_summary_v1",
            "average_score": avg,
            "level_counts": levels,
            "replay_ran_count": replay_ran,
            "finding_count": len(findings),
            "honesty_note": "Scores reflect evidence quality and replay readiness; v1.20 does not run behavioral replay.",
        }

    def _artifact_context_summary(self) -> dict[str, Any]:
        resources = list(self.resources.values())
        return {
            "kind": "dbt_artifact_context_v1",
            "resource_count": len(resources),
            "compiled_sql_resource_count": sum(1 for r in resources if bool(r.compiled_sql)),
            "raw_sql_resource_count": sum(1 for r in resources if bool(r.raw_sql)),
            "catalog_enriched_resource_count": sum(
                1 for r in resources if bool((r.artifact_meta or {}).get("catalog"))
            ),
            "run_results_enriched_resource_count": sum(1 for r in resources if bool(r.runtime)),
            "offline_warehouse_history_profile_count": len(self.warehouse_history or {}),
            "warehouse_history_matched_resource_count": sum(
                1
                for r in resources
                if bool(
                    profile_for_resource(
                        self.warehouse_history,
                        unique_id=r.unique_id,
                        name=r.name,
                        relation_name=r.relation_name,
                        path=r.original_file_path,
                    )
                )
            ),
            "test_resource_count": sum(
                1
                for r in resources
                if r.resource_type in {"test", "unit_test"} or r.unique_id.startswith("test.")
            ),
            "exposure_resource_count": sum(1 for r in resources if r.resource_type == "exposure"),
            "owner_tagged_resource_count": sum(1 for r in resources if bool(r.owner)),
            "warnings": self.artifact_warnings[:20],
            "precision_note": "Real dbt manifest/catalog/run_results plus offline warehouse history exports improve blast radius, control coverage, and cost calibration without live warehouse credentials.",
        }

    def _confidence(
        self,
        family: str,
        resource: DbtResource,
        blast: list[dict[str, str]],
        cost_estimate: dict[str, Any],
    ) -> str:
        if family == "incremental_filter" and cost_estimate.get("confidence") == "medium":
            return "high"
        if any(
            item.get("criticality") == "high"
            or item.get("business_severity")
            in {"BOARD_CRITICAL", "EXEC_CRITICAL", "REVENUE_CRITICAL", "CUSTOMER_FACING"}
            for item in blast
        ):
            return "high"
        if self.tests_by_parent.get(resource.unique_id):
            return "medium"
        return "medium" if blast else "low"

    def _noise_controls(self, family: str, blast: list[dict[str, str]]) -> list[str]:
        controls = [
            "pattern emitted only because a related changed-resource trigger was present",
            "finding is tied to changed resource or transitive downstream resource",
        ]
        if blast:
            controls.append("blast radius attached; finding is not a standalone SQL lint warning")
        else:
            controls.append(
                "no downstream blast radius found; severity should be interpreted conservatively"
            )
        if family == "join_cardinality":
            controls.append(
                "join finding checks for equality joins and uniqueness/relationship hints"
            )
        if family == "incremental_filter":
            controls.append(
                "cost estimate is rough and labelled directional until warehouse metadata is connected"
            )
        return controls

    def _risk_score(
        self, severity: str, blast: list[dict[str, str]], cost_estimate: dict[str, Any]
    ) -> int:
        score = SEVERITY_RANK.get(severity, 1) * 25
        score += min(len(blast), 5) * 5
        if any(
            item.get("criticality") == "high"
            or item.get("business_severity")
            in {"BOARD_CRITICAL", "EXEC_CRITICAL", "REVENUE_CRITICAL", "CUSTOMER_FACING"}
            for item in blast
        ):
            score += 15
        if isinstance(cost_estimate.get("estimated_extra_cost_per_run_usd"), (int, float)):
            score += 10
        return min(score, 100)

    @staticmethod
    def _line_excerpt(text: str, index: int, radius: int = 180) -> str:
        start = max(0, index - radius)
        end = min(len(text), index + radius)
        excerpt = text[start:end]
        return re.sub(r"\s+", " ", excerpt).strip()

    @staticmethod
    def _norm_path(path: str | Path) -> str:
        return str(path).replace("\\", "/").lstrip("./")

    @staticmethod
    def _version() -> str:
        try:
            from semzero.version import SEMZERO_VERSION

            return SEMZERO_VERSION
        except Exception:
            return "unknown"


def _finding_exception_state(finding: AssumptionFindingV1) -> str:
    exc = finding.exception or {}
    return str(exc.get("state") or "none")


def _finding_business_severity(finding: AssumptionFindingV1) -> str:
    return str((finding.business_impact or {}).get("highest_business_severity") or "UNKNOWN")


def _finding_monthly_cost(finding: AssumptionFindingV1) -> float:
    value = (finding.cost_estimate or {}).get("estimated_extra_cost_per_month_usd")
    try:
        return float(value or 0.0)
    except Exception:
        return 0.0


def _is_must_review_finding(finding: AssumptionFindingV1) -> bool:
    if _finding_exception_state(finding) == "active_exception":
        return False
    business = _finding_business_severity(finding)
    return (
        finding.severity in {"high", "critical"}
        or finding.risk_score >= 80
        or business in {"BOARD_CRITICAL", "EXEC_CRITICAL", "REVENUE_CRITICAL", "CUSTOMER_FACING"}
        or _finding_monthly_cost(finding) > 0
    )



def _comment_group_key(finding: AssumptionFindingV1) -> tuple[str, str, str, str, str]:
    """Group repeated-looking findings for compact PR comments.

    The JSON receipt remains exhaustive. This only reduces reviewer fatigue in the
    sticky GitHub comment.
    """
    detector = str(getattr(finding, "detector", "") or "")
    family = str(getattr(finding, "assumption_family", "") or getattr(finding, "finding_type", "") or "")
    reviewer_check = str(getattr(finding, "reviewer_check", "") or "")
    blast = _finding_blast_summary(finding, limit=8)
    evidence = str(getattr(finding, "why_now", "") or getattr(finding, "evidence", "") or "")[:180]
    return (detector, family, reviewer_check, blast, evidence)


def _group_comment_findings(findings: list[AssumptionFindingV1]) -> list[list[AssumptionFindingV1]]:
    grouped: dict[tuple[str, str, str, str, str], list[AssumptionFindingV1]] = {}
    for finding in findings:
        grouped.setdefault(_comment_group_key(finding), []).append(finding)

    groups = list(grouped.values())

    def _sort_key(group: list[AssumptionFindingV1]) -> tuple[int, float, int]:
        top = group[0]
        severity_rank = {
            "critical": 0,
            "high": 1,
            "medium": 2,
            "low": 3,
            "info": 4,
        }.get(str(getattr(top, "severity", "") or "").lower(), 5)
        risk = float(getattr(top, "risk_score", 0) or 0)
        return (severity_rank, -risk, -len(group))

    groups.sort(key=_sort_key)
    return groups


def _comment_display_severity(finding: AssumptionFindingV1) -> str:
    severity = str(getattr(finding, "severity", "") or "review").lower()
    confidence = str(getattr(finding, "confidence", "") or "").lower()
    blast = _finding_blast_summary(finding, limit=3)
    business = str(getattr(finding, "business_impact", "") or getattr(finding, "business_criticality", "") or "").upper()

    low_confidence = confidence in {"low", "weak"}
    no_blast = "No downstream resources found" in blast
    unknown_business = not business or business == "UNKNOWN"

    if low_confidence and no_blast and unknown_business and severity in {"critical", "high"}:
        return "potential high"
    return severity


def _clean_comment_snippet(raw: str, max_chars: int = 220) -> str:
    """Return a reviewer-readable evidence snippet.

    Avoids starting/ending mid-token where possible and makes truncation explicit.
    This is PR-comment polish only; raw evidence remains preserved in the JSON receipt.
    """
    text = " ".join(str(raw or "").replace("\n", " ").split())
    if not text:
        return ""

    # Prefer starting at a useful SQL-ish boundary instead of mid-token snippets like "ders as".
    lowered = text.lower()
    boundary_candidates = [
        "with ",
        "select ",
        "from ",
        "join ",
        "orders as",
        "customers as",
        "customer_orders",
        "payments as",
    ]
    start = 0
    for marker in boundary_candidates:
        idx = lowered.find(marker)
        if 0 <= idx <= 80:
            start = idx
            break

    prefix = "… " if start > 0 else ""
    text = text[start:]

    if len(text) <= max_chars:
        return prefix + text

    cut = text[:max_chars]
    # Avoid ending mid-token.
    last_space = cut.rfind(" ")
    if last_space >= int(max_chars * 0.65):
        cut = cut[:last_space]

    return prefix + cut.rstrip(" ,.;:") + "…"



def _comment_why_now(finding: AssumptionFindingV1) -> str:
    explicit = bool(getattr(finding, "explicit_before_after_context", False) or getattr(finding, "has_explicit_diff", False))
    raw = str(getattr(finding, "why_now", "") or "").strip()

    if explicit and raw:
        return raw

    if raw:
        snippet = _clean_comment_snippet(raw)
        return (
            "This PR touched a dbt resource connected to this assumption. "
            "Explicit before/after semantic diff was not available, so SemZero used "
            f"changed-resource reachability plus static detector evidence. Evidence snippet: {snippet}"
        )

    return (
        "This PR touched a dbt resource connected to this assumption. "
        "SemZero used PR diff context, changed-resource reachability, and static detector evidence."
    )


def _render_comment_finding_group(group: list[AssumptionFindingV1]) -> list[str]:
    finding = group[0]
    title = str(getattr(finding, "title", "") or getattr(finding, "assumption_family", "") or "Assumption finding")
    severity = _comment_display_severity(finding)
    confidence = str(getattr(finding, "confidence", "") or "unknown")
    risk = getattr(finding, "risk_score", None)
    risk_txt = f"{risk}/100" if risk is not None else "unknown"

    suffix = f" · {len(group)} related findings grouped" if len(group) > 1 else ""
    lines = [
        f"#### {title} — {severity} · confidence {confidence} · risk {risk_txt}{suffix}",
        "",
    ]

    if severity.startswith("potential"):
        lines += [
            "_Low-confidence/no-blast finding. Review as a possible assumption, not as a proven breakage._",
            "",
        ]

    lines += [
        f"**Why now:** {_comment_why_now(finding)}",
        "",
    ]

    drift = str(getattr(finding, "assumption_drift", "") or getattr(finding, "description", "") or "").strip()
    if drift:
        lines += [f"**Assumption drift:** {drift}", ""]

    fidelity = getattr(finding, "evidence_fidelity", None)
    fidelity_band = str(getattr(finding, "evidence_fidelity_band", "") or "").strip()
    replay_ran = getattr(finding, "replay_ran", False)
    if fidelity is not None:
        band = f" ({fidelity_band})" if fidelity_band else ""
        lines += [f"**Evidence fidelity:** {fidelity}{band} · replay ran: {replay_ran}", ""]

    validation_status = str(getattr(finding, "validation_replay_status", "") or "").strip()
    validation_reason = str(getattr(finding, "validation_replay_reason", "") or "").strip()
    if validation_status or validation_reason:
        joined = " · ".join(x for x in [validation_status, validation_reason] if x)
        lines += [f"**Validation replay:** {joined}", ""]

    blast = _finding_blast_summary(finding)
    business = str(getattr(finding, "business_impact", "") or getattr(finding, "business_criticality", "") or "UNKNOWN")
    coverage = str(getattr(finding, "control_coverage", "") or "unknown")
    detector = str(getattr(finding, "detector", "") or "unknown")
    lines += [
        f"**Blast radius:** {blast}",
        f"**Business:** {business} · Control coverage: {coverage} · Detector: `{detector}`",
    ]

    ids = [str(getattr(f, "stable_id", "") or getattr(f, "finding_id", "")) for f in group]
    ids = [x for x in ids if x]
    if ids:
        label = "Stable IDs" if len(ids) > 1 else "Stable ID"
        lines += [f"**{label}:** " + ", ".join(f"`{x}`" for x in ids[:6])]

    reviewer_check = str(getattr(finding, "reviewer_check", "") or "").strip()
    if reviewer_check:
        lines += [f"**Reviewer check:** {reviewer_check}"]

    lines += [""]
    return lines


def _finding_blast_summary(finding: AssumptionFindingV1, limit: int = 3) -> str:
    nodes = finding.blast_radius[:limit]
    if not nodes:
        return "No downstream resources found"
    chunks: list[str] = []
    for item in nodes:
        node_type = item.get("node_type") or item.get("type") or "node"
        name = item.get("name") or item.get("unique_id") or "unknown"
        biz = item.get("business_severity") or item.get("metadata", {}).get("business_severity")
        if biz:
            chunks.append(f"{node_type} `{name}` ({biz})")
        else:
            chunks.append(f"{node_type} `{name}`")
    extra = len(finding.blast_radius) - len(nodes)
    if extra > 0:
        chunks.append(f"+{extra} more")
    return ", ".join(chunks)


def _comment_group_key(finding: AssumptionFindingV1) -> tuple[str, str, str, str, str]:
    """Group repeated-looking findings for compact PR comments.

    The JSON receipt remains exhaustive. This only reduces reviewer fatigue in the
    sticky GitHub comment.
    """
    detector = (finding.pattern_detail or {}).get("pattern_type", finding.family)
    business = _finding_business_severity(finding)
    control = (finding.control_coverage or {}).get("status", "unknown")
    blast = _finding_blast_summary(finding, limit=8)
    drift = (finding.assumption_diff or {}).get(
        "drift_summary"
    ) or "Assumption-relevant behavior may have changed."
    reviewer_check = finding.recommended_check or ""
    return (
        str(finding.family),
        str(detector),
        str(business),
        str(control),
        str(blast),
        str(drift),
        str(reviewer_check),
    )


def _group_comment_findings(findings: list[AssumptionFindingV1]) -> list[list[AssumptionFindingV1]]:
    grouped: dict[tuple[str, str, str, str, str], list[AssumptionFindingV1]] = {}
    for finding in findings:
        grouped.setdefault(_comment_group_key(finding), []).append(finding)

    groups = list(grouped.values())

    def _sort_key(group: list[AssumptionFindingV1]) -> tuple[int, int, int]:
        top = group[0]
        return (
            0 if _is_must_review_finding(top) else 1,
            -SEVERITY_RANK.get(top.severity, 0),
            -int(top.risk_score or 0),
        )

    groups.sort(key=_sort_key)
    return groups


def _comment_display_severity(finding: AssumptionFindingV1) -> str:
    severity = str(finding.severity or "review").lower()
    confidence = str(finding.confidence or "").lower()
    business = _finding_business_severity(finding)
    blast = _finding_blast_summary(finding)

    low_confidence = confidence in {"low", "weak"}
    no_blast = blast == "No downstream resources found"
    unknown_business = business == "UNKNOWN"

    if low_confidence and no_blast and unknown_business and severity in {"critical", "high"}:
        return "potential high"
    return severity


def _comment_why_now(finding: AssumptionFindingV1) -> str:
    raw = (finding.trigger_evidence[0] if finding.trigger_evidence else finding.trigger or "").strip()
    has_explicit = bool((finding.assumption_diff or {}).get("explicit_before_after_context"))

    if has_explicit and raw:
        return raw[:320]

    if raw:
        snippet = _clean_comment_snippet(raw)
        return (
            "This PR touched a dbt resource connected to this assumption. "
            "Explicit before/after semantic diff was not available, so SemZero used "
            f"changed-resource reachability plus static detector evidence. Evidence snippet: {snippet}"
        )

    return (
        "This PR touched a dbt resource connected to this assumption. "
        "SemZero used PR diff context, changed-resource reachability, and static detector evidence."
    )



def _comment_business_weight(finding: AssumptionFindingV1) -> float:
    business = str(_finding_business_severity(finding) or "").upper()
    mapping = {
        "REVENUE_CRITICAL": 1.00,
        "FINANCE_CRITICAL": 1.00,
        "CUSTOMER_FACING": 0.80,
        "CUSTOMER_CRITICAL": 0.80,
        "OPERATIONAL": 0.60,
        "INTERNAL_ONLY": 0.40,
        "UNKNOWN_CONFIRMED": 0.35,
        "UNKNOWN": 0.30,
        "NONE": 0.30,
        "": 0.30,
    }
    return mapping.get(business, 0.60)


def _comment_downstream_count(finding: AssumptionFindingV1) -> int:
    summary = _finding_blast_summary(finding)
    if not summary or summary == "No downstream resources found":
        return 0
    if "+1 more" in summary:
        # Approximate safely from rendered summary: three visible plus one or more.
        return max(4, summary.count(",") + 1)
    if "+" in summary and "more" in summary:
        return max(4, summary.count(",") + 1)
    return max(1, summary.count(",") + 1)


def _comment_blast_weight(finding: AssumptionFindingV1, max_expected_downstream: int = 50) -> float:
    import math

    downstream_count = _comment_downstream_count(finding)
    if downstream_count <= 0:
        # Do not collapse local model risk to zero when lineage is weak/incomplete.
        return 0.10

    return min(
        1.0,
        math.log10(downstream_count + 1) / math.log10(max_expected_downstream + 1),
    )


def _comment_fidelity_tier_weight(finding: AssumptionFindingV1) -> tuple[str, float]:
    """Return human tier label and scoring weight.

    Tier 3: replay/fixture validation available.
    Tier 2: explicit before/after semantic diff or supplied manifest-level evidence.
    Tier 1: manifest generated/lineage available in CI with static evidence.
    Tier 0: static fallback only.
    """
    validation = finding.validation_replay or {}
    replay = finding.replay_fidelity or {}
    assumption_diff = finding.assumption_diff or {}

    validation_status = str(validation.get("status") or "").lower()
    replay_ran = bool(replay.get("replay_ran"))

    if validation_status in {"passed", "validated", "drift_detected"} or replay_ran:
        return "Tier 3", 1.00

    explicit_diff = bool(
        assumption_diff.get("explicit_before_after")
        or assumption_diff.get("before_after_available")
        or assumption_diff.get("diff_summary")
        or assumption_diff.get("drift_summary")
    )
    if explicit_diff:
        return "Tier 2", 0.80

    fidelity_level = str(replay.get("level") or "").lower()
    score = replay.get("score")
    if score is not None or "static" in fidelity_level:
        return "Tier 1", 0.65

    return "Tier 0", 0.40


def _comment_detection_confidence_weight(finding: AssumptionFindingV1) -> float:
    confidence = str(getattr(finding, "confidence", "") or "").lower()
    if confidence == "high":
        return 1.00
    if confidence == "medium":
        return 0.65
    if confidence == "low":
        return 0.35
    return 0.50


def _comment_replay_weight(finding: AssumptionFindingV1) -> tuple[str, float]:
    validation = finding.validation_replay or {}
    replay = finding.replay_fidelity or {}
    status = str(validation.get("status") or "").lower()

    if status in {"passed", "validated", "drift_detected"} or bool(replay.get("replay_ran")):
        return "validated", 1.00
    if status in {"failed", "failed_to_confirm"}:
        return "failed_to_confirm", 0.60
    if status in {"error", "unavailable"}:
        return "attempted_unavailable", 0.75
    return "not_run", 0.85


def _comment_review_priority_breakdown(finding: AssumptionFindingV1) -> dict[str, object]:
    raw_detector_risk = int(getattr(finding, "risk_score", 0) or 0)

    blast_weight = _comment_blast_weight(finding)
    business_weight = _comment_business_weight(finding)
    impact_score = blast_weight * business_weight

    fidelity_tier, fidelity_weight = _comment_fidelity_tier_weight(finding)
    detection_weight = _comment_detection_confidence_weight(finding)
    replay_status, replay_weight = _comment_replay_weight(finding)
    evidence_score = fidelity_weight * detection_weight * replay_weight

    raw_score = round(100 * impact_score * evidence_score)

    caps = []
    floors = []
    score = raw_score

    downstream_count = _comment_downstream_count(finding)
    confidence = str(getattr(finding, "confidence", "") or "").lower()
    business = str(_finding_business_severity(finding) or "").upper()

    # Hard ceilings: evidence must earn high scores.
    if fidelity_tier in {"Tier 0", "Tier 1"} and score > 80:
        score = 80
        caps.append("tier_below_2_cap_80")

    if downstream_count == 0 and score > 60:
        score = 60
        caps.append("no_confirmed_blast_cap_60")

    if confidence == "low" and score > 50:
        score = 50
        caps.append("low_confidence_cap_50")

    if confidence == "low" and downstream_count == 0 and score > 35:
        score = 35
        caps.append("low_confidence_no_blast_cap_35")

    # Product floors: do not make important confirmed reviewer work look trivial.
    if (
        business in {"REVENUE_CRITICAL", "FINANCE_CRITICAL"}
        and confidence == "high"
        and downstream_count > 0
        and score < 45
    ):
        score = 45
        floors.append("revenue_high_confidence_blast_floor_45")
    elif (
        business in {"CUSTOMER_FACING", "CUSTOMER_CRITICAL"}
        and confidence == "high"
        and downstream_count > 0
        and score < 35
    ):
        score = 35
        floors.append("customer_high_confidence_blast_floor_35")
    elif (
        business in {"REVENUE_CRITICAL", "FINANCE_CRITICAL"}
        and confidence == "high"
        and score < 35
    ):
        score = 35
        floors.append("revenue_high_confidence_floor_35")

    score = max(0, min(100, int(score)))

    if score >= 80:
        band = "block candidate"
    elif score >= 65:
        band = "review strongly recommended"
    elif score >= 45:
        band = "review recommended"
    elif score >= 25:
        band = "advisory"
    else:
        band = "informational"

    return {
        "score": score,
        "band": band,
        "raw_formula_score": raw_score,
        "raw_detector_risk": raw_detector_risk,
        "impact_score": round(impact_score, 3),
        "evidence_score": round(evidence_score, 3),
        "blast_weight": round(blast_weight, 3),
        "business_weight": round(business_weight, 3),
        "fidelity_tier": fidelity_tier,
        "fidelity_weight": fidelity_weight,
        "detection_weight": detection_weight,
        "replay_status": replay_status,
        "replay_weight": replay_weight,
        "caps": caps,
        "floors": floors,
    }


def _comment_review_priority_score(finding: AssumptionFindingV1) -> int:
    return int(_comment_review_priority_breakdown(finding)["score"])




def _comment_business_priority(finding: AssumptionFindingV1) -> int:
    return round(_comment_business_weight(finding) * 100)


def _comment_confidence_priority(finding: AssumptionFindingV1) -> int:
    return round(_comment_detection_confidence_weight(finding) * 100)


def _comment_blast_priority(finding: AssumptionFindingV1) -> int:
    return round(_comment_blast_weight(finding) * 100)


def _comment_severity_priority(finding: AssumptionFindingV1) -> int:
    severity = str(_comment_display_severity(finding) or "").lower()
    if severity.startswith("potential"):
        return 0
    if "critical" in severity:
        return 100
    if "high" in severity:
        return 80
    if "medium" in severity:
        return 50
    if "low" in severity:
        return 20
    return 0


def _comment_reviewer_priority(finding: AssumptionFindingV1) -> tuple[int, int, int, int, int, str]:
    """Sort key for PR comment display.

    Evidence-adjusted review priority comes first. Business, confidence,
    blast radius, and severity remain as deterministic tie-breakers.
    """
    return (
        _comment_review_priority_score(finding),
        _comment_business_priority(finding),
        _comment_confidence_priority(finding),
        _comment_blast_priority(finding),
        _comment_severity_priority(finding),
        str(getattr(finding, "stable_id", "") or getattr(finding, "finding_id", "")),
    )


def _should_demote_to_advisory(finding: AssumptionFindingV1) -> bool:
    """Demote weak/no-blast findings out of must-review.

    This is a reviewer-surface decision, not a detector decision. The finding
    still remains in the receipt and can still appear as useful advisory.
    """
    confidence = str(getattr(finding, "confidence", "") or "").lower()
    blast = _finding_blast_summary(finding)
    no_blast = not blast or blast == "No downstream resources found"
    severity = str(_comment_display_severity(finding) or "").lower()
    priority = _comment_review_priority_score(finding)

    return (
        confidence == "low"
        and no_blast
        and severity.startswith("potential")
        and priority < 45
    )



def _comment_trigger_summary(finding: AssumptionFindingV1) -> str:
    """Reviewer-safe trigger summary.

    Avoid raw diff hunk headers, schema-qualified debug SQL, and mid-token snippets.
    Full detector evidence remains in receipt.json.
    """
    family = str(getattr(finding, "family", "") or "").lower()
    detector = str((finding.pattern_detail or {}).get("pattern_type", "") or "").lower()

    evidence = getattr(finding, "evidence", None) or {}
    pattern_detail = getattr(finding, "pattern_detail", None) or {}

    resource = (
        getattr(finding, "changed_resource", None)
        or getattr(finding, "resource_name", None)
        or evidence.get("resource_name")
        or evidence.get("path")
        or pattern_detail.get("resource_name")
        or pattern_detail.get("path")
    )

    if resource:
        resource_text = f"`{resource}`"
    else:
        resource_text = "the changed dbt resource"

    if "enum" in family or "domain" in family or "enum" in detector:
        return (
            f"{resource_text} — status/domain mapping changed in conditional logic. "
            "Check whether downstream mappings, filters, accepted values, or dashboards still match the new default."
        )

    if "null" in family or "fallback" in family or "default" in family:
        return (
            f"{resource_text} — fallback/default handling changed. "
            "Check whether null or default values still carry the same business meaning."
        )

    if "join" in family or "cardinality" in family or "fanout" in family or "join" in detector:
        return (
            f"{resource_text} — join or aggregation grain is connected to the changed model. "
            "Check whether key uniqueness and deduplication assumptions still hold."
        )

    if "temporal" in family or "date" in family or "time" in family:
        return (
            f"{resource_text} — date/time bucketing or filtering changed. "
            "Check whether downstream reporting windows still mean the same thing."
        )

    return f"{resource_text} — SemZero found assumption-relevant structural evidence connected to this PR change."


def _render_comment_finding_group(group: list[AssumptionFindingV1], idx: int) -> list[str]:
    finding = group[0]
    detector = (finding.pattern_detail or {}).get("pattern_type", finding.family)
    business = _finding_business_severity(finding)
    control = (finding.control_coverage or {}).get("status", "unknown")
    cost_month = _finding_monthly_cost(finding)
    cost_text = f" · est. `${round(cost_month, 2)}/mo` exposure" if cost_month else ""
    drift = (finding.assumption_diff or {}).get(
        "drift_summary"
    ) or "Assumption-relevant behavior may have changed."
    fidelity = finding.replay_fidelity or {}
    fidelity_score = fidelity.get("score")
    fidelity_level = fidelity.get("level", "unknown")
    fidelity_text = (
        f"{fidelity_score} ({fidelity_level})"
        if fidelity_score is not None
        else str(fidelity_level)
    )
    validation = finding.validation_replay or {}
    validation_status = validation.get("status", "not_run")
    validation_summary = validation.get("summary", "No Replay Lite fixture supplied.")[:140]
    stable_ids = [f.stable_id or f.finding_id for f in group]
    stable_ids = [sid for sid in stable_ids if sid]
    stable_label = "Stable IDs" if len(stable_ids) > 1 else "Stable ID"
    grouped_suffix = f" · {len(group)} related findings grouped" if len(group) > 1 else ""

    severity = _comment_display_severity(finding)
    priority = _comment_review_priority_breakdown(finding)
    lines = [
        f"{idx}. **{finding.family.replace('_', ' ').title()}** — {priority['band']} · priority `{priority['score']}/100` · confidence `{finding.confidence}`{cost_text}{grouped_suffix}",
    ]

    if severity.startswith("potential"):
        lines += [
            "   - _Low-confidence/no-blast finding. Review as a possible assumption, not as a proven breakage._",
        ]

    stable_id_text = ", ".join(f"`{sid}`" for sid in stable_ids[:6]) or "`unavailable`"

    lines += [
        f"   - **Reviewer action:** {finding.recommended_check}",
        f"   - **Why it matters:** {_finding_blast_summary(finding)}",
        f"   - **What triggered this:** {_comment_trigger_summary(finding)}",
        f"   - **Confidence:** `{fidelity_text}`. Evidence tier: `{priority['fidelity_tier']}`. Replay: `{priority['replay_status']}`.",
        f"   - **Reference:** **{stable_label}:** {stable_id_text}",
        f"   - **Score detail:** impact `{priority['impact_score']}` × evidence `{priority['evidence_score']}` → displayed priority `{priority['score']}/100`.",
        f"   - **Technical detail:** drift `{drift}` · business `{business}` · control coverage `{control}` · detector `{detector}`",
        f"   - **Validation replay:** `{validation_status}` · {validation_summary}",
        "",
    ]
    return lines


def render_pr_comment(receipt: AssumptionGateReceiptV1, max_findings: int = 5) -> str:
    """Render a compact reviewer-first PR comment.

    Full detail remains in the JSON receipt; the PR comment groups repeated
    findings into reviewer-action items so engineers do not see duplicated
    walls of text.
    """
    data = receipt.to_dict()
    findings = sorted(
        receipt.findings,
        key=lambda f: (
            0 if _is_must_review_finding(f) else 1,
            -SEVERITY_RANK.get(f.severity, 0),
            -int(f.risk_score or 0),
            f.family,
        ),
    )
    accepted_risk = [f for f in findings if _finding_exception_state(f) == "active_exception"]
    must_review = [
        f
        for f in findings
        if f not in accepted_risk
        and _is_must_review_finding(f)
        and not _should_demote_to_advisory(f)
    ]
    useful_advisory = [
        f
        for f in findings
        if f not in must_review and f not in accepted_risk
    ]
    needs_feedback = [f for f in findings if _finding_exception_state(f) != "active_exception"]

    must_review = sorted(must_review, key=_comment_reviewer_priority, reverse=True)
    useful_advisory = sorted(useful_advisory, key=_comment_reviewer_priority, reverse=True)
    accepted_risk = sorted(accepted_risk, key=_comment_reviewer_priority, reverse=True)
    needs_feedback = sorted(needs_feedback, key=_comment_reviewer_priority, reverse=True)

    must_review_groups = _group_comment_findings(must_review)
    useful_advisory_groups = _group_comment_findings(useful_advisory)
    accepted_risk_groups = _group_comment_findings(accepted_risk)

    summary = data.get("summary", {})
    cost_month = summary.get("estimated_extra_cost_per_month_usd")
    cost_run = summary.get("estimated_extra_cost_per_run_usd")
    biz_summary = (summary.get("business_impact") or {}).get("summary")

    must_review_count = len(must_review_groups)
    advisory_count = len(useful_advisory_groups)

    if biz_summary and "revenue" in str(biz_summary).lower():
        lead_target = "revenue-critical data"
    elif biz_summary and "customer" in str(biz_summary).lower():
        lead_target = "customer-facing data"
    else:
        lead_target = "downstream dbt data"

    if must_review_count and advisory_count:
        lead_sentence = f"{must_review_count} assumption may break {lead_target}. {advisory_count} additional advisory finding."
    elif must_review_count:
        lead_sentence = f"{must_review_count} assumption may break {lead_target}. Review before merging."
    elif advisory_count:
        lead_sentence = f"{advisory_count} advisory finding needs attention before this change becomes enforced."
    elif findings:
        lead_sentence = f"{len(findings)} assumption signal found. Review before relying on this change."
    else:
        lead_sentence = "No reviewer-actionable assumption drift found in the changed dbt scope."

    lines = [
        "<!-- semzero-assumption-gate -->",
        "## SemZero Assumption Gate",
        "",
        f"**{lead_sentence}**",
        "",
        f"Verdict: `{receipt.verdict}` · Mode: `{receipt.mode}` · Review-required: `{must_review_count}` · Advisory: `{advisory_count}`",
        f"Changed dbt resource(s): `{summary.get('changed_resource_count', 0)}` · Affected downstream resource(s): `{summary.get('blast_radius_resource_count', 0)}`",
    ]
    if cost_run is not None or cost_month is not None:
        if cost_month is not None:
            lines.append(
                f"**Rough cost exposure:** `${cost_run}`/run · `${cost_month}`/month, pending warehouse validation"
            )
        else:
            lines.append(
                f"**Rough cost exposure:** `${cost_run}`/run, pending warehouse validation"
            )
    if biz_summary:
        lines.append(f"**Business impact:** {biz_summary}")
    fidelity_summary = summary.get("replay_fidelity_summary") or {}
    if fidelity_summary.get("average_score") is not None:
        lines.append(
            f"**Confidence:** Medium static confidence. Replay ran for `{fidelity_summary.get('replay_ran_count', 0)}` finding(s)."
        )
    validation_summary = summary.get("validation_replay_summary") or {}
    if validation_summary.get("replay_ran_count") is not None:
        lines.append(
            f"**Validation replay lite:** `{validation_summary.get('replay_ran_count', 0)}` replay(s), `{validation_summary.get('drift_detected_count', 0)}` drift signal(s)"
        )
    diff_summary = summary.get("assumption_diff_summary") or {}
    if diff_summary.get("finding_count"):
        lines.append(
            f"**Assumption diffing:** `{diff_summary.get('explicit_before_after_diff_count', 0)}` finding(s) have explicit before/after PR context"
        )
    lines.append("")

    if not findings:
        lines += [
            "No trigger-linked hidden assumptions were detected for this PR.",
            "",
            "_SemZero only reports an assumption when a related PR change makes it relevant._",
        ]
        return "\n".join(lines)

    lines += [
        "### Review summary",
        "",
        f"- **Must review:** `{len(must_review_groups)}` reviewer item(s) from `{len(must_review)}` raw finding(s)",
        f"- **Useful advisory:** `{len(useful_advisory_groups)}` reviewer item(s) from `{len(useful_advisory)}` raw finding(s)",
        f"- **Accepted risk / active exceptions:** `{len(accepted_risk_groups)}` reviewer item(s) from `{len(accepted_risk)}` raw finding(s)",
        f"- **Needs feedback:** `{len(needs_feedback)}`",
        "",
    ]

    visible_count = 0
    if must_review_groups:
        lines += ["### Review before merge", ""]
        for idx, group in enumerate(must_review_groups[:max_findings], start=1):
            lines.extend(_render_comment_finding_group(group, idx))
            visible_count += len(group)

    remaining_budget = max(0, max_findings - len(must_review_groups[:max_findings]))
    if useful_advisory_groups and remaining_budget:
        lines += ["### Useful advisory", ""]
        for idx, group in enumerate(useful_advisory_groups[:remaining_budget], start=1):
            lines.extend(_render_comment_finding_group(group, idx))
            visible_count += len(group)

    if accepted_risk_groups:
        lines += ["### Accepted risk / exceptions", ""]
        for group in accepted_risk_groups[:3]:
            finding = group[0]
            exc = finding.exception or {}
            active = exc.get("active") or []
            first = active[0] if active else {}
            reason = first.get("reason") or "No reason captured"
            expires = first.get("expires_at") or "no expiry"
            ids = ", ".join(f"`{f.stable_id or f.finding_id}`" for f in group[:6])
            grouped_suffix = f" ({len(group)} grouped findings)" if len(group) > 1 else ""
            lines += [
                f"- {ids} — **{finding.family.replace('_', ' ').title()}** is annotated as accepted risk{grouped_suffix}.",
                f"  - Reason: {reason}",
                f"  - Expires: `{expires}`",
            ]
        lines.append("")

    hidden = max(0, len(findings) - visible_count)
    if hidden:
        lines += [
            f"_{hidden} additional raw finding(s) are in the JSON receipt; this PR comment is intentionally capped/grouped to reduce review noise._",
            "",
        ]

    if needs_feedback:
        calibration_targets = []
        seen_ids = set()
        for finding in needs_feedback[:3]:
            fid = finding.stable_id or finding.finding_id
            if fid and fid not in seen_ids:
                seen_ids.add(fid)
                calibration_targets.append(fid)

        lines += [
            "### Calibrate these findings",
            "",
            "Reply with one command per finding:",
            "",
        ]
        for fid in calibration_targets:
            lines += [
                f"Finding `{fid}`:",
                f"- `/semzero agree {fid}`",
                f"- `/semzero false-positive {fid}`",
                f"- `/semzero accepted-risk {fid}`",
                "",
            ]

    lines.append(
        "_Full evidence is preserved in the JSON receipt artifact. This comment is ordered for reviewer action; raw detector evidence stays in the receipt._"
    )
    return "\n".join(lines)


def _load_structured_file(path: str | Path | None) -> dict[str, Any]:
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(str(p))
    text = p.read_text(encoding="utf-8")
    if p.suffix.lower() in {".yml", ".yaml"}:
        try:
            import yaml  # type: ignore

            return yaml.safe_load(text) or {}
        except Exception:
            # Tiny fallback for simple key/value YAML-like fixtures.
            out: dict[str, Any] = {}
            for line in text.splitlines():
                if ":" in line and not line.lstrip().startswith("#"):
                    key, val = line.split(":", 1)
                    out[key.strip()] = val.strip().strip("\"'")
            return out
    return json.loads(text)


def load_table_sizes(path: str | Path | None) -> dict[str, Any]:
    return _load_structured_file(path)


def load_cost_profiles(path: str | Path | None) -> dict[str, Any]:
    return _load_structured_file(path)


def load_business_criticality(path: str | Path | None) -> dict[str, Any]:
    return load_criticality_registry(path)


def load_assumption_exceptions(path: str | Path | None) -> list[dict[str, Any]]:
    return load_exceptions(path)
