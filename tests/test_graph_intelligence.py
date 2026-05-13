from pathlib import Path


def test_graph_intelligence_engine_returns_heuristic_signals(schema_graph):
    from semzero.integrations.graph_intelligence import GraphIntelligenceEngine

    report = GraphIntelligenceEngine(schema_graph, enabled=True).analyse()
    payload = report.to_dict()

    assert payload["provider"] == "heuristic"
    assert payload["top_nodes"]
    assert any(
        item["node_id"].startswith("orders.") or item["node_id"].startswith("order_items.")
        for item in payload["top_nodes"]
    )


def test_change_gate_exposes_graph_intelligence(schema_graph):
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

    result = ChangeGate(schema_graph, GateConfig(db_url="sqlite:///gate.db")).evaluate(drift_report)
    assert result.graph_intelligence["top_nodes"]
    assert result.assessments[0].graph_risk_score >= 0.0
    assert result.recommended_execution["priority_nodes"]


def test_chaos_targets_include_graph_intelligence(schema_graph):
    from semzero.chaos.chaos_engine import ChaosConfig, ChaosEngine

    engine = ChaosEngine(ChaosConfig(db_url="", mutation_count=6, workload_replay=False))
    _, targets = engine._compute_targets(schema_graph)

    assert targets
    assert "graph_intelligence_score" in targets[0]
    assert (
        "graph intelligence" in targets[0]["reason"].lower()
        or targets[0]["graph_intelligence_score"] >= 0.0
    )


def test_unified_ops_report_renders_graph_intelligence(tmp_path):
    from semzero.reporting.live_report import UnifiedOpsReport

    report = UnifiedOpsReport(
        gate_result={
            "verdict": "BLOCK",
            "reliability_score": 38,
            "oncall_risk": "HIGH",
            "total_blast_radius": 3,
            "total_estimated_backfill_cost_usd": 55,
            "assessments": [],
            "graph_intelligence": {
                "provider": "heuristic",
                "status": "heuristic",
                "top_nodes": [
                    {
                        "node_id": "orders.user_id",
                        "score": 0.81,
                        "reasons": ["Identity/join-key style column"],
                    }
                ],
            },
            "recommended_execution": {
                "run_wind_tunnel": True,
                "run_chaos": True,
                "future_workload_required": True,
                "scope_assets": ["orders.user_id"],
            },
        },
        wind_tunnel_receipt={
            "verdict": "BLOCKED",
            "queries_replayed": 2,
            "queries_broken": 1,
            "queries_mismatch": 0,
            "graph_intelligence": {
                "provider": "heuristic",
                "status": "heuristic",
                "top_nodes": [{"node_id": "orders.user_id", "score": 0.81}],
            },
            "top_failure_modes": ["join breakage"],
            "suggested_debug_steps": ["Fix the join key first."],
        },
        chaos_report={
            "summary": {
                "fragility_score": 51,
                "fragility_grade": "D",
                "mutations_applied": 6,
                "mutations_that_broke": 3,
            },
            "graph_intelligence": {
                "provider": "heuristic",
                "status": "heuristic",
                "top_nodes": [{"node_id": "orders.user_id", "score": 0.81}],
            },
        },
    )
    out = tmp_path / "graph_report.md"
    report.save_markdown(str(out))
    text = out.read_text()
    assert "Graph intelligence" in text or "Graph-ranked scope" in text
    assert "orders.user_id" in text
