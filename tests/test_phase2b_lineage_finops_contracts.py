from __future__ import annotations

import json
from pathlib import Path


def test_python_compiler_lineage_tracks_dataframe_derivations():
    from semzero.integrations.python_compiler_lineage import PythonCompilerLineage

    source = """
import pandas as pd
base = pd.read_sql("select user_ref, amount, status from payments", conn)
renamed = base.rename(columns={"amount": "gross_amount"})
enriched = renamed.assign(net_amount=renamed["gross_amount"], status_copy=renamed["status"])
filtered = enriched.query("status_copy == 'active'")
"""
    lineage = PythonCompilerLineage.compile(source)
    assert "gross_amount<-payments.amount" in lineage.exact_pairs
    assert "net_amount<-payments.amount" in lineage.exact_pairs
    assert "status_copy<-payments.status" in lineage.exact_pairs
    assert "status_copy" in lineage.filters


def test_python_asset_parser_emits_exact_lineage_pairs(tmp_path):
    from semzero.integrations.ast_proofing import PythonAssetParser

    path = tmp_path / "transform.py"
    path.write_text(
        "import pandas as pd\n"
        'base = pd.read_sql("select user_ref, amount from payments", conn)\n'
        'enriched = base.assign(net_amount=base["amount"])\n',
        encoding="utf-8",
    )
    parsed = PythonAssetParser(str(path)).parse(path.read_text(encoding="utf-8"))
    assert "net_amount<-payments.amount" in parsed.exact_lineage_pairs
    assert parsed.lineage_provenance.get("net_amount") in {"exact", "exact+inferred"}


def test_change_gate_finops_summary_widens_with_blast_radius(
    schema_graph, drift_safe, gate_config, tmp_path
):
    from semzero.integrations.change_gate import ChangeGate

    sql = tmp_path / "wasteful.sql"
    sql.write_text(
        "select * from orders o join payments p on o.user_ref = p.user_ref join sessions s on o.user_ref = s.user_ref",
        encoding="utf-8",
    )
    gate_config.proof_source_paths = [str(sql)]
    gate = ChangeGate(schema_graph, gate_config)
    result = gate.evaluate(
        drift_safe,
        blast_reports={
            "users.email": {
                "summary": {"total_impacted": 7, "cascade_score": 0.8},
                "impacted_nodes": [{"node_id": "mart.gmv"}],
            }
        },
    )
    finops = result.finops_summary
    assert finops["projected_weekly_cost_usd"] >= 126.0
    assert finops["recompute_radius"] >= 1
    assert finops["projected_weekly_dbu"] > 0


def test_contract_violations_include_pii_and_sla(schema_graph, gate_config):
    from semzero.integrations.change_gate import ChangeGate
    from semzero.integrations.change_gate import CompatibilityType

    gate = ChangeGate(schema_graph, gate_config)
    pii_violations = gate._contract_violations(
        "users.email", CompatibilityType.SEMANTIC_BREAKING, {}
    )
    sla_violations = gate._contract_violations(
        "orders.total", CompatibilityType.SEMANTIC_BREAKING, {}
    )
    assert any("privacy tags" in v.lower() for v in pii_violations)
    assert any("freshness sla" in v.lower() for v in sla_violations)


def test_demo_pack_chaos_labyrinth_builds_extra_workloads(tmp_path):
    from semzero.reliability.validation import build_demo_validation_pack

    pack = build_demo_validation_pack(tmp_path, scale="large", profile="chaos_labyrinth")
    workload = Path(pack.workload_path).read_text(encoding="utf-8")
    assert "sessions" in workload
    assert "DISTINCT s.session_id" in workload
    proof_root = tmp_path / "proof"
    assert (proof_root / "session_enrichment.py").exists()
    assert (proof_root / "session_rollup.sql").exists()
