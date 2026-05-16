# SemZero Assumption Precision Evaluation

Generated: `2026-05-15T04:28:25.781888+00:00`

- Receipts scanned: **6**
- Findings evaluated: **17**
- Feedback coverage: **0.0%**
- Developer-validated findings: **0**
- Negative-feedback findings: **0**
- Missing trigger evidence: **0**
- No blast radius: **12**
- Over-broad blast radius: **0**
- Enforcement-risky findings: **0**
- Replay Lite coverage: **100.0%**
- Replay-validated drift findings: **17**
- Inferred-only findings: **0**
- Low-fidelity findings: **0**

## Precision summary

Precision calibration needed: feedback coverage is low; some findings lack downstream blast radius.

## Family precision

- `enum_domain_closure` → **continue_shadow_collect_feedback**: More feedback is needed before policy tuning. (3 finding(s))
- `temporal_bucket` → **improve_evidence_before_policy**: Some findings lack trigger evidence or downstream blast radius. (4 finding(s))
- `incremental_filter` → **improve_evidence_before_policy**: Some findings lack trigger evidence or downstream blast radius. (3 finding(s))
- `join_cardinality` → **improve_evidence_before_policy**: Some findings lack trigger evidence or downstream blast radius. (3 finding(s))
- `null_default_fallback` → **improve_evidence_before_policy**: Some findings lack trigger evidence or downstream blast radius. (3 finding(s))
- `materialization_cost` → **improve_evidence_before_policy**: Some findings lack trigger evidence or downstream blast radius. (1 finding(s))

## Finding review queue

- `AG-ENUM-DOMAIN-CLOSURE-241D2039A8` `enum_domain_closure` **replay_validated_shadow** source `finance_daily_revenue` — Prioritize human review and feedback: Replay Lite detected drift but developer validation is still needed.
- `AG-ENUM-DOMAIN-CLOSURE-1F1FCA58FB` `enum_domain_closure` **replay_validated_shadow** source `refund_adjustments` — Prioritize human review and feedback: Replay Lite detected drift but developer validation is still needed.
- `AG-ENUM-DOMAIN-CLOSURE-1F1FCA58FB` `enum_domain_closure` **replay_validated_shadow** source `refund_adjustments` — Prioritize human review and feedback: Replay Lite detected drift but developer validation is still needed.
- `AG-INCREMENTAL-FILTER-4D53B52C82` `incremental_filter` **insufficient_evidence** source `incremental_events` — Improve trigger/blast-radius evidence before treating this as policy signal.
- `AG-INCREMENTAL-FILTER-4D53B52C82` `incremental_filter` **insufficient_evidence** source `incremental_events` — Improve trigger/blast-radius evidence before treating this as policy signal.
- `AG-INCREMENTAL-FILTER-4D53B52C82` `incremental_filter` **insufficient_evidence** source `incremental_events` — Improve trigger/blast-radius evidence before treating this as policy signal.
- `AG-JOIN-CARDINALITY-BEA7A43ED4` `join_cardinality` **insufficient_evidence** source `user_revenue` — Improve trigger/blast-radius evidence before treating this as policy signal.
- `AG-JOIN-CARDINALITY-BEA7A43ED4` `join_cardinality` **insufficient_evidence** source `user_revenue` — Improve trigger/blast-radius evidence before treating this as policy signal.
- `AG-JOIN-CARDINALITY-BEA7A43ED4` `join_cardinality` **insufficient_evidence** source `user_revenue` — Improve trigger/blast-radius evidence before treating this as policy signal.
- `AG-NULL-DEFAULT-FALLBACK-007ABB85B7` `null_default_fallback` **replay_validated_shadow** source `discount_metrics` — Prioritize human review and feedback: Replay Lite detected drift but developer validation is still needed.
- `AG-TEMPORAL-BUCKET-4B8BE3AAF0` `temporal_bucket` **replay_validated_shadow** source `finance_daily_revenue` — Prioritize human review and feedback: Replay Lite detected drift but developer validation is still needed.
- `AG-TEMPORAL-BUCKET-9D6AD574D3` `temporal_bucket` **insufficient_evidence** source `incremental_events` — Improve trigger/blast-radius evidence before treating this as policy signal.
- `AG-TEMPORAL-BUCKET-9D6AD574D3` `temporal_bucket` **insufficient_evidence** source `incremental_events` — Improve trigger/blast-radius evidence before treating this as policy signal.
- `AG-TEMPORAL-BUCKET-9D6AD574D3` `temporal_bucket` **insufficient_evidence** source `incremental_events` — Improve trigger/blast-radius evidence before treating this as policy signal.
- `AG-MATERIALIZATION-COST-439A2EF48A` `materialization_cost` **insufficient_evidence** source `incremental_events` — Improve trigger/blast-radius evidence before treating this as policy signal.

## Guardrail

Advisory-only. This report identifies noisy/over-broad findings before stricter policy; it does not block CI or mutate policy.
