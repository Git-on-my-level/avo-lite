# shellcheck shell=bash
# AVO-lite commit ratchet: accept/reject decision for a scored candidate.

# decide_accept <score.json> : echo "accept" or "reject".
# rank:    accept iff correct AND objective > best + max(min_improvement_abs>=0, stddev>=0)
# discover: accept iff correct (append-only)
decide_accept() {
  local scorefile="$1" mode correct obj best margin min_abs stddev
  mode=$(cfg_or '' mode rank)
  correct=$(jq -r '.correct' "$scorefile")
  [ "$correct" = "true" ] || { echo reject; return; }

  if [ "$mode" = "discover" ]; then echo accept; return; fi

  # rank mode; objective guaranteed numeric by validate_score
  obj=$(jq -r '.objective' "$scorefile")
  best=$(state_get best_objective null)
  [ "$best" = "null" ] && { echo accept; return; }   # first correct candidate seeds lineage

  min_abs=$(cfg_num search min_improvement_abs 0)
  # clamp negatives to 0 so a misconfigured floor can never let a regression through
  min_abs=$(jq -n --argjson a "$min_abs" '[$a,0]|max')
  stddev=$(jq -r '(.metrics.stddev // 0) | if .<0 then 0 else . end' "$scorefile")
  margin=$(jq -n --argjson a "$min_abs" --argjson s "$stddev" '[$a,$s]|max')

  if jq -n --argjson o "$obj" --argjson b "$best" --argjson m "$margin" -e '$o > ($b + $m)' >/dev/null; then
    echo accept
  else
    echo reject
  fi
}

# score_delta <score.json> : objective - best (0 if incorrect or not computable). For the ledger.
score_delta() {
  local scorefile="$1" obj best
  [ "$(jq -r '.correct' "$scorefile")" = "true" ] || { echo 0; return; }
  obj=$(jq -r '(.objective // empty)' "$scorefile")
  best=$(state_get best_objective null)
  { [ -z "$obj" ] || [ "$best" = "null" ]; } && { echo 0; return; }
  jq -n --argjson o "$obj" --argjson b "$best" '$o - $b'
}
