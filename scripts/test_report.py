import json
from semzero.reporting.reporter import HTMLReporter

graph = json.load(open("data/schema_graph_v2.json"))
drift = json.load(open("data/drift_report.json"))
repair = json.load(open("data/repair_plan.json"))

reporter = HTMLReporter()
path = reporter.generate(
    graph_json=graph, drift_report=drift, repair_plan=repair, output_path="data/semzero_report.html"
)

print(f"Report ready → {path}")
print(f"Open it:  open {path}")
