#!/usr/bin/env bash
# Example discover-mode scorer. A changed findings/*.md file with valid status is one
# correct resolved item; objective is monotone finding coverage. Pair with --verify.
set -o pipefail
cd "$1" || exit 3

changed=$(
  { git diff --cached --name-only -- 'findings/*.md' 2>/dev/null
    git diff --name-only -- 'findings/*.md' 2>/dev/null
    git ls-files --others --exclude-standard -- 'findings/*.md' 2>/dev/null; } |
  sed '/^$/d' | sort -u
)

correct=false
note="no new/changed finding this tick"
if [ -n "$changed" ]; then
  latest=$(printf '%s\n' "$changed" | while IFS= read -r file; do [ -f "$file" ] && printf '%s\n' "$file"; done | head -1)
  if [ -n "$latest" ] && grep -qiE '^status:[[:space:]]*(confirmed|dead-end|open)' "$latest"; then
    correct=true
    note=$(sed -n 's/^title:[[:space:]]*//p' "$latest" | head -1)
    [ -n "$note" ] || note="finding: $(basename "$latest")"
  else
    note="changed finding missing valid status frontmatter"
  fi
fi

coverage=$(
  { printf '%s\n' "$changed"; git ls-tree -r --name-only HEAD -- findings 2>/dev/null; } |
  sed '/^$/d' | sort -u | grep -c '\.md$'
)
coverage=${coverage:-0}

python3 - "$correct" "$coverage" "$note" <<'PY'
import json, sys
correct = sys.argv[1] == "true"
coverage = int(sys.argv[2])
print(json.dumps({
    "correct": correct,
    "objective": coverage,
    "metrics": {"coverage": coverage},
    "note": sys.argv[3],
}))
PY
