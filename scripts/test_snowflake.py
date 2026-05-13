"""
test_snowflake.py — Test the Snowflake connector.

Before running:
  pip install snowflake-sqlalchemy

  export SNOWFLAKE_ACCOUNT=xy12345.us-east-1
  export SNOWFLAKE_USER=your_username
  export SNOWFLAKE_PASSWORD=your_password
  export SNOWFLAKE_DATABASE=your_database
  export SNOWFLAKE_SCHEMA=PUBLIC
  export SNOWFLAKE_WAREHOUSE=COMPUTE_WH

  python scripts/test_snowflake.py

Or with a direct URL:
  python scripts/test_snowflake.py --url "snowflake://user:pass@account/db/schema?warehouse=WH"
"""

import sys
import json
import argparse
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument(
    "--url", default=None, help="Snowflake URL (optional, uses env vars if not set)"
)
args = parser.parse_args()

print("\n  SemZero — Snowflake Connector Test")
print("  " + "─" * 40)

# ── Connect ───────────────────────────────────────────────────────────────────
print("\n  [1/4] Connecting to Snowflake...")

try:
    from src.crawler.connector_factory import get_connector

    connector = get_connector(db_url=args.url or "", dialect="snowflake" if not args.url else "")
    print(f"        Connected.")
except EnvironmentError as e:
    print(f"\n  ✗ Missing environment variables:\n  {e}")
    sys.exit(1)
except ImportError as e:
    print(f"\n  ✗ {e}")
    sys.exit(1)

# ── Get tables ────────────────────────────────────────────────────────────────
print("\n  [2/4] Fetching tables...")
tables = connector.get_tables()

if not tables:
    print("  ✗ No tables found. Check your SNOWFLAKE_SCHEMA and permissions.")
    sys.exit(1)

print(f"        Found {len(tables)} tables:")
for t in tables[:10]:
    print(f"          - {t}")
if len(tables) > 10:
    print(f"          ... and {len(tables) - 10} more")

# ── Crawl stats ───────────────────────────────────────────────────────────────
print(f"\n  [3/4] Crawling stats for first 5 tables...")
sample_tables = tables[:5]
all_stats = connector.get_all_table_stats(sample_tables)

for table, stats in all_stats.items():
    if stats.failed:
        print(f"        ✗ {table}: {stats.crawl_error}")
    else:
        cluster_info = f" [clustered on {stats.clustering_keys}]" if stats.is_clustered else ""
        print(f"        ✓ {table}: {stats.row_count:,} rows, {stats.size_gb:.3f}GB{cluster_info}")
        for col in stats.columns[:3]:
            print(
                f"             {col.name}: {col.dtype} "
                f"null_rate={col.null_rate:.1%} "
                f"cardinality={col.cardinality:.3f}"
            )

# ── Build graph ───────────────────────────────────────────────────────────────
print(f"\n  [4/4] Building schema graph...")

from src.crawler.builder import SchemaGraphBuilder
from src.crawler.graph_store import GraphStore

store = GraphStore("data/graph_store.db")
builder = SchemaGraphBuilder.__new__(SchemaGraphBuilder)
builder.connector = connector
builder.store = store
builder.graph = {"meta": {}, "nodes": [], "edges": []}

graph = builder.build(label="snowflake_crawl")
path = builder.save("data/schema_graph_snowflake.json")

print(f"        Tables:  {graph['meta']['table_count']}")
print(f"        Nodes:   {graph['meta']['node_count']}")
print(f"        Edges:   {graph['meta']['edge_count']}")
print(f"        Saved:   {path}")
print(f"\n  ✓ Snowflake crawl complete.\n")
print(f"  Next steps:")
print(f"    semzero diff --before <prev_id> --after {graph.get('_snapshot_id')}")
print(f"    semzero trace --node <table_name>")
print(f"    semzero report --graph data/schema_graph_snowflake.json\n")
