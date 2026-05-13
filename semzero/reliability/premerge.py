from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..chaos.chaos_engine import ChaosConfig, ChaosEngine
from ..chaos.wind_tunnel import MigrationWindTunnel, WindTunnelConfig
from ..integrations.change_gate import ChangeGate, GateConfig
from ..reporting.live_report import UnifiedOpsReport
from ..utils.live_readiness import build_live_readiness_report, resolve_live_mode
from .evidence import EvidenceBundle, EvidenceStore
from .shadow_mode import ShadowDashboard, ShadowRunLedger


@dataclass
class PremergeWorkflowConfig:
    db_url: str = ""
    github_repo: str = ""
    github_token: str = ""
    data_owner_team: str = ""
    data_dir: str = "data"
    strict_mode: bool = False
    proof_paths: list[str] = field(default_factory=list)
    run_wind_tunnel: bool = True
    run_chaos: bool = False
    wind_live_mode: str = "safe"
    chaos_live_mode: str = "safe"
    keep_clone: bool = False
    chaos_mutation_count: int = 12
    wind_tunnel_max_queries: int = 100
    workload_query_files: list[str] = field(default_factory=list)
    workload_history_files: list[str] = field(default_factory=list)
    dbt_manifest_path: str = ""
    dbt_catalog_path: str = ""
    dbt_run_results_path: str = ""
    dbt_sources_path: str = ""
    openlineage_paths: list[str] = field(default_factory=list)
    airflow_paths: list[str] = field(default_factory=list)
    dagster_paths: list[str] = field(default_factory=list)
    looker_paths: list[str] = field(default_factory=list)
    montecarlo_paths: list[str] = field(default_factory=list)
    graph_intelligence_enabled: bool = True
    rgcn_model_path: str = ""
    shadow_mode: bool = False
    evidence_store_path: str = ""


@dataclass
class PremergeBundle:
    gate_result: dict[str, Any]
    wind_tunnel_receipt: Optional[dict[str, Any]] = None
    chaos_report: Optional[dict[str, Any]] = None
    unified_report_markdown: str = ""
    evidence_summary: dict[str, Any] = field(default_factory=dict)
    artifact_paths: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate_result": self.gate_result,
            "wind_tunnel_receipt": self.wind_tunnel_receipt,
            "chaos_report": self.chaos_report,
            "evidence_summary": self.evidence_summary,
            "artifact_paths": self.artifact_paths,
        }

    def save(self, path: str) -> Path:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(self.to_dict(), indent=2, default=str), encoding="utf-8")
        return out


class PremergeWorkflow:
    """One-command premerge workflow for live data systems.

    It keeps the platform in the data-reliability lane:
      1. Gate for zero/low-compute proofing
      2. Wind Tunnel for scoped replay when the Gate says it matters
      3. Chaos for silent-failure verification only on high-risk assets
    """

    def __init__(self, graph_json: dict, config: PremergeWorkflowConfig) -> None:
        self.graph_json = graph_json
        self.config = config
        self.data_dir = Path(config.data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def run(
        self,
        drift_report: dict,
        migration_sql: str = "",
        pr_number: Optional[int] = None,
    ) -> PremergeBundle:
        gate_config = GateConfig(
            github_token=self.config.github_token,
            github_repo=self.config.github_repo,
            data_owner_team=self.config.data_owner_team,
            strict_mode=self.config.strict_mode,
            db_url=self.config.db_url,
            proof_enabled=True,
            proof_source_paths=list(self.config.proof_paths),
            data_dir=str(self.data_dir),
            dbt_manifest_path=self.config.dbt_manifest_path,
            dbt_catalog_path=self.config.dbt_catalog_path,
            dbt_run_results_path=self.config.dbt_run_results_path,
            dbt_sources_path=self.config.dbt_sources_path,
            openlineage_paths=list(self.config.openlineage_paths),
            airflow_paths=list(self.config.airflow_paths),
            dagster_paths=list(self.config.dagster_paths),
            looker_paths=list(self.config.looker_paths),
            montecarlo_paths=list(self.config.montecarlo_paths),
            calibration_store_path=str(self.data_dir / "calibration_history.jsonl"),
            graph_intelligence_enabled=self.config.graph_intelligence_enabled,
            rgcn_model_path=self.config.rgcn_model_path,
            wind_tunnel_max_queries=self.config.wind_tunnel_max_queries,
        )
        wind_receipt: Optional[dict[str, Any]] = None
        chaos_report: Optional[dict[str, Any]] = None
        readiness = None
        evidence = EvidenceBundle(
            mode="shadow" if self.config.shadow_mode else "safe", db_url=self.config.db_url
        )
        evidence.add(
            "pregate",
            "drift_report",
            "OK",
            f"Loaded drift report with {len(drift_report.get('events', []))} event(s)",
            details={"event_count": len(drift_report.get("events", []))},
        )
        if self.config.db_url:
            readiness = build_live_readiness_report(self.config.db_url)
            evidence.add(
                "runtime",
                "live_readiness",
                "OK",
                f"Dialect {readiness.dialect}; clone support={'yes' if readiness.clone_supported else 'no'}",
                details=readiness.to_dict()
                if hasattr(readiness, "to_dict")
                else {
                    "dialect": getattr(readiness, "dialect", "unknown"),
                    "clone_supported": getattr(readiness, "clone_supported", False),
                },
            )

        gate = ChangeGate(self.graph_json, gate_config)
        gate_result_obj = gate.evaluate(drift_report, pr_number=pr_number)
        proof_findings = (
            ((gate_result_obj.proof_bundle or {}).get("findings") or [])
            if isinstance(gate_result_obj.proof_bundle, dict)
            else []
        )
        evidence.add(
            "pregate",
            "gate_verdict",
            gate_result_obj.verdict.value,
            f"PreGate returned {gate_result_obj.verdict.value} with reliability {gate_result_obj.reliability_score:.1f}",
            details={
                "reliability_score": gate_result_obj.reliability_score,
                "oncall_risk": gate_result_obj.oncall_risk,
                "blast_radius": gate_result_obj.total_blast_radius,
            },
        )
        if proof_findings:
            evidence.add(
                "pregate",
                "ast_mapping",
                "OK",
                f"Cross-modal AST proof found {len(proof_findings)} reference(s)",
                details={
                    "finding_count": len(proof_findings),
                    "languages": sorted(
                        {str(item.get("language", "code")).lower() for item in proof_findings}
                    ),
                },
            )
        assumption_findings = (
            ((gate_result_obj.assumption_summary or {}).get("findings") or [])
            if isinstance(gate_result_obj.assumption_summary, dict)
            else []
        )
        if assumption_findings:
            evidence.add(
                "pregate",
                "assumption_gate",
                "OK",
                f"Assumption Gate surfaced {len(assumption_findings)} undocumented downstream assumption(s)",
                details={
                    "assumption_types": sorted(
                        {
                            str(item.get("assumption_type", "ASSUMPTION"))
                            for item in assumption_findings
                        }
                    ),
                    "top_nodes": (gate_result_obj.assumption_summary or {}).get("top_nodes", [])[
                        :5
                    ],
                },
            )
        plan = gate_result_obj.recommended_execution or {}

        if self.config.run_wind_tunnel and self.config.db_url and plan.get("run_wind_tunnel"):
            wind_receipt = self._run_wind_tunnel(drift_report, migration_sql, plan, readiness)
            gate_result_obj.wind_tunnel_receipt = wind_receipt
            evidence.add(
                "wind_tunnel",
                "replay",
                str(wind_receipt.get("verdict", "UNKNOWN")).upper(),
                f"Replayed {wind_receipt.get('queries_replayed', 0)} queries on isolated validation state",
                observed=True,
                details={
                    "queries_replayed": wind_receipt.get("queries_replayed", 0),
                    "queries_broken": wind_receipt.get("queries_broken", 0),
                    "queries_mismatch": wind_receipt.get("queries_mismatch", 0),
                    "clone_name": wind_receipt.get("clone_name") or wind_receipt.get("clone") or "",
                },
            )
            gate._finalise_result(gate_result_obj)

        if self.config.run_chaos and self.config.db_url and plan.get("run_chaos"):
            chaos_report = self._run_chaos(plan, readiness)
            gate_result_obj.chaos_report = chaos_report
            summary = chaos_report.get("summary", {}) if isinstance(chaos_report, dict) else {}
            evidence.add(
                "chaos",
                "mutation_run",
                "OK" if int(summary.get("mutations_that_broke", 0) or 0) == 0 else "FAIL",
                f"Chaos applied {summary.get('mutations_applied', 0)} mutation(s) with {summary.get('mutations_that_broke', 0)} observed break(s)",
                observed=True,
                details={
                    "mutations_applied": summary.get("mutations_applied", 0),
                    "mutations_that_broke": summary.get("mutations_that_broke", 0),
                    "fragility_score": summary.get(
                        "fragility_score", chaos_report.get("fragility_score")
                    ),
                },
            )
            gate._finalise_result(gate_result_obj)

        if self.config.shadow_mode:
            gate_result_obj.iron_gate = dict(
                gate_result_obj.iron_gate or {},
                state="success",
                should_block_merge=False,
                reasons=(gate_result_obj.iron_gate or {}).get("reasons", [])
                + ["Shadow mode enabled: merge left unblocked while evidence is collected"],
            )
            gate_result_obj.next_actions = list(
                dict.fromkeys(
                    list(gate_result_obj.next_actions)
                    + [
                        "Shadow mode recorded the risk without blocking the merge. Review the evidence ledger before enabling Iron Gate."
                    ]
                )
            )

        gate_path = str(self.data_dir / "premerge_gate_result.json")
        wind_path = str(self.data_dir / "premerge_wind_tunnel.json")
        chaos_path = str(self.data_dir / "premerge_chaos.json")
        report_path = str(self.data_dir / "premerge_report.md")
        report_html_path = str(self.data_dir / "premerge_report.html")
        bundle_path = str(self.data_dir / "premerge_bundle.json")
        override_ledger_path = str(self.data_dir / "override_ledger.jsonl")
        incident_ledger_path = str(self.data_dir / "incident_ledger.jsonl")
        savings_ledger_path = str(self.data_dir / "savings_ledger.jsonl")
        shadow_runs_path = str(self.data_dir / "shadow_runs.jsonl")
        shadow_dashboard_path = str(self.data_dir / "shadow_dashboard.json")
        shadow_dashboard_html_path = str(self.data_dir / "shadow_dashboard.html")
        shadow_feedback_path = str(self.data_dir / "shadow_feedback.jsonl")

        gate_result = gate_result_obj.to_dict()
        if self.config.shadow_mode:
            ShadowRunLedger(path=shadow_runs_path).record(
                gate_result,
                pr_number=pr_number,
                team=self.config.data_owner_team,
                repo=self.config.github_repo,
            )
            dashboard = ShadowDashboard(
                shadow_runs_path=shadow_runs_path,
                feedback_path=shadow_feedback_path,
                override_path=override_ledger_path,
                incident_path=incident_ledger_path,
            ).build()
            gate_result["shadow_summary"] = dashboard
            Path(shadow_dashboard_path).write_text(
                json.dumps(dashboard, indent=2, default=str), encoding="utf-8"
            )
            ShadowDashboard(
                shadow_runs_path=shadow_runs_path,
                feedback_path=shadow_feedback_path,
                override_path=override_ledger_path,
                incident_path=incident_ledger_path,
            ).save_html(shadow_dashboard_html_path)
        Path(gate_path).write_text(json.dumps(gate_result, indent=2, default=str), encoding="utf-8")
        if gate_result_obj.savings_ledger:
            with Path(savings_ledger_path).open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(gate_result_obj.savings_ledger, default=str) + "\n")
        if wind_receipt:
            Path(wind_path).write_text(
                json.dumps(wind_receipt, indent=2, default=str), encoding="utf-8"
            )
        if chaos_report:
            Path(chaos_path).write_text(
                json.dumps(chaos_report, indent=2, default=str), encoding="utf-8"
            )

        evidence_store_path = self.config.evidence_store_path or str(
            self.data_dir / "evidence_history.jsonl"
        )
        evidence_path = str(self.data_dir / "premerge_evidence.json")
        Path(evidence_path).write_text(
            json.dumps(evidence.to_dict(), indent=2, default=str), encoding="utf-8"
        )
        EvidenceStore(evidence_store_path).append(evidence)
        gate_result["evidence_summary"] = evidence.summary()
        gate_result["shadow_mode"] = self.config.shadow_mode
        gate_result["artifact_paths"] = {
            "override_ledger": override_ledger_path,
            "incident_ledger": incident_ledger_path,
            "savings_ledger": savings_ledger_path,
            "shadow_runs": shadow_runs_path,
            "shadow_dashboard": shadow_dashboard_path,
            "shadow_dashboard_html": shadow_dashboard_html_path,
            "shadow_feedback": shadow_feedback_path,
        }

        report = UnifiedOpsReport(
            gate_result=gate_result,
            wind_tunnel_receipt=wind_receipt,
            chaos_report=chaos_report,
        )
        report.save_markdown(report_path)
        report.save_html(report_html_path)
        bundle = PremergeBundle(
            gate_result=gate_result,
            wind_tunnel_receipt=wind_receipt,
            chaos_report=chaos_report,
            unified_report_markdown=Path(report_path).read_text(encoding="utf-8"),
            evidence_summary=evidence.summary(),
            artifact_paths={
                "gate": gate_path,
                "wind_tunnel": wind_path if wind_receipt else "",
                "chaos": chaos_path if chaos_report else "",
                "report": report_path,
                "report_html": report_html_path,
                "evidence": evidence_path,
                "evidence_history": evidence_store_path,
                "bundle": bundle_path,
                "override_ledger": override_ledger_path,
                "incident_ledger": incident_ledger_path,
                "savings_ledger": savings_ledger_path,
                "shadow_runs": shadow_runs_path,
                "shadow_dashboard": shadow_dashboard_path,
                "shadow_dashboard_html": shadow_dashboard_html_path,
                "shadow_feedback": shadow_feedback_path,
            },
        )
        bundle.save(bundle_path)
        return bundle

    def _run_wind_tunnel(
        self,
        drift_report: dict,
        migration_sql: str,
        plan: dict[str, Any],
        readiness: Any,
    ) -> dict[str, Any]:
        dry_run, _ = resolve_live_mode(
            self.config.wind_live_mode,
            getattr(readiness, "dialect", "unknown") if readiness else "unknown",
            bool(getattr(readiness, "clone_supported", False)) if readiness else False,
        )
        config = WindTunnelConfig(
            db_url=self.config.db_url,
            dry_run=dry_run,
            auto_destroy_clone=not self.config.keep_clone,
            data_dir=str(self.data_dir),
            focus_assets=list(plan.get("scope_assets") or []),
            query_files=list(self.config.workload_query_files),
            workload_history_files=list(self.config.workload_history_files),
            dbt_manifest_path=self.config.dbt_manifest_path,
            dbt_catalog_path=self.config.dbt_catalog_path,
            dbt_run_results_path=self.config.dbt_run_results_path,
            dbt_sources_path=self.config.dbt_sources_path,
            openlineage_paths=list(self.config.openlineage_paths),
            airflow_paths=list(self.config.airflow_paths),
            dagster_paths=list(self.config.dagster_paths),
            looker_paths=list(self.config.looker_paths),
            montecarlo_paths=list(self.config.montecarlo_paths),
            post_to_pr=False,
            graph_intelligence_enabled=self.config.graph_intelligence_enabled,
            rgcn_model_path=self.config.rgcn_model_path,
            max_queries=max(
                1,
                min(
                    int(
                        plan.get("wind_tunnel_query_budget") or self.config.wind_tunnel_max_queries
                    ),
                    int(self.config.wind_tunnel_max_queries or 1),
                ),
            ),
        )
        receipt = MigrationWindTunnel(config).run(
            migration_sql=migration_sql,
            drift_report=drift_report,
            graph_json=self.graph_json,
        )
        return receipt.to_dict()

    def _run_chaos(self, plan: dict[str, Any], readiness: Any) -> dict[str, Any]:
        dry_run, _ = resolve_live_mode(
            self.config.chaos_live_mode,
            getattr(readiness, "dialect", "unknown") if readiness else "unknown",
            bool(getattr(readiness, "clone_supported", False)) if readiness else False,
        )
        chaos = ChaosEngine(
            ChaosConfig(
                db_url=self.config.db_url,
                dry_run=dry_run,
                auto_destroy_clone=not self.config.keep_clone,
                data_dir=str(self.data_dir),
                mutation_count=max(
                    1,
                    min(
                        int(plan.get("chaos_mutation_budget") or self.config.chaos_mutation_count),
                        int(self.config.chaos_mutation_count or 1),
                    ),
                ),
                parallel_mutations=False,
                workload_query_files=list(self.config.workload_query_files),
                workload_history_files=list(self.config.workload_history_files),
                focus_assets=list(plan.get("scope_assets") or []),
                dbt_manifest_path=self.config.dbt_manifest_path,
                dbt_run_results_path=self.config.dbt_run_results_path,
                dbt_sources_path=self.config.dbt_sources_path,
                montecarlo_paths=list(self.config.montecarlo_paths),
                stateful_recovery=True,
                graph_intelligence_enabled=self.config.graph_intelligence_enabled,
                rgcn_model_path=self.config.rgcn_model_path,
            )
        )
        report = chaos.run(graph_json=self.graph_json)
        return report.to_dict()
