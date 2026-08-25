#!/usr/bin/env python3
"""AVO-lite: a small, durable optimization loop for coding agents.

Core dependencies: Python 3.8+ and git. Agent, scorer, verifier, and supervisor
commands are intentionally external process contracts.
"""

import argparse
import contextlib
import datetime as dt
import hashlib
import json
import math
import os
import re
import shutil
import shlex
import signal
import socket
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


VERSION = 2
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_AGENT = shlex.quote(str(SCRIPT_DIR / "adapters" / "agent-claude.sh"))
TERMINAL_ACTIONS = {"accept", "reject", "error", "preview"}


class AvoError(RuntimeError):
    pass


class HookResult:
    def __init__(self, returncode: int, stdout: Path, stderr: Path):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def quiet() -> bool:
    return bool(os.environ.get("AVO_QUIET"))


def info(message: str) -> None:
    if not quiet():
        print("avo: " + message, file=sys.stderr)


def warn(message: str) -> None:
    print("avo: " + message, file=sys.stderr)


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def json_dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True, ensure_ascii=False, allow_nan=False)


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix="." + path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_name)


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_text(path, json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n")


def load_json(path: Path) -> Any:
    try:
        raw = path.read_text(encoding="utf-8")
        return json.loads(raw, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except FileNotFoundError:
        raise AvoError("missing file: {}".format(path))
    except (json.JSONDecodeError, ValueError) as exc:
        raise AvoError("invalid JSON in {}: {}".format(path, exc))


def load_one_json(path: Path, label: str) -> Any:
    value = load_json(path)
    if not isinstance(value, dict):
        raise AvoError("{} must be exactly one JSON object: {}".format(label, path))
    return value


def run_process(
    argv: Sequence[str],
    cwd: Optional[Path] = None,
    check: bool = True,
    capture: bool = True,
    env: Optional[Dict[str, str]] = None,
) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(
            list(argv),
            cwd=str(cwd) if cwd else None,
            check=False,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
            text=True,
            env=env,
        )
    except FileNotFoundError:
        raise AvoError("missing dependency: {}".format(argv[0]))
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise AvoError("command failed ({}): {}{}".format(result.returncode, " ".join(argv), "\n" + detail if detail else ""))
    return result


def git(root: Path, args: Sequence[str], check: bool = True, capture: bool = True) -> subprocess.CompletedProcess:
    return run_process(["git", "-C", str(root)] + list(args), check=check, capture=capture)


def git_text(root: Path, args: Sequence[str]) -> str:
    return git(root, args).stdout.strip()


def git_root(cwd: Path) -> Optional[Path]:
    result = run_process(["git", "-C", str(cwd), "rev-parse", "--show-toplevel"], check=False)
    if result.returncode != 0:
        return None
    return Path(result.stdout.strip()).resolve()


def current_branch(root: Path) -> str:
    result = git(root, ["symbolic-ref", "--quiet", "--short", "HEAD"], check=False)
    return result.stdout.strip() if result.returncode == 0 else ""


def head_commit(root: Path) -> Optional[str]:
    result = git(root, ["rev-parse", "HEAD"], check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def parse_scalar(raw: str) -> Any:
    raw = raw.strip()
    if not raw:
        return ""
    if (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'")):
        return raw[1:-1]
    if raw.lower() in ("true", "false"):
        return raw.lower() == "true"
    try:
        if any(ch in raw for ch in ".eE"):
            return float(raw)
        return int(raw)
    except ValueError:
        return raw


def strip_toml_comment(line: str) -> str:
    quote = ""
    escaped = False
    out: List[str] = []
    for char in line:
        if escaped:
            out.append(char)
            escaped = False
            continue
        if char == "\\" and quote == '"':
            out.append(char)
            escaped = True
            continue
        if char in ("'", '"'):
            if not quote:
                quote = char
            elif quote == char:
                quote = ""
            out.append(char)
            continue
        if char == "#" and not quote:
            break
        out.append(char)
    return "".join(out).strip()


def load_legacy_toml(path: Path) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    if not path.exists():
        return result
    section = ""
    for original in path.read_text(encoding="utf-8").splitlines():
        line = strip_toml_comment(original)
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
            result.setdefault(section, {})
            continue
        if "=" not in line:
            continue
        key, raw = line.split("=", 1)
        key = key.strip()
        value = parse_scalar(raw)
        if section:
            result.setdefault(section, {})[key] = value
        else:
            result[key] = value
    return result


def merge_legacy_config(root: Path, raw: Dict[str, Any]) -> Dict[str, Any]:
    legacy = load_legacy_toml(root / "avo.toml")
    return {
        "version": 1,
        "task": current_branch(root).split("avo/", 1)[-1],
        "goal": raw.get("goal", ""),
        "mode": legacy.get("mode", "rank"),
        "cmd": dict(raw.get("cmd", {})),
        "model": dict(legacy.get("model", {})),
        "search": dict(legacy.get("search", {})),
        "report": dict(legacy.get("report", {})),
    }


def path_is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def deep_get(mapping: Dict[str, Any], path: Sequence[str], default: Any = None) -> Any:
    value: Any = mapping
    for key in path:
        if not isinstance(value, dict) or key not in value:
            return default
        value = value[key]
    return value


class Task:
    def __init__(self, root: Path, state_dir: Path):
        self.root = root.resolve()
        self.avo_dir = state_dir.resolve()
        self.config_path = self.avo_dir / "config.json"
        self.state_path = self.avo_dir / "state.json"
        self.ledger_path = self.avo_dir / "ledger.jsonl"
        self.memory_path = self.avo_dir / "memory.md"
        self.pins_path = self.avo_dir / "pins.md"
        self.redirect_path = self.avo_dir / "redirect.json"
        self.runs_dir = self.avo_dir / "runs"
        self.prompts_dir = self.avo_dir / "prompts"
        self.report_path = self.avo_dir / "report.log"
        self.lock_dir = self.avo_dir / "lock"
        self.knowledge_dir = self.avo_dir / "knowledge"
        self.config: Dict[str, Any] = {}
        self.legacy = False

    @classmethod
    def from_cwd(cls, require: bool = True) -> "Task":
        root = git_root(Path.cwd())
        if root is None:
            if require:
                raise AvoError("not in a git repository; run 'avo init' first")
            root = Path.cwd().resolve()
        state_override = os.environ.get("AVO_HOME")
        state_dir = Path(state_override).expanduser() if state_override else root / ".avo"
        if not state_dir.is_absolute():
            state_dir = root / state_dir
        task = cls(root, state_dir)
        if require:
            task.load()
        return task

    def load(self) -> None:
        if not self.config_path.exists():
            raise AvoError("not in an AVO task root (missing {})".format(self.config_path))
        raw = load_one_json(self.config_path, "config")
        if "version" not in raw:
            self.legacy = True
            self.config = merge_legacy_config(self.root, raw)
            old_ledger = self.root / "ledger.jsonl"
            if not self.ledger_path.exists() and old_ledger.exists():
                self.ledger_path = old_ledger
        else:
            self.config = raw
        if self.config.get("mode", "rank") not in ("rank", "discover"):
            raise AvoError("config mode must be 'rank' or 'discover'")
        if not self.state_path.exists():
            raise AvoError("missing state: {}".format(self.state_path))
        load_one_json(self.state_path, "state")

    def read_state(self) -> Dict[str, Any]:
        value = load_one_json(self.state_path, "state")
        defaults = {
            "version": VERSION,
            "tick": 0,
            "best_objective": None,
            "best_commit": None,
            "stall": 0,
            "redirects": 0,
            "status": "running",
            "last_action": "init",
            "active_run": None,
            "last_supervised_tick": 0,
            "last_reflect_tick": 0,
        }
        defaults.update(value)
        if defaults.get("best_commit") == "null":
            defaults["best_commit"] = None
        return defaults

    def write_state(self, state: Dict[str, Any]) -> None:
        state["version"] = VERSION
        atomic_write_json(self.state_path, state)

    def setting(self, section: str, key: str, default: Any) -> Any:
        return deep_get(self.config, [section, key], default)

    def command(self, name: str) -> str:
        env_name = {
            "agent": "AVO_AGENT_CMD",
            "score": "AVO_SCORE_CMD",
            "verify": "AVO_VERIFY_CMD",
            "supervisor": "AVO_SUPERVISOR_CMD",
        }[name]
        if env_name in os.environ:
            return os.environ[env_name]
        command = str(deep_get(self.config, ["cmd", name], "") or "")
        if name == "supervisor" and not command:
            command = str(deep_get(self.config, ["cmd", "agent"], "") or "")
        return command

    def model(self, name: str) -> str:
        env_name = "AVO_DRIVER_MODEL" if name == "driver" else "AVO_SUPERVISOR_MODEL"
        if env_name in os.environ:
            return os.environ[env_name]
        return str(deep_get(self.config, ["model", name], "") or "")

    @property
    def mode(self) -> str:
        return str(self.config.get("mode", "rank"))

    @property
    def goal(self) -> str:
        return str(self.config.get("goal", ""))


class TaskLock:
    def __init__(self, task: Task):
        self.task = task
        self.held = False

    def __enter__(self) -> "TaskLock":
        self.task.avo_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.task.lock_dir.mkdir()
        except FileExistsError:
            owner_path = self.task.lock_dir / "owner.json"
            owner: Dict[str, Any] = {}
            with contextlib.suppress(Exception):
                owner = load_json(owner_path)
            host = owner.get("host")
            pid = owner.get("pid")
            stale = host == socket.gethostname() and isinstance(pid, int) and not process_alive(pid)
            if stale:
                warn("reclaiming stale lock from pid {}".format(pid))
                shutil.rmtree(str(self.task.lock_dir), ignore_errors=True)
                try:
                    self.task.lock_dir.mkdir()
                except FileExistsError:
                    raise AvoError("could not reclaim lock: {}".format(self.task.lock_dir))
            else:
                raise AvoError("another AVO command holds {} (owner: {})".format(self.task.lock_dir, owner or "unknown"))
        atomic_write_json(
            self.task.lock_dir / "owner.json",
            {"pid": os.getpid(), "host": socket.gethostname(), "at": now_iso()},
        )
        self.held = True
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self.held:
            shutil.rmtree(str(self.task.lock_dir), ignore_errors=True)
            self.held = False


def process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def append_ledger(task: Task, entry: Dict[str, Any]) -> None:
    task.ledger_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "tick": int(entry.get("tick", 0)),
        "ts": entry.get("ts", now_iso()),
        "action": entry.get("action", "error"),
        "correct": entry.get("correct"),
        "objective": entry.get("objective"),
        "delta": entry.get("delta", 0),
        "note": entry.get("note", ""),
        "commit": entry.get("commit"),
        "parent": entry.get("parent"),
        "diff_hash": entry.get("diff_hash", ""),
        "agent_model": entry.get("agent_model", ""),
        "metrics": entry.get("metrics", {}),
        "verify": entry.get("verify"),
        "run_dir": entry.get("run_dir"),
    }
    with task.ledger_path.open("a", encoding="utf-8") as handle:
        handle.write(json_dumps(record) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def read_ledger_path(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    entries: List[Dict[str, Any]] = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AvoError("ledger parse error at {}:{}: {}".format(path, line_number, exc))
        if not isinstance(value, dict):
            raise AvoError("ledger entry at {}:{} is not an object".format(path, line_number))
        entries.append(value)
    return entries


def read_ledger(task: Task) -> List[Dict[str, Any]]:
    return read_ledger_path(task.ledger_path)


def ledger_has_terminal(task: Task, tick: int) -> Optional[Dict[str, Any]]:
    for entry in reversed(read_ledger(task)):
        if entry.get("tick") == tick and entry.get("action") in TERMINAL_ACTIONS:
            return entry
    return None


def redact(text: str) -> str:
    patterns = [
        (r"(Bearer )[A-Za-z0-9._-]{8,}", r"\1***REDACTED***"),
        (r"(sk-[A-Za-z0-9]{8})[A-Za-z0-9]+", r"\1***"),
        (r"(gh[pousr]_[A-Za-z0-9]{6})[A-Za-z0-9]+", r"\1***"),
        (r"(xox[baprs]-[A-Za-z0-9-]{6})[A-Za-z0-9-]+", r"\1***"),
        (r"([Aa]pi[_-]?[Kk]ey[\"' :=]+)[A-Za-z0-9._-]{8,}", r"\1***"),
        (r"([Pp]assword[\"' :=]+)[^ \"']+", r"\1***"),
    ]
    for pattern, replacement in patterns:
        text = re.sub(pattern, replacement, text)
    return text


def report_event(task: Task, message: str) -> None:
    task.report_path.parent.mkdir(parents=True, exist_ok=True)
    with task.report_path.open("a", encoding="utf-8") as handle:
        handle.write(redact("[{}] {}\n".format(now_iso(), message)))


def ensure_local_exclude(task: Task) -> None:
    try:
        relative = task.avo_dir.relative_to(task.root)
    except ValueError:
        return
    if not relative.parts:
        raise AvoError("AVO_HOME cannot be the repository root")
    exclude_path = Path(git_text(task.root, ["rev-parse", "--git-path", "info/exclude"]))
    if not exclude_path.is_absolute():
        exclude_path = task.root / exclude_path
    exclude_path.parent.mkdir(parents=True, exist_ok=True)
    pattern = "/{}/".format(relative.as_posix().rstrip("/"))
    existing = exclude_path.read_text(encoding="utf-8") if exclude_path.exists() else ""
    if pattern not in existing.splitlines():
        prefix = "" if not existing or existing.endswith("\n") else "\n"
        with exclude_path.open("a", encoding="utf-8") as handle:
            handle.write(prefix + pattern + "\n")


def require_branch(task: Task, state: Dict[str, Any]) -> None:
    expected = state.get("task_branch")
    actual = current_branch(task.root)
    if expected and actual != expected:
        raise AvoError("refusing to run on branch '{}'; task branch is '{}'".format(actual or "DETACHED", expected))


def require_clean(task: Task) -> None:
    result = git(task.root, ["status", "--porcelain", "--untracked-files=no"])
    if result.stdout.strip():
        raise AvoError("canonical worktree has modified tracked files; commit or stash them first:\n{}".format(result.stdout.rstrip()))


def run_hook(
    command: str,
    args: Sequence[Path],
    cwd: Path,
    stdout_path: Path,
    stderr_path: Path,
    env_updates: Optional[Dict[str, str]] = None,
) -> HookResult:
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    if env_updates:
        env.update({key: str(value) for key, value in env_updates.items()})
    argv = ["/bin/sh", "-c", command + ' "$@"', "avo-hook"] + [str(arg) for arg in args]
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        try:
            result = subprocess.run(argv, cwd=str(cwd), env=env, stdout=stdout, stderr=stderr, check=False)
        except FileNotFoundError:
            raise AvoError("missing /bin/sh; hook commands require a POSIX shell")
    return HookResult(result.returncode, stdout_path, stderr_path)


def validate_score(score: Dict[str, Any], mode: str) -> None:
    if not isinstance(score.get("correct"), bool):
        raise AvoError("score.correct must be boolean")
    correct = score["correct"]
    objective = score.get("objective")
    if correct and mode == "rank" and not is_number(objective):
        raise AvoError("rank mode requires numeric score.objective when correct=true")
    if objective is not None and not is_number(objective):
        raise AvoError("score.objective must be a finite number or null")
    metrics = score.get("metrics", {})
    if not isinstance(metrics, dict):
        raise AvoError("score.metrics must be an object")
    stddev = metrics.get("stddev")
    if stddev is not None and (not is_number(stddev) or float(stddev) < 0):
        raise AvoError("score.metrics.stddev must be a non-negative finite number")
    note = score.get("note", "")
    if note is not None and not isinstance(note, str):
        raise AvoError("score.note must be a string")
    artifacts = score.get("artifacts", [])
    if artifacts is not None and (not isinstance(artifacts, list) or not all(isinstance(item, str) for item in artifacts)):
        raise AvoError("score.artifacts must be an array of strings")


def validate_verify(value: Dict[str, Any]) -> None:
    if not isinstance(value.get("pass"), bool):
        raise AvoError("verify.pass must be boolean")
    if "note" in value and not isinstance(value["note"], str):
        raise AvoError("verify.note must be a string")
    if "evidence" in value and (
        not isinstance(value["evidence"], list) or not all(isinstance(item, str) for item in value["evidence"])
    ):
        raise AvoError("verify.evidence must be an array of strings")


def decide_accept(task: Task, state: Dict[str, Any], score: Dict[str, Any]) -> Tuple[bool, float, float]:
    if not score["correct"]:
        return False, 0.0, 0.0
    objective = score.get("objective")
    best = state.get("best_objective")
    if task.mode == "discover":
        delta = float(objective - best) if is_number(objective) and is_number(best) else 0.0
        return True, delta, 0.0
    if not is_number(objective):
        return False, 0.0, 0.0
    if not is_number(best):
        return True, 0.0, 0.0
    min_abs = task.setting("search", "min_improvement_abs", 0)
    if not is_number(min_abs):
        raise AvoError("search.min_improvement_abs must be numeric")
    min_abs = max(float(min_abs), 0.0)
    stddev = score.get("metrics", {}).get("stddev", 0)
    stddev = max(float(stddev), 0.0) if is_number(stddev) else 0.0
    margin = max(min_abs, stddev)
    delta = float(objective) - float(best)
    return float(objective) > float(best) + margin, delta, margin


def copy_artifacts(score: Dict[str, Any], candidate: Path, run_dir: Path) -> None:
    artifacts = score.get("artifacts") or []
    destination = run_dir / "artifacts"
    candidate_resolved = candidate.resolve()
    for raw in artifacts:
        source = (candidate / raw).resolve()
        try:
            source.relative_to(candidate_resolved)
        except ValueError:
            warn("skipping artifact outside candidate: {}".format(raw))
            continue
        if not source.is_file():
            warn("skipping missing/non-file artifact: {}".format(raw))
            continue
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(source), str(destination / source.name))


def create_worktree(task: Task, path: Path, base_commit: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        remove_worktree(task, path)
    result = git(task.root, ["worktree", "add", "--detach", "--force", str(path), base_commit], check=False)
    if result.returncode != 0:
        raise AvoError("could not create candidate worktree:\n{}".format((result.stderr or result.stdout).strip()))


def remove_worktree(task: Task, path: Path) -> None:
    if path.exists():
        git(task.root, ["worktree", "remove", "--force", str(path)], check=False)
    if path.exists():
        shutil.rmtree(str(path), ignore_errors=True)
    git(task.root, ["worktree", "prune"], check=False)


def cleanup_orphan_worktrees(task: Task, active_path: Optional[str] = None) -> None:
    if not task.runs_dir.exists():
        return
    active = Path(active_path).resolve() if active_path else None
    for path in task.runs_dir.glob("*/worktree"):
        if active and path.resolve() == active:
            continue
        remove_worktree(task, path)


def build_patch(candidate: Path, base_commit: str, patch_path: Path) -> Tuple[str, bool]:
    git(candidate, ["reset", "--soft", base_commit])
    git(candidate, ["add", "-A"])
    result = git(candidate, ["diff", "--cached", "--binary", "--full-index", base_commit], capture=True)
    data = result.stdout.encode("utf-8", errors="surrogateescape")
    patch_path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()[:16], bool(data)


def run_relative(task: Task, run_dir: Path) -> str:
    try:
        return str(run_dir.relative_to(task.avo_dir))
    except ValueError:
        return str(run_dir)


def format_ledger_entry(entry: Dict[str, Any]) -> str:
    verify = entry.get("verify")
    verify_note = ""
    if isinstance(verify, dict) and verify.get("note"):
        verify_note = " verify={}".format(str(verify["note"]).replace("\n", " ")[:160])
    note = str(entry.get("note") or "").replace("\n", " ")[:240]
    return "- t{tick} {action} correct={correct} obj={objective} delta={delta} diff={diff_hash} {note}{verify}".format(
        tick=entry.get("tick", "?"),
        action=entry.get("action", "?"),
        correct=entry.get("correct"),
        objective=entry.get("objective", "-"),
        delta=entry.get("delta", "-"),
        diff_hash=entry.get("diff_hash", ""),
        note=note,
        verify=verify_note,
    )


def read_optional(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""


def knowledge_index(task: Task) -> Tuple[Optional[Path], str]:
    local = task.knowledge_dir / "INDEX.md"
    legacy = task.root / "K" / "INDEX.md"
    if local.exists():
        return local, read_optional(local)
    if legacy.exists():
        return legacy, read_optional(legacy)
    return None, ""


def consume_redirect(task: Task, run_dir: Path) -> Optional[Dict[str, Any]]:
    if not task.redirect_path.exists():
        return None
    consumed = run_dir / "redirect.consumed.json"
    os.replace(str(task.redirect_path), str(consumed))
    value = load_one_json(consumed, "redirect")
    directions = value.get("directions")
    if not isinstance(directions, list) or not all(isinstance(item, str) and item.strip() for item in directions):
        raise AvoError("redirect.directions must be a non-empty array of strings")
    return value


def build_driver_prompt(task: Task, state: Dict[str, Any], run_dir: Path, redirect: Optional[Dict[str, Any]]) -> Path:
    entries = read_ledger(task)
    count = task.setting("search", "context_entries", 8)
    if not isinstance(count, int) or count < 1:
        raise AvoError("search.context_entries must be a positive integer")
    prompt_path = run_dir / "prompt.md"
    sections: List[str] = [read_optional(task.prompts_dir / "driver.md")]
    sections += ["## Goal\n{}".format(task.goal), "## Mode\n{}".format(task.mode)]

    pins = read_optional(task.pins_path)
    if pins:
        sections.append("## Human pins — authoritative\n{}".format(pins))
    memory = read_optional(task.memory_path)
    if memory:
        sections.append("## Curated memory\n{}".format(memory))
    index_path, index = knowledge_index(task)
    if index:
        sections.append("## Knowledge index\nPath: `{}`\n\n{}".format(index_path, index))

    current = state.get("best_commit") or head_commit(task.root)
    if current:
        sections.append(
            "## Current candidate\nThe disposable worktree starts from commit `{}`. Best objective: `{}`.".format(
                current, state.get("best_objective")
            )
        )
    recent = entries[-count:]
    if recent:
        sections.append("## Recent attempts — accepted and rejected\n" + "\n".join(format_ledger_entry(item) for item in recent))
    if redirect:
        lines = "\n".join("- " + item for item in redirect.get("directions", []))
        sections.append("## Supervisor redirect — prioritize a genuinely different direction\n" + lines)

    sections.append(
        "## This tick\n"
        "Make one focused, coherent improvement in the candidate worktree. You own the inner loop: "
        "inspect, edit, test, diagnose, and revise until the change is worth scoring. Do not push. "
        "Do not edit AVO state. Do not commit; AVO records the accepted version."
    )
    atomic_write_text(prompt_path, "\n\n".join(section for section in sections if section).strip() + "\n")
    return prompt_path


def build_supervisor_prompt(task: Task, state: Dict[str, Any], run_dir: Path, reason: str) -> Path:
    entries = read_ledger(task)
    max_entries = task.setting("search", "supervisor_context_entries", 60)
    if not isinstance(max_entries, int) or max_entries < 1:
        max_entries = 60
    sections = [
        read_optional(task.prompts_dir / "supervisor.md"),
        "## Goal\n{}".format(task.goal),
        "## Trigger\n{}".format(reason),
    ]
    pins = read_optional(task.pins_path)
    if pins:
        sections.append("## Human pins — preserve these\n{}".format(pins))
    memory = read_optional(task.memory_path)
    if memory:
        sections.append("## Existing curated memory\n{}".format(memory))
    sections.append(
        "## Trajectory — includes failures because they define the local optimum\n"
        + "\n".join(format_ledger_entry(item) for item in entries[-max_entries:])
    )
    sections.append(
        "## Output\nThe last line must be one compact JSON object:\n"
        '`{"directions":["fresh direction 1","fresh direction 2"],"memory":"optional complete replacement for memory.md"}`\n'
        "Give 2-4 concrete, materially different directions. `memory` is optional. Any reasoning must precede the final JSON line."
    )
    path = run_dir / "prompt.md"
    atomic_write_text(path, "\n\n".join(section for section in sections if section).strip() + "\n")
    return path


def build_reflect_prompt(task: Task, state: Dict[str, Any], run_dir: Path) -> Path:
    entries = read_ledger(task)
    max_entries = task.setting("search", "supervisor_context_entries", 60)
    if not isinstance(max_entries, int) or max_entries < 1:
        max_entries = 60
    sections = [
        read_optional(task.prompts_dir / "reflect.md"),
        "## Goal\n{}".format(task.goal),
    ]
    pins = read_optional(task.pins_path)
    if pins:
        sections.append("## Human pins — preserve these\n{}".format(pins))
    memory = read_optional(task.memory_path)
    if memory:
        sections.append("## Existing memory\n{}".format(memory))
    sections.append("## Recent trajectory\n" + "\n".join(format_ledger_entry(item) for item in entries[-max_entries:]))
    sections.append("## Output\nOutput only the complete replacement Markdown for memory.md. Keep it concise and evidence-grounded.")
    path = run_dir / "prompt.md"
    atomic_write_text(path, "\n\n".join(section for section in sections if section).strip() + "\n")
    return path


def parse_supervisor_output(path: Path) -> Dict[str, Any]:
    raw = path.read_text(encoding="utf-8").strip()
    candidates = [raw] + list(reversed(raw.splitlines()))
    for candidate in candidates:
        candidate = candidate.strip()
        if not candidate.startswith("{"):
            continue
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(value, dict):
            continue
        directions = value.get("directions")
        if isinstance(directions, list) and 1 <= len(directions) <= 8 and all(
            isinstance(item, str) and item.strip() for item in directions
        ):
            if "memory" in value and not isinstance(value["memory"], str):
                continue
            return value
    raise AvoError("supervisor produced no valid final redirect JSON")


def detect_stall_entries(
    entries: List[Dict[str, Any]],
    stall_window: int = 8,
    cycle_window: int = 10,
    reject_ratio: float = 0.8,
    repeat_edit_max: int = 3,
) -> str:
    # A successful accept, a supervisor redirect, or an explicit human resume starts a fresh search segment.
    segment_start = 0
    for index, entry in enumerate(entries):
        if entry.get("action") in ("accept", "redirect", "resume"):
            segment_start = index + 1
    candidates = [entry for entry in entries[segment_start:] if entry.get("action") in ("accept", "reject")]
    if not candidates:
        return ""
    since_progress = len(candidates)
    if since_progress >= stall_window:
        return "stall: {} candidate attempts since last progress/redirect (>= {})".format(since_progress, stall_window)

    window = candidates[-cycle_window:]
    if len(window) < cycle_window:
        return ""
    hashes = [str(item.get("diff_hash") or "") for item in window]
    max_repeat = max(Counter(hashes).values()) if hashes else 0
    if max_repeat >= repeat_edit_max:
        return "repeat-edits: identical diff {}x in last {} attempts".format(max_repeat, cycle_window)
    rejects = sum(1 for entry in window if entry.get("action") == "reject")
    ratio = rejects / float(len(window))
    if ratio >= reject_ratio:
        return "unproductive: reject-ratio {}% over last {} attempts".format(int(ratio * 100), cycle_window)
    return ""


def detect_stall(task: Task) -> str:
    stall_window = task.setting("search", "stall_window", 8)
    cycle_window = task.setting("search", "cycle_window", 10)
    reject_ratio = task.setting("search", "reject_ratio", 0.8)
    repeat_edit_max = task.setting("search", "repeat_edit_max", 3)
    if not isinstance(stall_window, int) or stall_window < 1:
        raise AvoError("search.stall_window must be a positive integer")
    if not isinstance(cycle_window, int) or cycle_window < 1:
        raise AvoError("search.cycle_window must be a positive integer")
    if not is_number(reject_ratio) or not 0 <= float(reject_ratio) <= 1:
        raise AvoError("search.reject_ratio must be between 0 and 1")
    if not isinstance(repeat_edit_max, int) or repeat_edit_max < 2:
        raise AvoError("search.repeat_edit_max must be an integer >= 2")
    return detect_stall_entries(
        read_ledger(task), stall_window, cycle_window, float(reject_ratio), repeat_edit_max
    )


def sync_state_from_entry(state: Dict[str, Any], entry: Dict[str, Any]) -> None:
    action = entry.get("action")
    state["last_action"] = action
    if action == "accept":
        state["best_commit"] = entry.get("commit")
        if is_number(entry.get("objective")):
            if not is_number(state.get("best_objective")) or float(entry["objective"]) > float(state["best_objective"]):
                state["best_objective"] = entry["objective"]
        state["stall"] = 0
        state["redirects"] = 0
        state["status"] = "running"
    elif action == "reject":
        state["stall"] = int(state.get("stall", 0)) + 1
    elif action == "error":
        state["status"] = state.get("status", "running")


def recover_interrupted(task: Task, state: Dict[str, Any]) -> Dict[str, Any]:
    active = state.get("active_run")
    if not isinstance(active, dict):
        cleanup_orphan_worktrees(task)
        return state
    tick = int(active.get("tick", state.get("tick", 0)))
    worktree = Path(active.get("worktree", "")) if active.get("worktree") else None
    existing = ledger_has_terminal(task, tick)
    if existing:
        sync_state_from_entry(state, existing)
    else:
        base = active.get("base_commit")
        phase = active.get("phase", "unknown")
        current = head_commit(task.root)
        pending = active.get("pending") if isinstance(active.get("pending"), dict) else None
        recovered_accept = False
        if phase == "finalizing" and base and current and current != base and pending:
            parent = git_text(task.root, ["rev-parse", "{}^".format(current)])
            subject = git_text(task.root, ["show", "-s", "--format=%s", current])
            if parent == base and subject.startswith("avo: tick {} ".format(tick)):
                entry = dict(pending)
                entry["commit"] = current
                append_ledger(task, entry)
                sync_state_from_entry(state, entry)
                recovered_accept = True
                warn("recovered accepted tick {} after interruption".format(tick))
        if not recovered_accept:
            if base and current == base:
                git(task.root, ["reset", "--hard", base], check=False)
            elif base and current != base:
                raise AvoError(
                    "interrupted run {} left canonical HEAD at unexpected commit {}; inspect before continuing".format(tick, current)
                )
            entry = {
                "tick": tick,
                "action": "error",
                "correct": None,
                "objective": None,
                "delta": 0,
                "note": "interrupted during {}".format(phase),
                "parent": base,
                "diff_hash": active.get("diff_hash", ""),
                "agent_model": active.get("agent_model", ""),
                "run_dir": active.get("run_dir"),
            }
            append_ledger(task, entry)
            sync_state_from_entry(state, entry)
            warn("recovered interrupted tick {} ({})".format(tick, phase))
    if worktree:
        remove_worktree(task, worktree)
    state["active_run"] = None
    task.write_state(state)
    cleanup_orphan_worktrees(task)
    return state


def set_active(task: Task, state: Dict[str, Any], active: Dict[str, Any]) -> None:
    state["active_run"] = active
    task.write_state(state)


def finish_run(
    task: Task,
    state: Dict[str, Any],
    entry: Dict[str, Any],
    worktree: Optional[Path],
) -> Dict[str, Any]:
    append_ledger(task, entry)
    sync_state_from_entry(state, entry)
    if worktree:
        remove_worktree(task, worktree)
    state["active_run"] = None
    task.write_state(state)
    return state


def make_run_dir(task: Task, name: str) -> Path:
    run_dir = task.runs_dir / name
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir


def score_candidate(task: Task, candidate: Path, run_dir: Path) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    command = task.command("score")
    if not command:
        return None, "no score command configured"
    result = run_hook(command, [candidate], candidate, run_dir / "score.json", run_dir / "score.err")
    if result.returncode != 0:
        return None, "score command infra failure (exit {})".format(result.returncode)
    try:
        score = load_one_json(run_dir / "score.json", "score")
        validate_score(score, task.mode)
    except AvoError as exc:
        return None, "invalid score: {}".format(exc)
    copy_artifacts(score, candidate, run_dir)
    return score, None


def verify_candidate(task: Task, candidate: Path, run_dir: Path) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    command = task.command("verify")
    if not command:
        return None, None
    result = run_hook(
        command,
        [candidate, run_dir / "score.json"],
        candidate,
        run_dir / "verify.json",
        run_dir / "verify.err",
    )
    if result.returncode != 0:
        return None, "verify command infra failure (exit {})".format(result.returncode)
    try:
        value = load_one_json(run_dir / "verify.json", "verify")
        validate_verify(value)
        return value, None
    except AvoError as exc:
        return None, "invalid verify result: {}".format(exc)


def apply_candidate(
    task: Task,
    state: Dict[str, Any],
    run_dir: Path,
    patch_path: Path,
    base_commit: str,
    score: Dict[str, Any],
    verify: Optional[Dict[str, Any]],
    entry: Dict[str, Any],
) -> str:
    require_branch(task, state)
    require_clean(task)
    if head_commit(task.root) != base_commit:
        raise AvoError("canonical HEAD changed during tick; refusing to apply candidate")
    active = dict(state.get("active_run") or {})
    active["phase"] = "finalizing"
    active["pending"] = entry
    set_active(task, state, active)

    apply_result = git(task.root, ["apply", "--index", "--binary", str(patch_path)], check=False)
    if apply_result.returncode != 0:
        git(task.root, ["reset", "--hard", base_commit], check=False)
        raise AvoError("candidate patch did not apply cleanly: {}".format((apply_result.stderr or apply_result.stdout).strip()))
    objective = score.get("objective")
    note = str(score.get("note") or "")
    subject = "avo: tick {} (correct=true objective={})".format(state["tick"], objective if objective is not None else "n/a")
    commit_args = ["commit", "-m", subject]
    if note:
        commit_args += ["-m", note]
    commit_args += ["-m", "avo-score: " + json_dumps(score)]
    commit_result = git(task.root, commit_args, check=False)
    if commit_result.returncode != 0:
        git(task.root, ["reset", "--hard", base_commit], check=False)
        raise AvoError("accepted candidate could not be committed: {}".format((commit_result.stderr or commit_result.stdout).strip()))
    commit = git_text(task.root, ["rev-parse", "HEAD"])
    git(task.root, ["notes", "--ref=avo", "add", "-f", "-m", json_dumps({"score": score, "verify": verify}), commit], check=False)
    return commit


def supervisor_internal(task: Task, state: Dict[str, Any], reason: str, automatic: bool) -> bool:
    command = task.command("supervisor")
    if not command:
        warn("stall detected but no supervisor command is configured")
        state["status"] = "stalled"
        state["last_action"] = "stalled"
        task.write_state(state)
        return False
    name = "supervise-{:06d}-{}".format(int(state.get("tick", 0)), int(time.time()))
    run_dir = make_run_dir(task, name)
    worktree = run_dir / "worktree"
    base = head_commit(task.root)
    if not base:
        raise AvoError("repository has no HEAD")
    create_worktree(task, worktree, base)
    try:
        prompt = build_supervisor_prompt(task, state, run_dir, reason)
        model = task.model("supervisor")
        result = run_hook(
            command,
            [worktree, prompt],
            worktree,
            run_dir / "supervisor.stdout",
            run_dir / "supervisor.stderr",
            {"AVO_SUPERVISOR_MODEL": model},
        )
        if result.returncode != 0:
            note = "supervisor failed with exit {}".format(result.returncode)
            append_ledger(task, {"tick": state.get("tick", 0), "action": "supervisor_error", "note": note, "run_dir": run_relative(task, run_dir)})
            state["status"] = "stalled" if automatic else state.get("status", "running")
            state["last_action"] = "supervisor_error"
            task.write_state(state)
            warn(note)
            return False
        try:
            value = parse_supervisor_output(run_dir / "supervisor.stdout")
        except AvoError as exc:
            append_ledger(task, {"tick": state.get("tick", 0), "action": "supervisor_error", "note": str(exc), "run_dir": run_relative(task, run_dir)})
            state["status"] = "stalled" if automatic else state.get("status", "running")
            state["last_action"] = "supervisor_error"
            task.write_state(state)
            warn(str(exc))
            return False
        atomic_write_json(task.redirect_path, {"directions": value["directions"], "created_at": now_iso(), "reason": reason})
        if value.get("memory", "").strip():
            atomic_write_text(task.memory_path, value["memory"].strip() + "\n")
        state["stall"] = 0
        state["redirects"] = int(state.get("redirects", 0)) + 1
        state["last_supervised_tick"] = int(state.get("tick", 0))
        state["last_action"] = "redirect"
        state["status"] = "running"
        task.write_state(state)
        append_ledger(
            task,
            {
                "tick": state.get("tick", 0),
                "action": "redirect",
                "note": "{}: {}".format(reason, "; ".join(value["directions"])),
                "diff_hash": "redirect",
                "agent_model": model,
                "run_dir": run_relative(task, run_dir),
            },
        )
        report_event(task, "supervisor redirect: {}".format("; ".join(value["directions"])))
        info("supervisor redirect written")
        return True
    finally:
        remove_worktree(task, worktree)


def maybe_supervise(task: Task, state: Dict[str, Any]) -> None:
    reason = detect_stall(task)
    if not reason:
        return
    warn(reason)
    max_redirects = task.setting("search", "max_redirects", 2)
    if not isinstance(max_redirects, int) or max_redirects < 0:
        raise AvoError("search.max_redirects must be a non-negative integer")
    if int(state.get("redirects", 0)) >= max_redirects:
        state["status"] = "stalled"
        state["last_action"] = "stalled"
        task.write_state(state)
        append_ledger(task, {"tick": state.get("tick", 0), "action": "stalled", "note": reason})
        report_event(task, "stalled after {} redirect(s): {}".format(max_redirects, reason))
        warn("task marked stalled; add/update pins and run 'avo resume'")
        return
    supervisor_internal(task, state, reason, automatic=True)


def new_config(task_name: str, goal: str, mode: str, agent: str, score: str, verify: str, supervisor: str) -> Dict[str, Any]:
    return {
        "version": VERSION,
        "task": task_name,
        "goal": goal,
        "mode": mode,
        "cmd": {"agent": agent, "score": score, "verify": verify, "supervisor": supervisor},
        "model": {"driver": "", "supervisor": ""},
        "search": {
            "context_entries": 8,
            "supervisor_context_entries": 60,
            "min_improvement_abs": 0,
            "stall_window": 8,
            "cycle_window": 10,
            "reject_ratio": 0.8,
            "repeat_edit_max": 3,
            "max_redirects": 2,
            "score_on_agent_error": False,
        },
        "report": {"notify_min_objective": None},
    }


def init_prompts(task: Task) -> None:
    task.prompts_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        task.prompts_dir / "driver.md",
        "You are the AVO-lite variation operator. Improve the current candidate using one focused, coherent change. "
        "Use tests and local experiments freely. Learn from both accepted and rejected attempts.\n",
    )
    atomic_write_text(
        task.prompts_dir / "supervisor.md",
        "You are the AVO-lite supervisor. The search has stalled. Diagnose the trajectory, especially repeated failures, "
        "then propose materially different directions. Preserve human pins. Do not edit or commit code.\n",
    )
    atomic_write_text(
        task.prompts_dir / "reflect.md",
        "Distill the trajectory into compact durable memory for the next agents. Record what worked, what failed, current "
        "hypotheses, and closed directions. Do not invent evidence.\n",
    )


def cmd_init(args: argparse.Namespace) -> int:
    root = git_root(Path.cwd())
    if root is None:
        run_process(["git", "init", "-q", str(Path.cwd())], capture=True)
        root = git_root(Path.cwd())
    if root is None:
        raise AvoError("git init failed")
    state_override = os.environ.get("AVO_HOME")
    state_dir = Path(state_override).expanduser() if state_override else root / ".avo"
    if not state_dir.is_absolute():
        state_dir = root / state_dir
    task = Task(root, state_dir)
    if task.config_path.exists():
        raise AvoError("{} already exists; refusing to overwrite task state".format(task.config_path))
    branch = "avo/{}".format(args.task)
    check_ref = git(root, ["check-ref-format", "--branch", branch], check=False)
    if check_ref.returncode != 0:
        raise AvoError("invalid task name for git branch: {}".format(args.task))

    ensure_local_exclude(task)
    state_inside_repo = path_is_relative_to(task.avo_dir, root)
    tracked_state = git(root, ["ls-files", "--", str(task.avo_dir.relative_to(root))], check=False) if state_inside_repo else None
    if tracked_state is not None and tracked_state.stdout.strip():
        raise AvoError("AVO state directory is already tracked; untrack it before init")

    status = git(root, ["status", "--porcelain", "--untracked-files=all"]).stdout.strip()
    if status:
        warn("uncommitted files will be captured in baseline v0:\n{}".format(status))
    branch_exists = git(root, ["show-ref", "--verify", "--quiet", "refs/heads/" + branch], check=False).returncode == 0
    switch_args = ["switch", branch] if branch_exists else ["switch", "-c", branch]
    switched = git(root, switch_args, check=False)
    if switched.returncode != 0:
        # git switch is unavailable on very old git; checkout is the portable fallback.
        fallback = ["checkout", branch] if branch_exists else ["checkout", "-b", branch]
        git(root, fallback)

    task.avo_dir.mkdir(parents=True, exist_ok=True)
    task.runs_dir.mkdir(parents=True, exist_ok=True)
    task.knowledge_dir.mkdir(parents=True, exist_ok=True)
    config = new_config(
        args.task,
        args.goal,
        args.mode,
        args.agent or DEFAULT_AGENT,
        args.score or "",
        args.verify or "",
        args.supervisor or "",
    )
    atomic_write_json(task.config_path, config)
    init_prompts(task)
    atomic_write_text(task.memory_path, "# Current understanding\n\n_No durable lessons recorded yet._\n")
    atomic_write_text(task.pins_path, "# Human pins\n\n")
    if args.k:
        source = Path(args.k).expanduser().resolve()
        if not source.is_dir():
            raise AvoError("--k is not a directory: {}".format(source))
        shutil.copytree(str(source), str(task.knowledge_dir), dirs_exist_ok=True)
    index = task.knowledge_dir / "INDEX.md"
    if not index.exists():
        atomic_write_text(index, "# Knowledge index\n\nAdd one line per useful reference.\n")

    task.config = config
    configured_score = task.command("score")
    state = {
        "version": VERSION,
        "task_branch": branch,
        "tick": 0,
        "best_objective": None,
        "best_commit": None,
        "stall": 0,
        "redirects": 0,
        "status": "running",
        "last_action": "init",
        "active_run": None,
        "last_supervised_tick": 0,
        "last_reflect_tick": 0,
        "preview": not bool(configured_score),
    }
    task.write_state(state)

    git(root, ["add", "-A"])
    commit = git(root, ["commit", "--allow-empty", "-m", "avo: init task '{}' (baseline v0)".format(args.task)], check=False)
    if commit.returncode != 0:
        raise AvoError("baseline commit failed; configure git user.name/user.email and retry")
    base = head_commit(root)
    if not base:
        raise AvoError("baseline commit did not produce HEAD")

    baseline_dir = task.runs_dir / "000000"
    baseline_dir.mkdir(exist_ok=True)
    if configured_score:
        info("scoring baseline v0")
        score, error = score_candidate(task, root, baseline_dir)
        if error:
            warn("baseline was not seeded: {}".format(error))
            append_ledger(task, {"tick": 0, "action": "baseline_error", "note": error, "commit": base, "diff_hash": "baseline"})
        else:
            assert score is not None
            correct = bool(score["correct"])
            objective = score.get("objective") if correct else None
            if correct:
                state["best_commit"] = base
                state["best_objective"] = objective
            task.write_state(state)
            append_ledger(
                task,
                {
                    "tick": 0,
                    "action": "baseline",
                    "correct": correct,
                    "objective": objective,
                    "delta": 0,
                    "note": score.get("note", "baseline"),
                    "commit": base,
                    "parent": git_text(root, ["rev-parse", "{}^".format(base)]) if git(root, ["rev-parse", "{}^".format(base)], check=False).returncode == 0 else None,
                    "diff_hash": "baseline",
                    "metrics": score.get("metrics", {}),
                    "run_dir": run_relative(task, baseline_dir),
                },
            )
    else:
        append_ledger(task, {"tick": 0, "action": "baseline", "correct": None, "objective": None, "note": "preview baseline", "commit": base, "diff_hash": "baseline"})
        warn("no --score given: preview mode; ticks save diffs but accept nothing")
    info("initialized '{}' on {} (baseline {})".format(args.task, branch, base[:12]))
    return 0


def cmd_tick(args: argparse.Namespace) -> int:
    task = Task.from_cwd()
    with TaskLock(task):
        state = recover_interrupted(task, task.read_state())
        require_branch(task, state)
        require_clean(task)
        if state.get("status") == "stalled" and not args.force:
            raise AvoError("task is stalled; update pins/config and run 'avo resume' (or tick --force)")
        agent = task.command("agent")
        if not agent:
            raise AvoError("no agent command configured")
        preview = not bool(task.command("score"))
        if not preview and state.get("preview"):
            state["preview"] = False
            task.write_state(state)
        tick = int(state.get("tick", 0)) + 1
        state["tick"] = tick
        base = head_commit(task.root)
        if not base:
            raise AvoError("repository has no HEAD")
        run_dir = make_run_dir(task, "{:06d}".format(tick))
        worktree = run_dir / "worktree"
        active = {
            "tick": tick,
            "phase": "creating_worktree",
            "base_commit": base,
            "worktree": str(worktree),
            "run_dir": run_relative(task, run_dir),
            "agent_model": task.model("driver"),
        }
        set_active(task, state, active)
        create_worktree(task, worktree, base)
        try:
            redirect = consume_redirect(task, run_dir)
            prompt = build_driver_prompt(task, state, run_dir, redirect)
            active["phase"] = "agent"
            set_active(task, state, active)
            info("tick {}: running agent (model={})".format(tick, task.model("driver") or "default"))
            result = run_hook(
                agent,
                [worktree, prompt],
                worktree,
                run_dir / "agent.stdout",
                run_dir / "agent.stderr",
                {"AVO_TICK": str(tick), "AVO_DRIVER_MODEL": task.model("driver")},
            )
            active["agent_rc"] = result.returncode
            active["phase"] = "capturing"
            set_active(task, state, active)
            diff_hash, has_diff = build_patch(worktree, base, run_dir / "diff.patch")
            active["diff_hash"] = diff_hash
            set_active(task, state, active)

            if preview:
                entry = {
                    "tick": tick,
                    "action": "preview",
                    "note": "preview: agent_rc={} diff={}".format(result.returncode, "yes" if has_diff else "no"),
                    "parent": base,
                    "diff_hash": diff_hash,
                    "agent_model": task.model("driver"),
                    "run_dir": run_relative(task, run_dir),
                }
                finish_run(task, state, entry, worktree)
                info("tick {}: preview saved to {}".format(tick, run_dir / "diff.patch"))
                return 0

            score_on_error = bool(task.setting("search", "score_on_agent_error", False))
            if result.returncode != 0 and not (score_on_error and has_diff):
                entry = {
                    "tick": tick,
                    "action": "error",
                    "note": "agent exited {}".format(result.returncode),
                    "parent": base,
                    "diff_hash": diff_hash,
                    "agent_model": task.model("driver"),
                    "run_dir": run_relative(task, run_dir),
                }
                finish_run(task, state, entry, worktree)
                report_event(task, "tick {} error: agent exited {}".format(tick, result.returncode))
                return 0
            if not has_diff:
                entry = {
                    "tick": tick,
                    "action": "reject",
                    "correct": None,
                    "objective": None,
                    "delta": 0,
                    "note": "agent produced no diff",
                    "parent": base,
                    "diff_hash": diff_hash,
                    "agent_model": task.model("driver"),
                    "run_dir": run_relative(task, run_dir),
                }
                state = finish_run(task, state, entry, worktree)
                info("tick {}: reject (no diff)".format(tick))
                maybe_supervise(task, state)
                return 0

            active["phase"] = "scoring"
            set_active(task, state, active)
            info("tick {}: scoring".format(tick))
            score, score_error = score_candidate(task, worktree, run_dir)
            if score_error:
                entry = {
                    "tick": tick,
                    "action": "error",
                    "note": score_error,
                    "parent": base,
                    "diff_hash": diff_hash,
                    "agent_model": task.model("driver"),
                    "run_dir": run_relative(task, run_dir),
                }
                finish_run(task, state, entry, worktree)
                report_event(task, "tick {} error: {}".format(tick, score_error))
                return 0
            assert score is not None
            accept, delta, margin = decide_accept(task, state, score)
            verify: Optional[Dict[str, Any]] = None
            if accept and task.command("verify"):
                active["phase"] = "verifying"
                set_active(task, state, active)
                info("tick {}: adversarial verification".format(tick))
                verify, verify_error = verify_candidate(task, worktree, run_dir)
                if verify_error:
                    entry = {
                        "tick": tick,
                        "action": "error",
                        "correct": score.get("correct"),
                        "objective": score.get("objective"),
                        "delta": delta,
                        "note": verify_error,
                        "parent": base,
                        "diff_hash": diff_hash,
                        "agent_model": task.model("driver"),
                        "metrics": score.get("metrics", {}),
                        "run_dir": run_relative(task, run_dir),
                    }
                    finish_run(task, state, entry, worktree)
                    report_event(task, "tick {} error: {}".format(tick, verify_error))
                    return 0
                if verify is not None and not verify["pass"]:
                    accept = False

            note = str(score.get("note") or "")
            if verify is not None and not verify.get("pass", True):
                verdict_note = str(verify.get("note") or "adversarial verification failed")
                note = "{}{}verification: {}".format(note, "; " if note else "", verdict_note)
            entry = {
                "tick": tick,
                "action": "accept" if accept else "reject",
                "correct": score.get("correct"),
                "objective": score.get("objective") if score.get("correct") else None,
                "delta": delta,
                "note": note,
                "parent": base,
                "diff_hash": diff_hash,
                "agent_model": task.model("driver"),
                "metrics": score.get("metrics", {}),
                "verify": verify,
                "run_dir": run_relative(task, run_dir),
            }
            if accept:
                active["phase"] = "preparing_finalization"
                set_active(task, state, active)
                try:
                    commit = apply_candidate(task, state, run_dir, run_dir / "diff.patch", base, score, verify, entry)
                except AvoError as exc:
                    entry["action"] = "error"
                    entry["note"] = "finalization failed: {}".format(exc)
                    entry["delta"] = 0
                    finish_run(task, state, entry, worktree)
                    report_event(task, "tick {} error: {}".format(tick, entry["note"]))
                    return 0
                entry["commit"] = commit
                state = finish_run(task, state, entry, worktree)
                objective = score.get("objective")
                threshold = task.setting("report", "notify_min_objective", None)
                if threshold is None or (is_number(objective) and is_number(threshold) and float(objective) >= float(threshold)):
                    report_event(task, "new best: objective={} commit={} {}".format(objective, commit[:12], note))
                info("tick {}: ACCEPT objective={} delta={} margin={} commit={}".format(tick, objective, delta, margin, commit[:12]))
            else:
                state = finish_run(task, state, entry, worktree)
                info("tick {}: reject correct={} objective={} stall={}".format(tick, score.get("correct"), score.get("objective"), state.get("stall")))
            maybe_supervise(task, state)
            return 0
        finally:
            # Normal paths already removed it. This is harmless and preserves cleanup on exceptions.
            remove_worktree(task, worktree)


def cmd_run(args: argparse.Namespace) -> int:
    task = Task.from_cwd()
    maximum = args.max_ticks
    if maximum is None:
        maximum = 20
        info("bounded run: --max-ticks 20 (use 0 for unbounded)")
    completed = 0
    while maximum == 0 or completed < maximum:
        task.load()
        state = task.read_state()
        if state.get("status") == "stalled":
            info("stopping: task is stalled")
            break
        cmd_tick(argparse.Namespace(force=False))
        completed += 1
        state = task.read_state()
        if state.get("status") == "stalled":
            info("stopping: task is stalled")
            break
        if args.sleep > 0 and (maximum == 0 or completed < maximum):
            time.sleep(args.sleep)
    return 0


def cmd_supervise(args: argparse.Namespace) -> int:
    task = Task.from_cwd()
    with TaskLock(task):
        state = recover_interrupted(task, task.read_state())
        require_branch(task, state)
        require_clean(task)
        ok = supervisor_internal(task, state, args.reason or "manual review", automatic=False)
        if not ok:
            raise AvoError("supervisor did not produce a redirect")
    return 0


def cmd_reflect(args: argparse.Namespace) -> int:
    task = Task.from_cwd()
    with TaskLock(task):
        state = recover_interrupted(task, task.read_state())
        require_branch(task, state)
        require_clean(task)
        command = task.command("supervisor")
        if not command:
            raise AvoError("no supervisor command configured")
        name = "reflect-{:06d}-{}".format(int(state.get("tick", 0)), int(time.time()))
        run_dir = make_run_dir(task, name)
        worktree = run_dir / "worktree"
        base = head_commit(task.root)
        if not base:
            raise AvoError("repository has no HEAD")
        create_worktree(task, worktree, base)
        try:
            prompt = build_reflect_prompt(task, state, run_dir)
            result = run_hook(
                command,
                [worktree, prompt],
                worktree,
                run_dir / "reflect.stdout",
                run_dir / "reflect.stderr",
                {"AVO_SUPERVISOR_MODEL": task.model("supervisor")},
            )
            if result.returncode != 0:
                raise AvoError("reflect command failed with exit {}".format(result.returncode))
            memory = (run_dir / "reflect.stdout").read_text(encoding="utf-8").strip()
            if not memory:
                raise AvoError("reflect command returned empty memory")
            atomic_write_text(task.memory_path, memory + "\n")
            state["last_reflect_tick"] = int(state.get("tick", 0))
            state["last_action"] = "reflect"
            task.write_state(state)
            append_ledger(task, {"tick": state.get("tick", 0), "action": "reflect", "note": "memory refreshed", "agent_model": task.model("supervisor"), "run_dir": run_relative(task, run_dir)})
            info("memory refreshed: {}".format(task.memory_path))
        finally:
            remove_worktree(task, worktree)
    return 0


def read_pins(task: Task) -> List[str]:
    pins = []
    for line in read_optional(task.pins_path).splitlines():
        if line.startswith("- ") and line[2:].strip():
            pins.append(line[2:].strip())
    return pins


def write_pins(task: Task, pins: List[str]) -> None:
    body = "# Human pins\n\n" + "".join("- {}\n".format(pin) for pin in pins)
    atomic_write_text(task.pins_path, body)


def cmd_pin(args: argparse.Namespace) -> int:
    task = Task.from_cwd()
    text = " ".join(args.text).strip()
    if not text:
        raise AvoError("pin text cannot be empty")
    with TaskLock(task):
        state = recover_interrupted(task, task.read_state())
        pins = read_pins(task)
        pins.append(text)
        write_pins(task, pins)
        state["last_action"] = "pin"
        task.write_state(state)
    info("added pin {}".format(len(pins)))
    return 0


def cmd_pins(args: argparse.Namespace) -> int:
    task = Task.from_cwd()
    pins = read_pins(task)
    if not pins:
        print("(no human pins)")
    for index, pin in enumerate(pins, 1):
        print("{}: {}".format(index, pin))
    return 0


def cmd_unpin(args: argparse.Namespace) -> int:
    task = Task.from_cwd()
    with TaskLock(task):
        state = recover_interrupted(task, task.read_state())
        pins = read_pins(task)
        if args.index < 1 or args.index > len(pins):
            raise AvoError("pin index out of range")
        removed = pins.pop(args.index - 1)
        write_pins(task, pins)
        state["last_action"] = "unpin"
        task.write_state(state)
    info("removed pin: {}".format(removed))
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    task = Task.from_cwd()
    with TaskLock(task):
        state = recover_interrupted(task, task.read_state())
        state["status"] = "running"
        state["stall"] = 0
        state["redirects"] = 0
        state["last_action"] = "resume"
        task.write_state(state)
        append_ledger(task, {"tick": state.get("tick", 0), "action": "resume", "note": "human resumed search"})
    info("task resumed")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    task = Task.from_cwd()
    state = task.read_state()
    reason = detect_stall(task)
    print("task branch   : {} (expected {})".format(current_branch(task.root) or "DETACHED", state.get("task_branch")))
    print("state dir     : {}".format(task.avo_dir))
    print("mode/status   : {} / {}".format(task.mode, state.get("status", "running")))
    print("ticks         : {}".format(state.get("tick", 0)))
    print("best objective: {}".format(state.get("best_objective")))
    print("best commit   : {}".format(state.get("best_commit")))
    print("redirects     : {}".format(state.get("redirects", 0)))
    if not task.command("score"):
        print("preview       : yes (no scorer; diffs are saved but never accepted)")
    if state.get("active_run"):
        print("active run    : {}".format(json_dumps(state["active_run"])))
    if reason:
        print("STALL         : {}".format(reason))
    print("--- recent ledger ---")
    for entry in read_ledger(task)[-8:]:
        print(format_ledger_entry(entry))
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    task = Task.from_cwd()
    if task.report_path.exists():
        print(task.report_path.read_text(encoding="utf-8"), end="")
    else:
        print("(nothing noteworthy)")
    return 0


def cmd_stall_detect(args: argparse.Namespace) -> int:
    if args.ledger:
        entries = read_ledger_path(Path(args.ledger))
        reason = detect_stall_entries(
            entries,
            args.stall_window,
            args.cycle_window,
            args.reject_ratio,
            args.repeat_max,
        )
    else:
        task = Task.from_cwd()
        reason = detect_stall(task)
    if reason:
        print(reason)
        return 0
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="avo", description="Lightweight autonomous variation/optimization loop")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="initialize an AVO task")
    init.add_argument("task")
    init.add_argument("--goal", required=True)
    init.add_argument("--score", default="")
    init.add_argument("--agent", default="")
    init.add_argument("--verify", default="")
    init.add_argument("--supervisor", default="")
    init.add_argument("--k", default="")
    init.add_argument("--mode", choices=("rank", "discover"), default="rank")
    init.set_defaults(func=cmd_init)

    tick = sub.add_parser("tick", help="run one isolated candidate attempt")
    tick.add_argument("--force", action="store_true", help="run even when task status is stalled")
    tick.set_defaults(func=cmd_tick)

    run = sub.add_parser("run", help="run repeated ticks")
    run.add_argument("--max-ticks", type=int, default=None, help="0 means unbounded; default 20")
    run.add_argument("--until-stagnant", action="store_true")
    run.add_argument("--sleep", type=float, default=0)
    run.set_defaults(func=cmd_run)

    supervise = sub.add_parser("supervise", help="ask the supervisor for fresh directions")
    supervise.add_argument("reason", nargs="?", default="manual review")
    supervise.set_defaults(func=cmd_supervise)

    reflect = sub.add_parser("reflect", help="refresh curated memory using the supervisor")
    reflect.set_defaults(func=cmd_reflect)

    pin = sub.add_parser("pin", help="add an authoritative human pin")
    pin.add_argument("text", nargs="+")
    pin.set_defaults(func=cmd_pin)

    pins = sub.add_parser("pins", help="list human pins")
    pins.set_defaults(func=cmd_pins)

    unpin = sub.add_parser("unpin", help="remove a human pin by number")
    unpin.add_argument("index", type=int)
    unpin.set_defaults(func=cmd_unpin)

    resume = sub.add_parser("resume", help="resume a task marked stalled")
    resume.set_defaults(func=cmd_resume)

    status = sub.add_parser("status", help="show task state and recent attempts")
    status.set_defaults(func=cmd_status)

    report = sub.add_parser("report", help="show redacted noteworthy events")
    report.set_defaults(func=cmd_report)

    stall = sub.add_parser("stall-detect", help="standalone deterministic stall check")
    stall.add_argument("--ledger")
    stall.add_argument("--stall-window", type=int, default=8)
    stall.add_argument("--cycle-window", type=int, default=10)
    stall.add_argument("--reject-ratio", type=float, default=0.8)
    stall.add_argument("--repeat-max", type=int, default=3)
    stall.set_defaults(func=cmd_stall_detect)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    if sys.version_info < (3, 8):
        print("avo: Python 3.8+ is required", file=sys.stderr)
        return 1
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "max_ticks", 0) is not None and getattr(args, "max_ticks", 0) < 0:
        parser.error("--max-ticks must be >= 0")
    if getattr(args, "sleep", 0) < 0:
        parser.error("--sleep must be >= 0")
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        warn("interrupted")
        return 130
    except AvoError as exc:
        warn(str(exc))
        return 2 if getattr(args, "command", "") == "stall-detect" else 1


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda signum, frame: (_ for _ in ()).throw(KeyboardInterrupt()))
    sys.exit(main())
