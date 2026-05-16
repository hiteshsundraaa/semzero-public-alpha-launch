# SemZero Action Wrapper Dogfood Postmortem

## Executive framing: SemZero's real product contract

SemZero is not just a detector. It is a trust gate.

That means the product must always distinguish between three states:

```text
1. I found no risk.
2. I found risk.
3. I could not inspect enough to know.
