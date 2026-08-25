#!/usr/bin/env bash
# Example adapter only — not the AVO default. Contract: <candidate-dir> <prompt-file>.
# Copy and edit, or write your own. The agent edits the working tree in place.
# Model tier comes from AVO_DRIVER_MODEL / AVO_SUPERVISOR_MODEL (driver sets one).
set -e
cd "$1"
model="${AVO_DRIVER_MODEL:-${AVO_SUPERVISOR_MODEL:-}}"
exec claude -p "$(cat "$2")" \
  ${model:+--model "$model"} \
  --permission-mode acceptEdits \
  --allowedTools "Edit,Write,Read,Bash,Grep,Glob"
