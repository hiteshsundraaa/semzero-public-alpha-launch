import subprocess
import sys
from pathlib import Path

from semzero.reliability.dogfood_report import DogfoodReportBuilder


def test_dogfood_report_builder_after_script_run():
    root = Path(__file__).resolve().parents[1]
    subprocess.run([sys.executable, "scripts/run_dogfood_assumption_gate.py"], cwd=root, check=True)
    dogfood = root / "examples" / "dogfood_dbt_assumption_gate"
    builder = DogfoodReportBuilder(dogfood)
    payload = builder.build()
    assert payload["report_kind"] == "semzero_dogfood_demo_report_v1_13"
    assert payload["scenario_count"] == 6
    assert payload["scenario_fail_count"] == 0
    assert set(payload["families_covered"]) >= {
        "temporal_bucket",
        "incremental_filter",
        "join_cardinality",
        "enum_domain_closure",
        "null_default_fallback",
    }
    md = builder.save_markdown(dogfood / "dogfood_demo_report.md")
    assert "Product loop demonstrated" in md
    assert "dbt PR diff" in md
