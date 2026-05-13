"""
conftest.py — Shared fixtures for all SemZero tests.

All tests use SQLite in-memory or file databases.
No Postgres, Snowflake, or network access required.
"""

import json
import sqlite3
import tempfile
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest


# ── SQLite test database ───────────────────────────────────────────────────────

SEED_SQL = """
CREATE TABLE users (
    id         INTEGER PRIMARY KEY,
    email      TEXT    NOT NULL UNIQUE,
    name       TEXT,
    status     TEXT    NOT NULL DEFAULT 'active',
    created_at TEXT    DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE products (
    id       INTEGER PRIMARY KEY,
    name     TEXT  NOT NULL,
    sku      TEXT  UNIQUE,
    price    REAL  NOT NULL,
    stock    INTEGER DEFAULT 0,
    category TEXT
);
CREATE TABLE orders (
    id         INTEGER PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id),
    status     TEXT    NOT NULL DEFAULT 'pending',
    total      REAL,
    created_at TEXT    DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE order_items (
    id         INTEGER PRIMARY KEY,
    order_id   INTEGER NOT NULL REFERENCES orders(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    qty        INTEGER NOT NULL DEFAULT 1,
    unit_price REAL    NOT NULL
);
CREATE TABLE events (
    id         INTEGER PRIMARY KEY,
    user_id    INTEGER REFERENCES users(id),
    event_type TEXT,
    payload    TEXT,
    ts         TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Seed data
INSERT INTO users   VALUES (1,'alice@co.com','Alice','active','2024-01-01');
INSERT INTO users   VALUES (2,'bob@co.com',  'Bob',  'active','2024-01-02');
INSERT INTO products VALUES (1,'Widget','WID-001',9.99, 100,'tools');
INSERT INTO products VALUES (2,'Gadget','GAD-002',29.99,50, 'electronics');
INSERT INTO products VALUES (3,'Doohickey','DOO-003',4.99,200,'misc');
INSERT INTO orders  VALUES (1,1,'completed',19.98,'2024-01-10');
INSERT INTO orders  VALUES (2,2,'pending',  29.99,'2024-01-11');
INSERT INTO order_items VALUES (1,1,1,2,9.99);
INSERT INTO order_items VALUES (2,2,2,1,29.99);
INSERT INTO events  VALUES (1,1,'page_view',  '{"page":"/home"}',  '2024-01-10');
INSERT INTO events  VALUES (2,2,'add_to_cart','{"product":1}',      '2024-01-11');
INSERT INTO events  VALUES (3,1,'purchase',   '{"order":1}',        '2024-01-10');
"""


@pytest.fixture(scope="session")
def db_path(tmp_path_factory) -> Path:
    """Create a persistent SQLite file database for the test session."""
    p = tmp_path_factory.mktemp("semzero") / "test.db"
    conn = sqlite3.connect(str(p))
    conn.executescript(SEED_SQL)
    conn.commit()
    conn.close()
    return p


@pytest.fixture(scope="session")
def db_url(db_path) -> str:
    return f"sqlite:///{db_path}"


@pytest.fixture(scope="session")
def schema_graph(db_url, tmp_path_factory) -> dict:
    """Crawl the test DB once and reuse the graph for the session."""
    from semzero.crawler.builder import SchemaGraphBuilder

    store = tmp_path_factory.mktemp("store") / "store.db"
    builder = SchemaGraphBuilder(db_url, collect_stats=True, store_path=str(store))
    return builder.build(label="test_baseline")


@pytest.fixture
def drift_with_remove(schema_graph) -> dict:
    """Drift report that removes a column — should produce BLOCK."""
    from semzero.crawler.drift import SchemaDriftDetector

    v2 = json.loads(json.dumps(schema_graph))
    v2["nodes"] = [n for n in v2["nodes"] if n["id"] != "products.price"]
    return SchemaDriftDetector().diff(schema_graph, v2, "v1", "v2").to_dict()


@pytest.fixture
def drift_safe(schema_graph) -> dict:
    """Drift report with only additive changes — should produce SAFE."""
    from semzero.crawler.drift import SchemaDriftDetector

    v2 = json.loads(json.dumps(schema_graph))
    v2["nodes"].append(
        {
            "id": "products.description",
            "label": "Column",
            "table": "products",
            "name": "description",
            "dtype": "VARCHAR",
            "nullable": True,
            "is_primary_key": False,
            "null_rate": 0.0,
            "fingerprint": "abc123",
        }
    )
    return SchemaDriftDetector().diff(schema_graph, v2, "v1", "v2").to_dict()


@pytest.fixture
def gate_config():
    from semzero.integrations.change_gate import GateConfig

    return GateConfig(
        block_on_destructive=True,
        block_on_narrowing=True,
        require_review_rename=True,
        auto_patch_consumers=False,
        run_wind_tunnel=False,
    )
