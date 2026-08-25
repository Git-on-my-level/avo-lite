#!/usr/bin/env bash
# Agent adapter for launchers with run/await/result semantics.
# Contract: <candidate-dir> <prompt-file>.
set -euo pipefail
cd "$1"
target="${AVO_AGENTCTL_TARGET:-omp}"
model="${AVO_DRIVER_MODEL:-${AVO_SUPERVISOR_MODEL:-}}"
prompt=$(cat "$2")

# AVO_AGENTCTL_TARGET is intentionally a shell-style command fragment, for example:
#   AVO_AGENTCTL_TARGET="codex exec"
# shellcheck disable=SC2086
rid=$(agentctl run --json -- $target ${model:+--model "$model"} -p "$prompt" | python3 -c '
import json, sys
value = json.load(sys.stdin)
print(value.get("id") or value.get("run_id") or "")
')
[ -n "$rid" ] || { echo "agentctl: no run id" >&2; exit 1; }
agentctl await "$rid" >/dev/null 2>&1 || true
agentctl result "$rid"
