from pathlib import Path

from click.testing import CliRunner


def test_finops_change_analyser_detects_transform_cost_drivers(tmp_path):
    from semzero.integrations.finops_gate import FinOpsChangeAnalyser

    model = tmp_path / "models" / "fct_orders.sql"
    model.parent.mkdir(parents=True, exist_ok=True)
    model.write_text(
        """
        {{ config(materialized='table') }}
        select *
        from orders o
        join order_items oi on o.id = oi.order_id
        join products p on oi.product_id = p.id
        join users u on o.user_id = u.id
        order by o.created_at desc
        """
    )

    summary = FinOpsChangeAnalyser([str(tmp_path)]).analyse(["orders.total"])
    payload = summary.to_dict()

    assert payload["projected_weekly_cost_usd"] > 0
    kinds = {item["kind"] for item in payload["drivers"]}
    assert {"SELECT_STAR", "FANOUT_JOIN", "FULL_REFRESH_PATH"} <= kinds


def test_gate_result_surfaces_finops_summary(schema_graph, drift_safe, gate_config, tmp_path):
    from semzero.integrations.change_gate import ChangeGate, GateConfig

    model = tmp_path / "models" / "orders.sql"
    model.parent.mkdir(parents=True, exist_ok=True)
    model.write_text(
        """
        {{ config(materialized='table') }}
        select * from orders o
        join order_items oi on o.id = oi.order_id
        join products p on oi.product_id = p.id
        """
    )
    config = GateConfig(**gate_config.__dict__)
    config.proof_source_paths = [str(tmp_path)]
    result = ChangeGate(schema_graph, config).evaluate(drift_safe)

    assert result.finops_summary["projected_weekly_cost_usd"] > 0
    assert result.recommended_execution["run_finops_review"] is True


def test_wind_tunnel_receipt_includes_finops_summary(db_url, schema_graph):
    from semzero.chaos.wind_tunnel import MigrationWindTunnel, WindTunnelConfig

    receipt = MigrationWindTunnel(
        WindTunnelConfig(
            db_url=db_url,
            dry_run=False,
            auto_destroy_clone=True,
            data_dir="data",
            provided_queries=[
                {
                    "query_id": "Q1",
                    "query_text": "SELECT * FROM orders o JOIN order_items oi ON o.id = oi.order_id JOIN products p ON p.id = oi.product_id",
                    "calls": 48,
                    "source": "history:simulated",
                }
            ],
            max_queries=5,
        )
    ).run(graph_json=schema_graph, migration_sql="select 1")

    payload = receipt.to_dict()
    assert payload["finops_summary"]["projected_weekly_cost_usd"] > 0
    assert payload["finops_summary"]["notes"]


def test_init_ci_scaffolds_drop_in_workflow(tmp_path):
    from semzero.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["init-ci", "--preset", "snowflake", "--output-dir", str(tmp_path)])

    assert result.exit_code == 0
    workflow = tmp_path / ".github" / "workflows" / "semzero_quickstart.yml"
    env_file = tmp_path / ".semzero" / "config.env.example"
    commands = tmp_path / ".semzero" / "quickstart_commands.txt"
    assert workflow.exists()
    assert env_file.exists()
    assert commands.exists()
    assert "SEMZERO_DB_URL=snowflake://" in env_file.read_text(encoding="utf-8")
    assert "semzero premerge" in workflow.read_text(encoding="utf-8")


def test_commands_doc_mentions_init_ci():
    text = Path("docs/COMMANDS.md").read_text(encoding="utf-8")
    assert "semzero init-ci" in text
    assert "drop-in GitHub Action" in text
