from pathlib import Path


def test_sql_compiler_lineage_resolves_cte_derivations():
    from semzero.integrations.compiler_lineage import SQLCompilerLineage

    sql = """
    with base as (
      select o.user_id, o.amount, o.status
      from analytics.orders o
    ),
    enriched as (
      select user_id, amount * 1.2 as gross_amount, status
      from base
      where status in ('paid', 'settled')
    )
    select user_id, gross_amount as revenue, status
    from enriched
    """

    lineage = SQLCompilerLineage.compile(sql)
    assert lineage.columns["revenue"].provenance in {"exact", "exact+inferred"}
    assert "analytics.orders.amount" in lineage.columns["revenue"].exact_sources
    assert "analytics.orders.status" in lineage.columns["status"].exact_sources
    assert "status" in lineage.filters


def test_sql_asset_parser_emits_exact_lineage_pairs(tmp_path):
    from semzero.integrations.ast_proofing import SQLAssetParser

    sql = """
    with staged as (
      select id, status, amount
      from {{ ref('orders') }}
    )
    select id, amount as revenue_amount, status
    from staged
    where status = 'paid'
    """
    parsed = SQLAssetParser.parse(sql, str(tmp_path / "model.sql"))
    assert "revenue_amount<-orders.amount" in parsed.exact_lineage_pairs
    assert parsed.lineage_provenance.get("revenue_amount") in {"exact", "exact+inferred"}
    assert "status" in parsed.filters


def test_ast_change_prover_prefers_exact_lineage(schema_graph, tmp_path):
    from semzero.integrations.ast_proofing import ASTChangeProver

    model_file = tmp_path / "models" / "fct_orders.sql"
    model_file.parent.mkdir(parents=True, exist_ok=True)
    model_file.write_text("""
    with staged as (
      select id, status, amount
      from orders
    )
    select amount as revenue_amount, status
    from staged
    where status in ('paid', 'settled')
    """)
    drift = {
        "events": [
            {
                "change_type": "TYPE_CHANGED",
                "node_id": "orders.amount",
                "before": {"table": "orders", "name": "amount"},
                "after": {"table": "orders", "name": "amount"},
            }
        ]
    }
    bundle = ASTChangeProver(schema_graph, [str(tmp_path)], max_files=20, boundary_hops=1).prove(
        drift
    )
    findings = bundle.for_node("orders.amount")
    assert findings
    assert findings[0].exact_lineage_hits
    assert findings[0].lineage_provenance.get("revenue_amount") in {"exact", "exact+inferred"}
