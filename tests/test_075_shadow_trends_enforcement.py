import json
from click.testing import CliRunner


def test_shadow_dashboard_repo_team_trends_and_recommendations(tmp_path):
    from semzero.reliability.shadow_mode import ShadowDashboard

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    rows = [
        {
            "kind": "shadow_run",
            "recorded_at": "2026-04-01T00:00:00+00:00",
            "repo": "warehouse-core",
            "team": "analytics",
            "verdict": "BLOCK",
            "would_block": True,
            "would_require_review": True,
            "risk_categories": ["financial", "semantic"],
            "primary_reason": "projected_compute_blowup",
            "confidence": "high",
            "assumption_count": 3,
            "critical_assumption_count": 1,
            "estimated_savings_usd": 700,
            "projected_weekly_cost_usd": 900,
            "projected_monthly_cost_usd": 3600,
            "recurring_waste_patterns": ["JOIN_THEN_DEDUP"],
        },
        {
            "kind": "shadow_run",
            "recorded_at": "2026-04-08T00:00:00+00:00",
            "repo": "warehouse-core",
            "team": "analytics",
            "verdict": "BLOCK",
            "would_block": True,
            "would_require_review": True,
            "risk_categories": ["assumptions"],
            "primary_reason": "critical_assumption",
            "confidence": "high",
            "assumption_count": 4,
            "critical_assumption_count": 2,
            "estimated_savings_usd": 200,
            "projected_weekly_cost_usd": 200,
            "projected_monthly_cost_usd": 800,
            "recurring_waste_patterns": [],
        },
        {
            "kind": "shadow_run",
            "recorded_at": "2026-04-15T00:00:00+00:00",
            "repo": "warehouse-core",
            "team": "analytics",
            "verdict": "REQUIRE_REVIEW",
            "would_block": False,
            "would_require_review": True,
            "risk_categories": ["semantic"],
            "primary_reason": "semantic_break",
            "confidence": "medium",
            "assumption_count": 1,
            "critical_assumption_count": 0,
            "estimated_savings_usd": 0,
            "projected_weekly_cost_usd": 50,
            "projected_monthly_cost_usd": 200,
            "recurring_waste_patterns": [],
        },
    ]
    (data_dir / "shadow_runs.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )
    feedback = [
        {
            "kind": "shadow_feedback",
            "repo": "warehouse-core",
            "team": "analytics",
            "outcome": "confirmed",
        },
        {
            "kind": "shadow_feedback",
            "repo": "warehouse-core",
            "team": "analytics",
            "outcome": "useful",
        },
        {
            "kind": "shadow_feedback",
            "repo": "warehouse-core",
            "team": "analytics",
            "outcome": "fixed",
        },
    ]
    (data_dir / "shadow_feedback.jsonl").write_text(
        "\n".join(json.dumps(r) for r in feedback) + "\n", encoding="utf-8"
    )

    payload = ShadowDashboard(
        str(data_dir / "shadow_runs.jsonl"), str(data_dir / "shadow_feedback.jsonl")
    ).build()
    assert payload["repo_trends"][0]["repo"] == "warehouse-core"
    assert payload["team_trends"][0]["team"] == "analytics"
    assert payload["repo_trends"][0]["enforcement_recommendation"]["tier"] in {
        "TIER_2_REQUIRE_REVIEW",
        "TIER_3_SELECTIVE_BLOCK",
    }
    assert payload["weekly_trends"]
    assert payload["monthly_trends"][0]["period"] == "2026-04"


def test_shadow_trends_cli_and_feedback_dimensions(tmp_path):
    from semzero.cli import cli

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "shadow_runs.jsonl").write_text(
        json.dumps(
            {
                "kind": "shadow_run",
                "repo": "semantic-marts",
                "team": "data-platform",
                "verdict": "BLOCK",
                "would_block": True,
                "would_require_review": True,
                "risk_categories": ["financial"],
                "confidence": "high",
                "estimated_savings_usd": 1000,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "shadow-feedback",
            "--receipt-id",
            "r1",
            "--target",
            "pr:1",
            "--actor",
            "dev",
            "--outcome",
            "confirmed",
            "--repo",
            "semantic-marts",
            "--team",
            "data-platform",
            "--risk-category",
            "financial",
            "--data-dir",
            str(data_dir),
        ],
    )
    assert result.exit_code == 0, result.output
    result = runner.invoke(cli, ["shadow-trends", "--data-dir", str(data_dir), "--scope", "repo"])
    assert result.exit_code == 0, result.output
    assert "semantic-marts" in result.output
    assert "TIER_" in result.output
