from pathlib import Path

from click.testing import CliRunner


def test_live_readiness_report_for_sqlite(db_url):
    from semzero.utils.live_readiness import build_live_readiness_report

    report = build_live_readiness_report(db_url)

    assert report.connectivity_ok is True
    assert report.dialect == "sqlite"
    assert report.clone_supported is True
    assert report.recommended_live_mode == "clone"
    assert report.table_count >= 1
    assert "semzero scan" in "\n".join(report.recommended_commands)


def test_resolve_live_mode_falls_back_when_clone_not_supported():
    from semzero.utils.live_readiness import resolve_live_mode

    dry_run, warnings = resolve_live_mode("safe", "bigquery", False)

    assert dry_run is True
    assert warnings
    assert "fell back to metadata-only" in warnings[0]


def test_doctor_command_writes_outputs(db_url, tmp_path):
    from semzero.cli import cli

    runner = CliRunner()
    output = tmp_path / "doctor.json"
    markdown = tmp_path / "doctor.md"

    result = runner.invoke(
        cli,
        [
            "doctor",
            "--db-url",
            db_url,
            "--output",
            str(output),
            "--markdown-out",
            str(markdown),
        ],
    )

    assert result.exit_code == 0
    assert output.exists()
    assert markdown.exists()
    assert "Recommended live mode" in markdown.read_text()


def test_unified_ops_report_renders_sections(tmp_path):
    from semzero.reporting.live_report import UnifiedOpsReport

    gate = {
        "verdict": "BLOCK",
        "total_blast_radius": 3,
        "total_estimated_backfill_cost_usd": 120.0,
        "assessments": [
            {
                "node_id": "orders.user_id",
                "compatibility": "DESTRUCTIVE_DELETE",
                "predicted_failure_modes": ["Join failure in downstream revenue models"],
                "recommendation": "Add a dual-write transition column before removing user_id.",
            }
        ],
    }
    wind = {
        "verdict": "BLOCKED",
        "queries_replayed": 7,
        "queries_broken": 2,
        "queries_mismatch": 1,
        "semantic_risks": [
            {"risk_type": "NULL_PROPAGATION", "description": "Order joins lost rows."}
        ],
    }
    chaos = {
        "fragility_grade": "CRITICAL",
        "summary": {
            "fragility_score": 82,
            "mutations_applied": 10,
            "mutations_that_broke": 4,
            "top_anti_patterns": ["High-null join keys", "Missing default handling"],
        },
    }

    report = UnifiedOpsReport(gate_result=gate, wind_tunnel_receipt=wind, chaos_report=chaos)
    out = tmp_path / "ops.md"
    report.save_markdown(str(out))
    rendered = out.read_text()

    assert "Change Gate" in rendered
    assert "Wind Tunnel" in rendered
    assert "Chaos" in rendered
    assert "orders.user_id" in rendered


def test_unified_ops_report_surfaces_budget_efficiency(tmp_path):
    from semzero.reporting.live_report import UnifiedOpsReport

    report = UnifiedOpsReport(
        gate_result={
            "verdict": "BLOCK",
            "reliability_score": 51,
            "oncall_risk": "MEDIUM",
            "total_blast_radius": 3,
            "total_estimated_backfill_cost_usd": 45,
            "assessments": [],
            "recommended_execution": {
                "run_wind_tunnel": True,
                "run_chaos": True,
                "wind_tunnel_query_budget": 18,
                "baseline_wind_tunnel_budget": 80,
                "chaos_mutation_budget": 6,
                "baseline_chaos_budget": 12,
                "scope_reduction_pct": 58.0,
                "estimated_compute_minutes_saved_per_run": 22.5,
                "estimated_review_minutes_saved_per_run": 14.0,
                "scope_assets": ["orders.user_id"],
            },
        },
        wind_tunnel_receipt={
            "verdict": "BLOCKED",
            "queries_replayed": 18,
            "queries_broken": 1,
            "queries_mismatch": 1,
            "replay_budget_summary": {
                "candidate_queries": 80,
                "selected_queries": 18,
                "deferred_queries": 62,
                "compute_saved_pct": 77.5,
                "focus_hit_rate": 88.9,
            },
            "compute_cost_risk": 44,
            "compute_cost_notes": ["Q1: compute-heavy pattern score 24"],
        },
        chaos_report={
            "summary": {
                "fragility_score": 70,
                "fragility_grade": "C",
                "mutations_applied": 6,
                "mutations_that_broke": 1,
            },
            "budget_summary": {
                "candidate_targets": 24,
                "selected_mutations": 6,
                "compute_saved_pct": 75.0,
            },
        },
    )
    out = tmp_path / "ops_budget.md"
    report.save_markdown(str(out))
    rendered = out.read_text()

    assert "Estimated compute saved per run" in rendered
    assert "Replay budget efficiency" in rendered
    assert "Mutation budget efficiency" in rendered
