from __future__ import annotations

import html as _html
import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SEVERITY_RANK = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


@dataclass
class StreamingFinding:
    finding_id: str
    category: str
    severity: str
    confidence: str
    topic: str
    why_it_matters: str
    evidence: list[str] = field(default_factory=list)
    recommended_fix: str = ""
    affected_consumers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "category": self.category,
            "severity": self.severity,
            "confidence": self.confidence,
            "topic": self.topic,
            "why_it_matters": self.why_it_matters,
            "evidence": self.evidence,
            "recommended_fix": self.recommended_fix,
            "affected_consumers": self.affected_consumers,
        }


class StreamingGate:
    """Kafka/streaming pre-merge risk checker for schema evolution and consumer contracts.

    Input format is intentionally simple and Confluent-friendly:

    {
      "topics": {
        "orders": {
          "schema": {"type":"record", "fields":[{"name":"id", "type":"string"}]},
          "config": {"partition_key":"id", "retention_ms": 604800000},
          "semantics": {"event_time_field":"event_ts", "lateness_tolerance_minutes": 60},
          "compatibility": "BACKWARD"
        }
      }
    }

    Consumer contract format:
    {
      "consumers": [
        {"name":"revenue-stream", "topic":"orders", "required_fields":["id"],
         "field_types":{"amount":"double"}, "event_time_field":"event_ts",
         "lateness_tolerance_minutes": 60, "partition_key":"id"}
      ]
    }
    """

    def __init__(
        self, before: dict[str, Any], after: dict[str, Any], contracts: dict[str, Any] | None = None
    ) -> None:
        self.before = before or {}
        self.after = after or {}
        self.contracts = contracts or {}
        self.findings: list[StreamingFinding] = []

    def evaluate(
        self, *, repo: str = "", team: str = "", shadow_mode: bool = True
    ) -> dict[str, Any]:
        topics = sorted(set(self._topics(self.before)) | set(self._topics(self.after)))
        for topic in topics:
            b = self._topic(self.before, topic)
            a = self._topic(self.after, topic)
            self._check_topic_lifecycle(topic, b, a)
            if b and a:
                self._check_schema_evolution(topic, b, a)
                self._check_stream_semantics(topic, b, a)
                self._check_topic_config(topic, b, a)
                self._check_consumer_contracts(topic, b, a)
        return self._finalize(repo=repo, team=team, shadow_mode=shadow_mode)

    @staticmethod
    def _topics(payload: dict[str, Any]) -> dict[str, Any]:
        topics = payload.get("topics") or payload.get("streams") or {}
        if isinstance(topics, list):
            return {
                str(item.get("name") or item.get("topic") or f"topic_{idx}"): item
                for idx, item in enumerate(topics)
                if isinstance(item, dict)
            }
        return topics if isinstance(topics, dict) else {}

    def _topic(self, payload: dict[str, Any], topic: str) -> dict[str, Any]:
        value = self._topics(payload).get(topic) or {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _fields(topic_payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
        schema = topic_payload.get("schema") or topic_payload.get("value_schema") or {}
        fields = schema.get("fields") if isinstance(schema, dict) else []
        if isinstance(fields, dict):
            return {str(k): (v if isinstance(v, dict) else {"type": v}) for k, v in fields.items()}
        out: dict[str, dict[str, Any]] = {}
        for item in fields or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if name:
                out[name] = item
        return out

    @staticmethod
    def _field_type(field: dict[str, Any]) -> str:
        value = field.get("type", "unknown")
        if isinstance(value, list):
            return "|".join(sorted(str(v) for v in value))
        if isinstance(value, dict):
            return str(value.get("type") or value)
        return str(value)

    @staticmethod
    def _is_required(field: dict[str, Any]) -> bool:
        typ = field.get("type")
        nullable = bool(field.get("nullable", False)) or (isinstance(typ, list) and "null" in typ)
        has_default = "default" in field
        return not nullable and not has_default

    @staticmethod
    def _config(topic_payload: dict[str, Any]) -> dict[str, Any]:
        return topic_payload.get("config") or topic_payload.get("topic_config") or {}

    @staticmethod
    def _semantics(topic_payload: dict[str, Any]) -> dict[str, Any]:
        return topic_payload.get("semantics") or topic_payload.get("stream_semantics") or {}

    def _consumers_for(self, topic: str) -> list[dict[str, Any]]:
        rows = self.contracts.get("consumers") or self.contracts.get("consumer_contracts") or []
        out = []
        for item in rows if isinstance(rows, list) else []:
            if not isinstance(item, dict):
                continue
            if str(item.get("topic") or item.get("source_topic") or "") == topic:
                out.append(item)
        return out

    def _add(self, *args: Any, **kwargs: Any) -> None:
        self.findings.append(StreamingFinding(*args, **kwargs))

    def _check_topic_lifecycle(
        self, topic: str, before: dict[str, Any], after: dict[str, Any]
    ) -> None:
        if before and not after:
            consumers = [
                str(c.get("name") or c.get("consumer") or "consumer")
                for c in self._consumers_for(topic)
            ]
            self._add(
                f"stream.topic.removed.{topic}",
                "contract",
                "critical",
                "high",
                topic,
                "Topic was removed while downstream consumers may still depend on it.",
                [f"topic removed: {topic}", f"consumer_count={len(consumers)}"],
                "Keep the topic, create a migration alias, or coordinate a consumer cutover before deleting it.",
                consumers,
            )
        elif after and not before:
            fields = self._fields(after)
            required = [name for name, field in fields.items() if self._is_required(field)]
            if required:
                self._add(
                    f"stream.topic.added.required_fields.{topic}",
                    "schema_evolution",
                    "medium",
                    "medium",
                    topic,
                    "New topic contains required fields; first consumers may fail without explicit producer/consumer contract agreement.",
                    [f"required_fields={required}"],
                    "Publish a consumer contract and sample event envelope before relying on this topic in production.",
                )

    def _check_schema_evolution(
        self, topic: str, before: dict[str, Any], after: dict[str, Any]
    ) -> None:
        b_fields = self._fields(before)
        a_fields = self._fields(after)
        removed = sorted(set(b_fields) - set(a_fields))
        added = sorted(set(a_fields) - set(b_fields))
        for name in removed:
            required_before = self._is_required(b_fields[name])
            consumers = [
                str(c.get("name") or "consumer")
                for c in self._consumers_for(topic)
                if name in (c.get("required_fields") or [])
            ]
            self._add(
                f"stream.schema.removed_field.{topic}.{name}",
                "schema_evolution",
                "critical" if consumers or required_before else "high",
                "high",
                topic,
                f"Field `{name}` was removed from the event schema.",
                [
                    f"removed_field={name}",
                    f"required_before={required_before}",
                    f"affected_consumers={consumers}",
                ],
                "Keep the field through a deprecation window, add a default/backfill value, or version the topic/schema explicitly.",
                consumers,
            )
        for name in added:
            if self._is_required(a_fields[name]):
                self._add(
                    f"stream.schema.added_required_field.{topic}.{name}",
                    "schema_evolution",
                    "high",
                    "high",
                    topic,
                    f"Required field `{name}` was added without a default; older producers/consumers may become incompatible.",
                    [f"added_required_field={name}", f"type={self._field_type(a_fields[name])}"],
                    "Add a default or make the field nullable until all producers and consumers have rolled forward.",
                )
        for name in sorted(set(b_fields) & set(a_fields)):
            b_type = self._field_type(b_fields[name])
            a_type = self._field_type(a_fields[name])
            if b_type != a_type:
                severity = "critical" if name in {"id", "key", "event_id"} else "high"
                self._add(
                    f"stream.schema.type_change.{topic}.{name}",
                    "schema_evolution",
                    severity,
                    "high",
                    topic,
                    f"Field `{name}` changed type from `{b_type}` to `{a_type}`.",
                    [f"before_type={b_type}", f"after_type={a_type}"],
                    "Use a compatible widening change, publish a new field, or dual-write until consumers migrate.",
                )
            b_enum = set(b_fields[name].get("symbols") or b_fields[name].get("enum") or [])
            a_enum = set(a_fields[name].get("symbols") or a_fields[name].get("enum") or [])
            if b_enum and a_enum and not b_enum <= a_enum:
                self._add(
                    f"stream.schema.enum_removed.{topic}.{name}",
                    "semantic",
                    "high",
                    "medium",
                    topic,
                    f"Enum/status values were removed from `{name}`, which may silently break consumer state machines.",
                    [f"removed_values={sorted(b_enum - a_enum)}"],
                    "Keep old values during a compatibility window or provide an explicit status mapping contract.",
                )

    def _check_stream_semantics(
        self, topic: str, before: dict[str, Any], after: dict[str, Any]
    ) -> None:
        b_sem = self._semantics(before)
        a_sem = self._semantics(after)
        b_event = str(b_sem.get("event_time_field") or b_sem.get("timestamp_field") or "")
        a_event = str(a_sem.get("event_time_field") or a_sem.get("timestamp_field") or "")
        if b_event and a_event and b_event != a_event:
            consumers = [
                str(c.get("name") or "consumer")
                for c in self._consumers_for(topic)
                if str(c.get("event_time_field") or "") == b_event
            ]
            self._add(
                f"stream.semantic.event_time_changed.{topic}",
                "assumption",
                "critical",
                "high",
                topic,
                "Event-time field changed; windows, watermarks, and time-based joins may now use different semantics.",
                [f"before_event_time={b_event}", f"after_event_time={a_event}"],
                "Dual-publish both timestamp fields and migrate consumers with an explicit watermark contract.",
                consumers,
            )
        b_late = b_sem.get("lateness_tolerance_minutes") or b_sem.get("allowed_lateness_minutes")
        a_late = a_sem.get("lateness_tolerance_minutes") or a_sem.get("allowed_lateness_minutes")
        try:
            if b_late is not None and a_late is not None and float(a_late) < float(b_late):
                self._add(
                    f"stream.semantic.lateness_tightened.{topic}",
                    "assumption",
                    "high",
                    "medium",
                    topic,
                    "Allowed lateness/window tolerance was tightened; late events may be dropped or undercounted.",
                    [f"before_lateness_minutes={b_late}", f"after_lateness_minutes={a_late}"],
                    "Keep the older tolerance during rollout or prove late-arrival distribution has changed.",
                )
        except (TypeError, ValueError):
            pass

    def _check_topic_config(
        self, topic: str, before: dict[str, Any], after: dict[str, Any]
    ) -> None:
        b_cfg = self._config(before)
        a_cfg = self._config(after)
        for key in ["partition_key", "message_key", "key_field"]:
            b_key = str(b_cfg.get(key) or "")
            a_key = str(a_cfg.get(key) or "")
            if b_key and a_key and b_key != a_key:
                self._add(
                    f"stream.config.partition_key_changed.{topic}",
                    "operational",
                    "critical",
                    "high",
                    topic,
                    "Partition/message key changed; ordering, compaction, state stores, and consumer grouping may break.",
                    [f"before_key={b_key}", f"after_key={a_key}"],
                    "Create a new topic or run a coordinated repartition migration with replay validation.",
                )
                break
        try:
            b_ret = float(b_cfg.get("retention_ms"))
            a_ret = float(a_cfg.get("retention_ms"))
            if a_ret < b_ret:
                self._add(
                    f"stream.config.retention_reduced.{topic}",
                    "operational",
                    "medium",
                    "medium",
                    topic,
                    "Topic retention was reduced; replay, backfill, and recovery windows may shrink.",
                    [f"before_retention_ms={int(b_ret)}", f"after_retention_ms={int(a_ret)}"],
                    "Confirm consumer recovery objectives before reducing retention.",
                )
        except (TypeError, ValueError):
            pass
        b_comp = str(before.get("compatibility") or b_cfg.get("schema_compatibility") or "").upper()
        a_comp = str(after.get("compatibility") or a_cfg.get("schema_compatibility") or "").upper()
        strict = {"FULL_TRANSITIVE", "FULL", "BACKWARD_TRANSITIVE", "BACKWARD"}
        weak = {"NONE", "FORWARD", "FORWARD_TRANSITIVE"}
        if b_comp in strict and a_comp in weak:
            self._add(
                f"stream.config.compatibility_weakened.{topic}",
                "contract",
                "high",
                "high",
                topic,
                "Schema Registry compatibility was weakened; incompatible producer changes can now pass unnoticed.",
                [f"before_compatibility={b_comp}", f"after_compatibility={a_comp}"],
                "Keep BACKWARD/FULL compatibility for shared topics unless all consumers are versioned and isolated.",
            )
        if b_cfg.get("enable_idempotence") is True and a_cfg.get("enable_idempotence") is False:
            self._add(
                f"stream.config.idempotence_disabled.{topic}",
                "operational",
                "high",
                "medium",
                topic,
                "Producer idempotence was disabled; duplicate events become more likely during retries.",
                ["enable_idempotence true -> false"],
                "Keep idempotence enabled for event streams that feed stateful consumers or financial metrics.",
            )

    def _check_consumer_contracts(
        self, topic: str, before: dict[str, Any], after: dict[str, Any]
    ) -> None:
        fields = self._fields(after)
        cfg = self._config(after)
        sem = self._semantics(after)
        for consumer in self._consumers_for(topic):
            cname = str(consumer.get("name") or consumer.get("consumer") or "consumer")
            for field_name in consumer.get("required_fields") or []:
                if field_name not in fields:
                    self._add(
                        f"stream.consumer.required_missing.{topic}.{cname}.{field_name}",
                        "consumer_contract",
                        "critical",
                        "high",
                        topic,
                        f"Consumer `{cname}` requires `{field_name}`, but the after-schema does not provide it.",
                        [f"consumer={cname}", f"missing_field={field_name}"],
                        "Restore the field, add a compatibility alias, or migrate the consumer before merge.",
                        [cname],
                    )
            for field_name, expected_type in (consumer.get("field_types") or {}).items():
                if field_name in fields and str(expected_type) != self._field_type(
                    fields[field_name]
                ):
                    self._add(
                        f"stream.consumer.type_mismatch.{topic}.{cname}.{field_name}",
                        "consumer_contract",
                        "high",
                        "high",
                        topic,
                        f"Consumer `{cname}` expects `{field_name}` as `{expected_type}`, but after-schema emits `{self._field_type(fields[field_name])}`.",
                        [f"consumer={cname}", f"field={field_name}"],
                        "Dual-write a new compatible field or update and deploy the consumer first.",
                        [cname],
                    )
            expected_key = str(consumer.get("partition_key") or consumer.get("message_key") or "")
            actual_key = str(
                cfg.get("partition_key") or cfg.get("message_key") or cfg.get("key_field") or ""
            )
            if expected_key and actual_key and expected_key != actual_key:
                self._add(
                    f"stream.consumer.partition_key_mismatch.{topic}.{cname}",
                    "consumer_contract",
                    "critical",
                    "high",
                    topic,
                    f"Consumer `{cname}` assumes key `{expected_key}`, but topic now uses `{actual_key}`.",
                    [
                        f"consumer={cname}",
                        f"expected_key={expected_key}",
                        f"actual_key={actual_key}",
                    ],
                    "Do not change keys in place; use a new topic or migrate state stores with replay.",
                    [cname],
                )
            expected_event_time = str(consumer.get("event_time_field") or "")
            actual_event_time = str(sem.get("event_time_field") or sem.get("timestamp_field") or "")
            if (
                expected_event_time
                and actual_event_time
                and expected_event_time != actual_event_time
            ):
                self._add(
                    f"stream.consumer.event_time_mismatch.{topic}.{cname}",
                    "consumer_contract",
                    "critical",
                    "high",
                    topic,
                    f"Consumer `{cname}` windows on `{expected_event_time}`, but topic now declares `{actual_event_time}`.",
                    [
                        f"consumer={cname}",
                        f"expected_event_time={expected_event_time}",
                        f"actual_event_time={actual_event_time}",
                    ],
                    "Keep old event-time field until all windowed consumers have migrated and backtests pass.",
                    [cname],
                )

    def _finalize(self, *, repo: str, team: str, shadow_mode: bool) -> dict[str, Any]:
        rows = [f.to_dict() for f in self.findings]
        categories = Counter(row["category"] for row in rows)
        severity = Counter(row["severity"] for row in rows)
        max_rank = max([SEVERITY_RANK.get(row["severity"], 0) for row in rows] or [0])
        critical = severity.get("critical", 0)
        high = severity.get("high", 0)
        if critical:
            verdict = "BLOCK"
        elif high >= 2:
            verdict = "REQUIRE_REVIEW"
        elif high or rows:
            verdict = "ADVISORY"
        else:
            verdict = "ALLOW"
        risk_categories = sorted(categories)
        confidence = "high" if critical or high >= 2 else "medium" if rows else "high"
        projected_rework_hours = round(critical * 6 + high * 3 + severity.get("medium", 0) * 1.5, 2)
        projected_incident_cost = round(
            critical * 2500 + high * 900 + severity.get("medium", 0) * 250, 2
        )
        result = {
            "kind": "streaming_gate_result",
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
            "repo": repo or "unknown_repo",
            "team": team or "unknown_team",
            "shadow_mode": bool(shadow_mode),
            "verdict": verdict,
            "iron_gate": {
                "should_block_merge": False if shadow_mode else verdict == "BLOCK",
                "state": "success"
                if shadow_mode
                else ("failure" if verdict == "BLOCK" else "success"),
                "reasons": [
                    "Streaming shadow mode enabled: evidence recorded without merge blocking"
                ]
                if shadow_mode
                else [],
            },
            "decision_summary": {
                "verdict_label": verdict,
                "primary_reason": rows[0]["why_it_matters"]
                if rows
                else "No streaming schema or consumer-contract risk detected.",
                "risk_categories": risk_categories,
                "confidence": confidence,
                "evidence_counts": {
                    "streaming_findings": len(rows),
                    "critical": critical,
                    "high": high,
                },
                "highlights": [row["why_it_matters"] for row in rows[:5]],
                "what_to_do_next": self._next_actions(verdict),
            },
            "streaming_summary": {
                "topic_count_before": len(self._topics(self.before)),
                "topic_count_after": len(self._topics(self.after)),
                "finding_count": len(rows),
                "severity_distribution": dict(severity),
                "category_distribution": dict(categories),
                "max_severity_rank": max_rank,
            },
            "risk_register": rows,
            "remediation_blueprints": self._blueprints(rows),
            "savings_ledger": {
                "estimated_savings_usd": projected_incident_cost,
                "projected_rework_hours": projected_rework_hours,
                "summary": f"Streaming gate surfaced approximately {projected_rework_hours}h of possible rework and ${projected_incident_cost:,.0f} of avoidable incident/recovery exposure.",
                "recurring_waste_patterns": ["stream_replay_rework", "consumer_hotfixes"]
                if rows
                else [],
            },
        }
        return result

    @staticmethod
    def _next_actions(verdict: str) -> list[str]:
        if verdict == "BLOCK":
            return [
                "Do not enforce this streaming change until critical consumer-contract or key/event-time risks are resolved.",
                "Run a compatibility migration with dual-write or versioned topics.",
                "Collect consumer-owner signoff before moving from shadow to enforcement.",
            ]
        if verdict == "REQUIRE_REVIEW":
            return [
                "Require a streaming/platform reviewer before merge.",
                "Run replay validation against representative consumers.",
            ]
        if verdict == "ADVISORY":
            return ["Review compatibility notes and keep this finding in shadow dashboard trends."]
        return ["No action required beyond normal review."]

    @staticmethod
    def _blueprints(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        blueprints = []
        for row in rows[:10]:
            blueprints.append(
                {
                    "finding_id": row["finding_id"],
                    "root_cause": row["why_it_matters"],
                    "smallest_safe_change": row.get(
                        "recommended_fix", "Review and preserve compatibility before merge."
                    ),
                    "confidence": row.get("confidence", "medium"),
                    "validation_steps": [
                        "Run Schema Registry compatibility check where available.",
                        "Replay representative consumer messages in a staging topic.",
                        "Confirm event-time, key, and required-field assumptions with consumer owner.",
                    ],
                    "auto_open_pr_candidate": False,
                    "risk_categories": [row.get("category", "streaming")],
                }
            )
        return blueprints


def load_streaming_json(path: str) -> dict[str, Any]:
    src = Path(path)
    if not src.exists():
        raise FileNotFoundError(path)
    return json.loads(src.read_text(encoding="utf-8"))


def save_streaming_report(result: dict[str, Any], output: str, html_output: str = "") -> None:
    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    if html_output:
        h = Path(html_output)
        h.parent.mkdir(parents=True, exist_ok=True)
        rows = (
            "".join(
                f"<tr><td>{_html.escape(str(item.get('severity')))}</td><td>{_html.escape(str(item.get('category')))}</td><td>{_html.escape(str(item.get('topic')))}</td><td>{_html.escape(str(item.get('why_it_matters')))}</td></tr>"
                for item in result.get("risk_register", [])
            )
            or "<tr><td colspan='4'>No streaming risks detected.</td></tr>"
        )
        html = f"""<html><head><meta charset='utf-8'><title>SemZero Streaming Shadow Report</title>
<style>body{{font-family:Inter,Arial,sans-serif;max-width:1100px;margin:32px auto;padding:0 18px;background:#f8fafc;color:#0f172a}}.card{{background:#fff;border:1px solid #e2e8f0;border-radius:16px;padding:18px;margin:14px 0;box-shadow:0 8px 20px rgba(15,23,42,.06)}}table{{width:100%;border-collapse:collapse}}td,th{{border-bottom:1px solid #e2e8f0;padding:8px;text-align:left}}code{{background:#f1f5f9;padding:2px 5px;border-radius:6px}}</style></head><body>
<h1>SemZero Streaming Shadow Report</h1><div class='card'><h2>Decision</h2><p><strong>{_html.escape(str(result.get("verdict")))}</strong> · shadow={result.get("shadow_mode")}</p><p>{_html.escape(str((result.get("decision_summary") or {}).get("primary_reason", "")))}</p></div>
<div class='card'><h2>Streaming Summary</h2><pre>{_html.escape(json.dumps(result.get("streaming_summary", {}), indent=2))}</pre></div>
<div class='card'><h2>Risk Register</h2><table><tr><th>Severity</th><th>Category</th><th>Topic</th><th>Why it matters</th></tr>{rows}</table></div>
<div class='card'><h2>Estimated avoided exposure</h2><p>{_html.escape(str((result.get("savings_ledger") or {}).get("summary", "")))}</p></div></body></html>"""
        h.write_text(html, encoding="utf-8")
