from pathlib import Path
import tomllib


def test_cli_version_matches_pyproject():
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())
    expected = pyproject["project"]["version"]
    cli_text = Path("src/cli.py").read_text()
    assert f'@click.version_option("{expected}", prog_name="semzero")' in cli_text


def test_chaos_engine_exposes_domain_and_key_skew_mutations(tmp_path):
    from semzero.reliability.validation import build_demo_validation_pack
    from semzero.chaos.chaos_engine import ChaosConfig, ChaosEngine, MutationType
    import json

    pack = build_demo_validation_pack(tmp_path / "demo_pack", scale="small", profile="messy")
    schema_graph = json.loads(Path(pack.graph_path).read_text())
    engine = ChaosEngine(ChaosConfig(db_url=pack.db_url, parallel_mutations=False))
    graph, targets = engine._compute_targets(schema_graph)
    users_status = next(t for t in targets if t["node_id"] == "users.status")
    orders_ref = next(t for t in targets if t["node_id"] == "orders.user_ref")

    assert MutationType.DOMAIN_EXPANSION in users_status["mutations"]
    assert MutationType.KEY_SKEW in orders_ref["mutations"]


def test_validation_harness_reports_pregate_gate_stop(tmp_path):
    from semzero.reliability.validation import ValidationConfig, ValidationHarness

    report = ValidationHarness(
        ValidationConfig(
            data_dir=str(tmp_path / "artifacts"),
            demo_pack_dir=str(tmp_path / "demo"),
            demo_scale="small",
            demo_profile="messy",
            run_chaos=True,
            scenarios=["pregate_gate_stop"],
        )
    ).run()

    payload = report.to_dict()
    assert payload["summary"]["scenario_count"] == 1
    scenario = payload["scenarios"][0]
    assert scenario["name"] == "pregate_gate_stop"
    assert scenario["status"] == "PASS"
    assert scenario["aligned"] is True


def test_change_gate_treats_domain_growth_as_regression_when_samples_expand(
    drift_with_remove, schema_graph, gate_config
):
    from semzero.integrations.change_gate import ChangeGate, GateConfig, CompatibilityType

    domain_drift = {
        "events": [
            {
                "change_type": "STATS_DRIFTED",
                "node_id": "users.status",
                "before": {
                    "name": "status",
                    "table": "users",
                    "sample_values": ["active", "paused"],
                    "cardinality": 0.5,
                },
                "after": {
                    "name": "status",
                    "table": "users",
                    "sample_values": ["active", "paused", "archived"],
                    "cardinality": 0.75,
                },
            }
        ]
    }
    gate = ChangeGate(schema_graph, GateConfig(**gate_config.__dict__))
    result = gate.evaluate(domain_drift)
    assert result.assessments
    assert result.assessments[0].compatibility == CompatibilityType.DATA_REGRESSION


def test_change_gate_emits_domain_filter_drift_required_for_hardcoded_filter(
    tmp_path, schema_graph, gate_config
):
    from semzero.integrations.change_gate import ChangeGate, GateConfig

    sql_file = tmp_path / "consumer.sql"
    sql_file.write_text("SELECT status FROM users WHERE status IN ('active','paused')")
    domain_drift = {
        "events": [
            {
                "change_type": "STATS_DRIFTED",
                "node_id": "users.status",
                "before": {
                    "name": "status",
                    "table": "users",
                    "sample_values": ["active", "paused"],
                    "cardinality": 0.5,
                },
                "after": {
                    "name": "status",
                    "table": "users",
                    "sample_values": ["active", "paused", "archived"],
                    "cardinality": 0.75,
                },
            }
        ]
    }
    config = GateConfig(**gate_config.__dict__)
    config.proof_source_paths = [str(sql_file)]
    gate = ChangeGate(schema_graph, config)
    result = gate.evaluate(domain_drift)

    assert result.recommended_execution["domain_filter_drift_required"] is True
    assert any(item.get("filters") for item in result.proof_bundle["findings"])
