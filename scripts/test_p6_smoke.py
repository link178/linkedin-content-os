"""Live Cursor CLI smoke test for cycle.py. Skips when CLI unavailable.

Deterministic cycle behavior stays in test_p6.py. This module checks a real
CLI run against the cycle contract and the JSON parser the Inbox already uses.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
import cycle as cycle_mod  # noqa: E402
import inbox  # noqa: E402

AGENT = Path(os.environ.get("LOCALAPPDATA", "")) / "cursor-agent" / "agent.cmd"
SMOKE_TIMEOUT_SEC = 900


def cli_available() -> bool:
    if not AGENT.is_file():
        return False
    completed = subprocess.run(
        [str(AGENT), "status"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    return completed.returncode == 0 and "Logged in" in (completed.stdout + completed.stderr)


def line_scanner_payload(stdout: str) -> dict:
    """Previous smoke parser. It only accepts a complete JSON object on one line."""
    payload: dict = {}
    for line in reversed((stdout or "").splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        break
    return payload


class SmokeOutcomeParserTests(unittest.TestCase):
    def test_indented_ready_object_is_visible_to_the_inbox_parser(self):
        stdout = json.dumps(
            {
                "post_id": "POST-20261116-01",
                "outcome": cycle_mod.OUTCOME_READY,
                "stage": "complete",
            },
            indent=2,
        )
        payload = inbox.extract_last_json_object(stdout) or {}
        self.assertEqual(payload.get("post_id"), "POST-20261116-01")
        self.assertEqual(payload.get("outcome"), cycle_mod.OUTCOME_READY)
        self.assertEqual(line_scanner_payload(stdout), {})

    def test_compact_failure_object_has_no_post(self):
        stdout = json.dumps({"outcome": "DISCOVERY_FAILURE", "error": "research failed"})
        payload = inbox.extract_last_json_object(stdout) or {}
        self.assertEqual(payload.get("outcome"), "DISCOVERY_FAILURE")
        self.assertFalse(payload.get("post_id"))


@unittest.skipUnless(
    os.environ.get("LCOS_ALLOW_LIVE_SMOKE") == "1" and cli_available(),
    "live Cursor CLI smoke is opt-in via LCOS_ALLOW_LIVE_SMOKE=1",
)
class LiveCycleSmoke(unittest.TestCase):
    def test_headless_cycle_in_temp_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "repo"
            shutil.copytree(
                ROOT,
                workspace,
                ignore=shutil.ignore_patterns(
                    ".git",
                    ".env",
                    ".env.*",
                    "content/drafts",
                    "content/approved",
                    "content/cycles",
                    "__pycache__",
                ),
            )
            content = workspace / "content"
            for name in ("drafts", "ideas", "plans", "previews", "cycles", "strategy", "approved", "published"):
                (content / name).mkdir(parents=True, exist_ok=True)
            for stray in (content / "approved").glob("*.md"):
                stray.unlink()
            for stray in (content / "published").glob("*.md"):
                stray.unlink()
            for stray in (content / "drafts").glob("*.md"):
                stray.unlink()
            strategy_src = ROOT / "content" / "strategy" / "STRATEGY-20261007-01.md"
            if strategy_src.is_file():
                shutil.copy2(strategy_src, content / "strategy" / strategy_src.name)
            env = os.environ.copy()
            env["CONTENT_ROOT"] = str(content)
            env["CURSOR_AGENT"] = str(AGENT)
            proc = subprocess.Popen(
                [
                    sys.executable,
                    str(workspace / "scripts" / "cycle.py"),
                    "--content-root",
                    str(content),
                    "--repo-root",
                    str(workspace),
                    "run",
                ],
                cwd=str(workspace),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
            )
            try:
                stdout, stderr = proc.communicate(timeout=SMOKE_TIMEOUT_SEC)
            except subprocess.TimeoutExpired:
                proc.kill()
                self.fail("cycle smoke timed out")
            payload = inbox.extract_last_json_object(stdout or "") or {}
            post_id = str(payload.get("post_id") or "")
            outcome = str(payload.get("outcome") or "")
            detail = f"exit={proc.returncode} outcome={outcome!r}\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}"
            approved = list((content / "approved").glob("POST-*.md"))
            published = list((content / "published").glob("POST-*.md")) if (content / "published").exists() else []
            self.assertFalse(approved, detail)
            self.assertFalse(published, detail)
            if proc.returncode == 0:
                self.assertEqual(outcome, cycle_mod.OUTCOME_READY, detail)
            else:
                self.assertNotEqual(outcome, cycle_mod.OUTCOME_READY, detail)
            if not post_id:
                self.assertNotEqual(proc.returncode, 0, detail)
                return
            self.assertTrue(post_id.startswith("POST-"), detail)
            ready_proc = subprocess.run(
                [
                    sys.executable,
                    str(workspace / "scripts" / "cycle.py"),
                    "--content-root",
                    str(content),
                    "ready",
                    post_id,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=env,
            )
            ready_out = (ready_proc.stdout or "").strip().splitlines()[0] if ready_proc.stdout else ""
            if ready_out == cycle_mod.OUTCOME_READY:
                self.assertEqual(ready_proc.returncode, 0, detail)
                self.assertEqual(proc.returncode, 0, detail)
                self.assertEqual(outcome, cycle_mod.OUTCOME_READY, detail)
            else:
                self.assertIn(ready_out, {"NOT_READY", ""}, detail)
                self.assertEqual(proc.returncode, 1, detail)


if __name__ == "__main__":
    unittest.main()
