from __future__ import annotations

import json
from dataclasses import dataclass

from click.testing import CliRunner

from semzero.cli import cli
from semzero.repo_understanding.mutation_harness import (
    RESULT_FAIL,
    RESULT_PARTIAL,
    RESULT_PASS,
    SCENARIOS,
    build_mutation_summary_markdown,
    classify_mutation_result,
    clean_mutation_context,
    compare_baseline_receipts,
)


@dataclass(frozen=True)
class _Mutation:
    target_files: tuple[str, ...]


def test_clean_mutation_context_restores_files_on_exception(tmp_path):
    repo = tmp_path / "repo"
    target = repo / "models" / "intermediate" / "int_payment_summary.sql"
    target.parent.mkdir(parents=True)
    target.write_text("select 1 as customer_id\n", encoding="utf-8")

    try:
        with clean_mutation_context(
            repo, _Mutation(("models/intermediate/int_payment_summary.sql",))
        ):
            target.write_text("select 2 as customer_id\n", encoding="utf-8")
            raise KeyboardInterrupt("simulated interrupt")
    except KeyboardInterrupt:
        pass

    assert target.read_text(encoding="utf-8") == "select 1 as customer_id\n"


def test_scenario_metadata_is_complete_and_stable():
    expected = {
        "enum_default_change",
        "grain_group_by_change",
        "join_key_change",
        "metric_formula_change",
        "filter_semantic_change",
        "column_removal_6a",
        "column_removal_6b",
        "format_only_7a",
        "format_only_7b",
        "format_only_7c",
    }

    assert set(SCENARIOS) == expected
    for scenario in SCENARIOS.values():
        assert scenario.scenario_id
        assert scenario.target_files
        assert scenario.mutation_summary
        assert scenario.expectation.expected_routing


def test_format_only_scenarios_expect_silent_output():
    for scenario_id in ("format_only_7a", "format_only_7b", "format_only_7c"):
        expectation = SCENARIOS[scenario_id].expectation
        assert expectation.expect_silent_pass is True
        assert expectation.expected_routing == ("silent_pass",)


def test_summary_classifier_pass_partial_fail_boundaries():
    enum_pass = classify_mutation_result(
        "enum_default_change",
        {
            "primary_family": "enum_domain_closure",
            "routing": "must_review",
            "priority": 52,
            "two_signal_confirmed": True,
            "must_review_families": ["enum_domain_closure"],
            "reason": "CASE ELSE enum status changed",
        },
    )
    assert enum_pass.result == RESULT_PASS

    enum_partial = classify_mutation_result(
        "enum_default_change",
        {
            "primary_family": "enum_domain_closure",
            "routing": "advisory",
            "priority": 52,
            "two_signal_confirmed": True,
            "must_review_families": [],
            "reason": "CASE ELSE enum status changed",
        },
    )
    assert enum_partial.result == RESULT_PARTIAL

    enum_fail = classify_mutation_result(
        "enum_default_change",
        {
            "primary_family": "join_cardinality",
            "routing": "must_review",
            "priority": 52,
            "two_signal_confirmed": True,
            "must_review_families": ["join_cardinality"],
            "reason": "join context only",
        },
    )
    assert enum_fail.result == RESULT_FAIL

    grain_partial = classify_mutation_result(
        "grain_group_by_change",
        {
            "primary_family": "join_cardinality",
            "routing": "must_review",
            "two_signal_confirmed": True,
            "must_review_families": ["join_cardinality"],
            "reason": "GROUP BY changed row multiplicity",
        },
    )
    assert grain_partial.result == RESULT_PARTIAL
    assert grain_partial.reason == "acceptable alternative family became primary"


def test_summary_markdown_has_routing_and_signal_columns():
    classification = classify_mutation_result(
        "format_only_7a",
        {"primary_family": None, "routing": "silent_pass", "must_review_families": []},
    )

    markdown = build_mutation_summary_markdown([classification])

    assert "Routing Match" in markdown
    assert "Signal Match" in markdown
    assert "format_only_7a" in markdown
    assert RESULT_PASS in markdown


def test_baseline_compare_tolerates_priority_within_three_points():
    baseline = {
        "receipt_kind": "dbt_assumption_gate_v1_25",
        "verdict": "REQUIRE_REVIEW",
        "findings": [{"family": "enum_domain_closure", "review_priority": {"score": 50}}],
    }
    current = {
        "receipt_kind": "dbt_assumption_gate_v1_25",
        "verdict": "REQUIRE_REVIEW",
        "findings": [{"family": "enum_domain_closure", "review_priority": {"score": 53}}],
    }

    result = compare_baseline_receipts(current, baseline, priority_tolerance=3)
    assert result["status"] == "PASS"

    current["findings"][0]["review_priority"]["score"] = 57
    result = compare_baseline_receipts(current, baseline, priority_tolerance=3)
    assert result["status"] == "FAIL"
    assert "finding[0].priority drifted" in result["mismatches"]


def test_baseline_check_command_reports_missing_manifest(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    baseline = tmp_path / "outputs" / "baseline" / "repo3_clean_baseline"
    baseline.mkdir(parents=True)
    (baseline / "receipt.json").write_text(
        json.dumps({"receipt_kind": "dbt_assumption_gate_v1_25", "findings": []}),
        encoding="utf-8",
    )
    output = tmp_path / "outputs" / "baseline" / "baseline_check_result.json"

    result = CliRunner().invoke(
        cli,
        [
            "baseline-check",
            "--repo",
            str(repo),
            "--baseline-dir",
            str(baseline),
            "--output",
            str(output),
        ],
    )

    assert result.exit_code != 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["status"] == "CONFIG_ERROR"
    assert payload["reason"] == "dbt_manifest_missing"
