from __future__ import annotations

import json
from pathlib import Path
from click.testing import CliRunner

from semzero.cli import cli
from semzero.integrations.dbt_assumption_gate import DbtAssumptionGate, render_pr_comment


def _manifest(tmp_path: Path) -> Path:
    payload = {
        "nodes": {
            "model.demo.stg_events": {
                "resource_type": "model",
                "name": "stg_events",
                "original_file_path": "models/staging/stg_events.sql",
                "raw_sql": "select convert_timezone('UTC','America/New_York', event_ts) as event_ts, user_id, status from raw.events",
                "compiled_sql": "select convert_timezone('UTC','America/New_York', event_ts) as event_ts, user_id, status from raw.events",
                "depends_on": {"nodes": []},
                "columns": {
                    "event_ts": {"data_type": "timestamp"},
                    "user_id": {"data_type": "integer"},
                },
            },
            "model.demo.finance_daily_revenue": {
                "resource_type": "model",
                "name": "finance_daily_revenue",
                "original_file_path": "models/marts/finance_daily_revenue.sql",
                "raw_sql": "select date(event_ts) as day, status, count(*) from {{ ref('stg_events') }} where status in ('paid','refunded') group by 1,2",
                "compiled_sql": "select date(event_ts) as day, status, count(*) from analytics.stg_events where status in ('paid','refunded') group by 1,2",
                "depends_on": {"nodes": ["model.demo.stg_events"]},
                "columns": {},
            },
            "model.demo.user_revenue": {
                "resource_type": "model",
                "name": "user_revenue",
                "original_file_path": "models/marts/user_revenue.sql",
                "raw_sql": "select u.user_id, sum(o.amount) from {{ ref('finance_daily_revenue') }} o join dim_users u on o.user_id = u.user_id group by 1",
                "compiled_sql": "select u.user_id, sum(o.amount) from analytics.finance_daily_revenue o join dim_users u on o.user_id = u.user_id group by 1",
                "depends_on": {"nodes": ["model.demo.finance_daily_revenue"]},
                "columns": {},
            },
            "model.demo.incremental_events": {
                "resource_type": "model",
                "name": "incremental_events",
                "original_file_path": "models/marts/incremental_events.sql",
                "raw_sql": "{{ config(materialized='incremental') }} select * from {{ ref('stg_events') }} {% if is_incremental() %} where date(updated_at) >= date((select max(updated_at) from {{ this }})) {% endif %}",
                "compiled_sql": "select * from analytics.stg_events where date(updated_at) >= date((select max(updated_at) from this))",
                "depends_on": {"nodes": ["model.demo.stg_events"]},
                "columns": {},
            },
        },
        "exposures": {
            "exposure.demo.executive_revenue_dashboard": {
                "resource_type": "exposure",
                "name": "executive_revenue_dashboard",
                "original_file_path": "models/exposures.yml",
                "depends_on": {"nodes": ["model.demo.finance_daily_revenue"]},
                "owner": {"name": "Finance"},
            }
        },
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_dbt_assumption_gate_detects_trigger_linked_hidden_assumptions(tmp_path: Path):
    manifest = _manifest(tmp_path)
    gate = DbtAssumptionGate(manifest, table_sizes={"incremental_events": {"gb": 500}})

    receipt = gate.run(["models/staging/stg_events.sql"], mode="shadow")
    payload = receipt.to_dict()

    assert payload["receipt_kind"] == "dbt_assumption_gate_v1_25"
    assert payload["domain"] == "data"
    assert payload["adapter"] == "dbt_assumption_gate"
    assert payload["verdict"] == "REQUIRE_REVIEW"
    families = {finding["family"] for finding in payload["findings"]}
    assert "temporal_bucket" in families
    assert "join_cardinality" in families
    assert "incremental_filter" in families
    assert any(
        item["node_type"] == "dbt_exposure"
        for finding in payload["findings"]
        for item in finding["blast_radius"]
    )
    assert all(finding["domain"] == "data" for finding in payload["findings"])
    assert all("risk_score" in finding for finding in payload["findings"])
    assert all("trigger_evidence" in finding for finding in payload["findings"])
    assert payload["summary"]["risk_score_total"] >= 1


def test_pr_comment_is_assumption_first(tmp_path: Path):
    manifest = _manifest(tmp_path)
    receipt = DbtAssumptionGate(manifest).run(["models/staging/stg_events.sql"], mode="shadow")
    comment = render_pr_comment(receipt)
    assert "SemZero Assumption Gate" in comment
    assert "Reviewer summary" in comment
    assert "Temporal Bucket" in comment
    assert "Blast radius" in comment


def test_cli_writes_receipt_and_comment(tmp_path: Path):
    manifest = _manifest(tmp_path)
    out = tmp_path / "receipt.json"
    comment = tmp_path / "comment.md"
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "assumption-gate",
            "--dbt-manifest",
            str(manifest),
            "--changed-file",
            "models/staging/stg_events.sql",
            "--output",
            str(out),
            "--comment-out",
            str(comment),
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["verdict"] == "REQUIRE_REVIEW"
    assert payload["findings"][0]["source"]["domain"] == "data"
    assert comment.exists()
    assert "SemZero Assumption Gate" in comment.read_text(encoding="utf-8")


def test_v1_3_emits_deep_pattern_details_from_diff(tmp_path: Path):
    manifest = _manifest(tmp_path)
    diff = """
--- a/models/marts/incremental_events.sql
+++ b/models/marts/incremental_events.sql
- where updated_at > last_run
+ where date(updated_at) >= date(last_run) or updated_at is null
"""
    receipt = DbtAssumptionGate(manifest, table_sizes={"incremental_events": {"gb": 100}}).run(
        ["models/staging/stg_events.sql"],
        mode="shadow",
        changed_diff=diff,
    )
    payload = receipt.to_dict()
    assert payload["receipt_kind"] == "dbt_assumption_gate_v1_25"
    incremental = next(f for f in payload["findings"] if f["family"] == "incremental_filter")
    assert incremental["pattern_detail"]["partition_column_wrapped"] is True
    assert incremental["pattern_detail"]["or_expansion"] is True
    assert incremental["detector_version"] == "assumption_gate_core_v1_25"
    temporal = next(f for f in payload["findings"] if f["family"] == "temporal_bucket")
    assert temporal["pattern_detail"]["pattern_type"] == "timezone_or_date_boundary_bucket"


def test_v1_13_cost_profiles_add_monthly_engine_aware_exposure(tmp_path: Path):
    manifest = _manifest(tmp_path)
    cost_profiles = {
        "models": {
            "incremental_events": {
                "engine": "snowflake",
                "table_size_gb": 500,
                "run_frequency": "daily",
                "rough_cost_per_tb_scanned_usd": 24,
            }
        }
    }
    diff = """
--- a/models/marts/incremental_events.sql
+++ b/models/marts/incremental_events.sql
- where updated_at > last_run
+ where date(updated_at) >= date(last_run) or updated_at is null
"""
    receipt = DbtAssumptionGate(manifest, cost_profiles=cost_profiles).run(
        ["models/staging/stg_events.sql"],
        mode="shadow",
        changed_diff=diff,
    )
    payload = receipt.to_dict()
    incremental = next(f for f in payload["findings"] if f["family"] == "incremental_filter")
    estimate = incremental["cost_estimate"]
    assert estimate["engine"] == "snowflake"
    assert estimate["estimated_extra_cost_per_run_usd"] is not None
    assert estimate["estimated_extra_cost_per_month_usd"] is not None
    assert "Snowflake" in estimate["engine_note"]
    assert payload["summary"]["estimated_extra_cost_per_month_usd"] is not None


def test_v1_13_materialization_cost_family_detects_full_rebuild_path(tmp_path: Path):
    manifest = _manifest(tmp_path)
    cost_profiles = {
        "models": {
            "incremental_events": {
                "engine": "databricks",
                "rough_cost_per_run_usd": 10,
                "run_frequency": "daily",
            }
        }
    }
    diff = """
--- a/models/marts/incremental_events.sql
+++ b/models/marts/incremental_events.sql
- {{ config(materialized='incremental') }}
+ {{ config(materialized='table') }}
+ -- full_refresh intentional for backfill
"""
    receipt = DbtAssumptionGate(manifest, cost_profiles=cost_profiles).run(
        ["models/marts/incremental_events.sql"],
        mode="shadow",
        changed_diff=diff,
    )
    payload = receipt.to_dict()
    materialization = next(f for f in payload["findings"] if f["family"] == "materialization_cost")
    assert (
        materialization["pattern_detail"]["pattern_type"]
        == "dbt_materialization_or_full_refresh_cost"
    )
    assert materialization["cost_estimate"]["engine"] == "databricks"
    assert "Databricks" in materialization["cost_estimate"]["engine_note"]
