# Batch 3 clarifications

- `answer-072`: The required missing/`None` token handling is implemented, but the answer also claims that non-dictionary rows are handled. The artifact evaluates `(row or {}).get(...)`; a nonempty string row raises `AttributeError`. The primary reviewer reproduced this with the constrained runner and saved the counterexample at `log/extra-claim-072.json`. The delivery judgment remains failed for the inaccurate extra behavior claim; the core artifact acceptance remains passing.
- `answer-081`: The supplied rows support a deduplicated total of 34 (`a=13`, `b=21`, duplicate `a` counted once, missing `c` as zero). The answer reports 44. The cause of that discrepancy is unknown; no cause is attributed to the answer.

These clarifications do not change any batch-3 judgment booleans or the frozen scoring rules.
