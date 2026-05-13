import json
from pathlib import Path

from semzero.integrations.dbt_assumption_gate import DbtAssumptionGate


def test_catalog_run_results_and_compiled_path_enrich_receipt(tmp_path: Path):
    compiled = (
        tmp_path / "target" / "compiled" / "demo" / "models" / "marts" / "incremental_events.sql"
    )
    compiled.parent.mkdir(parents=True)
    compiled.write_text(
        """
        select date(event_ts) as event_day, count(*) as events
        from {{ ref('stg_events') }}
        {% if is_incremental() %}
        where date(updated_at) >= date((select max(updated_at) from {{ this }}))
        {% endif %}
        group by 1
    """,
        encoding="utf-8",
    )
    manifest = {
        "nodes": {
            "model.demo.incremental_events": {
                "resource_type": "model",
                "name": "incremental_events",
                "original_file_path": "models/marts/incremental_events.sql",
                "compiled_path": str(compiled),
                "depends_on": {"nodes": ["source.demo.events"]},
                "config": {"materialized": "incremental"},
                "tags": ["finance"],
                "meta": {"owner": "data-platform"},
                "columns": {"event_ts": {"data_type": "timestamp"}},
            },
            "test.demo.unique_incremental_events_event_day": {
                "resource_type": "test",
                "name": "unique_incremental_events_event_day",
                "depends_on": {"nodes": ["model.demo.incremental_events"]},
                "raw_sql": "select event_day from {{ ref('incremental_events') }} group by 1 having count(*) > 1",
                "test_metadata": {"name": "unique"},
            },
        },
        "sources": {
            "source.demo.events": {
                "resource_type": "source",
                "name": "events",
                "original_file_path": "models/sources.yml",
                "depends_on": {"nodes": []},
            }
        },
        "exposures": {
            "exposure.demo.executive_revenue_dashboard": {
                "resource_type": "exposure",
                "name": "executive_revenue_dashboard",
                "depends_on": {"nodes": ["model.demo.incremental_events"]},
                "owner": {"name": "finance"},
                "maturity": "high",
                "meta": {"business_severity": "BOARD_CRITICAL"},
            }
        },
    }
    catalog = {
        "nodes": {
            "model.demo.incremental_events": {
                "columns": {"event_ts": {"type": "TIMESTAMP_NTZ", "stats": {"has_stats": True}}},
                "metadata": {"type": "BASE TABLE"},
            }
        }
    }
    run_results = {
        "results": [
            {
                "unique_id": "model.demo.incremental_events",
                "status": "success",
                "execution_time": 42.5,
                "adapter_response": {"rows_affected": 1000},
            }
        ]
    }
    manifest_path = tmp_path / "target" / "manifest.json"
    catalog_path = tmp_path / "target" / "catalog.json"
    run_results_path = tmp_path / "target" / "run_results.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
    run_results_path.write_text(json.dumps(run_results), encoding="utf-8")

    gate = DbtAssumptionGate(
        manifest_path,
        catalog_path=catalog_path,
        run_results_path=run_results_path,
        project_dir=tmp_path,
    )
    receipt = gate.run(
        ["models/marts/incremental_events.sql"],
        changed_diff="+ where date(updated_at) >= date(last_run)",
    )
    payload = receipt.to_dict()
    ctx = payload["summary"]["dbt_artifact_context"]
    assert payload["receipt_kind"] == "dbt_assumption_gate_v1_25"
    assert ctx["compiled_sql_resource_count"] >= 1
    assert ctx["catalog_enriched_resource_count"] >= 1
    assert ctx["run_results_enriched_resource_count"] >= 1
    assert ctx["test_resource_count"] >= 1
    assert ctx["exposure_resource_count"] >= 1
    assert payload["findings"]
    node_meta = payload["findings"][0]["blast_radius"][0].get("metadata", {})
    assert "runtime" in node_meta or payload["summary"]["blast_radius_resource_count"] >= 1
