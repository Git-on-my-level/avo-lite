# Authoring a score command (`f`)

The scorer is the whole game. Everything else (lineage, ratchet, supervisor) is mechanical once `f` is
right. `avo init` refuses without one.

## The contract

`$AVO_SCORE_CMD <candidate-dir>` prints **one** JSON object to stdout and:
- exits **0** when the evaluation *completed* — whether the candidate passed or failed correctness;
- exits **non-zero** only on **infra failure** (compiler missing, network down). A non-zero exit is
  recorded as a tick `error`, never a correctness fail, and does not increment the stall counter — so
  transient flakes never poison the lineage.

```json
{
  "correct":   true,
  "objective": 0.873,
  "metrics":   {"latency_ms": 12.3, "stddev": 0.4},
  "note":      "branchless rescale; removed blocking fence",
  "artifacts": ["profile.txt"]
}
```

Validated against `scripts/lib/score-schema.json`. Only `correct` is strictly required; `objective` is
required (a number) in `rank` mode.

## Design rules

1. **Separate the gate from the metric.** Compute `correct` first, from a real oracle (a reference
   implementation, a test suite, an invariant check). Never fold correctness into the quality number.
   If `correct` is false, `objective` is ignored — don't waste an expensive benchmark on a broken candidate.

2. **Pick one scalar `objective`, higher-is-better.** If your natural metric is lower-is-better
   (latency, cost), negate it (`objective = -latency_ms`). The vector goes in `metrics` for context and
   dashboards; the ratchet only ever compares the one scalar. Do not try to encode a Pareto front here —
   that is deliberately out of scope.

3. **Average and report `stddev` when the metric is noisy.** The ratchet's accept margin is
   `max(min_improvement_abs, 1·stddev)`, so a scorer that reports `stddev` automatically refuses to
   accept "wins" inside measurement noise. The AVO paper averages 10 runs for exactly this reason.
   If you can't average, set a conservative `min_improvement_abs` in `avo.toml`.

4. **Make `note` a one-liner the next agent will act on.** It is fed forward as lineage context. "value=96"
   is useless; "removed blocking fence on unmasked path, +8% non-causal" teaches the next iteration.

5. **Keep `f` fast, deterministic-ish, non-destructive, and fully automated.** It runs hundreds of times
   unattended. If a single eval is minutes-to-hours or costs real money (cloud quota), widen the driver
   cadence rather than making `f` cut corners.

## rank vs discover

- **rank** (optimization): `objective` is your real metric. The ratchet keeps only above-noise gains.
  Example: `adapters/score-ci.sh` — correctness = a test suite passes, objective = a benchmark number.
- **discover** (sparse reward, security-research-shaped): `objective` is a **monotone coverage counter**
  (hypotheses tested, findings recorded), not a quality score; every correct candidate is appended.
  Correctness here means "a real, non-hallucinated finding". **Pair with `--verify`** so an adversarial
  falsification pass drops bogus findings before they enter the lineage. Example: `adapters/score-discover.sh`.

## Common scorer shapes

- **Test-suite wrapper** (`score-ci.sh`): `correct = (make test exits 0)`, `objective = extract a
  number from a bench command`. Point `AVO_CORRECT_CMD` / `AVO_METRIC_CMD` at your repo's commands.
- **Reference-oracle numeric**: run candidate and a reference on the same inputs; `correct = outputs
  match within tolerance`; `objective = throughput/accuracy`.
- **Coverage counter** (`score-discover.sh`): `correct = latest finding parses and isn't marked
  hallucinated`; `objective = count of recorded findings`.
- **LLM-judge** (last resort, when no mechanical metric exists): a cheap model scores the candidate
  against a rubric. Prefer a mechanical `f`; a judge is softer and slower, but still valid if it is
  automated and reasonably stable. Keep the *correctness* half mechanical even when the *quality* half
  is judged.
