from __future__ import annotations

import html
import json
import math
import shutil
import sqlite3
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import create_engine, text

from ..crawler.builder import SchemaGraphBuilder
from ..reliability.premerge import PremergeWorkflow, PremergeWorkflowConfig


_SCALE_ROWS = {
    "small": 250,
    "medium": 2500,
    "large": 12000,
    "xlarge": 50000,
}


@dataclass
class ValidationScenarioResult:
    name: str
    status: str
    predicted_safe: Optional[bool] = None
    actual_safe: Optional[bool] = None
    aligned: Optional[bool] = None
    metrics: dict[str, Any] = field(default_factory=dict)
    observations: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "predicted_safe": self.predicted_safe,
            "actual_safe": self.actual_safe,
            "aligned": self.aligned,
            "metrics": self.metrics,
            "observations": self.observations,
            "recommendations": self.recommendations,
        }


@dataclass
class ValidationReport:
    title: str
    db_url: str
    graph_path: str
    drift_path: str
    migration_path: str
    bundle_path: str
    scenarios: list[ValidationScenarioResult] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    artifact_paths: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "db_url": self.db_url,
            "graph_path": self.graph_path,
            "drift_path": self.drift_path,
            "migration_path": self.migration_path,
            "bundle_path": self.bundle_path,
            "summary": self.summary,
            "scenarios": [item.to_dict() for item in self.scenarios],
            "artifact_paths": self.artifact_paths,
        }

    def render_markdown(self) -> str:
        lines = [
            f"# {self.title}",
            "",
            "## Validation summary",
            "",
            f"- **Database:** `{self.db_url}`",
            f"- **Graph:** `{self.graph_path}`",
            f"- **Drift report:** `{self.drift_path}`",
            f"- **Migration:** `{self.migration_path}`",
            f"- **Premerge bundle:** `{self.bundle_path}`",
            f"- **Scenario coverage:** {self.summary.get('scenario_count', 0)}",
            f"- **Aligned predictions:** {self.summary.get('aligned_predictions', 0)} / {self.summary.get('scenarios_with_ground_truth', 0)}",
            f"- **Query replay:** {self.summary.get('queries_replayed', 0)}",
            f"- **Broken queries:** {self.summary.get('queries_broken', 0)}",
            f"- **Row mismatches:** {self.summary.get('queries_mismatch', 0)}",
            f"- **Chaos broken mutations:** {self.summary.get('mutations_that_broke', 0)}",
            "",
            "## Scenario results",
            "",
        ]
        for item in self.scenarios:
            lines.extend(
                [
                    f"### {item.name}",
                    "",
                    f"- **Status:** {item.status}",
                    f"- **Predicted safe:** {item.predicted_safe if item.predicted_safe is not None else 'n/a'}",
                    f"- **Actual safe:** {item.actual_safe if item.actual_safe is not None else 'n/a'}",
                    f"- **Prediction aligned:** {item.aligned if item.aligned is not None else 'n/a'}",
                ]
            )
            if item.metrics:
                lines.append("")
                lines.append("**Metrics**")
                for key, value in item.metrics.items():
                    lines.append(f"- `{key}`: {value}")
            if item.observations:
                lines.append("")
                lines.append("**What happened**")
                for note in item.observations:
                    lines.append(f"- {note}")
            if item.recommendations:
                lines.append("")
                lines.append("**What to do next**")
                for note in item.recommendations:
                    lines.append(f"- {note}")
            lines.append("")
        return "\n".join(lines).strip() + "\n"

    def render_html(self) -> str:
        cards = []
        for item in self.scenarios:
            metrics = "".join(
                f"<li><code>{html.escape(str(k))}</code>: {html.escape(str(v))}</li>"
                for k, v in item.metrics.items()
            )
            observations = "".join(f"<li>{html.escape(note)}</li>" for note in item.observations)
            recommendations = "".join(
                f"<li>{html.escape(note)}</li>" for note in item.recommendations
            )
            cards.append(
                f"""
                <div class='card'>
                  <h2>{html.escape(item.name)}</h2>
                  <p><strong>Status:</strong> {html.escape(item.status)}</p>
                  <p><strong>Predicted safe:</strong> {html.escape(str(item.predicted_safe)) if item.predicted_safe is not None else "n/a"} &nbsp; <strong>Actual safe:</strong> {html.escape(str(item.actual_safe)) if item.actual_safe is not None else "n/a"}</p>
                  <p><strong>Prediction aligned:</strong> {html.escape(str(item.aligned)) if item.aligned is not None else "n/a"}</p>
                  <h3>Metrics</h3>
                  <ul>{metrics or "<li>n/a</li>"}</ul>
                  <h3>What happened</h3>
                  <ul>{observations or "<li>n/a</li>"}</ul>
                  <h3>What to do next</h3>
                  <ul>{recommendations or "<li>n/a</li>"}</ul>
                </div>
                """
            )
        return f"""<!doctype html>
<html><head><meta charset='utf-8'><title>{html.escape(self.title)}</title>
<style>
body{{font-family:Inter,system-ui,sans-serif;background:#f8fafc;color:#0f172a;margin:0;padding:32px}}
.grid{{display:grid;grid-template-columns:repeat(12,1fr);gap:16px}}
.card{{background:white;border:1px solid #e2e8f0;border-radius:16px;padding:20px;grid-column:span 6;box-shadow:0 8px 24px rgba(15,23,42,.05)}}
h1,h2,h3{{margin-top:0}} code{{background:#eef2ff;padding:2px 6px;border-radius:6px}}
ul{{padding-left:20px}}
.summary{{background:white;border:1px solid #e2e8f0;border-radius:16px;padding:20px;margin-bottom:16px}}
</style></head><body>
<div class='summary'>
<h1>{html.escape(self.title)}</h1>
<p><strong>Database:</strong> <code>{html.escape(self.db_url)}</code></p>
<p><strong>Scenario coverage:</strong> {self.summary.get("scenario_count", 0)} &nbsp; <strong>Aligned predictions:</strong> {self.summary.get("aligned_predictions", 0)} / {self.summary.get("scenarios_with_ground_truth", 0)}</p>
<p><strong>Query replay:</strong> {self.summary.get("queries_replayed", 0)} &nbsp; <strong>Broken queries:</strong> {self.summary.get("queries_broken", 0)} &nbsp; <strong>Chaos broken mutations:</strong> {self.summary.get("mutations_that_broke", 0)}</p>
</div>
<div class='grid'>{"".join(cards)}</div>
</body></html>"""

    def save(
        self, json_path: str, markdown_path: Optional[str] = None, html_path: Optional[str] = None
    ) -> None:
        Path(json_path).parent.mkdir(parents=True, exist_ok=True)
        Path(json_path).write_text(
            json.dumps(self.to_dict(), indent=2, default=str), encoding="utf-8"
        )
        if markdown_path:
            Path(markdown_path).parent.mkdir(parents=True, exist_ok=True)
            Path(markdown_path).write_text(self.render_markdown(), encoding="utf-8")
        if html_path:
            Path(html_path).parent.mkdir(parents=True, exist_ok=True)
            Path(html_path).write_text(self.render_html(), encoding="utf-8")


@dataclass
class DemoValidationPack:
    db_url: str
    graph_path: str
    drift_path: str
    migration_path: str
    workload_path: str
    proof_paths: list[str]


@dataclass
class ValidationConfig:
    db_url: str = ""
    graph_path: str = ""
    drift_path: str = ""
    migration_path: str = ""
    data_dir: str = "data"
    proof_paths: list[str] = field(default_factory=list)
    workload_query_files: list[str] = field(default_factory=list)
    run_chaos: bool = True
    demo_pack_dir: str = ""
    demo_scale: str = "medium"
    demo_profile: str = "standard"
    demo_backend: str = "sqlite"
    source_schema: str = "public"
    scenarios: list[str] = field(
        default_factory=lambda: [
            "silent_truncation",
            "rename_breakage",
            "nullability_hardening",
            "numeric_precision_narrowing",
            "domain_enum_drift",
            "temporal_timezone_mismatch",
            "distribution_drift",
            "blank_string_fanout",
            "incremental_ghost",
            "ast_cross_modal_truth",
            "pregate_gate_stop",
        ]
    )


class ValidationHarness:
    def __init__(self, config: ValidationConfig) -> None:
        self.config = config
        self.data_dir = Path(config.data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def run(self) -> ValidationReport:
        pack = None
        if self.config.demo_pack_dir:
            if self.config.demo_backend == "postgres":
                pack = build_live_postgres_validation_pack(
                    Path(self.config.demo_pack_dir),
                    db_url=self.config.db_url,
                    scale=self.config.demo_scale,
                    source_schema=self.config.source_schema,
                    profile=self.config.demo_profile,
                )
            else:
                pack = build_demo_validation_pack(
                    Path(self.config.demo_pack_dir),
                    self.config.demo_scale,
                    profile=self.config.demo_profile,
                )
            self.config.db_url = pack.db_url
            self.config.graph_path = pack.graph_path
            self.config.drift_path = pack.drift_path
            self.config.migration_path = pack.migration_path
            self.config.proof_paths = list(
                dict.fromkeys(self.config.proof_paths + pack.proof_paths)
            )
            self.config.workload_query_files = list(
                dict.fromkeys(self.config.workload_query_files + [pack.workload_path])
            )

        if not self.config.db_url or not self.config.graph_path or not self.config.drift_path:
            raise ValueError(
                "Validation requires db_url, graph_path, and drift_path (or a demo pack)."
            )

        graph_json = json.loads(Path(self.config.graph_path).read_text(encoding="utf-8"))
        drift_report = json.loads(Path(self.config.drift_path).read_text(encoding="utf-8"))
        migration_sql = (
            Path(self.config.migration_path).read_text(encoding="utf-8")
            if self.config.migration_path and Path(self.config.migration_path).exists()
            else ""
        )

        workflow = PremergeWorkflow(
            graph_json,
            PremergeWorkflowConfig(
                db_url=self.config.db_url,
                data_dir=str(self.data_dir),
                proof_paths=self.config.proof_paths,
                run_wind_tunnel=True,
                run_chaos=self.config.run_chaos,
                wind_live_mode="safe",
                chaos_live_mode="safe",
                workload_query_files=self.config.workload_query_files,
                chaos_mutation_count=10,
                graph_intelligence_enabled=True,
            ),
        )
        bundle = workflow.run(drift_report=drift_report, migration_sql=migration_sql)
        bundle_path = str(self.data_dir / "validation_premerge_bundle.json")
        Path(bundle_path).write_text(
            json.dumps(bundle.to_dict(), indent=2, default=str), encoding="utf-8"
        )

        scenarios: list[ValidationScenarioResult] = []
        for scenario in self.config.scenarios:
            handler = getattr(self, f"_scenario_{scenario}", None)
            if handler is None:
                scenarios.append(
                    ValidationScenarioResult(
                        name=scenario,
                        status="SKIPPED",
                        observations=["Scenario handler not implemented."],
                    )
                )
                continue
            scenarios.append(handler(bundle=bundle.to_dict(), drift_report=drift_report, pack=pack))

        summary = self._build_summary(bundle.to_dict(), scenarios)
        report = ValidationReport(
            title="SemZero Product Validation Report",
            db_url=self.config.db_url,
            graph_path=self.config.graph_path,
            drift_path=self.config.drift_path,
            migration_path=self.config.migration_path,
            bundle_path=bundle_path,
            scenarios=scenarios,
            summary=summary,
            artifact_paths={
                "bundle": bundle_path,
            },
        )
        return report

    def _build_summary(
        self, bundle: dict[str, Any], scenarios: list[ValidationScenarioResult]
    ) -> dict[str, Any]:
        gate = bundle.get("gate_result") or {}
        wind = bundle.get("wind_tunnel_receipt") or {}
        chaos = bundle.get("chaos_report") or {}
        aligned = sum(1 for item in scenarios if item.aligned is True)
        with_truth = sum(1 for item in scenarios if item.aligned is not None)
        return {
            "gate_verdict": gate.get("verdict", "UNKNOWN"),
            "reliability_score": gate.get("reliability_score", 0.0),
            "oncall_risk": gate.get("oncall_risk", "UNKNOWN"),
            "scenario_count": len(scenarios),
            "aligned_predictions": aligned,
            "scenarios_with_ground_truth": with_truth,
            "failed_scenarios": [item.name for item in scenarios if item.status == "FAIL"],
            "queries_replayed": wind.get("queries_replayed", 0),
            "queries_broken": wind.get("queries_broken", 0),
            "queries_mismatch": wind.get("queries_mismatch", 0),
            "compute_cost_risk": wind.get("compute_cost_risk", 0),
            "mutations_that_broke": (chaos.get("summary") or {}).get("mutations_that_broke", 0),
            "recoverability_score": (chaos.get("summary") or {})
            .get("recovery_summary", {})
            .get("recoverability_score", 0),
        }

    def _scenario_silent_truncation(
        self,
        bundle: dict[str, Any],
        drift_report: dict[str, Any],
        pack: Optional[DemoValidationPack],
    ) -> ValidationScenarioResult:
        event = next(
            (
                e
                for e in drift_report.get("events", [])
                if e.get("node_id") == "users.external_user_id"
            ),
            None,
        )
        if not event:
            return ValidationScenarioResult(
                name="silent_truncation",
                status="SKIPPED",
                observations=["No narrowing event for users.external_user_id found."],
            )
        new_len = _varchar_length((event.get("after") or {}).get("dtype", ""))
        if new_len is None:
            return ValidationScenarioResult(
                name="silent_truncation",
                status="SKIPPED",
                observations=["No VARCHAR length narrowing found in drift report."],
            )
        max_len = _scalar(self.config.db_url, "SELECT MAX(LENGTH(external_user_id)) FROM users")
        actual_safe = (max_len or 0) <= new_len
        assessment = _find_assessment(bundle, "users.external_user_id")
        predicted_safe = assessment.get("compatibility") not in {
            "TYPE_NARROWING",
            "SEMANTIC_BREAKING",
        }
        observations = [
            f"Observed max external_user_id length = {max_len}; proposed limit = {new_len}."
        ]
        if not actual_safe:
            observations.append(
                "Production-length values would be silently truncated if the narrowing shipped as-is."
            )
        return ValidationScenarioResult(
            name="silent_truncation",
            status="PASS" if predicted_safe == actual_safe else "FAIL",
            predicted_safe=predicted_safe,
            actual_safe=actual_safe,
            aligned=predicted_safe == actual_safe,
            metrics={"observed_max_length": max_len, "proposed_max_length": new_len},
            observations=observations,
            recommendations=[
                assessment.get("recommendation", "Validate truncate risk before merging.")
            ],
        )

    def _scenario_rename_breakage(
        self,
        bundle: dict[str, Any],
        drift_report: dict[str, Any],
        pack: Optional[DemoValidationPack],
    ) -> ValidationScenarioResult:
        event = next(
            (
                e
                for e in drift_report.get("events", [])
                if e.get("node_id") == "orders.user_ref"
                and e.get("change_type") == "COLUMN_RENAMED"
            ),
            None,
        )
        if not event:
            return ValidationScenarioResult(
                name="rename_breakage",
                status="SKIPPED",
                observations=["No rename event for orders.user_ref found."],
            )
        before_name = (event.get("before") or {}).get("name", "user_ref")
        after_name = (event.get("after") or {}).get("name", "user_key")
        references_old_name = False
        for path in self.config.workload_query_files + self.config.proof_paths:
            p = Path(path)
            texts = []
            if p.is_dir():
                texts = [
                    file.read_text(encoding="utf-8", errors="ignore")
                    for file in p.rglob("*")
                    if file.is_file()
                ]
            elif p.exists():
                texts = [p.read_text(encoding="utf-8", errors="ignore")]
            if any(before_name.lower() in t.lower() for t in texts):
                references_old_name = True
                break
        assessment = _find_assessment(bundle, "orders.user_ref")
        predicted_safe = assessment.get("compatibility") in {"ADDITIVE_SAFE", "TYPE_WIDENING"}
        actual_safe = not references_old_name
        observations = [f"Column renamed: {before_name} -> {after_name}."]
        if references_old_name:
            observations.append(
                "Representative SQL or proof assets still reference the old column name and would break after the rename."
            )
        return ValidationScenarioResult(
            name="rename_breakage",
            status="PASS" if predicted_safe == actual_safe else "FAIL",
            predicted_safe=predicted_safe,
            actual_safe=actual_safe,
            aligned=predicted_safe == actual_safe,
            metrics={"old_name_referenced": references_old_name},
            observations=observations,
            recommendations=[
                assessment.get(
                    "recommendation",
                    "Stage the rename with a compatibility alias or update downstream SQL before merge.",
                )
            ],
        )

    def _scenario_nullability_hardening(
        self,
        bundle: dict[str, Any],
        drift_report: dict[str, Any],
        pack: Optional[DemoValidationPack],
    ) -> ValidationScenarioResult:
        event = next(
            (e for e in drift_report.get("events", []) if e.get("node_id") == "users.phone"), None
        )
        if not event:
            return ValidationScenarioResult(
                name="nullability_hardening",
                status="SKIPPED",
                observations=["No users.phone nullability hardening event found."],
            )
        null_count = (
            _scalar(self.config.db_url, "SELECT COUNT(*) FROM users WHERE phone IS NULL") or 0
        )
        actual_safe = null_count == 0
        assessment = _find_assessment(bundle, "users.phone")
        predicted_safe = assessment.get("compatibility") in {"ADDITIVE_SAFE", "TYPE_WIDENING"}
        observations = [f"Observed NULL phone values in live data: {null_count}."]
        if not actual_safe:
            observations.append(
                "Hardening users.phone to NOT NULL would fail or require a backfill because NULL values are already present."
            )
        return ValidationScenarioResult(
            name="nullability_hardening",
            status="PASS" if predicted_safe == actual_safe else "FAIL",
            predicted_safe=predicted_safe,
            actual_safe=actual_safe,
            aligned=predicted_safe == actual_safe,
            metrics={"null_count": null_count},
            observations=observations,
            recommendations=[
                assessment.get(
                    "recommendation", "Backfill or default-null records before enforcing NOT NULL."
                )
            ],
        )

    def _scenario_numeric_precision_narrowing(
        self,
        bundle: dict[str, Any],
        drift_report: dict[str, Any],
        pack: Optional[DemoValidationPack],
    ) -> ValidationScenarioResult:
        event = next(
            (e for e in drift_report.get("events", []) if e.get("node_id") == "payments.amount"),
            None,
        )
        if not event:
            return ValidationScenarioResult(
                name="numeric_precision_narrowing",
                status="SKIPPED",
                observations=["No payments.amount precision narrowing event found."],
            )
        before_dtype = str((event.get("before") or {}).get("dtype", ""))
        after_dtype = str((event.get("after") or {}).get("dtype", ""))
        precision, scale = _numeric_precision(after_dtype)
        unsafe_rows = 0
        if precision is not None and scale is not None:
            integer_digits = max(precision - scale, 0)
            threshold = 10**integer_digits
            sql = (
                "SELECT COUNT(*) FROM payments "
                f"WHERE amount IS NOT NULL AND ((ABS(amount) >= {threshold}) OR (ABS(amount * POWER(10, {scale}) - ROUND(amount * POWER(10, {scale}))) > 0.000001))"
            )
            unsafe_rows = _scalar(self.config.db_url, sql) or 0
        actual_safe = unsafe_rows == 0
        assessment = _find_assessment(bundle, "payments.amount")
        predicted_safe = assessment.get("compatibility") in {"ADDITIVE_SAFE", "TYPE_WIDENING"}
        observations = [
            f"Precision change observed: {before_dtype} -> {after_dtype}.",
            f"Rows that would round or overflow the new precision: {unsafe_rows}.",
        ]
        if not actual_safe:
            observations.append(
                "The narrowing would silently round or overflow live monetary values."
            )
        return ValidationScenarioResult(
            name="numeric_precision_narrowing",
            status="PASS" if predicted_safe == actual_safe else "FAIL",
            predicted_safe=predicted_safe,
            actual_safe=actual_safe,
            aligned=predicted_safe == actual_safe,
            metrics={
                "unsafe_rows": unsafe_rows,
                "before_dtype": before_dtype,
                "after_dtype": after_dtype,
            },
            observations=observations,
            recommendations=[
                assessment.get(
                    "recommendation",
                    "Keep the wider numeric contract or replay finance workloads before merge.",
                )
            ],
        )

    def _scenario_domain_enum_drift(
        self,
        bundle: dict[str, Any],
        drift_report: dict[str, Any],
        pack: Optional[DemoValidationPack],
    ) -> ValidationScenarioResult:
        event = next(
            (e for e in drift_report.get("events", []) if e.get("node_id") == "users.status"), None
        )
        if not event:
            return ValidationScenarioResult(
                name="domain_enum_drift",
                status="SKIPPED",
                observations=["No status drift event found."],
            )
        before_values = {
            str(v).lower()
            for v in (event.get("before") or {}).get("sample_values", [])
            if v is not None
        }
        after_values = {
            str(v).lower()
            for v in (event.get("after") or {}).get("sample_values", [])
            if v is not None
        }
        added_values = sorted(after_values - before_values)
        hardcoded_filter = False
        for path in self.config.proof_paths:
            p = Path(path)
            if p.is_dir():
                content = "\n".join(
                    file.read_text(encoding="utf-8", errors="ignore")
                    for file in p.rglob("*")
                    if file.is_file()
                )
            else:
                content = p.read_text(encoding="utf-8", errors="ignore") if p.exists() else ""
            lowered = content.lower()
            if (
                "status in ('active','paused')" in lowered
                or "status in ('active','paused')" in lowered
            ):
                hardcoded_filter = True
                break
        actual_safe = not (added_values and hardcoded_filter)
        assessment = _find_assessment(bundle, "users.status")
        predicted_safe = assessment.get("compatibility") not in {
            "DATA_REGRESSION",
            "SEMANTIC_BREAKING",
        }
        observations = [
            f"New domain values detected: {', '.join(added_values) if added_values else 'none'}."
        ]
        if hardcoded_filter:
            observations.append(
                "Downstream SQL contains a hardcoded `status IN ('active','paused')` filter."
            )
        return ValidationScenarioResult(
            name="domain_enum_drift",
            status="PASS" if predicted_safe == actual_safe else "FAIL",
            predicted_safe=predicted_safe,
            actual_safe=actual_safe,
            aligned=predicted_safe == actual_safe,
            metrics={"added_values": added_values, "hardcoded_filter_present": hardcoded_filter},
            observations=observations,
            recommendations=[
                assessment.get(
                    "recommendation", "Audit downstream hardcoded filters for new enum values."
                )
            ],
        )

    def _scenario_temporal_timezone_mismatch(
        self,
        bundle: dict[str, Any],
        drift_report: dict[str, Any],
        pack: Optional[DemoValidationPack],
    ) -> ValidationScenarioResult:
        event = next(
            (e for e in drift_report.get("events", []) if e.get("node_id") == "events.ts"), None
        )
        if not event:
            return ValidationScenarioResult(
                name="temporal_timezone_mismatch",
                status="SKIPPED",
                observations=["No temporal type change found."],
            )
        old_dtype = str((event.get("before") or {}).get("dtype", ""))
        new_dtype = str((event.get("after") or {}).get("dtype", ""))
        actual_safe = not _timezone_boundary_change(old_dtype, new_dtype)
        assessment = _find_assessment(bundle, "events.ts")
        predicted_safe = assessment.get("compatibility") not in {
            "SEMANTIC_BREAKING",
            "TYPE_NARROWING",
        }
        observations = [f"Temporal type transition observed: {old_dtype} -> {new_dtype}."]
        if not actual_safe:
            observations.append(
                "Timezone-aware to timezone-naive casting can shift daily buckets and duplicate or drop region-sensitive aggregates."
            )
        return ValidationScenarioResult(
            name="temporal_timezone_mismatch",
            status="PASS" if predicted_safe == actual_safe else "FAIL",
            predicted_safe=predicted_safe,
            actual_safe=actual_safe,
            aligned=predicted_safe == actual_safe,
            metrics={"before_dtype": old_dtype, "after_dtype": new_dtype},
            observations=observations,
            recommendations=[
                assessment.get(
                    "recommendation",
                    "Keep UTC semantics explicit and replay temporal workloads before merge.",
                )
            ],
        )

    def _scenario_distribution_drift(
        self,
        bundle: dict[str, Any],
        drift_report: dict[str, Any],
        pack: Optional[DemoValidationPack],
    ) -> ValidationScenarioResult:
        event = next(
            (
                e
                for e in drift_report.get("events", [])
                if e.get("node_id") == "payments.adjustment"
                and e.get("change_type") == "STATS_DRIFTED"
            ),
            None,
        )
        if not event:
            return ValidationScenarioResult(
                name="distribution_drift",
                status="SKIPPED",
                observations=["No payments.adjustment distribution drift event found."],
            )
        baseline_stats = _profile_column(self.config.db_url, "payments", "adjustment")
        if _is_sqlite_url(self.config.db_url):
            db_file = self.config.db_url.replace("sqlite:///", "", 1)
            temp_dir = Path(tempfile.mkdtemp(prefix="semzero_distribution_drift_"))
            clone_path = temp_dir / "dist.db"
            shutil.copy2(db_file, clone_path)
            clone_url = f"sqlite:///{clone_path}"
            engine = create_engine(clone_url)
            try:
                with engine.begin() as conn:
                    conn.execute(text("UPDATE payments SET adjustment = NULL WHERE id % 3 = 0"))
                mutated_stats = _profile_column(clone_url, "payments", "adjustment")
            finally:
                engine.dispose()
                shutil.rmtree(temp_dir, ignore_errors=True)
        elif _is_postgres_url(self.config.db_url):
            with _postgres_shadow_env(
                self.config.db_url, self.config.source_schema, ["payments"]
            ) as (engine, schema):
                with engine.begin() as conn:
                    conn.execute(
                        text(
                            f"UPDATE {_qt(schema, 'payments')} SET adjustment = NULL WHERE MOD(id, 3) = 0"
                        )
                    )
                mutated_stats = _profile_column_engine(engine, schema, "payments", "adjustment")
        else:
            return ValidationScenarioResult(
                name="distribution_drift",
                status="SKIPPED",
                observations=[
                    "Distribution drift validation currently supports SQLite and PostgreSQL live validation paths."
                ],
            )
        null_delta = (mutated_stats["null_rate"] or 0.0) - (baseline_stats["null_rate"] or 0.0)
        avg_delta = abs((mutated_stats["avg"] or 0.0) - (baseline_stats["avg"] or 0.0))
        actual_safe = null_delta < 0.2 and avg_delta < max(
            abs((baseline_stats["avg"] or 0.0) * 0.25), 1.0
        )
        assessment = _find_assessment(bundle, "payments.adjustment")
        predicted_safe = assessment.get("compatibility") in {"ADDITIVE_SAFE", "TYPE_WIDENING"}
        observations = [
            f"Baseline adjustment null-rate: {baseline_stats['null_rate']:.3f}; mutated null-rate: {mutated_stats['null_rate']:.3f}.",
            f"Baseline avg adjustment: {baseline_stats['avg']:.3f}; mutated avg adjustment: {mutated_stats['avg']:.3f}.",
        ]
        if not actual_safe:
            observations.append(
                "The query still runs, but the shape of the output drifts enough to threaten downstream metrics and contracts."
            )
        return ValidationScenarioResult(
            name="distribution_drift",
            status="PASS" if predicted_safe == actual_safe else "FAIL",
            predicted_safe=predicted_safe,
            actual_safe=actual_safe,
            aligned=predicted_safe == actual_safe,
            metrics={
                "baseline_null_rate": round(baseline_stats["null_rate"], 4),
                "mutated_null_rate": round(mutated_stats["null_rate"], 4),
                "baseline_avg": round(baseline_stats["avg"], 4),
                "mutated_avg": round(mutated_stats["avg"], 4),
            },
            observations=observations,
            recommendations=[
                assessment.get(
                    "recommendation",
                    "Treat distribution drift as a release blocker for contract-bearing metrics and finance outputs.",
                )
            ],
        )

    def _scenario_ast_cross_modal_truth(
        self,
        bundle: dict[str, Any],
        drift_report: dict[str, Any],
        pack: Optional[DemoValidationPack],
    ) -> ValidationScenarioResult:
        gate = bundle.get("gate_result") or {}
        proof = gate.get("proof_bundle") or {}
        findings = proof.get("findings") or []
        by_lang = {str(item.get("language", "code")).lower() for item in findings}
        impacted = {str(item.get("node_id", "")) for item in findings if item.get("node_id")}
        observations = []
        recommendations = []
        if findings:
            observations.append(
                f"Cross-modal proof found {len(findings)} reference(s) across {', '.join(sorted(by_lang))}."
            )
        else:
            observations.append("No cross-modal proof findings were attached to the Gate result.")
        expected_nodes = {
            "orders.user_ref",
            "users.external_user_id",
            "users.status",
            "events.ts",
            "payments.amount",
        }
        hit_nodes = sorted(expected_nodes & impacted)
        if hit_nodes:
            observations.append("Expected nodes found in AST evidence: " + ", ".join(hit_nodes))
        else:
            recommendations.append(
                "Strengthen AST proof extraction against the demo proof pack; expected contract references were not found."
            )
        aligned = bool(findings) and bool(hit_nodes)
        return ValidationScenarioResult(
            name="ast_cross_modal_truth",
            status="PASS" if aligned else "FAIL",
            predicted_safe=False if findings else None,
            actual_safe=False if hit_nodes else None,
            aligned=aligned if findings else None,
            metrics={
                "finding_count": len(findings),
                "language_count": len(by_lang),
                "expected_nodes_hit": len(hit_nodes),
            },
            observations=observations,
            recommendations=recommendations
            or [
                "Keep validating AST proofing against messy app/dbt/warehouse changes before trusting it as a hard blocker."
            ],
        )

    def _scenario_blank_string_fanout(
        self,
        bundle: dict[str, Any],
        drift_report: dict[str, Any],
        pack: Optional[DemoValidationPack],
    ) -> ValidationScenarioResult:
        baseline = 0
        after = 0
        if _is_sqlite_url(self.config.db_url):
            db_file = self.config.db_url.replace("sqlite:///", "", 1)
            temp_dir = Path(tempfile.mkdtemp(prefix="semzero_blank_fanout_"))
            clone_path = temp_dir / "fanout.db"
            shutil.copy2(db_file, clone_path)
            clone_url = f"sqlite:///{clone_path}"
            baseline = (
                _scalar(
                    clone_url,
                    "SELECT COUNT(*) FROM orders o JOIN users u ON o.user_ref = u.external_user_id",
                )
                or 0
            )
            engine = create_engine(clone_url)
            try:
                with engine.begin() as conn:
                    conn.execute(text("UPDATE orders SET user_ref = '' WHERE id % 7 = 0"))
                    conn.execute(text("UPDATE users SET external_user_id = '' WHERE id % 5 = 0"))
                after = (
                    _scalar(
                        clone_url,
                        "SELECT COUNT(*) FROM orders o JOIN users u ON o.user_ref = u.external_user_id",
                    )
                    or 0
                )
            finally:
                engine.dispose()
                shutil.rmtree(temp_dir, ignore_errors=True)
        elif _is_postgres_url(self.config.db_url):
            with _postgres_shadow_env(
                self.config.db_url, self.config.source_schema, ["users", "orders"]
            ) as (engine, schema):
                baseline = (
                    _scalar_engine(
                        engine,
                        f"SELECT COUNT(*) FROM {_qt(schema, 'orders')} o JOIN {_qt(schema, 'users')} u ON o.user_ref = u.external_user_id",
                    )
                    or 0
                )
                with engine.begin() as conn:
                    conn.execute(
                        text(
                            f"UPDATE {_qt(schema, 'orders')} SET user_ref = '' WHERE MOD(id, 7) = 0"
                        )
                    )
                    conn.execute(
                        text(
                            f"UPDATE {_qt(schema, 'users')} SET external_user_id = '' WHERE MOD(id, 5) = 0"
                        )
                    )
                after = (
                    _scalar_engine(
                        engine,
                        f"SELECT COUNT(*) FROM {_qt(schema, 'orders')} o JOIN {_qt(schema, 'users')} u ON o.user_ref = u.external_user_id",
                    )
                    or 0
                )
        else:
            return ValidationScenarioResult(
                name="blank_string_fanout",
                status="SKIPPED",
                observations=[
                    "Blank-string fan-out validation currently supports SQLite and PostgreSQL live validation paths."
                ],
            )
        actual_safe = after <= max(int(baseline * 1.5), baseline + 10)
        predicted_safe = True
        chaos_report = bundle.get("chaos_report") or {}
        mutation_results = (
            chaos_report.get("mutation_results", []) if isinstance(chaos_report, dict) else []
        )
        if any(item.get("mutation_type") == "BLANK_STRING_FLOOD" for item in mutation_results):
            predicted_safe = False
        elif ((bundle.get("gate_result") or {}).get("recommended_execution") or {}).get(
            "run_chaos"
        ):
            predicted_safe = False
        observations = [
            f"Join rows before blank-string flood: {baseline}",
            f"Join rows after blank-string flood: {after}",
        ]
        if not actual_safe:
            observations.append(
                "Replacing join-key NULL-like values with empty strings creates a fan-out join and compute-risk spike."
            )
        return ValidationScenarioResult(
            name="blank_string_fanout",
            status="PASS" if predicted_safe == actual_safe else "FAIL",
            predicted_safe=predicted_safe,
            actual_safe=actual_safe,
            aligned=predicted_safe == actual_safe,
            metrics={
                "baseline_join_rows": baseline,
                "post_mutation_join_rows": after,
                "row_multiplier": round((after / baseline), 2) if baseline else None,
            },
            observations=observations,
            recommendations=[
                "Normalize join keys with NULLIF(TRIM(col), '') before joins and rerun Chaos on identity/domain fields."
            ],
        )

    def _scenario_incremental_ghost(
        self,
        bundle: dict[str, Any],
        drift_report: dict[str, Any],
        pack: Optional[DemoValidationPack],
    ) -> ValidationScenarioResult:
        has_incremental = False
        for path in self.config.proof_paths:
            p = Path(path)
            texts = []
            if p.is_dir():
                texts = [
                    file.read_text(encoding="utf-8", errors="ignore")
                    for file in p.rglob("*")
                    if file.is_file()
                ]
            elif p.exists():
                texts = [p.read_text(encoding="utf-8", errors="ignore")]
            if any("is_incremental" in text.lower() for text in texts):
                has_incremental = True
                break
        if not has_incremental:
            return ValidationScenarioResult(
                name="incremental_ghost",
                status="SKIPPED",
                observations=["No incremental logic markers found in proof paths."],
            )
        pre_rollup = post_rollup = stale = 0
        if _is_sqlite_url(self.config.db_url):
            db_file = self.config.db_url.replace("sqlite:///", "", 1)
            temp_dir = Path(tempfile.mkdtemp(prefix="semzero_incremental_ghost_"))
            clone_path = temp_dir / "incremental.db"
            shutil.copy2(db_file, clone_path)
            clone_url = f"sqlite:///{clone_path}"
            engine = create_engine(clone_url)
            try:
                with engine.begin() as conn:
                    pre_rollup = (
                        conn.execute(text("SELECT COUNT(*) FROM order_rollup")).scalar() or 0
                    )
                    conn.execute(text("DELETE FROM orders WHERE id = 3"))
                    conn.execute(
                        text(
                            "INSERT OR REPLACE INTO order_rollup (order_id, user_ref, total, created_at) SELECT id, user_ref, total, created_at FROM orders WHERE created_at >= '2024-01-12'"
                        )
                    )
                    stale = (
                        conn.execute(
                            text(
                                "SELECT COUNT(*) FROM order_rollup r LEFT JOIN orders o ON r.order_id = o.id WHERE o.id IS NULL"
                            )
                        ).scalar()
                        or 0
                    )
                    post_rollup = (
                        conn.execute(text("SELECT COUNT(*) FROM order_rollup")).scalar() or 0
                    )
            finally:
                engine.dispose()
                shutil.rmtree(temp_dir, ignore_errors=True)
        elif _is_postgres_url(self.config.db_url):
            with _postgres_shadow_env(
                self.config.db_url, self.config.source_schema, ["orders", "order_rollup"]
            ) as (engine, schema):
                with engine.begin() as conn:
                    pre_rollup = (
                        conn.execute(
                            text(f"SELECT COUNT(*) FROM {_qt(schema, 'order_rollup')}")
                        ).scalar()
                        or 0
                    )
                    conn.execute(text(f"DELETE FROM {_qt(schema, 'orders')} WHERE id = 3"))
                    conn.execute(
                        text(
                            f"""
                        INSERT INTO {_qt(schema, "order_rollup")} (order_id, user_ref, total, created_at)
                        SELECT id, user_ref, total, created_at
                        FROM {_qt(schema, "orders")}
                        WHERE created_at >= '2024-01-12'
                        ON CONFLICT (order_id) DO UPDATE
                        SET user_ref = EXCLUDED.user_ref,
                            total = EXCLUDED.total,
                            created_at = EXCLUDED.created_at
                        """
                        )
                    )
                    stale = (
                        conn.execute(
                            text(
                                f"SELECT COUNT(*) FROM {_qt(schema, 'order_rollup')} r LEFT JOIN {_qt(schema, 'orders')} o ON r.order_id = o.id WHERE o.id IS NULL"
                            )
                        ).scalar()
                        or 0
                    )
                    post_rollup = (
                        conn.execute(
                            text(f"SELECT COUNT(*) FROM {_qt(schema, 'order_rollup')}")
                        ).scalar()
                        or 0
                    )
        else:
            return ValidationScenarioResult(
                name="incremental_ghost",
                status="SKIPPED",
                observations=[
                    "Incremental ghost validation currently supports SQLite and PostgreSQL live validation paths."
                ],
            )
        actual_safe = stale == 0
        predicted_safe = not (
            ((bundle.get("gate_result") or {}).get("recommended_execution") or {}).get(
                "incremental_state_checks_required"
            )
            or False
        )
        observations = [
            f"Order rollup rows before simulated delete: {pre_rollup}",
            f"Order rollup rows after incremental rerun: {post_rollup}",
            f"Stale deleted rows retained in incremental state: {stale}",
        ]
        if not actual_safe:
            observations.append(
                "The incremental state stayed wrong after a hard delete because the reconciliation path never saw the missing row."
            )
        return ValidationScenarioResult(
            name="incremental_ghost",
            status="PASS" if predicted_safe == actual_safe else "FAIL",
            predicted_safe=predicted_safe,
            actual_safe=actual_safe,
            aligned=predicted_safe == actual_safe,
            metrics={
                "stale_rows": stale,
                "pre_rollup_rows": pre_rollup,
                "post_rollup_rows": post_rollup,
            },
            observations=observations,
            recommendations=[
                "Require state-reconciliation checks for incremental models, including delete blindness, duplicate keys, and late-arriving replay windows."
            ],
        )

    def _scenario_pregate_gate_stop(
        self,
        bundle: dict[str, Any],
        drift_report: dict[str, Any],
        pack: Optional[DemoValidationPack],
    ) -> ValidationScenarioResult:
        gate = bundle.get("gate_result") or {}
        verdict = str(gate.get("verdict") or "UNKNOWN")
        recommended = gate.get("recommended_execution") or {}
        high_risk_events = [
            e
            for e in drift_report.get("events", [])
            if e.get("change_type")
            in {"COLUMN_RENAMED", "TYPE_CHANGED", "NULLABLE_CHANGED", "STATS_DRIFTED"}
        ]
        actual_safe = len(high_risk_events) == 0
        predicted_safe = verdict in {"SAFE", "ALLOW", "ADDITIVE_SAFE"}
        should_have_stopped = not actual_safe
        stopped = (
            verdict in {"BLOCK", "NEEDS_REVIEW"}
            or bool(recommended.get("run_wind_tunnel"))
            or bool(recommended.get("run_chaos"))
        )
        observations = [
            f"Gate verdict: {verdict}",
            f"High-risk events observed in drift: {len(high_risk_events)}",
        ]
        if should_have_stopped:
            observations.append(
                "PreGate should force a stop or escalation before live apply for this manipulated change set."
            )
        return ValidationScenarioResult(
            name="pregate_gate_stop",
            status="PASS" if (stopped == should_have_stopped) else "FAIL",
            predicted_safe=predicted_safe,
            actual_safe=actual_safe,
            aligned=(stopped == should_have_stopped),
            metrics={
                "high_risk_event_count": len(high_risk_events),
                "gate_verdict": verdict,
                "run_wind_tunnel": bool(recommended.get("run_wind_tunnel")),
                "run_chaos": bool(recommended.get("run_chaos")),
            },
            observations=observations,
            recommendations=[
                "If Gate does not stop or escalate on manipulated high-risk drift, tighten compatibility rules and make Wind Tunnel mandatory for the affected hazard family."
            ],
        )


def _find_assessment(bundle: dict[str, Any], node_id: str) -> dict[str, Any]:
    for item in (bundle.get("gate_result") or {}).get("assessments", []):
        if item.get("node_id") == node_id:
            return item
    return {}


def _scalar(db_url: str, sql: str) -> Any:
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            return conn.execute(text(sql)).scalar()
    finally:
        engine.dispose()


def _varchar_length(dtype: str) -> int | None:
    import re

    match = re.search(r"(?:VAR)?CHAR\s*\((\d+)\)", str(dtype or ""), re.IGNORECASE)
    return int(match.group(1)) if match else None


def _numeric_precision(dtype: str) -> tuple[int | None, int | None]:
    import re

    match = re.search(
        r"(?:NUMERIC|DECIMAL)\s*\((\d+)\s*,\s*(\d+)\)", str(dtype or ""), re.IGNORECASE
    )
    if not match:
        return None, None
    return int(match.group(1)), int(match.group(2))


def _profile_column(db_url: str, table: str, column: str) -> dict[str, float]:
    engine = create_engine(db_url)
    try:
        return _profile_column_engine(engine, None, table, column)
    finally:
        engine.dispose()


def _profile_column_engine(engine, schema: str | None, table: str, column: str) -> dict[str, float]:
    relation = _qt(schema, table) if schema else table
    sql = f"SELECT COUNT(*) AS total_rows, SUM(CASE WHEN {column} IS NULL THEN 1 ELSE 0 END) AS null_rows, AVG({column}) AS avg_val FROM {relation}"
    with engine.begin() as conn:
        row = conn.execute(text(sql)).mappings().first() or {}
    total = float(row.get("total_rows") or 0.0)
    null_rows = float(row.get("null_rows") or 0.0)
    avg_val = float(row.get("avg_val") or 0.0)
    return {
        "total_rows": total,
        "null_rate": (null_rows / total) if total else 0.0,
        "avg": avg_val,
    }


def _timezone_boundary_change(before_dtype: str, after_dtype: str) -> bool:
    before = str(before_dtype or "").upper()
    after = str(after_dtype or "").upper()
    tz_before = any(
        token in before for token in ("TIMESTAMPTZ", "TIMESTAMP_TZ", "TIMESTAMP WITH TIME ZONE")
    )
    tz_after = any(
        token in after for token in ("TIMESTAMPTZ", "TIMESTAMP_TZ", "TIMESTAMP WITH TIME ZONE")
    )
    ntz_before = any(
        token in before for token in ("TIMESTAMP_NTZ", "TIMESTAMP WITHOUT TIME ZONE")
    ) or ("TIMESTAMP" in before and not tz_before)
    ntz_after = any(
        token in after for token in ("TIMESTAMP_NTZ", "TIMESTAMP WITHOUT TIME ZONE")
    ) or ("TIMESTAMP" in after and not tz_after)
    return (tz_before and ntz_after) or (ntz_before and tz_after)


def _scalar_engine(engine, sql: str) -> Any:
    with engine.begin() as conn:
        return conn.execute(text(sql)).scalar()


def _is_sqlite_url(db_url: str) -> bool:
    return str(db_url or "").startswith("sqlite:///")


def _is_postgres_url(db_url: str) -> bool:
    return str(db_url or "").startswith("postgresql")


def _qid(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _qt(schema: str, table: str) -> str:
    return f"{_qid(schema)}.{_qid(table)}"


def _postgres_shadow_env(db_url: str, source_schema: str, tables: list[str]):
    class _ShadowCtx:
        def __enter__(self_nonlocal):
            self_nonlocal.engine = create_engine(db_url)
            self_nonlocal.schema = f"semzero_shadow_{uuid.uuid4().hex[:8]}"
            with self_nonlocal.engine.begin() as conn:
                conn.exec_driver_sql(f"CREATE SCHEMA {_qid(self_nonlocal.schema)}")
                for table in tables:
                    exists = conn.execute(
                        text(
                            "SELECT 1 FROM information_schema.tables WHERE table_schema = :schema AND table_name = :table"
                        ),
                        {"schema": source_schema, "table": table},
                    ).scalar()
                    if not exists:
                        continue
                    conn.exec_driver_sql(
                        f"CREATE TABLE {_qt(self_nonlocal.schema, table)} (LIKE {_qt(source_schema, table)} INCLUDING ALL)"
                    )
                    conn.exec_driver_sql(
                        f"INSERT INTO {_qt(self_nonlocal.schema, table)} SELECT * FROM {_qt(source_schema, table)}"
                    )
            return self_nonlocal.engine, self_nonlocal.schema

        def __exit__(self_nonlocal, exc_type, exc, tb):
            try:
                with self_nonlocal.engine.begin() as conn:
                    conn.exec_driver_sql(
                        f"DROP SCHEMA IF EXISTS {_qid(self_nonlocal.schema)} CASCADE"
                    )
            finally:
                self_nonlocal.engine.dispose()

    return _ShadowCtx()


def build_live_postgres_validation_pack(
    base_dir: Path,
    db_url: str,
    scale: str = "large",
    source_schema: str = "public",
    profile: str = "standard",
) -> DemoValidationPack:
    if not _is_postgres_url(db_url):
        raise ValueError("Live Postgres validation packs require a PostgreSQL db_url.")
    base_dir.mkdir(parents=True, exist_ok=True)
    row_count = _SCALE_ROWS.get(scale, _SCALE_ROWS["large"])
    messy = profile in {"messy", "finance", "chaos_labyrinth", "black_swan"}
    black_swan = profile == "black_swan"
    engine = create_engine(db_url)
    statuses = ["active", "paused"]
    try:
        with engine.begin() as conn:
            if source_schema != "public":
                conn.exec_driver_sql(f"CREATE SCHEMA IF NOT EXISTS {_qid(source_schema)}")
            for table in ["order_rollup", "events", "orders", "users", "payments"]:
                conn.exec_driver_sql(f"DROP TABLE IF EXISTS {_qt(source_schema, table)} CASCADE")
            conn.exec_driver_sql(
                f"CREATE TABLE {_qt(source_schema, 'users')} (id INTEGER PRIMARY KEY, external_user_id VARCHAR(255) NOT NULL, email VARCHAR(255) NOT NULL, phone VARCHAR(32), status VARCHAR(32) NOT NULL, created_at DATE NOT NULL)"
            )
            conn.exec_driver_sql(
                f"CREATE TABLE {_qt(source_schema, 'orders')} (id INTEGER PRIMARY KEY, user_ref VARCHAR(255) NOT NULL, status VARCHAR(32) NOT NULL, total NUMERIC(18,2) NOT NULL, created_at DATE NOT NULL)"
            )
            conn.exec_driver_sql(
                f"CREATE TABLE {_qt(source_schema, 'order_rollup')} (order_id INTEGER PRIMARY KEY, user_ref VARCHAR(255) NOT NULL, total NUMERIC(18,2) NOT NULL, created_at DATE NOT NULL)"
            )
            conn.exec_driver_sql(
                f"CREATE TABLE {_qt(source_schema, 'events')} (id INTEGER PRIMARY KEY, user_ref VARCHAR(255) NOT NULL, event_type VARCHAR(64) NOT NULL, ts TIMESTAMPTZ NOT NULL)"
            )
            conn.exec_driver_sql(
                f"CREATE TABLE {_qt(source_schema, 'payments')} (id INTEGER PRIMARY KEY, user_ref VARCHAR(255) NOT NULL, amount NUMERIC(18,4) NOT NULL, adjustment NUMERIC(18,4), currency VARCHAR(3) NOT NULL, created_at DATE NOT NULL)"
            )
            user_rows = []
            order_rows = []
            roll_rows = []
            event_rows = []
            payment_rows = []
            for i in range(1, row_count + 1):
                ext = f"cust_{i:06d}_" + ("x" * 60)
                phone = None if (messy and i % 9 == 0) else f"+1-555-{i:06d}"
                user_rows.append(
                    {
                        "id": i,
                        "external_user_id": ext,
                        "email": f"user{i}@demo.local",
                        "phone": phone,
                        "status": statuses[i % 2],
                        "created_at": f"2024-01-{(i % 28) + 1:02d}",
                    }
                )
                order_rows.append(
                    {
                        "id": i,
                        "user_ref": ext,
                        "status": "completed" if i % 4 else "pending",
                        "total": round(((i % 11) + 1) * 17.35, 2),
                        "created_at": f"2024-01-{(i % 28) + 1:02d}",
                    }
                )
                roll_rows.append(
                    {
                        "order_id": i,
                        "user_ref": ext,
                        "total": round(((i % 11) + 1) * 17.35, 2),
                        "created_at": f"2024-01-{(i % 28) + 1:02d}",
                    }
                )
                ts = f"2024-01-{(i % 28) + 1:02d}T{(i % 23):02d}:00:00+00:00"
                event_rows.append(
                    {
                        "id": i,
                        "user_ref": ext,
                        "event_type": "purchase" if i % 3 else "view",
                        "ts": ts,
                    }
                )
                adjustment = None if i % 20 == 0 else round(((i % 17) - 8) * 0.1375, 4)
                if messy and i % 37 == 0:
                    adjustment = round(((i % 33) - 16) * 7.4321, 4)
                amount = round(((i % 19) + 1) * 91.2375, 4)
                if messy and i % 111 == 0:
                    amount = 123456789.4321
                payment_rows.append(
                    {
                        "id": i,
                        "user_ref": ext,
                        "amount": amount,
                        "adjustment": adjustment,
                        "currency": "USD" if i % 5 else "EUR",
                        "created_at": f"2024-01-{(i % 28) + 1:02d}",
                    }
                )
            conn.execute(
                text(
                    f"INSERT INTO {_qt(source_schema, 'users')} (id, external_user_id, email, phone, status, created_at) VALUES (:id, :external_user_id, :email, :phone, :status, :created_at)"
                ),
                user_rows,
            )
            conn.execute(
                text(
                    f"INSERT INTO {_qt(source_schema, 'orders')} (id, user_ref, status, total, created_at) VALUES (:id, :user_ref, :status, :total, :created_at)"
                ),
                order_rows,
            )
            conn.execute(
                text(
                    f"INSERT INTO {_qt(source_schema, 'order_rollup')} (order_id, user_ref, total, created_at) VALUES (:order_id, :user_ref, :total, :created_at)"
                ),
                roll_rows,
            )
            conn.execute(
                text(
                    f"INSERT INTO {_qt(source_schema, 'events')} (id, user_ref, event_type, ts) VALUES (:id, :user_ref, :event_type, :ts)"
                ),
                event_rows,
            )
            conn.execute(
                text(
                    f"INSERT INTO {_qt(source_schema, 'payments')} (id, user_ref, amount, adjustment, currency, created_at) VALUES (:id, :user_ref, :amount, :adjustment, :currency, :created_at)"
                ),
                payment_rows,
            )
    finally:
        engine.dispose()

    graph_path = base_dir / "schema_graph.json"
    graph = SchemaGraphBuilder(
        db_url, collect_stats=True, store_path=str(base_dir / "graph_store.db")
    ).build(label="validation_demo_postgres")
    graph_path.write_text(json.dumps(graph, indent=2, default=str), encoding="utf-8")

    drift = {
        "events": [
            {
                "change_type": "COLUMN_RENAMED",
                "node_id": "orders.user_ref",
                "before": {"name": "user_ref", "dtype": "VARCHAR(255)", "nullable": False},
                "after": {"name": "user_key", "dtype": "VARCHAR(255)", "nullable": False},
                "detail": "renamed to user_key",
            },
            {
                "change_type": "TYPE_CHANGED",
                "node_id": "users.external_user_id",
                "before": {
                    "dtype": "VARCHAR(255)",
                    "nullable": False,
                    "sample_values": [f"cust_000001_" + ("x" * 60)],
                },
                "after": {
                    "dtype": "VARCHAR(50)",
                    "nullable": False,
                    "sample_values": [f"cust_000001_" + ("x" * 40)],
                },
                "detail": "narrowed external_user_id to VARCHAR(50)",
            },
            {
                "change_type": "NULLABLE_CHANGED",
                "node_id": "users.phone",
                "before": {
                    "dtype": "VARCHAR(32)",
                    "nullable": True,
                    "null_rate": 0.11 if messy else 0.04,
                },
                "after": {"dtype": "VARCHAR(32)", "nullable": False},
                "detail": "hardened phone to NOT NULL",
            },
            {
                "change_type": "TYPE_CHANGED",
                "node_id": "payments.amount",
                "before": {
                    "dtype": "NUMERIC(18,4)",
                    "nullable": False,
                    "sample_values": ["91.2375", "182.4750"],
                },
                "after": {
                    "dtype": "NUMERIC(10,2)",
                    "nullable": False,
                    "sample_values": ["91.23", "182.47"],
                },
                "detail": "narrowed payment amount precision and scale",
            },
            {
                "change_type": "STATS_DRIFTED",
                "node_id": "users.status",
                "before": {
                    "null_rate": 0.0,
                    "cardinality": 0.5,
                    "sample_values": ["active", "paused"],
                },
                "after": {
                    "null_rate": 0.0,
                    "cardinality": 0.75,
                    "sample_values": ["active", "paused", "archived"],
                },
                "detail": "new archived domain value introduced upstream",
            },
            {
                "change_type": "TYPE_CHANGED",
                "node_id": "events.ts",
                "before": {"dtype": "TIMESTAMP_TZ", "nullable": False},
                "after": {"dtype": "TIMESTAMP_NTZ", "nullable": False},
                "detail": "timezone stripped from event timestamp",
            },
            {
                "change_type": "STATS_DRIFTED",
                "node_id": "payments.adjustment",
                "before": {
                    "null_rate": 0.05,
                    "cardinality": 0.65,
                    "sample_values": ["0.1375", "-0.1375", "0.0000"],
                },
                "after": {
                    "null_rate": 0.45,
                    "cardinality": 0.18,
                    "sample_values": [None, "0.0000"],
                },
                "detail": "adjustment distribution regressed and null rate spiked",
            },
        ]
    }
    if black_swan:
        drift["events"].extend(
            [
                {
                    "change_type": "TYPE_CHANGED",
                    "node_id": "subscription_events.mrr",
                    "before": {"dtype": "NUMERIC(18,2)", "nullable": False},
                    "after": {"dtype": "NUMERIC(10,2)", "nullable": False},
                    "detail": "narrowed recurring revenue precision under mixed-plan workload",
                },
                {
                    "change_type": "STATS_DRIFTED",
                    "node_id": "support_tickets.severity",
                    "before": {
                        "null_rate": 0.0,
                        "cardinality": 0.35,
                        "sample_values": ["low", "medium", "high", "urgent"],
                    },
                    "after": {
                        "null_rate": 0.0,
                        "cardinality": 0.48,
                        "sample_values": ["low", "medium", "high", "urgent", "sev0"],
                    },
                    "detail": "support severity domain expanded with sev0 and stale downstream bucket logic",
                },
            ]
        )
    drift_path = base_dir / "drift_report.json"
    drift_path.write_text(json.dumps(drift, indent=2), encoding="utf-8")
    migration_path = base_dir / "migration.sql"
    migration_path.write_text(
        "\n".join(
            [
                "ALTER TABLE orders RENAME COLUMN user_ref TO user_key;",
                "-- SQLite demo pack keeps narrowing/nullability hazards in the drift report for static validation while applying a safe runtime rename for replay.",
                "-- ALTER TABLE users ALTER COLUMN phone SET NOT NULL;",
                "-- ALTER TABLE payments ALTER COLUMN amount TYPE NUMERIC(10,2);",
            ]
        ),
        encoding="utf-8",
    )
    workload_path = base_dir / "validation_workload.sql"
    workload_queries = [
        "SELECT COUNT(*) FROM orders o JOIN users u ON o.user_ref = u.external_user_id;",
        "SELECT status, COUNT(*) FROM users WHERE status IN ('active','paused') GROUP BY status;",
        "SELECT DATE(ts) AS day_bucket, COUNT(*) FROM events GROUP BY DATE(ts);",
        "SELECT SUM(total) FROM orders;",
        "SELECT currency, ROUND(SUM(amount + COALESCE(adjustment, 0)), 2) FROM payments GROUP BY currency;",
        "SELECT AVG(adjustment) FROM payments WHERE adjustment IS NOT NULL;",
    ]
    if profile in {"chaos_labyrinth", "black_swan"}:
        workload_queries.extend(
            [
                "SELECT u.status, s.campaign, COUNT(DISTINCT s.session_id) FROM sessions s JOIN users u ON s.user_ref = u.external_user_id WHERE COALESCE(s.campaign,'unknown') <> 'internal' GROUP BY 1,2;",
                "SELECT DATE(e.ts) AS bucket, SUM(CASE WHEN e.event_type='purchase' THEN p.amount ELSE 0 END) FROM events e JOIN payments p ON e.user_ref = p.user_ref GROUP BY DATE(e.ts);",
                "SELECT o.user_ref, COUNT(*) AS order_count, SUM(o.total) AS gmv FROM orders o JOIN sessions s ON o.user_ref = s.user_ref GROUP BY o.user_ref HAVING COUNT(*) > 1;",
                "SELECT campaign, AVG((julianday(COALESCE(ended_at, started_at)) - julianday(started_at))*24.0) AS session_hours FROM sessions GROUP BY campaign;",
            ]
        )
    if black_swan:
        workload_queries.extend(
            [
                "WITH latest_sub AS (SELECT user_ref, plan_code, state, mrr, ROW_NUMBER() OVER (PARTITION BY user_ref ORDER BY effective_at DESC) rn FROM subscription_events) SELECT plan_code, state, ROUND(SUM(mrr),2) FROM latest_sub WHERE rn = 1 GROUP BY plan_code, state;",
                "SELECT COALESCE(s.campaign,'unknown') AS campaign, t.severity, COUNT(*) AS open_ticket_sessions FROM sessions s JOIN support_tickets t ON s.user_ref = t.user_ref WHERE t.status <> 'closed' GROUP BY 1,2;",
                "SELECT o.user_ref, ROUND(SUM(o.total) - COALESCE(SUM(r.amount),0), 2) AS net_revenue FROM orders o LEFT JOIN refunds r ON o.id = r.order_id GROUP BY o.user_ref HAVING net_revenue > 0;",
                "WITH daily AS (SELECT DATE(e.ts) AS day_bucket, COUNT(*) AS event_cnt FROM events e GROUP BY DATE(e.ts)), paid AS (SELECT DATE(created_at) AS day_bucket, SUM(amount) AS paid_amt FROM payments GROUP BY DATE(created_at)) SELECT d.day_bucket, d.event_cnt, COALESCE(p.paid_amt,0) FROM daily d LEFT JOIN paid p ON d.day_bucket = p.day_bucket;",
                "SELECT u.status, se.state, COUNT(DISTINCT u.external_user_id) FROM users u JOIN subscription_events se ON u.external_user_id = se.user_ref WHERE u.status IN ('active','paused') AND se.state IN ('active','past_due') GROUP BY 1,2;",
            ]
        )
    workload_path.write_text("\n".join(workload_queries), encoding="utf-8")
    proof_root = base_dir / "proof"
    proof_root.mkdir(exist_ok=True)
    (proof_root / "stg_orders.sql").write_text(
        """
        {{ config(materialized='incremental', unique_key='id') }}
        SELECT o.id, o.user_ref, o.total
        FROM orders o
        JOIN users u ON o.user_ref = u.external_user_id
        WHERE u.status IN ('active','paused')
        {% if is_incremental() %}
          AND o.created_at >= (SELECT COALESCE(MAX(created_at), '1900-01-01') FROM {{ this }})
        {% endif %}
        """.strip(),
        encoding="utf-8",
    )
    (proof_root / "fct_payments.sql").write_text(
        """
        SELECT currency, SUM(amount + COALESCE(adjustment, 0)) AS gross_revenue
        FROM payments
        GROUP BY currency
        """.strip(),
        encoding="utf-8",
    )
    (proof_root / "schema.prisma").write_text(
        """
        enum UserStatus {
          active
          paused
          archived
        }
        model User {
          id               Int      @id
          external_user_id String   @db.VarChar(255)
          phone            String?  @db.VarChar(32)
          status           UserStatus
        }
        """.strip(),
        encoding="utf-8",
    )
    (proof_root / "user_model.ts").write_text(
        """
        export type UserStatus = 'active' | 'paused' | 'archived';
        export interface WarehouseUserContract {
          external_user_id: string;
          phone?: string | null;
          status: UserStatus;
        }
        """.strip(),
        encoding="utf-8",
    )
    if profile in {"chaos_labyrinth", "black_swan"}:
        (proof_root / "session_rollup.sql").write_text(
            """
            WITH session_base AS (
              SELECT s.user_ref, COALESCE(s.campaign, 'unknown') AS campaign, s.session_id, s.started_at, s.ended_at
              FROM sessions s
              LEFT JOIN users u ON s.user_ref = u.external_user_id
              WHERE u.status IN ('active','paused')
            ),
            campaign_rollup AS (
              SELECT user_ref, campaign, COUNT(DISTINCT session_id) AS session_count
              FROM session_base
              GROUP BY user_ref, campaign
            )
            SELECT c.user_ref, c.campaign, c.session_count, p.amount + COALESCE(p.adjustment,0) AS net_amount
            FROM campaign_rollup c
            LEFT JOIN payments p ON c.user_ref = p.user_ref
        """.strip(),
            encoding="utf-8",
        )
        (proof_root / "session_enrichment.py").write_text(
            """
import pandas as pd

base = pd.read_sql("select user_ref, campaign, session_id, started_at, ended_at from sessions", conn)
orders = pd.read_sql("select user_ref, total, status from orders", conn)
renamed = orders.rename(columns={"total": "gross_total"})
joined = base.merge(renamed, on="user_ref", how="left")
enriched = joined.assign(active_status=joined["status"], revenue_bucket=joined["gross_total"])
filtered = enriched.query("active_status == 'completed'")
""".strip(),
            encoding="utf-8",
        )
        if black_swan:
            (proof_root / "subscription_margin.sql").write_text(
                """
                WITH latest_sub AS (
                  SELECT user_ref, plan_code, state, mrr,
                         ROW_NUMBER() OVER (PARTITION BY user_ref ORDER BY effective_at DESC) AS rn
                  FROM subscription_events
                ),
                current_sub AS (
                  SELECT user_ref, plan_code, state, mrr
                  FROM latest_sub
                  WHERE rn = 1 AND state IN ('active','past_due')
                ),
                refund_rollup AS (
                  SELECT user_ref, SUM(amount) AS refunded_amount
                  FROM refunds
                  GROUP BY user_ref
                )
                SELECT c.user_ref, c.plan_code, c.state, c.mrr - COALESCE(r.refunded_amount, 0) AS net_mrr
                FROM current_sub c
                LEFT JOIN refund_rollup r ON c.user_ref = r.user_ref
            """.strip(),
                encoding="utf-8",
            )
            (proof_root / "ticket_risk.py").write_text(
                """
import pandas as pd

tickets = pd.read_sql("select user_ref, severity, status, opened_at, resolved_at from support_tickets", conn)
sessions = pd.read_sql("select user_ref, campaign, session_id from sessions", conn)
subs = pd.read_sql("select user_ref, plan_code, state, mrr from subscription_events", conn)
joined = tickets.merge(sessions, on="user_ref", how="left").merge(subs, on="user_ref", how="left")
renamed = joined.rename(columns={"mrr": "recurring_revenue"})
scored = renamed.assign(active_subscription=renamed["state"], issue_weight=renamed["severity"], revenue_at_risk=renamed["recurring_revenue"])
filtered = scored.query("status != 'closed' and active_subscription in ['active', 'past_due']")
""".strip(),
                encoding="utf-8",
            )
    return DemoValidationPack(
        db_url=db_url,
        graph_path=str(graph_path),
        drift_path=str(drift_path),
        migration_path=str(migration_path),
        workload_path=str(workload_path),
        proof_paths=[str(proof_root)],
    )


def build_demo_validation_pack(
    base_dir: Path, scale: str = "medium", profile: str = "standard"
) -> DemoValidationPack:
    base_dir.mkdir(parents=True, exist_ok=True)
    db_path = base_dir / "validation_demo.db"
    row_count = _SCALE_ROWS.get(scale, _SCALE_ROWS["medium"])
    messy = profile in {"messy", "finance", "chaos_labyrinth", "black_swan"}
    black_swan = profile == "black_swan"
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            external_user_id TEXT NOT NULL,
            email TEXT NOT NULL,
            phone TEXT,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY,
            user_ref TEXT NOT NULL,
            status TEXT NOT NULL,
            total REAL NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE order_rollup (
            order_id INTEGER PRIMARY KEY,
            user_ref TEXT NOT NULL,
            total REAL NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE events (
            id INTEGER PRIMARY KEY,
            user_ref TEXT NOT NULL,
            event_type TEXT NOT NULL,
            ts TEXT NOT NULL
        );
        CREATE TABLE payments (
            id INTEGER PRIMARY KEY,
            user_ref TEXT NOT NULL,
            amount REAL NOT NULL,
            adjustment REAL,
            currency TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE sessions (
            id INTEGER PRIMARY KEY,
            user_ref TEXT NOT NULL,
            session_id TEXT NOT NULL,
            campaign TEXT,
            started_at TEXT NOT NULL,
            ended_at TEXT
        );
        CREATE TABLE subscription_events (
            id INTEGER PRIMARY KEY,
            user_ref TEXT NOT NULL,
            plan_code TEXT NOT NULL,
            state TEXT NOT NULL,
            effective_at TEXT NOT NULL,
            mrr REAL NOT NULL
        );
        CREATE TABLE support_tickets (
            id INTEGER PRIMARY KEY,
            user_ref TEXT NOT NULL,
            severity TEXT NOT NULL,
            status TEXT NOT NULL,
            opened_at TEXT NOT NULL,
            resolved_at TEXT
        );
        CREATE TABLE refunds (
            id INTEGER PRIMARY KEY,
            order_id INTEGER NOT NULL,
            user_ref TEXT NOT NULL,
            amount REAL NOT NULL,
            reason TEXT,
            created_at TEXT NOT NULL
        );
        """
    )
    users = []
    orders = []
    rollup = []
    events = []
    payments = []
    sessions = []
    subscription_events = []
    support_tickets = []
    refunds = []
    statuses = ["active", "paused"]
    for idx in range(1, row_count + 1):
        ext = f"cust_{idx:06d}_" + ("x" * 60)
        phone = None if (messy and idx % 9 == 0) else f"+1-555-{idx:06d}"
        users.append(
            (
                idx,
                ext,
                f"user{idx}@demo.local",
                phone,
                statuses[idx % 2],
                f"2024-01-{(idx % 28) + 1:02d}",
            )
        )
        order_status = "completed" if idx % 4 else "pending"
        total = round(((idx % 11) + 1) * 17.35, 2)
        created = f"2024-01-{(idx % 28) + 1:02d}"
        orders.append((idx, ext, order_status, total, created))
        rollup.append((idx, ext, total, created))
        events.append(
            (
                idx,
                ext,
                "purchase" if idx % 3 else "view",
                f"2024-01-{(idx % 28) + 1:02d}T{(idx % 23):02d}:00:00+00:00",
            )
        )
        adjustment = None if idx % 20 == 0 else round(((idx % 17) - 8) * 0.1375, 4)
        if messy and idx % 37 == 0:
            adjustment = round(((idx % 33) - 16) * 7.4321, 4)
        amount = round(((idx % 19) + 1) * 91.2375, 4)
        if messy and idx % 111 == 0:
            amount = 123456789.4321
        payments.append((idx, ext, amount, adjustment, "USD" if idx % 5 else "EUR", created))
        session_id = f"sess_{idx:07d}" if idx % 23 else f"sess_dup_{idx % 11}"
        campaign = (
            None if (messy and idx % 14 == 0) else ("paid_search" if idx % 4 == 0 else "organic")
        )
        ended = (
            None
            if (messy and idx % 29 == 0)
            else f"2024-01-{(idx % 28) + 1:02d}T{((idx + 1) % 23):02d}:00:00+00:00"
        )
        sessions.append(
            (
                idx,
                ext,
                session_id,
                campaign,
                f"2024-01-{(idx % 28) + 1:02d}T{(idx % 23):02d}:00:00+00:00",
                ended,
            )
        )
        if black_swan:
            plan = ["free", "starter", "growth", "enterprise"][idx % 4]
            sub_state = "active" if idx % 5 else ("past_due" if idx % 7 else "canceled")
            if idx % 41 == 0:
                sub_state = "grace_period"
            mrr = round(((idx % 13) + 1) * 42.75, 2)
            if idx % 97 == 0:
                mrr = round(mrr * 11.0, 2)
            subscription_events.append(
                (
                    idx,
                    ext,
                    plan,
                    sub_state,
                    f"2024-02-{(idx % 27) + 1:02d}T{(idx % 23):02d}:15:00+00:00",
                    mrr,
                )
            )
            sev = ["low", "medium", "high", "urgent"][idx % 4]
            ticket_status = "open" if idx % 6 == 0 else ("pending" if idx % 5 == 0 else "closed")
            resolved = (
                None
                if ticket_status != "closed"
                else f"2024-02-{(idx % 27) + 1:02d}T{((idx + 3) % 23):02d}:45:00+00:00"
            )
            if idx % 53 == 0:
                sev = "sev0"
            support_tickets.append(
                (
                    idx,
                    ext,
                    sev,
                    ticket_status,
                    f"2024-02-{(idx % 27) + 1:02d}T{(idx % 23):02d}:30:00+00:00",
                    resolved,
                )
            )
            if idx % 8 == 0:
                refund_reason = None if idx % 16 else "duplicate_charge"
                refund_amount = round(total * (0.25 if idx % 24 else 1.0), 2)
                refunds.append(
                    (idx, idx, ext, refund_amount, refund_reason, f"2024-02-{(idx % 27) + 1:02d}")
                )
    cur.executemany("INSERT INTO users VALUES (?,?,?,?,?,?)", users)
    cur.executemany("INSERT INTO orders VALUES (?,?,?,?,?)", orders)
    cur.executemany("INSERT INTO order_rollup VALUES (?,?,?,?)", rollup)
    cur.executemany("INSERT INTO events VALUES (?,?,?,?)", events)
    cur.executemany("INSERT INTO payments VALUES (?,?,?,?,?,?)", payments)
    cur.executemany("INSERT INTO sessions VALUES (?,?,?,?,?,?)", sessions)
    if black_swan:
        cur.executemany("INSERT INTO subscription_events VALUES (?,?,?,?,?,?)", subscription_events)
        cur.executemany("INSERT INTO support_tickets VALUES (?,?,?,?,?,?)", support_tickets)
        cur.executemany("INSERT INTO refunds VALUES (?,?,?,?,?,?)", refunds)
    conn.commit()
    conn.close()

    db_url = f"sqlite:///{db_path}"
    graph_path = base_dir / "schema_graph.json"
    graph = SchemaGraphBuilder(
        db_url, collect_stats=True, store_path=str(base_dir / "graph_store.db")
    ).build(label="validation_demo")
    graph_path.write_text(json.dumps(graph, indent=2, default=str), encoding="utf-8")

    drift = {
        "events": [
            {
                "change_type": "COLUMN_RENAMED",
                "node_id": "orders.user_ref",
                "before": {"name": "user_ref", "dtype": "VARCHAR(255)", "nullable": False},
                "after": {"name": "user_key", "dtype": "VARCHAR(255)", "nullable": False},
                "detail": "renamed to user_key",
            },
            {
                "change_type": "TYPE_CHANGED",
                "node_id": "users.external_user_id",
                "before": {
                    "dtype": "VARCHAR(255)",
                    "nullable": False,
                    "sample_values": [users[0][1]],
                },
                "after": {
                    "dtype": "VARCHAR(50)",
                    "nullable": False,
                    "sample_values": [users[0][1][:40]],
                },
                "detail": "narrowed external_user_id to VARCHAR(50)",
            },
            {
                "change_type": "NULLABLE_CHANGED",
                "node_id": "users.phone",
                "before": {
                    "dtype": "VARCHAR(32)",
                    "nullable": True,
                    "null_rate": 0.11 if messy else 0.04,
                },
                "after": {"dtype": "VARCHAR(32)", "nullable": False},
                "detail": "hardened phone to NOT NULL",
            },
            {
                "change_type": "TYPE_CHANGED",
                "node_id": "payments.amount",
                "before": {
                    "dtype": "NUMERIC(18,4)",
                    "nullable": False,
                    "sample_values": [str(payments[0][2])],
                },
                "after": {
                    "dtype": "NUMERIC(10,2)",
                    "nullable": False,
                    "sample_values": [f"{payments[0][2]:.2f}"],
                },
                "detail": "narrowed payment amount precision and scale",
            },
            {
                "change_type": "STATS_DRIFTED",
                "node_id": "users.status",
                "before": {
                    "null_rate": 0.0,
                    "cardinality": 0.5,
                    "sample_values": ["active", "paused"],
                },
                "after": {
                    "null_rate": 0.0,
                    "cardinality": 0.75,
                    "sample_values": ["active", "paused", "archived"],
                },
                "detail": "new archived domain value introduced upstream",
            },
            {
                "change_type": "TYPE_CHANGED",
                "node_id": "events.ts",
                "before": {"dtype": "TIMESTAMP_TZ", "nullable": False},
                "after": {"dtype": "TIMESTAMP_NTZ", "nullable": False},
                "detail": "timezone stripped from event timestamp",
            },
            {
                "change_type": "STATS_DRIFTED",
                "node_id": "payments.adjustment",
                "before": {
                    "null_rate": 0.05,
                    "cardinality": 0.65,
                    "sample_values": ["0.1375", "-0.1375", "0.0000"],
                },
                "after": {
                    "null_rate": 0.45,
                    "cardinality": 0.18,
                    "sample_values": [None, "0.0000"],
                },
                "detail": "adjustment distribution regressed and null rate spiked",
            },
        ]
    }
    if black_swan:
        drift["events"].extend(
            [
                {
                    "change_type": "TYPE_CHANGED",
                    "node_id": "subscription_events.mrr",
                    "before": {"dtype": "NUMERIC(18,2)", "nullable": False},
                    "after": {"dtype": "NUMERIC(10,2)", "nullable": False},
                    "detail": "narrowed recurring revenue precision under mixed-plan workload",
                },
                {
                    "change_type": "STATS_DRIFTED",
                    "node_id": "support_tickets.severity",
                    "before": {
                        "null_rate": 0.0,
                        "cardinality": 0.35,
                        "sample_values": ["low", "medium", "high", "urgent"],
                    },
                    "after": {
                        "null_rate": 0.0,
                        "cardinality": 0.48,
                        "sample_values": ["low", "medium", "high", "urgent", "sev0"],
                    },
                    "detail": "support severity domain expanded with sev0 and stale downstream bucket logic",
                },
            ]
        )
    drift_path = base_dir / "drift_report.json"
    drift_path.write_text(json.dumps(drift, indent=2), encoding="utf-8")

    migration_path = base_dir / "migration.sql"
    migration_path.write_text(
        "\n".join(
            [
                "ALTER TABLE orders RENAME COLUMN user_ref TO user_key;",
                "-- SQLite demo pack keeps narrowing/nullability hazards in the drift report for static validation while applying a safe runtime rename for replay.",
                "-- ALTER TABLE users ALTER COLUMN phone SET NOT NULL;",
                "-- ALTER TABLE payments ALTER COLUMN amount TYPE NUMERIC(10,2);",
            ]
        ),
        encoding="utf-8",
    )

    workload_path = base_dir / "validation_workload.sql"
    workload_queries = [
        "SELECT COUNT(*) FROM orders o JOIN users u ON o.user_ref = u.external_user_id;",
        "SELECT status, COUNT(*) FROM users WHERE status IN ('active','paused') GROUP BY status;",
        "SELECT DATE(ts) AS day_bucket, COUNT(*) FROM events GROUP BY DATE(ts);",
        "SELECT SUM(total) FROM orders;",
        "SELECT currency, ROUND(SUM(amount + COALESCE(adjustment, 0)), 2) FROM payments GROUP BY currency;",
        "SELECT AVG(adjustment) FROM payments WHERE adjustment IS NOT NULL;",
    ]
    if profile in {"chaos_labyrinth", "black_swan"}:
        workload_queries.extend(
            [
                "SELECT u.status, s.campaign, COUNT(DISTINCT s.session_id) FROM sessions s JOIN users u ON s.user_ref = u.external_user_id WHERE COALESCE(s.campaign,'unknown') <> 'internal' GROUP BY 1,2;",
                "SELECT DATE(e.ts) AS bucket, SUM(CASE WHEN e.event_type='purchase' THEN p.amount ELSE 0 END) FROM events e JOIN payments p ON e.user_ref = p.user_ref GROUP BY DATE(e.ts);",
                "SELECT o.user_ref, COUNT(*) AS order_count, SUM(o.total) AS gmv FROM orders o JOIN sessions s ON o.user_ref = s.user_ref GROUP BY o.user_ref HAVING COUNT(*) > 1;",
                "SELECT campaign, AVG((julianday(COALESCE(ended_at, started_at)) - julianday(started_at))*24.0) AS session_hours FROM sessions GROUP BY campaign;",
            ]
        )
    if black_swan:
        workload_queries.extend(
            [
                "WITH latest_sub AS (SELECT user_ref, plan_code, state, mrr, ROW_NUMBER() OVER (PARTITION BY user_ref ORDER BY effective_at DESC) rn FROM subscription_events) SELECT plan_code, state, ROUND(SUM(mrr),2) FROM latest_sub WHERE rn = 1 GROUP BY plan_code, state;",
                "SELECT COALESCE(s.campaign,'unknown') AS campaign, t.severity, COUNT(*) AS open_ticket_sessions FROM sessions s JOIN support_tickets t ON s.user_ref = t.user_ref WHERE t.status <> 'closed' GROUP BY 1,2;",
                "SELECT o.user_ref, ROUND(SUM(o.total) - COALESCE(SUM(r.amount),0), 2) AS net_revenue FROM orders o LEFT JOIN refunds r ON o.id = r.order_id GROUP BY o.user_ref HAVING net_revenue > 0;",
                "WITH daily AS (SELECT DATE(e.ts) AS day_bucket, COUNT(*) AS event_cnt FROM events e GROUP BY DATE(e.ts)), paid AS (SELECT DATE(created_at) AS day_bucket, SUM(amount) AS paid_amt FROM payments GROUP BY DATE(created_at)) SELECT d.day_bucket, d.event_cnt, COALESCE(p.paid_amt,0) FROM daily d LEFT JOIN paid p ON d.day_bucket = p.day_bucket;",
                "SELECT u.status, se.state, COUNT(DISTINCT u.external_user_id) FROM users u JOIN subscription_events se ON u.external_user_id = se.user_ref WHERE u.status IN ('active','paused') AND se.state IN ('active','past_due') GROUP BY 1,2;",
            ]
        )
    workload_path.write_text("\n".join(workload_queries), encoding="utf-8")

    proof_root = base_dir / "proof"
    proof_root.mkdir(exist_ok=True)
    (proof_root / "stg_orders.sql").write_text(
        """
        {{ config(materialized='incremental', unique_key='id') }}
        SELECT o.id, o.user_ref, o.total
        FROM orders o
        JOIN users u ON o.user_ref = u.external_user_id
        WHERE u.status IN ('active','paused')
        {% if is_incremental() %}
          AND o.created_at >= (SELECT COALESCE(MAX(created_at), '1900-01-01') FROM {{ this }})
        {% endif %}
        """.strip(),
        encoding="utf-8",
    )
    (proof_root / "fct_payments.sql").write_text(
        """
        SELECT currency, SUM(amount + COALESCE(adjustment, 0)) AS gross_revenue
        FROM payments
        GROUP BY currency
        """.strip(),
        encoding="utf-8",
    )
    (proof_root / "schema.prisma").write_text(
        """
        enum UserStatus {
          active
          paused
          archived
        }
        model User {
          id               Int      @id
          external_user_id String   @db.VarChar(255)
          phone            String?  @db.VarChar(32)
          status           UserStatus
        }
        """.strip(),
        encoding="utf-8",
    )
    (proof_root / "user_model.ts").write_text(
        """
        export type UserStatus = 'active' | 'paused' | 'archived';
        export interface WarehouseUserContract {
          external_user_id: string;
          phone?: string | null;
          status: UserStatus;
        }
        """.strip(),
        encoding="utf-8",
    )
    if profile in {"chaos_labyrinth", "black_swan"}:
        (proof_root / "session_rollup.sql").write_text(
            """
            WITH session_base AS (
              SELECT s.user_ref, COALESCE(s.campaign, 'unknown') AS campaign, s.session_id, s.started_at, s.ended_at
              FROM sessions s
              LEFT JOIN users u ON s.user_ref = u.external_user_id
              WHERE u.status IN ('active','paused')
            ),
            campaign_rollup AS (
              SELECT user_ref, campaign, COUNT(DISTINCT session_id) AS session_count
              FROM session_base
              GROUP BY user_ref, campaign
            )
            SELECT c.user_ref, c.campaign, c.session_count, p.amount + COALESCE(p.adjustment,0) AS net_amount
            FROM campaign_rollup c
            LEFT JOIN payments p ON c.user_ref = p.user_ref
        """.strip(),
            encoding="utf-8",
        )
        (proof_root / "session_enrichment.py").write_text(
            """
import pandas as pd

base = pd.read_sql("select user_ref, campaign, session_id, started_at, ended_at from sessions", conn)
orders = pd.read_sql("select user_ref, total, status from orders", conn)
renamed = orders.rename(columns={"total": "gross_total"})
joined = base.merge(renamed, on="user_ref", how="left")
enriched = joined.assign(active_status=joined["status"], revenue_bucket=joined["gross_total"])
filtered = enriched.query("active_status == 'completed'")
""".strip(),
            encoding="utf-8",
        )
        if black_swan:
            (proof_root / "subscription_margin.sql").write_text(
                """
                WITH latest_sub AS (
                  SELECT user_ref, plan_code, state, mrr,
                         ROW_NUMBER() OVER (PARTITION BY user_ref ORDER BY effective_at DESC) AS rn
                  FROM subscription_events
                ),
                current_sub AS (
                  SELECT user_ref, plan_code, state, mrr
                  FROM latest_sub
                  WHERE rn = 1 AND state IN ('active','past_due')
                ),
                refund_rollup AS (
                  SELECT user_ref, SUM(amount) AS refunded_amount
                  FROM refunds
                  GROUP BY user_ref
                )
                SELECT c.user_ref, c.plan_code, c.state, c.mrr - COALESCE(r.refunded_amount, 0) AS net_mrr
                FROM current_sub c
                LEFT JOIN refund_rollup r ON c.user_ref = r.user_ref
            """.strip(),
                encoding="utf-8",
            )
            (proof_root / "ticket_risk.py").write_text(
                """
import pandas as pd

tickets = pd.read_sql("select user_ref, severity, status, opened_at, resolved_at from support_tickets", conn)
sessions = pd.read_sql("select user_ref, campaign, session_id from sessions", conn)
subs = pd.read_sql("select user_ref, plan_code, state, mrr from subscription_events", conn)
joined = tickets.merge(sessions, on="user_ref", how="left").merge(subs, on="user_ref", how="left")
renamed = joined.rename(columns={"mrr": "recurring_revenue"})
scored = renamed.assign(active_subscription=renamed["state"], issue_weight=renamed["severity"], revenue_at_risk=renamed["recurring_revenue"])
filtered = scored.query("status != 'closed' and active_subscription in ['active', 'past_due']")
""".strip(),
                encoding="utf-8",
            )
    return DemoValidationPack(
        db_url=db_url,
        graph_path=str(graph_path),
        drift_path=str(drift_path),
        migration_path=str(migration_path),
        workload_path=str(workload_path),
        proof_paths=[str(proof_root)],
    )
