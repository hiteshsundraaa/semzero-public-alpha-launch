# SemZero Dogfood Fix Log

This file records dogfood failures, fixes, verification, and product impact.

## Fix 003 — Add first-class GitHub Action wrapper

Date: 2026-05-15  
SemZero version: 0.8.1-alpha-dev  
SemZero commit before fix: `96d245eb046c`

### Problem

The alpha dogfood setup still required users to wire `SEMZERO_PACKAGE_SPEC` or install SemZero from a fork/source path. That is not acceptable product onboarding.

### User-visible symptoms

- Users had to configure a package source before SemZero could run.
- New repos required repeated YAML and variable setup.
- The setup felt like an internal CLI/tooling workflow instead of a native PR-review product.

### Root cause

SemZero did not yet expose a GitHub Action surface. The CLI existed, but users had to manually install and invoke it inside their own workflow.

### Fix

- Added root `action.yml`.
- The action installs SemZero from the pinned action checkout using `$GITHUB_ACTION_PATH`.
- The action accepts common inputs:
  - `mode`
  - `policy-path`
  - `manifest-path`
  - `artifact-dir`
  - `extra-pip-packages`
  - `dbt-profiles-dir`
  - `run-dbt-compile`
  - `post-pr-comment`
- The action attempts dbt compile, runs SemZero, uploads artifacts, and posts/updates the sticky PR comment.

### Verification required

- Commit and push this patch.
- Pin the action from a real dbt repo using a commit SHA or tag.
- Confirm the repo can run SemZero without `SEMZERO_PACKAGE_SPEC`.
- Confirm a sticky PR comment appears.
- Confirm evidence artifacts upload.

### Product impact

This removes the need for users to fork SemZero or manually set a package-source variable. It moves SemZero toward the intended product surface:

```yaml
- uses: semzero/assumption-gate-action@v1
```

### Remaining limitations

- Users may still need dbt adapter packages for manifest-backed analysis.
- Static fallback still needs to become first-class so missing profiles/adapters do not block first value.
- The action should eventually live in a dedicated `semzero/assumption-gate-action` repo or be published under an official org.

### Action wrapper test note — CLI option mismatch

During the first GitHub Action wrapper test, the action resolved correctly and installed SemZero, but failed at runtime:

```text
Error: No such option: --repo
Error: No such option: --repo
eof

### Action wrapper test note — output directory option mismatch

The second GitHub Action wrapper test reached `semzero assumption-ci` but failed:

```text
Error: No such option: --artifact-dir (Possible options: --output-dir, --project-dir)

### Action wrapper test note — manifest option mismatch

The third GitHub Action wrapper test reached `semzero assumption-ci` but failed:

```text
Error: No such option: --manifest Did you mean --dbt-manifest?

### Action wrapper test note — manifest option mismatch

The third GitHub Action wrapper test reached `semzero assumption-ci` but failed:

```text
Error: No such option: --manifest Did you mean --dbt-manifest?
## 3. Commit, push, get the new full SHA

```bash
git add action.yml docs/dogfood/FIXLOG.md
git commit -m "Use dbt manifest option in GitHub Action wrapper"
git push origin main
git rev-parse HEAD

### Action wrapper test note — blank comment artifact

The GitHub Action wrapper reached success, uploaded artifacts, but no sticky PR comment appeared because `comment.md` was blank.

Root cause:
The wrapper assumed successful `semzero assumption-ci` execution always produced a non-empty `comment.md`. That assumption was false.

Fix:
Added a `Normalize SemZero comment artifact` step that:
- lists artifact contents,
- ensures `comment.md` exists,
- creates a diagnostic fallback comment if SemZero produces a blank/missing comment file.

Product lesson:
The PR comment is the core product surface. A successful CI run with no visible PR comment is still a product failure. The Action wrapper must never silently succeed without a user-visible comment or diagnostic.

### Action wrapper test note — YAML heredoc indentation broke action manifest

The fallback-comment patch made `action.yml` invalid:

```text
While scanning an anchor or alias, did not find expected alphabetic or numeric character.
