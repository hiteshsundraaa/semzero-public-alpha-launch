# SemZero 0.7.0 — Merge Verdict, Assumption Gate, and Blue-Ocean Pre-Merge Control

0.7.0 pushes SemZero further away from a generic diff tool and closer to the product category it is actually building:

**a pre-merge reliability, semantics, and cost-control gate for data systems.**

## What changed

### 1. Merge gate is even more clearly the product
- Gate receipts now carry richer execution guidance and explicit merge-time reasoning.
- Reliability scoring now accounts for undocumented downstream assumptions in addition to structural, runtime, and FinOps risk.
- Unified reports surface the gate, AST proof, undocumented assumptions, Wind Tunnel, Chaos, and ROI in one operator-facing runbook.

### 2. Semantic-break engine was deepened
- Semantic-break detection now treats severe grain/cardinality expansion or collapse as dangerous.
- Large null-rate spikes are treated as semantic break signals, not only quality drift.
- Timezone boundary changes are treated as semantic break signals.
- Enum-like/domain-like sample flips and temporal sample shifts now contribute to semantic break classification.

### 3. Assumption Gate added
- New `semzero.integrations.assumption_gate` module.
- Extracts undocumented downstream assumptions from proof sources and code surfaces.
- Detects assumptions such as:
  - domain/status filtering assumptions
  - join-cardinality / merge-key assumptions
  - temporal bucketing and completeness assumptions
  - incremental / retained-state assumptions
  - null-handling assumptions
- Findings are attached to the gate result and included in merge recommendations, next actions, and unified reports.

### 4. Compiler-grade lineage and blast-radius proofing now drive assumption-aware triage
- Proof-source assumptions are attached to affected nodes.
- Affected nodes with assumption risk become stronger candidates for scoped Wind Tunnel replay and rollout caution.

### 5. Targeted Wind Tunnel is pulled in by assumption risk
- Assumption-heavy changes now force replay even when a plain structural diff might otherwise look less urgent.
- Execution plans include assumption revalidation and assumption-type summaries.

### 6. Pre-merge FinOps gate remains first-class
- FinOps receipts continue to flow into the gate result and reports.
- 0.7.0 validation runs confirmed end-to-end FinOps outputs on black-swan, chaos-labyrinth, and messy profiles.

## Validation performed

### Full automated suite
- `pytest -q`
- Result: **177 passed**

### End-to-end validation packs
Executed through `semzero validate-e2e` on medium demo packs:
- `black_swan`
- `chaos_labyrinth`
- `messy`

Validation artifacts are stored under:
- `validation_artifacts/run_0.7.0_merge_semantic_assumption/`

Observed in these runs:
- aligned predictions: 10/10 across all three profiles
- assumption findings surfaced end to end
- FinOps receipts populated end to end
- Wind Tunnel triggered end to end
- Chaos recovery remained recoverable in validation

## Why this release matters

0.7.0 is a category-shaping release.

It makes SemZero better at answering the question that a blue-ocean pre-merge startup must own:

**Should this data change be allowed to merge, given semantic risk, downstream tribal knowledge, runtime breakage risk, and compute-cost consequences?**
