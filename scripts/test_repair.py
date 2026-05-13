import json
from semzero.orchestrator.repair import RepairEngine
from semzero.crawler.drift import DriftEvent, ChangeType, Severity
from semzero.reporting.reporter import TerminalReporter

drift_data = json.load(open("data/drift_report.json"))

events = [
    DriftEvent(
        change_type=ChangeType(e["change_type"]),
        severity=Severity(e["severity"]),
        node_id=e["node_id"],
        before=e.get("before"),
        after=e.get("after"),
        detail=e.get("detail", ""),
    )
    for e in drift_data["events"]
]

engine = RepairEngine()
plan = engine.build_plan(events)

TerminalReporter().print_repair_plan(plan.to_dict())

with open("data/repair_plan.json", "w") as f:
    json.dump(plan.to_dict(), f, indent=2)

with open("data/migration.sql", "w") as f:
    f.write(plan.render_sql_script())

print("Repair plan → data/repair_plan.json")
print("Migration SQL → data/migration.sql")
