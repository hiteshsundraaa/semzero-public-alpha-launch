from pathlib import Path

from click.testing import CliRunner


def test_receipt_renderers_include_root_cause_and_artifacts(tmp_path):
    from semzero.receipt_tools import ReceiptSummary, render_receipt_html, render_receipt_markdown

    summary = ReceiptSummary(
        kind="composite_receipt",
        path="data/semzero_receipt.json",
        verdict="BLOCK",
        freshness="fresh",
        age_hours=0.5,
        confidence="high",
        evidence_completeness="full",
        summary={"root_cause": "domain drift", "queries_broken": 2},
        payload={"verdict": "BLOCK"},
        artifact_paths={"gate": "data/gate_result.json"},
    )

    md = render_receipt_markdown(summary)
    html = render_receipt_html(summary)

    assert "SemZero Receipt" in md
    assert "domain drift" in md
    assert "gate_result.json" in html
    assert "BLOCK" in html


def test_fix_command_writes_guidance(tmp_path):
    from semzero.cli import cli

    receipt = tmp_path / "receipt.json"
    receipt.write_text(
        '{"verdict":"BLOCK","assessments":[],"blocked_by":["stale downstream filters"],"review_reasons":[],"evaluated_at":"2026-04-05T00:00:00+00:00","next_actions":["Update mart_revenue.sql","Rerun replay"]}'
    )
    out = tmp_path / "fix.md"
    runner = CliRunner()
    result = runner.invoke(cli, ["fix", "--receipt", str(receipt), "--output", str(out)])

    assert result.exit_code == 0
    assert out.exists()
    text = out.read_text()
    assert "Recommended next steps" in text
    assert "mart_revenue.sql" in text


def test_feedback_ledgers_record_and_summarise(tmp_path):
    from semzero.integrations.feedback_ledger import IncidentLedger, OverrideLedger

    overrides = OverrideLedger(path=str(tmp_path / "override.jsonl"))
    incidents = IncidentLedger(path=str(tmp_path / "incident.jsonl"))

    overrides.record("rz_1", "pr:184", "alice", "known hotfix", "BLOCK")
    incidents.record("inc_1", "pr:184", "high", "dashboard broken", linked_receipt_id="rz_1")

    assert overrides.summary()["entry_count"] == 1
    assert incidents.summary()["linked_receipts"] == 1


def test_row_diff_summary_surfaces_changed_columns():
    from semzero.chaos.wind_tunnel import QueryReplayer, QueryResult, WindTunnelConfig, QueryStatus

    replayer = QueryReplayer(WindTunnelConfig(db_url="sqlite://"))
    result = QueryResult(
        query_id="Q1",
        query_text="select status, amount from orders order by id",
        query_hash="abc",
        original_rows=2,
        clone_rows=2,
        original_cols=["status", "amount"],
        clone_cols=["status", "amount"],
        original_sample_rows=[
            {"status": "active", "amount": 10},
            {"status": "paused", "amount": 20},
        ],
        clone_sample_rows=[
            {"status": "archived", "amount": 10},
            {"status": "paused", "amount": 25},
        ],
    )
    result._orig_fingerprint = "aaa"
    result._clone_fingerprint = "bbb"

    out = replayer._classify(result)

    assert out.status == QueryStatus.ROW_MISMATCH
    assert set(out.row_diff_summary["changed_columns"]) == {"status", "amount"}


def test_premerge_bundle_exposes_ledger_artifacts(tmp_path, schema_graph, drift_with_remove):
    from semzero.reliability.premerge import PremergeWorkflow, PremergeWorkflowConfig

    bundle = PremergeWorkflow(
        graph_json=schema_graph,
        config=PremergeWorkflowConfig(
            db_url="", data_dir=str(tmp_path), run_wind_tunnel=False, run_chaos=False
        ),
    ).run(drift_with_remove)

    assert bundle.artifact_paths["override_ledger"].endswith("override_ledger.jsonl")
    assert bundle.artifact_paths["incident_ledger"].endswith("incident_ledger.jsonl")
