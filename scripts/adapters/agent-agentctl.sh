#!/usr/bin/env bash
# Agent adapter: an agent-launcher CLI (example uses agentctl; adapt to your own launcher).
# Contract: <candidate-dir> <prompt-file>. Set AVO_AGENTCTL_TARGET to the native argv,
# e.g. AVO_AGENTCTL_TARGET="omp"  or  AVO_AGENTCTL_TARGET="codex exec".
# Any launcher with run/await/result semantics works; swap the three agentctl calls below.
set -e
cd "$1"
target="${AVO_AGENTCTL_TARGET:-omp}"
model="${AVO_DRIVER_MODEL:-${AVO_SUPERVISOR_MODEL:-}}"
prompt="$(cat "$2")"
# Fire-and-await a single native run; capture its result to stdout.
rid=$(agentctl run --json -- $target ${model:+--model "$model"} -p "$prompt" | jq -r '.id // .run_id')
[ -n "$rid" ] && [ "$rid" != "null" ] || { echo "agentctl: no run id" >&2; exit 1; }
agentctl await "$rid" >/dev/null 2>&1 || true
agentctl result "$rid"
