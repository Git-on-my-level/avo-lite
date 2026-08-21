# shellcheck shell=bash
# AVO-lite lineage ledger: typed append + git commit/notes helpers + strict score validation.

# validate_score <score.json> <mode> : enforce the full contract; die on violation.
validate_score() {
  local f="$1" mode="$2"
  # exactly one JSON value
  [ "$(jq -s 'length' "$f" 2>/dev/null)" = "1" ] || die "score must be exactly one JSON object: $f"
  jq -e '
    type=="object"
    and (.correct|type=="boolean")
    and ((.metrics|type=="object") or (has("metrics")|not))
    and ((.metrics.stddev == null) or ((.metrics.stddev|type=="number") and .metrics.stddev>=0))
    and ((.note == null) or (.note|type=="string"))
    and ((.artifacts == null) or ((.artifacts|type=="array") and (all(.artifacts[]?; type=="string"))))
  ' "$f" >/dev/null 2>&1 || die "score violates contract (see scripts/lib/score-schema.json): $(jq -c . "$f" 2>/dev/null)"
  if [ "$mode" = "rank" ]; then
    jq -e '.objective|type=="number"' "$f" >/dev/null 2>&1 \
      || die "rank mode requires numeric .objective: $(jq -c . "$f" 2>/dev/null)"
  else
    jq -e '(.objective==null) or (.objective|type=="number")' "$f" >/dev/null 2>&1 \
      || die "discover mode .objective must be a number or null"
  fi
}

# non-dying variant for baseline scoring: returns non-zero instead of aborting.
validate_score_ok() { ( validate_score "$1" "$2" ) >/dev/null 2>&1; }

# ledger_append : typed. Named args via VAR=value; strings quoted safely by jq.
# Required: tick action ; optional: correct objective delta note commit diff_hash agent_model parent metrics verify
ledger_append() {
  local tick="" action="" correct="null" objective="null" delta="0" note="" \
        commit="null" diff_hash="" agent_model="" parent="null" metrics="{}" verify="null"
  for kv in "$@"; do
    local k="${kv%%=*}" v="${kv#*=}"
    case "$k" in
      tick) tick="$v";; action) action="$v";; correct) correct="$v";; objective) objective="$v";;
      delta) delta="$v";; note) note="$v";; commit) commit="$v";; diff_hash) diff_hash="$v";;
      agent_model) agent_model="$v";; parent) parent="$v";; metrics) metrics="$v";; verify) verify="$v";;
    esac
  done
  [ -n "$metrics" ] || metrics="{}"
  [ -n "$tick" ] || tick=0
  [ -n "$correct" ] || correct=null
  [ -n "$objective" ] || objective=null
  [ -n "$delta" ] || delta=0
  [ -n "$verify" ] || verify=null
  jq -c -n \
    --argjson tick "$tick" --arg action "$action" \
    --argjson correct "$correct" --argjson objective "$objective" \
    --argjson delta "$delta" --arg note "$note" \
    --arg commit "$commit" --arg diff_hash "$diff_hash" --arg agent_model "$agent_model" \
    --arg parent "$parent" --argjson metrics "$metrics" --argjson verify "$verify" \
    --arg ts "$(now_iso)" \
    '{tick:$tick, ts:$ts, action:$action, correct:$correct, objective:$objective, delta:$delta,
      note:$note, commit:(if $commit=="null" or $commit=="" then null else $commit end),
      parent:(if $parent=="null" or $parent=="" then null else $parent end),
      diff_hash:$diff_hash, agent_model:$agent_model, metrics:$metrics, verify:$verify}' \
    >> "$AVO_LEDGER" || die "ledger append failed"
}

# commit_candidate <score.json> <note> <parent-oid> : commit staged tree; verify HEAD advanced.
# Prints short hash on success; returns non-zero (prints nothing) on any failure.
commit_candidate() {
  local scorefile="$1" note="$2" parent="$3" obj corr
  obj=$(jq -r '.objective // "n/a"' "$scorefile")
  corr=$(jq -r '.correct' "$scorefile")
  # candidate files already staged by caller (git add -A). Fail if nothing to commit.
  git diff --cached --quiet && return 1   # no staged changes => nothing to commit
  git commit -q \
    -m "avo: tick $(state_get tick 0) (correct=$corr objective=$obj)" \
    -m "$note" \
    -m "avo-score: $(jq -c . "$scorefile")" >/dev/null 2>&1 || return 1
  local new; new=$(git rev-parse HEAD 2>/dev/null) || return 1
  [ "$new" != "$parent" ] || return 1
  git notes --ref=avo add -f -m "$(jq -c . "$scorefile")" HEAD >/dev/null 2>&1 || true
  git rev-parse --short HEAD
}

last_n_ledger() { [ -f "$AVO_LEDGER" ] && tail -n "${1:-6}" "$AVO_LEDGER" || true; }
