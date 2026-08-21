# shellcheck shell=bash
# AVO-lite context builder: assemble the driver prompt from goal + K + lineage + redirect.

# build_context <out-prompt-file> [consumed-redirect-file]
build_context() {
  local out="$1" redir="$2" M
  M=$(cfg_num search context_entries 6)
  {
    cat "$AVO_DIR/prompts/driver.md" 2>/dev/null
    echo
    echo "## Goal"; get_goal; echo
    echo
    echo "## Mode"
    echo "$(cfg_or '' mode rank)  (rank = optimize objective via ratchet; discover = append every correct find)"
    echo

    if [ -s "K/INDEX.md" ]; then
      echo "## Knowledge base (K) — read these with your own tools as needed"
      echo '```'
      cat "K/INDEX.md" 2>/dev/null
      echo '```'
      echo
    fi

    local best_commit best_obj
    best_commit=$(state_get best_commit '""' | tr -d '"')
    best_obj=$(state_get best_objective null)
    if [ -n "$best_commit" ] && [ "$best_commit" != "null" ]; then
      echo "## Current best (objective=$best_obj, commit=$best_commit)"
      echo "The working tree already contains this best version. Improve on it."
      git log -1 --format='%b' "$best_commit" 2>/dev/null | grep -v '^avo-score:' | sed '/^$/d' | head -20
      echo
    fi

    echo "## Recent lineage (most recent last; accepts AND rejects — learn from both)"
    echo "Fields: tick, action, correct, objective, delta, metrics, note."
    last_n_ledger "$M" | jq -r '"- t\(.tick) \(.action) correct=\(.correct) obj=\(.objective // "-") d=\(.delta // "-") metrics=\(.metrics // {} | tojson)  \(.note // "")"' 2>/dev/null
    echo

    if [ -n "$redir" ] && [ -s "$redir" ]; then
      echo "## SUPERVISOR REDIRECT (priority — the search stalled; pursue one of these fresh directions)"
      jq -r '.directions[]? | "- \(.)"' "$redir" 2>/dev/null
      local rebase; rebase=$(jq -r '.rebase // empty' "$redir" 2>/dev/null)
      [ -n "$rebase" ] && echo "  (consider re-basing exploration on ancestor commit: $rebase)"
      echo
    fi

    echo "## Your task this tick"
    echo "Make ONE focused improvement to the candidate (the working tree). You own your inner loop:"
    echo "plan, edit, test, diagnose, and revise as many times as you need. Stop when you have a single"
    echo "coherent change worth scoring. Do not commit; the scaffold scores and commits for you."
  } > "$out"
}
