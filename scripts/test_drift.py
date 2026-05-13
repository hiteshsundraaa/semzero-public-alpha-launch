import json
import os
import shutil
from sqlalchemy import create_engine, text
from semzero.crawler.builder import SchemaGraphBuilder
from semzero.crawler.drift import SchemaDriftDetector
from semzero.reporting.reporter import TerminalReporter

# Start fresh every time
if os.path.exists("test.db"):
    os.remove("test.db")

db_url = "sqlite:///test.db"
engine = create_engine(db_url)

# ── Step 1: Seed fresh database ──────────────────────────────────────
print("Seeding database...")
with engine.connect() as conn:
    conn.execute(
        text("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            email VARCHAR(255) NOT NULL,
            name VARCHAR(100),
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
    conn.execute(
        text("""
        CREATE TABLE products (
            id INTEGER PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            price NUMERIC,
            stock INTEGER
        )
    """)
    )
    conn.commit()

# ── Step 2: Crawl v1 ─────────────────────────────────────────────────
print("Crawling v1...")
builder = SchemaGraphBuilder(db_url)
builder.build()
builder.save("data/schema_graph_v1.json")
print("Saved v1.")

# ── Step 3: Apply schema changes ─────────────────────────────────────
print("\nApplying schema changes...")
with engine.connect() as conn:
    conn.execute(text("ALTER TABLE users RENAME COLUMN email TO email_address"))
    conn.execute(text("ALTER TABLE orders ADD COLUMN notes VARCHAR(500)"))
    conn.execute(
        text("""
        CREATE TABLE products_new (
            id INTEGER PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            price NUMERIC
        )
    """)
    )
    conn.execute(text("INSERT INTO products_new SELECT id, name, price FROM products"))
    conn.execute(text("DROP TABLE products"))
    conn.execute(text("ALTER TABLE products_new RENAME TO products"))
    conn.commit()
print("Schema changes applied.")

# ── Step 4: Crawl v2 ─────────────────────────────────────────────────
print("Crawling v2...")
builder2 = SchemaGraphBuilder(db_url)
builder2.build()
builder2.save("data/schema_graph_v2.json")
print("Saved v2.")

# ── Step 5: Diff ─────────────────────────────────────────────────────
print("\nRunning drift detection...\n")
v1 = json.load(open("data/schema_graph_v1.json"))
v2 = json.load(open("data/schema_graph_v2.json"))

detector = SchemaDriftDetector()
report = detector.diff(v1, v2, before_label="v1", after_label="v2")

TerminalReporter().print_drift_report(report.to_dict())
report.save("data/drift_report.json")
print("Full report saved to data/drift_report.json")
