import json
import os
import subprocess
import tempfile
import textwrap
import time
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
AVO = PROJECT_ROOT / "scripts" / "avo"


class AvoIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        self.cmd("git", "init", "-q")
        self.cmd("git", "config", "user.name", "AVO Test")
        self.cmd("git", "config", "user.email", "avo@example.test")

    def tearDown(self):
        self.temp.cleanup()

    def cmd(self, *argv, check=True, env=None):
        merged = os.environ.copy()
        if env:
            merged.update(env)
        result = subprocess.run(
            list(argv),
            cwd=self.repo,
            env=merged,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if check and result.returncode != 0:
            self.fail(
                "command failed: {}\nstdout:\n{}\nstderr:\n{}".format(
                    " ".join(str(item) for item in argv), result.stdout, result.stderr
                )
            )
        return result

    def write(self, name, content, executable=False):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")
        if executable:
            path.chmod(0o755)
        return path

    def write_python_hook(self, name, body):
        return self.write(
            name,
            "#!/usr/bin/env python3\n" + textwrap.dedent(body).lstrip(),
            executable=True,
        )

    def avo(self, *args, check=True, env=None):
        return self.cmd(str(AVO), *args, check=check, env=env)

    def init_value_task(self, initial, agent_body, score_body, extra_args=()):
        self.write("value.txt", str(initial) + "\n")
        agent = self.write_python_hook("agent.py", agent_body)
        score = self.write_python_hook("score.py", score_body)
        self.avo(
            "init",
            "demo",
            "--goal",
            "maximize value",
            "--agent",
            str(agent),
            "--score",
            str(score),
            *extra_args,
        )
        return agent, score

    def ledger(self):
        return [json.loads(line) for line in (self.repo / ".avo" / "ledger.jsonl").read_text().splitlines()]

    def state(self):
        return json.loads((self.repo / ".avo" / "state.json").read_text())

    def config(self):
        return json.loads((self.repo / ".avo" / "config.json").read_text())

    def write_config(self, value):
        (self.repo / ".avo" / "config.json").write_text(json.dumps(value, indent=2) + "\n")

    def test_init_and_accept_use_ignored_nested_worktree(self):
        self.init_value_task(
            0,
            """
            import pathlib, sys
            pathlib.Path(sys.argv[1], "value.txt").write_text("1\\n")
            """,
            """
            import json, pathlib, sys
            value = int(pathlib.Path(sys.argv[1], "value.txt").read_text())
            print(json.dumps({"correct": True, "objective": value, "metrics": {}, "note": f"value={value}"}))
            """,
        )
        self.assertIn("/.avo/", (self.repo / ".git" / "info" / "exclude").read_text())
        self.assertFalse((self.repo / ".gitignore").exists())

        self.avo("tick")

        self.assertEqual((self.repo / "value.txt").read_text(), "1\n")
        self.assertEqual(self.ledger()[-1]["action"], "accept")
        self.assertEqual(self.state()["best_objective"], 1)
        self.assertEqual(self.cmd("git", "status", "--porcelain").stdout, "")
        self.assertFalse(any((self.repo / ".avo" / "runs").glob("*/worktree")))

    def test_rejected_candidate_never_touches_canonical_tree(self):
        self.init_value_task(
            10,
            """
            import pathlib, sys
            pathlib.Path(sys.argv[1], "value.txt").write_text("5\\n")
            """,
            """
            import json, pathlib, sys
            value = int(pathlib.Path(sys.argv[1], "value.txt").read_text())
            print(json.dumps({"correct": True, "objective": value, "metrics": {}, "note": f"value={value}"}))
            """,
        )
        before = self.cmd("git", "rev-parse", "HEAD").stdout.strip()
        self.avo("tick")
        self.assertEqual((self.repo / "value.txt").read_text(), "10\n")
        self.assertEqual(self.cmd("git", "rev-parse", "HEAD").stdout.strip(), before)
        self.assertEqual(self.ledger()[-1]["action"], "reject")

    def test_incorrect_score_may_have_null_objective(self):
        self.init_value_task(
            0,
            """
            import pathlib, sys
            pathlib.Path(sys.argv[1], "invalid").write_text("yes\\n")
            """,
            """
            import json, pathlib, sys
            root = pathlib.Path(sys.argv[1])
            if (root / "invalid").exists():
                print(json.dumps({"correct": False, "objective": None, "metrics": {}, "note": "invalid"}))
            else:
                print(json.dumps({"correct": True, "objective": 0, "metrics": {}, "note": "baseline"}))
            """,
        )
        self.avo("tick")
        entry = self.ledger()[-1]
        self.assertEqual(entry["action"], "reject")
        self.assertFalse(entry["correct"])
        self.assertIsNone(entry["objective"])
        self.assertFalse((self.repo / "invalid").exists())

    def test_incorrect_baseline_does_not_become_best(self):
        self.write("value.txt", "invalid\n")
        agent = self.write_python_hook(
            "agent.py",
            """
            import pathlib, sys
            pathlib.Path(sys.argv[1], "value.txt").write_text("1\\n")
            """,
        )
        score = self.write_python_hook(
            "score.py",
            """
            import json, pathlib, sys
            raw = pathlib.Path(sys.argv[1], "value.txt").read_text().strip()
            if raw == "invalid":
                print(json.dumps({"correct": False, "objective": None, "metrics": {}, "note": "bad baseline"}))
            else:
                print(json.dumps({"correct": True, "objective": int(raw), "metrics": {}, "note": "fixed"}))
            """,
        )
        self.avo("init", "demo", "--goal", "maximize", "--agent", str(agent), "--score", str(score))
        self.assertIsNone(self.state()["best_commit"])
        self.assertIsNone(self.state()["best_objective"])
        self.avo("tick")
        self.assertEqual(self.state()["best_objective"], 1)
        self.assertEqual(self.ledger()[-1]["action"], "accept")

    def test_adversarial_verifier_vetoes_only_would_be_winner(self):
        verifier = self.write_python_hook(
            "verify.py",
            """
            import json
            print(json.dumps({"pass": False, "note": "cold-cache control destroys the gain", "evidence": []}))
            """,
        )
        self.init_value_task(
            0,
            """
            import pathlib, sys
            pathlib.Path(sys.argv[1], "value.txt").write_text("1\\n")
            """,
            """
            import json, pathlib, sys
            value = int(pathlib.Path(sys.argv[1], "value.txt").read_text())
            print(json.dumps({"correct": True, "objective": value, "metrics": {}, "note": "apparent gain"}))
            """,
            ("--verify", str(verifier)),
        )
        self.avo("tick")
        entry = self.ledger()[-1]
        self.assertEqual(entry["action"], "reject")
        self.assertFalse(entry["verify"]["pass"])
        self.assertIn("cold-cache", entry["note"])
        self.assertEqual((self.repo / "value.txt").read_text(), "0\n")

    def test_human_pin_is_in_driver_prompt(self):
        agent = self.write_python_hook("agent.py", "import sys\n")
        self.avo("init", "demo", "--goal", "explore", "--agent", str(agent))
        self.avo("pin", "Do not change the public API")
        self.avo("tick")
        prompt = (self.repo / ".avo" / "runs" / "000001" / "prompt.md").read_text()
        self.assertIn("Do not change the public API", prompt)
        self.assertEqual(self.ledger()[-1]["action"], "preview")

    def test_stall_redirect_sees_rejects_and_starts_fresh_segment(self):
        agent = self.write_python_hook("agent.py", "import sys\n")
        score = self.write_python_hook(
            "score.py",
            """
            import json
            print(json.dumps({"correct": True, "objective": 0, "metrics": {}, "note": "unused"}))
            """,
        )
        supervisor = self.write_python_hook(
            "supervisor.py",
            """
            import json, pathlib, sys
            prompt = pathlib.Path(sys.argv[2]).read_text()
            if "agent produced no diff" not in prompt:
                raise SystemExit(7)
            print(json.dumps({
                "directions": ["try a different representation"],
                "memory": "# Current understanding\\n\\n- Repeated no-op attempts have failed."
            }))
            """,
        )
        self.write("seed.txt", "seed\n")
        self.avo(
            "init", "demo", "--goal", "make progress", "--agent", str(agent), "--score", str(score),
            "--supervisor", str(supervisor)
        )
        config = self.config()
        config["search"]["stall_window"] = 2
        config["search"]["max_redirects"] = 1
        self.write_config(config)

        self.avo("tick")
        self.avo("tick")
        self.assertTrue((self.repo / ".avo" / "redirect.json").exists())
        self.assertIn("Repeated no-op", (self.repo / ".avo" / "memory.md").read_text())
        self.assertEqual(self.state()["redirects"], 1)

        self.avo("tick")
        prompt = (self.repo / ".avo" / "runs" / "000003" / "prompt.md").read_text()
        self.assertIn("try a different representation", prompt)
        self.assertEqual(self.state()["status"], "running")

        self.avo("tick")
        self.assertEqual(self.state()["status"], "stalled")
        blocked = self.avo("tick", check=False)
        self.assertNotEqual(blocked.returncode, 0)
        self.avo("resume")
        self.assertEqual(self.state()["status"], "running")

    def test_stale_active_run_is_recovered(self):
        agent = self.write_python_hook("agent.py", "import sys\n")
        score = self.write_python_hook(
            "score.py",
            """
            import json
            print(json.dumps({"correct": True, "objective": 0, "metrics": {}, "note": "baseline"}))
            """,
        )
        self.write("seed.txt", "seed\n")
        self.avo("init", "demo", "--goal", "progress", "--agent", str(agent), "--score", str(score))
        stale = self.repo / ".avo" / "runs" / "000001" / "worktree"
        stale.parent.mkdir(parents=True)
        base = self.cmd("git", "rev-parse", "HEAD").stdout.strip()
        self.cmd("git", "worktree", "add", "--detach", str(stale), base)
        state = self.state()
        state["tick"] = 1
        state["active_run"] = {
            "tick": 1,
            "phase": "agent",
            "base_commit": base,
            "worktree": str(stale),
            "run_dir": "runs/000001",
        }
        (self.repo / ".avo" / "state.json").write_text(json.dumps(state, indent=2) + "\n")
        self.avo("tick")
        entries = self.ledger()
        self.assertTrue(any(item["tick"] == 1 and item["action"] == "error" for item in entries))
        self.assertEqual(entries[-1]["tick"], 2)
        self.assertFalse(stale.exists())
        self.assertIsNone(self.state()["active_run"])

    def test_init_requires_agent(self):
        result = self.avo("init", "demo", "--goal", "maximize", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--agent", result.stderr)
        self.assertFalse((self.repo / ".avo" / "config.json").exists())

    def test_init_rejects_invalid_baseline_score(self):
        agent = self.write_python_hook("agent.py", "import sys\n")
        score = self.write_python_hook(
            "score.py",
            """
            print("not-json")
            """,
        )
        result = self.avo(
            "init", "demo", "--goal", "maximize", "--agent", str(agent), "--score", str(score), check=False
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("baseline", result.stderr.lower())
        self.assertFalse((self.repo / ".avo" / "config.json").exists())

    def test_hung_scorer_is_timeout_error(self):
        self.init_value_task(
            0,
            """
            import pathlib, sys
            pathlib.Path(sys.argv[1], "value.txt").write_text("1\\n")
            """,
            """
            import json, pathlib, sys
            value = int(pathlib.Path(sys.argv[1], "value.txt").read_text())
            print(json.dumps({"correct": True, "objective": value, "metrics": {}, "note": "ok"}))
            """,
        )
        hung = self.write_python_hook(
            "hung.py",
            """
            import time
            time.sleep(30)
            """,
        )
        config = self.config()
        config["cmd"]["score"] = str(hung)
        config["search"]["hook_timeout_sec"] = 1
        self.write_config(config)
        started = time.time()
        self.avo("tick")
        self.assertLess(time.time() - started, 10)
        entry = self.ledger()[-1]
        self.assertEqual(entry["action"], "error")
        self.assertIn("timeout", entry["note"].lower())

    def test_knowledge_is_visible_in_worktree_and_not_committed(self):
        knowledge = self.repo / "k"
        knowledge.mkdir()
        (knowledge / "INDEX.md").write_text("# Knowledge index\n\n- ref.md\n", encoding="utf-8")
        (knowledge / "ref.md").write_text("secret-reference\n", encoding="utf-8")
        self.write("seed.txt", "seed\n")
        agent = self.write_python_hook(
            "agent.py",
            """
            import pathlib, sys
            root = pathlib.Path(sys.argv[1])
            text = (root / ".avo" / "knowledge" / "ref.md").read_text()
            (root / "seen.txt").write_text(text)
            """,
        )
        score = self.write_python_hook(
            "score.py",
            """
            import json, pathlib, sys
            seen = (pathlib.Path(sys.argv[1]) / "seen.txt").exists()
            print(json.dumps({"correct": seen, "objective": 1 if seen else 0, "metrics": {}, "note": "seen" if seen else "missing"}))
            """,
        )
        self.avo(
            "init",
            "demo",
            "--goal",
            "use knowledge",
            "--agent",
            str(agent),
            "--score",
            str(score),
            "--k",
            str(knowledge),
        )
        self.avo("tick")
        self.assertEqual((self.repo / "seen.txt").read_text(), "secret-reference\n")
        self.assertEqual(self.ledger()[-1]["action"], "accept")
        tracked = self.cmd("git", "ls-files").stdout
        self.assertNotIn(".avo/knowledge", tracked)
        prompt = (self.repo / ".avo" / "runs" / "000001" / "prompt.md").read_text()
        self.assertIn(".avo/knowledge/INDEX.md", prompt)

    def test_generic_exec_adapter_runs_avo_exec(self):
        adapter = PROJECT_ROOT / "scripts" / "adapters" / "agent-exec.sh"
        inner = self.write_python_hook(
            "inner.py",
            """
            from pathlib import Path
            Path("value.txt").write_text("1\\n")
            """,
        )
        score = self.write_python_hook(
            "score.py",
            """
            import json, pathlib, sys
            value = int(pathlib.Path(sys.argv[1], "value.txt").read_text())
            print(json.dumps({"correct": True, "objective": value, "metrics": {}, "note": f"value={value}"}))
            """,
        )
        self.write("value.txt", "0\n")
        self.avo(
            "init",
            "demo",
            "--goal",
            "maximize",
            "--agent",
            str(adapter),
            "--score",
            str(score),
            env={"AVO_EXEC": str(inner)},
        )
        self.avo("tick", env={"AVO_EXEC": str(inner)})
        self.assertEqual((self.repo / "value.txt").read_text(), "1\n")
        self.assertEqual(self.ledger()[-1]["action"], "accept")


if __name__ == "__main__":
    unittest.main()
