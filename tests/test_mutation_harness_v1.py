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
    SMOKE_SCENARIOS,
    build_mutation_summary_markdown,
    classify_smoke_summary_values,
    classify_mutation_result,
    clean_mutation_context,
    compare_baseline_receipts,
    render_smoke_summary_markdown,
    summarize_smoke_artifacts,
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


def test_smoke_scenario_metadata_shape():
    expected = {
        "enum_default_change",
        "grain_group_by_change",
        "format_only_change",
        "column_remove_used",
        "column_remove_unused",
        "join_key_change",
        "metric_formula_change",
        "filter_semantic_change",
    }

    assert expected.issubset(SMOKE_SCENARIOS)
    assert SMOKE_SCENARIOS["format_only_change"].should_be_silent is True
    assert SMOKE_SCENARIOS["enum_default_change"].expected_semantic_events == (
        "case_else_changed",
    )
    assert "selected_column_removed" in SMOKE_SCENARIOS[
        "column_remove_used"
    ].expected_semantic_events


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


def _write_smoke_artifacts(
    root,
    *,
    changed_files=("models/intermediate/int_payment_summary.sql",),
    compile_status="COMPLETE",
    outcome="ranked",
    primary_family="enum_domain_closure",
    primary_score=0.79,
    semantic_events=("case_else_changed",),
    ranked=None,
):
    root.mkdir(parents=True, exist_ok=True)
    (root / "changed_files.debug.txt").write_text("\n".join(changed_files), encoding="utf-8")
    (root / "dbt_compile.status.json").write_text(
        json.dumps({"status": compile_status, "reason": "dbt_compile_completed"}),
        encoding="utf-8",
    )
    (root / "receipt.json").write_text(
        json.dumps({"verdict": "REQUIRE_REVIEW", "findings": []}),
        encoding="utf-8",
    )
    ranked_payload = ranked
    if ranked_payload is None:
        ranked_payload = []
        if primary_family:
            ranked_payload.append(
                {
                    "family": primary_family,
                    "role": "primary",
                    "rank_score": primary_score,
                    "supporting_event_types": list(semantic_events),
                }
            )
    summary = {
        "primary_count": sum(1 for item in ranked_payload if item.get("role") == "primary"),
        "advisory_count": sum(1 for item in ranked_payload if item.get("role") == "advisory"),
        "suppressed_count": sum(1 for item in ranked_payload if item.get("role") == "suppressed"),
    }
    (root / "shadow_hypothesis_receipt.json").write_text(
        json.dumps(
            {
                "analysis_outcome": outcome,
                "primary_family": primary_family,
                "semantic_event_types": list(semantic_events),
                "summary": summary,
                "ranked_hypotheses": ranked_payload,
            }
        ),
        encoding="utf-8",
    )
    (root / "ranking_comparison.json").write_text(
        json.dumps(
            {
                "new_must_review_count": summary["primary_count"],
                "new_advisory_count": summary["advisory_count"],
                "suppressed_count": summary["suppressed_count"],
                "semantic_events": list(semantic_events),
                "silent_pass": outcome == "silent_pass",
            }
        ),
        encoding="utf-8",
    )


def test_smoke_summary_classifies_enum_pass(tmp_path):
    _write_smoke_artifacts(tmp_path)

    summary = summarize_smoke_artifacts(tmp_path, scenario="enum_default_change")

    assert summary.result == RESULT_PASS
    assert summary.actual_primary == "enum_domain_closure"
    assert summary.actual_primary_score == 0.79
    assert summary.semantic_events == ["case_else_changed"]


def test_smoke_summary_classifies_partial_for_generic_primary(tmp_path):
    _write_smoke_artifacts(
        tmp_path,
        primary_family="join_cardinality",
        semantic_events=("case_else_changed",),
    )

    summary = summarize_smoke_artifacts(tmp_path, scenario="enum_default_change")

    assert summary.result == RESULT_PARTIAL
    assert summary.reason == "semantic event and routing matched, but primary was less precise"


def test_smoke_summary_classifies_format_only_silent_pass(tmp_path):
    _write_smoke_artifacts(
        tmp_path,
        outcome="silent_pass",
        primary_family=None,
        semantic_events=(),
        ranked=[],
    )

    summary = summarize_smoke_artifacts(tmp_path, scenario="format_only_change")

    assert summary.result == RESULT_PASS
    assert summary.silent_pass is True
    assert summary.must_review_count == 0


def test_smoke_summary_fails_format_only_must_review(tmp_path):
    _write_smoke_artifacts(
        tmp_path,
        primary_family="join_cardinality",
        semantic_events=(),
    )

    summary = summarize_smoke_artifacts(tmp_path, scenario="format_only_change")

    assert summary.result == RESULT_FAIL
    assert summary.reason == "format-only change produced must_review or forced primary"


def test_smoke_summary_column_remove_used_schema_result(tmp_path):
    _write_smoke_artifacts(
        tmp_path,
        primary_family="schema_contract_break",
        semantic_events=("selected_column_removed",),
    )

    summary = summarize_smoke_artifacts(tmp_path, scenario="column_remove_used")

    assert summary.result == RESULT_PASS
    assert summary.actual_primary == "schema_contract_break"


def test_smoke_summary_column_remove_unused_rejects_schema_must_review(tmp_path):
    _write_smoke_artifacts(
        tmp_path,
        primary_family="schema_contract_break",
        semantic_events=("selected_column_removed",),
    )

    summary = summarize_smoke_artifacts(tmp_path, scenario="column_remove_unused")

    assert summary.result == RESULT_FAIL
    assert summary.reason == "forbidden family promoted to must_review"


def test_smoke_summary_polluted_changed_file_count_is_partial(tmp_path):
    _write_smoke_artifacts(
        tmp_path,
        changed_files=(
            "models/intermediate/int_payment_summary.sql",
            ".github/workflows/semzero_repo3_smoke.yml",
        ),
        primary_family="enum_domain_closure",
        semantic_events=("case_else_changed",),
    )

    summary = summarize_smoke_artifacts(tmp_path, scenario="enum_default_change")

    assert summary.result == RESULT_PARTIAL
    assert summary.reason == "changed_file_count != 1; smoke branch scope is polluted"


def test_smoke_markdown_summary_renders_expected_rows(tmp_path):
    _write_smoke_artifacts(tmp_path)
    summary = summarize_smoke_artifacts(tmp_path, scenario="enum_default_change")

    markdown = render_smoke_summary_markdown([summary])

    assert "enum_default_change" in markdown
    assert "enum_domain_closure" in markdown
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
