import json
from pathlib import Path


def test_change_gate_emits_decision_summary_risk_register_and_blueprints(schema_graph, tmp_path):
    from semzero.integrations.change_gate import ChangeGate, GateConfig

    proof = tmp_path / "proof"
    proof.mkdir()
    (proof / "orders.sql").write_text(
        """
        with latest as (
          select order_id, customer_id, status, event_time,
                 row_number() over (partition by order_id order by event_time desc) as rn
          from orders
          where status in ('paid', 'refunded')
            and event_time >= dateadd(day, -7, current_timestamp)
        )
        select customer_id, status, date_trunc('day', event_time) as bucket_day
        from latest
        where rn = 1
        """.strip(),
        encoding="utf-8",
    )

    drift_report = {
        "events": [
            {
                "change_type": "TYPE_CHANGED",
                "node_id": "orders.event_time",
                "before": {
                    "dtype": "TIMESTAMP_TZ",
                    "nullable": False,
                    "sample_values": ["2026-04-01T00:00:00+00:00"],
                },
                "after": {
                    "dtype": "TIMESTAMP_NTZ",
                    "nullable": False,
                    "sample_values": ["2026-04-01T00:00:00"],
                },
                "detail": "timezone stripped from event time",
            },
            {
                "change_type": "STATS_DRIFTED",
                "node_id": "orders.status",
                "before": {
                    "null_rate": 0.0,
                    "cardinality": 0.3,
                    "sample_values": ["paid", "refunded"],
                },
                "after": {
                    "null_rate": 0.15,
                    "cardinality": 0.72,
                    "sample_values": ["paid", "chargeback", "pending"],
                },
                "detail": "status meaning changed",
            },
        ]
    }
    gate = ChangeGate(
        schema_graph, GateConfig(db_url="sqlite:///decision.db", proof_source_paths=[str(proof)])
    )
    result = gate.evaluate(drift_report)

    assert result.decision_summary["primary_reason"]
    assert result.decision_summary["risk_categories"]
    assert result.risk_register
    assert result.remediation_blueprints
    assert result.savings_ledger["projected_weekly_cost_usd"] >= 0
    assert any(item["categories"] for item in result.risk_register)


def test_premerge_bundle_persists_savings_ledger_and_decision_surface(tmp_path):
    from semzero.reliability.validation import build_demo_validation_pack
    from semzero.reliability.premerge import PremergeWorkflow, PremergeWorkflowConfig

    pack = build_demo_validation_pack(tmp_path / "demo", scale="small", profile="messy")
    graph_json = json.loads(Path(pack.graph_path).read_text(encoding="utf-8"))
    drift_report = json.loads(Path(pack.drift_path).read_text(encoding="utf-8"))
    migration_sql = Path(pack.migration_path).read_text(encoding="utf-8")

    workflow = PremergeWorkflow(
        graph_json,
        PremergeWorkflowConfig(
            db_url=pack.db_url,
            data_dir=str(tmp_path / "artifacts"),
            proof_paths=list(pack.proof_paths),
            workload_query_files=[pack.workload_path],
            run_wind_tunnel=True,
            run_chaos=True,
            wind_live_mode="safe",
            chaos_live_mode="safe",
        ),
    )
    bundle = workflow.run(drift_report=drift_report, migration_sql=migration_sql)
    gate = bundle.gate_result
    assert gate.get("decision_summary", {}).get("primary_reason")
    assert gate.get("risk_register")
    assert gate.get("remediation_blueprints")
    savings_path = bundle.artifact_paths.get("savings_ledger")
    assert savings_path
    assert Path(savings_path).exists()
    lines = [
        line for line in Path(savings_path).read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    assert lines
    payload = json.loads(lines[-1])
    assert payload["projected_weekly_cost_usd"] >= 0


def test_validate_e2e_retains_decision_surface_fields(tmp_path):
    from click.testing import CliRunner
    from semzero.cli import cli

    out = tmp_path / "decision_report.json"
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "validate-e2e",
            "--demo-pack-dir",
            str(tmp_path / "demo_decision"),
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
    assert gate.get("decision_summary", {}).get("risk_categories") is not None
    assert isinstance(gate.get("risk_register", []), list)
    assert isinstance(gate.get("remediation_blueprints", []), list)
    assert gate.get("savings_ledger", {}).get("summary")
