import json
from pathlib import Path


def test_semzero_package_imports():
    import semzero
    from semzero.chaos.wind_tunnel import MigrationWindTunnel, WindTunnelConfig
    from semzero.integrations.change_gate import ChangeGate, GateConfig

    assert semzero is not None
    assert MigrationWindTunnel is not None
    assert WindTunnelConfig is not None
    assert ChangeGate is not None
    assert GateConfig is not None


def test_query_extractor_reads_workload_files(tmp_path):
    from semzero.chaos.wind_tunnel import QueryExtractor, WindTunnelConfig

    sql_file = tmp_path / "workload.sql"
    sql_file.write_text(
        """
        -- keep this query
        SELECT id, email FROM users;
        INSERT INTO audit_log VALUES (1);
        SELECT COUNT(*) FROM users WHERE created_at >= '2026-01-01';
        """
    )

    extractor = QueryExtractor(
        engine=None,
        dialect="sqlite",
        config=WindTunnelConfig(db_url="", query_files=[str(sql_file)], max_queries=10),
    )
    queries = extractor.extract(graph_json=None)
    texts = [q["query_text"].lower() for q in queries]

    assert any("select id, email from users" in q for q in texts)
    assert any("count(*)" in q for q in texts)
    assert all("insert into" not in q for q in texts)


def test_query_extractor_reads_history_jsonl(tmp_path):
    from semzero.chaos.wind_tunnel import QueryExtractor, WindTunnelConfig

    history = tmp_path / "query_history.jsonl"
    history.write_text(
        "\n".join(
            [
                json.dumps({"query_id": "H1", "query_text": "SELECT * FROM users", "rows": 2}),
                json.dumps({"query_id": "H2", "statement": "DELETE FROM users"}),
                json.dumps(
                    {"query_id": "H3", "sql": "SELECT COUNT(*) FROM orders", "row_count": 1}
                ),
            ]
        )
    )

    extractor = QueryExtractor(
        engine=None,
        dialect="sqlite",
        config=WindTunnelConfig(db_url="", workload_history_files=[str(history)], max_queries=10),
    )
    queries = extractor.extract(graph_json=None)
    ids = [q["query_id"] for q in queries]
    texts = [q["query_text"].lower() for q in queries]

    assert set(ids) == {"H1", "H3"}
    assert all(text.startswith("select") for text in texts)


def test_query_extractor_reads_dbt_manifest(tmp_path):
    from semzero.chaos.wind_tunnel import QueryExtractor, WindTunnelConfig

    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "nodes": {
                    "model.demo.orders": {
                        "resource_type": "model",
                        "compiled_sql": "select id, status from orders",
                    },
                    "model.demo.bad": {
                        "resource_type": "model",
                        "raw_sql": "{{ config(materialized='table') }} select * from bad_source",
                    },
                }
            }
        )
    )

    extractor = QueryExtractor(
        engine=None,
        dialect="sqlite",
        config=WindTunnelConfig(db_url="", dbt_manifest_path=str(manifest), max_queries=10),
    )
    queries = extractor.extract(graph_json=None)
    ids = [q["query_id"] for q in queries]

    assert "model.demo.orders" in ids
    assert "model.demo.bad" not in ids


def test_change_gate_treats_defaulted_not_null_addition_as_safe():
    from semzero.integrations.change_gate import CompatibilityOracle, CompatibilityType

    oracle = CompatibilityOracle()
    compat = oracle.classify(
        {
            "change_type": "COLUMN_ADDED",
            "node_id": "orders.status",
            "before": {},
            "after": {"nullable": False, "default": "'pending'"},
        },
        graph_json={"nodes": [], "edges": []},
        blast_report={"summary": {"total_impacted": 0}},
    )

    assert compat == CompatibilityType.ADDITIVE_SAFE


def test_change_gate_flags_severe_stats_regression():
    from semzero.integrations.change_gate import CompatibilityOracle, CompatibilityType

    oracle = CompatibilityOracle()
    compat = oracle.classify(
        {
            "change_type": "STATS_DRIFTED",
            "node_id": "orders.status",
            "before": {"null_rate": 0.0, "cardinality": 0.6, "sample_values": ["pending", "done"]},
            "after": {"null_rate": 0.18, "cardinality": 0.05, "sample_values": [None, "unknown"]},
        },
        graph_json={"nodes": [], "edges": []},
        blast_report={"summary": {"total_impacted": 0}},
    )

    assert compat == CompatibilityType.DATA_REGRESSION


def test_chaos_engine_sqlite_workload_replay_detects_breakage(db_url, schema_graph, tmp_path):
    from semzero.chaos.chaos_engine import ChaosConfig, ChaosEngine, MutationType

    sql_file = tmp_path / "chaos_workload.sql"
    sql_file.write_text("SELECT price FROM products;")

    engine = ChaosEngine(
        ChaosConfig(
            db_url=db_url,
            workload_query_files=[str(sql_file)],
            workload_max_queries=10,
            workload_per_mutation_limit=10,
            auto_destroy_clone=True,
            parallel_mutations=False,
        )
    )
    engine._prepare_workload(schema_graph)
    graph, _ = engine._compute_targets(schema_graph)
    clone = engine._create_env("sqlite_unit")
    try:
        result = engine._execute_one(
            {
                "type": MutationType.REMOVE_COLUMN,
                "node_id": "products.price",
                "table": "products",
                "col_name": "price",
                "detail": "products.price",
                "risk_score": 0.9,
                "reason": "unit test",
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
    assert result.tests_failed >= 1
    assert "SELECT price FROM products" in " ".join(result.error_messages) or result.failed_models


def test_query_extractor_adds_asset_metadata(tmp_path):
    from semzero.chaos.wind_tunnel import QueryExtractor, WindTunnelConfig

    sql_file = tmp_path / "scoped.sql"
    sql_file.write_text("SELECT o.id, u.email FROM orders o JOIN users u ON o.user_id = u.id")

    extractor = QueryExtractor(
        engine=None,
        dialect="sqlite",
        config=WindTunnelConfig(db_url="", query_files=[str(sql_file)], max_queries=10),
    )
    queries = extractor.extract(graph_json=None)

    assert queries
    query = queries[0]
    assert "orders" in {item.lower() for item in query.get("tables", [])}
    assert "users" in {item.lower() for item in query.get("tables", [])}
    assert "user_id" in {item.lower() for item in query.get("columns", [])}
    assert query.get("join_count", 0) >= 1


def test_chaos_engine_null_flood_breaks_join_workload(db_url, schema_graph, tmp_path):
    from semzero.chaos.chaos_engine import ChaosConfig, ChaosEngine, MutationType

    sql_file = tmp_path / "join_workload.sql"
    sql_file.write_text(
        "SELECT COUNT(*) AS joined_rows FROM orders o JOIN users u ON o.user_id = u.id"
    )

    engine = ChaosEngine(
        ChaosConfig(
            db_url=db_url,
            workload_query_files=[str(sql_file)],
            workload_max_queries=10,
            workload_per_mutation_limit=10,
            auto_destroy_clone=True,
            parallel_mutations=False,
            null_flood_pct=0.5,
        )
    )
    engine._prepare_workload(schema_graph)
    graph, _ = engine._compute_targets(schema_graph)
    clone = engine._create_env("sqlite_null_flood")
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
                "scope_assets": ["orders.user_id", "orders", "users", "order_items"],
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
    assert result.tests_failed >= 1


def test_chaos_engine_temporal_skew_changes_aggregate_workload(db_url, schema_graph, tmp_path):
    from semzero.chaos.chaos_engine import ChaosConfig, ChaosEngine, MutationType

    sql_file = tmp_path / "temporal_workload.sql"
    sql_file.write_text("SELECT MIN(ts) AS min_ts, MAX(ts) AS max_ts FROM events")

    engine = ChaosEngine(
        ChaosConfig(
            db_url=db_url,
            workload_query_files=[str(sql_file)],
            workload_max_queries=10,
            workload_per_mutation_limit=10,
            auto_destroy_clone=True,
            parallel_mutations=False,
            temporal_skew_pct=0.5,
            temporal_skew_days=30,
        )
    )
    engine._prepare_workload(schema_graph)
    graph, _ = engine._compute_targets(schema_graph)
    clone = engine._create_env("sqlite_temporal_skew")
    try:
        result = engine._execute_one(
            {
                "type": MutationType.TEMPORAL_SKEW,
                "node_id": "events.ts",
                "table": "events",
                "col_name": "ts",
                "detail": "events.ts temporal skew",
                "risk_score": 0.8,
                "reason": "unit test",
                "sample_pct": 0.5,
                "skew_days": 30,
                "scope_assets": ["events.ts", "events"],
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
    assert result.tests_failed >= 1


def test_change_gate_estimates_contract_and_backfill_risk(
    drift_with_remove, schema_graph, gate_config
):
    from semzero.integrations.change_gate import ChangeGate

    gate = ChangeGate(schema_graph, gate_config)
    result = gate.evaluate(drift_with_remove)

    assert result.assessments
    assessment = result.assessments[0]
    assert assessment.estimated_backfill_cost_usd > 0
    assert result.total_estimated_backfill_cost_usd >= assessment.estimated_backfill_cost_usd
    assert assessment.contract_violations


def test_ast_change_prover_detects_removed_column_in_sql(tmp_path, drift_with_remove, schema_graph):
    from semzero.integrations.ast_proofing import ASTChangeProver

    sql_file = tmp_path / "revenue_model.sql"
    sql_file.write_text(
        "SELECT p.price, oi.qty, o.id FROM products p JOIN order_items oi ON oi.product_id = p.id JOIN orders o ON o.id = oi.order_id"
    )

    prover = ASTChangeProver(schema_graph, [str(sql_file)], max_files=10, boundary_hops=1)
    bundle = prover.prove(drift_with_remove)

    findings = bundle.for_node("products.price")
    assert findings
    assert any(
        "compile/runtime failure" in finding.expected_failure_mode.lower() for finding in findings
    )
    assert any("price" in hit for finding in findings for hit in finding.direct_hits)


def test_ast_change_prover_detects_python_merge_usage(tmp_path, schema_graph):
    from semzero.integrations.ast_proofing import ASTChangeProver

    py_file = tmp_path / "orders_pipeline.py"
    py_file.write_text(
        """
import pandas as pd
orders = pd.read_sql('select user_id, total from orders', conn)
users = pd.read_sql('select id, email from users', conn)
report = orders.merge(users, left_on='user_id', right_on='id').groupby('email')['total'].sum()
"""
    )
    drift_report = {
        "events": [
            {
                "change_type": "NULLABLE_CHANGED",
                "node_id": "orders.user_id",
                "before": {"nullable": True, "table": "orders", "name": "user_id"},
                "after": {"nullable": False, "table": "orders", "name": "user_id"},
            }
        ]
    }

    prover = ASTChangeProver(schema_graph, [str(py_file)], max_files=10, boundary_hops=1)
    bundle = prover.prove(drift_report)

    findings = bundle.for_node("orders.user_id")
    assert findings
    top = findings[0]
    assert "join" in top.expected_failure_mode.lower() or "row" in top.expected_failure_mode.lower()
    assert top.confidence > 0.5


def test_change_gate_attaches_ast_proof_and_renders_merge_comment(
    tmp_path, drift_with_remove, schema_graph, gate_config
):
    from semzero.integrations.change_gate import ChangeGate, GateConfig

    sql_file = tmp_path / "consumer.sql"
    sql_file.write_text("SELECT price FROM products")

    config = GateConfig(**gate_config.__dict__)
    config.proof_source_paths = [str(sql_file)]
    gate = ChangeGate(schema_graph, config)
    result = gate.evaluate(drift_with_remove)

    assert result.proof_bundle
    summary = result.proof_bundle["summary"]
    assert summary["finding_count"] >= 1
    assessment = result.assessments[0]
    assert assessment.proof_evidence
    assert any("consumer.sql" in item["asset_path"] for item in assessment.proof_evidence)

    comment = gate._build_pr_comment(result)
    assert "Impacted nodes" in comment
    assert "Expected failure mode" in comment
    assert "Suggested fix" in comment
    assert "AST-first proofing" in comment


def test_merge_comment_renderer_includes_wind_tunnel_and_chaos_sections(
    tmp_path, drift_with_remove, schema_graph, gate_config
):
    from semzero.integrations.change_gate import ChangeGate, GateConfig

    sql_file = tmp_path / "consumer.sql"
    sql_file.write_text("SELECT price FROM products")

    config = GateConfig(**gate_config.__dict__)
    config.proof_source_paths = [str(sql_file)]
    gate = ChangeGate(schema_graph, config)
    result = gate.evaluate(drift_with_remove)
    result.wind_tunnel_receipt = {
        "verdict": "BLOCKED",
        "queries_replayed": 5,
        "queries_broken": 2,
        "queries_mismatch": 1,
        "broken_queries": [{"query_id": "Q1", "query_preview": "SELECT price FROM products"}],
        "semantic_risks": [
            {
                "risk_type": "TYPE_NARROWING",
                "column": "products.price",
                "suggestion": "Use a shadow column first.",
            }
        ],
    }
    result.chaos_report = {
        "summary": {
            "fragility_score": 61,
            "fragility_grade": "D",
            "mutations_that_broke": 3,
            "critical_pipelines": 1,
        },
        "mutation_results": [
            {
                "node_id": "products.price",
                "mutation_type": "REMOVE_COLUMN",
                "tests_failed": 2,
                "tests_run": 2,
            }
        ],
    }

    comment = gate._build_pr_comment(result)
    assert "Wind Tunnel replay" in comment
    assert "Chaos resilience" in comment
    assert "products.price" in comment


def test_ast_change_prover_detects_typescript_schema_reference(schema_graph, tmp_path):
    from semzero.integrations.ast_proofing import ASTChangeProver

    app_file = tmp_path / "orderContract.ts"
    app_file.write_text(
        """
        export interface OrderRecord {
          user_id: string;
          status: string;
        }
        export const orderSchema = z.object({ user_id: z.string(), status: z.enum(['pending','done']) })
        """
    )

    drift = {
        "events": [
            {
                "change_type": "COLUMN_RENAMED",
                "node_id": "orders.user_id",
                "before": {"name": "user_id"},
                "after": {"name": "customer_id"},
            }
        ]
    }
    bundle = ASTChangeProver(schema_graph, [str(app_file)]).prove(drift)

    assert bundle.findings
    finding = bundle.findings[0]
    assert finding.language == "typescript"
    assert "Application/backend schemas" in finding.expected_failure_mode


def test_query_extractor_generates_future_workload(schema_graph, drift_with_remove):
    from semzero.chaos.wind_tunnel import QueryExtractor, WindTunnelConfig

    extractor = QueryExtractor(
        engine=None,
        dialect="sqlite",
        config=WindTunnelConfig(
            db_url="",
            query_source="synthetic",
            synthetic_future_enabled=True,
            synthetic_future_max_queries=8,
            max_queries=20,
        ),
    )
    queries = extractor.extract(schema_graph, drift_report=drift_with_remove)

    future = [q for q in queries if str(q.get("source", "")).startswith("synthetic.future")]
    assert future
    assert any(
        "count(distinct" in q["query_text"].lower() or "orphan_rows" in q["query_text"].lower()
        for q in future
    )


def test_ast_change_prover_detects_dbt_incremental_filter_and_lineage(schema_graph, tmp_path):
    from semzero.integrations.ast_proofing import ASTChangeProver

    model = tmp_path / "fct_orders.sql"
    model.write_text(
        """
        {{ config(materialized='incremental', unique_key='order_id') }}
        with src as (
          select o.user_ref, o.status, o.updated_at
          from {{ ref('orders') }} o
          where o.status in ('active','paused')
          {% if is_incremental() %}
            and o.updated_at >= (select max(updated_at) from {{ this }})
          {% endif %}
        )
        select user_ref as customer_key, status from src
        """
    )
    drift = {
        "events": [
            {
                "change_type": "DOMAIN_EXPANSION",
                "node_id": "orders.status",
                "before": {
                    "table": "orders",
                    "name": "status",
                    "domain_values": ["active", "paused"],
                },
                "after": {
                    "table": "orders",
                    "name": "status",
                    "domain_values": ["active", "paused", "archived"],
                },
            }
        ]
    }
    bundle = ASTChangeProver(schema_graph, [str(model)], max_files=10, boundary_hops=1).prove(drift)

    findings = bundle.for_node("orders.status")
    assert findings
    top = findings[0]
    assert "incremental" in top.operations
    assert top.filters
    assert top.lineage_hits
    assert (
        "legacy domain" in top.expected_failure_mode.lower()
        or "silently dropped" in top.expected_failure_mode.lower()
    )


def test_sql_asset_parser_handles_snowflake_and_databricks_patterns(tmp_path):
    from semzero.integrations.ast_proofing import SQLAssetParser

    sql = """
    with exploded as (
      select src:user.id::string as user_id,
             status,
             amount,
             row_number() over (partition by src:user.id::string order by ts desc) as rn
      from raw.orders_stream,
           lateral flatten(input => raw.orders_stream.items)
      qualify rn = 1
    )
    merge into analytics.orders t
    using exploded s
      on t.user_id = s.user_id
    when matched then update set amount = s.amount
    when not matched then insert (user_id, amount, status) values (s.user_id, s.amount, s.status)
    """
    parsed = SQLAssetParser.parse(sql, str(tmp_path / "snowflake_databricks.sql"))

    assert "flatten" in parsed.operations
    assert "merge_into" in parsed.operations
    assert "qualify" in parsed.operations
    assert "orders" in parsed.tables
    assert "user_id" in parsed.columns
    assert any(
        hit.startswith("group:")
        or hit.startswith("window:")
        or "analytics.orders.user_id" in hit
        or "orders.user_id" in hit
        for hit in parsed.lineage_pairs
    )


def test_merge_comment_and_live_report_surface_ast_lineage_details(
    tmp_path, drift_with_remove, schema_graph, gate_config
):
    from semzero.integrations.change_gate import ChangeGate, GateConfig
    from semzero.reporting.live_report import UnifiedOpsReport

    sql_file = tmp_path / "consumer.sql"
    sql_file.write_text("SELECT price AS gross_price FROM products WHERE price > 10")

    config = GateConfig(**gate_config.__dict__)
    config.proof_source_paths = [str(sql_file)]
    gate = ChangeGate(schema_graph, config)
    result = gate.evaluate(drift_with_remove)

    finding = result.proof_bundle["findings"][0]
    assert finding["lineage_hits"] or finding["direct_hits"]
    report_html = UnifiedOpsReport(
        result.to_dict(), result.wind_tunnel_receipt or {}, result.chaos_report or {}
    ).render_html()
    assert "AST / source references" in report_html or "AST mapping" in report_html
