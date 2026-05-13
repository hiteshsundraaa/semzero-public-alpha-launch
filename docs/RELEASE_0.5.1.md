# SemZero 0.5.1

This release is a verification and packaging pass on top of 0.5.0.

## What stayed true
- Tier 1: daily-use command surface, composite receipts, and fix/report guidance remain in place
- Tier 2: row-level mismatch previews and replay fidelity remain wired into Wind Tunnel
- Tier 3: override and incident ledgers remain wired into premerge bundles

## Fresh verification performed
- full suite rerun in the packaged repo
- demo-loop rerun with `semzero validate-e2e` on the built-in large messy SQLite validation pack
- fresh receipt renders generated from the new premerge bundle

## Validation summary
- full suite: 157/157 passed
- gate verdict: BLOCK
- queries replayed: 18
- broken queries: 1
- prediction alignment: 10/10
- chaos grade: A

## Important honesty note
This verification pass still does **not** claim true live Snowflake or Databricks execution in this environment. Those paths remain code-wired and ready, but real warehouse validation still requires customer or trial credentials.
