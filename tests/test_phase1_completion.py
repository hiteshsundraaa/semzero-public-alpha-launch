import textwrap
import tomllib
from pathlib import Path
from types import SimpleNamespace

from click.testing import CliRunner


class _Ctx:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeConn:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.executed = []

    def execute(self, stmt):
        self.executed.append(str(stmt))
        return self

    def fetchall(self):
        return list(self.rows)


class FakeEngine:
    def __init__(
        self,
        rows=None,
        url="databricks://token:abc@workspace?http_path=/sql/1.0/endpoints/demo&catalog=main&schema=analytics",
    ):
        self._conn = FakeConn(rows=rows)
        self.url = SimpleNamespace(render_as_string=lambda hide_password=False: url)
        self.dialect = SimpleNamespace(name="databricks")

    def connect(self):
        return _Ctx(self._conn)

    def begin(self):
        return _Ctx(self._conn)

    def dispose(self):
        return None


def test_release_version_is_single_source_of_truth(tmp_path):
    from semzero.cli import cli
    from semzero.version import __version__, release_info

    pyproject = tomllib.loads(Path("pyproject.toml").read_text())
    assert pyproject["project"]["version"] == __version__ == release_info.version

    runner = CliRunner()
    result = runner.invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output

    meta_out = tmp_path / "release.json"
    result = runner.invoke(cli, ["release-info", "--output", str(meta_out)])
    assert result.exit_code == 0
    assert meta_out.exists()
    assert release_info.version in meta_out.read_text()


def test_live_readiness_detects_databricks_and_recommends_clone_mode():
    from semzero.utils.live_readiness import build_live_readiness_report

    report = build_live_readiness_report(
        "databricks://token:abc@workspace?http_path=/sql/1.0/endpoints/demo&catalog=main&schema=analytics"
    )

    assert report.dialect == "databricks"
    assert report.clone_supported is True
    assert report.zero_copy_clone is True
    assert report.native_query_history is True
    assert report.query_history_source == "system.query.history"
    assert report.recommended_live_mode == "clone"
    assert any("premerge" in cmd for cmd in report.recommended_commands)


def test_query_extractor_reads_databricks_system_history():
    from semzero.chaos.wind_tunnel import QueryExtractor, WindTunnelConfig

    rows = [
        {
            "statement_id": "abc",
            "statement_text": "SELECT user_id, amount FROM analytics.orders",
            "total_duration_ms": 42,
        },
        {
            "statement_id": "skip",
            "statement_text": "DELETE FROM analytics.orders",
            "total_duration_ms": 2,
        },
    ]
    extractor = QueryExtractor(
        engine=FakeEngine(rows=rows),
        dialect="databricks",
        config=WindTunnelConfig(
            db_url="databricks://demo", query_source="databricks", max_queries=10
        ),
    )

    queries = extractor._from_databricks_system_history()
    assert len(queries) == 1
    assert queries[0]["source"] == "databricks.history"
    assert queries[0]["query_id"] == "abc"
    assert "SELECT user_id" in queries[0]["query_text"]


def test_clone_manager_builds_databricks_shallow_clone(monkeypatch):
    import sqlalchemy
    from semzero.chaos.wind_tunnel import CloneManager, WindTunnelConfig

    orig = FakeEngine(rows=[{"tableName": "orders"}, {"tableName": "payments"}])
    created = []

    def fake_create_engine(url, **kwargs):
        created.append((url, kwargs))
        return FakeEngine(url=url)

    monkeypatch.setattr(sqlalchemy, "create_engine", fake_create_engine)

    mgr = CloneManager(
        WindTunnelConfig(
            db_url="databricks://demo",
            databricks_catalog="main",
            databricks_schema="analytics",
            databricks_clone_catalog="semzero_scratch",
        ),
        "databricks",
        "phase1a",
    )
    clone_engine = mgr.create(orig)

    sql = "\n".join(orig._conn.executed)
    assert "CREATE SCHEMA IF NOT EXISTS semzero_scratch.analytics_phase1a" in sql
    assert "SHALLOW CLONE main.analytics.orders" in sql
    assert "SHALLOW CLONE main.analytics.payments" in sql
    assert (
        getattr(clone_engine, "_semzero_clone_map")["main.analytics"]
        == "semzero_scratch.analytics_phase1a"
    )


def test_sql_asset_parser_tracks_dbt_macros_and_jinja_branches(tmp_path):
    from semzero.integrations.ast_proofing import SQLAssetParser

    sql = """
    {% macro normalize_status(col_name) %}
      case when {{ col_name }} in ('active','paused') then {{ col_name }} else 'archived' end
    {% endmacro %}
    {{ config(materialized='incremental') }}
    select {{ normalize_status('status') }} as status_norm
    from {{ ref('orders') }}
    where status in ('active', 'paused')
    {% if is_incremental() %}
      and updated_at >= (select max(updated_at) from {{ this }})
    {% else %}
      and is_test = false
    {% endif %}
    """

    parsed = SQLAssetParser.parse(sql, str(tmp_path / "model.sql"))

    assert {
        "jinja",
        "jinja_branch",
        "dbt_macro_definition",
        "dbt_macro_call",
        "dbt_this",
        "incremental_branch",
    }.issubset(set(parsed.operations))
    assert "macro:normalize_status" in parsed.macro_defs
    assert "macro:normalize_status" in parsed.macro_calls
    assert "this" in parsed.assets
    assert any(hit.startswith("macro:normalize_status<-") for hit in parsed.lineage_pairs)
    assert "status" in parsed.filters
    assert any("is_test" in snippet or "updated_at" in snippet for snippet in parsed.snippets)


def test_ast_change_prover_propagates_macro_blast_radius(schema_graph, tmp_path):
    from semzero.integrations.ast_proofing import ASTChangeProver

    macro_file = tmp_path / "macros" / "normalize_status.sql"
    macro_file.parent.mkdir(parents=True, exist_ok=True)
    macro_file.write_text(
        """
        {% macro normalize_status(col_name) %}
          case when {{ col_name }} in ('active','paused') then {{ col_name }} else 'archived' end
        {% endmacro %}
        """
    )
    model_file = tmp_path / "models" / "fct_orders.sql"
    model_file.parent.mkdir(parents=True, exist_ok=True)
    model_file.write_text(
        """
        select {{ normalize_status('status') }} as normalized_status
        from {{ ref('orders') }}
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

    bundle = ASTChangeProver(schema_graph, [str(tmp_path)], max_files=20, boundary_hops=1).prove(
        drift
    )
    findings = [
        item
        for item in bundle.for_node("orders.status")
        if item.asset_path.endswith("fct_orders.sql")
    ]

    assert findings
    assert any(hit.startswith("macro:normalize_status") for hit in findings[0].downstream_hits)


def test_dbt_yaml_parser_tracks_contracts_and_exposures(tmp_path):
    from semzero.integrations.ast_proofing import DbtYamlParser

    yaml_file = tmp_path / "schema.yml"
    yaml_file.write_text(
        textwrap.dedent("""
        version: 2
        models:
          - name: fct_orders
            description: canonical order facts
            columns:
              - name: status
                tests: [not_null, accepted_values]
              - name: revenue_total
                description: gross revenue
            tags: [finance, sla_bound]
        exposures:
          - name: exec_dashboard
            depends_on:
              - ref('fct_orders')
        """)
    )

    parsed = DbtYamlParser.parse(yaml_file.read_text(), str(yaml_file))
    assert parsed.language == "yaml"
    assert {"dbt_contract", "column_contracts", "tests", "tags", "exposure"}.issubset(
        set(parsed.operations)
    )
    assert "fct_orders" in parsed.tables
    assert "status" in parsed.columns
    assert "fct_orders.status" in parsed.lineage_pairs


def test_ast_change_prover_surfaces_dbt_yaml_contract_hits(schema_graph, tmp_path):
    from semzero.integrations.ast_proofing import ASTChangeProver

    yaml_file = tmp_path / "schema.yml"
    yaml_file.write_text(
        textwrap.dedent("""
        version: 2
        models:
          - name: orders
            columns:
              - name: status
                tests: [not_null, accepted_values]
        """)
    )
    drift = {
        "events": [
            {
                "change_type": "TYPE_CHANGED",
                "node_id": "orders.status",
                "before": {"table": "orders", "name": "status", "dtype": "VARCHAR"},
                "after": {"table": "orders", "name": "status", "dtype": "INTEGER"},
            }
        ]
    }

    bundle = ASTChangeProver(schema_graph, [str(yaml_file)], max_files=10, boundary_hops=1).prove(
        drift
    )
    findings = bundle.for_node("orders.status")
    assert findings
    assert findings[0].language == "yaml"
    assert "dbt contract metadata" in findings[0].expected_failure_mode.lower()
    assert bundle.summary()["indexed_token_count"] > 0


def test_python_asset_parser_tracks_assign_rename_and_query_filters(tmp_path):
    from semzero.integrations.ast_proofing import PythonAssetParser

    py_file = tmp_path / "pipeline.py"
    py_file.write_text(
        textwrap.dedent("""
        orders = warehouse.read_sql("select user_id, amount, status from orders")
        renamed = orders.rename(columns={"user_id": "customer_id"})
        enriched = renamed.assign(gross_amount=renamed["amount"], status_bucket=renamed["status"])
        filtered = enriched.query("status_bucket == 'active' and gross_amount > 0")
        """)
    )

    parsed = PythonAssetParser(str(py_file)).parse(py_file.read_text())
    assert {"rename", "assign", "query"}.issubset(set(parsed.operations))
    assert any(hit.startswith("rename:customer_id<-user_id") for hit in parsed.lineage_pairs)
    assert any(hit.startswith("assign:gross_amount<-amount") for hit in parsed.lineage_pairs)
    assert {"status_bucket", "gross_amount"}.issubset(set(parsed.filters))
