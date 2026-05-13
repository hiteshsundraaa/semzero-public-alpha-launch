"""
test_chaos.py — End-to-end Chaos Mode test.

Run from project root:
  python scripts/test_chaos.py
"""

import json
from pathlib import Path
from semzero.chaos.chaos_engine import ChaosConfig, ChaosEngine
from semzero.chaos.chaos_reporter import ChaosTerminalReporter, ChaosHTMLReporter

print("\n  SemZero Chaos Mode Test")
print("  " + "─" * 44)

graph_path = Path("data/schema_graph.json")
if not graph_path.exists():
    print("\n  ✗ No schema_graph.json — run: python scripts/test_crawl.py\n")
    exit(1)

graph_json = json.loads(graph_path.read_text())
print(
    f"\n  Graph:     {graph_json['meta']['table_count']} tables, "
    f"{graph_json['meta']['node_count']} nodes"
)

config = ChaosConfig(
    mutation_count=40,
    run_dbt_tests=False,  # graph-only — no dbt needed
    generate_html=True,
    dry_run=False,
    parallel_mutations=True,
    max_workers=4,
    data_dir="data",
)

print(f"  Mutations: {config.mutation_count}")
print(f"  Mode:      graph-only (cascade + DNA analysis)")
print(f"  Parallel:  {config.parallel_mutations}\n")

engine = ChaosEngine(config)
report = engine.run(graph_json=graph_json)

# Terminal summary (already printed by engine)
# Save
report.save("data/chaos_report.json")

# HTML
path = ChaosHTMLReporter().generate(
    report=report,
    output_path="data/chaos_report.html",
)

s = report.summary()
print(f"\n  ── Results ──────────────────────────────")
print(f"  Fragility Score:  {s['fragility_score']}/100  (Grade {s['fragility_grade']})")
print(f"  Anti-pattern DNA: {s['anti_pattern_score']}/100")
print(f"  Mutations broke:  {s['mutations_that_broke']}/{s['mutations_applied']}")
print(f"  Critical pipes:   {s['critical_pipelines']}")
print(f"  Duration:         {s['duration_s']:.1f}s")
print(f"\n  JSON  → data/chaos_report.json")
print(f"  HTML  → {path}")
print(f"  Open:   open data/chaos_report.html\n")
