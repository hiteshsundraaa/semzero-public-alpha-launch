"""
debug_wt.py — Wind Tunnel debug script.
Runs inside Docker to diagnose exactly where queries go to 0.
docker compose exec semzero python scripts/debug_wt.py
"""

import sys, json, logging

logging.basicConfig(level=logging.DEBUG, format="%(name)s %(levelname)s — %(message)s")

DB_URL = "postgresql://semzero:semzero@postgres/demo"

print("\n=== Step 1: Load graph ===")
try:
    graph = json.loads(open("data/schema_graph.json").read())
    tables = [n for n in graph["nodes"] if n.get("label") == "Table"]
    cols = [n for n in graph["nodes"] if n.get("label") == "Column"]
    edges = [e for e in graph["edges"] if e.get("relation") == "REFERENCES"]
    print(f"Tables: {len(tables)}, Columns: {len(cols)}, FK edges: {len(edges)}")
    print(f"Sample table: {tables[0] if tables else 'NONE'}")
    print(f"Sample col:   {cols[0] if cols else 'NONE'}")
except Exception as e:
    print(f"FAILED: {e}")
    sys.exit(1)

print("\n=== Step 2: Import WindTunnel and check version ===")
try:
    from semzero.chaos.wind_tunnel import (
        WindTunnelConfig,
        MigrationWindTunnel,
        QueryExtractor,
        CloneManager,
    )
    import inspect

    sig = inspect.signature(WindTunnelConfig.__init__)
    print(f"WindTunnelConfig fields: {list(sig.parameters.keys())[:8]}")
    print(f"Has provided_queries: {'provided_queries' in sig.parameters}")
    print(f"Has query_source: {'query_source' in sig.parameters}")

    # Check if _graph_from_drift exists
    has_gfd = hasattr(MigrationWindTunnel, "_graph_from_drift")
    print(f"Has _graph_from_drift: {has_gfd}")

    # Check extract method
    src = inspect.getsource(QueryExtractor.extract)
    print(f"extract() has 'Synthetic — ALWAYS': {'ALWAYS' in src}")
    print(f"extract() has synthetic fallback: {'_synthetic(graph_json)' in src}")
except Exception as e:
    print(f"FAILED: {e}")
    import traceback

    traceback.print_exc()

print("\n=== Step 3: Direct _synthetic test ===")
try:
    from sqlalchemy import create_engine

    engine = create_engine(DB_URL, pool_pre_ping=True)

    cfg = WindTunnelConfig(
        db_url=DB_URL,
        query_source="synthetic",
        max_queries=80,
        dry_run=False,
        data_dir="data",
        post_to_pr=False,
    )
    extractor = QueryExtractor(engine, "postgresql", cfg)

    # Call _synthetic directly with graph
    queries = extractor._synthetic(graph)
    print(f"_synthetic(graph) returned: {len(queries)} queries")
    if queries:
        for q in queries[:3]:
            print(f"  {q['query_id']}: {q['query_text'][:60]}")
    else:
        print("  EMPTY — investigating...")
        # Try with just nodes
        print(f"  graph has {len(graph.get('nodes', []))} nodes")
        print(f"  Table nodes: {len([n for n in graph['nodes'] if n.get('label') == 'Table'])}")
        test_tables = [n["id"] for n in graph.get("nodes", []) if n.get("label") == "Table"]
        print(f"  Table IDs: {test_tables[:3]}")
except Exception as e:
    print(f"FAILED: {e}")
    import traceback

    traceback.print_exc()

print("\n=== Step 4: Full extract() test ===")
try:
    queries = extractor.extract(graph)
    print(f"extract(graph) returned: {len(queries)} queries")
    if queries:
        for q in queries[:3]:
            print(f"  {q['query_id']}: {q['query_text'][:60]}")
except Exception as e:
    print(f"FAILED: {e}")
    import traceback

    traceback.print_exc()

print("\n=== Step 5: Clone creation test ===")
try:
    import time

    t0 = time.time()
    run_id = "debug01"
    mgr = CloneManager(cfg, "postgresql", run_id)
    orig_engine = create_engine(DB_URL, pool_pre_ping=True)
    clone_engine = mgr.create(orig_engine)
    print(f"Clone created in {time.time() - t0:.1f}s: {mgr._clone_dbname}")

    # Run a query on clone
    from sqlalchemy import text

    with clone_engine.connect() as conn:
        n = conn.execute(text('SELECT COUNT(*) FROM "orders"')).scalar()
        print(f"Clone has {n} rows in orders table")

    # Clean up
    mgr.destroy(clone_engine)
    print("Clone destroyed OK")
except Exception as e:
    print(f"FAILED: {e}")
    import traceback

    traceback.print_exc()

print("\n=== Step 6: Full Wind Tunnel run ===")
try:
    migration_sql = 'ALTER TABLE "orders" RENAME COLUMN "user_id" TO "account_id";'
    cfg2 = WindTunnelConfig(
        db_url=DB_URL,
        query_source="synthetic",
        max_queries=80,
        dry_run=False,
        data_dir="data",
        post_to_pr=False,
        run_semantic_analysis=False,
    )
    tunnel = MigrationWindTunnel(cfg2)
    receipt = tunnel.run(
        migration_sql=migration_sql,
        graph_json=graph,
    )
    print(f"Verdict:   {receipt.verdict}")
    print(f"Replayed:  {receipt.queries_replayed}")
    print(f"Passed:    {receipt.queries_passed}")
    print(f"Broken:    {receipt.queries_broken}")
    print(f"Confidence:{receipt.confidence_score}%")
    print(f"Duration:  {receipt.duration_s}s")
    if receipt.error:
        print(f"Error:     {receipt.error}")
except Exception as e:
    print(f"FAILED: {e}")
    import traceback

    traceback.print_exc()
