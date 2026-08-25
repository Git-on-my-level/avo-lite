# avo-lite

A small, agent-agnostic optimization loop for work that can be evaluated automatically.

AVO repeatedly gives an agent a disposable checkout of the current best solution. The agent changes
it, a scorer evaluates it, and an optional adversarial verifier tries to disprove any candidate that
would otherwise win. Accepted candidates become the next Git commit. Rejected candidates disappear,
but their results remain in an append-only ledger and are fed into later attempts.

The core is one dependency-free Python file plus two tiny launchers:

- Python 3.8+
- Git
- no pip packages, service, daemon, database, or plugin SDK

Agent, scorer, verifier, and supervisor integrations are plain executable commands.

## Design

AVO-lite intentionally assumes the configured models are honest and generally reliable. It does not
try to sandbox a malicious agent or defend the objective from deliberate reward hacking. Its
worktrees provide **transactional isolation**: an interrupted, rejected, or half-finished attempt does
not modify the canonical checkout.

The durable loop has eight concepts:

1. a disposable candidate worktree;
2. an agent command that edits it;
3. a scorer with a separate correctness gate and objective;
4. a Git ratchet that accepts only valid progress;
5. an optional adversarial verifier for would-be winners;
6. an append-only mechanical ledger;
7. a small curated memory file plus persistent human pins;
8. deterministic stagnation detection with a rare supervisor redirect.

Planner agents, research trackers, and domain-specific workflows belong outside the core. They can be
composed through commands and editable prompts without an in-process plugin framework.

## Quick start

```bash
git clone https://github.com/Git-on-my-level/avo-lite
export PATH="$PWD/avo-lite/scripts:$PATH"

cd my-project

avo init speedup \
  --goal "maximize throughput; higher objective is better" \
  --score ./score.sh \
  --agent ./agent.sh \
  --verify ./verify.sh \
  --mode rank

avo tick
avo run --max-ticks 40
avo status
```

`avo init` creates and checks out `avo/<task>`, captures the current project as baseline v0, and
scores that baseline. Without `--score`, the task starts in preview mode: the agent runs and each diff
is saved, but no candidate is accepted.

The default Claude Code adapter is used when `--agent` is omitted.

## Where runs live

By default all local state is under the project root:

```text
.avo/
├── config.json
├── state.json
├── ledger.jsonl
├── memory.md
├── pins.md
├── redirect.json          # only while a redirect is waiting to be consumed
├── knowledge/
├── prompts/
└── runs/
    ├── 000000/            # baseline evaluation
    └── 000001/
        ├── worktree/      # present only while the attempt is active
        ├── prompt.md
        ├── agent.stdout
        ├── agent.stderr
        ├── diff.patch
        ├── score.json
        └── verify.json
```

AVO adds `/.avo/` to `.git/info/exclude`, not the tracked `.gitignore`.

A worktree inside `.avo/runs/...` is supported by Git. It is not an independent nested repository:
its `.git` is a small pointer file to the parent repository's worktree metadata. Once a tick reaches a
terminal result, AVO removes the worktree and keeps only the evidence files.

To place task state elsewhere, set `AVO_HOME` to the exact state directory on every invocation:

```bash
export AVO_HOME="$HOME/.avo/my-project"
avo status
```

Repo-local `.avo/` is recommended because it requires no project-to-state mapping and can be reset
with `rm -rf .avo`.

## Command contracts

### Agent

```text
agent-command <candidate-dir> <prompt-file>
```

The agent edits the candidate directory in place and exits 0. It may inspect, test, and revise as many
times as needed during that invocation. AVO normalizes any accidental local commits back into one
candidate diff before scoring.

Environment variables available to adapters include:

```text
AVO_TICK
AVO_DRIVER_MODEL
AVO_SUPERVISOR_MODEL
```

### Scorer

```text
score-command <candidate-dir>
```

It prints exactly one JSON object and exits:

- `0` when evaluation completed, whether correctness passed or failed;
- nonzero only for infrastructure or execution failure.

```json
{
  "correct": true,
  "objective": 0.873,
  "metrics": {"latency_ms": 12.3, "stddev": 0.4},
  "note": "removed a blocking fence",
  "artifacts": ["profile.txt"]
}
```

`correct` is the hard gate. In `rank` mode, `objective` must be a finite number only when
`correct=true`; an incorrect candidate may return `null` and skip an expensive benchmark. Higher is
always better. The acceptance margin is:

```text
max(search.min_improvement_abs, metrics.stddev)
```

In `discover` mode, every correct candidate is accepted. `objective` may be a monotone coverage
counter or `null`.

### Adversarial verifier

```text
verify-command <candidate-dir> <score-json-path>
```

The verifier runs only after correctness passes and the ratchet says the candidate would otherwise be
accepted. It prints exactly one JSON object:

```json
{
  "pass": false,
  "note": "the gain disappears under the cold-cache control",
  "evidence": ["control-results.json"]
}
```

Exit 0 means verification completed, regardless of verdict. Nonzero means verifier infrastructure
failure and records a tick error rather than a candidate rejection.

### Supervisor

The supervisor uses the same `<candidate-dir> <prompt-file>` contract as the driver. By default AVO
reuses the agent command with `AVO_SUPERVISOR_MODEL`; `--supervisor` can provide another command.

Its final output line must be compact JSON:

```json
{"directions":["try a different representation","remove the shared bottleneck"],"memory":"optional replacement Markdown"}
```

The supervisor sees accepted and rejected attempts, verifier failures, current memory, and human
pins. A redirect starts a fresh stagnation window and is consumed by the next driver tick.

## Memory and human steering

`ledger.jsonl` is mechanical truth and is never summarized away. `memory.md` is compact semantic
memory: what worked, what failed, current hypotheses, and closed directions.

Refresh it manually with the supervisor:

```bash
avo reflect
```

The supervisor may also replace it when producing a stagnation redirect.

Human pins are durable, authoritative instructions placed near the top of every driver and supervisor
prompt:

```bash
avo pin "Do not change the public API"
avo pins
avo unpin 1
```

They are deliberately stored in a boring Markdown file: `.avo/pins.md`.

## Stagnation

Stagnation is detected without an LLM from candidate ledger entries. The default checks are:

- eight candidate attempts since the last accept, redirect, or human resume;
- repeated identical diffs in a full cycle window;
- a high rejection ratio in a full cycle window.

On the first stalls, AVO calls the supervisor and writes a one-shot redirect. After
`search.max_redirects` unsuccessful redirects, the task becomes `stalled` and scheduled ticks stop
spending model calls.

```bash
avo status
avo pin "Try the allocation-heavy path next"
avo resume
```

The standalone watchdog preserves the conventional exit contract:

```bash
avo-stall-detect
# exit 0 and a reason: stalled
# exit 1 and no output: healthy
# exit 2: configuration or ledger error
```

## Commands

```text
avo init <task> ...          initialize and score baseline v0
avo tick [--force]           run one isolated attempt
avo run [--max-ticks N]      run bounded repeated attempts; 0 is unbounded
avo supervise [reason]       request a redirect manually
avo reflect                  refresh memory.md
avo pin <text>               add a human pin
avo pins                     list pins
avo unpin <number>           remove a pin
avo resume                   clear stalled state after human intervention
avo status                   show state and recent ledger entries
avo report                   show redacted noteworthy events
```

Every mutating command uses a local lock. If a process is killed, the next mutating command removes
the stale worktree and records the interrupted attempt as an error. The small finalization window is
also recoverable when the accepted Git commit was created before state was written.

## Configuration

Edit `.avo/config.json` directly. Commands and models can be overridden without editing the file:

```text
AVO_AGENT_CMD
AVO_SCORE_CMD
AVO_VERIFY_CMD
AVO_SUPERVISOR_CMD
AVO_DRIVER_MODEL
AVO_SUPERVISOR_MODEL
AVO_HOME
AVO_QUIET
```

Important search defaults:

```json
{
  "search": {
    "context_entries": 8,
    "supervisor_context_entries": 60,
    "min_improvement_abs": 0,
    "stall_window": 8,
    "cycle_window": 10,
    "reject_ratio": 0.8,
    "repeat_edit_max": 3,
    "max_redirects": 2,
    "score_on_agent_error": false
  }
}
```

## Existing v1 tasks

The Python implementation has best-effort read compatibility with the prior split state layout
(`avo.toml`, root `ledger.jsonl`, and the older `.avo/config.json`). New tasks use only `.avo/`.

## Testing

```bash
python3 -m unittest discover -s tests -v
```

The integration suite exercises accepted and rejected candidates, worktree cleanup, incorrect scores
with null objectives, incorrect baselines, verifier vetoes, pins, supervisor redirects, halted stalls,
and stale-run recovery.

## Further reading

- [`SKILL.md`](SKILL.md) — complete operational method
- [`references/invariants.md`](references/invariants.md) — the deliberately small set of invariants
- [`references/score-authoring.md`](references/score-authoring.md) — designing a useful evaluator
- [`references/harness-bindings.md`](references/harness-bindings.md) — cron and agent-runner examples
- [`references/paper-avo-condensed.md`](references/paper-avo-condensed.md) — AVO paper notes

## License

MIT © David Zhang. See [LICENSE](LICENSE).
