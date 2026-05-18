from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "semzero_memory_v1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, sort_keys=True, default=str)


def _hash_payload(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _read_json(path: str | Path) -> dict[str, Any]:
    src = Path(path)
    if not src.exists():
        raise FileNotFoundError(str(src))
    payload = json.loads(src.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {src}")
    return payload


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _repo_from_payload(payload: dict[str, Any], fallback: str = "unknown") -> str:
    for container in (payload.get("metadata"), payload.get("summary"), payload):
        if isinstance(container, dict):
            for key in ("repo", "repository", "github_repository"):
                value = container.get(key)
                if value:
                    return str(value)
    return fallback


def _run_id_for_receipt(receipt: dict[str, Any]) -> str:
    explicit = (
        receipt.get("run_id")
        or (receipt.get("metadata") or {}).get("run_id")
        or (receipt.get("metadata") or {}).get("github_run_id")
    )
    if explicit:
        return str(explicit)

    seed = {
        "kind": receipt.get("receipt_kind"),
        "generated_at": receipt.get("generated_at"),
        "changed_files": receipt.get("changed_files"),
        "findings": [
            f.get("stable_id") or f.get("id") or f.get("fingerprint")
            for f in receipt.get("findings", [])
            if isinstance(f, dict)
        ],
    }
    return "run_" + _hash_payload(seed)[:16]


def _finding_stable_id(finding: dict[str, Any]) -> str:
    return str(
        finding.get("stable_id")
        or finding.get("id")
        or finding.get("fingerprint")
        or "unknown_finding"
    )


def _source_model(finding: dict[str, Any]) -> str:
    source = finding.get("source") or {}
    if not isinstance(source, dict):
        source = {}
    return str(
        source.get("unique_id")
        or finding.get("source_resource")
        or source.get("name")
        or finding.get("source_path")
        or "unknown_model"
    )


def _business_label(finding: dict[str, Any]) -> str:
    impact = finding.get("business_impact") or {}
    if isinstance(impact, dict):
        sev = impact.get("highest_business_severity")
        if sev:
            return str(sev)
    return str(finding.get("business_severity") or "UNKNOWN")


def _fidelity_score(finding: dict[str, Any]) -> float:
    fidelity = finding.get("replay_fidelity") or {}
    if isinstance(fidelity, dict):
        return _safe_float(fidelity.get("score"), 0.0)
    return 0.0


def _fidelity_level(finding: dict[str, Any]) -> str:
    fidelity = finding.get("replay_fidelity") or {}
    if isinstance(fidelity, dict):
        return str(fidelity.get("level") or "unknown")
    return "unknown"


@dataclass
class SemZeroMemoryDB:
    path: str | Path = "data/semzero_memory.sqlite"

    def connect(self) -> sqlite3.Connection:
        db_path = Path(self.path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def init(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS semzero_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                INSERT OR REPLACE INTO semzero_meta(key, value)
                VALUES ('schema_version', 'semzero_memory_v1');

                CREATE TABLE IF NOT EXISTS semzero_runs (
                    run_id TEXT PRIMARY KEY,
                    repo TEXT NOT NULL,
                    pr_number TEXT NOT NULL DEFAULT '',
                    commit_sha TEXT NOT NULL DEFAULT '',
                    action_sha TEXT NOT NULL DEFAULT '',
                    verdict TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    generated_at TEXT NOT NULL,
                    changed_file_count INTEGER NOT NULL DEFAULT 0,
                    finding_count INTEGER NOT NULL DEFAULT 0,
                    review_required_count INTEGER NOT NULL DEFAULT 0,
                    advisory_count INTEGER NOT NULL DEFAULT 0,
                    raw_receipt_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS semzero_findings (
                    finding_key TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    stable_id TEXT NOT NULL,
                    repo TEXT NOT NULL,
                    family TEXT NOT NULL,
                    detector TEXT NOT NULL DEFAULT '',
                    source_model TEXT NOT NULL DEFAULT '',
                    source_path TEXT NOT NULL DEFAULT '',
                    business_label TEXT NOT NULL DEFAULT 'UNKNOWN',
                    blast_radius_count INTEGER NOT NULL DEFAULT 0,
                    evidence_tier TEXT NOT NULL DEFAULT '',
                    fidelity_score REAL NOT NULL DEFAULT 0,
                    fidelity_level TEXT NOT NULL DEFAULT '',
                    displayed_priority INTEGER NOT NULL DEFAULT 0,
                    routing TEXT NOT NULL DEFAULT '',
                    calibration_state TEXT NOT NULL DEFAULT 'uncalibrated',
                    created_at TEXT NOT NULL,
                    raw_finding_json TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES semzero_runs(run_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_findings_family_detector
                ON semzero_findings(family, detector);

                CREATE INDEX IF NOT EXISTS idx_findings_model
                ON semzero_findings(repo, source_model);

                CREATE TABLE IF NOT EXISTS repo_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    repo TEXT NOT NULL,
                    commit_sha TEXT NOT NULL DEFAULT '',
                    captured_at TEXT NOT NULL,
                    manifest_hash TEXT NOT NULL DEFAULT '',
                    model_count INTEGER NOT NULL DEFAULT 0,
                    source_count INTEGER NOT NULL DEFAULT 0,
                    test_count INTEGER NOT NULL DEFAULT 0,
                    dependency_contract_count INTEGER NOT NULL DEFAULT 0,
                    raw_snapshot_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS model_baselines (
                    baseline_id TEXT PRIMARY KEY,
                    snapshot_id TEXT NOT NULL,
                    repo TEXT NOT NULL,
                    model_unique_id TEXT NOT NULL,
                    model_name TEXT NOT NULL,
                    path TEXT NOT NULL DEFAULT '',
                    materialization TEXT NOT NULL DEFAULT '',
                    sensitivity_label TEXT NOT NULL DEFAULT 'UNKNOWN',
                    sensitivity_source TEXT NOT NULL DEFAULT '',
                    downstream_count INTEGER NOT NULL DEFAULT 0,
                    test_count INTEGER NOT NULL DEFAULT 0,
                    primary_key_columns_json TEXT NOT NULL DEFAULT '[]',
                    grain_candidates_json TEXT NOT NULL DEFAULT '[]',
                    column_surface_json TEXT NOT NULL DEFAULT '{}',
                    raw_model_json TEXT NOT NULL,
                    FOREIGN KEY(snapshot_id) REFERENCES repo_snapshots(snapshot_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_model_baselines_repo_model
                ON model_baselines(repo, model_unique_id);

                CREATE TABLE IF NOT EXISTS dependency_contracts (
                    contract_id TEXT PRIMARY KEY,
                    snapshot_id TEXT NOT NULL,
                    repo TEXT NOT NULL,
                    upstream_model TEXT NOT NULL DEFAULT '',
                    downstream_model TEXT NOT NULL DEFAULT '',
                    dependency_type TEXT NOT NULL DEFAULT '',
                    dependent_property TEXT NOT NULL DEFAULT '',
                    column_name TEXT NOT NULL DEFAULT '',
                    confidence REAL NOT NULL DEFAULT 0,
                    source TEXT NOT NULL DEFAULT '',
                    raw_contract_json TEXT NOT NULL,
                    FOREIGN KEY(snapshot_id) REFERENCES repo_snapshots(snapshot_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS calibrations (
                    calibration_id TEXT PRIMARY KEY,
                    finding_key TEXT NOT NULL DEFAULT '',
                    stable_id TEXT NOT NULL,
                    repo TEXT NOT NULL,
                    response TEXT NOT NULL,
                    actor TEXT NOT NULL DEFAULT '',
                    reason TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    raw_calibration_json TEXT NOT NULL
                );
                """
            )

    def ingest_receipt(
        self,
        receipt_path: str | Path,
        repo: str = "",
        pr_number: str = "",
        commit_sha: str = "",
        action_sha: str = "",
    ) -> dict[str, Any]:
        receipt = _read_json(receipt_path)
        self.init()

        findings = [f for f in receipt.get("findings", []) if isinstance(f, dict)]
        run_id = _run_id_for_receipt(receipt)
        effective_repo = repo or _repo_from_payload(receipt)
        generated_at = str(receipt.get("generated_at") or _now())

        review_required = 0
        advisory = 0

        for finding in findings:
            causality = finding.get("causality") or {}
            routing = str(causality.get("routing") or finding.get("routing") or "")
            if routing == "must_review":
                review_required += 1
            elif routing:
                advisory += 1

        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO semzero_runs (
                    run_id, repo, pr_number, commit_sha, action_sha, verdict, mode,
                    generated_at, changed_file_count, finding_count,
                    review_required_count, advisory_count, raw_receipt_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    effective_repo,
                    pr_number or str((receipt.get("metadata") or {}).get("pr_number") or ""),
                    commit_sha or str((receipt.get("metadata") or {}).get("commit_sha") or ""),
                    action_sha or str((receipt.get("metadata") or {}).get("action_sha") or ""),
                    str(receipt.get("verdict") or "UNKNOWN"),
                    str(receipt.get("mode") or "unknown"),
                    generated_at,
                    len(receipt.get("changed_files") or []),
                    len(findings),
                    review_required,
                    advisory,
                    _json(receipt),
                ),
            )

            for finding in findings:
                stable_id = _finding_stable_id(finding)
                finding_key = f"{run_id}:{stable_id}"
                causality = finding.get("causality") or {}

                conn.execute(
                    """
                    INSERT OR REPLACE INTO semzero_findings (
                        finding_key, run_id, stable_id, repo, family, detector,
                        source_model, source_path, business_label, blast_radius_count,
                        evidence_tier, fidelity_score, fidelity_level, displayed_priority,
                        routing, calibration_state, created_at, raw_finding_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        finding_key,
                        run_id,
                        stable_id,
                        effective_repo,
                        str(finding.get("family") or "unknown"),
                        str(finding.get("detector_version") or finding.get("adapter") or ""),
                        _source_model(finding),
                        str(finding.get("source_path") or ""),
                        _business_label(finding),
                        len(finding.get("blast_radius") or []),
                        str(finding.get("evidence_tier") or ""),
                        _fidelity_score(finding),
                        _fidelity_level(finding),
                        _safe_int(causality.get("priority") or finding.get("risk_score"), 0),
                        str(causality.get("routing") or finding.get("routing") or ""),
                        "uncalibrated",
                        generated_at,
                        _json(finding),
                    ),
                )

        return {
            "kind": "semzero_memory_ingest_receipt_v1",
            "run_id": run_id,
            "repo": effective_repo,
            "finding_count": len(findings),
            "review_required_count": review_required,
            "advisory_count": advisory,
        }

    def ingest_snapshot(self, snapshot_path: str | Path, repo: str = "") -> dict[str, Any]:
        snapshot = _read_json(snapshot_path)
        self.init()

        effective_repo = repo or str(snapshot.get("repo") or snapshot.get("repository") or "unknown")
        commit_sha = str(snapshot.get("commit_sha") or "")
        captured_at = str(snapshot.get("captured_at") or _now())
        manifest_hash = str(snapshot.get("manifest_hash") or "")
        models = snapshot.get("models") or {}
        contracts = snapshot.get("dependency_contracts") or {}
        summary = snapshot.get("summary") or {}

        if isinstance(contracts, dict):
            contract_items = list(contracts.values())
        elif isinstance(contracts, list):
            contract_items = contracts
        else:
            contract_items = []

        snapshot_id = str(snapshot.get("snapshot_id") or f"snapshot_{_hash_payload(snapshot)[:16]}")

        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO repo_snapshots (
                    snapshot_id, repo, commit_sha, captured_at, manifest_hash,
                    model_count, source_count, test_count, dependency_contract_count,
                    raw_snapshot_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    effective_repo,
                    commit_sha,
                    captured_at,
                    manifest_hash,
                    len(models) if isinstance(models, dict) else _safe_int(summary.get("model_count")),
                    _safe_int(summary.get("source_count")),
                    _safe_int(summary.get("test_count")),
                    len(contract_items),
                    _json(snapshot),
                ),
            )

            if isinstance(models, dict):
                for uid, model in models.items():
                    if not isinstance(model, dict):
                        continue

                    sensitivity = model.get("sensitivity") or {}
                    if not isinstance(sensitivity, dict):
                        sensitivity = {"label": str(sensitivity), "source": "unknown"}

                    baseline_id = f"{snapshot_id}:{uid}"
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO model_baselines (
                            baseline_id, snapshot_id, repo, model_unique_id, model_name,
                            path, materialization, sensitivity_label, sensitivity_source,
                            downstream_count, test_count, primary_key_columns_json,
                            grain_candidates_json, column_surface_json, raw_model_json
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            baseline_id,
                            snapshot_id,
                            effective_repo,
                            str(uid),
                            str(model.get("name") or uid),
                            str(model.get("path") or ""),
                            str(model.get("materialization") or ""),
                            str(sensitivity.get("label") or "UNKNOWN"),
                            str(sensitivity.get("source") or ""),
                            _safe_int(model.get("downstream_count")),
                            _safe_int(model.get("test_count")),
                            _json(model.get("primary_key_candidates") or []),
                            _json(model.get("grain_candidates") or []),
                            _json(model.get("columns") or {}),
                            _json(model),
                        ),
                    )

            for contract in contract_items:
                if not isinstance(contract, dict):
                    continue

                seed = {
                    "snapshot_id": snapshot_id,
                    "upstream": contract.get("upstream_model"),
                    "downstream": contract.get("downstream_model"),
                    "type": contract.get("dependency_type"),
                    "property": contract.get("dependent_property"),
                    "column": contract.get("column"),
                }
                contract_id = "contract_" + _hash_payload(seed)[:24]

                conn.execute(
                    """
                    INSERT OR REPLACE INTO dependency_contracts (
                        contract_id, snapshot_id, repo, upstream_model,
                        downstream_model, dependency_type, dependent_property,
                        column_name, confidence, source, raw_contract_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        contract_id,
                        snapshot_id,
                        effective_repo,
                        str(contract.get("upstream_model") or ""),
                        str(contract.get("downstream_model") or ""),
                        str(contract.get("dependency_type") or ""),
                        str(contract.get("dependent_property") or ""),
                        str(contract.get("column") or ""),
                        _safe_float(contract.get("confidence"), 0.0),
                        str(contract.get("source") or ""),
                        _json(contract),
                    ),
                )

        return {
            "kind": "semzero_memory_ingest_snapshot_v1",
            "snapshot_id": snapshot_id,
            "repo": effective_repo,
            "model_count": len(models) if isinstance(models, dict) else 0,
            "dependency_contract_count": len(contract_items),
        }

    def record_calibration(
        self,
        stable_id: str,
        response: str,
        repo: str = "",
        actor: str = "",
        reason: str = "",
        run_id: str = "",
    ) -> dict[str, Any]:
        self.init()

        calibration_id = "cal_" + _hash_payload(
            {
                "stable_id": stable_id,
                "response": response,
                "repo": repo,
                "actor": actor,
                "reason": reason,
                "at": _now(),
            }
        )[:20]

        finding_key = ""

        with self.connect() as conn:
            if run_id:
                row = conn.execute(
                    """
                    SELECT finding_key, repo
                    FROM semzero_findings
                    WHERE run_id = ? AND stable_id = ?
                    LIMIT 1
                    """,
                    (run_id, stable_id),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT finding_key, repo
                    FROM semzero_findings
                    WHERE stable_id = ?
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (stable_id,),
                ).fetchone()

            if row:
                finding_key = str(row["finding_key"])
                repo = repo or str(row["repo"])

            payload = {
                "calibration_id": calibration_id,
                "finding_key": finding_key,
                "stable_id": stable_id,
                "repo": repo or "unknown",
                "response": response,
                "actor": actor,
                "reason": reason,
                "created_at": _now(),
            }

            conn.execute(
                """
                INSERT OR REPLACE INTO calibrations (
                    calibration_id, finding_key, stable_id, repo, response,
                    actor, reason, created_at, raw_calibration_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["calibration_id"],
                    payload["finding_key"],
                    payload["stable_id"],
                    payload["repo"],
                    payload["response"],
                    payload["actor"],
                    payload["reason"],
                    payload["created_at"],
                    _json(payload),
                ),
            )

            if finding_key:
                conn.execute(
                    """
                    UPDATE semzero_findings
                    SET calibration_state = ?
                    WHERE finding_key = ?
                    """,
                    (response, finding_key),
                )

        return payload

    def summary(self) -> dict[str, Any]:
        self.init()

        with self.connect() as conn:
            runs = conn.execute("SELECT COUNT(*) AS n FROM semzero_runs").fetchone()["n"]
            findings = conn.execute("SELECT COUNT(*) AS n FROM semzero_findings").fetchone()["n"]
            snapshots = conn.execute("SELECT COUNT(*) AS n FROM repo_snapshots").fetchone()["n"]
            models = conn.execute("SELECT COUNT(*) AS n FROM model_baselines").fetchone()["n"]
            contracts = conn.execute("SELECT COUNT(*) AS n FROM dependency_contracts").fetchone()["n"]
            calibrations = conn.execute("SELECT COUNT(*) AS n FROM calibrations").fetchone()["n"]

            family_rows = conn.execute(
                """
                SELECT
                    family,
                    detector,
                    COUNT(*) AS finding_count,
                    SUM(CASE WHEN calibration_state = 'false_positive' THEN 1 ELSE 0 END) AS false_positive_count,
                    SUM(CASE WHEN calibration_state IN ('agree', 'fixed') THEN 1 ELSE 0 END) AS agreed_count
                FROM semzero_findings
                GROUP BY family, detector
                ORDER BY finding_count DESC, family ASC
                """
            ).fetchall()

            family_calibration = []
            for row in family_rows:
                total = int(row["finding_count"] or 0)
                fp = int(row["false_positive_count"] or 0)
                agreed = int(row["agreed_count"] or 0)
                family_calibration.append(
                    {
                        "family": row["family"],
                        "detector": row["detector"],
                        "finding_count": total,
                        "false_positive_count": fp,
                        "agreed_count": agreed,
                        "false_positive_rate": round(fp / total, 4) if total else 0.0,
                    }
                )

        return {
            "kind": "semzero_memory_summary_v1",
            "schema_version": SCHEMA_VERSION,
            "run_count": runs,
            "finding_count": findings,
            "snapshot_count": snapshots,
            "model_baseline_count": models,
            "dependency_contract_count": contracts,
            "calibration_count": calibrations,
            "family_calibration": family_calibration,
        }
