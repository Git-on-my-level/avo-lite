#!/usr/bin/env bash
# Example scorer (discover mode, sparse-reward): security-research / hypothesis-shaped work
# where reward is binary and each finding is orthogonal (the security-research discovery pattern).
# Correctness = "THIS tick produced a valid, non-hallucinated finding" — judged on the finding
# files ADDED/CHANGED by the candidate diff (not merely the newest by mtime, which would let an
# unrelated edit re-bank a stale finding). Objective = monotone coverage counter over committed
# lineage + this candidate. Pair with `avo init --verify ...` for an adversarial falsification pass.
# Contract: <candidate-dir> -> score JSON on stdout.
set -o pipefail
cd "$1" || exit 3

# findings live under findings/*.md with frontmatter `status: confirmed|dead-end|open`.
# changed-this-tick = staged/working diff vs HEAD, restricted to findings/.
changed=$(git diff --cached --name-only -- 'findings/*.md' 2>/dev/null; \
          git diff --name-only -- 'findings/*.md' 2>/dev/null; \
          git ls-files --others --exclude-standard -- 'findings/*.md' 2>/dev/null)
changed=$(printf '%s\n' "$changed" | sed '/^$/d' | sort -u)

correct=false; note="no new/changed finding this tick"
if [ -n "$changed" ]; then
  latest=$(printf '%s\n' "$changed" | while IFS= read -r f; do [ -f "$f" ] && echo "$f"; done | head -1)
  if [ -n "$latest" ] && grep -qiE '^status:[[:space:]]*(confirmed|dead-end|open)' "$latest"; then
    correct=true
    note=$(sed -n 's/^title:[[:space:]]*//p' "$latest" | head -1)
    [ -n "$note" ] || note="finding: $(basename "$latest")"
  else
    note="changed finding missing valid status frontmatter"
  fi
fi

# coverage = committed findings (at HEAD) + this tick's changed set, de-duplicated
committed=$(git ls-tree -r --name-only HEAD -- 'findings' 2>/dev/null | grep -c '\.md$' || echo 0)
coverage=$(printf '%s\n' "$changed" | sed '/^$/d' | cat - <(git ls-tree -r --name-only HEAD -- 'findings' 2>/dev/null) | sort -u | grep -c '\.md$' || echo "$committed")

jq -n --argjson c "$correct" --argjson o "${coverage:-0}" --arg n "$note" \
  '{correct:$c, objective:$o, metrics:{coverage:$o}, note:$n}'
