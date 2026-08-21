#!/usr/bin/env bash
# Example scorer (rank mode, optimization-shaped): wrap a CI/test suite + a metric.
# Contract: <candidate-dir> -> one score JSON on stdout; exit 0 = evaluation completed,
# non-zero = infra failure. Correctness = tests pass (hard gate). Objective = a
# higher-is-better number. In rank mode a numeric objective is REQUIRED, so AVO_METRIC_CMD
# is mandatory here — otherwise every correct tick would be rejected for lacking an objective.
set -o pipefail
cd "$1" || exit 3

CORRECT_CMD="${AVO_CORRECT_CMD:-make test}"     # exit 0 => correct
METRIC_CMD="${AVO_METRIC_CMD:-}"                 # must print exactly one number (higher = better)
[ -n "$METRIC_CMD" ] || { echo "score-ci: set AVO_METRIC_CMD (rank mode needs a numeric objective)" >&2; exit 3; }

correct=false
if sh -c "$CORRECT_CMD" >/tmp/avo-correct.$$ 2>&1; then correct=true; fi

objective=null; note=""
if [ "$correct" = "true" ]; then
  objective=$(sh -c "$METRIC_CMD" 2>/tmp/avo-metric.$$ | grep -Eo '[-+]?[0-9]*\.?[0-9]+' | head -1)
  if [ -z "$objective" ]; then
    rm -f /tmp/avo-correct.$$ /tmp/avo-metric.$$
    echo "score-ci: metric command produced no number" >&2; exit 3   # infra error, not a correctness fail
  fi
  note="tests pass; metric=$objective"
else
  note=$(tail -3 /tmp/avo-correct.$$ | tr '\n' ' ' | cut -c1-160)
fi
rm -f /tmp/avo-correct.$$ /tmp/avo-metric.$$

jq -n --argjson c "$correct" --argjson o "${objective:-null}" --arg n "$note" \
  '{correct:$c, objective:$o, metrics:{}, note:$n}'
