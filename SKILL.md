---
name: avo-lite
description: "Stand up an autonomous self-improving agent loop (scored lineage + hard gate + ratchet + stagnation-redirect) for any task, in any harness, in minutes."
version: 1.0.0
author: David Zhang
license: MIT
platforms: [linux, macos]
metadata:
  tags: [autonomous, evolutionary, self-improving, loop, cron, avo, optimization, discovery]
---

# AVO-lite

## When to use

Use when you want an agent to **improve something over many unattended iterations** and keep only
what verifiably gets better: kernel/latency/cost optimization, eval-score climbing, prompt or config
tuning, or sparse-reward discovery (security-research hypothesis sweeps). It is a lightweight,
harness-agnostic distillation of NVIDIA's *Agentic Variation Operators* (AVO) method — the paper's
condensed notes are in `references/paper-avo-condensed.md`.

Use it when a bespoke "cron that calls an LLM" keeps starting cold, reinvents "did it work" per
project, and gives up (circuit-breaker) instead of re-aiming. AVO-lite adds the three pieces most
hand-built loops lack: a **scored lineage read back as context**, **one hard correctness gate**, and a
**stagnation detector that redirects** instead of quitting.

Do **not** use it for one-shot tasks, or where you have no fast automated way to score a candidate —
the hard gate is mandatory; without a `--score` command `avo init` refuses.

## The method (seven invariants)

A loop is "AVO" (not just a scheduled LLM call) iff it has all of these. Details in
`references/invariants.md`.

1. **Split gate.** Correctness is a hard boolean, checked first and alone; fail ⇒ reject, quality ignored.
2. **Scored immutable lineage, read back as context.** Not just persisted — fed to the next run.
3. **Commit ratchet.** A candidate enters the lineage only if correct and not worse than best.
4. **Agent owns its inner loop.** Many internal attempts per one committed version; the scaffold gates commits, never micromanages turns.
5. **Retrievable K.** Domain docs/reference impls the agent consults at will (a directory + `K/INDEX.md`), not prompt-stuffed.
6. **Stagnation detector + trajectory redirect.** A cheap no-LLM check finds stall/cycles; a rare expensive agent reviews the whole trajectory and proposes fresh directions.
7. **Cheap-driver / expensive-supervisor model split.** Driver ticks on a cheap model; supervisor on Opus/Fable.

## Quick start

```bash
AVO=<this-skill>/scripts        # add $AVO to PATH, or call scripts by full path

# 1. scaffold a task inside (or next to) a git repo. --score is MANDATORY.
$AVO/avo init my-task \
  --goal  "maximize throughput (higher objective = faster)" \
  --score "$AVO/adapters/score-ci.sh" \
  --agent "$AVO/adapters/agent-claude.sh" \
  --mode  rank                 # rank = optimize; discover = append every correct find

# 2. run one iteration (the universal unit; safe under any scheduler)
$AVO/avo tick

# 3. or loop locally
$AVO/avo run --max-ticks 40

$AVO/avo status                 # best score, tick count, stall state, recent lineage
$AVO/avo report                 # redacted, noteworthy-only summary (silent on success)
```

`init` creates branch `avo/<task>`, scores and commits a baseline v0, and writes local control state
that is **gitignored so it never becomes candidate content**: `avo.toml` (scalar knobs), `.avo/`
(`state.json`, `config.json` with the quote-safe command strings, prompts, per-run logs), and
`ledger.jsonl`. **Accepted versions are git commits; `ledger.jsonl` mirrors every tick (accept, reject,
error, redirect, with `parent` and `metrics`) for fast context read-back.**

Preconditions the scaffold enforces every tick: it holds an exclusive lock (`.avo/lock`, atomic
`mkdir`) so overlapping schedulers can't corrupt state; it refuses to run unless HEAD is the task
branch (**it will not commit to main**); and it refuses to run with a dirty working tree, because a
reject does `git reset --hard HEAD` + a scoped `git clean` and it will not risk your uncommitted work.
Commit your own edits (or use a dedicated worktree) before ticking.

## Contracts (what you must supply)

- **Score** — `$AVO_SCORE_CMD <candidate-dir>` prints one JSON object and exits 0 when the evaluation
  *completed* (pass or fail); non-zero only on infra failure. Schema in `scripts/lib/score-schema.json`:
  ```json
  {"correct": true, "objective": 0.87, "metrics": {"stddev": 0.4}, "note": "what changed", "artifacts": []}
  ```
  `correct` (bool, required) is the hard gate. `objective` (higher-is-better number) is required in
  `rank` mode; in `discover` mode use a monotone coverage counter. `note` is what the next agent reads.
  See `references/score-authoring.md`.
- **Agent** — `$AVO_AGENT_CMD <candidate-dir> <prompt-file>`; edits the working tree in place, exits 0.
  A **non-zero exit is treated as a tick error** (not scored, not counted as a stall). Whatever
  model/CLI sits behind it is opaque to the core. Adapters provided: `agent-claude.sh`,
  `agent-agentctl.sh` (omp/codex/cursor via agentctl). For anything beyond a trivial one-liner, point
  `--score`/`--agent` at a **wrapper script** (like the adapters), not an inline quoted string — the
  command strings are stored quote-safe in `.avo/config.json`, but a shell one-liner still runs through
  `sh -c` and hits normal quoting limits.
- **Verify** (optional) — `$AVO_VERIFY_CMD <candidate-dir>`; exit 0 = independently confirmed. When set,
  an accept requires correct **and** verify. This is the graft point for an adversarial falsification
  pass — recommended on for discovery tasks.

Env vars `AVO_AGENT_CMD` / `AVO_SCORE_CMD` / `AVO_VERIFY_CMD` / `AVO_DRIVER_MODEL` / `AVO_SUPERVISOR_MODEL`
override `avo.toml`.

## Two modes

- **`rank`** (optimization): the ratchet accepts only an above-noise improvement in `objective`
  (`margin = max(min_improvement_abs, 1·stddev)`). Use for perf/latency/cost/eval-score.
- **`discover`** (sparse reward): append-only; every candidate that passes correctness (and verify) is
  kept, `objective` is a coverage counter. Use for security-research / hypothesis-sweep shapes where the
  ranking ratchet degenerates. Pair with `--verify`.

## Harness bindings (core identical; only the scheduler differs)

`avo tick` is one idempotent iteration. The model lives inside the agent command, so every scheduler
treats a tick as a plain command. Full table in `references/harness-bindings.md`.

- **Hermes cron**: a `no_agent` job `{command: "avo tick"}` on your cadence; add `avo-stall-detect` as a
  second `no_agent` watchdog. (See `hermes-cron`.)
- **Claude Code**: `/loop 30m avo tick`, or `--agent adapters/agent-claude.sh`.
- **Bare cron/shell**: `*/30 * * * * cd <task> && avo tick >> tick.log 2>&1`.
- **codex/omp/cursor**: `--agent adapters/agent-agentctl.sh` with `AVO_AGENTCTL_TARGET`. (See `agentctl-portable`.)

## Safety defaults (non-negotiable, built in)

- **Never pushes; never commits to main.** The scaffold itself runs no `git push`, and every command
  aborts unless HEAD is the task branch. (It cannot stop an *agent adapter* you configure from pushing —
  run agents without push creds, or behind a wrapper that denies `git push`, if that must be a hard
  guarantee.)
- **Non-destructive rejects.** A clean working tree is required at tick start; the full run dir (prompt,
  diff, score, transcript) is saved *before* the tree is reset; avo's control state (`avo.toml`, `.avo/`,
  `ledger.jsonl`) is gitignored and excluded from the reset, so accept/reject never rewinds it and never
  touches files outside this tick's candidate.
- **Fails closed.** Malformed scores, corrupt `state.json`, non-numeric config, and failed commits are
  hard errors, not silent defaults; a failed/no-op commit is recorded as `error`, never a false accept.
- **Silent on success.** `report` fires only on new best above threshold, supervisor firing, tick error,
  or halt.
- **Redact the report, never the ledger.** Reports pass through `lib/redact.sh`; the ledger keeps raw
  truth. (Redacting the source of truth is how a real loop once produced three false findings — a
  redaction filter masked a benign token in the evidence itself, so the loop "discovered" a bug that
  wasn't there. Redact what you send, never what you reason over.)

## Stagnation

`avo-stall-detect` (no LLM, reads only `ledger.jsonl`) triggers on: **stall** (`ticks_since_last_accept
>= stall_window`, default 8), **unproductive cycle** (reject-ratio `> reject_ratio` over a full
`cycle_window`), or **repeat edits** (same diff `>= repeat_edit_max` in the window). On trigger the
driver invokes `avo supervise` (expensive model): it reviews the accepted trajectory and writes 2–4
fresh directions (optionally a re-base commit) to `.avo/redirect.json`, which the next tick reads as
priority context. If it redirects and still stalls, it stops and reports for human review rather than
looping.

## Verification (that your setup is sound)

- [ ] `avo init` refused without `--score` (proves the gate is wired).
- [ ] `avo tick` produced a `.avo/runs/<n>/score.json` that validates against the schema.
- [ ] A correct-but-not-better candidate shows `action:"reject"` in `ledger.jsonl`; a better one shows
      `action:"accept"` with a new commit.
- [ ] `.avo/` and `ledger.jsonl` are gitignored (git log shows only real candidate diffs).
- [ ] Forcing a plateau eventually writes `.avo/redirect.json` and a `redirect` ledger entry.

## Further reading

- `references/invariants.md` — the seven invariants and why each is load-bearing.
- `references/score-authoring.md` — how to design `f` for a new domain.
- `references/harness-bindings.md` — Claude Code / bare cron / agent-launcher wiring.
- `references/paper-avo-condensed.md` — condensed notes on the AVO paper this distills.
