import json
from pathlib import Path


def test_assumption_gate_surfaces_contract_recommendations_and_severity(tmp_path):
    from semzero.integrations.assumption_gate import AssumptionGate

    proof = tmp_path / "proof"
    proof.mkdir()
    (proof / "model.sql").write_text(
        """
        with ranked as (
          select user_id, status, event_time,
                 row_number() over (partition by user_id order by event_time desc) as rn
          from subscriptions
          where status in ('active','paused')
            and event_time >= dateadd(day, -7, current_timestamp)
        )
        select date_trunc('day', event_time) as bucket_day, count(*)
        from ranked
        where rn = 1
        group by 1
        """.strip(),
        encoding="utf-8",
    )

    drift = {
        "events": [
            {
                "node_id": "subscriptions.status",
                "before": {"name": "status"},
                "after": {"name": "status"},
            },
            {
                "node_id": "subscriptions.event_time",
                "before": {"name": "event_time"},
                "after": {"name": "event_time"},
            },
            {
                "node_id": "subscriptions.user_id",
                "before": {"name": "user_id"},
                "after": {"name": "user_id"},
            },
        ]
    }

    payload = AssumptionGate([str(proof)]).analyse({"nodes": []}, drift).to_dict()

    assert payload["risk_score"] > 0
    assert payload["severity_counts"]["high"] >= 1 or payload["severity_counts"]["critical"] >= 1
    assert payload["contract_recommendations"]
    assert any(
        item["assumption_type"] == "FRESHNESS_WINDOW_ASSUMPTION" for item in payload["findings"]
    )


def test_change_gate_hardening_uses_assumption_risk_in_execution_and_reliability(
    schema_graph, tmp_path
):
    from semzero.integrations.change_gate import ChangeGate, GateConfig

    proof = tmp_path / "proof"
    proof.mkdir()
    (proof / "subscriptions.sql").write_text(
        """
        with latest as (
          select user_id, status, event_time,
                 row_number() over (partition by user_id order by event_time desc) as rn
          from subscriptions
          where status in ('active', 'paused')
            and event_time >= dateadd(day, -14, current_timestamp)
        )
        select user_id, status, date_trunc('day', event_time) as bucket_day
        from latest
        where rn = 1
        """.strip(),
        encoding="utf-8",
    )

    drift_report = {
        "events": [
            {
                "change_type": "TYPE_CHANGED",
                "node_id": "subscriptions.event_time",
                "before": {
                    "dtype": "TIMESTAMP_TZ",
                    "nullable": False,
                    "sample_values": ["2026-04-01T00:00:00+00:00"],
                },
                "after": {
                    "dtype": "TIMESTAMP_NTZ",
                    "nullable": False,
                    "sample_values": ["2026-04-01T00:00:00"],
                },
                "detail": "timezone stripped from event time",
            },
            {
                "change_type": "STATS_DRIFTED",
                "node_id": "subscriptions.status",
                "before": {
                    "null_rate": 0.0,
                    "cardinality": 0.4,
                    "sample_values": ["active", "paused"],
                },
                "after": {
                    "null_rate": 0.0,
                    "cardinality": 0.75,
                    "sample_values": ["active", "cancelled", "trialing"],
                },
                "detail": "status values repurposed",
            },
        ]
    }
    gate = ChangeGate(
        schema_graph, GateConfig(db_url="sqlite:///moat.db", proof_source_paths=[str(proof)])
    )
    result = gate.evaluate(drift_report)

    assert result.recommended_execution["contract_updates_required"] is True
    assert result.recommended_execution["assumption_risk_score"] > 0
    assert result.recommended_execution["targeted_test_modes"]
    assert result.reliability_score < 100
    assert result.finops_summary["projected_weekly_cost_usd"] >= 0


def test_finops_gate_detects_deeper_cost_antipatterns(tmp_path):
    from semzero.integrations.finops_gate import FinOpsChangeAnalyser

    model = tmp_path / "models" / "heavy.sql"
    model.parent.mkdir(parents=True, exist_ok=True)
    model.write_text(
        """
        with a as (select * from raw_a),
             b as (select * from raw_b),
             c as (select * from raw_c),
             d as (select * from raw_d)
        select distinct regexp_replace(a.email, '@.*', '') as email_name, *
        from a
        join b on a.id = b.id
        join c on a.account_id = c.account_id
        join d on a.session_id = d.session_id
        union all
        select * from raw_archive
        union all
        select * from raw_history
        order by random()
        """.strip(),
        encoding="utf-8",
    )

    payload = FinOpsChangeAnalyser([str(tmp_path)]).analyse(["heavy.metric"]).to_dict()
    kinds = {item["kind"] for item in payload["drivers"]}

    assert "UNION_ALL_FANIN" in kinds
    assert "DEEP_CTE_STACK" in kinds
    assert "JOIN_THEN_DEDUP" in kinds
    assert payload["projected_weekly_cost_usd"] > 0


def test_validate_e2e_finance_profile_retains_hardened_gate_fields(tmp_path):
    from click.testing import CliRunner
    from semzero.cli import cli

    out = tmp_path / "finance_report.json"
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "validate-e2e",
            "--demo-pack-dir",
            str(tmp_path / "demo_finance"),
            "--demo-scale",
            "small",
            "--demo-profile",
            "finance",
            "--output",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(out.read_text(encoding="utf-8"))
    bundle = json.loads(Path(payload["bundle_path"]).read_text(encoding="utf-8"))
    gate = bundle.get("gate_result", {})
    execution = gate.get("recommended_execution", {})
    assert execution.get("contract_updates_required") in {True, False}
    assert "targeted_test_modes" in execution
    assert gate.get("assumption_summary", {}).get("risk_score", 0) >= 0
