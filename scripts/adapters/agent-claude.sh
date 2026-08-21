#!/usr/bin/env bash
# Agent adapter: Claude Code headless. Contract: <candidate-dir> <prompt-file>.
# The agent edits the working tree in place; stdout is captured as transcript.
# Model tier comes from AVO_DRIVER_MODEL / AVO_SUPERVISOR_MODEL (driver sets one).
set -e
cd "$1"
model="${AVO_DRIVER_MODEL:-${AVO_SUPERVISOR_MODEL:-}}"
exec claude -p "$(cat "$2")" \
  ${model:+--model "$model"} \
  --permission-mode acceptEdits \
  --allowedTools "Edit,Write,Read,Bash,Grep,Glob"
