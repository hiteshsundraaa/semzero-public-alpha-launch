from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SENSITIVITY_WEIGHTS = {
    "REVENUE_CRITICAL": 1.00,
    "CUSTOMER_FACING": 0.80,
    "OPERATIONAL": 0.60,
    "ANALYTICAL": 0.35,
    "EXPERIMENTAL": 0.10,
    "UNKNOWN": 0.30,
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_json_hash(payload: Any) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _git_commit_sha(repo_root: str | Path | None = None) -> str:
    cwd = str(repo_root or ".")
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=cwd,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return "unknown"


def _norm_path(path: str | None) -> str:
    return (path or "").replace("\\", "/").strip()


def _resource_name(resource: dict[str, Any]) -> str:
    return str(resource.get("name") or resource.get("alias") or resource.get("unique_id") or "")


def _resource_path(resource: dict[str, Any]) -> str:
    return _norm_path(
        resource.get("original_file_path")
        or resource.get("path")
        or resource.get("compiled_path")
        or ""
    )


def _depends_on_nodes(resource: dict[str, Any]) -> list[str]:
    depends_on = resource.get("depends_on") or {}
    nodes = depends_on.get("nodes") if isinstance(depends_on, dict) else []
    if isinstance(nodes, list):
        return [str(node) for node in nodes]
    return []


def _resource_columns(resource: dict[str, Any]) -> dict[str, Any]:
    cols = resource.get("columns") or {}
    return cols if isinstance(cols, dict) else {}


def _column_tests_from_column_meta(column_meta: dict[str, Any]) -> list[str]:
    tests = []
    raw_tests = column_meta.get("tests") or column_meta.get("data_tests") or []
    if isinstance(raw_tests, list):
        for item in raw_tests:
            if isinstance(item, str):
                tests.append(item)
            elif isinstance(item, dict):
                tests.extend(str(key) for key in item.keys())
    return sorted(set(tests))


def _test_name_and_args(test_node: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    name = str(test_node.get("name") or test_node.get("test_metadata", {}).get("name") or "")
    metadata = test_node.get("test_metadata") or {}
    kwargs = metadata.get("kwargs") if isinstance(metadata, dict) else {}
    if not isinstance(kwargs, dict):
        kwargs = {}

    if not name:
        # dbt unique IDs often contain test family names.
        uid = str(test_node.get("unique_id") or "").lower()
        for candidate in ["not_null", "unique", "accepted_values", "relationships"]:
            if candidate in uid:
                name = candidate
                break

    return name, kwargs


def _test_column_name(test_node: dict[str, Any], kwargs: dict[str, Any]) -> str:
    column = (
        test_node.get("column_name")
        or kwargs.get("column_name")
        or kwargs.get("field")
        or kwargs.get("arg")
        or ""
    )
    return str(column or "")


def _accepted_values(kwargs: dict[str, Any]) -> list[str]:
    values = kwargs.get("values") or kwargs.get("accepted_values") or []
    if isinstance(values, list):
        return [str(v) for v in values]
    return []


def _materialization(resource: dict[str, Any]) -> str:
    config = resource.get("config") or {}
    if isinstance(config, dict):
        materialized = config.get("materialized")
        if materialized:
            return str(materialized)
    return ""


def _meta(resource: dict[str, Any]) -> dict[str, Any]:
    meta = resource.get("meta") or {}
    return meta if isinstance(meta, dict) else {}


def _tags(resource: dict[str, Any]) -> list[str]:
    tags = resource.get("tags") or []
    if isinstance(tags, list):
        return [str(tag) for tag in tags]
    return []


def _infer_sensitivity(resource: dict[str, Any], criticality_registry: dict[str, Any] | None = None) -> dict[str, str]:
    unique_id = str(resource.get("unique_id") or "")
    name = _resource_name(resource)
    path = _resource_path(resource)
    meta = _meta(resource)
    tags = _tags(resource)

    registry = criticality_registry or {}
    for key in [unique_id, name, path]:
        if key and isinstance(registry, dict) and key in registry:
            value = registry[key]
            if isinstance(value, dict):
                label = str(value.get("label") or value.get("criticality") or "UNKNOWN").upper()
            else:
                label = str(value).upper()
            return {"label": _normalize_sensitivity(label), "source": "explicit_registry"}

    for key in ["semzero_criticality", "criticality", "sensitivity", "business_criticality"]:
        if meta.get(key):
            return {"label": _normalize_sensitivity(str(meta[key]).upper()), "source": "dbt_meta"}

    text = " ".join([name, path, " ".join(tags)]).lower()
    tokens = set(re.split(r"[^a-z0-9]+", text))
    compact = text.replace("_", " ").replace("-", " ")

    revenue_terms = {"revenue", "billing", "payment", "payments", "finance", "invoice", "invoices", "close", "arr", "mrr", "bookings"}
    customer_terms = {"customer", "customers", "user", "users", "subscription", "subscriptions", "activation", "churn", "customer_facing"}
    ops_terms = {"ops", "operation", "operations", "fulfillment", "sla", "queue", "support"}
    experimental_terms = {"scratch", "sandbox", "experiment", "experimental", "dev", "tmp", "temp"}

    if tokens & revenue_terms or any(term in compact for term in ["order payments", "payments", "revenue", "billing", "finance"]):
        return {"label": "REVENUE_CRITICAL", "source": "inferred_pattern"}
    if tokens & customer_terms or any(term in compact for term in ["customer", "customers", "user facing"]):
        return {"label": "CUSTOMER_FACING", "source": "inferred_pattern"}
    if tokens & ops_terms:
        return {"label": "OPERATIONAL", "source": "inferred_pattern"}
    if tokens & experimental_terms:
        return {"label": "EXPERIMENTAL", "source": "inferred_pattern"}

    return {"label": "UNKNOWN", "source": "unknown"}


def _normalize_sensitivity(label: str) -> str:
    label = label.upper().replace("-", "_").replace(" ", "_")
    aliases = {
        "HIGH": "REVENUE_CRITICAL",
        "CRITICAL": "REVENUE_CRITICAL",
        "FINANCE_CRITICAL": "REVENUE_CRITICAL",
        "MEDIUM": "OPERATIONAL",
        "LOW": "ANALYTICAL",
        "INTERNAL": "ANALYTICAL",
        "INTERNAL_ONLY": "ANALYTICAL",
    }
    label = aliases.get(label, label)
    if label in SENSITIVITY_WEIGHTS:
        return label
    return "UNKNOWN"


def _build_children(resources: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    children: dict[str, list[str]] = {uid: [] for uid in resources}
    for uid, resource in resources.items():
        for parent in _depends_on_nodes(resource):
            if parent in children:
                children[parent].append(uid)
    return {key: sorted(set(value)) for key, value in children.items()}


def _shortest_downstream(
    root: str,
    children: dict[str, list[str]],
    resources: dict[str, dict[str, Any]],
    sensitivity_by_uid: dict[str, dict[str, str]],
    max_depth: int = 8,
) -> list[dict[str, Any]]:
    out = []
    seen = {root}
    queue = deque([(root, 0)])
    while queue:
        node, depth = queue.popleft()
        if depth >= max_depth:
            continue
        for child in children.get(node, []):
            if child in seen:
                continue
            seen.add(child)
            child_depth = depth + 1
            res = resources.get(child, {})
            sensitivity = sensitivity_by_uid.get(child, {"label": "UNKNOWN", "source": "unknown"})
            out.append(
                {
                    "unique_id": child,
                    "name": _resource_name(res),
                    "resource_type": str(res.get("resource_type") or ""),
                    "distance": child_depth,
                    "sensitivity": sensitivity.get("label", "UNKNOWN"),
                    "sensitivity_source": sensitivity.get("source", "unknown"),
                }
            )
            queue.append((child, child_depth))
    return out


def _contracts_for_model(
    model_uid: str,
    model: dict[str, Any],
    tests_by_parent: dict[str, list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], list[str], list[str]]:
    contracts: list[dict[str, Any]] = []
    column_surface: dict[str, dict[str, Any]] = {}
    primary_key_candidates: set[str] = set()
    grain_candidates: set[str] = set()

    for col_name, col_meta in _resource_columns(model).items():
        col_tests = _column_tests_from_column_meta(col_meta if isinstance(col_meta, dict) else {})
        column_surface[str(col_name)] = {
            "tests": col_tests,
            "inferred_required": "not_null" in col_tests,
            "inferred_unique": "unique" in col_tests,
            "accepted_values": [],
        }

    for test in tests_by_parent.get(model_uid, []):
        test_name, kwargs = _test_name_and_args(test)
        test_name_lower = test_name.lower()
        column = _test_column_name(test, kwargs)

        if column and column not in column_surface:
            column_surface[column] = {
                "tests": [],
                "inferred_required": False,
                "inferred_unique": False,
                "accepted_values": [],
            }

        if "not_null" in test_name_lower and column:
            column_surface[column]["inferred_required"] = True
            column_surface[column]["tests"] = sorted(set(column_surface[column]["tests"] + ["not_null"]))
            contracts.append(
                {
                    "dependency_type": "column",
                    "dependent_property": f"{column} not_null",
                    "column": column,
                    "confidence": 0.70,
                    "source": "inferred_test",
                    "test_unique_id": test.get("unique_id"),
                }
            )

        if test_name_lower == "unique" or test_name_lower.endswith(".unique") or "unique" in test_name_lower:
            if column:
                column_surface[column]["inferred_unique"] = True
                column_surface[column]["tests"] = sorted(set(column_surface[column]["tests"] + ["unique"]))
                primary_key_candidates.add(column)
                grain_candidates.add(column)
                contracts.append(
                    {
                        "dependency_type": "grain",
                        "dependent_property": f"{column} unique",
                        "column": column,
                        "confidence": 0.75,
                        "source": "inferred_test",
                        "test_unique_id": test.get("unique_id"),
                    }
                )

        if "accepted_values" in test_name_lower and column:
            values = _accepted_values(kwargs)
            column_surface[column]["accepted_values"] = values
            column_surface[column]["tests"] = sorted(set(column_surface[column]["tests"] + ["accepted_values"]))
            contracts.append(
                {
                    "dependency_type": "enum_domain",
                    "dependent_property": f"{column} accepted_values",
                    "column": column,
                    "accepted_values": values,
                    "confidence": 0.75,
                    "source": "inferred_test",
                    "test_unique_id": test.get("unique_id"),
                }
            )

        if "relationships" in test_name_lower:
            contracts.append(
                {
                    "dependency_type": "relationship",
                    "dependent_property": "referential_integrity",
                    "column": column,
                    "confidence": 0.70,
                    "source": "inferred_test",
                    "test_unique_id": test.get("unique_id"),
                    "kwargs": kwargs,
                }
            )

    return contracts, column_surface, sorted(primary_key_candidates), sorted(grain_candidates)


def build_dbt_repo_snapshot(
    manifest_path: str | Path,
    *,
    repo: str = "unknown",
    repo_root: str | Path | None = None,
    criticality_registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    manifest_path = Path(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    nodes = manifest.get("nodes") or {}
    sources = manifest.get("sources") or {}
    exposures = manifest.get("exposures") or {}
    metrics = manifest.get("metrics") or {}

    resources: dict[str, dict[str, Any]] = {}
    resources.update({str(uid): node for uid, node in nodes.items() if isinstance(node, dict)})
    resources.update({str(uid): src for uid, src in sources.items() if isinstance(src, dict)})
    resources.update({str(uid): exp for uid, exp in exposures.items() if isinstance(exp, dict)})
    resources.update({str(uid): metric for uid, metric in metrics.items() if isinstance(metric, dict)})

    model_like = {
        uid: res
        for uid, res in resources.items()
        if str(res.get("resource_type") or "") in {"model", "source", "seed", "snapshot"}
    }

    test_nodes = [
        node
        for node in nodes.values()
        if isinstance(node, dict)
        and (
            str(node.get("resource_type") or "") in {"test", "unit_test"}
            or str(node.get("unique_id") or "").startswith("test.")
        )
    ]

    tests_by_parent: dict[str, list[dict[str, Any]]] = {}
    for test in test_nodes:
        for parent in _depends_on_nodes(test):
            tests_by_parent.setdefault(parent, []).append(test)

    children = _build_children(resources)
    sensitivity_by_uid = {
        uid: _infer_sensitivity(res, criticality_registry=criticality_registry)
        for uid, res in resources.items()
    }

    models: dict[str, Any] = {}
    dependency_contracts: list[dict[str, Any]] = []

    for uid, model in sorted(model_like.items()):
        contracts, column_surface, pk_candidates, grain_candidates = _contracts_for_model(
            uid,
            model,
            tests_by_parent,
        )
        downstream = _shortest_downstream(uid, children, resources, sensitivity_by_uid)
        sensitivity = sensitivity_by_uid.get(uid, {"label": "UNKNOWN", "source": "unknown"})

        for contract in contracts:
            dependency_contracts.append(
                {
                    "upstream_model": uid,
                    "downstream_model": uid,
                    **contract,
                }
            )

        models[uid] = {
            "unique_id": uid,
            "name": _resource_name(model),
            "path": _resource_path(model),
            "resource_type": str(model.get("resource_type") or ""),
            "materialization": _materialization(model),
            "tags": _tags(model),
            "owner": str(model.get("owner") or _meta(model).get("owner") or ""),
            "depends_on": _depends_on_nodes(model),
            "downstream": downstream,
            "downstream_count": len(downstream),
            "columns": column_surface,
            "primary_key_candidates": pk_candidates,
            "grain_candidates": grain_candidates,
            "sensitivity": sensitivity,
            "test_count": len(tests_by_parent.get(uid, [])),
            "contracts": contracts,
        }

    snapshot = {
        "snapshot_kind": "semzero_repo_snapshot_v1",
        "repo": repo,
        "commit_sha": _git_commit_sha(repo_root),
        "captured_at": _utc_now(),
        "manifest_path": str(manifest_path),
        "manifest_hash": _stable_json_hash(manifest),
        "summary": {
            "model_count": sum(1 for res in nodes.values() if isinstance(res, dict) and res.get("resource_type") == "model"),
            "source_count": len(sources),
            "exposure_count": len(exposures),
            "metric_count": len(metrics),
            "test_count": len(test_nodes),
            "indexed_resource_count": len(model_like),
            "dependency_contract_count": len(dependency_contracts),
        },
        "models": models,
        "dependency_contracts": dependency_contracts,
    }
    snapshot["snapshot_hash"] = _stable_json_hash(snapshot)
    return snapshot


def write_dbt_repo_snapshot(
    manifest_path: str | Path,
    output_path: str | Path,
    *,
    repo: str = "unknown",
    repo_root: str | Path | None = None,
    criticality_registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    snapshot = build_dbt_repo_snapshot(
        manifest_path,
        repo=repo,
        repo_root=repo_root,
        criticality_registry=criticality_registry,
    )
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(snapshot, indent=2, sort_keys=True), encoding="utf-8")
    return snapshot
