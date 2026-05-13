"""
slack.py — Slack webhook alerts for schema drift events.

Sends rich Block Kit messages to Slack when drift is detected.
CRITICAL events trigger immediate @channel pings.

Usage:
    from semzero.integrations.slack import SlackAlerter
    alerter = SlackAlerter(webhook_url="https://hooks.slack.com/...")
    alerter.send_drift_alert(drift_report, pr_url="https://github.com/...")
"""

from __future__ import annotations

import logging
from typing import Optional

import requests

log = logging.getLogger(__name__)

_SEVERITY_EMOJI = {
    "CRITICAL": "🔴",
    "HIGH": "🟠",
    "MEDIUM": "🟡",
    "LOW": "🟢",
}

_TIMEOUT = 10


class SlackAlerter:
    def __init__(self, webhook_url: str, channel: str = "#data-alerts"):
        self.webhook_url = webhook_url
        self.channel = channel

    def send_drift_alert(
        self,
        drift_report: dict,
        pr_url: Optional[str] = None,
        report_url: Optional[str] = None,
    ) -> bool:
        """
        Sends a rich Slack message for a drift report.
        Returns True if message was sent successfully.
        """
        if not self.webhook_url:
            log.debug("Slack webhook not configured — skipping alert.")
            return False

        summary = drift_report.get("summary", {})
        events = drift_report.get("events", [])
        by_sev = summary.get("by_severity", {})
        total = summary.get("total_changes", 0)
        detected = drift_report.get("detected_at", "")[:19]

        has_critical = by_sev.get("CRITICAL", 0) > 0
        has_high = by_sev.get("HIGH", 0) > 0

        # Header text
        if has_critical:
            header = f"🚨 *CRITICAL Schema Drift Detected* — {total} changes"
            color = "#e53e3e"
            ping = "<!channel> "
        elif has_high:
            header = f"⚠️ *Schema Drift Detected* — {total} changes"
            color = "#dd6b20"
            ping = ""
        else:
            header = f"ℹ️ *Schema Changes Detected* — {total} changes"
            color = "#3182ce"
            ping = ""

        # Severity summary line
        sev_parts = [
            f"{_SEVERITY_EMOJI[s]} {count} {s}"
            for s, count in [
                ("CRITICAL", by_sev.get("CRITICAL", 0)),
                ("HIGH", by_sev.get("HIGH", 0)),
                ("MEDIUM", by_sev.get("MEDIUM", 0)),
                ("LOW", by_sev.get("LOW", 0)),
            ]
            if count > 0
        ]
        sev_line = "  ·  ".join(sev_parts)

        # Event list (top 5)
        event_lines = []
        for e in events[:5]:
            emoji = _SEVERITY_EMOJI.get(e["severity"], "⚪")
            event_lines.append(f"{emoji} `{e['node_id']}` — {e['change_type']}: {e['detail']}")
        if len(events) > 5:
            event_lines.append(f"_...and {len(events) - 5} more changes_")
        events_text = "\n".join(event_lines)

        # Action buttons
        actions = []
        if pr_url:
            actions.append(
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "📋 View PR"},
                    "url": pr_url,
                    "style": "primary" if has_critical else "default",
                }
            )
        if report_url:
            actions.append(
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "📊 Full Report"},
                    "url": report_url,
                }
            )

        # Build Block Kit message
        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "SemZero — Schema Intelligence"},
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"{ping}{header}\n{sev_line}"},
            },
            {"type": "divider"},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Changes detected at* `{detected}` UTC\n\n{events_text}",
                },
            },
        ]

        if actions:
            blocks.append(
                {
                    "type": "actions",
                    "elements": actions,
                }
            )

        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"SemZero Autonomous Schema Intelligence  ·  {self.channel}",
                    }
                ],
            }
        )

        payload = {
            "channel": self.channel,
            "attachments": [{"color": color, "blocks": blocks}],
        }

        try:
            r = requests.post(
                self.webhook_url,
                json=payload,
                timeout=_TIMEOUT,
            )
            if r.status_code == 200:
                log.info(f"Slack alert sent ({total} changes).")
                return True
            else:
                log.warning(f"Slack alert failed: {r.status_code} {r.text}")
                return False
        except Exception as e:
            log.error(f"Slack alert exception: {e}")
            return False

    def send_repair_complete(
        self,
        repair_plan: dict,
        pr_url: Optional[str] = None,
    ) -> bool:
        """Sends a concise confirmation when a repair PR is opened."""
        if not self.webhook_url:
            return False

        summary = repair_plan.get("summary", {})
        auto = summary.get("auto_executable", 0)
        review = summary.get("needs_approval", 0)
        total = summary.get("total_actions", 0)

        text = (
            f"✅ *SemZero repair PR opened* — {total} actions\n"
            f"🟢 {auto} auto-executable  ·  ⚠️ {review} need review"
        )
        if pr_url:
            text += f"\n<{pr_url}|View Pull Request>"

        payload = {
            "channel": self.channel,
            "text": text,
        }

        try:
            r = requests.post(self.webhook_url, json=payload, timeout=_TIMEOUT)
            return r.status_code == 200
        except Exception as e:
            log.error(f"Slack repair notification failed: {e}")
            return False

    def send_clean(self) -> bool:
        """Sends a brief ✓ message when no drift is detected."""
        if not self.webhook_url:
            return False
        payload = {
            "channel": self.channel,
            "text": "✅ *SemZero* — Schema check passed. No drift detected.",
        }
        try:
            r = requests.post(self.webhook_url, json=payload, timeout=_TIMEOUT)
            return r.status_code == 200
        except Exception:
            return False
