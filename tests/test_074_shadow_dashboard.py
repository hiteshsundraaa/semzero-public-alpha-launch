from click.testing import CliRunner


def test_shadow_premerge_persists_dashboard_artifacts(tmp_path, schema_graph, drift_with_remove):
    from semzero.reliability.premerge import PremergeWorkflow, PremergeWorkflowConfig

    bundle = PremergeWorkflow(
        graph_json=schema_graph,
        config=PremergeWorkflowConfig(
            db_url="",
            data_dir=str(tmp_path),
            run_wind_tunnel=False,
            run_chaos=False,
            shadow_mode=True,
        ),
    ).run(drift_with_remove)

    assert bundle.artifact_paths["shadow_runs"].endswith("shadow_runs.jsonl")
    assert bundle.artifact_paths["shadow_dashboard"].endswith("shadow_dashboard.json")
    assert bundle.artifact_paths["shadow_dashboard_html"].endswith("shadow_dashboard.html")
    assert bundle.gate_result["shadow_summary"]["would_have_blocked"] >= 1
    assert "summary" in bundle.gate_result["shadow_summary"]


def test_shadow_dashboard_and_feedback_cli(tmp_path):
    from semzero.cli import cli
    import json

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "shadow_runs.jsonl").write_text(
        json.dumps(
            {
                "kind": "shadow_run",
                "verdict": "BLOCK",
                "would_block": True,
                "would_require_review": True,
                "risk_categories": ["financial", "semantic"],
                "primary_reason": "projected_compute_blowup",
                "confidence": "high",
                "assumption_count": 2,
                "critical_assumption_count": 1,
                "estimated_savings_usd": 250.0,
                "projected_weekly_cost_usd": 400.0,
                "projected_monthly_cost_usd": 1600.0,
                "recurring_waste_patterns": ["JOIN_THEN_DEDUP"],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    runner = CliRunner()
    out_json = data_dir / "shadow_dashboard.json"
    out_html = data_dir / "shadow_dashboard.html"
    result = runner.invoke(
        cli,
        [
            "shadow-dashboard",
            "--data-dir",
            str(data_dir),
            "--output",
            str(out_json),
            "--html-output",
            str(out_html),
        ],
    )
    assert result.exit_code == 0
    assert out_json.exists()
    assert out_html.exists()

    result = runner.invoke(
        cli,
        [
            "shadow-feedback",
            "--receipt-id",
            "rz_1",
            "--target",
            "pr:184",
            "--actor",
            "alice",
            "--outcome",
            "confirmed",
            "--note",
            "caught the exact issue",
            "--data-dir",
            str(data_dir),
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(out_json.read_text())
    assert payload["feedback_summary"]["entry_count"] == 1
    assert payload["feedback_summary"]["precision_proxy"] >= 1.0


def test_shadow_feedback_ledger_summary(tmp_path):
    from semzero.integrations.feedback_ledger import ShadowFeedbackLedger

    ledger = ShadowFeedbackLedger(path=str(tmp_path / "shadow_feedback.jsonl"))
    ledger.record("rz_1", "pr:184", "alice", "confirmed", "good catch")
    ledger.record("rz_2", "pr:185", "bob", "false_positive", "expected change")

    summary = ledger.summary()
    assert summary["entry_count"] == 2
    assert summary["by_outcome"]["confirmed"] == 1
    assert summary["precision_proxy"] == 0.5
