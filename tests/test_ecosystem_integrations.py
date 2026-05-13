import json
from pathlib import Path


def test_ecosystem_context_loads_dbt_airflow_and_looker(tmp_path):
    from semzero.integrations.ecosystem import EcosystemContext

    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "nodes": {
                    "model.demo.orders": {
                        "resource_type": "model",
                        "config": {"materialized": "table"},
                    },
                }
            }
        )
    )
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps({"nodes": {"model.demo.orders": {"columns": {"id": {}, "status": {}}}}})
    )
    run_results = tmp_path / "run_results.json"
    run_results.write_text(
        json.dumps({"results": [{"unique_id": "model.demo.orders", "status": "success"}]})
    )
    airflow = tmp_path / "airflow.json"
    airflow.write_text(
        json.dumps(
            {
                "dags": [
                    {
                        "dag_id": "daily_orders",
                        "schedule": "0 0 * * *",
                        "tasks": [{"task_id": "build_orders", "outlets": ["orders"]}],
                    }
                ]
            }
        )
    )
    lookml = tmp_path / "orders.view.lkml"
    lookml.write_text(
        "view: orders { sql_table_name: analytics.orders ;; dimension: status { sql: ${TABLE}.status ;; } }"
    )

    ctx = EcosystemContext.load(
        dbt_manifest_path=str(manifest),
        dbt_catalog_path=str(catalog),
        dbt_run_results_path=str(run_results),
        airflow_paths=[str(airflow)],
        looker_paths=[str(tmp_path)],
    )

    payload = ctx.to_dict()
    assert "orders" in payload["focus_assets"]
    assert payload["airflow"]["temporal_paths"] == [] or isinstance(
        payload["airflow"]["temporal_paths"], list
    )
    assert payload["dbt"]["materializations"]["model.demo.orders"] == "table"
    assert payload["looker"]["impacted_assets"]


def test_calibration_store_records_and_summarises(tmp_path):
    from semzero.integrations.calibration import ReliabilityCalibrationStore

    store = ReliabilityCalibrationStore(str(tmp_path / "calibration.jsonl"))
    store.record(
        {
            "evaluated_at": "2026-03-24T00:00:00+00:00",
            "verdict": "BLOCK",
            "reliability_score": 42,
            "oncall_risk": "HIGH",
            "total_blast_radius": 3,
            "assessments": [{"predicted_failure_modes": ["Join failure"]}],
        }
    )
    summary = store.load_summary().to_dict()
    assert summary["total_runs"] == 1
    assert summary["block_rate"] == 1.0
    assert summary["recent_failure_modes"] == ["Join failure"]


def test_wind_tunnel_estimates_compute_cost_risk(db_url, schema_graph):
    from semzero.chaos.wind_tunnel import MigrationWindTunnel, WindTunnelConfig

    tunnel = MigrationWindTunnel(
        WindTunnelConfig(
            db_url=db_url,
            provided_queries=[
                "SELECT u.id, COUNT(*) FROM users u JOIN orders o ON u.id = o.user_id GROUP BY u.id ORDER BY COUNT(*) DESC"
            ],
            explain_plan_enabled=True,
            dry_run=True,
        )
    )
    receipt = tunnel.run(
        migration_sql="ALTER TABLE orders ADD COLUMN flag INTEGER", graph_json=schema_graph
    )
    payload = receipt.to_dict()
    assert payload["compute_cost_risk"] > 0
    assert payload["compute_cost_notes"]


def test_chaos_stateful_recovery_verifier_sets_summary(db_url, schema_graph, tmp_path):
    from semzero.chaos.chaos_engine import ChaosConfig, ChaosEngine, MutationType

    sql_file = tmp_path / "join.sql"
    sql_file.write_text("SELECT COUNT(*) FROM orders o JOIN users u ON o.user_id = u.id")

    engine = ChaosEngine(
        ChaosConfig(
            db_url=db_url,
            workload_query_files=[str(sql_file)],
            workload_max_queries=5,
            workload_per_mutation_limit=5,
            stateful_recovery=True,
            null_flood_pct=0.5,
            auto_destroy_clone=True,
            parallel_mutations=False,
        )
    )
    engine._prepare_workload(schema_graph)
    graph, _ = engine._compute_targets(schema_graph)
    clone = engine._create_env("sqlite_recovery")
    try:
        result = engine._execute_one(
            {
                "type": MutationType.NULL_FLOOD,
                "node_id": "orders.user_id",
                "table": "orders",
                "col_name": "user_id",
                "detail": "orders.user_id null flood",
                "risk_score": 0.9,
                "reason": "unit test",
                "sample_pct": 0.5,
            },
            clone,
            schema_graph,
            graph,
        )
    finally:
        engine._destroy_env(clone)
        if engine._orig_engine is not None:
            engine._orig_engine.dispose()

    assert result.manual_backfill_required is True
    assert result.recovery_notes


def test_wind_tunnel_plan_summary_and_top_queries(db_url, schema_graph):
    from semzero.chaos.wind_tunnel import MigrationWindTunnel, WindTunnelConfig

    tunnel = MigrationWindTunnel(
        WindTunnelConfig(
            db_url=db_url,
            provided_queries=[
                "SELECT u.id, COUNT(*) FROM users u JOIN orders o ON u.id = o.user_id GROUP BY u.id ORDER BY COUNT(*) DESC"
            ],
            explain_plan_enabled=True,
            dry_run=True,
        )
    )
    receipt = tunnel.run(
        migration_sql="ALTER TABLE orders ADD COLUMN flag INTEGER", graph_json=schema_graph
    )
    payload = receipt.to_dict()
    assert payload["plan_risk_summary"]["queries_analysed"] >= 1
    assert payload["top_expensive_queries"]


def test_wind_tunnel_replay_budget_summary_scopes_queries(db_url, schema_graph):
    from semzero.chaos.wind_tunnel import MigrationWindTunnel, WindTunnelConfig

    provided = [
        "SELECT COUNT(*) FROM orders",
        "SELECT COUNT(*) FROM users",
        "SELECT o.user_id, COUNT(*) FROM orders o JOIN users u ON o.user_id = u.id GROUP BY o.user_id",
        "SELECT COUNT(*) FROM payments",
        "SELECT status, COUNT(*) FROM users GROUP BY status",
    ]
    tunnel = MigrationWindTunnel(
        WindTunnelConfig(
            db_url=db_url,
            provided_queries=provided,
            max_queries=2,
            dry_run=True,
            focus_assets=["orders.user_id", "orders"],
        )
    )
    receipt = tunnel.run(
        migration_sql="ALTER TABLE orders ADD COLUMN flag INTEGER", graph_json=schema_graph
    )
    budget = receipt.to_dict()["replay_budget_summary"]

    assert budget["candidate_queries"] >= len(provided)
    assert budget["selected_queries"] == 2
    assert budget["deferred_queries"] >= 1
    assert budget["focus_hit_rate"] >= 0
