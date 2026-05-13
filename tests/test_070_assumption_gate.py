import json
from pathlib import Path


def test_assumption_gate_extracts_domain_temporal_and_join_assumptions(tmp_path):
    from semzero.integrations.assumption_gate import AssumptionGate

    proof = tmp_path / "proof"
    proof.mkdir()
    (proof / "model.sql").write_text(
        """
        SELECT DATE(ts) AS day_bucket, COUNT(DISTINCT user_ref) AS users
        FROM events
        WHERE status IN ('active','paused')
        GROUP BY DATE(ts)
        """.strip(),
        encoding="utf-8",
    )
    (proof / "pipeline.py").write_text(
        """
        joined = orders.merge(users, left_on='user_ref', right_on='external_user_id', how='left')
        safe = joined.assign(status_safe=joined['status'].fillna('unknown'))
        """.strip(),
        encoding="utf-8",
    )

    graph = {"nodes": []}
    drift = {
        "events": [
            {"node_id": "events.ts", "before": {"name": "ts"}, "after": {"name": "ts"}},
            {"node_id": "users.status", "before": {"name": "status"}, "after": {"name": "status"}},
            {
                "node_id": "orders.user_ref",
                "before": {"name": "user_ref"},
                "after": {"name": "user_ref"},
            },
        ]
    }
    summary = AssumptionGate([str(proof)]).analyse(graph, drift).to_dict()

    assert summary["finding_count"] >= 4
    assert "TEMPORAL_BUCKETING_ASSUMPTION" in summary["assumption_types"]
    assert "DOMAIN_FILTER_ASSUMPTION" in summary["assumption_types"]
    assert "JOIN_CARDINALITY_ASSUMPTION" in summary["assumption_types"]


def test_change_gate_surfaces_assumptions_in_execution_plan(schema_graph, tmp_path):
    from semzero.integrations.change_gate import ChangeGate, GateConfig

    proof = tmp_path / "proof"
    proof.mkdir()
    (proof / "session_rollup.sql").write_text(
        """
        SELECT DATE(ts) AS day_bucket, status, COUNT(DISTINCT user_id)
        FROM events
        WHERE status IN ('active','paused')
        GROUP BY DATE(ts), status
        """.strip(),
        encoding="utf-8",
    )

    drift_report = {
        "events": [
            {
                "change_type": "TYPE_CHANGED",
                "node_id": "events.ts",
                "before": {
                    "dtype": "TIMESTAMP_TZ",
                    "nullable": False,
                    "sample_values": ["2026-03-01T00:00:00+00:00"],
                },
                "after": {
                    "dtype": "TIMESTAMP_NTZ",
                    "nullable": False,
                    "sample_values": ["2026-03-01T00:00:00"],
                },
                "detail": "timezone stripped from event timestamp",
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
        ]
    }
    gate = ChangeGate(
        schema_graph, GateConfig(db_url="sqlite:///planner.db", proof_source_paths=[str(proof)])
    )
    result = gate.evaluate(drift_report)

    assert result.assumption_summary["finding_count"] >= 2
    assert result.recommended_execution["assumption_revalidation_required"] is True
    assert result.recommended_execution["undocumented_assumption_types"]
    assert any(
        "tribal knowledge" in action.lower()
        or "undocumented downstream assumptions" in action.lower()
        for action in result.next_actions
    )
    assert any(assessment.assumption_risks for assessment in result.assessments)


def test_validate_e2e_black_swan_keeps_new_gate_fields(tmp_path):
    from click.testing import CliRunner
    from semzero.cli import cli

    out = tmp_path / "report.json"
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "validate-e2e",
            "--demo-pack-dir",
            str(tmp_path / "demo"),
            "--demo-scale",
            "small",
            "--demo-profile",
            "black_swan",
            "--output",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(out.read_text(encoding="utf-8"))
    bundle = json.loads(Path(payload["bundle_path"]).read_text(encoding="utf-8"))
    gate = bundle.get("gate_result", {})
    assert gate.get("finops_summary")
    assert gate.get("recommended_execution", {}).get("run_wind_tunnel") is True
    assert "assumption_summary" in gate
