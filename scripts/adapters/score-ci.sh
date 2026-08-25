#!/usr/bin/env bash
# Example rank-mode scorer: hard correctness command + one higher-is-better metric.
# Contract: <candidate-dir> -> one JSON object; nonzero exit is infrastructure failure.
set -o pipefail
cd "$1" || exit 3

CORRECT_CMD="${AVO_CORRECT_CMD:-make test}"
METRIC_CMD="${AVO_METRIC_CMD:-}"
[ -n "$METRIC_CMD" ] || { echo "score-ci: set AVO_METRIC_CMD" >&2; exit 3; }

correct_log=$(mktemp "${TMPDIR:-/tmp}/avo-correct.XXXXXX") || exit 3
metric_log=$(mktemp "${TMPDIR:-/tmp}/avo-metric.XXXXXX") || { rm -f "$correct_log"; exit 3; }
trap 'rm -f "$correct_log" "$metric_log"' EXIT

correct=false
if sh -c "$CORRECT_CMD" >"$correct_log" 2>&1; then correct=true; fi

objective=null
if [ "$correct" = true ]; then
  objective=$(sh -c "$METRIC_CMD" 2>"$metric_log" | grep -Eo '[-+]?[0-9]*\.?[0-9]+([eE][-+]?[0-9]+)?' | head -1)
  [ -n "$objective" ] || { echo "score-ci: metric command produced no number" >&2; exit 3; }
  note="tests pass; metric=$objective"
else
  note=$(tail -3 "$correct_log" | tr '\n' ' ' | cut -c1-160)
fi

python3 - "$correct" "$objective" "$note" <<'PY'
import json, sys
correct = sys.argv[1] == "true"
objective = None if sys.argv[2] == "null" else float(sys.argv[2])
print(json.dumps({"correct": correct, "objective": objective, "metrics": {}, "note": sys.argv[3]}))
PY
