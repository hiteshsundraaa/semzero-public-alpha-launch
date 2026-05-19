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
    assert "assumption may break" in comment
    assert "Review summary" in comment
    assert "Temporal Bucket" in comment
    assert "Why it matters" in comment


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

def _write_minimal_manifest(tmp_path):
    manifest = {
        "metadata": {"dbt_version": "1.0.0"},
        "nodes": {
            "model.test.orders": {
                "unique_id": "model.test.orders",
                "resource_type": "model",
                "name": "orders",
                "original_file_path": "models/orders.sql",
                "depends_on": {"nodes": []},
                "raw_code": "select 1 as order_id",
                "compiled_code": "select 1 as order_id",
                "columns": {},
                "config": {},
                "meta": {},
                "tags": [],
            }
        },
        "sources": {},
        "child_map": {},
        "parent_map": {},
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path


def test_analysis_incomplete_when_no_changed_files(tmp_path):
    manifest_path = _write_minimal_manifest(tmp_path)
    gate = DbtAssumptionGate(manifest_path)

    receipt = gate.run([])

    assert receipt.verdict == "ANALYSIS_INCOMPLETE"
    assert receipt.summary["analysis_status"]["reason"] == "changed_file_discovery_empty"

    comment = render_pr_comment(receipt)
    assert "SemZero could not prove this PR is safe" in comment
    assert "SemZero did **not** find proof that this change is safe" in comment
    assert "ALLOW" not in comment


def test_analysis_incomplete_when_dbt_file_not_mapped_to_manifest(tmp_path):
    manifest_path = _write_minimal_manifest(tmp_path)
    gate = DbtAssumptionGate(manifest_path)

    receipt = gate.run(["models/not_in_manifest.sql"])

    assert receipt.verdict == "ANALYSIS_INCOMPLETE"
    assert receipt.summary["analysis_status"]["reason"] == "dbt_changed_files_not_mapped_to_manifest"

    comment = render_pr_comment(receipt)
    assert "ANALYSIS_INCOMPLETE" in comment
    assert "models/not_in_manifest.sql" in comment


def test_assumption_ci_manifest_missing_writes_config_error(tmp_path, monkeypatch):
    from click.testing import CliRunner
    from semzero.cli import cli

    out = tmp_path / "out"
    missing_manifest = tmp_path / "missing_manifest.json"

    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("GITHUB_BASE_REF", "main")

    result = CliRunner().invoke(
        cli,
        [
            "assumption-ci",
            "--dbt-manifest",
            str(missing_manifest),
            "--changed-files",
            "models/orders.sql",
            "--base-ref",
            "origin/main",
            "--output-dir",
            str(out),
            "--no-write-github-summary",
        ],
    )

    assert result.exit_code == 0, result.output

    receipt = json.loads((out / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["verdict"] == "CONFIG_ERROR"
    assert receipt["summary"]["analysis_status"]["reason"] == "dbt_manifest_missing"

    comment = (out / "comment.md").read_text(encoding="utf-8")
    assert "CONFIG_ERROR" in comment
    assert "configuration prevented a safe review" in comment
    assert "dbt manifest was not found" in comment

def test_discovery_mode_setup_state_appears_without_optional_setup(tmp_path):
    manifest_path = _write_minimal_manifest(tmp_path)
    gate = DbtAssumptionGate(manifest_path)

    receipt = gate.run(["models/orders.sql"])

    assert receipt.summary["setup_state"]["status"] == "DISCOVERY"
    assert "suggested_next_steps" in receipt.summary["setup_state"]


def test_discovery_mode_note_appears_in_review_comment(tmp_path):
    manifest_path = _write_minimal_manifest(tmp_path)
    gate = DbtAssumptionGate(manifest_path)

    receipt = gate.run(["models/orders.sql"])
    comment = render_pr_comment(receipt)

    assert "SemZero setup note" in comment
    assert "discovery mode" in comment
    assert "inferred dbt lineage and static SQL evidence" in comment

def test_repo_snapshot_indexes_models_and_inferred_contracts(tmp_path):
    from semzero.repo_understanding.dbt_repo_snapshot import build_dbt_repo_snapshot

    manifest = {
        "metadata": {"dbt_version": "1.0.0"},
        "nodes": {
            "model.test.int_payment_summary": {
                "unique_id": "model.test.int_payment_summary",
                "resource_type": "model",
                "name": "int_payment_summary",
                "original_file_path": "models/intermediate/int_payment_summary.sql",
                "depends_on": {"nodes": []},
                "raw_code": "select customer_id, 'paid' as final_payment_status from source",
                "compiled_code": "select customer_id, 'paid' as final_payment_status from source",
                "columns": {
                    "customer_id": {},
                    "final_payment_status": {},
                },
                "config": {"materialized": "view"},
                "meta": {},
                "tags": [],
            },
            "model.test.mart_order_payments": {
                "unique_id": "model.test.mart_order_payments",
                "resource_type": "model",
                "name": "mart_order_payments",
                "original_file_path": "models/marts/mart_order_payments.sql",
                "depends_on": {"nodes": ["model.test.int_payment_summary"]},
                "raw_code": "select p.final_payment_status from int_payment_summary p",
                "compiled_code": "select p.final_payment_status from int_payment_summary p",
                "columns": {},
                "config": {"materialized": "table"},
                "meta": {},
                "tags": [],
            },
            "test.test.not_null_int_payment_summary_customer_id": {
                "unique_id": "test.test.not_null_int_payment_summary_customer_id",
                "resource_type": "test",
                "name": "not_null_int_payment_summary_customer_id",
                "depends_on": {"nodes": ["model.test.int_payment_summary"]},
                "column_name": "customer_id",
                "test_metadata": {"name": "not_null", "kwargs": {"column_name": "customer_id"}},
            },
            "test.test.unique_int_payment_summary_customer_id": {
                "unique_id": "test.test.unique_int_payment_summary_customer_id",
                "resource_type": "test",
                "name": "unique_int_payment_summary_customer_id",
                "depends_on": {"nodes": ["model.test.int_payment_summary"]},
                "column_name": "customer_id",
                "test_metadata": {"name": "unique", "kwargs": {"column_name": "customer_id"}},
            },
            "test.test.accepted_values_payment_status": {
                "unique_id": "test.test.accepted_values_payment_status",
                "resource_type": "test",
                "name": "accepted_values_payment_status",
                "depends_on": {"nodes": ["model.test.int_payment_summary"]},
                "column_name": "final_payment_status",
                "test_metadata": {
                    "name": "accepted_values",
                    "kwargs": {
                        "column_name": "final_payment_status",
                        "values": ["paid", "pending"],
                    },
                },
            },
        },
        "sources": {},
        "exposures": {},
        "metrics": {},
        "child_map": {},
        "parent_map": {},
    }

    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    snapshot = build_dbt_repo_snapshot(manifest_path, repo="test/repo", repo_root=tmp_path)

    assert snapshot["snapshot_kind"] == "semzero_repo_snapshot_v1"
    assert snapshot["summary"]["model_count"] == 2
    assert snapshot["summary"]["test_count"] == 3
    assert snapshot["summary"]["dependency_contract_count"] >= 3

    model = snapshot["models"]["model.test.int_payment_summary"]
    assert model["columns"]["customer_id"]["inferred_required"] is True
    assert model["columns"]["customer_id"]["inferred_unique"] is True
    assert model["columns"]["final_payment_status"]["accepted_values"] == ["paid", "pending"]
    assert "final_payment_status" in model["selected_columns"]
    assert {
        "column": "final_payment_status",
        "downstream_model": "model.test.mart_order_payments",
        "downstream_name": "mart_order_payments",
        "downstream_path": "models/marts/mart_order_payments.sql",
        "resource_type": "model",
        "sensitivity": "REVENUE_CRITICAL",
        "sensitivity_source": "inferred_pattern",
        "source": "downstream_sql_reference",
    } in model["downstream_column_references"]
    assert "customer_id" in model["primary_key_candidates"]
    assert "customer_id" in model["grain_candidates"]
    assert model["downstream_count"] >= 1

    mart = snapshot["models"]["model.test.mart_order_payments"]
    assert mart["sensitivity"]["label"] == "REVENUE_CRITICAL"


def test_repo_index_cli_writes_snapshot(tmp_path):
    from click.testing import CliRunner
    from semzero.cli import cli

    manifest_path = _write_minimal_manifest(tmp_path)
    output = tmp_path / "snapshot.json"

    result = CliRunner().invoke(
        cli,
        [
            "repo-index",
            "--dbt-manifest",
            str(manifest_path),
            "--output",
            str(output),
            "--repo",
            "test/repo",
            "--project-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["snapshot_kind"] == "semzero_repo_snapshot_v1"
    assert payload["summary"]["indexed_resource_count"] >= 1
