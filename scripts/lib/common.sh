# shellcheck shell=bash
# AVO-lite common helpers. Sourced by `avo`. bash 3.2 safe (macOS + Linux).

set -o pipefail

AVO_DIR=".avo"
AVO_LEDGER="ledger.jsonl"
AVO_CONFIG="avo.toml"
AVO_STATE="$AVO_DIR/state.json"
AVO_REDIRECT="$AVO_DIR/redirect.json"
AVO_CMDS="$AVO_DIR/config.json"          # command strings live here (quote-safe), not in TOML
AVO_LOCK="$AVO_DIR/lock"

die()  { printf 'avo: %s\n' "$*" >&2; exit 1; }
warn() { printf 'avo: %s\n' "$*" >&2; }
info() { [ -n "$AVO_QUIET" ] || printf 'avo: %s\n' "$*" >&2; }

need() { command -v "$1" >/dev/null 2>&1 || die "missing dependency: $1"; }

now_iso() { date -u +%Y-%m-%dT%H:%M:%SZ; }

# --- concurrency lock (atomic mkdir; re-entrant in-process; released by trap) ------
# Auto-reclaims a lock whose owner PID is dead on this same host (crash recovery).
acquire_lock() {
  [ -n "$AVO_LOCK_HELD" ] && return 0     # re-entrant: this process already holds it
  mkdir "$AVO_DIR" 2>/dev/null || true
  if ! mkdir "$AVO_LOCK" 2>/dev/null; then
    local owner host pid
    owner=$(cat "$AVO_LOCK/owner" 2>/dev/null)
    host=$(printf '%s' "$owner" | sed -n 's/.*host=\([^ ]*\).*/\1/p')
    pid=$(printf '%s' "$owner"  | sed -n 's/.*pid=\([0-9]*\).*/\1/p')
    if [ "$host" = "$(hostname 2>/dev/null)" ] && [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; then
      warn "reclaiming stale lock (owner pid $pid is dead)"; rm -rf "$AVO_LOCK"
      mkdir "$AVO_LOCK" 2>/dev/null || die "could not acquire lock after reclaim ($AVO_LOCK)"
    else
      die "another avo command holds the lock ($AVO_LOCK, owner: ${owner:-unknown}). If it is stale: rm -rf $AVO_LOCK"
    fi
  fi
  printf 'pid=%s host=%s at=%s\n' "$$" "$(hostname 2>/dev/null)" "$(now_iso)" > "$AVO_LOCK/owner" 2>/dev/null || true
  AVO_LOCK_HELD=1
  trap 'release_lock' EXIT INT TERM
}
release_lock() { rm -rf "$AVO_LOCK" 2>/dev/null || true; AVO_LOCK_HELD=""; }

# --- tiny TOML reader (flat `key = value` and [section] key = value) --------
# Strips inline comments outside quotes. Values with '=' or quoted '#' via config.json instead.
cfg() {
  local want_sec="$1" want_key="$2" sec="" line k v
  [ -f "$AVO_CONFIG" ] || return 0
  while IFS= read -r line; do
    case "$line" in
      \#*|"") continue ;;
      \[*\]) sec=$(printf '%s' "$line" | tr -d '[] ' | sed 's/#.*//'); continue ;;
    esac
    case "$line" in *=*) : ;; *) continue ;; esac
    k=$(printf '%s' "${line%%=*}" | tr -d ' \t')
    v=${line#*=}
    v=$(printf '%s' "$v" | sed -e 's/^[[:space:]]*//')
    case "$v" in
      \"*) v=$(printf '%s' "$v" | sed -e 's/^"\([^"]*\)".*$/\1/') ;;
      \'*) v=$(printf '%s' "$v" | sed -e "s/^'\([^']*\)'.*$/\1/") ;;
      *)   v=$(printf '%s' "$v" | sed -e 's/[[:space:]]#.*$//' -e 's/[[:space:]]*$//') ;;
    esac
    if [ "$k" = "$want_key" ] && { [ -z "$want_sec" ] || [ "$sec" = "$want_sec" ]; }; then
      printf '%s' "$v"; return 0
    fi
  done < "$AVO_CONFIG"
}
cfg_or() { local val; val=$(cfg "$1" "$2"); [ -n "$val" ] && printf '%s' "$val" || printf '%s' "$3"; }

# numeric cfg with validation: cfg_num section key default  (dies on non-numeric)
cfg_num() {
  local v; v=$(cfg_or "$1" "$2" "$3")
  printf '%s' "$v" | jq -e 'type=="number"' >/dev/null 2>&1 && { printf '%s' "$v"; return; }
  # allow integers/floats as strings
  case "$v" in
    ''|*[!0-9.eE+-]*) die "config $1.$2 is not numeric: '$v'";;
    *) printf '%s' "$v";;
  esac
}

# --- command config (.avo/config.json): quote-safe agent/score/verify -------
getcmd() {  # getcmd agent|score|verify  ; env AVO_<X>_CMD overrides
    local key="$1" ev
    case "$key" in
      agent)  ev=AVO_AGENT_CMD ;; score) ev=AVO_SCORE_CMD ;; verify) ev=AVO_VERIFY_CMD ;;
      *) die "getcmd: unknown key $key" ;;
    esac
    if eval "[ \"\${$ev+set}\" = set ]"; then printf '%s' "$(eval "printf '%s' \"\$$ev\"")"; return; fi
    [ -f "$AVO_CMDS" ] && jq -r --arg k "$key" '.cmd[$k] // ""' "$AVO_CMDS" || printf ''
}

get_goal() { [ -f "$AVO_CMDS" ] && jq -r ".goal // \"\"" "$AVO_CMDS" || printf ""; }

# resolve model with env override (set-vs-unset aware): resolve_model driver|supervisor
resolve_model() {
  local ev; case "$1" in driver) ev=AVO_DRIVER_MODEL;; supervisor) ev=AVO_SUPERVISOR_MODEL;; esac
  if eval "[ \"\${$ev+set}\" = set ]"; then eval "printf '%s' \"\$$ev\""; return; fi
  cfg_or model "$1" ""
}

# --- state.json accessors (atomic, temp inside .avo) ------------------------
state_get() { [ -f "$AVO_STATE" ] || { printf '%s' "$2"; return; }
  jq -r --arg d "$2" ".${1} // \$d" "$AVO_STATE" 2>/dev/null || printf '%s' "$2"; }

state_set() {
  local f="$AVO_DIR/.state.tmp.$$"
  [ -f "$AVO_STATE" ] || printf '{}' > "$AVO_STATE"
  jq -e . "$AVO_STATE" >/dev/null 2>&1 || die "state.json is corrupt; refusing to proceed (inspect $AVO_STATE)"
  local filter="." i=0; local -a args
  for kv in "$@"; do
    local k="${kv%%=*}" v="${kv#*=}"
    filter="$filter | .${k} = \$v${i}"
    args+=(--argjson "v${i}" "$v")
    i=$((i+1))
  done
  jq "${args[@]}" "$filter" "$AVO_STATE" > "$f" && mv "$f" "$AVO_STATE" || die "state write failed"
}

git_repo_root() { git rev-parse --show-toplevel 2>/dev/null; }

require_task_root() {
  [ -f "$AVO_CONFIG" ] || die "not in an avo task root (no $AVO_CONFIG). Run 'avo init' first."
  jq -e . "$AVO_STATE" >/dev/null 2>&1 || die "state.json missing/corrupt; run from the task root or re-init."
}

# assert we are on the task branch (never touch main)
require_branch() {
  local want cur
  want=$(state_get task_branch '""' | tr -d '"')
  cur=$(git symbolic-ref --quiet --short HEAD 2>/dev/null)
  [ -n "$want" ] || return 0
  [ "$cur" = "$want" ] || die "refusing to run: on branch '$cur', task branch is '$want'. checkout '$want' first."
}

# Refuse a tick only for MODIFIED TRACKED files (a reject does `git reset --hard` and would lose them).
# Pre-existing UNTRACKED files are safe — they are snapshotted per tick and never deleted by the
# scoped reject-clean — so they only warn. AVO_ALLOW_DIRTY=1 (or --allow-dirty) overrides entirely.
require_clean_tree() {
  if [ -n "$AVO_ALLOW_DIRTY" ]; then
    warn "--allow-dirty: modified tracked files may be reset (lost) on a reject"; return 0
  fi
  local modified
  modified=$(git status --porcelain --untracked-files=no 2>/dev/null | grep -v -e '^!! ' || true)
  if [ -n "$modified" ]; then
    die "modified tracked files present (a reject resets tracked files and would lose these). Commit/stash them, or pass --allow-dirty:
$modified"
  fi
}
