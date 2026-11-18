"""Deterministic tests for scheduler, guard, and operational records. Stdlib only."""

from __future__ import annotations

import json
import multiprocessing
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cycle as cycle_mod  # noqa: E402
import cycle_guard as cycle_guard_mod  # noqa: E402
import inbox as inbox_mod  # noqa: E402
import ops_runs as ops_mod  # noqa: E402
import scheduled_cycle as scheduled_mod  # noqa: E402

if sys.platform == "win32":
    import schedule_windows as schedule_mod  # noqa: E402


def _hold_guard(content_root: str, seconds: float) -> None:
    root = Path(content_root)
    guard = cycle_guard_mod.CycleGuard(root)
    guard.acquire()
    time.sleep(seconds)
    guard.release()


class CycleGuardTests(unittest.TestCase):
    def test_acquire_release_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            guard = cycle_guard_mod.CycleGuard(root)
            ok, _ = guard.acquire()
            self.assertTrue(ok)
            self.assertTrue(cycle_guard_mod.is_busy(root))
            guard.release()
            self.assertFalse(cycle_guard_mod.is_busy(root))

    def test_second_acquire_fails_while_held(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = cycle_guard_mod.CycleGuard(root)
            second = cycle_guard_mod.CycleGuard(root)
            self.assertTrue(first.acquire()[0])
            self.assertFalse(second.acquire()[0])
            first.release()
            self.assertTrue(second.acquire()[0])
            second.release()

    def test_cross_process_exclusion(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = multiprocessing.Process(target=_hold_guard, args=(tmp, 2.0))
            proc.start()
            deadline = time.time() + 1.0
            while time.time() < deadline and not cycle_guard_mod.is_busy(Path(tmp)):
                time.sleep(0.05)
            self.assertTrue(cycle_guard_mod.is_busy(Path(tmp)))
            guard = cycle_guard_mod.CycleGuard(Path(tmp))
            self.assertFalse(guard.acquire()[0])
            proc.join(5)
            self.assertTrue(proc.exitcode == 0)


class OpsRunsTests(unittest.TestCase):
    def test_classify_editorial_outcomes(self):
        self.assertEqual(
            ops_mod.classify(outcome="NO_EDITORIAL_OPPORTUNITY", post_id="", exit_code=1, process_result="complete"),
            "NO_EDITORIAL_OPPORTUNITY",
        )
        self.assertEqual(
            ops_mod.classify(outcome="REVISION_EXHAUSTED", post_id="", exit_code=1, process_result="complete"),
            "REVISION_EXHAUSTED",
        )
        self.assertEqual(
            ops_mod.classify(outcome="READY_FOR_HUMAN", post_id="POST-20260101-01", exit_code=0, process_result="complete"),
            "READY_FOR_HUMAN",
        )

    def test_dedupe_running_and_final(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "content"
            (root / "cycles").mkdir(parents=True)
            ops_mod.append_record(
                root,
                {"run_id": "RUN-1", "process_result": "running", "trigger": ops_mod.TRIGGER_SCHEDULED},
            )
            ops_mod.append_record(
                root,
                {
                    "run_id": "RUN-1",
                    "process_result": "complete",
                    "trigger": ops_mod.TRIGGER_SCHEDULED,
                    "classification": "TEST_OK",
                },
            )
            rows = ops_mod.load_records(root)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["process_result"], "complete")

    def test_same_run_id_consumes_one_daily_slot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "content"
            (root / "cycles").mkdir(parents=True)
            shared = {
                "run_id": "RUN-20261118-085507-26208",
                "trigger": ops_mod.TRIGGER_SCHEDULED,
                "local_started": "2026-11-18T09:55:07+01:00",
                "started_at": "2026-11-18T08:55:07Z",
            }
            ops_mod.append_record(
                root,
                {
                    **shared,
                    "process_result": "INTERRUPTED",
                    "classification": "INTERRUPTED",
                },
            )
            ops_mod.append_record(
                root,
                {
                    **shared,
                    "process_result": "complete",
                    "classification": "RUNTIME_FAILURE",
                    "outcome": "RUNTIME_FAILURE",
                },
            )
            ops_mod.append_record(
                root,
                {
                    "run_id": "RUN-other",
                    "trigger": ops_mod.TRIGGER_SCHEDULED,
                    "local_started": "2026-11-18T12:00:00+01:00",
                    "process_result": "complete",
                    "classification": cycle_guard_mod.SKIPPED_OUTCOME,
                },
            )
            self.assertEqual(ops_mod.count_scheduled_today(root, "2026-11-18"), 1)

    def test_generation_summary_not_generic_for_exhaustion(self):
        job = {
            "status": "error",
            "message": "REVISION_EXHAUSTED",
            "error": "editorial REVISE after revision limit",
            "post_id": "",
        }
        text = ops_mod.generation_job_summary(job)
        self.assertIn("Quality gates exhausted", text)
        self.assertNotEqual(text, "Generation failed.")


class ScheduledRunnerTests(unittest.TestCase):
    def test_build_cycle_command_uses_absolute_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            content = repo / "content"
            content.mkdir()
            (repo / "scripts").mkdir()
            (repo / "scripts" / "cycle.py").write_text("# stub\n", encoding="utf-8")
            cmd = scheduled_mod.build_cycle_command(Path(sys.executable), repo, content)
            self.assertIn("cycle.py", cmd[1])
            self.assertTrue(Path(cmd[1]).is_absolute() or str(repo) in cmd[1])

    def test_test_mode_writes_record_without_cycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "content"
            root.mkdir(parents=True)
            code = scheduled_mod.run_test_mode(root, 0)
            self.assertEqual(code, 0)
            rows = ops_mod.load_records(root)
            self.assertEqual(rows[0]["classification"], "TEST_OK")
            self.assertFalse(any(p.name.startswith("POST-") for p in (root / "drafts").glob("*") if (root / "drafts").exists()))

    def test_skipped_test_mode_when_busy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "content"
            root.mkdir(parents=True)
            guard = cycle_guard_mod.CycleGuard(root)
            guard.acquire()
            try:
                code = scheduled_mod.run_test_mode(root, 0)
                self.assertEqual(code, 1)
                rows = ops_mod.load_records(root)
                self.assertEqual(rows[0]["classification"], cycle_guard_mod.SKIPPED_OUTCOME)
            finally:
                guard.release()


class InboxSchedulerTests(unittest.TestCase):
    def test_dashboard_scheduled_panel(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ops_mod.append_record(
                root,
                {
                    "run_id": "RUN-X",
                    "trigger": ops_mod.TRIGGER_SCHEDULED,
                    "local_started": "2026-11-16T09:00:00+01:00",
                    "classification": "REVISION_EXHAUSTED",
                    "process_result": "complete",
                },
            )
            html = inbox_mod.render_scheduled_runs(root)
            self.assertIn("Quality gates exhausted", html)
            self.assertNotIn("Generation failed", html)

    def test_start_generation_refuses_when_global_lock_busy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            guard = cycle_guard_mod.CycleGuard(root)
            guard.acquire()
            try:
                ok, message = inbox_mod.start_generation(root, "run")
                self.assertFalse(ok)
                self.assertIn("running", message.lower())
            finally:
                guard.release()


class CycleCliGuardTests(unittest.TestCase):
    def test_cmd_run_skipped_when_guard_busy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            guard = cycle_guard_mod.CycleGuard(root)
            guard.acquire()
            try:
                args = cycle_mod.build_parser().parse_args(
                    ["--content-root", str(root), "--repo-root", str(ROOT), "run", "--topic", "guard-busy-probe"]
                )
                code = cycle_mod.cmd_run(args)
                self.assertEqual(code, 1)
            finally:
                guard.release()


@unittest.skipUnless(sys.platform == "win32", "Windows Task Scheduler only")
class ScheduleWindowsTests(unittest.TestCase):
    def test_task_xml_weekday_ignore_new(self):
        config = json.loads((ROOT / "config" / "scheduler.json").read_text(encoding="utf-8"))
        xml = schedule_mod.build_task_xml(
            config,
            python_exe=Path(sys.executable),
            runner_args="\"C:\\\\repo\\\\scheduled_cycle.py\"",
            enabled=True,
        )
        self.assertIn("IgnoreNew", xml)
        self.assertIn("<Monday />", xml)
        self.assertIn("T09:00:00", xml)
        self.assertIn("<WakeToRun>false</WakeToRun>", xml)
        self.assertIn("<StartWhenAvailable>false</StartWhenAvailable>", xml)


if __name__ == "__main__":
    unittest.main()