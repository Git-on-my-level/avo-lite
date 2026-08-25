# Harness bindings

`avo tick` is one synchronous, locked, self-contained candidate attempt. Any scheduler that can run a
command can drive it. The model invocation belongs inside **your** adapter. AVO does not default to a
vendor CLI.

Assume `AVO=/path/to/avo-lite/scripts` and a task is already initialized. Shipped examples live in
`scripts/adapters/` (`$AVO/adapters/`), not `references/adapters/`.

## Write an adapter

Contract:

```text
adapter <candidate-dir> <prompt-file>
```

Rules:

1. Edit only the candidate directory. AVO scores that tree, then either commits one patch or deletes it.
2. Exit 0 when the tree is ready. Nonzero is an infrastructure error unless `score_on_agent_error` is set.
3. Do not push, and do not write AVO state (`.avo/config.json`, ledger, pins).
4. Read `.avo/knowledge/` inside the candidate when the prompt points at it.
5. Honor `AVO_DRIVER_MODEL` / `AVO_SUPERVISOR_MODEL` if your CLI has a model flag.

Minimal template:

```bash
#!/bin/sh
set -eu
cd "$1"
# Replace this line with any authenticated CLI you already have.
exec your-agent --prompt-file "$2"
```

`--agent` is required at `avo init`. Point it at this script (or an inline command).

Example wrappers in this repo, for copy-and-edit only:

```bash
# Claude Code
--agent "$AVO/adapters/agent-claude.sh"

# agentctl run/await/result
export AVO_AGENTCTL_TARGET="codex exec"
--agent "$AVO/adapters/agent-agentctl.sh"
```

Adapt the three `agentctl` calls, or replace the body entirely, as long as the two-argument contract
stays the same.

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
