import json
from pathlib import Path


def test_change_gate_builds_execution_recommendation(schema_graph):
    from semzero.integrations.change_gate import ChangeGate, GateConfig

    drift_report = {
        "events": [
            {
                "change_type": "STATS_DRIFTED",
                "node_id": "orders.user_id",
                "before": {"null_rate": 0.0, "cardinality": 0.8, "sample_values": [1, 2]},
                "after": {"null_rate": 0.22, "cardinality": 0.2, "sample_values": [None, 2]},
            }
        ]
    }
    gate = ChangeGate(schema_graph, GateConfig(db_url="sqlite:///planner.db"))
    result = gate.evaluate(drift_report)

    assert result.reliability_score < 100
    assert result.recommended_execution["run_wind_tunnel"] is True
    assert result.recommended_execution["run_chaos"] is True
    assert result.oncall_risk in {"MEDIUM", "HIGH"}
    assert result.next_actions


def test_simulation_receipt_debug_summary_is_populated():
    from semzero.chaos.wind_tunnel import (
        QueryResult,
        QueryStatus,
        SemanticRisk,
        SimulationReceipt,
        TunnelVerdict,
    )

    receipt = SimulationReceipt(
        run_id="abc12345",
        clone_name="clone_demo",
        migration_summary="drop legacy column",
        db_dialect="sqlite",
        started_at="2026-03-24T00:00:00+00:00",
        queries_replayed=2,
        queries_passed=0,
        queries_broken=1,
        queries_mismatch=1,
        broken_queries=[
            QueryResult(
                query_id="Q1",
                query_text="SELECT user_id FROM orders",
                query_hash="h1",
                status=QueryStatus.BROKEN,
                clone_error="no such column: user_id",
                affected_cols=["orders.user_id"],
            )
        ],
        mismatch_queries=[
            QueryResult(
                query_id="Q2",
                query_text="SELECT COUNT(*) FROM orders",
                query_hash="h2",
                status=QueryStatus.ROW_MISMATCH,
                original_rows=10,
                clone_rows=7,
                row_delta=-3,
                affected_cols=["orders.user_id"],
            )
        ],
        semantic_risks=[
            SemanticRisk(
                risk_type="NULL_PROPAGATION",
                severity="HIGH",
                column="orders.user_id",
                description="Join keys can drop rows silently.",
                suggestion="Add a null-safe backfill before hardening the constraint.",
            )
        ],
        verdict=TunnelVerdict.BLOCKED,
    )

    payload = receipt.to_dict()
    assert payload["debug_focus_assets"]
    assert payload["top_failure_modes"]
    assert payload["suggested_debug_steps"]


def test_unified_ops_report_includes_debug_checklist(tmp_path):
    from semzero.reporting.live_report import UnifiedOpsReport

    gate = {
        "verdict": "BLOCK",
        "reliability_score": 34.0,
        "oncall_risk": "HIGH",
        "total_blast_radius": 4,
        "total_estimated_backfill_cost_usd": 180.0,
        "assessments": [],
        "next_actions": ["Patch the removed join key before merge."],
        "recommended_execution": {
            "run_wind_tunnel": True,
            "run_chaos": True,
            "scope_assets": ["orders.user_id"],
        },
    }
    wind = {
        "verdict": "BLOCKED",
        "queries_replayed": 8,
        "queries_broken": 2,
        "queries_mismatch": 1,
        "semantic_risks": [],
        "suggested_debug_steps": ["Replay the broken revenue queries on the narrowed scope."],
    }
    chaos = {
        "summary": {
            "fragility_score": 51,
            "fragility_grade": "D",
            "mutations_applied": 6,
            "mutations_that_broke": 3,
        },
        "recommended_hardening": [
            "Add defensive join handling and null checks on nullable foreign-key paths."
        ],
    }

    report = UnifiedOpsReport(gate_result=gate, wind_tunnel_receipt=wind, chaos_report=chaos)
    out = tmp_path / "report.md"
    report.save_markdown(str(out))
    rendered = out.read_text()

    assert "Merge recommendation" in rendered
    assert "Debug checklist" in rendered
    assert "Patch the removed join key before merge." in rendered


def test_chaos_focus_assets_limits_targets(schema_graph):
    from semzero.chaos.chaos_engine import ChaosConfig, ChaosEngine

    engine = ChaosEngine(
        ChaosConfig(
            db_url="", focus_assets=["orders.user_id"], mutation_count=4, workload_replay=False
        )
    )
    _, targets = engine._compute_targets(schema_graph)

    assert targets
    assert all(target["node_id"].startswith("orders.") for target in targets)


def test_premerge_workflow_runs_and_emits_bundle(db_url, schema_graph, drift_with_remove, tmp_path):
    from semzero.reliability.premerge import PremergeWorkflow, PremergeWorkflowConfig

    workflow = PremergeWorkflow(
        schema_graph,
        PremergeWorkflowConfig(
            db_url=db_url,
            data_dir=str(tmp_path),
            run_wind_tunnel=True,
            run_chaos=False,
            proof_paths=[],
        ),
    )
    bundle = workflow.run(drift_with_remove)

    assert bundle.gate_result["verdict"] == "BLOCK"
    assert bundle.wind_tunnel_receipt is not None
    assert "report" in bundle.artifact_paths
    assert Path(bundle.artifact_paths["report"]).exists()
    assert "Debug checklist" in Path(bundle.artifact_paths["report"]).read_text()


def test_unified_ops_report_saves_html(tmp_path):
    from semzero.reporting.live_report import UnifiedOpsReport

    report = UnifiedOpsReport(
        gate_result={
            "verdict": "BLOCK",
            "reliability_score": 44,
            "oncall_risk": "MEDIUM",
            "total_blast_radius": 2,
            "total_estimated_backfill_cost_usd": 20,
            "assessments": [],
            "proof_bundle": {
                "summary": {"finding_count": 1},
                "findings": [
                    {
                        "asset_path": "app/order.ts",
                        "language": "typescript",
                        "node_id": "orders.user_id",
                        "expected_failure_mode": "Application/backend schemas still reference the legacy field.",
                    }
                ],
            },
            "recommended_execution": {
                "run_wind_tunnel": True,
                "run_chaos": False,
                "future_workload_required": True,
                "scope_assets": ["orders.user_id"],
            },
        },
        wind_tunnel_receipt={
            "verdict": "BLOCKED",
            "queries_replayed": 3,
            "queries_broken": 1,
            "queries_mismatch": 1,
            "query_mix_summary": {
                "historical_queries": 1,
                "synthetic_queries": 1,
                "future_queries": 1,
            },
            "prevention_summary": ["Prevented 1 hard query failure before merge."],
            "top_failure_modes": ["Row-count drift detected"],
            "suggested_debug_steps": ["Replay the broken query first."],
        },
        chaos_report={
            "summary": {
                "fragility_score": 61,
                "fragility_grade": "D",
                "mutations_applied": 4,
                "mutations_that_broke": 2,
            },
            "top_oncall_triggers": ["NULL_FLOOD on orders.user_id failed 1/1 checks"],
            "recommended_hardening": ["Add defensive join handling."],
        },
    )
    out = tmp_path / "ops.html"
    report.save_html(str(out))
    html = out.read_text()
    assert "SemZero Ops Report" in html
    assert "Prevented 1 hard query failure before merge." in html


def test_chaos_targeting_adds_semantic_role_pressure(schema_graph):
    from semzero.chaos.chaos_engine import ChaosConfig, ChaosEngine, MutationType

    engine = ChaosEngine(ChaosConfig(db_url="", mutation_count=6, workload_replay=False))
    _, targets = engine._compute_targets(schema_graph)
    revenue_like = next(t for t in targets if t["node_id"] == "order_items.qty")
    assert revenue_like["semantic_roles"]
    assert (
        MutationType.VOLUME_SPIKE in revenue_like["mutations"]
        or MutationType.EMPTY_TABLE in revenue_like["mutations"]
    )


def test_change_gate_iron_gate_emits_status_payloads(schema_graph):
    from semzero.integrations.change_gate import ChangeGate, GateConfig

    drift_report = {
        "events": [
            {
                "change_type": "COLUMN_REMOVED",
                "node_id": "orders.user_id",
                "before": {"dtype": "INTEGER", "nullable": False},
                "after": {},
                "detail": "removed orders.user_id",
            }
        ]
    }
    gate = ChangeGate(schema_graph, GateConfig(db_url="sqlite:///planner.db"))
    result = gate.evaluate(drift_report)

    payloads = result.iron_gate["status_payloads"]
    assert payloads["github"]["state"] == "failure"
    assert payloads["gitlab"]["state"] == "failed"


def test_unified_ops_report_includes_ecosystem_and_recovery_sections(tmp_path):
    from semzero.reporting.live_report import UnifiedOpsReport

    report = UnifiedOpsReport(
        gate_result={
            "verdict": "BLOCK",
            "reliability_score": 44,
            "oncall_risk": "MEDIUM",
            "total_blast_radius": 2,
            "total_estimated_backfill_cost_usd": 20,
            "assessments": [],
            "next_actions": ["Patch the renamed backend field before merge."],
            "recommended_execution": {
                "run_wind_tunnel": True,
                "run_chaos": True,
                "future_workload_required": True,
                "scope_assets": ["orders.user_id"],
            },
            "iron_gate": {
                "state": "failure",
                "should_block_merge": True,
                "reasons": ["blocking compatibility findings"],
                "status_payloads": {
                    "github": {"context": "semzero/iron-gate", "state": "failure"},
                    "gitlab": {"name": "semzero/iron-gate", "state": "failed"},
                },
            },
            "ecosystem_context": {
                "focus_assets": ["orders"],
                "looker": {"impacted_assets": ["orders_dashboard"]},
            },
            "calibration_summary": {
                "total_runs": 4,
                "block_rate": 0.5,
                "average_reliability_score": 71.2,
            },
        },
        wind_tunnel_receipt={
            "verdict": "BLOCKED",
            "queries_replayed": 3,
            "queries_broken": 1,
            "queries_mismatch": 1,
            "query_mix_summary": {
                "historical_queries": 1,
                "synthetic_queries": 1,
                "future_queries": 1,
            },
            "compute_cost_risk": 55,
            "compute_cost_notes": ["Q1: compute-heavy pattern score 22"],
            "top_expensive_queries": [
                {"query_id": "Q1", "score": 22, "reasons": ["2 join(s)", "grouped aggregation"]}
            ],
            "prevention_summary": ["Prevented 1 hard query failure before merge."],
            "top_failure_modes": ["Row-count drift detected"],
            "suggested_debug_steps": ["Replay the broken query first."],
        },
        chaos_report={
            "summary": {
                "fragility_score": 61,
                "fragility_grade": "D",
                "mutations_applied": 4,
                "mutations_that_broke": 2,
                "recovery_summary": {
                    "verified_recoveries": 1,
                    "manual_backfill_required": 1,
                    "recoverability_score": 50.0,
                },
            },
            "top_oncall_triggers": ["NULL_FLOOD on orders.user_id failed 1/1 checks"],
            "recommended_hardening": ["Add defensive join handling."],
            "recovery_summary": {
                "verified_recoveries": 1,
                "manual_backfill_required": 1,
                "recoverability_score": 50.0,
            },
            "recovery_playbook": [
                "Preserve a restore path or clone checkpoint before mutating critical tables in rollout validation."
            ],
        },
    )
    out = tmp_path / "ops.md"
    report.save_markdown(str(out))
    rendered = out.read_text()

    assert "Iron Gate" in rendered
    assert "Ecosystem context" in rendered
    assert "Recovery verification" in rendered
    assert "orders_dashboard" in rendered


def test_change_gate_recommendation_includes_budget_and_roi(schema_graph):
    from semzero.integrations.change_gate import ChangeGate, GateConfig

    drift_report = {
        "events": [
            {
                "change_type": "STATS_DRIFTED",
                "node_id": "events.ts",
                "before": {
                    "null_rate": 0.0,
                    "cardinality": 0.95,
                    "sample_values": ["2026-03-01T00:00:00+00:00"],
                },
                "after": {
                    "null_rate": 0.05,
                    "cardinality": 0.3,
                    "sample_values": ["2026-03-01T00:00:00", None],
                },
                "detail": "incremental boundary shifted",
            }
        ]
    }
    gate = ChangeGate(
        schema_graph,
        GateConfig(
            db_url="sqlite:///planner.db",
            wind_tunnel_max_queries=80,
            chaos_default_mutation_budget=12,
        ),
    )
    result = gate.evaluate(drift_report)
    plan = result.recommended_execution

    assert plan["wind_tunnel_query_budget"] > 0
    assert plan["chaos_mutation_budget"] >= 0
    assert plan["estimated_compute_minutes_saved_per_run"] >= 0
    assert plan["estimated_review_minutes_saved_per_run"] > 0
    assert plan["scope_reduction_pct"] >= 0
