#!/usr/bin/env python3
"""Run SemZero Assumption Gate dogfood scenarios.

This script is intentionally local/offline: it uses the packaged mini dbt
manifest and scenario diffs, then writes one receipt/comment per scenario plus
an aggregate assumption dashboard.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from semzero.integrations.dbt_assumption_gate import (
    DbtAssumptionGate,
    render_pr_comment,
    load_table_sizes,
    load_cost_profiles,
    load_business_criticality,
    load_warehouse_history,
    load_replay_fixtures,
)
from semzero.reliability.assumption_dashboard import AssumptionDashboard
from semzero.reliability.dogfood_report import DogfoodReportBuilder
from semzero.reliability.assumption_precision import AssumptionPrecisionEvaluator, PrecisionConfig
from semzero.reliability.assumption_lineage import AssumptionLineageBuilder
from semzero.reliability.assumption_decay import AssumptionDecayConfig, AssumptionDecayTracker
from semzero.reliability.assumption_memory import AssumptionMemoryBuilder, AssumptionMemoryConfig

DOGFOOD = ROOT / "examples" / "dogfood_dbt_assumption_gate"


def main() -> int:
    manifest = DOGFOOD / "target" / "manifest.json"
    table_sizes = DOGFOOD / "table_sizes" / "table_sizes.json"
    cost_profiles = DOGFOOD / "table_sizes" / "cost_profiles.yml"
    scenarios_path = DOGFOOD / "scenarios" / "scenarios.json"
    criticality_registry = DOGFOOD / "criticality" / "business_criticality.yml"
    warehouse_history = DOGFOOD / "warehouse_history" / "snowflake_query_history.csv"
    replay_fixtures = DOGFOOD / "replay_fixtures" / "replay_lite_samples.json"
    receipts_dir = DOGFOOD / "receipts"
    comments_dir = DOGFOOD / "comments"
    receipts_dir.mkdir(parents=True, exist_ok=True)
    comments_dir.mkdir(parents=True, exist_ok=True)

    spec = json.loads(scenarios_path.read_text(encoding="utf-8"))
    gate = DbtAssumptionGate(
        manifest,
        table_sizes=load_table_sizes(table_sizes),
        cost_profiles=load_cost_profiles(cost_profiles),
        warehouse_history=load_warehouse_history(warehouse_history),
        replay_fixtures=load_replay_fixtures(replay_fixtures),
        criticality_registry=load_business_criticality(criticality_registry),
    )
    results = []
    for scenario in spec["scenarios"]:
        sid = scenario["id"]
        diff_text = (DOGFOOD / scenario["diff"]).read_text(encoding="utf-8")
        receipt = gate.run([scenario["changed_file"]], mode="shadow", changed_diff=diff_text)
        payload = receipt.to_dict()
        receipt_path = receipts_dir / f"{sid}.receipt.json"
        comment_path = comments_dir / f"{sid}.comment.md"
        receipt_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        comment_path.write_text(render_pr_comment(receipt), encoding="utf-8")
        families = sorted({finding["family"] for finding in payload.get("findings", [])})
        results.append(
            {
                "id": sid,
                "expected_family": scenario["expected_family"],
                "families": families,
                "verdict": payload.get("verdict"),
                "finding_count": payload.get("summary", {}).get("finding_count", 0),
                "receipt": str(receipt_path.relative_to(ROOT)),
                "comment": str(comment_path.relative_to(ROOT)),
            }
        )

    dashboard_obj = AssumptionDashboard(receipt_dir=str(receipts_dir))
    dashboard = dashboard_obj.build()
    dashboard_path = DOGFOOD / "assumption_dashboard.json"
    dashboard_md = DOGFOOD / "assumption_dashboard.md"
    dashboard_path.write_text(json.dumps(dashboard, indent=2), encoding="utf-8")
    dashboard_obj.save_markdown(dashboard_md)

    precision = AssumptionPrecisionEvaluator(PrecisionConfig(receipt_dir=receipts_dir)).build()
    precision_path = DOGFOOD / "assumption_precision_eval.json"
    precision_md = DOGFOOD / "assumption_precision_eval.md"
    precision_path.write_text(json.dumps(precision, indent=2), encoding="utf-8")
    AssumptionPrecisionEvaluator(PrecisionConfig(receipt_dir=receipts_dir)).save_markdown(
        precision_md
    )

    lineage_builder = AssumptionLineageBuilder(receipt_dir=str(receipts_dir))
    lineage_path = DOGFOOD / "assumption_lineage.json"
    lineage_md = DOGFOOD / "assumption_lineage.md"
    lineage_builder.save_json(lineage_path)
    lineage_builder.save_markdown(lineage_md)

    decay_tracker = AssumptionDecayTracker(AssumptionDecayConfig(receipt_dir=receipts_dir))
    decay_path = DOGFOOD / "assumption_decay.json"
    decay_md = DOGFOOD / "assumption_decay.md"
    decay_tracker.save_json(decay_path)
    decay_tracker.save_markdown(decay_md)

    memory_builder = AssumptionMemoryBuilder(AssumptionMemoryConfig(receipt_dir=receipts_dir))
    memory_path = DOGFOOD / "assumption_memory.json"
    memory_md = DOGFOOD / "assumption_memory.md"
    memory_builder.save_json(memory_path)
    memory_builder.save_markdown(memory_md)

    summary_path = DOGFOOD / "dogfood_run_summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "scenario_results": results,
                "dashboard": str(dashboard_path.relative_to(ROOT)),
                "precision_eval": str(precision_path.relative_to(ROOT)),
                "lineage": str(lineage_path.relative_to(ROOT)),
                "decay": str(decay_path.relative_to(ROOT)),
                "memory": str(memory_path.relative_to(ROOT)),
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    report_builder = DogfoodReportBuilder(DOGFOOD)
    report_json = DOGFOOD / "dogfood_demo_report.json"
    report_md = DOGFOOD / "dogfood_demo_report.md"
    report_builder.save_json(report_json)
    report_builder.save_markdown(report_md)

    print(f"Wrote {len(results)} dogfood scenario receipts to {receipts_dir}")
    print(f"Dashboard: {dashboard_path}")
    print(f"Precision eval: {DOGFOOD / 'assumption_precision_eval.md'}")
    print(f"Lineage: {DOGFOOD / 'assumption_lineage.md'}")
    print(f"Decay: {DOGFOOD / 'assumption_decay.md'}")
    print(f"Memory: {DOGFOOD / 'assumption_memory.md'}")
    print(f"Demo report: {DOGFOOD / 'dogfood_demo_report.md'}")
    for row in results:
        status = "ok" if row["expected_family"] in row["families"] else "missing"
        print(
            f"- {row['id']}: {row['verdict']} · {row['finding_count']} findings · expected {row['expected_family']} = {status}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
