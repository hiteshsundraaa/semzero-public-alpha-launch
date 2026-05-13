from pathlib import Path


def test_compatibility_oracle_flags_varchar_length_narrowing():
    from semzero.integrations.change_gate import CompatibilityOracle, CompatibilityType

    oracle = CompatibilityOracle()
    compat = oracle.classify(
        {
            "change_type": "TYPE_CHANGED",
            "node_id": "users.external_user_id",
            "before": {"dtype": "VARCHAR(255)", "nullable": False},
            "after": {"dtype": "VARCHAR(50)", "nullable": False},
        },
        graph_json={"nodes": [], "edges": []},
        blast_report={"summary": {"total_impacted": 0}},
    )

    assert compat == CompatibilityType.TYPE_NARROWING


def test_compatibility_oracle_flags_timezone_boundary_as_semantic_break():
    from semzero.integrations.change_gate import CompatibilityOracle, CompatibilityType

    oracle = CompatibilityOracle()
    compat = oracle.classify(
        {
            "change_type": "TYPE_CHANGED",
            "node_id": "events.ts",
            "before": {"dtype": "TIMESTAMP_TZ", "nullable": False},
            "after": {"dtype": "TIMESTAMP_NTZ", "nullable": False},
        },
        graph_json={"nodes": [], "edges": []},
        blast_report={"summary": {"total_impacted": 0}},
    )

    assert compat == CompatibilityType.SEMANTIC_BREAKING


def test_validation_harness_demo_pack_runs(tmp_path):
    from semzero.reliability.validation import ValidationConfig, ValidationHarness

    report = ValidationHarness(
        ValidationConfig(
            data_dir=str(tmp_path / "artifacts"),
            demo_pack_dir=str(tmp_path / "demo"),
            demo_scale="small",
            run_chaos=True,
        )
    ).run()

    payload = report.to_dict()
    assert payload["summary"]["scenario_count"] >= 9
    assert payload["summary"]["queries_replayed"] >= 1
    names = {item["name"] for item in payload["scenarios"]}
    assert "silent_truncation" in names
    assert "incremental_ghost" in names
    assert "numeric_precision_narrowing" in names
    assert "distribution_drift" in names


def test_chaos_engine_blank_string_flood_detects_join_fanout(tmp_path):
    from semzero.reliability.validation import build_demo_validation_pack
    from semzero.chaos.chaos_engine import ChaosConfig, ChaosEngine, MutationType
    import json

    pack = build_demo_validation_pack(tmp_path / "demo_pack", scale="small")
    schema_graph = json.loads(Path(pack.graph_path).read_text())
    sql_file = tmp_path / "join_workload.sql"
    sql_file.write_text(
        "SELECT COUNT(*) FROM orders o JOIN users u ON o.user_ref = u.external_user_id"
    )

    engine = ChaosEngine(
        ChaosConfig(
            db_url=pack.db_url,
            workload_query_files=[str(sql_file)],
            workload_max_queries=5,
            workload_per_mutation_limit=5,
            auto_destroy_clone=True,
            parallel_mutations=False,
            null_flood_pct=0.4,
        )
    )
    engine._prepare_workload(schema_graph)
    graph, _ = engine._compute_targets(schema_graph)
    clone = engine._create_env("sqlite_blank_string")
    try:
        result = engine._execute_one(
            {
                "type": MutationType.BLANK_STRING_FLOOD,
                "node_id": "orders.user_ref",
                "table": "orders",
                "col_name": "user_ref",
                "detail": "orders.user_ref blank-string flood",
                "risk_score": 0.95,
                "reason": "unit test",
                "sample_pct": 0.4,
                "scope_assets": ["orders.user_ref", "orders", "users"],
            },
            clone,
            schema_graph,
            graph,
        )
    finally:
        engine._destroy_env(clone)
        if engine._orig_engine is not None:
            engine._orig_engine.dispose()

    assert result.tests_run >= 1
    assert result.tests_failed >= 1 or result.failed_models


def test_compatibility_oracle_flags_numeric_precision_narrowing():
    from semzero.integrations.change_gate import CompatibilityOracle, CompatibilityType

    oracle = CompatibilityOracle()
    compat = oracle.classify(
        {
            "change_type": "TYPE_CHANGED",
            "node_id": "payments.amount",
            "before": {"dtype": "NUMERIC(18,4)", "nullable": False},
            "after": {"dtype": "NUMERIC(10,2)", "nullable": False},
        },
        graph_json={"nodes": [], "edges": []},
        blast_report={"summary": {"total_impacted": 0}},
    )

    assert compat == CompatibilityType.TYPE_NARROWING
