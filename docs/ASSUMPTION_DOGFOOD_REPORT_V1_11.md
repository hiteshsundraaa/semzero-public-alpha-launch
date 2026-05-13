# SemZero Core v1.11 — Dogfood Demo Report

This release stays core-only. It does not add Terraform/Kubernetes adapters, cross-domain graphing, RGCN, Chaos Mode, Streaming Gate, full Wind Tunnel, or repair automation.

## What changed

v1.11 adds a product-demo reporting layer for the focused dbt Assumption Gate dogfood pack.

The dogfood run now produces:

- one receipt per risky PR scenario
- one PR-ready comment per risky PR scenario
- an assumption dashboard JSON/Markdown pair
- a dogfood run summary
- a product-demo report JSON/Markdown pair

## New outputs

After running:

```bash
python scripts/run_dogfood_assumption_gate.py
```

SemZero writes:

```text
examples/dogfood_dbt_assumption_gate/dogfood_demo_report.json
examples/dogfood_dbt_assumption_gate/dogfood_demo_report.md
```

The report shows:

- product loop demonstrated
- scope guardrail
- scenario pass/fail status
- assumption family coverage
- top evidence snippets
- dashboard summary
- cost exposure surfaced
- calibration posture
- rerun command

## New command

```bash
semzero assumption-dogfood-report \
  --dogfood-dir examples/dogfood_dbt_assumption_gate \
  --output examples/dogfood_dbt_assumption_gate/dogfood_demo_report.json \
  --markdown-output examples/dogfood_dbt_assumption_gate/dogfood_demo_report.md
```

This command rebuilds the product-demo report from existing dogfood receipts and dashboard outputs.

## Why this matters

The dogfood fixture already proved that the five core assumption families can be exercised locally. v1.11 turns that into a cleaner demo artifact that shows the entire focused SemZero loop:

```text
dbt PR diff
→ hidden assumption finding
→ blast radius
→ typed receipt
→ PR-ready comment
→ dashboard calibration
```

This improves demo readiness without expanding scope.
