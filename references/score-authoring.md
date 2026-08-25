# Authoring a scorer

The scorer defines the problem. AVO's worktrees, ledger, ratchet, memory, and supervisor are mechanical
once the evaluation contract is reliable.

## Contract

```text
score-command <candidate-dir>
```

Print exactly one JSON object. Exit 0 when evaluation completed, whether correctness passed or failed.
Exit nonzero only when evaluation itself could not run.

```json
{
  "correct": true,
  "objective": 0.873,
  "metrics": {"latency_ms": 12.3, "stddev": 0.4},
  "note": "branchless rescale removed a blocking fence",
  "artifacts": ["profile.txt"]
}
```

## Rules

### Separate the gate from the metric

Compute `correct` from tests, a reference implementation, invariants, or another real oracle. If it is
false, return immediately when possible:

```json
{"correct":false,"objective":null,"metrics":{},"note":"property test failed"}
```

Rank mode requires a numeric objective only for correct candidates.

### Use one higher-is-better scalar

Negate lower-is-better quantities:

```text
objective = -latency_ms
objective = -cost_usd
```

Keep diagnostic dimensions in `metrics`; the ratchet compares only `objective`.

### Quantify measurement noise

Average repeated runs when practical and report `metrics.stddev`. AVO requires:

```text
candidate > best + max(min_improvement_abs, stddev)
```

This avoids accepting wins inside ordinary run-to-run variation.

### Make the note useful to the next model

Prefer:

```text
batched metadata writes; +7% on large cases, unchanged small cases
```

not:

```text
objective=0.873
```

### Preserve evidence

List relative artifact paths under the candidate directory. AVO copies regular files into the run
directory before removing the worktree.

## Rank mode

Use a stable metric with meaningful ordering. The first correct candidate seeds an incorrect or
unscored baseline. A no-op or regression is rejected without changing canonical Git state.

## Discover mode

Use `correct=true` for a validated resolved item. That may be:

- a confirmed positive finding;
- a decisively disproved hypothesis;
- a completed test class with an honest negative result.

When helpful, use `objective` as monotone validated coverage. Do not count only positive findings or
the loop will learn to avoid useful falsification. Pair soft discovery scorers with an adversarial
verifier. If shrinkage is a contract violation, set `correct=false`; AVO will not reject a smaller
objective in discover mode.

## Verifier contract

```text
verify-command <candidate-dir> <score-json-path>
```

```json
{"pass":false,"note":"competing control reproduces the effect","evidence":["control.json"]}
```

Exit 0 means a verdict was reached. Nonzero means infrastructure failure.

## Common scorer shapes

- test suite + benchmark;
- candidate output compared with a reference oracle;
- accuracy/eval score with hard format and safety checks;
- monotone resolved-hypothesis coverage;
- rubric judge only when no mechanical objective exists.

Keep the evaluator automated, reasonably fast, non-destructive, and deterministic enough for many
unattended invocations.
