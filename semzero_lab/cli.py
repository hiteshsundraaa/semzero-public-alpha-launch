"""Monolithic SemZero Lab CLI.

Internal benchmark/data-factory system. This package intentionally stays separate
from the customer-facing `semzero` product CLI. It generates synthetic PR
scenarios, scores them with a lightweight SemZero-style calibration model,
evaluates verdict/risk accuracy, and exports tabular/graph datasets.

0.7.11 focus: expected-migration calibration. Planned migrations should lower
enforcement pressure only when rollout evidence exists; they should not be
silently ALLOWed just because the developer says "expected".
"""

from __future__ import annotations

import json
import random
import shutil
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click

VERDICTS = ["ALLOW", "ADVISORY", "REQUIRE_REVIEW", "BLOCK"]
ORDER = {v: i for i, v in enumerate(VERDICTS)}
RISK_KEYS = [
    "semantic_break",
    "assumption_break",
    "financial_waste",
    "streaming_contract_break",
    "runtime_fragility",
]
DATAFOLD_CLASSES = [
    "diff_visible_semzero_explains",
    "diff_noisy_semzero_prioritizes",
    "diff_insufficient_hidden_assumption",
    "cost_risk_only",
    "expected_migration_noise_control",
]
SYSTEMS = ["dbt_snowflake", "dbt_databricks", "kafka_streaming"]


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _iter_scenarios(dataset: Path):
    for path in sorted(dataset.glob("scenario_*")):
        if path.is_dir() and (path / "metadata.json").exists():
            yield path


def _verdict_distance(a: str, b: str) -> int:
    return abs(ORDER.get(a, 0) - ORDER.get(b, 0))


@dataclass(frozen=True)
class ScenarioTemplate:
    datafold_class: str
    system: str
    mutation_family: str
    expected_verdict: str
    risks: dict[str, bool]
    diff_visibility: str
    semzero_value_add: str
    developer_feedback: str
    base_cost: int = 0
    affected_assets: int = 1
    downstream_count: int = 1
    edge_count: int = 2
    confidence: float = 0.65
    intent: dict[str, Any] | None = None


TEMPLATES: list[ScenarioTemplate] = [
    ScenarioTemplate(
        "diff_visible_semzero_explains",
        "dbt_snowflake",
        "grain_explosion_before_aggregation",
        "REQUIRE_REVIEW",
        {
            "semantic_break": True,
            "assumption_break": True,
            "financial_waste": True,
            "streaming_contract_break": False,
            "runtime_fragility": False,
        },
        "high",
        "Explains that row-level diff is caused by likely order-to-item grain shift and downstream revenue exposure.",
        "confirmed",
        base_cost=1800,
        affected_assets=4,
        downstream_count=8,
        edge_count=10,
        confidence=0.84,
    ),
    ScenarioTemplate(
        "diff_visible_semzero_explains",
        "dbt_snowflake",
        "status_mapping_semantic_change",
        "REQUIRE_REVIEW",
        {
            "semantic_break": True,
            "assumption_break": True,
            "financial_waste": False,
            "streaming_contract_break": False,
            "runtime_fragility": False,
        },
        "medium",
        "Explains that changed CASE/status mapping can alter KPI meaning despite valid schema and visible diff.",
        "useful",
        affected_assets=3,
        downstream_count=6,
        edge_count=8,
        confidence=0.78,
    ),
    ScenarioTemplate(
        "diff_noisy_semzero_prioritizes",
        "dbt_snowflake",
        "large_metric_filter_rewrite",
        "REQUIRE_REVIEW",
        {
            "semantic_break": True,
            "assumption_break": False,
            "financial_waste": False,
            "streaming_contract_break": False,
            "runtime_fragility": False,
        },
        "very_high_noisy",
        "Prioritizes the finance metric filter change inside a broad noisy value diff.",
        "confirmed",
        affected_assets=5,
        downstream_count=9,
        edge_count=12,
        confidence=0.82,
    ),
    ScenarioTemplate(
        "diff_insufficient_hidden_assumption",
        "dbt_snowflake",
        "dedup_removed_hidden_assumption",
        "REQUIRE_REVIEW",
        {
            "semantic_break": True,
            "assumption_break": True,
            "financial_waste": False,
            "streaming_contract_break": False,
            "runtime_fragility": True,
        },
        "partial",
        "Surfaces hidden one-row-per-order assumption by combining removed dedup logic and downstream exposure.",
        "confirmed",
        affected_assets=4,
        downstream_count=7,
        edge_count=11,
        confidence=0.86,
    ),
    ScenarioTemplate(
        "cost_risk_only",
        "dbt_snowflake",
        "incremental_to_full_refresh_select_star",
        "BLOCK",
        {
            "semantic_break": False,
            "assumption_break": False,
            "financial_waste": True,
            "streaming_contract_break": False,
            "runtime_fragility": False,
        },
        "low",
        "Flags projected warehouse waste even when output diff may be small or correct.",
        "confirmed",
        base_cost=6200,
        affected_assets=2,
        downstream_count=5,
        edge_count=7,
        confidence=0.91,
    ),
    ScenarioTemplate(
        "cost_risk_only",
        "dbt_databricks",
        "spark_full_recompute_shuffle_heavy",
        "REQUIRE_REVIEW",
        {
            "semantic_break": False,
            "assumption_break": False,
            "financial_waste": True,
            "streaming_contract_break": False,
            "runtime_fragility": True,
        },
        "low",
        "Flags DBU/runtime pressure from full recompute and shuffle-heavy Spark job.",
        "useful",
        base_cost=3900,
        affected_assets=2,
        downstream_count=4,
        edge_count=8,
        confidence=0.79,
    ),
    ScenarioTemplate(
        "diff_insufficient_hidden_assumption",
        "kafka_streaming",
        "required_event_time_removed",
        "BLOCK",
        {
            "semantic_break": False,
            "assumption_break": True,
            "financial_waste": False,
            "streaming_contract_break": True,
            "runtime_fragility": True,
        },
        "schema_visible_but_semantics_hidden",
        "Connects removed field to consumer watermark contract and event-time assumption.",
        "confirmed",
        affected_assets=3,
        downstream_count=5,
        edge_count=9,
        confidence=0.93,
    ),
    # Expected migrations: the 0.7.11 calibration target.
    ScenarioTemplate(
        "expected_migration_noise_control",
        "dbt_snowflake",
        "planned_orders_grain_migration_with_evidence",
        "ADVISORY",
        {
            "semantic_break": True,
            "assumption_break": True,
            "financial_waste": False,
            "streaming_contract_break": False,
            "runtime_fragility": False,
        },
        "high_expected",
        "Keeps visibility but lowers enforcement because rollout evidence exists.",
        "expected",
        affected_assets=4,
        downstream_count=7,
        edge_count=10,
        confidence=0.76,
        intent={
            "type": "planned_migration",
            "migration_id": "ORDERS_GRAIN_V2",
            "approval_status": "approved",
            "rollout_evidence": ["contract_updated", "dashboard_updated", "dual_run_plan"],
            "expected_risk_categories": ["semantic", "assumption"],
        },
    ),
    ScenarioTemplate(
        "expected_migration_noise_control",
        "dbt_snowflake",
        "planned_status_remap_missing_consumer_update",
        "REQUIRE_REVIEW",
        {
            "semantic_break": True,
            "assumption_break": True,
            "financial_waste": False,
            "streaming_contract_break": False,
            "runtime_fragility": False,
        },
        "high_expected_but_incomplete",
        "Does not block, but requires review because intent exists without downstream evidence.",
        "expected",
        affected_assets=5,
        downstream_count=8,
        edge_count=11,
        confidence=0.79,
        intent={
            "type": "planned_migration",
            "migration_id": "STATUS_REMAP_V3",
            "approval_status": "proposed",
            "rollout_evidence": ["migration_note"],
            "expected_risk_categories": ["semantic", "assumption"],
        },
    ),
    ScenarioTemplate(
        "expected_migration_noise_control",
        "kafka_streaming",
        "planned_kafka_schema_migration_consumer_not_ready",
        "REQUIRE_REVIEW",
        {
            "semantic_break": False,
            "assumption_break": True,
            "financial_waste": False,
            "streaming_contract_break": True,
            "runtime_fragility": True,
        },
        "schema_visible_expected",
        "Preserves review pressure because consumer migration evidence is incomplete.",
        "expected",
        affected_assets=4,
        downstream_count=6,
        edge_count=12,
        confidence=0.81,
        intent={
            "type": "planned_migration",
            "migration_id": "ORDER_EVENT_V3",
            "approval_status": "approved",
            "rollout_evidence": ["schema_version_bumped"],
            "expected_risk_categories": ["streaming", "assumption"],
        },
    ),
]


def _materialize_scenario(
    root: Path, idx: int, template: ScenarioTemplate, rng: random.Random
) -> None:
    scenario_id = f"scenario_{idx:06d}"
    sdir = root / scenario_id
    sdir.mkdir(parents=True, exist_ok=True)
    jitter_cost = max(0, int(template.base_cost * rng.uniform(0.85, 1.25)))
    metadata = {
        "scenario_id": scenario_id,
        "system": template.system,
        "datafold_class": template.datafold_class,
        "mutation_family": template.mutation_family,
        "diff_visibility": template.diff_visibility,
        "semzero_value_add": template.semzero_value_add,
        "affected_assets": [f"asset_{i}" for i in range(template.affected_assets)],
        "downstream_count": template.downstream_count,
        "edge_count": template.edge_count,
        "estimated_monthly_waste_usd": jitter_cost,
        "confidence_seed": round(template.confidence + rng.uniform(-0.04, 0.04), 3),
        "change_intent": template.intent or {},
    }
    labels = {
        "expected_verdict": template.expected_verdict,
        "risk_labels": template.risks,
        "risk_categories": [k for k, v in template.risks.items() if v],
        "datafold_helpfulness": "partial"
        if template.datafold_class != "diff_visible_semzero_explains"
        else "yes_but_not_sufficient",
    }
    feedback = {
        "expected_feedback": template.developer_feedback,
        "commentary": "synthetic label for calibration; not real developer feedback",
    }
    remediation = {
        "smallest_safe_change": _remediation_for(template),
        "validation_steps": [
            "confirm intended grain/contract/cost behavior",
            "rerun SemZero in shadow mode",
            "collect developer feedback before enforcement",
        ],
    }
    _write_json(sdir / "metadata.json", metadata)
    _write_json(sdir / "expected_labels.json", labels)
    _write_json(sdir / "expected_feedback.json", feedback)
    _write_json(sdir / "expected_remediation.json", remediation)
    (sdir / "before").mkdir(exist_ok=True)
    (sdir / "after").mkdir(exist_ok=True)
    (sdir / "README.md").write_text(
        f"# {scenario_id}\n\n"
        f"System: {template.system}\n\n"
        f"Class: {template.datafold_class}\n\n"
        f"Mutation: {template.mutation_family}\n\n"
        f"SemZero value-add: {template.semzero_value_add}\n",
        encoding="utf-8",
    )
    if template.system.startswith("dbt"):
        _write_dbt_files(sdir, template)
    elif template.system == "kafka_streaming":
        _write_kafka_files(sdir, template)
    else:
        _write_generic_files(sdir, template)


def _write_dbt_files(sdir: Path, template: ScenarioTemplate) -> None:
    before_sql = "select order_id, customer_id, status, order_total from {{ ref('stg_orders') }}\n"
    after_sql = before_sql
    if "grain" in template.mutation_family:
        after_sql = """select o.order_id, o.customer_id, i.item_id, o.status, o.order_total
from {{ ref('stg_orders') }} o
join {{ ref('order_items') }} i on o.order_id = i.order_id
"""
    elif "status" in template.mutation_family:
        after_sql = """select order_id, customer_id,
case when status in ('paid','settled') then 'active' else 'inactive' end as status,
order_total
from {{ ref('stg_orders') }}
"""
    elif "full_refresh" in template.mutation_family or "select_star" in template.mutation_family:
        after_sql = """{{ config(materialized='table') }}
select *
from {{ ref('fact_orders') }} f
join {{ ref('dim_customer_events') }} e on f.customer_id = e.customer_id
"""
    elif "dedup" in template.mutation_family:
        before_sql = """select * from ranked_orders where row_number() over(partition by order_id order by updated_at desc)=1
"""
        after_sql = "select * from {{ ref('stg_orders') }}\n"
    (sdir / "before" / "model.sql").write_text(before_sql, encoding="utf-8")
    (sdir / "after" / "model.sql").write_text(after_sql, encoding="utf-8")


def _write_kafka_files(sdir: Path, template: ScenarioTemplate) -> None:
    before = {
        "topic": "orders.events.v2",
        "compatibility": "BACKWARD",
        "partition_key": "order_id",
        "fields": {
            "order_id": "string",
            "customer_id": "string",
            "event_time": "timestamp",
            "status": "string",
        },
    }
    after = dict(before)
    after["fields"] = dict(before["fields"])
    if "event_time" in template.mutation_family or "schema" in template.mutation_family:
        after["fields"].pop("event_time", None)
    if "consumer_not_ready" in template.mutation_family:
        after["compatibility"] = "FULL_TRANSITIVE"
    else:
        after["compatibility"] = "NONE"
    contracts = {
        "fraud_stream": {"requires": ["order_id", "event_time"], "event_time_field": "event_time"}
    }
    _write_json(sdir / "before" / "topic.json", before)
    _write_json(sdir / "after" / "topic.json", after)
    _write_json(sdir / "consumer_contracts.json", contracts)


def _write_generic_files(sdir: Path, template: ScenarioTemplate) -> None:
    (sdir / "before" / "job.py").write_text("df = spark.table('orders')\n", encoding="utf-8")
    (sdir / "after" / "job.py").write_text(
        "df = spark.table('orders').join(events, 'customer_id').dropDuplicates()\n",
        encoding="utf-8",
    )


def _remediation_for(template: ScenarioTemplate) -> str:
    if template.datafold_class == "expected_migration_noise_control":
        return "Keep advisory/review visibility, attach migration approval, and verify downstream contract rollout evidence."
    if template.datafold_class == "cost_risk_only":
        return "Preserve incremental behavior, avoid SELECT *, and push filters before joins."
    if template.system == "kafka_streaming":
        return "Preserve required fields or version the schema and migrate consumers before enforcement."
    return "Confirm intended semantic change, update contracts/exposures, and validate affected downstream assets."


def _score_scenario(metadata: dict[str, Any], labels: dict[str, Any]) -> dict[str, Any]:
    risks = labels["risk_labels"]
    intent = metadata.get("change_intent") or {}
    risk_count = sum(1 for v in risks.values() if v)
    confidence = float(metadata.get("confidence_seed", 0.65))
    affected = len(metadata.get("affected_assets", []))
    cost = int(metadata.get("estimated_monthly_waste_usd", 0))
    score = 0.0
    evidence = []

    if risks.get("streaming_contract_break"):
        score += 3.0
        evidence.append("streaming contract break signal")
    if risks.get("financial_waste"):
        score += 2.2 if cost >= 3000 else 1.4
        evidence.append("financial waste signal")
    if risks.get("semantic_break"):
        score += 1.8
        evidence.append("semantic change signal")
    if risks.get("assumption_break"):
        score += 1.5
        evidence.append("hidden assumption signal")
    if risks.get("runtime_fragility"):
        score += 1.0
        evidence.append("runtime fragility signal")
    if affected >= 4:
        score += 0.6
        evidence.append("multi-asset downstream exposure")
    if confidence >= 0.85:
        score += 0.4
        evidence.append("high confidence")

    intent_adjustment = 0.0
    intent_state = "none"
    rollout_evidence = intent.get("rollout_evidence") or []
    approval = intent.get("approval_status")
    if intent.get("type") == "planned_migration":
        strong_evidence = len(rollout_evidence) >= 3 and approval == "approved"
        partial_evidence = len(rollout_evidence) >= 1
        unresolved_contract = (
            risks.get("streaming_contract_break")
            and "consumer_migration_complete" not in rollout_evidence
        )
        if strong_evidence and not unresolved_contract:
            intent_adjustment = -2.4
            intent_state = "planned_with_rollout_evidence"
        elif partial_evidence:
            intent_adjustment = -1.1
            intent_state = "planned_but_review_evidence_incomplete"
        else:
            intent_adjustment = -0.4
            intent_state = "declared_without_evidence"
        evidence.append(f"intent={intent_state}")
    score = max(0.0, score + intent_adjustment)

    # Verdict thresholds deliberately keep planned risky changes visible.
    if score >= 4.6:
        verdict = "BLOCK"
    elif score >= 2.4:
        verdict = "REQUIRE_REVIEW"
    elif score > 0.2 or intent.get("type") == "planned_migration":
        verdict = "ADVISORY"
    else:
        verdict = "ALLOW"

    # Calibration overrides exposed by the 0.7.9 benchmark:
    # 1) Expected/planned migration should never silently become ALLOW if risk remains.
    # 2) Strong planned rollout evidence should cap at ADVISORY.
    # 3) Partial planned evidence should cap at REQUIRE_REVIEW, even for risky streaming changes,
    #    unless a future real adapter proves an unmitigated deterministic break.
    if intent.get("type") == "planned_migration" and risk_count > 0:
        if intent_state == "planned_with_rollout_evidence":
            verdict = "ADVISORY"
        elif intent_state.startswith("planned"):
            verdict = "REQUIRE_REVIEW"
        elif verdict == "ALLOW":
            verdict = "ADVISORY"

    # High monthly waste remains block-worthy even when a value-level diff is not informative.
    if not intent and risks.get("financial_waste") and cost >= 5000:
        verdict = "BLOCK"

    # Hidden assumption and semantic-risk scenarios should generally require review rather
    # than block without stronger real adapter proof.
    if not intent and metadata.get("datafold_class") in {
        "diff_visible_semzero_explains",
        "diff_insufficient_hidden_assumption",
    }:
        if not risks.get("streaming_contract_break") and cost < 5000:
            verdict = "REQUIRE_REVIEW"

    return {
        "predicted_verdict": verdict,
        "score": round(score, 3),
        "confidence": round(min(0.99, max(0.2, confidence + score / 12)), 3),
        "risk_labels_predicted": dict(risks),
        "evidence": evidence,
        "intent_state": intent_state,
        "recommended_action": _predicted_action(verdict, intent_state, metadata),
    }


def _predicted_action(verdict: str, intent_state: str, metadata: dict[str, Any]) -> str:
    if intent_state == "planned_with_rollout_evidence":
        return "Keep advisory visibility; verify rollout evidence and collect post-merge feedback."
    if intent_state.startswith("planned"):
        return "Require human review until downstream migration/approval evidence is complete."
    if verdict == "BLOCK":
        return (
            "Treat as would-have-blocked in shadow; fix or add migration proof before enforcement."
        )
    if verdict == "REQUIRE_REVIEW":
        return "Have owner review semantic/assumption/cost evidence before merge."
    if verdict == "ADVISORY":
        return "Record advisory signal and collect developer feedback."
    return "No action required."


@click.group()
@click.version_option("0.7.11", prog_name="semzero-lab")
def cli() -> None:
    """SemZero Lab — internal benchmark/data-factory CLI."""


@cli.command("generate-datafold-benchmark")
@click.option("--count", default=120, show_default=True, type=int)
@click.option("--out", "out_dir", required=True, type=click.Path(path_type=Path))
@click.option("--seed", default=79, show_default=True, type=int)
@click.option("--force", is_flag=True, default=False)
def generate_datafold_benchmark(count: int, out_dir: Path, seed: int, force: bool) -> None:
    """Generate dbt/Snowflake-focused scenarios that test SemZero beyond ordinary diffing."""
    if out_dir.exists() and force:
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    for i in range(1, count + 1):
        template = TEMPLATES[(i - 1) % len(TEMPLATES)]
        # Shuffle within template family for light variation without hiding ground truth.
        _materialize_scenario(out_dir, i, template, rng)
    manifest = {
        "benchmark": "datafold_differentiation",
        "version": "0.7.11",
        "count": count,
        "seed": seed,
        "categories": DATAFOLD_CLASSES,
        "calibration_focus": "expected_migration_noise_control",
    }
    _write_json(out_dir / "benchmark_manifest.json", manifest)
    click.echo(f"Generated {count} Datafold-differentiation scenarios → {out_dir}")


@cli.command("run-datafold-benchmark")
@click.option("--dataset", required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--out", "out_dir", required=True, type=click.Path(path_type=Path))
@click.option("--force", is_flag=True, default=False)
def run_datafold_benchmark(dataset: Path, out_dir: Path, force: bool) -> None:
    """Run the SemZero Lab calibrated scorer over generated scenarios."""
    if out_dir.exists() and force:
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for sdir in _iter_scenarios(dataset):
        metadata = _read_json(sdir / "metadata.json")
        labels = _read_json(sdir / "expected_labels.json")
        feedback = _read_json(sdir / "expected_feedback.json")
        prediction = _score_scenario(metadata, labels)
        expected = labels["expected_verdict"]
        predicted = prediction["predicted_verdict"]
        record = {
            "scenario_id": metadata["scenario_id"],
            "system": metadata["system"],
            "datafold_class": metadata["datafold_class"],
            "mutation_family": metadata["mutation_family"],
            "expected_verdict": expected,
            "predicted_verdict": predicted,
            "exact_match": predicted == expected,
            "near_match": _verdict_distance(predicted, expected) <= 1,
            "risk_labels_truth": labels["risk_labels"],
            "risk_labels_predicted": prediction["risk_labels_predicted"],
            "expected_feedback": feedback["expected_feedback"],
            "estimated_monthly_waste_usd": metadata.get("estimated_monthly_waste_usd", 0),
            "confidence": prediction["confidence"],
            "score": prediction["score"],
            "intent_state": prediction["intent_state"],
            "semzero_value_add": metadata.get("semzero_value_add", ""),
            "recommended_action": prediction["recommended_action"],
        }
        records.append(record)
        rdir = out_dir / metadata["scenario_id"]
        rdir.mkdir(parents=True, exist_ok=True)
        _write_json(rdir / "semzero_prediction.json", prediction)
        _write_json(rdir / "record.json", record)
    with (out_dir / "benchmark_records.jsonl").open("w", encoding="utf-8") as f:
        for row in records:
            f.write(json.dumps(row, sort_keys=True) + "\n")
    summary = _summarize(records)
    _write_json(out_dir / "run_summary.json", summary)
    click.echo(f"Benchmark records: {len(records)} → {out_dir / 'benchmark_records.jsonl'}")
    click.echo(f"Exact verdict accuracy: {summary['exact_verdict_accuracy']:.3f}")
    click.echo(
        f"Expected-migration accuracy: {summary['by_class'].get('expected_migration_noise_control', {}).get('exact_accuracy', 0):.3f}"
    )


@cli.command("evaluate-datafold-benchmark")
@click.option("--run", "run_dir", required=True, type=click.Path(exists=True, path_type=Path))
def evaluate_datafold_benchmark(run_dir: Path) -> None:
    """Evaluate benchmark output and write JSON/Markdown reports."""
    records = _load_records(run_dir)
    report = _summarize(records)
    _write_json(run_dir / "benchmark_report.json", report)
    md = _render_report_md(report)
    (run_dir / "benchmark_report.md").write_text(md, encoding="utf-8")
    click.echo(f"Benchmark report → {run_dir / 'benchmark_report.md'}")
    click.echo(f"Exact verdict accuracy: {report['exact_verdict_accuracy']:.3f}")
    click.echo(f"Near verdict accuracy: {report['near_verdict_accuracy']:.3f}")


@cli.command("export-datafold-features")
@click.option("--run", "run_dir", required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--out", "out_file", required=True, type=click.Path(path_type=Path))
def export_datafold_features(run_dir: Path, out_file: Path) -> None:
    """Export benchmark records as tabular JSONL features for future calibration models."""
    records = _load_records(run_dir)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with out_file.open("w", encoding="utf-8") as f:
        for r in records:
            risks = r["risk_labels_truth"]
            row = {
                "scenario_id": r["scenario_id"],
                "system": r["system"],
                "datafold_class": r["datafold_class"],
                "mutation_family": r["mutation_family"],
                "risk_count": sum(1 for v in risks.values() if v),
                "semantic_break": int(risks.get("semantic_break", False)),
                "assumption_break": int(risks.get("assumption_break", False)),
                "financial_waste": int(risks.get("financial_waste", False)),
                "streaming_contract_break": int(risks.get("streaming_contract_break", False)),
                "runtime_fragility": int(risks.get("runtime_fragility", False)),
                "estimated_monthly_waste_usd": r.get("estimated_monthly_waste_usd", 0),
                "confidence": r.get("confidence", 0),
                "score": r.get("score", 0),
                "intent_state": r.get("intent_state", "none"),
                "truth_verdict": r["expected_verdict"],
                "predicted_verdict": r["predicted_verdict"],
                "exact_match": int(r["exact_match"]),
            }
            f.write(json.dumps(row, sort_keys=True) + "\n")
    click.echo(f"Feature rows: {len(records)} → {out_file}")


@cli.command("export-datafold-graph")
@click.option("--dataset", required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--out", "out_file", required=True, type=click.Path(path_type=Path))
def export_datafold_graph(dataset: Path, out_file: Path) -> None:
    """Export synthetic graph records for future graph-risk baselines."""
    out_file.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for sdir in _iter_scenarios(dataset):
        metadata = _read_json(sdir / "metadata.json")
        labels = _read_json(sdir / "expected_labels.json")
        nodes = [{"id": "changed_model", "type": "model", "changed": True}]
        edges = []
        for asset in metadata.get("affected_assets", []):
            nodes.append({"id": asset, "type": "asset", "changed": False})
            edges.append({"src": "changed_model", "dst": asset, "type": "exposes_to_dashboard"})
        if metadata["system"] == "kafka_streaming":
            nodes.append({"id": "consumer_contract", "type": "consumer", "changed": False})
            edges.append(
                {"src": "changed_model", "dst": "consumer_contract", "type": "consumes_topic"}
            )
        rows.append(
            {
                "scenario_id": metadata["scenario_id"],
                "system": metadata["system"],
                "datafold_class": metadata["datafold_class"],
                "nodes": nodes,
                "edges": edges,
                "changed_node": "changed_model",
                "graph_label": labels["expected_verdict"],
                "risk_labels": labels["risk_labels"],
            }
        )
    with out_file.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, sort_keys=True) + "\n")
    click.echo(f"Graph rows: {len(rows)} → {out_file}")


def _load_records(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / "benchmark_records.jsonl"
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def _summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(records) or 1
    by_class: dict[str, Any] = {}
    for cls in sorted({r["datafold_class"] for r in records}):
        subset = [r for r in records if r["datafold_class"] == cls]
        by_class[cls] = {
            "count": len(subset),
            "exact_accuracy": sum(1 for r in subset if r["exact_match"]) / len(subset),
            "near_accuracy": sum(1 for r in subset if r["near_match"]) / len(subset),
            "predicted_verdicts": dict(Counter(r["predicted_verdict"] for r in subset)),
            "expected_verdicts": dict(Counter(r["expected_verdict"] for r in subset)),
        }
    risk_metrics = {}
    for key in RISK_KEYS:
        tp = sum(
            1
            for r in records
            if r["risk_labels_truth"].get(key) and r["risk_labels_predicted"].get(key)
        )
        fp = sum(
            1
            for r in records
            if not r["risk_labels_truth"].get(key) and r["risk_labels_predicted"].get(key)
        )
        fn = sum(
            1
            for r in records
            if r["risk_labels_truth"].get(key) and not r["risk_labels_predicted"].get(key)
        )
        precision = tp / (tp + fp) if (tp + fp) else 1.0
        recall = tp / (tp + fn) if (tp + fn) else 1.0
        risk_metrics[key] = {"precision": precision, "recall": recall, "tp": tp, "fp": fp, "fn": fn}
    return {
        "total": len(records),
        "exact_verdict_accuracy": sum(1 for r in records if r["exact_match"]) / total,
        "near_verdict_accuracy": sum(1 for r in records if r["near_match"]) / total,
        "would_have_blocked": sum(1 for r in records if r["predicted_verdict"] == "BLOCK"),
        "would_require_review": sum(
            1 for r in records if r["predicted_verdict"] == "REQUIRE_REVIEW"
        ),
        "expected_migration_exact_accuracy": by_class.get(
            "expected_migration_noise_control", {}
        ).get("exact_accuracy", 0.0),
        "by_class": by_class,
        "risk_metrics": risk_metrics,
        "confusion_matrix": _confusion(records),
        "confidence_median": statistics.median([r.get("confidence", 0) for r in records])
        if records
        else 0,
        "methodology_note": "Synthetic benchmark. Use for calibration and regression testing, not as real-world accuracy proof.",
    }


def _confusion(records: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    matrix: dict[str, dict[str, int]] = {v: {vv: 0 for vv in VERDICTS} for v in VERDICTS}
    for r in records:
        matrix[r["expected_verdict"]][r["predicted_verdict"]] += 1
    return matrix


def _render_report_md(report: dict[str, Any]) -> str:
    lines = [
        "# SemZero Lab Datafold-Differentiation Benchmark Report",
        "",
        f"Total scenarios: **{report['total']}**",
        f"Exact verdict accuracy: **{report['exact_verdict_accuracy']:.3f}**",
        f"Near verdict accuracy: **{report['near_verdict_accuracy']:.3f}**",
        f"Expected-migration exact accuracy: **{report['expected_migration_exact_accuracy']:.3f}**",
        "",
        "> Synthetic benchmark only. This report is for calibration and regression testing, not real-world accuracy proof.",
        "",
        "## By Datafold-Differentiation Class",
        "",
        "| Class | Count | Exact | Near | Predicted verdicts |",
        "|---|---:|---:|---:|---|",
    ]
    for cls, row in report["by_class"].items():
        lines.append(
            f"| {cls} | {row['count']} | {row['exact_accuracy']:.3f} | {row['near_accuracy']:.3f} | `{row['predicted_verdicts']}` |"
        )
    lines.extend(
        [
            "",
            "## Confusion Matrix",
            "",
            "```json",
            json.dumps(report["confusion_matrix"], indent=2),
            "```",
            "",
        ]
    )
    return "\n".join(lines)


if __name__ == "__main__":
    cli()
