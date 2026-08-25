#!/bin/sh
# Generic adapter. Contract: <candidate-dir> <prompt-file>
# Set AVO_EXEC to the shell command that should run inside the candidate.
# The candidate is cwd. The prompt path is $AVO_PROMPT.
set -eu
[ -n "${AVO_EXEC:-}" ] || { echo "agent-exec: set AVO_EXEC to your agent command" >&2; exit 1; }
cd "$1"
AVO_CANDIDATE="$1"
AVO_PROMPT="$2"
export AVO_CANDIDATE AVO_PROMPT
# ponytail: user-supplied command fragment, same trust class as --agent
eval "$AVO_EXEC"
