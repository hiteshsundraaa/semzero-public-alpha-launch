# SemZero Core v1.10 — Dogfood Assumption Gate Fixtures

This release adds a local dogfooding fixture pack for the focused dbt Assumption Gate.

## Purpose

The goal is to make SemZero demonstrable without external services. A user can run five intentionally risky dbt PR scenarios and inspect the generated receipts, PR comments, and dashboard.

This validates the product loop, not real-world incident accuracy:

```text
changed dbt resource
→ why-now trigger evidence
→ hidden assumption finding
→ downstream blast radius
→ stable finding ID
→ evidence receipt
→ PR comment
→ assumption dashboard
```

## Added fixture path

```text
examples/dogfood_dbt_assumption_gate/
```

Important files:

```text
target/manifest.json
scenarios/scenarios.json
scenarios/*.diff
table_sizes/table_sizes.json
README.md
```

## Added runner

```bash
python scripts/run_dogfood_assumption_gate.py
```

The runner writes one receipt and comment per scenario plus an aggregate dashboard.

## Covered assumption families

- `temporal_bucket`
- `incremental_filter`
- `join_cardinality`
- `enum_domain_closure`
- `null_default_fallback`

## Guardrails

This is still core-only. The fixture does not introduce adapters, cross-domain graphing, RGCN, Chaos Mode, Streaming Gate, full Wind Tunnel, or repair automation.
