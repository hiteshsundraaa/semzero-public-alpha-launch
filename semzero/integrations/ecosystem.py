from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class DBTArtifactSnapshot:
    manifest_nodes: dict[str, dict] = field(default_factory=dict)
    catalog_columns: dict[str, list[str]] = field(default_factory=dict)
    executed_nodes: list[str] = field(default_factory=list)
    failing_nodes: list[str] = field(default_factory=list)
    materializations: dict[str, str] = field(default_factory=dict)
    source_freshness: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(
        cls,
        manifest_path: str = "",
        catalog_path: str = "",
        run_results_path: str = "",
        sources_path: str = "",
    ) -> "DBTArtifactSnapshot":
        snapshot = cls()
        if manifest_path and Path(manifest_path).exists():
            payload = _read_json(Path(manifest_path))
            nodes = (payload.get("nodes") or {}) if isinstance(payload, dict) else {}
            for unique_id, node in nodes.items():
                if not isinstance(node, dict):
                    continue
                snapshot.manifest_nodes[str(unique_id)] = node
                snapshot.materializations[str(unique_id)] = str(
                    (node.get("config") or {}).get("materialized") or node.get("materialized") or ""
                )
        if catalog_path and Path(catalog_path).exists():
            payload = _read_json(Path(catalog_path))
            nodes = (payload.get("nodes") or {}) if isinstance(payload, dict) else {}
            for unique_id, node in nodes.items():
                cols = list((node.get("columns") or {}).keys()) if isinstance(node, dict) else []
                snapshot.catalog_columns[str(unique_id)] = cols
        if run_results_path and Path(run_results_path).exists():
            payload = _read_json(Path(run_results_path))
            for row in (payload.get("results") or []) if isinstance(payload, dict) else []:
                if not isinstance(row, dict):
                    continue
                unique_id = str(row.get("unique_id") or "")
                if unique_id:
                    snapshot.executed_nodes.append(unique_id)
                status = str(row.get("status") or "").lower()
                failures = int(row.get("failures") or 0)
                if unique_id and (status not in {"success", "pass", "ok"} or failures > 0):
                    snapshot.failing_nodes.append(unique_id)
        if sources_path and Path(sources_path).exists():
            payload = _read_json(Path(sources_path))
            for row in (payload.get("results") or []) if isinstance(payload, dict) else []:
                if not isinstance(row, dict):
                    continue
                unique_id = str(row.get("unique_id") or row.get("node") or "")
                status = str(row.get("status") or row.get("freshness") or "unknown")
                if unique_id:
                    snapshot.source_freshness[unique_id] = status
        snapshot.executed_nodes = list(dict.fromkeys(snapshot.executed_nodes))
        snapshot.failing_nodes = list(dict.fromkeys(snapshot.failing_nodes))
        return snapshot

    def hot_assets(self) -> list[str]:
        candidates = self.failing_nodes or self.executed_nodes
        return [_node_to_asset(node) for node in candidates[:20] if _node_to_asset(node)]

    def to_dict(self) -> dict[str, Any]:
        return {
            "executed_nodes": self.executed_nodes,
            "failing_nodes": self.failing_nodes,
            "materializations": self.materializations,
            "source_freshness": self.source_freshness,
            "hot_assets": self.hot_assets(),
        }


@dataclass
class OpenLineageSnapshot:
    jobs: list[str] = field(default_factory=list)
    datasets: list[str] = field(default_factory=list)
    column_edges: list[dict[str, str]] = field(default_factory=list)

    @classmethod
    def load(cls, paths: list[str]) -> "OpenLineageSnapshot":
        snapshot = cls()
        for raw_path in paths:
            path = Path(raw_path)
            if not path.exists():
                continue
            payloads = _read_records(path)
            for payload in payloads:
                if not isinstance(payload, dict):
                    continue
                job = payload.get("job") or {}
                namespace = str(job.get("namespace") or "")
                name = str(job.get("name") or "")
                if name:
                    snapshot.jobs.append(f"{namespace}.{name}" if namespace else name)
                for key in ("inputs", "outputs"):
                    for ds in payload.get(key) or []:
                        if not isinstance(ds, dict):
                            continue
                        ns = str(ds.get("namespace") or "")
                        nm = str(ds.get("name") or "")
                        if nm:
                            snapshot.datasets.append(f"{ns}.{nm}" if ns else nm)
                        facets = ds.get("facets") or {}
                        cl = (
                            ((facets.get("columnLineage") or {}).get("fields") or {})
                            if isinstance(facets, dict)
                            else {}
                        )
                        for out_col, detail in cl.items():
                            inputs = (
                                detail.get("inputFields") or [] if isinstance(detail, dict) else []
                            )
                            for item in inputs:
                                if not isinstance(item, dict):
                                    continue
                                snapshot.column_edges.append(
                                    {
                                        "source": f"{item.get('namespace', '')}.{item.get('name', '')}.{item.get('field', '')}",
                                        "target": out_col,
                                    }
                                )
        snapshot.jobs = list(dict.fromkeys(snapshot.jobs))
        snapshot.datasets = list(dict.fromkeys(snapshot.datasets))
        return snapshot

    def focus_assets(self) -> list[str]:
        return [item.split(".")[-1] for item in self.datasets[:20] if item]

    def to_dict(self) -> dict[str, Any]:
        return {
            "jobs": self.jobs,
            "datasets": self.datasets,
            "column_edges": self.column_edges[:100],
            "focus_assets": self.focus_assets(),
        }


@dataclass
class AirflowSnapshot:
    dags: list[str] = field(default_factory=list)
    task_edges: list[tuple[str, str]] = field(default_factory=list)
    schedules: dict[str, str] = field(default_factory=dict)
    task_assets: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def load(cls, paths: list[str]) -> "AirflowSnapshot":
        snapshot = cls()
        for raw_path in paths:
            path = Path(raw_path)
            if not path.exists():
                continue
            payload = _read_json(path)
            dags = (
                payload
                if isinstance(payload, list)
                else payload.get("dags") or payload.get("data") or []
            )
            for dag in dags:
                if not isinstance(dag, dict):
                    continue
                dag_id = str(dag.get("dag_id") or dag.get("id") or "")
                if not dag_id:
                    continue
                snapshot.dags.append(dag_id)
                schedule = str(
                    dag.get("schedule")
                    or dag.get("schedule_interval")
                    or dag.get("timetable_summary")
                    or ""
                )
                if schedule:
                    snapshot.schedules[dag_id] = schedule
                tasks = dag.get("tasks") or dag.get("task_dict") or []
                if isinstance(tasks, dict):
                    tasks = list(tasks.values())
                task_ids = set()
                for task in tasks:
                    if not isinstance(task, dict):
                        continue
                    task_id = str(task.get("task_id") or task.get("id") or "")
                    if not task_id:
                        continue
                    full_id = f"{dag_id}.{task_id}"
                    task_ids.add(task_id)
                    upstream = task.get("upstream_task_ids") or task.get("upstream") or []
                    if isinstance(upstream, str):
                        upstream = [upstream]
                    for src in upstream:
                        snapshot.task_edges.append((f"{dag_id}.{src}", full_id))
                    assets = []
                    for k in ("outlets", "inlets", "datasets", "asset_keys"):
                        raw = task.get(k) or []
                        if isinstance(raw, dict):
                            raw = list(raw.values())
                        for item in raw:
                            assets.append(_normalise_asset(item))
                    snapshot.task_assets[full_id] = [a for a in assets if a]
                downstream_map = dag.get("downstream_map") or {}
                if isinstance(downstream_map, dict):
                    for src, dests in downstream_map.items():
                        for dest in dests or []:
                            snapshot.task_edges.append((f"{dag_id}.{src}", f"{dag_id}.{dest}"))
        snapshot.dags = list(dict.fromkeys(snapshot.dags))
        snapshot.task_edges = list(dict.fromkeys(snapshot.task_edges))
        return snapshot

    def focus_assets(self) -> list[str]:
        assets = []
        for vals in self.task_assets.values():
            assets.extend(vals)
        return list(dict.fromkeys(a for a in assets if a))[:20]

    def temporal_paths(self) -> list[str]:
        return [f"{src} -> {dst}" for src, dst in self.task_edges[:20]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "dags": self.dags,
            "schedules": self.schedules,
            "focus_assets": self.focus_assets(),
            "temporal_paths": self.temporal_paths(),
        }


@dataclass
class DagsterSnapshot:
    asset_checks: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def load(cls, paths: list[str]) -> "DagsterSnapshot":
        snapshot = cls()
        for raw_path in paths:
            path = Path(raw_path)
            if not path.exists():
                continue
            payload = _read_json(path)
            records = (
                payload
                if isinstance(payload, list)
                else payload.get("asset_checks") or payload.get("checks") or []
            )
            for row in records:
                if not isinstance(row, dict):
                    continue
                snapshot.asset_checks.append(row)
        return snapshot

    def failing_assets(self) -> list[str]:
        assets = []
        for row in self.asset_checks:
            passed = row.get("passed")
            severity = str(row.get("severity") or row.get("level") or "").lower()
            if passed is False or severity in {"warn", "warning", "error", "critical"}:
                asset = _normalise_asset(
                    row.get("asset_key") or row.get("asset") or row.get("asset_name")
                )
                if asset:
                    assets.append(asset)
        return list(dict.fromkeys(assets))

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_checks": self.asset_checks[:100],
            "failing_assets": self.failing_assets(),
        }


@dataclass
class LookerSnapshot:
    impacted_objects: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def load(cls, paths: list[str]) -> "LookerSnapshot":
        snapshot = cls()
        pattern = re.compile(
            r"sql_table_name\s*:\s*([\w.\-]+)|from\s*:\s*([\w.\-]+)", re.IGNORECASE
        )
        field_pattern = re.compile(r"\$\{([^}]+)\}")
        for raw_path in paths:
            path = Path(raw_path)
            if not path.exists():
                continue
            files = (
                [path]
                if path.is_file()
                else sorted(path.rglob("*.lookml")) + sorted(path.rglob("*.lkml"))
            )
            for file in files:
                text = file.read_text(encoding="utf-8", errors="ignore")
                tables = [a or b for a, b in pattern.findall(text)]
                refs = [ref.strip() for ref in field_pattern.findall(text)]
                impacted = list(
                    dict.fromkeys(
                        [_normalise_asset(t) for t in tables + refs if _normalise_asset(t)]
                    )
                )
                if impacted:
                    snapshot.impacted_objects[str(file.name)] = impacted
        return snapshot

    def impacted_assets(self) -> list[str]:
        assets = []
        for vals in self.impacted_objects.values():
            assets.extend(vals)
        return list(dict.fromkeys(assets))[:20]

    def to_dict(self) -> dict[str, Any]:
        return {
            "impacted_objects": self.impacted_objects,
            "impacted_assets": self.impacted_assets(),
        }


@dataclass
class MonteCarloSnapshot:
    monitors: list[dict[str, Any]] = field(default_factory=list)
    lineage_assets: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, paths: list[str]) -> "MonteCarloSnapshot":
        snapshot = cls()
        for raw_path in paths:
            path = Path(raw_path)
            if not path.exists():
                continue
            payload = _read_json(path)
            records = (
                payload
                if isinstance(payload, list)
                else payload.get("monitors") or payload.get("alerts") or payload.get("data") or []
            )
            for row in records:
                if not isinstance(row, dict):
                    continue
                snapshot.monitors.append(row)
                for key in ("dataset", "table", "asset", "full_table_name", "name"):
                    asset = _normalise_asset(row.get(key))
                    if asset:
                        snapshot.lineage_assets.append(asset)
                for key in ("upstream_assets", "downstream_assets", "lineage"):
                    raw = row.get(key) or []
                    if isinstance(raw, dict):
                        raw = list(raw.values())
                    for item in raw:
                        asset = _normalise_asset(item)
                        if asset:
                            snapshot.lineage_assets.append(asset)
        snapshot.lineage_assets = list(dict.fromkeys(snapshot.lineage_assets))
        return snapshot

    def focus_assets(self) -> list[str]:
        return self.lineage_assets[:20]

    def to_dict(self) -> dict[str, Any]:
        return {
            "monitors": self.monitors[:100],
            "focus_assets": self.focus_assets(),
        }


@dataclass
class EcosystemContext:
    dbt: DBTArtifactSnapshot = field(default_factory=DBTArtifactSnapshot)
    openlineage: OpenLineageSnapshot = field(default_factory=OpenLineageSnapshot)
    airflow: AirflowSnapshot = field(default_factory=AirflowSnapshot)
    dagster: DagsterSnapshot = field(default_factory=DagsterSnapshot)
    looker: LookerSnapshot = field(default_factory=LookerSnapshot)
    montecarlo: MonteCarloSnapshot = field(default_factory=MonteCarloSnapshot)

    @classmethod
    def load(
        cls,
        dbt_manifest_path: str = "",
        dbt_catalog_path: str = "",
        dbt_run_results_path: str = "",
        dbt_sources_path: str = "",
        openlineage_paths: list[str] | None = None,
        airflow_paths: list[str] | None = None,
        dagster_paths: list[str] | None = None,
        looker_paths: list[str] | None = None,
        montecarlo_paths: list[str] | None = None,
    ) -> "EcosystemContext":
        return cls(
            dbt=DBTArtifactSnapshot.load(
                dbt_manifest_path, dbt_catalog_path, dbt_run_results_path, dbt_sources_path
            ),
            openlineage=OpenLineageSnapshot.load(openlineage_paths or []),
            airflow=AirflowSnapshot.load(airflow_paths or []),
            dagster=DagsterSnapshot.load(dagster_paths or []),
            looker=LookerSnapshot.load(looker_paths or []),
            montecarlo=MonteCarloSnapshot.load(montecarlo_paths or []),
        )

    def focus_assets(self) -> list[str]:
        assets = []
        assets.extend(self.dbt.hot_assets())
        assets.extend(self.openlineage.focus_assets())
        assets.extend(self.airflow.focus_assets())
        assets.extend(self.dagster.failing_assets())
        assets.extend(self.looker.impacted_assets())
        assets.extend(self.montecarlo.focus_assets())
        return list(dict.fromkeys(a for a in assets if a))[:40]

    def to_dict(self) -> dict[str, Any]:
        return {
            "dbt": self.dbt.to_dict(),
            "openlineage": self.openlineage.to_dict(),
            "airflow": self.airflow.to_dict(),
            "dagster": self.dagster.to_dict(),
            "looker": self.looker.to_dict(),
            "montecarlo": self.montecarlo.to_dict(),
            "focus_assets": self.focus_assets(),
        }


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_records(path: Path) -> list[Any]:
    if path.suffix.lower() == ".jsonl":
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    payload = _read_json(path)
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        return payload.get("events") or payload.get("records") or [payload]
    return []


def _node_to_asset(node_id: str) -> str:
    raw = str(node_id or "")
    if not raw:
        return ""
    if raw.count(".") >= 2:
        return raw.split(".")[-1]
    return raw.replace("source:", "").replace("model:", "")


def _normalise_asset(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("name", "dataset", "asset_key", "key", "table", "id"):
            if value.get(key):
                return _normalise_asset(value.get(key))
        return ""
    if isinstance(value, (list, tuple)):
        return ".".join(str(v) for v in value if v)
    raw = str(value or "").strip()
    raw = raw.replace("${", "").replace("}", "")
    return raw
