# avo-lite

**A lightweight, harness-agnostic scaffold for autonomous self-improving loops.**

Point an agent and a scorer at a task and `avo-lite` runs an evolutionary loop: it keeps a
scored git lineage, feeds the recent history back to the agent as context, accepts a change only
if it's correct and measurably better, and — when the search stalls — calls a stronger model to
review the whole trajectory and propose fresh directions. Core is just **`bash` + `git` + `jq`**;
the agent and scorer are pluggable commands, so the same loop runs under Claude Code, a bare cron
job, or any agent-launcher CLI.

It's a small, practical distillation of NVIDIA's *Agentic Variation Operators* (AVO) paper
([arXiv:2603.24517](https://arxiv.org/abs/2603.24517)) — the idea that a coding agent can *be* the
variation operator in an evolutionary search, not just a one-shot candidate generator. See
[`references/paper-avo-condensed.md`](references/paper-avo-condensed.md).

## Why

Most "autonomous loop" setups are a cron that calls an LLM. They persist history but start every
iteration cold, reinvent "did it work?" per project, and give up on a stall instead of re-aiming.
avo-lite adds the three pieces that make a loop actually *evolutionary*:

1. **A scored, immutable lineage that is read back as context** — the agent sees prior scored
   versions before proposing the next.
2. **One hard correctness gate, separate from the quality metric** — a wrong candidate scores zero
   regardless of how fast/cheap/pretty it is.
3. **A stagnation detector that redirects** — a cheap no-LLM check spots a plateau; a stronger model
   reviews the trajectory and hands back fresh directions.

Full rationale: [`references/invariants.md`](references/invariants.md).

## Requirements

`bash` (3.2+, so macOS and Linux both work), `git`, and `jq`. Nothing else.

## Quick start

```bash
git clone https://github.com/Git-on-my-level/avo-lite
export PATH="$PWD/avo-lite/scripts:$PATH"

cd my-project            # any git repo (or an empty dir; avo will git-init it)

# scaffold a task. --score is mandatory (the hard gate); --agent defaults to `claude -p`.
avo init speedup \
  --goal  "maximize throughput (higher objective = faster)" \
  --score ./score.sh \
  --agent ./my-agent.sh \
  --mode  rank           # rank = optimize a metric; discover = collect correct findings

avo tick                 # run one iteration (the universal unit; safe under any scheduler)
avo run --max-ticks 40   # or loop locally (bounded by default)

avo status               # best score, tick count, stall state, recent lineage
avo report               # redacted, noteworthy-only summary
```

No scorer yet? `avo init` **without `--score`** starts a **preview mode**: the agent runs and its
diffs are saved, but nothing is scored or kept — handy for wiring up the agent/prompt before you
write `f`.

## The two contracts you provide

**Scorer** — `score.sh <candidate-dir>` prints one JSON object and exits 0 when the evaluation
*completed* (pass or fail); non-zero only on infra failure:

```json
{"correct": true, "objective": 0.87, "metrics": {"stddev": 0.4}, "note": "what changed", "artifacts": []}
```

`correct` (bool) is the hard gate. `objective` (higher-is-better number) is what the ratchet
compares — required in `rank` mode; in `discover` mode use a monotone coverage counter. `note` is the
one line the *next* agent reads. Design guidance: [`references/score-authoring.md`](references/score-authoring.md).

**Agent** — `my-agent.sh <candidate-dir> <prompt-file>` edits the working tree in place and exits 0.
Whatever model or CLI sits behind it is opaque to avo-lite. Ready-made adapters are in
[`scripts/adapters/`](scripts/adapters/) (`agent-claude.sh` for Claude Code; `agent-agentctl.sh` as a
generic agent-launcher example; `score-ci.sh` and `score-discover.sh` as scorer templates).

## Two modes

- **`rank`** (optimization): the ratchet accepts only an above-noise improvement in `objective`
  (`margin = max(min_improvement_abs, 1·stddev)`). For latency/throughput/cost/eval-score.
- **`discover`** (sparse reward): append-only; every candidate that passes correctness (and an
  optional `--verify` falsification pass) is kept, with `objective` as a coverage counter. For
  hypothesis-sweep / research-shaped work where a ranking ratchet degenerates.

## Running it anywhere

`avo tick` is one idempotent, locked iteration, so any scheduler can drive it — a `/loop` in Claude
Code, a plain crontab line, or a job in your agent runner. The model lives inside the agent adapter,
so the scheduler only ever runs a plain command. See
[`references/harness-bindings.md`](references/harness-bindings.md).

Pair the driver with the standalone `avo-stall-detect` (no-LLM, reads only the ledger) as a cheap,
more-frequent watchdog.

## Safety model

- **Never pushes; never commits to main.** All work stays on branch `avo/<task>`; every command
  aborts unless HEAD is that branch. (It can't stop an *agent adapter you configure* from pushing —
  run agents without push creds if that must be a hard guarantee.)
- **Non-destructive.** A tick refuses on modified tracked files (pass `--allow-dirty` to override);
  pre-existing untracked files are preserved; a reject restores only that tick's own changes. avo's
  own state (`avo.toml`, `.avo/`, `ledger.jsonl`) is gitignored, so the candidate tree is only ever
  your solution files.
- **Fails closed.** Malformed scores, corrupt state, and failed commits are hard errors — a failed or
  no-op commit is recorded as `error`, never a false "accept."
- **Concurrency-safe.** An atomic lock (auto-reclaimed if the owner died) makes overlapping schedules
  safe. **Silent on success** — reporting fires only on a new best, a supervisor redirect, or an error.

## Layout

```
SKILL.md                  # the skill / full reference (also readable as docs)
scripts/avo               # the dispatcher: init / tick / run / supervise / status / report
scripts/avo-stall-detect  # standalone no-LLM stagnation watchdog
scripts/lib/*.sh          # ledger, ratchet, context, config, redaction helpers
scripts/lib/score-schema.json
scripts/adapters/*        # example agent + scorer adapters
references/*.md           # method, score authoring, harness bindings, paper notes
```

## Provenance

Distilled from real production autonomous loops, then hardened against an adversarial code review and
tuned for ergonomics across two independent design passes. The design rationale lives in
[`references/invariants.md`](references/invariants.md) and [`SKILL.md`](SKILL.md).

## License

MIT © David Zhang. See [LICENSE](LICENSE).
