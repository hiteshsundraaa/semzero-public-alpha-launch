"""
test_rca.py — End-to-end test for the RCA forensic agent.

Run from project root:
  python scripts/test_rca.py
"""

import json
import os
from pathlib import Path
from sqlalchemy import create_engine, text, inspect

from semzero.crawler.builder import SchemaGraphBuilder
from semzero.crawler.drift import SchemaDriftDetector
from semzero.crawler.graph_store import GraphStore
from semzero.analytics.rca import RCAAgent

# ── Setup ──────────────────────────────────────────────────────────────────────
db_url = "sqlite:///test.db"
store_path = "data/graph_store.db"
engine = create_engine(db_url)

print("\n  SemZero RCA Agent Test")
print("  " + "─" * 40)

# ── Check what columns actually exist ─────────────────────────────────────────
print("\n  [0/5] Inspecting current schema...")
inspector = inspect(engine)
for table in inspector.get_table_names():
    cols = [c["name"] for c in inspector.get_columns(table)]
    print(f"        {table}: {cols}")

# Find a renameable column on users
users_cols = [c["name"] for c in inspector.get_columns("users")]

# Pick a non-PK column to rename
renameable = next((c for c in users_cols if c not in ("id",)), None)

if not renameable:
    print("  No renameable column found. Seeding fresh database...")
    with engine.connect() as conn:
        conn.execute(text("DROP TABLE IF EXISTS orders"))
        conn.execute(text("DROP TABLE IF EXISTS users"))
        conn.execute(
            text("""
            CREATE TABLE users (
                id INTEGER PRIMARY KEY,
                full_name VARCHAR(100),
                email VARCHAR(255),
                created_at TIMESTAMP
            )
        """)
        )
        conn.execute(
            text("""
            CREATE TABLE orders (
                id INTEGER PRIMARY KEY,
                user_id INTEGER REFERENCES users(id),
                amount NUMERIC,
                status VARCHAR(50)
            )
        """)
        )
        conn.commit()
    renameable = "full_name"

print(f"\n  Will rename: users.{renameable}")

# ── Step 1: Crawl v1 ───────────────────────────────────────────────────────────
print("\n  [1/5] Crawling baseline schema...")
store = GraphStore(store_path)
builder1 = SchemaGraphBuilder(db_url, store=store)
graph_v1 = builder1.build(label="rca_test_v1")
builder1.save("data/schema_graph_rca_v1.json")
print(f"        {graph_v1['meta']['table_count']} tables, {graph_v1['meta']['node_count']} nodes")

# ── Step 2: Apply breaking schema change ──────────────────────────────────────
new_col_name = f"{renameable}_legacy"
print(f"\n  [2/5] Applying root cause: users.{renameable} -> users.{new_col_name}")
with engine.connect() as conn:
    conn.execute(text(f'ALTER TABLE users RENAME COLUMN "{renameable}" TO "{new_col_name}"'))
    conn.commit()
print(f"        Done.")

# ── Step 3: Crawl v2 ───────────────────────────────────────────────────────────
print("\n  [3/5] Crawling post-change schema...")
builder2 = SchemaGraphBuilder(db_url, store=store)
graph_v2 = builder2.build(label="rca_test_v2")
builder2.save("data/schema_graph_rca_v2.json")
print(f"        {graph_v2['meta']['table_count']} tables, {graph_v2['meta']['node_count']} nodes")

# ── Step 4: Detect drift and save with timestamps ────────────────────────────
print("\n  [4/5] Detecting drift...")
detector = SchemaDriftDetector()
report = detector.diff(graph_v1, graph_v2, before_label="rca_test_v1", after_label="rca_test_v2")

print(f"        {len(report.events)} changes detected:")
for e in report.events:
    print(f"          [{e.severity}] {e.change_type}: {e.detail}")

# Stamp each event with detection time so RCA recency scoring works
drift_dict = report.to_dict()
for event in drift_dict["events"]:
    event["changed_at"] = drift_dict["detected_at"]

Path("data/drift_report.json").write_text(json.dumps(drift_dict, indent=2))
print(f"        Saved -> data/drift_report.json")

# ── Step 5: RCA investigation ──────────────────────────────────────────────────
print("\n  [5/5] Running RCA investigations...\n")

graph_for_rca = json.loads(Path("data/schema_graph_rca_v2.json").read_text())
agent = RCAAgent(graph_for_rca, store_path=store_path, lookback_hours=24)

# Investigate orders — it joins to users via user_id
print("  Investigation 1: orders table")
rca1 = agent.investigate("orders")
print(rca1.explain())

# Investigate orders.user_id specifically
print("  Investigation 2: orders.user_id")
rca2 = agent.investigate("orders.user_id")
print(rca2.explain())

rca1.save("data/rca_report.json")
print("  Full report -> data/rca_report.json\n")

# ── Restore schema ─────────────────────────────────────────────────────────────
print("  Restoring schema...")
with engine.connect() as conn:
    conn.execute(text(f'ALTER TABLE users RENAME COLUMN "{new_col_name}" TO "{renameable}"'))
    conn.commit()
print("  Done.\n")
