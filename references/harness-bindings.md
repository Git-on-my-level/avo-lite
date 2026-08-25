# Harness bindings

`avo tick` is one synchronous, locked, self-contained candidate attempt. Any scheduler that can run a
command can drive it. The model invocation belongs inside **your** adapter. AVO does not default to a
vendor CLI.

Assume `AVO=/path/to/avo-lite/scripts`. The one supported adapter is
`$AVO/adapters/agent-exec.sh`. It is not `references/adapters/`.

## Agent adapter

Contract:

```text
adapter <candidate-dir> <prompt-file>
```

Use the packaged adapter and put your CLI in `AVO_EXEC`:

```bash
export AVO_EXEC='your-agent --prompt-file "$AVO_PROMPT"'
avo init task --goal "..." --score ./score.sh --agent "$AVO/adapters/agent-exec.sh"
```

The candidate is cwd. `AVO_PROMPT` is the prompt file. `AVO_CANDIDATE` is the candidate path.
Honor `AVO_DRIVER_MODEL` / `AVO_SUPERVISOR_MODEL` if your CLI has a model flag.

Write your own script only when `AVO_EXEC` is too awkward (multi-step launchers). Keep the same
two-argument contract. Do not push or write AVO state. Read `.avo/knowledge/` in the candidate when
the prompt points at it.

Vendor CLIs are `AVO_EXEC` values, not repo files:

```bash
export AVO_EXEC='claude -p "$(cat "$AVO_PROMPT")" --permission-mode acceptEdits'
export AVO_EXEC='hermes -z < "$AVO_PROMPT"'
```

A multi-step launcher example lives at `$AVO/adapters/agent-agentctl.sh`.

## Cron

```cron
*/30 * * * * cd /path/to/project && /path/to/avo tick >> .avo/cron.log 2>&1
```

A task marked `stalled` rejects future ticks until `avo resume`, preventing an unattended scheduler
from spending indefinitely after the configured redirects fail.

Set `search.hook_timeout_sec` or `AVO_HOOK_TIMEOUT` so a hung scorer or agent becomes a recorded tick
error instead of a silent stall.

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
