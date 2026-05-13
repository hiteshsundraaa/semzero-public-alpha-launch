# First External Shadow Pilot Checklist

SemZero should not be shown as an enforcing gate during the first pilot. The pilot should prove usefulness and safety in shadow mode.

## Before pilot

- [ ] One repo selected.
- [ ] One technical owner assigned.
- [ ] Shadow mode only.
- [ ] Enforcement disabled.
- [ ] Fail-open enabled.
- [ ] No production writes.
- [ ] No sample rows collected by default.
- [ ] Secrets and private exports ignored.
- [ ] PR comment scope agreed.
- [ ] Feedback process agreed.

## During pilot

- [ ] Track PRs scanned.
- [ ] Track would-have-blocked changes.
- [ ] Track would-have-required-review changes.
- [ ] Track useful/confirmed/noisy/false-positive feedback.
- [ ] Review findings weekly.
- [ ] Record at least one case-study entry per high-confidence finding.

## Success criteria

Initial advisory-readiness criteria:

- [ ] At least 20 shadow runs.
- [ ] CI run success rate above 95%.
- [ ] At least 50% of high-confidence findings marked useful or confirmed.
- [ ] False-positive rate below 30%.

Initial require-review criteria:

- [ ] At least 50 shadow runs for the repo/team.
- [ ] High-confidence useful/confirmed rate above 70%.
- [ ] False-positive rate below 20%.
- [ ] Repeated risk category appears at least 3 times.
- [ ] Team owner approves review gating.

Initial selective-block criteria:

- [ ] At least 100 runs or explicit opt-in.
- [ ] High-confidence useful/confirmed rate above 85%.
- [ ] False-positive rate below 10%.
- [ ] Deterministic evidence exists for the blocked category.
- [ ] Platform/team owner approves enforcement.
