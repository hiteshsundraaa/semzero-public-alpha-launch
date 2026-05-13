import json
from pathlib import Path

from semzero.reliability.assumption_lineage import AssumptionLineageBuilder


def _ensure_dogfood_receipts():
    receipt_dir = Path("examples/dogfood_dbt_assumption_gate/receipts")
    if receipt_dir.exists() and any(receipt_dir.glob("*.json")):
        return receipt_dir
    from scripts.run_dogfood_assumption_gate import main as run_dogfood

    run_dogfood()
    return receipt_dir


def test_assumption_lineage_builds_graph_from_dogfood_receipts(tmp_path):
    receipt_dir = _ensure_dogfood_receipts()
    assert receipt_dir.exists()
    payload = AssumptionLineageBuilder(receipt_dir=str(receipt_dir)).build()
    assert payload["lineage_kind"] == "semzero_assumption_lineage_lite_v1_25"
    assert payload["assumption_node_count"] >= 1
    assert payload["edge_count"] >= payload["assumption_node_count"]
    assert "temporal_bucket" in payload["family_counts"]
    assert "graph" in payload and payload["graph"]["nodes"] and payload["graph"]["edges"]


def test_assumption_lineage_writes_json_and_markdown(tmp_path):
    receipt_dir = _ensure_dogfood_receipts()
    out = tmp_path / "lineage.json"
    md = tmp_path / "lineage.md"
    builder = AssumptionLineageBuilder(receipt_dir=str(receipt_dir))
    payload = builder.save_json(out)
    builder.save_markdown(md)
    assert out.exists()
    assert md.exists()
    assert json.loads(out.read_text())["lineage_kind"] == payload["lineage_kind"]
    assert "Assumption Lineage Lite" in md.read_text()
