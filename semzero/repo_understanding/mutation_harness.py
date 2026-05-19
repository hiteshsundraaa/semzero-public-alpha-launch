from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator


RESULT_PASS = "PASS"
RESULT_PARTIAL = "PARTIAL"
RESULT_FAIL = "FAIL"
RESULT_REGRESSION = "REGRESSION"


@dataclass(frozen=True, slots=True)
class MutationExpectation:
    expected_primary_families: tuple[str, ...] = ()
    acceptable_alternative_families: tuple[str, ...] = ()
    expected_routing: tuple[str, ...] = ()
    expected_two_signal_confirmed: bool | None = None
    priority_band: tuple[int, int] | None = None
    forbidden_primary_families: tuple[str, ...] = ()
    forbidden_must_review_families: tuple[str, ...] = ()
    expected_reason_terms: tuple[str, ...] = ()
    expect_silent_pass: bool = False


@dataclass(frozen=True, slots=True)
class MutationScenario:
    scenario_id: str
    description: str
    target_files: tuple[str, ...]
    mutation_summary: str
    expectation: MutationExpectation


@dataclass(frozen=True, slots=True)
class MutationClassification:
    scenario_id: str
    expected_family: str
    actual_family: str
    routing_match: str
    signal_match: str
    result: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "expected_family": self.expected_family,
            "actual_family": self.actual_family,
            "routing_match": self.routing_match,
            "signal_match": self.signal_match,
            "result": self.result,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class SmokeScenario:
    scenario: str
    expected_primary_families: tuple[str, ...] = ()
    expected_routing: str = ""
    should_be_silent: bool = False
    allowed_advisory_families: tuple[str, ...] = ()
    forbidden_must_review_families: tuple[str, ...] = ()
    expected_semantic_events: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario,
            "expected_primary_families": list(self.expected_primary_families),
            "expected_routing": self.expected_routing,
            "should_be_silent": self.should_be_silent,
            "allowed_advisory_families": list(self.allowed_advisory_families),
            "forbidden_must_review_families": list(self.forbidden_must_review_families),
            "expected_semantic_events": list(self.expected_semantic_events),
        }


@dataclass(frozen=True, slots=True)
class SmokeSummary:
    scenario: str
    expected_primary: list[str]
    expected_routing: str
    expected_silence: bool
    actual_primary: str | None
    actual_primary_score: float
    actual_routing: str
    semantic_events: list[str]
    must_review_count: int
    advisory_count: int
    suppressed_count: int
    silent_pass: bool
    compile_status: str
    changed_file_count: int
    changed_files: list[str]
    result: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario,
            "expected_primary": self.expected_primary,
            "expected_routing": self.expected_routing,
            "expected_silence": self.expected_silence,
            "actual_primary": self.actual_primary,
            "actual_primary_score": self.actual_primary_score,
            "actual_routing": self.actual_routing,
            "semantic_events": self.semantic_events,
            "must_review_count": self.must_review_count,
            "advisory_count": self.advisory_count,
            "suppressed_count": self.suppressed_count,
            "silent_pass": self.silent_pass,
            "compile_status": self.compile_status,
            "changed_file_count": self.changed_file_count,
            "changed_files": self.changed_files,
            "result": self.result,
            "reason": self.reason,
        }


SCENARIOS: dict[str, MutationScenario] = {
    "enum_default_change": MutationScenario(
        scenario_id="enum_default_change",
        description="CASE ELSE fallback value changes from pending to unresolved.",
        target_files=("models/intermediate/int_payment_summary.sql",),
        mutation_summary="ELSE 'pending' -> ELSE 'unresolved'",
        expectation=MutationExpectation(
            expected_primary_families=("enum_domain_closure",),
            expected_routing=("must_review",),
            expected_two_signal_confirmed=True,
            priority_band=(40, 60),
            forbidden_must_review_families=("join_cardinality",),
            expected_reason_terms=("enum", "case", "else", "status"),
        ),
    ),
    "grain_group_by_change": MutationScenario(
        scenario_id="grain_group_by_change",
        description="GROUP BY gains payment_status, changing model grain.",
        target_files=("models/intermediate/int_payment_summary.sql",),
        mutation_summary="GROUP BY customer_id -> GROUP BY customer_id, payment_status",
        expectation=MutationExpectation(
            expected_primary_families=("grain_contract_drift",),
            acceptable_alternative_families=("join_cardinality",),
            expected_routing=("must_review",),
            expected_two_signal_confirmed=True,
            forbidden_primary_families=("enum_domain_closure",),
            expected_reason_terms=("grain", "row", "multiplicity", "group"),
        ),
    ),
    "join_key_change": MutationScenario(
        scenario_id="join_key_change",
        description="Join predicate changes from customer_id to order_id.",
        target_files=("models/marts/mart_order_payments.sql",),
        mutation_summary="ON o.customer_id = p.customer_id -> ON o.order_id = p.customer_id",
        expectation=MutationExpectation(
            expected_primary_families=("join_relationship_drift",),
            acceptable_alternative_families=("join_cardinality",),
            expected_routing=("must_review",),
            expected_two_signal_confirmed=True,
            expected_reason_terms=("join", "predicate", "key"),
        ),
    ),
    "metric_formula_change": MutationScenario(
        scenario_id="metric_formula_change",
        description="SUM payment metric subtracts refunds without changing alias.",
        target_files=("models/intermediate/int_payment_summary.sql",),
        mutation_summary="SUM(payment_value) -> SUM(payment_value - refund_amount)",
        expectation=MutationExpectation(
            expected_primary_families=("metric_semantics_drift",),
            acceptable_alternative_families=("metric_formula_changed",),
            expected_routing=("must_review", "advisory"),
            expected_reason_terms=("aggregation", "formula", "metric"),
        ),
    ),
    "filter_semantic_change": MutationScenario(
        scenario_id="filter_semantic_change",
        description="Payment population expands from completed only to anything not failed.",
        target_files=("models/intermediate/int_payment_summary.sql",),
        mutation_summary="payment_status = 'completed' -> payment_status != 'failed'",
        expectation=MutationExpectation(
            expected_primary_families=("filter_population_drift",),
            acceptable_alternative_families=("enum_domain_closure",),
            expected_routing=("must_review", "advisory"),
            expected_reason_terms=("filter", "population", "included", "status"),
        ),
    ),
    "column_removal_6a": MutationScenario(
        scenario_id="column_removal_6a",
        description="Remove a column with confirmed downstream usage.",
        target_files=("models/intermediate/int_payment_summary.sql",),
        mutation_summary="remove downstream-referenced selected column",
        expectation=MutationExpectation(
            expected_primary_families=("schema_contract_break", "required_column_removed"),
            expected_routing=("must_review",),
            expected_two_signal_confirmed=True,
            expected_reason_terms=("column", "downstream", "schema"),
        ),
    ),
    "column_removal_6b": MutationScenario(
        scenario_id="column_removal_6b",
        description="Remove a selected column with no downstream usage.",
        target_files=("models/intermediate/int_payment_summary.sql",),
        mutation_summary="remove unused selected column",
        expectation=MutationExpectation(
            expected_routing=("advisory", "silent_pass"),
            forbidden_must_review_families=("schema_contract_break", "required_column_removed"),
        ),
    ),
    "format_only_7a": MutationScenario(
        scenario_id="format_only_7a",
        description="Add a SQL comment above SELECT.",
        target_files=("models/intermediate/int_payment_summary.sql",),
        mutation_summary="add SQL comment only",
        expectation=MutationExpectation(
            expected_routing=("silent_pass",),
            expect_silent_pass=True,
        ),
    ),
    "format_only_7b": MutationScenario(
        scenario_id="format_only_7b",
        description="Change SQL indentation only.",
        target_files=("models/intermediate/int_payment_summary.sql",),
        mutation_summary="indentation only",
        expectation=MutationExpectation(
            expected_routing=("silent_pass",),
            expect_silent_pass=True,
        ),
    ),
    "format_only_7c": MutationScenario(
        scenario_id="format_only_7c",
        description="Add blank line between CTEs.",
        target_files=("models/intermediate/int_payment_summary.sql",),
        mutation_summary="blank line only",
        expectation=MutationExpectation(
            expected_routing=("silent_pass",),
            expect_silent_pass=True,
        ),
    ),
}


SMOKE_SCENARIOS: dict[str, SmokeScenario] = {
    "enum_default_change": SmokeScenario(
        scenario="enum_default_change",
        expected_primary_families=("enum_domain_closure",),
        expected_routing="must_review",
        allowed_advisory_families=("join_cardinality",),
        expected_semantic_events=("case_else_changed",),
    ),
    "grain_group_by_change": SmokeScenario(
        scenario="grain_group_by_change",
        expected_primary_families=(
            "grain_contract_drift",
            "join_cardinality",
            "join_relationship_drift",
        ),
        expected_routing="must_review",
        allowed_advisory_families=("enum_domain_closure",),
        expected_semantic_events=("group_by_key_added", "group_by_key_removed"),
    ),
    "format_only_change": SmokeScenario(
        scenario="format_only_change",
        expected_routing="silent_pass",
        should_be_silent=True,
        forbidden_must_review_families=("*",),
    ),
    "column_remove_used": SmokeScenario(
        scenario="column_remove_used",
        expected_primary_families=("schema_contract_break", "required_column_removed"),
        expected_routing="must_review",
        expected_semantic_events=("selected_column_removed",),
    ),
    "column_remove_unused": SmokeScenario(
        scenario="column_remove_unused",
        expected_routing="advisory_or_silent",
        forbidden_must_review_families=("schema_contract_break", "required_column_removed"),
        expected_semantic_events=("selected_column_removed",),
    ),
    "join_key_change": SmokeScenario(
        scenario="join_key_change",
        expected_primary_families=("join_relationship_drift", "join_cardinality"),
        expected_routing="must_review",
        expected_semantic_events=("join_key_changed",),
    ),
    "metric_formula_change": SmokeScenario(
        scenario="metric_formula_change",
        expected_primary_families=("metric_semantics_drift", "metric_formula_changed"),
        expected_routing="must_review_or_advisory",
        expected_semantic_events=("aggregate_argument_changed", "arithmetic_expression_changed"),
    ),
    "filter_semantic_change": SmokeScenario(
        scenario="filter_semantic_change",
        expected_primary_families=("filter_population_drift", "enum_domain_closure"),
        expected_routing="must_review_or_advisory",
        expected_semantic_events=("where_predicate_changed", "status_population_changed"),
    ),
}


@contextmanager
def clean_mutation_context(
    repo_path: str | Path, mutation_fn: Any
) -> Iterator[Any]:
    root = Path(repo_path).expanduser().resolve()
    original_states: dict[Path, str | None] = {}
    try:
        for file_path in getattr(mutation_fn, "target_files", ()):
            path = Path(file_path)
            if not path.is_absolute():
                path = root / path
            original_states[path] = (
                path.read_text(encoding="utf-8") if path.exists() else None
            )
        yield mutation_fn
    finally:
        for path, content in original_states.items():
            if content is None:
                if path.exists():
                    path.unlink()
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")


def classify_mutation_result(
    scenario: MutationScenario | str,
    actual: dict[str, Any],
    *,
    previous_result: str | None = None,
) -> MutationClassification:
    scenario_obj = SCENARIOS[scenario] if isinstance(scenario, str) else scenario
    expected = scenario_obj.expectation
    actual_family = str(actual.get("primary_family") or "silent_pass")
    actual_routing = str(actual.get("routing") or "").lower() or _routing_from_actual(actual)
    must_review_families = tuple(str(x) for x in actual.get("must_review_families") or ())

    expected_family = (
        "silent_pass"
        if expected.expect_silent_pass
        else "|".join(expected.expected_primary_families or ("any",))
    )
    family_ok = (
        expected.expect_silent_pass
        or not expected.expected_primary_families
        or actual_family in expected.expected_primary_families
        or actual_family in expected.acceptable_alternative_families
    )
    family_exact = (
        expected.expect_silent_pass
        or not expected.expected_primary_families
        or actual_family in expected.expected_primary_families
    )
    family_alternative = actual_family in expected.acceptable_alternative_families
    forbidden_primary = actual_family in expected.forbidden_primary_families
    forbidden_promotion = bool(
        set(must_review_families).intersection(expected.forbidden_must_review_families)
    )
    routing_ok = actual_routing in expected.expected_routing
    signal_ok = _signal_matches(expected, actual)
    priority_ok = _priority_matches(expected, actual)
    reason_ok = _reason_matches(expected, actual)

    if previous_result == RESULT_PASS and not (
        family_ok and routing_ok and signal_ok and priority_ok and not forbidden_promotion
    ):
        result = RESULT_REGRESSION
        reason = "previously passing scenario changed outcome"
    elif expected.expect_silent_pass:
        noisy = actual_routing not in {"silent_pass", "allow", ""} or bool(
            must_review_families or actual.get("advisory_families")
        )
        result = RESULT_FAIL if noisy else RESULT_PASS
        reason = "format-only change produced finding" if noisy else "silent pass"
    elif (
        not family_ok
        or forbidden_primary
        or actual_routing == "silent_pass"
        or forbidden_promotion
    ):
        result = RESULT_FAIL
        reason = _first_reason(
            (
                (not family_ok, "primary family did not match expectation"),
                (forbidden_primary, "forbidden family became primary"),
                (actual_routing == "silent_pass", "silent pass when finding expected"),
                (forbidden_promotion, "forbidden family promoted to must_review"),
            )
        )
    elif family_exact and routing_ok and signal_ok and priority_ok and reason_ok:
        result = RESULT_PASS
        reason = "all constraints met"
    else:
        result = RESULT_PARTIAL
        reason = _partial_reason(
            routing_ok, signal_ok, priority_ok, reason_ok, family_alternative
        )

    return MutationClassification(
        scenario_id=scenario_obj.scenario_id,
        expected_family=expected_family,
        actual_family=actual_family,
        routing_match="yes" if routing_ok else "no",
        signal_match="yes" if signal_ok else "no",
        result=result,
        reason=reason,
    )


def build_mutation_summary_markdown(
    classifications: list[MutationClassification],
) -> str:
    lines = [
        "| Scenario | Expected Family | Actual Family | Routing Match | Signal Match | Result |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for item in classifications:
        lines.append(
            "| {scenario} | {expected} | {actual} | {routing} | {signal} | {result} |".format(
                scenario=item.scenario_id,
                expected=item.expected_family,
                actual=item.actual_family,
                routing=item.routing_match,
                signal=item.signal_match,
                result=item.result,
            )
        )
    return "\n".join(lines)


def summarize_smoke_artifacts(
    artifact_dir: str | Path,
    *,
    scenario: str,
    expected_changed_file_count: int = 1,
) -> SmokeSummary:
    root = Path(artifact_dir)
    smoke = SMOKE_SCENARIOS[scenario]
    changed_files = _read_lines(root / "changed_files.debug.txt")
    compile_payload = _read_json(root / "dbt_compile.status.json")
    receipt = _read_json(root / "receipt.json")
    shadow = _read_json(root / "shadow_hypothesis_receipt.json")
    comparison = _read_json(root / "ranking_comparison.json")

    ranked = list(shadow.get("ranked_hypotheses") or [])
    primary = next((item for item in ranked if item.get("role") == "primary"), None)
    actual_primary = primary.get("family") if primary else None
    actual_primary_score = round(float((primary or {}).get("rank_score") or 0.0), 4)
    semantic_events = list(
        shadow.get("semantic_event_types")
        or comparison.get("semantic_events")
        or []
    )
    silent_pass = bool(
        shadow.get("analysis_outcome") == "silent_pass"
        or comparison.get("silent_pass")
        or comparison.get("silent_pass_triggered")
    )
    must_review_count = int(
        comparison.get("new_must_review_count")
        if comparison.get("new_must_review_count") is not None
        else (shadow.get("summary") or {}).get("primary_count") or 0
    )
    advisory_count = int(
        comparison.get("new_advisory_count")
        if comparison.get("new_advisory_count") is not None
        else (shadow.get("summary") or {}).get("advisory_count") or 0
    )
    suppressed_count = int(
        comparison.get("suppressed_count")
        if comparison.get("suppressed_count") is not None
        else (shadow.get("summary") or {}).get("suppressed_count") or 0
    )
    compile_status = str(compile_payload.get("status") or "MISSING")
    actual_routing = _smoke_routing(
        compile_status=compile_status,
        receipt=receipt,
        silent_pass=silent_pass,
        must_review_count=must_review_count,
        advisory_count=advisory_count,
    )
    result, reason = classify_smoke_summary_values(
        smoke,
        actual_primary=actual_primary,
        actual_routing=actual_routing,
        semantic_events=semantic_events,
        must_review_count=must_review_count,
        advisory_count=advisory_count,
        silent_pass=silent_pass,
        compile_status=compile_status,
        changed_file_count=len(changed_files),
        expected_changed_file_count=expected_changed_file_count,
        ranked_hypotheses=ranked,
    )
    return SmokeSummary(
        scenario=scenario,
        expected_primary=list(smoke.expected_primary_families),
        expected_routing=smoke.expected_routing,
        expected_silence=smoke.should_be_silent,
        actual_primary=actual_primary,
        actual_primary_score=actual_primary_score,
        actual_routing=actual_routing,
        semantic_events=semantic_events,
        must_review_count=must_review_count,
        advisory_count=advisory_count,
        suppressed_count=suppressed_count,
        silent_pass=silent_pass,
        compile_status=compile_status,
        changed_file_count=len(changed_files),
        changed_files=changed_files,
        result=result,
        reason=reason,
    )


def classify_smoke_summary_values(
    smoke: SmokeScenario,
    *,
    actual_primary: str | None,
    actual_routing: str,
    semantic_events: list[str],
    must_review_count: int,
    advisory_count: int,
    silent_pass: bool,
    compile_status: str,
    changed_file_count: int,
    expected_changed_file_count: int = 1,
    ranked_hypotheses: list[dict[str, Any]] | None = None,
) -> tuple[str, str]:
    clean_scope = changed_file_count == expected_changed_file_count
    compile_ok = compile_status == "COMPLETE"
    compile_schema_break = actual_routing == "compile_schema_break"
    expected_events_present = all(
        event in semantic_events for event in smoke.expected_semantic_events
    )
    primary_ok = (
        not smoke.expected_primary_families
        or (actual_primary in smoke.expected_primary_families)
    )
    routing_ok = _expected_routing_matches(smoke.expected_routing, actual_routing)
    forbidden_promoted = _forbidden_must_review_present(
        smoke.forbidden_must_review_families,
        ranked_hypotheses or [],
        must_review_count=must_review_count,
    )

    if smoke.should_be_silent:
        if not clean_scope:
            return RESULT_FAIL, "changed_file_count != 1 for clean smoke branch"
        if must_review_count > 0 or actual_primary:
            return RESULT_FAIL, "format-only change produced must_review or forced primary"
        if not compile_ok:
            return RESULT_FAIL, "format-only compile did not complete"
        return RESULT_PASS, "format-only change stayed quiet"

    if not clean_scope:
        return RESULT_PARTIAL, "changed_file_count != 1; smoke branch scope is polluted"
    if forbidden_promoted:
        return RESULT_FAIL, "forbidden family promoted to must_review"
    if not compile_ok:
        if smoke.expected_primary_families and compile_schema_break:
            return RESULT_PARTIAL, "compile failed with honest schema-break classification"
        return RESULT_FAIL, "dbt compile absent or failed without honest classification"
    if smoke.expected_routing == "advisory_or_silent":
        if actual_routing in {"advisory", "silent_pass", "allow"} and must_review_count == 0:
            return RESULT_PASS, "unused/local change did not become must_review"
        return RESULT_FAIL, "unused/local change produced must_review"
    if primary_ok and routing_ok and expected_events_present:
        return RESULT_PASS, "expected primary, routing, and semantic events matched"
    if expected_events_present and routing_ok:
        return RESULT_PARTIAL, "semantic event and routing matched, but primary was less precise"
    if primary_ok and expected_events_present:
        return RESULT_PARTIAL, "expected family found but routing was weaker than expected"
    if actual_routing == "silent_pass":
        return RESULT_FAIL, "risky mutation stayed quiet"
    return RESULT_FAIL, "wrong family, routing, or semantic event"


def write_smoke_summary_artifacts(
    artifact_dir: str | Path,
    *,
    scenario: str,
    output_dir: str | Path | None = None,
    expected_changed_file_count: int = 1,
) -> dict[str, Any]:
    summary = summarize_smoke_artifacts(
        artifact_dir,
        scenario=scenario,
        expected_changed_file_count=expected_changed_file_count,
    )
    root = Path(output_dir) if output_dir else Path(artifact_dir)
    payload = {
        "kind": "semzero_smoke_summary_v1",
        "summary": summary.to_dict(),
    }
    markdown = render_smoke_summary_markdown([summary])
    _write_json(root / "smoke_summary.json", payload)
    (root / "smoke_summary.md").write_text(markdown, encoding="utf-8")
    return {"smoke_summary": payload, "markdown": markdown}


def render_smoke_summary_markdown(summaries: list[SmokeSummary]) -> str:
    lines = [
        "| Scenario | Expected Primary | Actual Primary | Routing | Events | Changed Files | Compile | Result | Reason |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in summaries:
        lines.append(
            "| {scenario} | {expected} | {actual} | {routing} | {events} | {changed} | {compile} | {result} | {reason} |".format(
                scenario=item.scenario,
                expected=", ".join(item.expected_primary) or "none",
                actual=item.actual_primary or "none",
                routing=item.actual_routing,
                events=", ".join(item.semantic_events) or "none",
                changed=item.changed_file_count,
                compile=item.compile_status,
                result=item.result,
                reason=item.reason,
            )
        )
    return "\n".join(lines) + "\n"


def normalize_receipt_for_baseline(receipt: dict[str, Any]) -> dict[str, Any]:
    normalized = copy.deepcopy(receipt)
    for key in ("generated_at", "created_at", "timestamp", "run_id", "duration_s"):
        normalized.pop(key, None)
    normalized.pop("_path", None)
    for finding in normalized.get("findings") or []:
        if isinstance(finding, dict):
            finding.pop("generated_at", None)
            finding.pop("created_at", None)
    return normalized


def compare_baseline_receipts(
    current: dict[str, Any],
    baseline: dict[str, Any],
    *,
    priority_tolerance: int = 3,
) -> dict[str, Any]:
    current_norm = normalize_receipt_for_baseline(current)
    baseline_norm = normalize_receipt_for_baseline(baseline)
    mismatches: list[str] = []

    for key in ("verdict", "receipt_kind"):
        if current_norm.get(key) != baseline_norm.get(key):
            mismatches.append(f"{key} changed")

    current_findings = list(current_norm.get("findings") or [])
    baseline_findings = list(baseline_norm.get("findings") or [])
    if len(current_findings) != len(baseline_findings):
        mismatches.append("finding_count changed")

    for index, (cur, base) in enumerate(zip(current_findings, baseline_findings)):
        cur_family = cur.get("family")
        base_family = base.get("family")
        if cur_family != base_family:
            mismatches.append(f"finding[{index}].family changed")
        cur_priority = _priority(cur)
        base_priority = _priority(base)
        if (
            cur_priority is not None
            and base_priority is not None
            and abs(cur_priority - base_priority) > priority_tolerance
        ):
            mismatches.append(f"finding[{index}].priority drifted")

    return {
        "kind": "semzero_baseline_check_result_v1",
        "status": "PASS" if not mismatches else "FAIL",
        "priority_tolerance": priority_tolerance,
        "mismatches": mismatches,
    }


def run_baseline_check(
    *,
    repo: str | Path,
    baseline_dir: str | Path,
    output: str | Path,
    priority_tolerance: int = 3,
) -> dict[str, Any]:
    repo_path = Path(repo).expanduser().resolve()
    baseline_path = Path(baseline_dir).expanduser().resolve()
    output_path = Path(output).expanduser()
    baseline_receipt_path = baseline_path / "receipt.json"
    if not baseline_receipt_path.exists():
        result = {
            "kind": "semzero_baseline_check_result_v1",
            "status": "CONFIG_ERROR",
            "reason": "baseline_receipt_missing",
            "baseline_receipt": str(baseline_receipt_path),
        }
        _write_json(output_path, result)
        return result

    manifest = repo_path / "target" / "manifest.json"
    if not manifest.exists():
        result = {
            "kind": "semzero_baseline_check_result_v1",
            "status": "CONFIG_ERROR",
            "reason": "dbt_manifest_missing",
            "required_fix": "Run dbt compile in the clean repo before baseline-check.",
            "manifest": str(manifest),
        }
        _write_json(output_path, result)
        return result

    with tempfile.TemporaryDirectory(prefix="semzero-baseline-") as tmp:
        out_dir = Path(tmp)
        cmd = [
            sys.executable,
            "-m",
            "semzero",
            "assumption-ci",
            "--dbt-manifest",
            str(manifest),
            "--output-dir",
            str(out_dir),
            "--no-write-github-summary",
        ]
        completed = subprocess.run(
            cmd,
            cwd=repo_path,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if completed.returncode != 0:
            result = {
                "kind": "semzero_baseline_check_result_v1",
                "status": "ERROR",
                "reason": "baseline_run_failed",
                "returncode": completed.returncode,
                "stdout": completed.stdout[-4000:],
                "stderr": completed.stderr[-4000:],
            }
            _write_json(output_path, result)
            return result
        current_receipt = json.loads((out_dir / "receipt.json").read_text(encoding="utf-8"))

    baseline_receipt = json.loads(baseline_receipt_path.read_text(encoding="utf-8"))
    result = compare_baseline_receipts(
        current_receipt,
        baseline_receipt,
        priority_tolerance=priority_tolerance,
    )
    result["repo"] = str(repo_path)
    result["baseline_dir"] = str(baseline_path)
    _write_json(output_path, result)
    return result


def _routing_from_actual(actual: dict[str, Any]) -> str:
    if actual.get("silent_pass") is True:
        return "silent_pass"
    if actual.get("must_review_families"):
        return "must_review"
    if actual.get("advisory_families"):
        return "advisory"
    return str(actual.get("verdict") or "").lower()


def _smoke_routing(
    *,
    compile_status: str,
    receipt: dict[str, Any],
    silent_pass: bool,
    must_review_count: int,
    advisory_count: int,
) -> str:
    verdict = str(receipt.get("verdict") or "").upper()
    analysis = (receipt.get("summary") or {}).get("analysis_status") or {}
    reason = str(analysis.get("reason") or "").lower()
    if compile_status not in {"COMPLETE", "MISSING"} and (
        "compile" in reason or "schema" in reason or verdict == "CONFIG_ERROR"
    ):
        return "compile_schema_break"
    if silent_pass:
        return "silent_pass"
    if must_review_count > 0:
        return "must_review"
    if advisory_count > 0:
        return "advisory"
    if verdict == "ALLOW":
        return "allow"
    return verdict.lower() or "unknown"


def _expected_routing_matches(expected: str, actual: str) -> bool:
    if expected == actual:
        return True
    if expected == "must_review_or_advisory":
        return actual in {"must_review", "advisory"}
    if expected == "advisory_or_silent":
        return actual in {"advisory", "silent_pass", "allow"}
    return False


def _forbidden_must_review_present(
    forbidden: tuple[str, ...],
    ranked_hypotheses: list[dict[str, Any]],
    *,
    must_review_count: int,
) -> bool:
    if not forbidden:
        return False
    if "*" in forbidden:
        return must_review_count > 0
    primary_or_secondary = {
        str(item.get("family"))
        for item in ranked_hypotheses
        if item.get("role") in {"primary", "secondary"}
    }
    return bool(primary_or_secondary.intersection(forbidden))


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _read_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _signal_matches(expected: MutationExpectation, actual: dict[str, Any]) -> bool:
    if expected.expected_two_signal_confirmed is None:
        return True
    return bool(actual.get("two_signal_confirmed")) is expected.expected_two_signal_confirmed


def _priority_matches(expected: MutationExpectation, actual: dict[str, Any]) -> bool:
    if not expected.priority_band:
        return True
    priority = actual.get("priority")
    if priority is None:
        return False
    low, high = expected.priority_band
    return low - 10 <= int(priority) <= high + 10


def _reason_matches(expected: MutationExpectation, actual: dict[str, Any]) -> bool:
    if not expected.expected_reason_terms:
        return True
    reason = " ".join(str(x) for x in actual.get("reasons") or ())
    reason = f"{reason} {actual.get('reason') or ''}".lower()
    return any(term.lower() in reason for term in expected.expected_reason_terms)


def _partial_reason(
    routing_ok: bool,
    signal_ok: bool,
    priority_ok: bool,
    reason_ok: bool,
    family_alternative: bool,
) -> str:
    if family_alternative:
        return "acceptable alternative family became primary"
    if not routing_ok:
        return "routing one level off"
    if not signal_ok:
        return "signal did not match expectation"
    if not priority_ok:
        return "priority outside tolerance"
    if not reason_ok:
        return "reason did not reference expected semantic cue"
    return "acceptable alternative family or calibrated tolerance"


def _first_reason(candidates: tuple[tuple[bool, str], ...]) -> str:
    for predicate, reason in candidates:
        if predicate:
            return reason
    return "classification failed"


def _priority(finding: dict[str, Any]) -> int | None:
    for key in ("priority", "risk_score"):
        value = finding.get(key)
        if value is not None:
            return int(value)
    priority = finding.get("review_priority")
    if isinstance(priority, dict) and priority.get("score") is not None:
        return int(priority["score"])
    return None


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
