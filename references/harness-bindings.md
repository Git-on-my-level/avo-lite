# Harness bindings

`avo tick` is one synchronous, locked, self-contained candidate attempt. Any scheduler that can run a
command can drive it. The model invocation belongs inside the configured adapter.

Assume `AVO=/path/to/avo-lite/scripts` and a task is already initialized.

## Cron

```cron
*/30 * * * * cd /path/to/project && /path/to/avo tick >> .avo/cron.log 2>&1
```

A task marked `stalled` rejects future ticks until `avo resume`, preventing an unattended scheduler
from spending indefinitely after the configured redirects fail.

## Claude Code

```bash
avo init task \
  --goal "..." \
  --score ./score.sh \
  --agent "$AVO/adapters/agent-claude.sh"
```

Or let `avo init` use that packaged adapter by default.

## Agent launcher

```bash
export AVO_AGENTCTL_TARGET="codex exec"
avo init task \
  --goal "..." \
  --score ./score.sh \
  --agent "$AVO/adapters/agent-agentctl.sh"
```

The example adapter expects an `agentctl run/await/result` interface. Adapt its three calls for another
launcher while preserving:

```text
adapter <candidate-dir> <prompt-file>
```

## Model tiers

Set models in `.avo/config.json` or environment variables:

```bash
export AVO_DRIVER_MODEL="cheap-driver"
export AVO_SUPERVISOR_MODEL="strong-supervisor"
```

The driver runs every tick. The supervisor runs only after deterministic stagnation or a manual
`avo supervise` / `avo reflect` request.

## External state directory

Repo-local state is simplest. For environments that routinely delete ignored directories or use
aggressive `git clean -ffdx`, set the exact task state directory consistently:

```cron
AVO_HOME=/var/lib/avo/my-task cd /path/to/project && /path/to/avo tick
```

The candidate worktrees are then created under that external directory.

## Cadence

Choose cadence from the combined cost of agent and evaluator:

- local tests and short benchmarks: minutes;
- long benchmarks or cloud quota: tens of minutes to hours;
- human or expensive verification: trigger less frequently or verify only release-worthy gains.

The core intentionally does not detach workers or manage a job queue. Use the scheduler or agent
launcher for process-level distribution; keep each `avo tick` synchronous and observable.
