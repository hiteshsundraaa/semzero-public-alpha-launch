# Repo 3 Dogfood Plan

## Objective

Validate SemZero GitHub Action on a second external dbt-style repository using a controlled PR and a different assumption family from the jaffle-shop join-cardinality test.

## Required setup

- Clean branch.
- Pull request inside a repo we control.
- Known expected changed files recorded before run.
- `actions/checkout@v4` uses `fetch-depth: 0`.
- `.semzero/profiles.yml` committed if dbt compile is enabled.
- Full 40-character SemZero Action SHA pinned.

## Current SemZero Action SHA

`adaa5fee55e9f7d7cb3188994a40446c6c9ea0f3`

## Required artifact validation

Before trusting the verdict, verify:

- `changed_files.debug.txt` exists.
- `changed_files.debug.txt` contains the expected changed dbt file.
- `changed.diff` exists.
- `receipt.json` exists.
- `comment.md` exists.
- PR sticky comment matches `comment.md`.

## Repo 3 target assumption family

Preferred: enum closure drift or null fallback/default drift.

Avoid retesting join cardinality unless no suitable repo/model exists.

## Pass condition

The PR comment must be reviewer-first:

- human lead sentence
- `Review before merge`
- `Reviewer action`
- `Why it matters`
- `What triggered this`
- `Confidence`
- `Reference`
- clean slash-command calibration

## Failure condition

Any empty changed-file discovery on a PR must be treated as analysis incomplete, not a trusted no-scope result.
