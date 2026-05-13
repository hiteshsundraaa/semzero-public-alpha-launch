from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

VALID_SCOPES = {"stable_id", "family", "source", "receipt", "global"}
VALID_STATUSES = {"active", "expired", "invalid"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


@dataclass(slots=True)
class AssumptionExceptionRecord:
    """Auditable exception/suppression record for Assumption Gate findings.

    Exceptions are intentionally advisory-first. They do not delete findings from
    receipts; they annotate them as suppressed/accepted with explicit reason and
    optional expiry so dashboards can track exception debt.
    """

    scope: str
    value: str
    reason: str
    owner: str = ""
    expires_at: str = ""
    action: str = "suppress"
    created_at: str = ""
    created_by: str = ""
    ticket: str = ""
    metadata: dict[str, Any] | None = None

    def normalized(self) -> "AssumptionExceptionRecord":
        scope = (self.scope or "stable_id").strip().lower().replace("-", "_")
        if scope not in VALID_SCOPES:
            raise ValueError(
                f"Unsupported exception scope: {self.scope!r}. Expected one of: {', '.join(sorted(VALID_SCOPES))}"
            )
        if not (self.reason or "").strip():
            raise ValueError("Exception reason is required.")
        if not (self.value or "").strip() and scope != "global":
            raise ValueError("Exception value is required unless scope=global.")
        return AssumptionExceptionRecord(
            scope=scope,
            value=(self.value or "").strip(),
            reason=self.reason.strip(),
            owner=self.owner.strip(),
            expires_at=self.expires_at.strip(),
            action=(self.action or "suppress").strip().lower().replace("-", "_"),
            created_at=self.created_at or _now_iso(),
            created_by=self.created_by.strip(),
            ticket=self.ticket.strip(),
            metadata=self.metadata or {},
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self.normalized())
        payload["status"] = exception_status(payload)
        if not payload.get("metadata"):
            payload.pop("metadata", None)
        return payload


def exception_status(record: dict[str, Any], now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    if not record.get("reason"):
        return "invalid"
    expires_at = _parse_dt(record.get("expires_at"))
    if expires_at and expires_at < now:
        return "expired"
    return "active"


def append_exception(
    record: AssumptionExceptionRecord, exceptions_file: str | Path
) -> dict[str, Any]:
    path = Path(exceptions_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = record.to_dict()
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")
    return payload


def load_exceptions(exceptions_file: str | Path | None) -> list[dict[str, Any]]:
    if not exceptions_file:
        return []
    path = Path(exceptions_file)
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        payload["scope"] = (
            str(payload.get("scope") or "stable_id").strip().lower().replace("-", "_")
        )
        payload["status"] = exception_status(payload)
        rows.append(payload)
    return rows


def match_exception(
    finding: dict[str, Any], receipt_key: str, records: Iterable[dict[str, Any]]
) -> dict[str, Any]:
    """Return advisory exception status for a finding.

    Active exceptions suppress policy/action emphasis but do not remove evidence.
    Expired matches are returned so dashboards can flag exception debt.
    """
    stable_id = str(finding.get("stable_id") or finding.get("id") or "")
    legacy_id = str(finding.get("legacy_id") or "")
    fingerprint = str(finding.get("fingerprint") or "")
    family = str(finding.get("family") or "")
    source = finding.get("source") or {}
    source_id = str(source.get("unique_id") or finding.get("source_resource") or "")
    source_path = str(source.get("path") or finding.get("source_path") or "")
    matches: list[dict[str, Any]] = []
    expired: list[dict[str, Any]] = []
    for row in records:
        scope = str(row.get("scope") or "stable_id")
        value = str(row.get("value") or "")
        status = str(row.get("status") or exception_status(row))
        ok = False
        if scope == "global":
            ok = True
        elif scope == "stable_id":
            ok = value in {stable_id, legacy_id, fingerprint}
        elif scope == "family":
            ok = value == family
        elif scope == "source":
            ok = value in {source_id, source_path}
        elif scope == "receipt":
            ok = value == receipt_key
        if not ok:
            continue
        projected = {
            "scope": scope,
            "value": value,
            "status": status,
            "reason": row.get("reason", ""),
            "owner": row.get("owner", ""),
            "expires_at": row.get("expires_at", ""),
            "ticket": row.get("ticket", ""),
            "action": row.get("action", "suppress"),
        }
        if status == "active":
            matches.append(projected)
        elif status == "expired":
            expired.append(projected)
    state = "none"
    if matches:
        state = "active_exception"
    elif expired:
        state = "expired_exception"
    return {
        "kind": "semzero_assumption_exception_match_v1",
        "state": state,
        "active": matches,
        "expired": expired,
        "advisory_note": "Exceptions annotate findings for calibration; they do not delete evidence or create hard blocks.",
    }


def summarize_exceptions(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(records)
    by_status: dict[str, int] = {}
    by_scope: dict[str, int] = {}
    expired_soon = 0
    now = datetime.now(timezone.utc)
    for row in rows:
        status = str(row.get("status") or exception_status(row))
        by_status[status] = by_status.get(status, 0) + 1
        scope = str(row.get("scope") or "stable_id")
        by_scope[scope] = by_scope.get(scope, 0) + 1
        exp = _parse_dt(row.get("expires_at"))
        if status == "active" and exp and 0 <= (exp - now).days <= 14:
            expired_soon += 1
    return {
        "exception_count": len(rows),
        "active_exception_count": by_status.get("active", 0),
        "expired_exception_count": by_status.get("expired", 0),
        "invalid_exception_count": by_status.get("invalid", 0),
        "expiring_within_14_days_count": expired_soon,
        "status_counts": dict(sorted(by_status.items())),
        "scope_counts": dict(sorted(by_scope.items())),
        "guardrail": "Exceptions are accepted-risk/suppression records with reasons and optional expiry; they are advisory and auditable, not silent deletion.",
    }
