from __future__ import annotations

import json
from pathlib import Path

from semzero.repo_understanding.repo_memory import SemZeroMemoryDB


def test_memory_ingests_receipt_snapshot_and_calibration(tmp_path: Path) -> None:
    db_path = tmp_path / "semzero_memory.sqlite"
    receipt_path = tmp_path / "receipt.json"
    snapshot_path = tmp_path / "repo_snapshot.json"

    receipt = {
        "receipt_kind": "dbt_assumption_gate_v1_25",
        "mode": "shadow",
        "verdict": "REQUIRE_REVIEW",
        "generated_at": "2026-05-18T00:00:00+00:00",
        "changed_files": ["models/intermediate/int_payment_summary.sql"],
        "summary": {"finding_count": 1},
        "findings": [
            {
                "stable_id": "AG-ENUM-DOMAIN-CLOSURE-123",
                "family": "enum_domain_closure",
                "adapter": "dbt_assumption_gate",
                "source": {
                    "unique_id": "model.example.int_payment_summary",
                    "name": "int_payment_summary",
                },
                "source_path": "models/intermediate/int_payment_summary.sql",
                "blast_radius": [{"unique_id": "model.example.mart_order_payments"}],
                "business_impact": {"highest_business_severity": "REVENUE_CRITICAL"},
                "replay_fidelity": {"score": 0.58, "level": "medium_static_fidelity"},
                "causality": {"priority": 45, "routing": "must_review"},
            }
        ],
    }

    snapshot = {
        "repo": "hiteshsundraaa/dbt-example",
        "commit_sha": "abc123",
        "captured_at": "2026-05-18T00:00:00+00:00",
        "manifest_hash": "manifest-hash",
        "models": {
            "model.example.int_payment_summary": {
                "name": "int_payment_summary",
                "path": "models/intermediate/int_payment_summary.sql",
                "materialization": "view",
                "sensitivity": {"label": "REVENUE_CRITICAL", "source": "inferred_pattern"},
                "downstream_count": 1,
                "test_count": 2,
                "primary_key_candidates": ["customer_id"],
                "grain_candidates": ["customer_id"],
                "columns": {"customer_id": {"tests": ["not_null", "unique"]}},
            }
        },
        "dependency_contracts": [
            {
                "upstream_model": "model.example.int_payment_summary",
                "downstream_model": "model.example.mart_order_payments",
                "dependency_type": "grain",
                "dependent_property": "customer_id unique",
                "column": "customer_id",
                "confidence": 0.75,
                "source": "inferred_test",
            }
        ],
    }

    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")

    db = SemZeroMemoryDB(db_path)
    snapshot_result = db.ingest_snapshot(snapshot_path)
    receipt_result = db.ingest_receipt(receipt_path, repo="hiteshsundraaa/dbt-example")
    calibration = db.record_calibration(
        stable_id="AG-ENUM-DOMAIN-CLOSURE-123",
        response="agree",
        actor="reviewer@example.com",
    )
    summary = db.summary()

    assert snapshot_result["model_count"] == 1
    assert snapshot_result["dependency_contract_count"] == 1
    assert receipt_result["finding_count"] == 1
    assert receipt_result["review_required_count"] == 1
    assert calibration["response"] == "agree"
    assert summary["run_count"] == 1
    assert summary["finding_count"] == 1
    assert summary["snapshot_count"] == 1
    assert summary["model_baseline_count"] == 1
    assert summary["dependency_contract_count"] == 1
    assert summary["calibration_count"] == 1
    assert summary["family_calibration"][0]["agreed_count"] == 1


def test_memory_calibration_without_known_finding_still_records(tmp_path: Path) -> None:
    db = SemZeroMemoryDB(tmp_path / "memory.sqlite")
    row = db.record_calibration(
        stable_id="AG-UNKNOWN",
        response="false_positive",
        repo="owner/repo",
        actor="reviewer",
        reason="not relevant",
    )
    summary = db.summary()

    assert row["stable_id"] == "AG-UNKNOWN"
    assert row["finding_key"] == ""
    assert summary["calibration_count"] == 1
