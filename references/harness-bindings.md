# Harness bindings

The core unit is `avo tick` — one idempotent iteration, safe under any scheduler. The model lives
*inside* `$AVO_AGENT_CMD`, so every harness treats a tick as a plain command. Only the invocation and
the scheduler differ; the scaffold, ledger, ratchet, and supervisor are identical everywhere.

Assume `AVO=/path/to/avo-lite/scripts` and a task already `init`ed in `<task-dir>`.

## Hermes cron

Two `no_agent` jobs (the model is inside the agent adapter, so Hermes itself schedules only):

```json
{
  "id": "mytask-driver",
  "schedule": "0 */2 * * *",
  "no_agent": true,
  "command": "cd /path/to/<task-dir> && /path/to/avo tick",
  "deliver_to": "telegram:<your DM>"
}
{
  "id": "mytask-stall-watch",
  "schedule": "17 * * * *",
  "no_agent": true,
  "command": "cd /path/to/<task-dir> && /path/to/avo-stall-detect || true"
}
```

This mirrors a common production pattern: a periodic agent driver plus a more frequent no-LLM watchdog. Set model tiers in `avo.toml`
(`driver`/`supervisor`) — the adapter reads `AVO_DRIVER_MODEL`/`AVO_SUPERVISOR_MODEL`. Job installation is scheduler-specific; wire `avo tick` into whatever cron/agent runner you use.

## Claude Code

```bash
# self-paced loop
/loop 30m avo tick
```

or configure the task with the Claude adapter and run ticks by hand / from cron:

```bash
$AVO/avo init t --goal "…" --score …/score.sh --agent "$AVO/adapters/agent-claude.sh"
```

`agent-claude.sh` runs `claude -p` headless with `--permission-mode acceptEdits` and a tool allowlist,
passing `--model $AVO_DRIVER_MODEL` when set.

## Bare cron / shell

```cron
*/30 * * * * cd /path/to/<task-dir> && /path/to/avo tick >> tick.log 2>&1
```

or a foreground loop with sleep:

```bash
$AVO/avo run --sleep 1800 --until-stagnant
```

## Any agent-launcher CLI (codex / omp / cursor / …)

Route the agent through an agent-launcher CLI (this example uses `agentctl`):

```bash
export AVO_AGENTCTL_TARGET="omp"          # or: "codex exec"
$AVO/avo init t --goal "…" --score …/score.sh --agent "$AVO/adapters/agent-agentctl.sh"
```

`agent-agentctl.sh` does `agentctl run -- <target> …`, `agentctl await`, `agentctl result`. The
supervisor call goes through the same adapter with `AVO_SUPERVISOR_MODEL`. Consult your launcher's
own model/trust settings for unattended runs.

## Choosing the driver cadence

- Cheap, fast `f` (unit tests, a local benchmark): tight cadence (a `/loop` or a 5–15 min cron).
- Expensive `f` (real cloud quota, long benchmark, human-in-loop verify): 1–6 h cadence (e.g. a 2-hourly driver). Budget the eval, not just the agent.
- Always add `avo-stall-detect` as a cheaper, more frequent watchdog than the driver so a stall is
  caught between driver ticks.
