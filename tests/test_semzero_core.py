"""
test_semzero_core.py — Production test suite for SemZero's three core features.

  - Wind Tunnel (MigrationWindTunnel)
  - Change Gate (ChangeGate + CompatibilityOracle)
  - Chaos Engine (ChaosEngine)

All tests run on SQLite. Zero external services required.
Run: pytest tests/test_semzero_core.py -v
"""

import json
import re
import shutil
import sqlite3
import tempfile
from pathlib import Path

import pytest


# ══════════════════════════════════════════════════════════════════════════════
# WIND TUNNEL
# ══════════════════════════════════════════════════════════════════════════════


class TestWindTunnelDialectDetection:
    def test_sqlite(self, db_url):
        from semzero.chaos.wind_tunnel import MigrationWindTunnel, WindTunnelConfig

        t = MigrationWindTunnel(WindTunnelConfig(db_url=db_url))
        assert t._dialect == "sqlite"

    def test_postgres(self):
        from semzero.chaos.wind_tunnel import MigrationWindTunnel, WindTunnelConfig

        t = MigrationWindTunnel(WindTunnelConfig(db_url="postgresql://u:p@host/db"))
        assert t._dialect == "postgresql"

    def test_snowflake_by_url(self):
        from semzero.chaos.wind_tunnel import MigrationWindTunnel, WindTunnelConfig

        t = MigrationWindTunnel(WindTunnelConfig(db_url="snowflake://u:p@acct/db/PUBLIC"))
        assert t._dialect == "snowflake"

    def test_snowflake_by_account(self):
        from semzero.chaos.wind_tunnel import MigrationWindTunnel, WindTunnelConfig

        t = MigrationWindTunnel(WindTunnelConfig(snowflake_account="myacct"))
        assert t._dialect == "snowflake"


class TestCloneManager:
    def test_sqlite_clone_is_independent(self, db_url, db_path):
        from sqlalchemy import create_engine, text
        from semzero.chaos.wind_tunnel import CloneManager, WindTunnelConfig

        config = WindTunnelConfig(db_url=db_url)
        orig = create_engine(db_url, connect_args={"check_same_thread": False})
        mgr = CloneManager(config, "sqlite", "clonetest")
        clone = mgr.create(orig)
        try:
            assert clone is not None
            # Clone has same data
            with clone.connect() as c:
                count = c.execute(text("SELECT COUNT(*) FROM users")).scalar()
            assert count == 2

            # Mutation on clone does NOT touch original
            with clone.begin() as c:
                c.execute(text("DELETE FROM events"))
            with orig.connect() as c:
                orig_count = c.execute(text("SELECT COUNT(*) FROM events")).scalar()
            assert orig_count == 3
        finally:
            mgr.destroy(clone)
            orig.dispose()

    def test_sqlite_clone_file_cleaned_up(self, db_url):
        from sqlalchemy import create_engine
        from semzero.chaos.wind_tunnel import CloneManager, WindTunnelConfig

        config = WindTunnelConfig(db_url=db_url)
        orig = create_engine(db_url, connect_args={"check_same_thread": False})
        mgr = CloneManager(config, "sqlite", "cleanup")
        clone = mgr.create(orig)
        path = mgr._clone_path
        mgr.destroy(clone)
        orig.dispose()
        assert not Path(path).exists()

    def test_unsupported_dialect_raises(self, db_url):
        from sqlalchemy import create_engine
        from semzero.chaos.wind_tunnel import CloneManager, WindTunnelConfig

        config = WindTunnelConfig(db_url="mysql://u:p@host/db")
        orig = create_engine(db_url, connect_args={"check_same_thread": False})
        mgr = CloneManager(config, "mysql", "x")
        with pytest.raises(RuntimeError, match="not supported"):
            mgr.create(orig)
        orig.dispose()

    def test_dry_run_returns_original(self, db_url):
        from sqlalchemy import create_engine
        from semzero.chaos.wind_tunnel import CloneManager, WindTunnelConfig

        config = WindTunnelConfig(db_url=db_url, dry_run=True)
        orig = create_engine(db_url, connect_args={"check_same_thread": False})
        mgr = CloneManager(config, "sqlite", "dry")
        result = mgr.create(orig)
        assert result is orig  # same engine returned in dry_run
        orig.dispose()


class TestMigrationApplicator:
    def test_applies_sql_successfully(self, db_url):
        from semzero.chaos.wind_tunnel import MigrationApplicator, WindTunnelConfig, CloneManager
        from sqlalchemy import create_engine, inspect

        config = WindTunnelConfig(db_url=db_url)
        orig = create_engine(db_url, connect_args={"check_same_thread": False})
        mgr = CloneManager(config, "sqlite", "applytest")
        clone = mgr.create(orig)
        try:
            app = MigrationApplicator()
            err = app.apply_sql(clone, "ALTER TABLE products ADD COLUMN notes TEXT;")
            assert err is None
            cols = [c["name"] for c in inspect(clone).get_columns("products")]
            assert "notes" in cols
        finally:
            mgr.destroy(clone)
            orig.dispose()

    def test_bad_sql_returns_error_string(self, db_url):
        from semzero.chaos.wind_tunnel import MigrationApplicator, WindTunnelConfig, CloneManager
        from sqlalchemy import create_engine

        config = WindTunnelConfig(db_url=db_url)
        orig = create_engine(db_url, connect_args={"check_same_thread": False})
        mgr = CloneManager(config, "sqlite", "badsql")
        clone = mgr.create(orig)
        try:
            app = MigrationApplicator()
            err = app.apply_sql(clone, "THIS IS NOT SQL;")
            assert err is not None
            assert isinstance(err, str)
        finally:
            mgr.destroy(clone)
            orig.dispose()

    def test_empty_sql_returns_error(self, db_url):
        from semzero.chaos.wind_tunnel import MigrationApplicator
        from sqlalchemy import create_engine

        app = MigrationApplicator()
        eng = create_engine(db_url, connect_args={"check_same_thread": False})
        err = app.apply_sql(eng, "   ;   ;  ")
        assert err is not None
        eng.dispose()


class TestQueryExtractor:
    def test_synthetic_queries_all_select(self, db_url, schema_graph):
        from sqlalchemy import create_engine
        from semzero.chaos.wind_tunnel import QueryExtractor, WindTunnelConfig

        config = WindTunnelConfig(db_url=db_url, query_source="synthetic")
        engine = create_engine(db_url, connect_args={"check_same_thread": False})
        extractor = QueryExtractor(engine, "sqlite", config)
        queries = extractor.extract(schema_graph)
        engine.dispose()

        assert len(queries) > 0
        for q in queries:
            assert re.match(r"^\s*SELECT", q["query_text"], re.IGNORECASE), (
                f"Non-SELECT query leaked: {q['query_text'][:60]}"
            )

    def test_provided_queries_used(self, db_url):
        from sqlalchemy import create_engine
        from semzero.chaos.wind_tunnel import QueryExtractor, WindTunnelConfig

        provided = ["SELECT * FROM users", "SELECT COUNT(*) FROM orders"]
        config = WindTunnelConfig(db_url=db_url, provided_queries=provided)
        engine = create_engine(db_url, connect_args={"check_same_thread": False})
        extractor = QueryExtractor(engine, "sqlite", config)
        queries = extractor.extract(None)
        engine.dispose()

        texts = [q["query_text"] for q in queries]
        for p in provided:
            assert p in texts

    def test_dml_filtered_out(self, db_url):
        from sqlalchemy import create_engine
        from semzero.chaos.wind_tunnel import QueryExtractor, WindTunnelConfig

        config = WindTunnelConfig(
            db_url=db_url,
            provided_queries=[
                "SELECT * FROM users",
                "INSERT INTO users VALUES (99,'x','X','active',NULL)",
                "DELETE FROM orders",
                "UPDATE users SET name='Z' WHERE id=1",
                "SELECT COUNT(*) FROM products",
            ],
        )
        engine = create_engine(db_url, connect_args={"check_same_thread": False})
        extractor = QueryExtractor(engine, "sqlite", config)
        queries = extractor.extract(None)
        engine.dispose()
        assert len(queries) == 2  # only the SELECTs survive

    def test_deduplication(self, db_url):
        from sqlalchemy import create_engine
        from semzero.chaos.wind_tunnel import QueryExtractor, WindTunnelConfig

        dup = "SELECT * FROM users"
        config = WindTunnelConfig(
            db_url=db_url,
            provided_queries=[dup, dup, dup, "SELECT COUNT(*) FROM orders"],
        )
        engine = create_engine(db_url, connect_args={"check_same_thread": False})
        extractor = QueryExtractor(engine, "sqlite", config)
        queries = extractor.extract(None)
        engine.dispose()
        assert len(queries) == 2

    def test_max_queries_respected(self, db_url, schema_graph):
        from sqlalchemy import create_engine
        from semzero.chaos.wind_tunnel import QueryExtractor, WindTunnelConfig

        config = WindTunnelConfig(db_url=db_url, max_queries=3)
        engine = create_engine(db_url, connect_args={"check_same_thread": False})
        extractor = QueryExtractor(engine, "sqlite", config)
        queries = extractor.extract(schema_graph)
        engine.dispose()
        assert len(queries) <= 3


class TestQueryReplayer:
    def test_identical_result_safe_migration(self, db_url):
        from sqlalchemy import create_engine
        from semzero.chaos.wind_tunnel import QueryReplayer, WindTunnelConfig, QueryStatus

        config = WindTunnelConfig(db_url=db_url)
        engine = create_engine(db_url, connect_args={"check_same_thread": False})
        replayer = QueryReplayer(config)
        results = replayer.replay(
            [
                {"query_id": "Q1", "query_text": "SELECT * FROM users", "rows": 2},
                {"query_id": "Q2", "query_text": "SELECT COUNT(*) FROM orders", "rows": 1},
            ],
            engine,
            engine,
        )
        engine.dispose()
        assert all(r.status == QueryStatus.PASSED for r in results)

    def test_broken_query_detected(self, db_path):
        """Query on a column that was dropped should produce BROKEN status."""
        import shutil, tempfile
        from sqlalchemy import create_engine
        from semzero.chaos.wind_tunnel import QueryReplayer, WindTunnelConfig, QueryStatus

        tmp = tempfile.mkdtemp()
        clone_p = Path(tmp) / "clone.db"
        shutil.copy2(db_path, clone_p)

        # Drop 'price' column from products in clone via table recreation
        c = sqlite3.connect(str(clone_p))
        c.executescript("""
            CREATE TABLE products_new (
                id       INTEGER PRIMARY KEY, name TEXT NOT NULL,
                sku TEXT UNIQUE, stock INTEGER DEFAULT 0, category TEXT
            );
            INSERT INTO products_new SELECT id, name, sku, stock, category FROM products;
            DROP TABLE products;
            ALTER TABLE products_new RENAME TO products;
        """)
        c.commit()
        c.close()

        orig_eng = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
        clone_eng = create_engine(f"sqlite:///{clone_p}", connect_args={"check_same_thread": False})
        config = WindTunnelConfig(db_url=f"sqlite:///{db_path}")
        replayer = QueryReplayer(config)
        results = replayer.replay(
            [{"query_id": "Q1", "query_text": "SELECT id, name, price FROM products", "rows": 0}],
            orig_eng,
            clone_eng,
        )
        orig_eng.dispose()
        clone_eng.dispose()
        shutil.rmtree(tmp, ignore_errors=True)

        assert results[0].status == QueryStatus.BROKEN

    def test_limit_injected(self):
        from semzero.chaos.wind_tunnel import QueryReplayer

        r = QueryReplayer.__new__(QueryReplayer)
        # When no LIMIT present: adds one
        result_no_limit = QueryReplayer._inject_limit("SELECT * FROM t", 500)
        assert "LIMIT 500" in result_no_limit
        # When LIMIT already present: doesn't add a second one
        result_with_limit = QueryReplayer._inject_limit("SELECT * FROM t LIMIT 100", 500)
        assert result_with_limit.upper().count("LIMIT") == 1


class TestSemanticAnalyser:
    def test_not_null_no_default(self):
        from semzero.chaos.wind_tunnel import SemanticAnalyser

        risks = SemanticAnalyser().analyse(
            "ALTER TABLE orders ADD COLUMN priority INTEGER NOT NULL;"
        )
        assert any(r.risk_type == "NOT_NULL_TRAP" for r in risks)

    def test_set_not_null(self):
        from semzero.chaos.wind_tunnel import SemanticAnalyser

        risks = SemanticAnalyser().analyse("ALTER TABLE orders ALTER COLUMN total SET NOT NULL;")
        assert any(r.risk_type == "NOT_NULL_TRAP" for r in risks)

    def test_type_narrowing_to_int(self):
        from semzero.chaos.wind_tunnel import SemanticAnalyser

        risks = SemanticAnalyser().analyse("ALTER TABLE orders ALTER COLUMN total TYPE INTEGER;")
        assert any(r.risk_type == "TYPE_NARROWING" for r in risks)

    def test_fk_drop_detected(self, schema_graph):
        from semzero.chaos.wind_tunnel import SemanticAnalyser

        risks = SemanticAnalyser().analyse(
            "ALTER TABLE products DROP COLUMN id;",
            graph_json=schema_graph,
        )
        assert any(r.risk_type == "FK_DROP" for r in risks)

    def test_safe_additive_no_risks(self):
        from semzero.chaos.wind_tunnel import SemanticAnalyser

        risks = SemanticAnalyser().analyse("ALTER TABLE products ADD COLUMN description TEXT;")
        assert len(risks) == 0

    def test_truncate_flagged(self):
        from semzero.chaos.wind_tunnel import SemanticAnalyser

        risks = SemanticAnalyser().analyse("TRUNCATE TABLE events;")
        assert any(r.risk_type == "DATA_LOSS" for r in risks)

    def test_severity_is_valid(self):
        from semzero.chaos.wind_tunnel import SemanticAnalyser

        valid = {"CRITICAL", "HIGH", "MEDIUM", "LOW"}
        risks = SemanticAnalyser().analyse("ALTER TABLE orders ADD COLUMN flag INTEGER NOT NULL;")
        for r in risks:
            assert r.severity in valid


class TestSimulationReceipt:
    def _make_receipt(self):
        from semzero.chaos.wind_tunnel import SimulationReceipt, TunnelVerdict

        r = SimulationReceipt(
            run_id="abc12345",
            clone_name="TEST_CLONE",
            migration_summary="DROP COLUMN price",
            db_dialect="sqlite",
            started_at="2024-01-01T00:00:00Z",
        )
        r.queries_replayed = 100
        r.queries_passed = 96
        r.queries_broken = 4
        r.compute_confidence()
        return r

    def test_verdict_safe_with_patches(self):
        from semzero.chaos.wind_tunnel import TunnelVerdict

        r = self._make_receipt()
        assert r.verdict == TunnelVerdict.SAFE_WITH_PATCHES
        assert r.confidence_score == 96.0

    def test_verdict_safe_100pct(self):
        from semzero.chaos.wind_tunnel import SimulationReceipt, TunnelVerdict

        r = SimulationReceipt("x", "c", "", "sqlite", "now")
        r.queries_replayed = 5
        r.queries_passed = 5
        r.compute_confidence()
        assert r.verdict == TunnelVerdict.SAFE
        assert r.confidence_score == 100.0

    def test_verdict_blocked(self):
        from semzero.chaos.wind_tunnel import SimulationReceipt, TunnelVerdict

        r = SimulationReceipt("x", "c", "", "sqlite", "now")
        r.queries_replayed = 10
        r.queries_passed = 3
        r.compute_confidence()
        assert r.verdict == TunnelVerdict.BLOCKED

    def test_verdict_no_queries(self):
        from semzero.chaos.wind_tunnel import SimulationReceipt, TunnelVerdict

        r = SimulationReceipt("x", "c", "", "sqlite", "now")
        r.queries_replayed = 0
        r.compute_confidence()
        assert r.verdict == TunnelVerdict.NO_QUERIES

    def test_pr_comment_contains_verdict(self):
        r = self._make_receipt()
        comment = r.to_pr_comment()
        assert "Wind Tunnel" in comment
        assert "Safe with patches" in comment or "SAFE_WITH_PATCHES" in comment

    def test_to_dict_structure(self):
        r = self._make_receipt()
        d = r.to_dict()
        for key in (
            "verdict",
            "confidence_score",
            "queries_replayed",
            "queries_passed",
            "queries_broken",
            "run_id",
        ):
            assert key in d

    def test_save_and_reload(self, tmp_path):
        r = self._make_receipt()
        path = tmp_path / "receipt.json"
        r.save(str(path))
        data = json.loads(path.read_text())
        assert data["verdict"] in ("SAFE", "SAFE_WITH_PATCHES", "BLOCKED", "NO_QUERIES", "ERROR")


class TestWindTunnelFullRun:
    def test_safe_migration_end_to_end(self, db_url, schema_graph):
        from semzero.chaos.wind_tunnel import MigrationWindTunnel, WindTunnelConfig, TunnelVerdict

        config = WindTunnelConfig(
            db_url=db_url,
            max_queries=5,
            run_semantic_analysis=True,
            provided_queries=["SELECT * FROM users", "SELECT COUNT(*) FROM orders"],
        )
        tunnel = MigrationWindTunnel(config)
        receipt = tunnel.run(
            migration_sql="ALTER TABLE products ADD COLUMN description TEXT;",
            graph_json=schema_graph,
        )
        assert receipt.clone_created
        assert receipt.migration_applied
        assert receipt.verdict == TunnelVerdict.SAFE
        assert receipt.queries_broken == 0

    def test_receipt_pr_comment_not_empty(self, db_url):
        from semzero.chaos.wind_tunnel import MigrationWindTunnel, WindTunnelConfig

        config = WindTunnelConfig(
            db_url=db_url,
            provided_queries=["SELECT * FROM users", "SELECT COUNT(*) FROM products"],
        )
        tunnel = MigrationWindTunnel(config)
        receipt = tunnel.run(
            migration_sql="ALTER TABLE products ADD COLUMN notes TEXT;",
        )
        comment = receipt.to_pr_comment()
        assert len(comment) > 200
        assert "Wind Tunnel" in comment

    def test_semantic_risks_included_in_comment(self, db_url):
        from semzero.chaos.wind_tunnel import MigrationWindTunnel, WindTunnelConfig

        config = WindTunnelConfig(
            db_url=db_url,
            provided_queries=["SELECT * FROM orders"],
            run_semantic_analysis=True,
        )
        tunnel = MigrationWindTunnel(config)
        receipt = tunnel.run(
            migration_sql="ALTER TABLE orders ADD COLUMN flag INTEGER NOT NULL;",
        )
        assert len(receipt.semantic_risks) > 0
        comment = receipt.to_pr_comment()
        assert "Semantic Risk" in comment or "NOT_NULL" in comment

    def test_drift_report_input(self, db_url, schema_graph, drift_safe):
        from semzero.chaos.wind_tunnel import MigrationWindTunnel, WindTunnelConfig

        config = WindTunnelConfig(db_url=db_url, provided_queries=["SELECT 1"])
        tunnel = MigrationWindTunnel(config)
        receipt = tunnel.run(drift_report=drift_safe, graph_json=schema_graph)
        assert receipt.run_id is not None
        assert receipt.verdict is not None

    def test_save_creates_file(self, db_url, tmp_path):
        from semzero.chaos.wind_tunnel import MigrationWindTunnel, WindTunnelConfig

        out = tmp_path / "receipt.json"
        config = WindTunnelConfig(
            db_url=db_url,
            data_dir=str(tmp_path),
            provided_queries=["SELECT 1"],
        )
        tunnel = MigrationWindTunnel(config)
        receipt = tunnel.run(migration_sql="SELECT 1")
        assert (tmp_path / "simulation_receipt.json").exists()


# ══════════════════════════════════════════════════════════════════════════════
# CHANGE GATE
# ══════════════════════════════════════════════════════════════════════════════


class TestCompatibilityOracle:
    @pytest.fixture(autouse=True)
    def oracle(self):
        from semzero.integrations.change_gate import CompatibilityOracle

        self.o = CompatibilityOracle()

    def _c(self, ct, before=None, after=None, blast=None):
        event = {
            "change_type": ct,
            "node_id": "t.col",
            "before": before or {},
            "after": after or {},
        }
        return self.o.classify(event, {}, blast)

    def test_column_removed_destructive(self):
        from semzero.integrations.change_gate import CompatibilityType

        assert self._c("COLUMN_REMOVED") == CompatibilityType.DESTRUCTIVE_DELETE

    def test_table_removed_destructive(self):
        from semzero.integrations.change_gate import CompatibilityType

        assert self._c("TABLE_REMOVED") == CompatibilityType.DESTRUCTIVE_DELETE

    def test_nullable_additive_safe(self):
        from semzero.integrations.change_gate import CompatibilityType

        assert self._c("COLUMN_ADDED", after={"nullable": True}) == CompatibilityType.ADDITIVE_SAFE

    def test_not_null_additive_breaking(self):
        from semzero.integrations.change_gate import CompatibilityType

        assert (
            self._c("COLUMN_ADDED", after={"nullable": False})
            == CompatibilityType.ADDITIVE_BREAKING
        )

    def test_type_widening_int_bigint(self):
        from semzero.integrations.change_gate import CompatibilityType

        assert (
            self._c("TYPE_CHANGED", before={"dtype": "INTEGER"}, after={"dtype": "BIGINT"})
            == CompatibilityType.TYPE_WIDENING
        )

    def test_type_narrowing_bigint_int(self):
        from semzero.integrations.change_gate import CompatibilityType

        assert (
            self._c("TYPE_CHANGED", before={"dtype": "BIGINT"}, after={"dtype": "INTEGER"})
            == CompatibilityType.TYPE_NARROWING
        )

    def test_varchar_to_text_widening(self):
        from semzero.integrations.change_gate import CompatibilityType

        assert (
            self._c("TYPE_CHANGED", before={"dtype": "VARCHAR"}, after={"dtype": "TEXT"})
            == CompatibilityType.TYPE_WIDENING
        )

    def test_nullable_hardening(self):
        from semzero.integrations.change_gate import CompatibilityType

        assert (
            self._c("NULLABLE_CHANGED", before={"nullable": True}, after={"nullable": False})
            == CompatibilityType.NULLABLE_HARDENING
        )

    def test_nullable_loosening_safe(self):
        from semzero.integrations.change_gate import CompatibilityType

        assert (
            self._c("NULLABLE_CHANGED", before={"nullable": False}, after={"nullable": True})
            == CompatibilityType.ADDITIVE_SAFE
        )

    def test_rename_high_confidence_small_blast(self):
        from semzero.integrations.change_gate import CompatibilityType

        assert (
            self._c("COLUMN_RENAMED", blast={"summary": {"total_impacted": 2}})
            == CompatibilityType.RENAME_HIGH_CONFIDENCE
        )

    def test_rename_low_confidence_large_blast(self):
        from semzero.integrations.change_gate import CompatibilityType

        assert (
            self._c("COLUMN_RENAMED", blast={"summary": {"total_impacted": 20}})
            == CompatibilityType.RENAME_LOW_CONFIDENCE
        )

    def test_semantic_break_cardinality_collapse(self):
        from semzero.integrations.change_gate import CompatibilityType

        assert (
            self._c(
                "TYPE_CHANGED",
                before={"dtype": "VARCHAR", "cardinality": 0.95},
                after={"dtype": "VARCHAR", "cardinality": 0.02},
            )
            == CompatibilityType.SEMANTIC_BREAKING
        )

    def test_stats_drifted_is_safe(self):
        from semzero.integrations.change_gate import CompatibilityType

        assert self._c("STATS_DRIFTED") == CompatibilityType.ADDITIVE_SAFE

    def test_table_added_safe(self):
        from semzero.integrations.change_gate import CompatibilityType

        assert self._c("TABLE_ADDED") == CompatibilityType.ADDITIVE_SAFE


class TestChangeGateVerdicts:
    def test_block_on_destructive(self, schema_graph, gate_config, drift_with_remove):
        from semzero.integrations.change_gate import ChangeGate, Verdict

        result = ChangeGate(schema_graph, gate_config).evaluate(drift_with_remove)
        assert result.verdict == Verdict.BLOCK
        assert len(result.blocking_assessments) > 0
        assert len(result.blocked_by) > 0

    def test_safe_on_additive_only(self, schema_graph, gate_config, drift_safe):
        from semzero.integrations.change_gate import ChangeGate, Verdict

        result = ChangeGate(schema_graph, gate_config).evaluate(drift_safe)
        assert result.verdict == Verdict.SAFE

    def test_needs_review_on_rename(self, schema_graph, gate_config):
        from semzero.integrations.change_gate import ChangeGate, Verdict

        drift = {
            "events": [
                {
                    "change_type": "COLUMN_RENAMED",
                    "severity": "HIGH",
                    "node_id": "users.email",
                    "before": {"dtype": "VARCHAR"},
                    "after": {"dtype": "VARCHAR"},
                    "detail": "renamed",
                }
            ]
        }
        result = ChangeGate(schema_graph, gate_config).evaluate(drift)
        assert result.verdict == Verdict.NEEDS_REVIEW

    def test_empty_events_safe(self, schema_graph, gate_config):
        from semzero.integrations.change_gate import ChangeGate, Verdict

        result = ChangeGate(schema_graph, gate_config).evaluate({"events": []})
        assert result.verdict == Verdict.SAFE

    def test_type_narrowing_blocks(self, schema_graph, gate_config):
        from semzero.integrations.change_gate import ChangeGate, Verdict

        drift = {
            "events": [
                {
                    "change_type": "TYPE_CHANGED",
                    "severity": "HIGH",
                    "node_id": "orders.total",
                    "before": {"dtype": "DOUBLE"},
                    "after": {"dtype": "INTEGER"},
                }
            ]
        }
        result = ChangeGate(schema_graph, gate_config).evaluate(drift)
        assert result.verdict == Verdict.BLOCK

    def test_nullable_hardening_needs_review(self, schema_graph, gate_config):
        from semzero.integrations.change_gate import ChangeGate, Verdict

        drift = {
            "events": [
                {
                    "change_type": "NULLABLE_CHANGED",
                    "severity": "MEDIUM",
                    "node_id": "users.name",
                    "before": {"nullable": True},
                    "after": {"nullable": False},
                }
            ]
        }
        result = ChangeGate(schema_graph, gate_config).evaluate(drift)
        assert result.verdict == Verdict.NEEDS_REVIEW

    def test_mixed_drift_inherits_worst(self, schema_graph, gate_config):
        """Safe + BLOCK → BLOCK."""
        from semzero.integrations.change_gate import ChangeGate, Verdict

        drift = {
            "events": [
                {
                    "change_type": "COLUMN_ADDED",
                    "node_id": "users.notes",
                    "after": {"nullable": True},
                },
                {"change_type": "COLUMN_REMOVED", "node_id": "orders.total"},
            ]
        }
        result = ChangeGate(schema_graph, gate_config).evaluate(drift)
        assert result.verdict == Verdict.BLOCK


class TestGateResult:
    def test_to_dict_has_required_keys(self, schema_graph, gate_config, drift_with_remove):
        from semzero.integrations.change_gate import ChangeGate

        result = ChangeGate(schema_graph, gate_config).evaluate(drift_with_remove)
        d = result.to_dict()
        for key in (
            "gate_id",
            "verdict",
            "assessments",
            "blocked_by",
            "total_blast_radius",
            "simulation_summary",
        ):
            assert key in d

    def test_rollout_playbook_populated(self, schema_graph, gate_config, drift_with_remove):
        from semzero.integrations.change_gate import ChangeGate

        result = ChangeGate(schema_graph, gate_config).evaluate(drift_with_remove)
        assert any(len(a.rollout_strategy) > 0 for a in result.assessments)

    def test_pr_comment_contains_verdict(self, schema_graph, gate_config, drift_with_remove):
        from semzero.integrations.change_gate import ChangeGate

        gate = ChangeGate(schema_graph, gate_config)
        result = gate.evaluate(drift_with_remove)
        comment = gate._build_pr_comment(result)
        assert "SemZero Change Gate" in comment
        assert "BLOCK" in comment or "Blocked" in comment or "🚫" in comment

    def test_save_creates_file(self, schema_graph, gate_config, drift_safe, tmp_path):
        from semzero.integrations.change_gate import ChangeGate

        result = ChangeGate(schema_graph, gate_config).evaluate(drift_safe)
        path = tmp_path / "gate.json"
        result.save(str(path))
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["verdict"] in ("SAFE", "NEEDS_REVIEW", "BLOCK")


class TestWindTunnelGateWiring:
    def test_disabled_by_default(self, schema_graph, gate_config, drift_with_remove):
        from semzero.integrations.change_gate import ChangeGate

        gate = ChangeGate(schema_graph, gate_config)
        result = gate.evaluate(drift_with_remove)
        result = gate.run_wind_tunnel(result, "SELECT 1")
        assert result.simulation_summary == ""  # run_wind_tunnel=False → no summary

    def test_no_db_url_gives_info_message(self, schema_graph):
        from semzero.integrations.change_gate import ChangeGate, GateConfig, Verdict

        config = GateConfig(run_wind_tunnel=True, db_url="")
        gate = ChangeGate(schema_graph, config)
        result_mock = type(
            "R",
            (),
            {
                "verdict": Verdict.NEEDS_REVIEW,
                "simulation_summary": "",
                "blocked_by": [],
            },
        )()
        result = gate.run_wind_tunnel(result_mock, "SELECT 1")
        assert "Not Configured" in result.simulation_summary

    def test_enabled_populates_summary(self, schema_graph, db_url):
        from semzero.integrations.change_gate import ChangeGate, GateConfig

        config = GateConfig(
            run_wind_tunnel=True,
            db_url=db_url,
            wind_tunnel_max_queries=3,
        )
        drift = {
            "events": [
                {
                    "change_type": "COLUMN_ADDED",
                    "node_id": "users.notes",
                    "after": {"nullable": True, "dtype": "TEXT"},
                }
            ]
        }
        gate = ChangeGate(schema_graph, config)
        result = gate.evaluate(drift)
        result = gate.run_wind_tunnel(
            result,
            migration_sql="ALTER TABLE users ADD COLUMN notes TEXT;",
            graph_json=schema_graph,
        )
        assert result.simulation_summary != ""
        assert "Wind Tunnel" in result.simulation_summary

    def test_wind_tunnel_in_pr_comment(self, schema_graph, db_url):
        from semzero.integrations.change_gate import ChangeGate, GateConfig

        config = GateConfig(run_wind_tunnel=True, db_url=db_url, wind_tunnel_max_queries=2)
        drift = {
            "events": [
                {
                    "change_type": "COLUMN_ADDED",
                    "node_id": "products.ean",
                    "after": {"nullable": True, "dtype": "TEXT"},
                }
            ]
        }
        gate = ChangeGate(schema_graph, config)
        result = gate.evaluate(drift)
        result = gate.run_wind_tunnel(result, "ALTER TABLE products ADD COLUMN ean TEXT;")
        comment = gate._build_pr_comment(result)
        assert "Wind Tunnel" in comment


# ══════════════════════════════════════════════════════════════════════════════
# CHAOS ENGINE
# ══════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def chaos_config(db_url, tmp_path):
    from semzero.chaos.chaos_engine import ChaosConfig

    return ChaosConfig(
        db_url=db_url,
        mutation_count=8,
        dry_run=True,
        parallel_mutations=False,
        generate_html=False,
        data_dir=str(tmp_path),
        store_path=str(tmp_path / "store.db"),
        history_path=str(tmp_path / "history.json"),
    )


class TestChaosEngineRun:
    def test_run_completes_without_error(self, chaos_config, schema_graph):
        from semzero.chaos.chaos_engine import ChaosEngine

        report = ChaosEngine(chaos_config).run(graph_json=schema_graph)
        assert report.error is None

    def test_fragility_score_valid_range(self, chaos_config, schema_graph):
        from semzero.chaos.chaos_engine import ChaosEngine

        report = ChaosEngine(chaos_config).run(graph_json=schema_graph)
        assert 0 <= report.fragility_score <= 100

    def test_fragility_grade_is_letter(self, chaos_config, schema_graph):
        from semzero.chaos.chaos_engine import ChaosEngine

        report = ChaosEngine(chaos_config).run(graph_json=schema_graph)
        assert report.fragility_grade in ("A", "B", "C", "D", "F", "?")

    def test_mutation_count_respected(self, chaos_config, schema_graph):
        from semzero.chaos.chaos_engine import ChaosEngine

        report = ChaosEngine(chaos_config).run(graph_json=schema_graph)
        assert report.mutations_applied <= chaos_config.mutation_count

    def test_fragility_dna_populated(self, chaos_config, schema_graph):
        from semzero.chaos.chaos_engine import ChaosEngine

        report = ChaosEngine(chaos_config).run(graph_json=schema_graph)
        assert report.fragility_dna is not None
        assert 0 <= report.fragility_dna.anti_pattern_score <= 100

    def test_serialises_to_dict(self, chaos_config, schema_graph):
        from semzero.chaos.chaos_engine import ChaosEngine

        report = ChaosEngine(chaos_config).run(graph_json=schema_graph)
        d = report.to_dict()
        assert all(k in d for k in ("summary", "fragility_dna", "mutation_results"))
        assert isinstance(d["mutation_results"], list)

    def test_saves_to_file(self, chaos_config, schema_graph, tmp_path):
        from semzero.chaos.chaos_engine import ChaosEngine

        report = ChaosEngine(chaos_config).run(graph_json=schema_graph)
        out = tmp_path / "report.json"
        report.save(str(out))
        assert out.exists()
        data = json.loads(out.read_text())
        assert "summary" in data


class TestFragilityDNA:
    def test_wide_table_detected(self, tmp_path):
        from semzero.chaos.chaos_engine import ChaosConfig, ChaosEngine

        nodes = [
            {
                "id": "fat",
                "label": "Table",
                "row_count": 10,
                "query_frequency": 0,
                "size_bytes": 0,
                "column_count": 40,
            }
        ]
        for i in range(40):
            nodes.append(
                {
                    "id": f"fat.c{i}",
                    "label": "Column",
                    "table": "fat",
                    "name": f"c{i}",
                    "dtype": "VARCHAR",
                    "nullable": True,
                    "is_primary_key": False,
                    "null_rate": 0.0,
                    "fingerprint": "x",
                }
            )
        graph = {"meta": {}, "nodes": nodes, "edges": []}
        engine = ChaosEngine(
            ChaosConfig(data_dir=str(tmp_path), history_path=str(tmp_path / "h.json"))
        )
        dna = engine._analyse_dna(graph)
        assert len(dna.wide_tables) > 0
        assert dna.anti_pattern_score > 0

    def test_nullable_fk_detected(self, tmp_path):
        from semzero.chaos.chaos_engine import ChaosConfig, ChaosEngine

        nodes = [
            {
                "id": "orders",
                "label": "Table",
                "row_count": 1,
                "query_frequency": 0,
                "size_bytes": 0,
                "column_count": 2,
            },
            {
                "id": "orders.user_id",
                "label": "Column",
                "table": "orders",
                "name": "user_id",
                "dtype": "INTEGER",
                "nullable": True,
                "is_primary_key": False,
                "null_rate": 0.1,
                "fingerprint": "a",
            },
            {
                "id": "users",
                "label": "Table",
                "row_count": 1,
                "query_frequency": 0,
                "size_bytes": 0,
                "column_count": 1,
            },
            {
                "id": "users.id",
                "label": "Column",
                "table": "users",
                "name": "id",
                "dtype": "INTEGER",
                "nullable": False,
                "is_primary_key": True,
                "null_rate": 0.0,
                "fingerprint": "b",
            },
        ]
        edges = [
            {
                "source": "orders.user_id",
                "target": "users.id",
                "relation": "REFERENCES",
                "weight": 2.0,
            }
        ]
        graph = {"meta": {}, "nodes": nodes, "edges": edges}
        engine = ChaosEngine(
            ChaosConfig(data_dir=str(tmp_path), history_path=str(tmp_path / "h.json"))
        )
        dna = engine._analyse_dna(graph)
        assert len(dna.nullable_fk_columns) > 0


class TestChaosTargeting:
    def test_targets_sorted_by_risk(self, schema_graph, tmp_path):
        from semzero.chaos.chaos_engine import ChaosConfig, ChaosEngine

        engine = ChaosEngine(
            ChaosConfig(data_dir=str(tmp_path), history_path=str(tmp_path / "h.json"))
        )
        G, targets = engine._compute_targets(schema_graph)
        assert len(targets) > 0
        scores = [t["risk_score"] for t in targets]
        assert scores == sorted(scores, reverse=True)

    def test_plan_respects_budget(self, schema_graph, tmp_path):
        from semzero.chaos.chaos_engine import ChaosConfig, ChaosEngine

        budget = 4
        engine = ChaosEngine(
            ChaosConfig(
                mutation_count=budget, data_dir=str(tmp_path), history_path=str(tmp_path / "h.json")
            )
        )
        import networkx as nx

        G = nx.DiGraph()
        G, targets = engine._compute_targets(schema_graph)
        plan = engine._build_plan(schema_graph, targets, G)
        assert len(plan) <= budget


class TestChaosScoring:
    def test_all_resilient_high_score(self):
        from semzero.chaos.chaos_engine import (
            ChaosReport,
            MutationResult,
            MutationType,
            ResilienceLevel,
            CascadeResult,
        )

        report = ChaosReport(run_id="x", started_at="now")
        for i in range(10):
            r = MutationResult(
                MutationType.ADD_COLUMN,
                f"t.c{i}",
                "",
                resilience=ResilienceLevel.RESILIENT,
                blast_score=0.05,
            )
            r.cascade = CascadeResult(origin_node=f"t.c{i}", cascade_score=0.0)
            report.mutation_results.append(r)
        report.compute_score()
        assert report.fragility_score >= 60
        assert report.fragility_grade in ("A", "B", "C")

    def test_all_critical_low_score(self):
        from semzero.chaos.chaos_engine import (
            ChaosReport,
            MutationResult,
            MutationType,
            ResilienceLevel,
            CascadeResult,
        )

        report = ChaosReport(run_id="x", started_at="now")
        for i in range(10):
            r = MutationResult(
                MutationType.REMOVE_COLUMN,
                f"t.c{i}",
                "",
                resilience=ResilienceLevel.CRITICAL,
                blast_score=0.9,
            )
            r.cascade = CascadeResult(origin_node=f"t.c{i}", cascade_score=0.9, max_depth=5)
            report.mutation_results.append(r)
        report.compute_score()
        assert report.fragility_score < 50
        assert report.fragility_grade in ("D", "F")


# ══════════════════════════════════════════════════════════════════════════════
# END-TO-END PIPELINE
# ══════════════════════════════════════════════════════════════════════════════


class TestEndToEndPipeline:
    def test_crawl_diff_gate_wind_tunnel(self, db_url, schema_graph, tmp_path):
        """
        Full pipeline: crawl → drift detect → gate evaluate →
        wind tunnel → PR comment contains everything.
        """
        from semzero.crawler.drift import SchemaDriftDetector
        from semzero.integrations.change_gate import ChangeGate, GateConfig, Verdict

        # Simulate column removal
        v2 = json.loads(json.dumps(schema_graph))
        v2["nodes"] = [n for n in v2["nodes"] if n["id"] != "products.price"]

        drift = SchemaDriftDetector().diff(schema_graph, v2, "v1", "v2")
        assert not drift.is_clean

        removed = [e for e in drift.events if e.change_type.value == "COLUMN_REMOVED"]
        assert len(removed) > 0

        config = GateConfig(
            run_wind_tunnel=True,
            db_url=db_url,
            wind_tunnel_max_queries=5,
            data_dir=str(tmp_path),
        )
        gate = ChangeGate(schema_graph, config)
        result = gate.evaluate(drift.to_dict())
        assert result.verdict == Verdict.BLOCK

        result = gate.run_wind_tunnel(
            result,
            migration_sql="ALTER TABLE products DROP COLUMN price;",
            graph_json=schema_graph,
        )
        assert result.simulation_summary != ""

        comment = gate._build_pr_comment(result)
        assert "SemZero Change Gate" in comment
        assert "Wind Tunnel" in comment
        assert "🚫" in comment


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
