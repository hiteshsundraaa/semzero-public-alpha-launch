from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


_FILTER_PATTERN = re.compile(r"\b([A-Za-z_][\w]*)\s+(?:IN\s*\(|=|!=|<>|NOT\s+IN\s*\()", re.I)
_COALESCE_PATTERN = re.compile(r"(?:coalesce|nullif|ifnull)\s*\(\s*([A-Za-z_][\w\.]*)", re.I)
_DATE_PATTERN = re.compile(
    r"\b(?:date|strftime|date_trunc|dateadd|datediff|timestampdiff)\s*\(\s*([A-Za-z_][\w\.]*)", re.I
)
_JOIN_PATTERN = re.compile(
    r"\b(?:left|right|full|inner|outer|cross)?\s*join\b[^\n;]*?\bon\b\s+([^\n;]+)", re.I
)
_PANDAS_MERGE_PATTERN = re.compile(r"\.merge\s*\(([^\)]*)\)", re.I)
_GROUP_PATTERN = re.compile(
    r"\b(group\s+by|count\s*\(\s*distinct|select\s+distinct|distinct\s+on)\b", re.I
)
_INCREMENTAL_PATTERN = re.compile(
    r"\b(is_incremental|incremental|late[_ -]?arriv|backfill|max\(|merge\s+into|delete\s+from)\b",
    re.I,
)
_COMPLETENESS_PATTERN = re.compile(
    r"\b(complete\s+after|freshness|opened_at|resolved_at|created_at|effective_at|started_at|ended_at|event_time|ingest(?:ed|ion)_time|loaded_at|occurred_at|updated_at|ts)\b",
    re.I,
)
_ROW_NUMBER_PATTERN = re.compile(r"\b(row_number|rank|dense_rank)\s*\(", re.I)
_CASE_PATTERN = re.compile(r"\bcase\b[\s\S]{0,180}?\bend\b", re.I)
_IS_NULL_PATTERN = re.compile(r"\b([A-Za-z_][\w\.]*)\s+is\s+(?:not\s+)?null\b", re.I)
_WINDOW_PATTERN = re.compile(r"\bover\s*\(([^\)]*)\)", re.I)
_FRESHNESS_CUTOFF_PATTERN = re.compile(
    r"\b(?:where|and)\b[^\n;]{0,140}?\b(?:current_date|current_timestamp|now\(|dateadd|datediff|interval)\b",
    re.I,
)
_DEDUP_COMMENT_PATTERN = re.compile(
    r"\b(dedupe|dedup|unique\s+row|one\s+row\s+per|latest\s+record|surrogate\s+key)\b", re.I
)

_ASSUME_TYPE_WEIGHT = {
    "GRAIN_OR_DEDUP_ASSUMPTION": 1.0,
    "JOIN_CARDINALITY_ASSUMPTION": 1.0,
    "DOMAIN_FILTER_ASSUMPTION": 0.92,
    "TEMPORAL_BUCKETING_ASSUMPTION": 0.88,
    "TEMPORAL_COMPLETENESS_ASSUMPTION": 0.84,
    "INCREMENTAL_STATE_ASSUMPTION": 0.9,
    "NULL_HANDLING_ASSUMPTION": 0.72,
    "STATUS_MAPPING_ASSUMPTION": 0.86,
    "FRESHNESS_WINDOW_ASSUMPTION": 0.8,
}


def _severity_for(confidence: float) -> str:
    if confidence >= 0.9:
        return "critical"
    if confidence >= 0.82:
        return "high"
    if confidence >= 0.7:
        return "medium"
    return "low"


@dataclass(slots=True)
class AssumptionFinding:
    node_id: str
    assumption_type: str
    confidence: float
    source_path: str
    excerpt: str
    reason: str
    severity: str = "medium"
    consumer_surface: str = "unknown"
    contract_hint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "assumption_type": self.assumption_type,
            "confidence": round(float(self.confidence), 3),
            "severity": self.severity,
            "consumer_surface": self.consumer_surface,
            "source_path": self.source_path,
            "excerpt": self.excerpt,
            "reason": self.reason,
            "contract_hint": self.contract_hint,
        }


@dataclass(slots=True)
class AssumptionSummary:
    findings: list[AssumptionFinding] = field(default_factory=list)

    def for_node(self, node_id: str) -> list[AssumptionFinding]:
        return [item for item in self.findings if item.node_id == node_id]

    def to_dict(self) -> dict[str, Any]:
        type_counts: dict[str, int] = {}
        nodes: dict[str, int] = {}
        severity_counts: dict[str, int] = {}
        consumer_surfaces: dict[str, int] = {}
        risk_score = 0.0
        for item in self.findings:
            type_counts[item.assumption_type] = type_counts.get(item.assumption_type, 0) + 1
            nodes[item.node_id] = nodes.get(item.node_id, 0) + 1
            severity_counts[item.severity] = severity_counts.get(item.severity, 0) + 1
            consumer_surfaces[item.consumer_surface] = (
                consumer_surfaces.get(item.consumer_surface, 0) + 1
            )
            risk_score += float(item.confidence) * _ASSUME_TYPE_WEIGHT.get(
                item.assumption_type, 0.75
            )
        contract_recommendations = []
        for item in sorted(
            self.findings,
            key=lambda finding: (-finding.confidence, finding.node_id, finding.assumption_type),
        ):
            hint = item.contract_hint.strip()
            if hint and hint.lower() not in {x.lower() for x in contract_recommendations}:
                contract_recommendations.append(hint)
            if len(contract_recommendations) >= 8:
                break
        top_nodes = [
            {"node_id": node_id, "finding_count": count}
            for node_id, count in sorted(nodes.items(), key=lambda kv: (-kv[1], kv[0]))[:8]
        ]
        critical_findings = [
            item.to_dict() for item in self.findings if item.severity in {"critical", "high"}
        ][:12]
        return {
            "finding_count": len(self.findings),
            "assumption_types": type_counts,
            "severity_counts": severity_counts,
            "consumer_surfaces": consumer_surfaces,
            "risk_score": round(min(100.0, risk_score * 6.25), 2),
            "top_nodes": top_nodes,
            "critical_findings": critical_findings,
            "contract_recommendations": contract_recommendations,
            "findings": [item.to_dict() for item in self.findings[:80]],
        }


class AssumptionGate:
    """Extract and score undocumented downstream assumptions from proof/code surfaces.

    The gate looks for day-to-day producer/consumer assumptions that usually live in
    tribal knowledge instead of explicit contracts: grain, join cardinality, domain
    filters, temporal completeness, freshness windows, incremental state, and null
    handling expectations.
    """

    def __init__(self, proof_source_paths: Iterable[str] | None = None) -> None:
        self.proof_source_paths = [str(p) for p in (proof_source_paths or []) if str(p)]

    def analyse(
        self, graph_json: dict[str, Any], drift_report: dict[str, Any]
    ) -> AssumptionSummary:
        if not self.proof_source_paths:
            return AssumptionSummary()
        nodes = self._candidate_nodes(graph_json, drift_report)
        findings: list[AssumptionFinding] = []
        for path in self._iter_files():
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            low = text.lower()
            for node_id, aliases in nodes.items():
                if not any(alias in low for alias in aliases):
                    continue
                findings.extend(self._analyse_text_for_node(path, text, node_id, aliases))
        dedup: dict[tuple[str, str, str, str], AssumptionFinding] = {}
        for item in findings:
            key = (item.node_id, item.assumption_type, item.source_path, item.excerpt)
            if key not in dedup or dedup[key].confidence < item.confidence:
                dedup[key] = item
        ordered = sorted(
            dedup.values(),
            key=lambda item: (
                -item.confidence,
                item.node_id,
                item.assumption_type,
                item.source_path,
            ),
        )
        return AssumptionSummary(ordered)

    def _candidate_nodes(
        self, graph_json: dict[str, Any], drift_report: dict[str, Any]
    ) -> dict[str, set[str]]:
        nodes: dict[str, set[str]] = {}
        graph_nodes = {
            str(item.get("id") or ""): item
            for item in (graph_json.get("nodes") or [])
            if isinstance(item, dict)
        }
        for event in drift_report.get("events", []) or []:
            node_id = str(event.get("node_id") or "")
            if not node_id:
                continue
            table, _, column = node_id.partition(".")
            aliases = {node_id.lower(), table.lower(), column.lower()}
            before = event.get("before") or {}
            after = event.get("after") or {}
            for name in (
                before.get("name"),
                after.get("name"),
                table,
                f"{table}.{before.get('name', '')}",
                f"{table}.{after.get('name', '')}",
                before.get("semantic_name"),
                after.get("semantic_name"),
            ):
                if name:
                    aliases.add(str(name).lower())
            graph_meta = graph_nodes.get(node_id) or graph_nodes.get(column) or {}
            for sample in graph_meta.get("sample_values", [])[:4]:
                if isinstance(sample, str) and 1 <= len(sample) <= 32:
                    aliases.add(sample.lower())
            nodes[node_id] = {alias for alias in aliases if alias and alias != "."}
        return nodes

    def _iter_files(self) -> Iterable[Path]:
        allowed = {
            ".sql",
            ".py",
            ".ts",
            ".tsx",
            ".js",
            ".jsx",
            ".prisma",
            ".yml",
            ".yaml",
            ".json",
            ".md",
        }
        for root in self.proof_source_paths:
            base = Path(root)
            if base.is_file() and base.suffix.lower() in allowed:
                yield base
                continue
            if not base.exists():
                continue
            for path in sorted(base.rglob("*")):
                if path.is_file() and path.suffix.lower() in allowed:
                    yield path

    def _analyse_text_for_node(
        self, path: Path, text: str, node_id: str, aliases: set[str]
    ) -> list[AssumptionFinding]:
        snippets: list[AssumptionFinding] = []

        def add(assumption_type: str, confidence: float, excerpt: str, reason: str) -> None:
            severity = _severity_for(confidence)
            snippets.append(
                AssumptionFinding(
                    node_id=node_id,
                    assumption_type=assumption_type,
                    confidence=confidence,
                    source_path=str(path),
                    excerpt=excerpt.strip()[:220],
                    reason=reason,
                    severity=severity,
                    consumer_surface=self._consumer_surface(path),
                    contract_hint=self._contract_hint(node_id, assumption_type),
                )
            )

        excerpt = self._excerpt_for_alias(text, aliases)
        lowered = excerpt.lower()
        full_lowered = text.lower()
        if any(alias in lowered for alias in aliases):
            if (
                _GROUP_PATTERN.search(excerpt)
                or _ROW_NUMBER_PATTERN.search(excerpt)
                or _DEDUP_COMMENT_PATTERN.search(excerpt)
            ):
                add(
                    "GRAIN_OR_DEDUP_ASSUMPTION",
                    0.9 if _ROW_NUMBER_PATTERN.search(excerpt) else 0.84,
                    excerpt,
                    "Downstream logic groups, deduplicates, or ranks records around this field, which implies hard row-grain assumptions.",
                )
            if _INCREMENTAL_PATTERN.search(excerpt):
                add(
                    "INCREMENTAL_STATE_ASSUMPTION",
                    0.86,
                    excerpt,
                    "The surrounding logic references incremental, merge, delete, or backfill semantics, so retained-state assumptions matter.",
                )
            if _COMPLETENESS_PATTERN.search(excerpt):
                add(
                    "TEMPORAL_COMPLETENESS_ASSUMPTION",
                    0.8,
                    excerpt,
                    "The surrounding logic references business/event time or freshness windows, implying completeness-window assumptions.",
                )
            if _FRESHNESS_CUTOFF_PATTERN.search(excerpt) or (
                _FRESHNESS_CUTOFF_PATTERN.search(full_lowered)
                and any(alias in full_lowered for alias in aliases)
            ):
                add(
                    "FRESHNESS_WINDOW_ASSUMPTION",
                    0.82,
                    excerpt,
                    "The query applies an implicit freshness/cutoff window that downstream consumers may rely on operationally.",
                )
            if _CASE_PATTERN.search(excerpt) and any(tok in excerpt.lower() for tok in aliases):
                add(
                    "STATUS_MAPPING_ASSUMPTION",
                    0.84,
                    excerpt,
                    "CASE logic around this field implies semantic mapping assumptions for status/domain values.",
                )

        for match in _FILTER_PATTERN.finditer(text):
            token = match.group(1).lower()
            if token in aliases or f".{token}" in aliases:
                add(
                    "DOMAIN_FILTER_ASSUMPTION",
                    0.88,
                    match.group(0),
                    "Downstream logic filters on this field's domain values, so enum/status meaning changes can silently break results.",
                )

        for match in _COALESCE_PATTERN.finditer(text):
            token = match.group(1).split(".")[-1].lower()
            if token in aliases or f".{token}" in aliases:
                add(
                    "NULL_HANDLING_ASSUMPTION",
                    0.86,
                    match.group(0),
                    "The field is wrapped in null-handling logic, which signals assumptions about sparsity and consumer-safe defaults.",
                )
        for match in _IS_NULL_PATTERN.finditer(text):
            token = match.group(1).split(".")[-1].lower()
            if token in aliases or f".{token}" in aliases:
                add(
                    "NULL_HANDLING_ASSUMPTION",
                    0.78,
                    match.group(0),
                    "The field participates in null-check logic, which embeds assumptions about missingness and fallback behavior.",
                )

        for match in _DATE_PATTERN.finditer(text):
            token = match.group(1).split(".")[-1].lower()
            if token in aliases or f".{token}" in aliases:
                add(
                    "TEMPORAL_BUCKETING_ASSUMPTION",
                    0.91,
                    match.group(0),
                    "The field is bucketed or transformed in time, so timezone/event-time semantics can distort trend and period comparisons.",
                )
        for match in _WINDOW_PATTERN.finditer(text):
            clause = match.group(1).lower()
            if any(alias in clause for alias in aliases):
                add(
                    "TEMPORAL_BUCKETING_ASSUMPTION",
                    0.79,
                    match.group(0),
                    "Window clauses reference this field, which often encode ordering or temporal-bucketing assumptions.",
                )

        for match in _JOIN_PATTERN.finditer(text):
            clause = match.group(1)
            low_clause = clause.lower()
            if any(alias in low_clause for alias in aliases):
                add(
                    "JOIN_CARDINALITY_ASSUMPTION",
                    0.9,
                    clause,
                    "The field participates in join logic, so null spikes, dedupe shifts, or key-grain changes can silently change row counts.",
                )
        for match in _PANDAS_MERGE_PATTERN.finditer(text):
            clause = match.group(1)
            low_clause = clause.lower()
            if any(alias in low_clause for alias in aliases):
                add(
                    "JOIN_CARDINALITY_ASSUMPTION",
                    0.85,
                    clause,
                    "The field participates in dataframe merge logic, which embeds assumptions about key uniqueness and row grain.",
                )

        return snippets

    @staticmethod
    def _consumer_surface(path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix == ".sql":
            return "sql"
        if suffix in {".py"}:
            return "python"
        if suffix in {".ts", ".tsx", ".js", ".jsx"}:
            return "application"
        if suffix in {".yml", ".yaml"}:
            return "orchestration"
        if suffix in {".prisma", ".json"}:
            return "schema"
        return "docs"

    @staticmethod
    def _contract_hint(node_id: str, assumption_type: str) -> str:
        if assumption_type == "GRAIN_OR_DEDUP_ASSUMPTION":
            return f"Document and test the expected grain for `{node_id}` before merging any producer-side change."
        if assumption_type == "JOIN_CARDINALITY_ASSUMPTION":
            return f"Codify key uniqueness/cardinality expectations for `{node_id}` with an explicit join contract or dbt test."
        if assumption_type == "DOMAIN_FILTER_ASSUMPTION":
            return f"Freeze the allowed domain/status values for `{node_id}` in a contract, enum test, or rollout note."
        if assumption_type in {
            "TEMPORAL_BUCKETING_ASSUMPTION",
            "TEMPORAL_COMPLETENESS_ASSUMPTION",
            "FRESHNESS_WINDOW_ASSUMPTION",
        }:
            return f"Make the event-time, timezone, and freshness expectations for `{node_id}` explicit before rollout."
        if assumption_type == "INCREMENTAL_STATE_ASSUMPTION":
            return f"Prove incremental/backfill semantics for `{node_id}` with retained-state validation before merge."
        if assumption_type == "NULL_HANDLING_ASSUMPTION":
            return f"Write an explicit null/sparsity contract for `{node_id}` so consumers stop relying on implicit fallbacks."
        if assumption_type == "STATUS_MAPPING_ASSUMPTION":
            return f"Publish the semantic mapping for `{node_id}` so CASE logic does not silently drift across producers and consumers."
        return (
            f"Turn the downstream assumption around `{node_id}` into an explicit contract or test."
        )

    @staticmethod
    def _excerpt_for_alias(text: str, aliases: set[str]) -> str:
        lines = text.splitlines()
        for idx, line in enumerate(lines):
            low = line.lower()
            if any(alias in low for alias in aliases):
                window = lines[max(0, idx - 1) : min(len(lines), idx + 2)]
                return " ".join(item.strip() for item in window if item.strip())
        compact = re.sub(r"\s+", " ", text)
        return compact[:220]
