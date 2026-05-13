from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

REVIEW_VERDICTS = {"REQUIRE_REVIEW", "BLOCK"}
HIGH_SEVERITIES = {"high", "critical"}


def parse_dt(value: Any) -> datetime | None:
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
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def age_days(value: Any, *, path: str | None = None, now: datetime | None = None) -> float | None:
    now = now or datetime.now(timezone.utc)
    dt = parse_dt(value)
    if dt is None and path:
        try:
            dt = datetime.fromtimestamp(Path(path).stat().st_mtime, tz=timezone.utc)
        except Exception:
            dt = None
    if dt is None:
        return None
    return max(0.0, (now - dt).total_seconds() / 86400.0)


def freshness_state(
    days: float | None, *, stale_after_days: int = 30, review_after_days: int = 14
) -> str:
    if days is None:
        return "unknown"
    if days > stale_after_days:
        return "stale"
    if days > review_after_days:
        return "review_due"
    return "fresh"


def receipt_freshness(
    receipt: dict[str, Any],
    *,
    now: datetime | None = None,
    stale_after_days: int = 30,
    review_after_days: int = 14,
) -> dict[str, Any]:
    path = str(receipt.get("_path") or "") or None
    generated_at = (
        receipt.get("generated_at") or receipt.get("created_at") or receipt.get("timestamp")
    )
    days = age_days(generated_at, path=path, now=now)
    state = freshness_state(
        days, stale_after_days=stale_after_days, review_after_days=review_after_days
    )
    return {
        "state": state,
        "age_days": round(days, 2) if days is not None else None,
        "generated_at": generated_at,
        "path": path or "",
        "review_after_days": review_after_days,
        "stale_after_days": stale_after_days,
    }


def finding_review_state(
    finding: dict[str, Any],
    receipt: dict[str, Any],
    feedback_rows: Iterable[dict[str, Any]],
    exception: dict[str, Any] | None = None,
    *,
    now: datetime | None = None,
    high_risk_review_after_days: int = 14,
) -> dict[str, Any]:
    freshness = receipt_freshness(receipt, now=now, review_after_days=high_risk_review_after_days)
    rows = list(feedback_rows or [])
    severity = str(finding.get("severity") or "unknown").lower()
    risk_score = int(finding.get("risk_score") or 0)
    biz = finding.get("business_impact") or {}
    biz_sev = str(biz.get("highest_business_severity") or "UNKNOWN")
    exception_state = str((exception or finding.get("exception") or {}).get("state") or "none")
    high_risk = (
        severity in HIGH_SEVERITIES
        or risk_score >= 80
        or biz_sev in {"BOARD_CRITICAL", "EXEC_CRITICAL", "REVENUE_CRITICAL"}
    )
    has_feedback = bool(rows)
    needs_review = bool(high_risk and not has_feedback and exception_state != "active_exception")
    stale_unreviewed = bool(needs_review and freshness.get("state") in {"review_due", "stale"})
    return {
        "kind": "semzero_finding_review_state_v1",
        "freshness_state": freshness.get("state"),
        "receipt_age_days": freshness.get("age_days"),
        "high_risk": high_risk,
        "has_feedback": has_feedback,
        "exception_state": exception_state,
        "needs_review": needs_review,
        "stale_unreviewed": stale_unreviewed,
        "reason": _finding_review_reason(
            high_risk, has_feedback, exception_state, freshness.get("state")
        ),
        "guardrail": "Freshness review is advisory-only. It highlights stale/unreviewed risk; it does not block merges.",
    }


def _finding_review_reason(
    high_risk: bool, has_feedback: bool, exception_state: str, state: str | None
) -> str:
    if not high_risk:
        return "Finding is not currently high-risk enough to require freshness review."
    if exception_state == "active_exception":
        return "Finding is covered by an active accepted-risk exception; review when the exception nears expiry."
    if has_feedback:
        return "Finding has developer feedback; keep it in calibration history."
    if state in {"review_due", "stale"}:
        return "High-risk finding has no feedback and its receipt is no longer fresh; review before relying on it for policy calibration."
    return "High-risk finding has no feedback yet; collect reviewer disposition during shadow/advisory mode."
