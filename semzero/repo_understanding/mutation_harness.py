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
