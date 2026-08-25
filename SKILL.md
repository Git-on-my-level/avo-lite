# AVO-lite skill

Use AVO-lite when an agent can alter a project and an automated evaluator can determine whether the
result is valid and better. The domain may be software performance, model prompts, research coverage,
configuration search, or another problem with a repeatable evaluation contract.

## Core mental model

One tick is one transaction:

```text
current canonical commit
        ↓
disposable Git worktree
        ↓
driver agent owns its edit/test/revise loop
        ↓
score: correctness gate + objective
        ↓
would this candidate enter the lineage?
        ↓ yes
optional adversarial verification
        ↓
apply one patch to canonical branch and commit, or discard worktree
        ↓
append evidence to ledger and update compact context
```

The scaffold should manage persistence and transitions, not micromanage the agent's internal turns.

## Required invariants

1. **Correctness is separate from quality.** A wrong candidate never wins because of a strong metric.
2. **Only accepted candidates modify the canonical checkout.** Each attempt runs in a disposable
   worktree under `.avo/runs/`.
3. **The lineage is monotone in rank mode.** A candidate must beat the current best by more than the
   configured noise margin.
4. **Every attempt is remembered.** Accepts, rejects, verifier verdicts, and infrastructure errors
   enter the raw JSONL ledger.
5. **History is retrieved, not merely stored.** Recent successes and failures, memory, pins, and any
   redirect are included in the next prompt.
6. **Adversarial verification occurs before trust.** It is optional, but when configured it runs only
   for would-be winners.
7. **Stagnation is detected mechanically.** A supervisor is an infrequent redirect mechanism, not the
   main loop.
8. **Human steering persists.** Pins remain authoritative until explicitly removed.

## Initialize

From a Git repository:

```bash
avo init <task> \
  --goal "the precise higher-is-better objective" \
  --score ./score.sh \
  --agent ./agent.sh \
  [--verify ./verify.sh] \
  [--supervisor ./supervisor.sh] \
  [--mode rank|discover] \
  [--k ./reference-material]
```

AVO creates `avo/<task>`, commits baseline v0, evaluates it, and stores local state in `.avo/` using
`.git/info/exclude`. `--agent` is required. `AVO_HOME=/another/path` relocates that task state when
set consistently.

`--k` copies host-side reference material into `.avo/knowledge/`. Each candidate worktree receives a
copy at `.avo/knowledge/`; it is not committed.

If `--score` is given, a malformed baseline score fails init (`.avo/` is removed).

## Write the scorer first

Contract:

```text
score <candidate-dir>
```

Output one JSON object and exit 0 when evaluation completed:

```json
{"correct":true,"objective":12.4,"metrics":{"stddev":0.2},"note":"batched writes","artifacts":[]}
```

Nonzero exit means infrastructure failure. In rank mode, an incorrect candidate may omit the metric:

```json
{"correct":false,"objective":null,"metrics":{},"note":"tests failed"}
```

Use one higher-is-better scalar. Negate lower-is-better measurements such as latency or cost. Put
other useful dimensions in `metrics`. Report `stddev` when measurements are noisy.

## Driver behavior

The agent receives a disposable candidate directory and a generated prompt. It should:

- inspect the current implementation and relevant knowledge;
- use recent accepts and rejects to avoid repeating failed ideas;
- respect human pins;
- make one coherent change;
- test and revise internally before returning;
- avoid pushing or editing AVO state.

AVO captures the complete candidate tree relative to the starting commit, so an accidental local
commit is normalized into the same final patch.

`--agent` is a two-argument executable you supply. There is no default vendor CLI. See
`scripts/adapters/` for copy-and-edit examples.

## Adversarial verification

Contract:

```text
verify <candidate-dir> <score-json-path>
```

Output:

```json
{"pass":true,"note":"independently reproduced","evidence":["verify.log"]}
```

or:

```json
{"pass":false,"note":"control explains the apparent gain","evidence":["control.json"]}
```

Exit 0 for either verdict. Use nonzero only when verification could not run. A falsification becomes a
normal reject and its reason enters future context.

A generic verifier should test independent reproduction, competing explanations, boundary cases,
controls, requirements, and whether the evidence supports the strength of the claim.

## Two-tier memory

Raw memory:

```text
.avo/ledger.jsonl
.avo/runs/<id>/prompt.md
.avo/runs/<id>/diff.patch
.avo/runs/<id>/score.json
.avo/runs/<id>/verify.json
```

Curated memory:

```text
.avo/memory.md
```

Keep curated memory short and actionable:

```markdown
# Current understanding

## What worked
- ...

## What failed
- ...

## Current hypotheses
- ...

## Closed directions
- ...
```

Run `avo reflect` when the ledger has become difficult to reason over. The supervisor can also return
a complete memory replacement with a redirect.

## Human pins

```bash
avo pin "Preserve backwards compatibility"
avo pins
avo unpin 1
```

Pins are injected before ordinary memory and trajectory context. Models may suggest changing a pin,
but only the human-facing commands modify it.

## Stagnation and redirects

`avo-stall-detect` uses only ledger data. A redirect or human resume begins a fresh search segment, so
the driver has several attempts to act on new advice instead of immediately retriggering the same
stall.

The supervisor sees rejected attempts as well as accepted lineage. Its final line is:

```json
{"directions":["direction 1","direction 2"],"memory":"optional Markdown"}
```

After the configured number of unsuccessful redirects, AVO marks the task `stalled`. Add pins or
change configuration, then run `avo resume`.

## Modes

### rank

Use for dense scalar optimization. A candidate is accepted when:

```text
correct
and objective > best_objective + max(min_improvement_abs, stddev)
```

The first correct candidate seeds an unscored or incorrect baseline.

### discover

Use for sparse, orthogonal progress. Every correct candidate is appended. Define correctness as a
validated resolved item, not necessarily a positive finding; honest negative results can therefore
advance coverage. Use an objective only when a monotone coverage count is meaningful. If a smaller
corpus or coverage count is invalid, set `correct=false` in the scorer — discover mode will not
reject on a shrinking objective.

## Optional extensions

Do not expand the core for every domain. Compose optional behavior with executable commands and
editable prompt files:

```text
.avo/prompts/driver.md
.avo/prompts/supervisor.md
.avo/prompts/reflect.md
```

A research profile may add an inbox, closure registry, reopen conditions, deterministic harnesses, or
an external planner. A monitoring deployment may schedule a cheap sentinel. Those tools should read
AVO evidence and write ordinary files or pins rather than becoming required control-plane concepts.

## Operational behavior

- `avo tick` is the universal scheduler unit.
- Mutating commands use an atomic local lock.
- A hard interruption leaves canonical Git state unchanged during agent/score/verify phases.
- The next mutating command removes stale worktrees and records the interrupted tick.
- Accepted finalization is recoverable if Git committed before state persistence completed.
- A bare `avo run` is bounded to 20 ticks; pass `--max-ticks 0` explicitly for an unbounded run.
- Reports are redacted, but the source ledger is not.

## Trust boundary

Worktrees protect against ordinary failures and accidental contamination. They are not a security
sandbox: an intentionally malicious process with host filesystem or credential access can escape its
candidate directory. Run untrusted agents in a container or VM and remove push credentials at the
adapter layer.
