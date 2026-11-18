"""P6 headless cycle, critic, and inbox contract tests. No LinkedIn calls."""

from __future__ import annotations

import base64
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from contextlib import redirect_stdout
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import content_engine as engine  # noqa: E402
import critic as critic_mod  # noqa: E402
import cursor_runtime as runtime_mod  # noqa: E402
import cycle as cycle_mod  # noqa: E402
import inbox  # noqa: E402
from test_content_engine import STRONG_P5_BODY, claim_lines, post_text  # noqa: E402


METRIC_FIRST_RAG_BODY = (
    "A nine-question retrieval evaluation of the Legal RAG Engine achieved Hit Rate 1.0 and MRR 0.75.\n\n"
    "The project was built as a technical RAG system. Evaluation used top-k retrieval with k=5.\n\n"
    "My conclusion: RAG quality has to be judged on a defined evaluation set—not described as general accuracy.\n"
)

ALIGNED_RAG_BODY = (
    "A retrieval score alone does not tell you which questions were asked or where the cutoff sat.\n\n"
    "A nine-question retrieval evaluation achieved Hit Rate 1.0. The figure stays on that documented set.\n\n"
    "My conclusion: judge retrieval on the named evaluation set rather than treating the demo as general accuracy.\n"
)

EDITORIAL_ONLY_BODY = (
    "A retrieval score alone does not tell you which questions were asked.\n\n"
    "Naming the evaluation set is the part a hiring lead can reuse.\n\n"
    "My conclusion: judge the demo on that named set.\n"
)

RAG_TOPIC = {
    "topic": "Judge RAG on defined evaluations not vibes",
    "lane": "POSITIONING",
    "audience": "BROAD_TECH",
    "standalone_value": "HIGH",
    "project_context_required": "LIGHT",
    "technical_depth": "ACCESSIBLE",
    "why_reader_cares": "Leaders need engineers who separate demo metrics from production judgment.",
}


class RecordingRuntime(cycle_mod.FakeRuntime):
    def __init__(self, responses: list[dict]) -> None:
        super().__init__(responses)
        self.prompts: list[str] = []

    def ask(self, prompt: str, **kwargs):
        self.prompts.append(prompt)
        return super().ask(prompt, **kwargs)


BROAD_TOPIC_TITLE = "Extra tooling has to earn its place"
DEEP_TOPIC_TITLE = "Rank quality needs two metrics"
BROAD_READER = "A hiring lead can reuse the decision to skip tooling that does not change the workflow."

BROAD_CONCRETE_BODY = (
    "A visible demo alone does not tell you the surrounding workflow got better.\n\n"
    "I compared the heavier setup with the lighter one before keeping the lighter artifacts.\n\n"
    "The lesson I take is that extra tooling has to change the work before it earns a place.\n"
)


def write_strategy(content_root: Path) -> None:
    folder = content_root / "strategy"
    folder.mkdir(parents=True, exist_ok=True)
    text = """---
id: STRATEGY-20261013-01
kind: strategy
status: proposed
created: 2026-10-13
concentration_risk: false
---

## Topics

- topic: Judge RAG on defined evaluations not vibes
  lane: POSITIONING
  audience: BROAD_TECH
  timeliness: EVERGREEN
  standalone_value: HIGH
  personal_evidence_strength: MEDIUM
  project_context_required: LIGHT
  technical_depth: ACCESSIBLE
  visual_potential: NONE
  why_now: Demos still hide evaluation scope.
  why_this_author: Scoped nine-question retrieval evaluation on a technical RAG project.
  why_reader_cares: Leaders need engineers who separate demo metrics from judgment.

- topic: Extra tooling has to earn its place
  lane: POSITIONING
  audience: BROAD_TECH
  timeliness: EVERGREEN
  standalone_value: HIGH
  personal_evidence_strength: MEDIUM
  project_context_required: LIGHT
  technical_depth: ACCESSIBLE
  visual_potential: NONE
  why_now: Teams add tooling before the workflow shows a need.
  why_this_author: A tested workflow kept the lighter setup after extra orchestration increased friction.
  why_reader_cares: A hiring lead can reuse the decision to skip tooling that does not change the workflow.

- topic: Rank quality needs two metrics
  lane: TECHNICAL_DEEP_DIVE
  audience: AI_ENGINEERING
  timeliness: EVERGREEN
  standalone_value: HIGH
  personal_evidence_strength: HIGH
  project_context_required: LIGHT
  technical_depth: INTERMEDIATE
  visual_potential: NONE
  why_now: Teams still report one retrieval score as quality.
  why_this_author: Scoped nine-question evaluation at k=5 with Hit Rate 1.0 and MRR 0.75.
  why_reader_cares: A retrieval stack can place a relevant document in the window and still rank it lower than expected.

## Mix notes

Positioning, plus one deep dive. The metric topic is labeled for the audience that can use it.
"""
    (folder / "STRATEGY-20261013-01.md").write_text(text, encoding="utf-8")


RAG_POSITIONING_TOPIC = "Judge RAG on defined evaluations not vibes"
HIT_RATE_TOPIC = "Hit Rate can hide rank quality in RAG"
LEADFINDER_TOPIC = "LeadFinder automation and audit discipline"
BACKEND_HABITS_TOPIC = "Backend platform habits in Applied AI work"
DESK_CHECKLIST_TOPIC = "Desk checklists stay local"
METRIC_RECENT_TITLE = "Scoped nine-question retrieval evaluation hit rate mrr"
BACKEND_RECENT_TITLE = "Backend platform habits from earlier roles"


def write_selection_strategy(content_root: Path) -> None:
    """Isolated strategy. Topic text is fixed so selection does not read content/."""
    folder = content_root / "strategy"
    folder.mkdir(parents=True, exist_ok=True)
    text = f"""---
id: STRATEGY-20261013-01
kind: strategy
status: proposed
created: 2026-10-13
concentration_risk: false
---

## Topics

- topic: {RAG_POSITIONING_TOPIC}
  lane: POSITIONING
  audience: BROAD_TECH
  timeliness: EVERGREEN
  standalone_value: HIGH
  personal_evidence_strength: MEDIUM
  project_context_required: LIGHT
  technical_depth: ACCESSIBLE
  visual_potential: NONE
  why_now: Retrieval demos still get described as accurate without naming the evaluation set or cutoff.
  why_this_author: Derived conclusion from a scoped nine-question retrieval evaluation on a technical RAG project.
  why_reader_cares: Leaders need engineers who separate demo metrics from production judgment.

- topic: {HIT_RATE_TOPIC}
  lane: TECHNICAL_DEEP_DIVE
  audience: AI_ENGINEERING
  timeliness: EVERGREEN
  standalone_value: HIGH
  personal_evidence_strength: HIGH
  project_context_required: LIGHT
  technical_depth: INTERMEDIATE
  visual_potential: NONE
  why_now: Teams still report one retrieval score as quality.
  why_this_author: Scoped nine-question evaluation at k=5 with Hit Rate 1.0 and MRR 0.75 on a RAG project.
  why_reader_cares: A retrieval stack can place a relevant document in the window and still rank it lower than expected.

- topic: {LEADFINDER_TOPIC}
  lane: PROJECT_SHOWCASE
  audience: BROAD_TECH
  timeliness: EVERGREEN
  standalone_value: MEDIUM
  personal_evidence_strength: HIGH
  project_context_required: LIGHT
  technical_depth: ACCESSIBLE
  visual_potential: NONE
  why_now: Personal automation projects often skip security until late.
  why_this_author: LeadFinder records audit phases credential rotation and an offline scorer bakeoff.
  why_reader_cares: Shows production-style discipline on a personal automation project.

- topic: {BACKEND_HABITS_TOPIC}
  lane: CAREER_ENGINEERING
  audience: BROAD_TECH
  timeliness: EVERGREEN
  standalone_value: HIGH
  personal_evidence_strength: MEDIUM
  project_context_required: NONE
  technical_depth: ACCESSIBLE
  visual_potential: NONE
  why_now: Hiring stories still skip the platform years.
  why_this_author: Tested workflow where extra orchestration increased friction without improving the use case. Backend platform habits stayed visible across later product roles.
  why_reader_cares: Managers can see the bridge without a borrowed specialty.

- topic: {DESK_CHECKLIST_TOPIC}
  lane: POSITIONING
  audience: BROAD_TECH
  timeliness: EVERGREEN
  standalone_value: HIGH
  personal_evidence_strength: MEDIUM
  project_context_required: LIGHT
  technical_depth: ACCESSIBLE
  visual_potential: NONE
  why_now: Teams keep adding trackers before the checklist is used.
  why_this_author: A local checklist replaced a shared tracker for one weekly review.
  why_reader_cares: A hiring lead can reuse the decision to keep the list local.

## Mix notes

Fixture only. Metric positioning stays blocked. Recent history decides the other deferrals.
"""
    (folder / "STRATEGY-20261013-01.md").write_text(text, encoding="utf-8")


def plant_recent_topic(
    content_root: Path,
    post_id: str,
    topic: str,
    lane: str,
    started_at: str,
) -> None:
    cycle_mod.save_cycle(
        content_root,
        cycle_mod.CycleState(
            post_id=post_id,
            topic=topic,
            lane=lane,
            audience="BROAD_TECH",
            started_at=started_at,
        ),
    )


class CursorRuntimeTests(unittest.TestCase):
    def test_extract_json_from_prose(self):
        text = 'Here is the result:\n{"verdict": "PASS", "blocking_issues": []}\nThanks.'
        obj = runtime_mod.extract_json_object(text)
        self.assertEqual(obj["verdict"], "PASS")

    def test_redact_secrets(self):
        out = runtime_mod.redact_secrets("token Bearer abc.def-123 and cursor_abc123")
        self.assertIn("[REDACTED]", out)
        self.assertNotIn("abc.def-123", out)

    def test_timeout_configuration_is_unchanged(self):
        self.assertEqual(runtime_mod.DEFAULT_TIMEOUT_SEC, 180)

    def test_timeout_without_process_attribute_is_a_runtime_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agent = root / "agent.cmd"
            agent.write_text("@echo off\r\n", encoding="utf-8")
            snapshots: list[Path] = []

            def tracking(repo_root, extra_paths=None):
                path = original_snapshot(repo_root, extra_paths)
                snapshots.append(path)
                return path

            def invoke(args, env=None, timeout=None):
                raise subprocess.TimeoutExpired(cmd=args, timeout=timeout)

            original_snapshot = runtime_mod.build_snapshot
            runtime = runtime_mod.CursorRuntime(
                repo_root=root,
                agent_path=agent,
                timeout_sec=12,
                invoke=invoke,
            )
            with patch.dict(os.environ, {"CURSOR_RUNTIME_SKIP_AUTH": "1"}):
                with patch.object(runtime_mod, "build_snapshot", tracking):
                    with self.assertRaises(runtime_mod.CursorRuntimeError) as raised:
                        runtime.ask("Return one JSON object.")
            self.assertEqual(str(raised.exception), "agent timed out after 12s")
            self.assertIsInstance(raised.exception.__cause__, subprocess.TimeoutExpired)
            self.assertFalse(hasattr(raised.exception.__cause__, "process"))
            self.assertEqual(len(snapshots), 1)
            self.assertFalse(snapshots[0].exists())

    def test_real_timeout_kills_descendant_processes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pidfile = root / "pids.txt"
            worker = root / "sleep_tree.py"
            worker.write_text(
                "import os, subprocess, sys, time\n"
                "from pathlib import Path\n"
                "grand = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
                "Path(os.environ['LI_TIMEOUT_PIDFILE']).write_text(\n"
                "    f'{os.getppid()}\\n{os.getpid()}\\n{grand.pid}\\n',\n"
                "    encoding='utf-8',\n"
                ")\n"
                "time.sleep(120)\n",
                encoding="utf-8",
            )
            agent = root / "agent.cmd"
            agent.write_text(
                f'@echo off\r\n"{sys.executable}" "{worker}"\r\n',
                encoding="utf-8",
            )
            snapshots: list[Path] = []
            original_snapshot = runtime_mod.build_snapshot

            def tracking(repo_root, extra_paths=None):
                path = original_snapshot(repo_root, extra_paths)
                snapshots.append(path)
                return path

            runtime = runtime_mod.CursorRuntime(repo_root=root, agent_path=agent, timeout_sec=3)
            env = {"CURSOR_RUNTIME_SKIP_AUTH": "1", "LI_TIMEOUT_PIDFILE": str(pidfile)}
            with patch.dict(os.environ, env):
                with patch.object(runtime_mod, "build_snapshot", tracking):
                    with self.assertRaises(runtime_mod.CursorRuntimeError) as raised:
                        runtime.ask("Return one JSON object.")
            self.assertEqual(str(raised.exception), "agent timed out after 3s")
            self.assertIsInstance(raised.exception.__cause__, subprocess.TimeoutExpired)
            self.assertFalse(hasattr(raised.exception.__cause__, "process"))
            self.assertTrue(pidfile.is_file(), "child did not record pids before the timeout")
            pids = [int(line) for line in pidfile.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertEqual(len(pids), 3)
            for pid in pids:
                self.assertFalse(cycle_mod.pid_alive(pid), f"pid {pid} still alive")
            self.assertEqual(len(snapshots), 1)
            self.assertFalse(snapshots[0].exists())

    def test_utf8_agent_json_with_non_ascii_parses(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            emitter = root / "emit.py"
            emitter.write_text(
                "import json\n"
                "print(json.dumps({'result': json.dumps({'verdict': 'PASS', 'note': \"café\"})}))\n",
                encoding="utf-8",
            )
            agent = root / "agent.cmd"
            agent.write_text(f'@echo off\r\n"{sys.executable}" "{emitter}"\r\n', encoding="utf-8")
            runtime = runtime_mod.CursorRuntime(repo_root=root, agent_path=agent, timeout_sec=30)
            with patch.dict(os.environ, {"CURSOR_RUNTIME_SKIP_AUTH": "1"}):
                result = runtime.ask("Return one JSON object.")
            self.assertEqual(result.parsed["note"], "café")

    def test_malformed_utf8_bytes_raise_cursor_runtime_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            emitter = root / "emit_bad.py"
            emitter.write_text(
                "import sys\n"
                "sys.stdout.buffer.write(b'{\"result\": \"\\x9d\"}')\n",
                encoding="utf-8",
            )
            agent = root / "agent.cmd"
            agent.write_text(f'@echo off\r\n"{sys.executable}" "{emitter}"\r\n', encoding="utf-8")
            runtime = runtime_mod.CursorRuntime(repo_root=root, agent_path=agent, timeout_sec=30)
            with patch.dict(os.environ, {"CURSOR_RUNTIME_SKIP_AUTH": "1"}):
                with self.assertRaises(runtime_mod.CursorRuntimeError) as raised:
                    runtime.ask("Return one JSON object.")
            self.assertIn("not valid UTF-8", str(raised.exception))

    def test_subprocess_run_subprocess_decodes_utf8_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / "say.py"
            script.write_text('print("external factual statement: café résumé")\n', encoding="utf-8")
            completed = cycle_mod.run_subprocess([str(script)], cwd=root)
            self.assertIn("café", completed.stdout or "")

    def test_run_subprocess_invalid_utf8_raises_cycle_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / "bad.py"
            script.write_text(
                "import sys\n"
                "sys.stdout.buffer.write(b'\\x9d\\n')\n",
                encoding="utf-8",
            )
            with self.assertRaises(cycle_mod.CycleError) as raised:
                cycle_mod.run_subprocess([str(script)], cwd=root)
            self.assertEqual(raised.exception.code, "RUNTIME_FAILURE")
            self.assertIn("not valid UTF-8", str(raised.exception))

    def test_successful_agent_output_still_parses(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            emitter = root / "emit.py"
            emitter.write_text(
                "import json\n"
                "print(json.dumps({'result': json.dumps({'verdict': 'PASS'})}))\n",
                encoding="utf-8",
            )
            agent = root / "agent.cmd"
            agent.write_text(
                f'@echo off\r\n"{sys.executable}" "{emitter}"\r\n',
                encoding="utf-8",
            )
            snapshots: list[Path] = []
            original_snapshot = runtime_mod.build_snapshot

            def tracking(repo_root, extra_paths=None):
                path = original_snapshot(repo_root, extra_paths)
                snapshots.append(path)
                return path

            runtime = runtime_mod.CursorRuntime(repo_root=root, agent_path=agent, timeout_sec=30)
            with patch.dict(os.environ, {"CURSOR_RUNTIME_SKIP_AUTH": "1"}):
                with patch.object(runtime_mod, "build_snapshot", tracking):
                    result = runtime.ask("Return one JSON object.")
            self.assertEqual(result.parsed["verdict"], "PASS")
            self.assertEqual(len(snapshots), 1)
            self.assertFalse(snapshots[0].exists())

    def test_cycle_timeout_is_a_terminal_inbox_error(self):
        previous_content = engine.CONTENT
        previous_active = cycle_mod.ACTIVE_CONTENT_ROOT
        try:
            with tempfile.TemporaryDirectory() as tmp:
                content_root = Path(tmp)
                write_strategy(content_root)
                for name in ("drafts", "ideas", "plans", "previews"):
                    (content_root / name).mkdir()

                class TimeoutAfterCompose:
                    def __init__(self) -> None:
                        self.calls = 0

                    def ask(self, prompt, **kwargs):
                        self.calls += 1
                        if self.calls == 1:
                            payload = {
                                "insight": "Extra tooling has to change the work.",
                                "hook": "receipt",
                                "hooks_considered": "receipt can follow the reader stake.",
                                "body": BROAD_CONCRETE_BODY,
                                "claims_yaml": "- id: AI_DEV_027\n  use: fact\n  scope: \"Applies only to the tested IntelliJ workflow and use case. It is not a claim that the layer is universally worse, and it does not name an external tool.\"",
                                "lane": "POSITIONING",
                                "audience": "BROAD_TECH",
                            }
                            return runtime_mod.AgentResult(
                                text=json.dumps(payload),
                                parsed=payload,
                                raw_envelope={"result": json.dumps(payload)},
                            )
                        raise runtime_mod.CursorRuntimeError("agent timed out after 180s")

                cycle_mod.ACTIVE_CONTENT_ROOT = content_root
                engine.CONTENT = content_root
                runtime = TimeoutAfterCompose()
                with self.assertRaises(cycle_mod.CycleError) as raised:
                    cycle_mod.Orchestrator(content_root, ROOT, runtime, max_revisions=2).run()
                self.assertEqual(raised.exception.code, "RUNTIME_FAILURE")
                self.assertIn("agent timed out after 180s", str(raised.exception))
                self.assertGreaterEqual(runtime.calls, 2)
                saved = list((content_root / "cycles").glob("POST-*.yaml"))
                self.assertEqual(len(saved), 1)
                state = cycle_mod.load_cycle(content_root, saved[0].stem)
                self.assertIsNotNone(state)
                self.assertEqual(state.outcome, "RUNTIME_FAILURE")
                self.assertIn("agent timed out after 180s", state.error)
                self.assertFalse(cycle_mod.lock_path(content_root, state.post_id).exists())

                class Boom:
                    def ask(self, prompt, **kwargs):
                        raise runtime_mod.CursorRuntimeError("agent timed out after 180s")

                fresh = content_root / "inbox-content"
                write_strategy(fresh)
                args = SimpleNamespace(
                    content_root=str(fresh),
                    repo_root=str(ROOT),
                    topic=None,
                    post_id=None,
                    human_note="",
                    max_revisions=2,
                    fake_responses=None,
                )
                buf = io.StringIO()
                with patch.object(cycle_mod.cursor_runtime_mod, "CursorRuntime", return_value=Boom()):
                    with redirect_stdout(buf):
                        code = cycle_mod.cmd_run(args)
                self.assertEqual(code, 1)
                payload = json.loads(buf.getvalue())
                self.assertEqual(payload["outcome"], "RUNTIME_FAILURE")
                self.assertEqual(payload["error"], "agent timed out after 180s")
                log = fresh / "cycles" / "inbox-run.log"
                log.parent.mkdir(parents=True, exist_ok=True)
                log.write_text(buf.getvalue(), encoding="utf-8")
                job = inbox.empty_job()
                job.update({"status": "running", "pid": 0, "command": "run", "log": str(log)})
                inbox.save_job(fresh, job)
                refreshed = inbox.refresh_job(fresh)
                self.assertEqual(refreshed["status"], "error")
                self.assertEqual(refreshed["message"], "RUNTIME_FAILURE")
                self.assertEqual(refreshed["error"], "agent timed out after 180s")
                self.assertFalse(list((fresh / "approved").glob("*.md")) if (fresh / "approved").is_dir() else [])
                self.assertFalse(list((fresh / "published").glob("*.md")) if (fresh / "published").is_dir() else [])
        finally:
            engine.CONTENT = previous_content
            cycle_mod.ACTIVE_CONTENT_ROOT = previous_active


class CriticContractTests(unittest.TestCase):
    def test_pass_requires_empty_blocking(self):
        with self.assertRaises(ValueError):
            critic_mod.findings_from_payload({"verdict": "PASS", "blocking_issues": ["x"]})

    def test_apply_critic_stale_hash(self):
        claim = next(c for c in engine.load_claims() if c["id"] == "LEGAL_RAG_007")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "drafts").mkdir()
            body = STRONG_P5_BODY
            text = post_text(claim, body, status="reviewed", review="PASS")
            text = text.replace("review_verdict: PASS", "review_verdict: PASS")
            path = root / "drafts" / "POST-20260923-01.md"
            path.write_text(text, encoding="utf-8")
            engine.apply_editorial(path, root)
            findings = critic_mod.CriticFindings("PASS", [], [], [], "ok")
            self.assertEqual(engine.apply_critic(path, root, findings), 0)
            stale = path.read_text(encoding="utf-8").replace(body, body + " Extra.")
            path.write_text(stale, encoding="utf-8")
            errors = engine.check_artifact(path, engine.load_claims(), engine.load_hooks(), root)
            self.assertTrue(any("critic" in e and "stale" in e for e in errors))


class CycleOrchestrationTests(unittest.TestCase):
    def test_lock_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "cycles").mkdir(parents=True)
            ok, _ = cycle_mod.acquire_lock(root, "POST-20261013-01", os.getpid())
            self.assertTrue(ok)
            ok2, msg = cycle_mod.acquire_lock(root, "POST-20261013-01", os.getpid())
            self.assertFalse(ok2)
            self.assertIn("running", msg)
            cycle_mod.release_lock(root, "POST-20261013-01")

    def test_fake_cycle_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            content_root = Path(tmp)
            write_strategy(content_root)
            (content_root / "drafts").mkdir()
            (content_root / "ideas").mkdir()
            (content_root / "plans").mkdir()
            (content_root / "previews").mkdir()
            claims_yaml = "- id: AI_DEV_027\n  use: fact\n  scope: \"Applies only to the tested IntelliJ workflow and use case. It is not a claim that the layer is universally worse, and it does not name an external tool.\""
            fake = [
                {
                    "insight": "Extra tooling has to change the work.",
                    "hook": "receipt",
                    "hooks_considered": "receipt can follow the reader stake.",
                    "body": BROAD_CONCRETE_BODY,
                    "claims_yaml": claims_yaml,
                    "lane": "POSITIONING",
                    "audience": "BROAD_TECH",
                },
                {"verdict": "PASS", "findings": "Grounded."},
                {"verdict": "PASS", "blocking_issues": [], "non_blocking_issues": [], "revision_instructions": [], "reason": "ok"},
            ]
            runtime = cycle_mod.FakeRuntime(fake)
            cycle_mod.ACTIVE_CONTENT_ROOT = content_root
            engine.CONTENT = content_root
            orch = cycle_mod.Orchestrator(content_root, ROOT, runtime, max_revisions=0)
            state = orch.run()
            ready, reasons = cycle_mod.readiness_report(content_root, state.post_id)
            self.assertTrue(ready, reasons)
            self.assertEqual(state.outcome, cycle_mod.OUTCOME_READY)
            self.assertEqual(state.topic, BROAD_TOPIC_TITLE)
            self.assertEqual(state.audience, "BROAD_TECH")

    def test_critic_drop_not_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            content_root = Path(tmp)
            write_strategy(content_root)
            fake = [
                {
                    "insight": "x",
                    "hook": "receipt",
                    "hooks_considered": "x",
                    "body": "Context matters for agents in software engineering.",
                    "claims_yaml": "- none",
                    "lane": "POSITIONING",
                    "audience": "BROAD_TECH",
                },
                {"verdict": "BLOCKED", "findings": "too thin"},
            ]
            runtime = cycle_mod.FakeRuntime(fake)
            orch = cycle_mod.Orchestrator(content_root, ROOT, runtime)
            with self.assertRaises(cycle_mod.CycleError):
                orch.run()

    def test_unknown_model_verdict_cannot_weaken_a_block(self):
        self.assertEqual(cycle_mod.merge_verdict("BLOCKED", "BANANA"), "BLOCKED")
        self.assertEqual(cycle_mod.merge_verdict("NEEDS_EDIT", "PASS"), "NEEDS_EDIT")
        self.assertEqual(cycle_mod.merge_verdict("PASS", "BLOCKED"), "BLOCKED")

    def test_compose_prompt_passes_claims_and_forbids_the_ranking(self):
        topic = {"topic": "Agent scaffolding before proof of need", "lane": "POSITIONING", "audience": "BROAD_TECH"}
        prompt = cycle_mod.compose_prompt(topic)
        self.assertIn("An eligible claim is not automatically a relevant claim.", prompt)
        self.assertIn("AI_DEV_027", prompt)
        self.assertIn("did not improve that workflow", prompt)
        self.assertIn("the lighter option won", prompt)
        self.assertIn("AI_DEV_018 (blocked): not eligible", prompt)
        self.assertNotIn("Confidential benchmark figures", prompt)

    def test_overstated_outcome_stays_blocked_and_names_the_sentence(self):
        sentence = "For the IntelliJ coding-agent workflow I tested, the lighter option won."
        by_id = {claim["id"]: claim for claim in engine.load_claims()}
        cited = [("AI_DEV_027", "fact"), ("AI_DEV_017", "opinion")]
        claims_yaml = claim_lines([(by_id[claim_id], use) for claim_id, use in cited])
        body = (
            sentence
            + "\n\nMy conclusion from that work: more agent scaffolding does not automatically improve developer experience.\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            content_root = Path(tmp)
            write_strategy(content_root)
            cycle_mod.ACTIVE_CONTENT_ROOT = content_root
            engine.CONTENT = content_root
            fake = [
                {
                    "insight": "Extra scaffolding added friction.",
                    "hook": "comparison",
                    "hooks_considered": "comparison states both options.",
                    "body": body,
                    "claims_yaml": claims_yaml,
                    "lane": "POSITIONING",
                    "audience": "BROAD_TECH",
                },
                {"verdict": "PASS", "findings": "Need metrics, logs, and repro steps."},
            ]
            orch = cycle_mod.Orchestrator(content_root, ROOT, cycle_mod.FakeRuntime(fake), max_revisions=0)
            with self.assertRaises(cycle_mod.CycleError) as raised:
                orch.run()
            self.assertIn(sentence, str(raised.exception))
            post_id = "POST-" + engine.today_stamp() + "-01"
            state = cycle_mod.load_cycle(content_root, post_id)
            self.assertIsNotNone(state)
            self.assertEqual(state.outcome, "SEMANTIC_BLOCKED")
            self.assertIn(sentence, state.error)
            draft = (content_root / "drafts" / f"{post_id}.md").read_text(encoding="utf-8")
            fields, rest = engine.parse_frontmatter(draft)
            self.assertEqual(fields["review_verdict"], "BLOCKED")
            self.assertEqual(fields.get("editorial_verdict", ""), "")
            self.assertEqual(fields.get("critic_verdict", ""), "")
            self.assertIn("Deterministic review:", rest)
            self.assertIn(sentence, rest)
            self.assertFalse((content_root / "previews" / f"{post_id}.html").is_file())
            ready, reasons = cycle_mod.readiness_report(content_root, post_id)
            self.assertFalse(ready)
            self.assertTrue(any(sentence in reason and reason.startswith("unsupported claim:") for reason in reasons))
            self.assertTrue(any(reason.startswith("skipped gate:") for reason in reasons))
            self.assertFalse(any("editorial review not current PASS" in reason for reason in reasons))
            approved = list((content_root / "approved").glob("*.md")) if (content_root / "approved").is_dir() else []
            self.assertEqual(approved, [])

    def test_supported_evidence_can_reach_ready_without_approval(self):
        by_id = {claim["id"]: claim for claim in engine.load_claims()}
        cited = [
            ("AI_DEV_027", "fact"),
            ("AI_DEV_017", "opinion"),
            ("AI_DEV_031", "opinion"),
        ]
        claims_yaml = claim_lines([(by_id[claim_id], use) for claim_id, use in cited])
        with tempfile.TemporaryDirectory() as tmp:
            content_root = Path(tmp)
            write_strategy(content_root)
            (content_root / "drafts").mkdir()
            (content_root / "ideas").mkdir()
            (content_root / "plans").mkdir()
            (content_root / "previews").mkdir()
            cycle_mod.ACTIVE_CONTENT_ROOT = content_root
            engine.CONTENT = content_root
            fake = [
                {
                    "insight": "Extra scaffolding has to earn its place.",
                    "hook": "receipt",
                    "hooks_considered": "receipt fits the observed workflow.",
                    "body": GROUNDED_SCAFFOLD_BODY,
                    "claims_yaml": claims_yaml,
                    "lane": "POSITIONING",
                    "audience": "BROAD_TECH",
                },
                {"verdict": "PASS", "findings": "Grounded."},
                {
                    "verdict": "PASS",
                    "blocking_issues": [],
                    "non_blocking_issues": [],
                    "revision_instructions": [],
                    "reason": "ok",
                },
            ]
            orch = cycle_mod.Orchestrator(content_root, ROOT, cycle_mod.FakeRuntime(fake), max_revisions=0)
            state = orch.run()
            ready, reasons = cycle_mod.readiness_report(content_root, state.post_id)
            self.assertTrue(ready, reasons)
            self.assertEqual(state.outcome, cycle_mod.OUTCOME_READY)
            fields, _rest = engine.parse_frontmatter(
                (content_root / "drafts" / f"{state.post_id}.md").read_text(encoding="utf-8")
            )
            self.assertEqual(fields["review_verdict"], "PASS")
            self.assertEqual(fields["human_editorial_decision"], "")
            self.assertEqual(list((content_root / "approved").glob("*.md")) if (content_root / "approved").is_dir() else [], [])
            self.assertEqual(list((content_root / "published").glob("*.md")) if (content_root / "published").is_dir() else [], [])


GROUNDED_SCAFFOLD_BODY = """Extra agent orchestration vs lightweight project context.

Practical experimentation has included coding agents.

The tested coding-agent experimentation used GitHub Copilot Agent in IntelliJ IDEA 2026.2.x with the GitHub Copilot JetBrains plugin and the JetBrains MCP Server, on Windows, against Java 11 and Spring Boot 2.2.x legacy codebases.

Repository comprehension and navigation were explicit benchmark targets, and the tested approaches included semantic and MCP-first navigation, custom repository context and instructions, and additional agent scaffolding.

More scaffolding did not automatically improve usability in this IntelliJ experimentation.

An additional agent orchestration and scaffolding layer was evaluated on the tested IntelliJ workflow, did not improve that workflow, and increased friction for the tested use case.

The experimentation kept lightweight project-local context artifacts for project context, decisions, and inventory instead of adding a heavier agent orchestration layer.

My conclusion from that work: more agent scaffolding does not automatically improve developer experience. Enough persistent project context to preserve decisions was preferable here to a larger orchestration layer that did not improve the tested workflow.
"""


class CycleLogTests(unittest.TestCase):
    def test_quoted_cycle_fields_decode_unicode_escapes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = cycle_mod.CycleState(
                post_id="POST-20261116-01",
                topic="Alpine — musl",
                lane="TREND_REACTION",
                audience="BROAD_TECH",
                outcome="REVISION_EXHAUSTED",
                error="opening: size call—until the stack",
            )
            cycle_mod.save_cycle(root, state)
            raw = (root / "cycles" / "POST-20261116-01.yaml").read_text(encoding="utf-8")
            self.assertIn("\\u2014", raw)
            loaded = cycle_mod.load_cycle(root, state.post_id)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.topic, state.topic)
            self.assertEqual(loaded.error, state.error)
            self.assertEqual(loaded.outcome, "REVISION_EXHAUSTED")


class ApprovalGuardTests(unittest.TestCase):
    def test_approve_refuses_critic_drop_metadata(self):
        claim = next(c for c in engine.load_claims() if c["id"] == "LEGAL_RAG_007")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "drafts").mkdir()
            body = STRONG_P5_BODY
            text = post_text(claim, body, status="reviewed", review="PASS")
            path = root / "drafts" / "POST-20260923-01.md"
            path.write_text(text, encoding="utf-8")
            engine.apply_editorial(path, root)
            drop = critic_mod.CriticFindings("DROP", ["no thesis"], [], [], "drop")
            engine.apply_critic(path, root, drop)
            code = engine.approve_post(path, root)
            self.assertNotEqual(code, 0)


class InboxHttpTests(unittest.TestCase):
    POST = "POST-20261005-01"
    MARKER = "dashboard-must-not-render-this-body"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._content = engine.CONTENT
        self._active = cycle_mod.ACTIVE_CONTENT_ROOT
        self._handler_root = inbox.InboxHandler.content_root
        inbox.InboxHandler.content_root = self.root
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), inbox.InboxHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        self.base = f"http://{host}:{port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        inbox.InboxHandler.content_root = self._handler_root
        engine.CONTENT = self._content
        cycle_mod.ACTIVE_CONTENT_ROOT = self._active
        self.tmp.cleanup()

    def open(self, path: str, data: bytes | None = None):
        request = urllib.request.Request(self.base + path, data=data)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, response.read(), response.headers.get("Content-Type", ""), response.geturl()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(), exc.headers.get("Content-Type", ""), exc.geturl()

    def page(self, path: str) -> str:
        code, body, _content_type, _url = self.open(path)
        self.assertEqual(code, 200, body.decode("utf-8", errors="replace"))
        return body.decode("utf-8")

    def csrf(self, html: str) -> str:
        match = re.search(r'name="csrf" value="([^"]+)"', html)
        self.assertIsNotNone(match)
        return match.group(1)

    def submit(self, path: str, fields: dict):
        return self.open(path, urllib.parse.urlencode(fields).encode())

    def write_not_ready(self) -> None:
        (self.root / "drafts").mkdir(parents=True, exist_ok=True)
        (self.root / "previews").mkdir(parents=True, exist_ok=True)
        (self.root / "assets").mkdir(parents=True, exist_ok=True)
        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
        )
        avatar = self.root / "assets" / "avatar.png"
        avatar.write_bytes(png)
        text = f"""---
id: {self.POST}
kind: post
status: reviewed
created: 2026-10-05
hook: receipt
lane: POSITIONING
audience: BROAD_TECH
body_sha256: abc
review_verdict: PASS
editorial_verdict: REVISE
critic_verdict: REVISE
human_editorial_decision: REVISE
---

## Body

{self.MARKER}

## Editorial review

Verdict: REVISE

Weakest element:
Standalone value is LOW for this fixture.

## Critic review

Verdict: REVISE

blocking_issues:
- The opening assumes project context.

non_blocking_issues:
- none

revision_instructions:
- Lead with the reader stake.

reason:
Critic wants a clearer opening.
"""
        (self.root / "drafts" / f"{self.POST}.md").write_text(text, encoding="utf-8")
        preview = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"/></head><body>
<p>{self.MARKER}</p>
<img src="missing.png"/>
<img src="{avatar.as_uri()}"/>
<img src="file:///C:/Windows/win.ini"/>
</body></html>
"""
        (self.root / "previews" / f"{self.POST}.html").write_text(preview, encoding="utf-8")

    def test_root_is_dashboard_and_generate_is_available(self):
        self.write_not_ready()
        html = self.page("/")
        self.assertIn("<h1>Review Inbox</h1>", html)
        self.assertIn("Generate Candidate", html)
        self.assertIn('action="/generate"', html)
        self.assertIn(f'href="/candidate/{self.POST}"', html)
        self.assertIn("POSITIONING", html)
        self.assertIn("BROAD_TECH", html)
        self.assertIn("NOT_READY", html)
        self.assertNotIn(self.MARKER, html)
        self.assertNotIn('value="approve"', html)
        self.assertNotIn("<iframe", html)

    def test_blocked_candidate_shows_the_claim_and_skipped_gates(self):
        sentence = "For the IntelliJ coding-agent workflow I tested, the lighter option won."
        body = (
            sentence
            + "\n\nMy conclusion from that work: more agent scaffolding does not automatically improve developer experience.\n"
        )
        by_id = {claim["id"]: claim for claim in engine.load_claims()}
        claims = claim_lines(
            [(by_id["AI_DEV_027"], "fact"), (by_id["AI_DEV_017"], "opinion")]
        )
        text = post_text(by_id["AI_DEV_027"], body, status="reviewed", review="BLOCKED", claims=claims)
        note = "Project outcome claims a comparative win but the body gives only narrative conclusion."
        text = text.replace("## Review\n\nBLOCKED\n", f"## Review\n\n{note}\n")
        (self.root / "drafts").mkdir(parents=True, exist_ok=True)
        (self.root / "drafts" / "POST-20260923-01.md").write_text(text, encoding="utf-8")
        detail = self.page("/candidate/POST-20260923-01")
        self.assertIn("Unsupported factual claims", detail)
        self.assertIn(sentence, detail)
        self.assertIn("Skipped gates", detail)
        self.assertIn("skipped gate: editorial review after semantic BLOCKED", detail)
        self.assertIn("skipped gate: critic review after semantic BLOCKED", detail)
        self.assertIn("skipped gate: preview after semantic BLOCKED", detail)
        self.assertIn(note, detail)
        self.assertNotIn("critic review missing", detail)
        self.assertNotIn("editorial review not current PASS", detail)
        self.assertRegex(detail, r'<button[^>]*value="approve"[^>]*disabled')

    def test_candidate_detail_navigation(self):
        self.write_not_ready()
        dashboard = self.page("/")
        self.assertIn(f'href="/candidate/{self.POST}"', dashboard)
        detail = self.page(f"/candidate/{self.POST}")
        self.assertIn(f"<h1>{self.POST}</h1>", detail)
        self.assertLess(detail.index("Revision history"), detail.index(self.MARKER))
        self.assertLess(detail.index(self.MARKER), detail.index("<summary>Diagnostics</summary>"))
        self.assertIn("Semantic", detail)
        self.assertIn("Editorial", detail)
        self.assertIn("Critic", detail)
        self.assertIn("REVISE", detail)
        self.assertIn("NOT_READY", detail)
        self.assertIn("human_editorial_decision is REVISE", detail)
        self.assertIn("The opening assumes project context.", detail)
        self.assertIn("Diagnostics", detail)
        self.assertIn(f'src="/preview/{self.POST}"', detail)
        self.assertIn('href="/"', detail)

    def test_not_ready_candidate_cannot_be_approved(self):
        self.write_not_ready()
        detail = self.page(f"/candidate/{self.POST}")
        self.assertRegex(detail, r'<button[^>]*value="approve"[^>]*disabled')
        code, body, _content_type, _url = self.submit(
            "/action",
            {"csrf": self.csrf(detail), "post_id": self.POST, "action": "approve"},
        )
        self.assertEqual(code, 409)
        self.assertRegex(body.decode("utf-8"), r'<button[^>]*value="approve"[^>]*disabled')
        draft = (self.root / "drafts" / f"{self.POST}.md").read_text(encoding="utf-8")
        self.assertIn("human_editorial_decision: REVISE", draft)
        self.assertNotIn("human_editorial_decision: APPROVE", draft)
        self.assertEqual(list((self.root / "approved").glob("*.md")) if (self.root / "approved").is_dir() else [], [])
        self.assertEqual(list((self.root / "published").glob("*.md")) if (self.root / "published").is_dir() else [], [])

    def test_preview_has_no_broken_asset_paths(self):
        self.write_not_ready()
        stored = (self.root / "previews" / f"{self.POST}.html").read_text(encoding="utf-8")
        code, body, content_type, _url = self.open(f"/preview/{self.POST}")
        self.assertEqual(code, 200)
        self.assertIn("text/html", content_type)
        preview = body.decode("utf-8")
        self.assertIn(self.MARKER, preview)
        self.assertNotIn("missing.png", preview)
        self.assertNotIn("file:", preview.lower())
        self.assertNotIn("win.ini", preview.lower())
        self.assertIn('id="inbox-preview-fit"', preview)
        match = re.search(r'src="(/local-asset/content/[^"]+)"', preview)
        self.assertIsNotNone(match)
        asset_code, asset_body, asset_type, _asset_url = self.open(match.group(1))
        self.assertEqual(asset_code, 200)
        self.assertIn("image/png", asset_type)
        self.assertEqual(asset_body[:8], b"\x89PNG\r\n\x1a\n")
        self.assertIn("missing.png", stored)
        self.assertIn("file:", stored)
        self.assertIsNone(inbox.resolve_local_asset("content", "../secrets.png", self.root))
        missing_code, _missing, _missing_type, _missing_url = self.open("/local-asset/content/missing.png")
        self.assertEqual(missing_code, 404)

    def test_generation_status_is_observable_without_a_live_model(self):
        inbox.save_job(self.root, {**inbox.empty_job(), "status": "error", "error": "CLI exited early"})
        html = self.page("/")
        self.assertIn('id="generation-status"', html)
        self.assertIn('data-status="error"', html)
        self.assertIn("CLI exited early", html)
        log = self.root / "cycles" / "inbox-run.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(
            json.dumps({"post_id": "POST-20261013-09", "outcome": "READY_FOR_HUMAN"}, indent=2),
            encoding="utf-8",
        )
        job = inbox.empty_job()
        job.update({"status": "running", "pid": 0, "command": "run", "log": str(log)})
        inbox.save_job(self.root, job)
        code, body, _content_type, _url = self.open("/generation")
        self.assertEqual(code, 200)
        payload = json.loads(body)
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["post_id"], "POST-20261013-09")
        finished = self.page("/")
        self.assertIn('data-status="success"', finished)
        self.assertIn("POST-20261013-09", finished)
        self.assertIn('href="/candidate/POST-20261013-09"', finished)

    def test_generate_and_revise_use_headless_cycle_commands(self):
        self.write_not_ready()
        with patch("inbox.spawn_cycle", return_value=SimpleNamespace(pid=os.getpid())) as spawned:
            dashboard = self.page("/")
            code, body, _content_type, url = self.submit("/generate", {"csrf": self.csrf(dashboard)})
            self.assertEqual(code, 200, body.decode("utf-8", errors="replace"))
            self.assertIn("notice=started", url)
            self.assertIn('data-status="running"', body.decode("utf-8"))
            self.assertEqual(spawned.call_args.args[1], "run")
            inbox.save_job(self.root, inbox.empty_job())
            detail = self.page(f"/candidate/{self.POST}")
            note = "Lead with why the metric matters to the reader."
            code, body, _content_type, url = self.submit(
                "/action",
                {"csrf": self.csrf(detail), "post_id": self.POST, "action": "revise", "feedback": note},
            )
            self.assertEqual(code, 200, body.decode("utf-8", errors="replace"))
            self.assertIn("notice=revised", url)
            self.assertEqual(spawned.call_args.args[1], "resume")
            self.assertEqual(spawned.call_args.kwargs["post_id"], self.POST)
            self.assertEqual(spawned.call_args.kwargs["human_note"], note)
        draft = (self.root / "drafts" / f"{self.POST}.md").read_text(encoding="utf-8")
        self.assertIn("human_editorial_decision: REVISE", draft)
        self.assertIn(note, draft)
        self.assertIn("## Human revision request", draft)

    def test_drop_records_rejection_without_publication(self):
        self.write_not_ready()
        detail = self.page(f"/candidate/{self.POST}")
        with patch("inbox.spawn_cycle") as spawned:
            code, body, _content_type, url = self.submit(
                "/action",
                {
                    "csrf": self.csrf(detail),
                    "post_id": self.POST,
                    "action": "drop",
                    "feedback": "Out of scope for this week.",
                },
            )
            spawned.assert_not_called()
        self.assertEqual(code, 200, body.decode("utf-8", errors="replace"))
        self.assertIn("notice=dropped", url)
        draft_path = self.root / "drafts" / f"{self.POST}.md"
        fields, _rest = engine.parse_frontmatter(draft_path.read_text(encoding="utf-8"))
        self.assertEqual(fields["human_editorial_decision"], "DROP")
        self.assertEqual(fields["status"], "reviewed")
        self.assertTrue(draft_path.is_file())
        state = cycle_mod.load_cycle(self.root, self.POST)
        self.assertIsNotNone(state)
        self.assertEqual(state.outcome, "DROPPED")
        self.assertIn("Out of scope for this week.", " ".join(state.events))
        self.assertEqual(list((self.root / "approved").glob("*.md")) if (self.root / "approved").is_dir() else [], [])
        self.assertEqual(list((self.root / "published").glob("*.md")) if (self.root / "published").is_dir() else [], [])

    def test_ready_approve_is_editorial_decision_only(self):
        write_strategy(self.root)
        for name in ("drafts", "ideas", "plans", "previews", "cycles"):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        claims_yaml = "- id: AI_DEV_027\n  use: fact\n  scope: \"Applies only to the tested IntelliJ workflow and use case. It is not a claim that the layer is universally worse, and it does not name an external tool.\""
        fake = [
            {
                "insight": "Extra tooling has to change the work.",
                "hook": "receipt",
                "hooks_considered": "receipt can follow the reader stake.",
                "body": BROAD_CONCRETE_BODY,
                "claims_yaml": claims_yaml,
                "lane": "POSITIONING",
                "audience": "BROAD_TECH",
            },
            {"verdict": "PASS", "findings": "Grounded."},
            {
                "verdict": "PASS",
                "blocking_issues": [],
                "non_blocking_issues": [],
                "revision_instructions": [],
                "reason": "ok",
            },
        ]
        cycle_mod.ACTIVE_CONTENT_ROOT = self.root
        engine.CONTENT = self.root
        state = cycle_mod.Orchestrator(self.root, ROOT, cycle_mod.FakeRuntime(fake), max_revisions=0).run()
        ready, reasons = cycle_mod.readiness_report(self.root, state.post_id)
        self.assertTrue(ready, reasons)
        detail = self.page(f"/candidate/{state.post_id}")
        self.assertNotRegex(detail, r'<button[^>]*value="approve"[^>]*disabled')
        code, body, _content_type, url = self.submit(
            "/action",
            {"csrf": self.csrf(detail), "post_id": state.post_id, "action": "approve"},
        )
        self.assertEqual(code, 200, body.decode("utf-8", errors="replace"))
        self.assertIn("notice=approved", url)
        page = body.decode("utf-8")
        self.assertIn("content_engine.py approve", page)
        self.assertIn("human_editorial_decision", page)
        draft = self.root / "drafts" / f"{state.post_id}.md"
        fields, _rest = engine.parse_frontmatter(draft.read_text(encoding="utf-8"))
        self.assertEqual(fields["human_editorial_decision"], "APPROVE")
        self.assertEqual(fields["status"], "reviewed")
        self.assertTrue(draft.is_file())
        approved = list((self.root / "approved").glob("*.md")) if (self.root / "approved").is_dir() else []
        published = list((self.root / "published").glob("*.md")) if (self.root / "published").is_dir() else []
        self.assertEqual(approved, [])
        self.assertEqual(published, [])


class StrategyAlignmentRevisionTests(unittest.TestCase):
    def setUp(self):
        self._content = engine.CONTENT
        self._active = cycle_mod.ACTIVE_CONTENT_ROOT

    def tearDown(self):
        engine.CONTENT = self._content
        cycle_mod.ACTIVE_CONTENT_ROOT = self._active

    def _prepare(self) -> Path:
        content_root = Path(self.tmp.name)
        write_strategy(content_root)
        for name in ("drafts", "ideas", "plans", "previews", "cycles"):
            (content_root / name).mkdir(parents=True, exist_ok=True)
        cycle_mod.ACTIVE_CONTENT_ROOT = content_root
        engine.CONTENT = content_root
        return content_root

    def test_metric_first_opening_is_misaligned_and_reader_first_is_not(self):
        issues = cycle_mod.strategy_alignment_issues(METRIC_FIRST_RAG_BODY, RAG_TOPIC)
        self.assertTrue(any("Hit Rate" in item for item in issues))
        self.assertTrue(any("BROAD_TECH" in item for item in issues))
        self.assertTrue(any("Standalone value mismatch" in item for item in issues))
        self.assertTrue(any(item.startswith("Audience mismatch:") for item in issues))
        reader_first = cycle_mod.strategy_alignment_issues(ALIGNED_RAG_BODY, RAG_TOPIC)
        self.assertTrue(any(item.startswith("Audience mismatch:") for item in reader_first))
        self.assertTrue(cycle_mod.alignment_is_terminal(reader_first))
        deep = dict(RAG_TOPIC)
        deep["lane"] = "TECHNICAL_DEEP_DIVE"
        deep["audience"] = "AI_ENGINEERING"
        deep["technical_depth"] = "INTERMEDIATE"
        self.assertEqual(cycle_mod.strategy_alignment_issues(ALIGNED_RAG_BODY, deep), [])
        prompt = cycle_mod.compose_prompt(RAG_TOPIC)
        self.assertIn(
            "If claims_yaml cites an opinion, each derived sentence needs one of these frames: My conclusion, I treat this as, The lesson I take.",
            prompt,
        )
        self.assertIn("Do not open with a project name, Hit Rate, MRR", prompt)
        self.assertIn("Do not teach Hit Rate, MRR, reciprocal rank, or RRF in this post.", prompt)
        self.assertIn(RAG_TOPIC["why_reader_cares"], prompt)
        self.assertIn("the lighter option won", prompt)
        self.assertNotIn("some MCP tools behaved as router-only tools", prompt)
        deep = dict(RAG_TOPIC)
        deep["lane"] = "TECHNICAL_DEEP_DIVE"
        deep["audience"] = "AI_ENGINEERING"
        deep["why_this_author"] = "Scoped nine-question evaluation at k=5 with Hit Rate 1.0 and MRR 0.75."
        deep_prompt = cycle_mod.compose_prompt(deep)
        self.assertIn("A nine-question retrieval evaluation achieved Hit Rate 1.0.", deep_prompt)
        self.assertIn("Do not generalize to perfect retrieval", deep_prompt)
        self.assertNotIn("some MCP tools behaved as router-only tools", deep_prompt)

    def test_alignment_revision_receives_the_mismatch_and_can_reach_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.tmp = SimpleNamespace(name=tmp)
            content_root = self._prepare()
            qual = next(c["qualification"] for c in engine.load_claims() if c["id"] == "LEGAL_RAG_007")
            claims_yaml = f'- id: LEGAL_RAG_007\n  use: fact\n  scope: "{qual}"'
            fake = [
                {
                    "insight": "Judge RAG on a named evaluation set.",
                    "hook": "receipt",
                    "hooks_considered": "receipt can follow the reader stake.",
                    "body": METRIC_FIRST_RAG_BODY,
                    "claims_yaml": claims_yaml,
                    "lane": "TECHNICAL_DEEP_DIVE",
                    "audience": "AI_ENGINEERING",
                },
                {"body": ALIGNED_RAG_BODY},
                {"verdict": "PASS", "findings": "Grounded."},
                {
                    "verdict": "PASS",
                    "blocking_issues": [],
                    "non_blocking_issues": [],
                    "revision_instructions": [],
                    "reason": "ok",
                },
            ]
            runtime = RecordingRuntime(fake)
            state = cycle_mod.Orchestrator(content_root, ROOT, runtime, max_revisions=2).run(
                topic_name=DEEP_TOPIC_TITLE
            )
            ready, reasons = cycle_mod.readiness_report(content_root, state.post_id)
            self.assertTrue(ready, reasons)
            self.assertEqual(state.revision_count, 1)
            self.assertEqual(state.outcome, cycle_mod.OUTCOME_READY)
            revise = runtime.prompts[1]
            self.assertIn("Do not invent metrics", revise)
            self.assertIn("Standalone value mismatch", revise)
            self.assertIn("AI_ENGINEERING", revise)
            self.assertNotIn("Audience mismatch:", revise)
            self.assertNotIn("editorial REVISE", revise)
            self.assertTrue(runtime.prompts[2].startswith("Semantic review"))
            draft = (content_root / "drafts" / f"{state.post_id}.md").read_text(encoding="utf-8")
            self.assertIn("alone does not tell you", draft)
            self.assertIn("Hit Rate 1.0", draft)
            fields, _rest = engine.parse_frontmatter(draft)
            self.assertEqual(fields["review_verdict"], "PASS")
            self.assertEqual(fields["editorial_verdict"], "PASS")

    def test_alignment_stops_at_revision_cap_with_a_precise_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.tmp = SimpleNamespace(name=tmp)
            content_root = self._prepare()
            qual = next(c["qualification"] for c in engine.load_claims() if c["id"] == "LEGAL_RAG_007")
            claims_yaml = f'- id: LEGAL_RAG_007\n  use: fact\n  scope: "{qual}"'
            compose = {
                "insight": "Judge RAG on a named evaluation set.",
                "hook": "receipt",
                "hooks_considered": "receipt",
                "body": METRIC_FIRST_RAG_BODY,
                "claims_yaml": claims_yaml,
                "lane": "TECHNICAL_DEEP_DIVE",
                "audience": "AI_ENGINEERING",
            }
            fake = [compose, {"body": METRIC_FIRST_RAG_BODY}, {"body": METRIC_FIRST_RAG_BODY}]
            runtime = RecordingRuntime(fake)
            with self.assertRaises(cycle_mod.CycleError) as raised:
                cycle_mod.Orchestrator(content_root, ROOT, runtime, max_revisions=2).run(
                    topic_name=DEEP_TOPIC_TITLE
                )
            self.assertEqual(raised.exception.code, "STRATEGY_MISALIGNED")
            message = str(raised.exception)
            self.assertIn("strategy misalignment after revision limit", message)
            self.assertIn("Standalone value mismatch", message)
            self.assertIn("Hit Rate 1.0", message)
            self.assertEqual(runtime.calls, 3)
            self.assertTrue(all(not prompt.startswith("Semantic review") for prompt in runtime.prompts))
            self.assertIn("Do not invent metrics", runtime.prompts[1])
            self.assertIn("Do not invent metrics", runtime.prompts[2])
            post_id = "POST-" + engine.today_stamp() + "-01"
            state = cycle_mod.load_cycle(content_root, post_id)
            self.assertIsNotNone(state)
            self.assertEqual(state.revision_count, 2)
            self.assertEqual(state.outcome, "STRATEGY_MISALIGNED")
            draft = (content_root / "drafts" / f"{post_id}.md").read_text(encoding="utf-8")
            self.assertIn("Hit Rate 1.0 and MRR 0.75", draft)
            self.assertNotIn("critic_verdict: PASS", draft)
            self.assertFalse((content_root / "previews" / f"{post_id}.html").is_file())

    def test_editorial_revise_prompt_carries_the_finding(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.tmp = SimpleNamespace(name=tmp)
            content_root = self._prepare()
            claims_yaml = "- id: AI_DEV_027\n  use: fact\n  scope: \"Applies only to the tested IntelliJ workflow and use case. It is not a claim that the layer is universally worse, and it does not name an external tool.\""
            fake = [
                {
                    "insight": "Name the evaluation set.",
                    "hook": "receipt",
                    "hooks_considered": "receipt",
                    "body": EDITORIAL_ONLY_BODY,
                    "claims_yaml": claims_yaml,
                    "lane": "POSITIONING",
                    "audience": "BROAD_TECH",
                },
                {"verdict": "PASS", "findings": "Grounded."},
                {"body": BROAD_CONCRETE_BODY},
                {"verdict": "PASS", "findings": "Grounded."},
                {
                    "verdict": "PASS",
                    "blocking_issues": [],
                    "non_blocking_issues": [],
                    "revision_instructions": [],
                    "reason": "ok",
                },
            ]
            runtime = RecordingRuntime(fake)
            state = cycle_mod.Orchestrator(content_root, ROOT, runtime, max_revisions=2).run()
            ready, reasons = cycle_mod.readiness_report(content_root, state.post_id)
            self.assertTrue(ready, reasons)
            revise = next(prompt for prompt in runtime.prompts if prompt.startswith("Revise this LinkedIn post body"))
            self.assertIn("Weakest element: Weakest dimension: concrete.", revise)
            self.assertIn("Suggested structural change:", revise)
            self.assertIn(BROAD_READER, revise)
            self.assertNotIn('"editorial REVISE"', revise)
            self.assertIn("Do not invent metrics", revise)
            self.assertEqual(state.revision_count, 1)


    def test_opinion_revision_names_the_required_conclusion_frame(self):
        unframed = (
            "A visible demo alone does not tell you the surrounding workflow got better.\n\n"
            "I compared the heavier setup with the lighter one before keeping the lighter artifacts.\n\n"
            "I judge extra tooling by whether the work changes.\n"
        )
        framed = unframed.replace(
            "I judge extra tooling by whether the work changes.",
            "My conclusion: extra tooling has to change the work before it earns a place.",
        )
        with tempfile.TemporaryDirectory() as tmp:
            self.tmp = SimpleNamespace(name=tmp)
            content_root = self._prepare()
            claims_yaml = "- id: AI_DEV_017\n  use: opinion"
            misleading = "Rewrite the closing as a universal law and drop the conclusion frame."
            fake = [
                {
                    "insight": "Name the evaluation set.",
                    "hook": "receipt",
                    "hooks_considered": "receipt",
                    "body": unframed,
                    "claims_yaml": claims_yaml,
                    "lane": "POSITIONING",
                    "audience": "BROAD_TECH",
                },
                {"verdict": "PASS", "findings": misleading},
                {"body": framed},
                {"verdict": "PASS", "findings": "Grounded."},
                {
                    "verdict": "PASS",
                    "blocking_issues": [],
                    "non_blocking_issues": [],
                    "revision_instructions": [],
                    "reason": "ok",
                },
            ]
            runtime = RecordingRuntime(fake)
            state = cycle_mod.Orchestrator(content_root, ROOT, runtime, max_revisions=2).run()
            ready, reasons = cycle_mod.readiness_report(content_root, state.post_id)
            self.assertTrue(ready, reasons)
            revise = next(prompt for prompt in runtime.prompts if prompt.startswith("Revise this LinkedIn post body"))
            self.assertIn("derived statement is not framed as a personal conclusion", revise)
            self.assertIn("My conclusion", revise)
            self.assertIn("I treat this as", revise)
            self.assertIn("The lesson I take", revise)
            self.assertIn("not authoritative", revise)
            self.assertIn(misleading, revise)
            self.assertIn("Do not move project metrics into the opening", revise)
            self.assertEqual(state.revision_count, 1)
            draft = (content_root / "drafts" / f"{state.post_id}.md").read_text(encoding="utf-8")
            self.assertIn("My conclusion:", draft)
            fields, _rest = engine.parse_frontmatter(draft)
            self.assertEqual(fields["review_verdict"], "PASS")


class TopicSelectionTests(unittest.TestCase):
    def test_auto_select_skips_metric_positioning_for_a_broad_topic(self):
        with tempfile.TemporaryDirectory() as tmp:
            content_root = Path(tmp)
            write_strategy(content_root)
            chosen = cycle_mod.select_topic(content_root)
            self.assertEqual(chosen["topic"], BROAD_TOPIC_TITLE)
            self.assertEqual(chosen["audience"], "BROAD_TECH")
            deferred = " ".join(chosen["_deferred"])
            self.assertIn("Judge RAG", deferred)
            self.assertIn("project-metric", deferred)

    def test_explicit_metric_positioning_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            content_root = Path(tmp)
            write_strategy(content_root)
            with self.assertRaises(cycle_mod.CycleError) as raised:
                cycle_mod.select_topic(content_root, "Judge RAG on defined evaluations not vibes")
            self.assertEqual(raised.exception.code, "STRATEGY_MISALIGNED")
            self.assertIn("project-metric", str(raised.exception))
            self.assertIn("TECHNICAL_DEEP_DIVE", str(raised.exception))

    def test_deep_dive_metric_topic_stays_eligible(self):
        with tempfile.TemporaryDirectory() as tmp:
            content_root = Path(tmp)
            write_strategy(content_root)
            chosen = cycle_mod.select_topic(content_root, DEEP_TOPIC_TITLE)
            self.assertEqual(chosen["lane"], "TECHNICAL_DEEP_DIVE")
            self.assertEqual(chosen["audience"], "AI_ENGINEERING")
            self.assertEqual(cycle_mod.strategy_mod.topic_audience_conflict(chosen, engine.load_claims()), "")

    def test_near_duplicate_is_deferred_when_another_topic_fits(self):
        with tempfile.TemporaryDirectory() as tmp:
            content_root = Path(tmp)
            write_strategy(content_root)
            (content_root / "cycles").mkdir()
            state = cycle_mod.CycleState(
                post_id="POST-20261013-09",
                topic=DEEP_TOPIC_TITLE,
                lane="TECHNICAL_DEEP_DIVE",
                audience="AI_ENGINEERING",
                started_at="2026-10-13T12:00:00Z",
            )
            cycle_mod.save_cycle(content_root, state)
            text = (content_root / "strategy" / "STRATEGY-20261013-01.md").read_text(encoding="utf-8")
            text = text.replace(
                "- topic: Extra tooling has to earn its place",
                "- topic: Cutoff scores need a second metric\n"
                "  lane: TECHNICAL_DEEP_DIVE\n"
                "  audience: AI_ENGINEERING\n"
                "  timeliness: EVERGREEN\n"
                "  standalone_value: HIGH\n"
                "  personal_evidence_strength: HIGH\n"
                "  project_context_required: LIGHT\n"
                "  technical_depth: INTERMEDIATE\n"
                "  visual_potential: NONE\n"
                "  why_now: One retrieval score still gets reported alone.\n"
                "  why_this_author: Scoped nine-question evaluation at k=5 with Hit Rate 1.0 and MRR 0.75.\n"
                "  why_reader_cares: Rank quality and presence in a window are different questions.\n\n"
                "- topic: Extra tooling has to earn its place",
            )
            (content_root / "strategy" / "STRATEGY-20261013-01.md").write_text(text, encoding="utf-8")
            chosen = cycle_mod.select_topic(content_root)
            self.assertEqual(chosen["topic"], BROAD_TOPIC_TITLE)
            self.assertTrue(any("Cutoff scores" in item and "too similar" in item for item in chosen["_deferred"]))

    def test_recorded_strategy_defers_broad_rag_metrics(self):
        claims = engine.load_claims()
        with tempfile.TemporaryDirectory() as tmp:
            content_root = Path(tmp)
            write_selection_strategy(content_root)
            _path, topics = cycle_mod.latest_strategy(content_root)
            rag = next(topic for topic in topics if topic.get("topic") == RAG_POSITIONING_TOPIC)
            hit = next(topic for topic in topics if topic.get("topic") == HIT_RATE_TOPIC)
            broad = next(topic for topic in topics if topic.get("topic") == LEADFINDER_TOPIC)
            self.assertIn("project-metric", cycle_mod.strategy_mod.topic_audience_conflict(rag, claims))
            self.assertEqual(cycle_mod.strategy_mod.topic_audience_conflict(hit, claims), "")
            self.assertEqual(cycle_mod.strategy_mod.topic_audience_conflict(broad, claims), "")
            self.assertTrue(cycle_mod.strategy_mod.topics_too_similar(hit, rag, claims))
            plant_recent_topic(
                content_root,
                "POST-20261001-01",
                METRIC_RECENT_TITLE,
                "TECHNICAL_DEEP_DIVE",
                "2026-10-01T00:00:00Z",
            )
            plant_recent_topic(
                content_root,
                "POST-20261002-01",
                BACKEND_RECENT_TITLE,
                "CAREER_ENGINEERING",
                "2026-10-02T00:00:00Z",
            )
            chosen = cycle_mod.select_topic(content_root)
            self.assertEqual(chosen["topic"], DESK_CHECKLIST_TOPIC)
            self.assertNotEqual(chosen["topic"], rag["topic"])
            self.assertNotEqual(chosen["topic"], hit["topic"])
            self.assertEqual(cycle_mod.strategy_mod.topic_audience_conflict(chosen, claims), "")
            deferred = chosen["_deferred"]
            rag_line = next(item for item in deferred if item.startswith("Judge RAG"))
            hit_line = next(item for item in deferred if item.startswith("Hit Rate"))
            backend_line = next(item for item in deferred if item.startswith("Backend platform habits"))
            self.assertIn("project-metric", rag_line)
            self.assertIn("too similar", hit_line)
            self.assertIn("too similar", backend_line)

    def test_recent_topic_window_changes_deferral(self):
        with tempfile.TemporaryDirectory() as tmp:
            content_root = Path(tmp)
            write_selection_strategy(content_root)
            plant_recent_topic(
                content_root,
                "POST-20260101-01",
                BACKEND_RECENT_TITLE,
                "CAREER_ENGINEERING",
                "2026-01-01T00:00:00Z",
            )
            plant_recent_topic(
                content_root,
                "POST-20260201-01",
                METRIC_RECENT_TITLE,
                "TECHNICAL_DEEP_DIVE",
                "2026-02-01T00:00:00Z",
            )
            fillers = (
                ("POST-20260301-01", "Monday invoice letters for clients", "2026-03-01T00:00:00Z"),
                ("POST-20260401-01", "Garden watering logs for summer", "2026-04-01T00:00:00Z"),
                ("POST-20260501-01", "Library shelf labels for fiction", "2026-05-01T00:00:00Z"),
                ("POST-20260601-01", "Bicycle chain oiling each quarter", "2026-06-01T00:00:00Z"),
            )
            for post_id, title, stamp in fillers:
                plant_recent_topic(content_root, post_id, title, "POSITIONING", stamp)
            chosen = cycle_mod.select_topic(content_root)
            deferred = " ".join(chosen["_deferred"])
            self.assertIn("Judge RAG", deferred)
            self.assertIn("project-metric", deferred)
            self.assertNotIn("Hit Rate", deferred)
            self.assertNotIn("Backend platform habits", deferred)
            self.assertEqual(chosen["topic"], LEADFINDER_TOPIC)
            self.assertEqual(cycle_mod.strategy_mod.topic_audience_conflict(chosen, engine.load_claims()), "")

    def test_specialist_body_cannot_pass_as_broad_tech(self):
        previous_content = engine.CONTENT
        previous_active = cycle_mod.ACTIVE_CONTENT_ROOT
        try:
            with tempfile.TemporaryDirectory() as tmp:
                content_root = Path(tmp)
                write_strategy(content_root)
                for name in ("drafts", "ideas", "plans", "previews", "cycles"):
                    (content_root / name).mkdir()
                cycle_mod.ACTIVE_CONTENT_ROOT = content_root
                engine.CONTENT = content_root
                fake = [
                    {
                        "insight": "Name the metric and the cutoff.",
                        "hook": "receipt",
                        "hooks_considered": "receipt",
                        "body": ALIGNED_RAG_BODY,
                        "claims_yaml": "- id: AI_DEV_027\n  use: fact\n  scope: \"Applies only to the tested IntelliJ workflow and use case. It is not a claim that the layer is universally worse, and it does not name an external tool.\"",
                        "lane": "POSITIONING",
                        "audience": "BROAD_TECH",
                    }
                ]
                runtime = RecordingRuntime(fake)
                with self.assertRaises(cycle_mod.CycleError) as raised:
                    cycle_mod.Orchestrator(content_root, ROOT, runtime, max_revisions=2).run()
                self.assertEqual(raised.exception.code, "STRATEGY_MISALIGNED")
                message = str(raised.exception)
                self.assertIn("Audience mismatch:", message)
                self.assertIn("BROAD_TECH", message)
                self.assertNotIn("after revision limit", message)
                self.assertEqual(runtime.calls, 1)
                self.assertFalse(any(prompt.startswith("Revise this") for prompt in runtime.prompts))
                self.assertFalse(any(prompt.startswith("Semantic review") for prompt in runtime.prompts))
                post_id = "POST-" + engine.today_stamp() + "-01"
                draft = (content_root / "drafts" / f"{post_id}.md").read_text(encoding="utf-8")
                self.assertIn("Hit Rate 1.0", draft)
                self.assertNotIn("critic_verdict: PASS", draft)
                self.assertFalse((content_root / "previews" / f"{post_id}.html").is_file())
                state = cycle_mod.load_cycle(content_root, post_id)
                self.assertEqual(state.outcome, "STRATEGY_MISALIGNED")
                self.assertEqual(state.revision_count, 0)
                self.assertEqual(state.audience, "BROAD_TECH")
        finally:
            engine.CONTENT = previous_content
            cycle_mod.ACTIVE_CONTENT_ROOT = previous_active


UNFRAMED_OPINION = (
    "Enough persistent project context to preserve decisions was preferable here "
    "to a larger orchestration layer that did not improve the tested workflow."
)

SPLIT_OPINION_BODY = (
    "A visible demo alone does not tell you the surrounding workflow got better.\n\n"
    f"{UNFRAMED_OPINION}\n\n"
    "The lesson I take: more agent scaffolding does not automatically improve developer experience.\n"
)

SPLIT_OPINION_FIXED = SPLIT_OPINION_BODY.replace(
    UNFRAMED_OPINION,
    "My conclusion: " + UNFRAMED_OPINION[0].lower() + UNFRAMED_OPINION[1:],
)

WIN_SENTENCE = "The lighter setup's win is what that comparison established."

WIN_BODY = (
    "A visible demo alone does not tell you the surrounding workflow got better.\n\n"
    "An additional agent orchestration and scaffolding layer was evaluated on the tested IntelliJ workflow, "
    "did not improve that workflow, and increased friction for the tested use case.\n\n"
    f"{WIN_SENTENCE}\n"
)

PROVED_SENTENCE = "I proved that more agent scaffolding improved the workflow result."


def _opinion_claims() -> str:
    by_id = {claim["id"]: claim for claim in engine.load_claims()}
    return claim_lines([(by_id["AI_DEV_017"], "opinion"), (by_id["AI_DEV_031"], "opinion")])


def _framing_concern() -> dict:
    return {
        "kind": "semantic",
        "sentence": UNFRAMED_OPINION,
        "claim_id": "AI_DEV_031",
        "exceeds": "Derived opinion stated as a bare assertion.",
        "recoverable": True,
        "correction": "Add one conclusion frame to that sentence: My conclusion, I treat this as, or The lesson I take.",
    }


def _compose(body: str, claims_yaml: str) -> dict:
    return {
        "insight": "Extra tooling has to change the work.",
        "hook": "receipt",
        "hooks_considered": "receipt follows the reader stake.",
        "body": body,
        "claims_yaml": claims_yaml,
        "lane": "POSITIONING",
        "audience": "BROAD_TECH",
    }


CRITIC_PASS = {
    "verdict": "PASS",
    "blocking_issues": [],
    "non_blocking_issues": [],
    "revision_instructions": [],
    "reason": "ok",
}


class SemanticDisagreementTests(unittest.TestCase):
    def setUp(self):
        self._content = engine.CONTENT
        self._active = cycle_mod.ACTIVE_CONTENT_ROOT

    def tearDown(self):
        engine.CONTENT = self._content
        cycle_mod.ACTIVE_CONTENT_ROOT = self._active

    def _prepare(self) -> Path:
        content_root = Path(self.tmp.name)
        write_strategy(content_root)
        for name in ("drafts", "ideas", "plans", "previews", "cycles"):
            (content_root / name).mkdir(parents=True, exist_ok=True)
        cycle_mod.ACTIVE_CONTENT_ROOT = content_root
        engine.CONTENT = content_root
        return content_root

    def test_gate_keeps_blocks_and_drops_editorial_objections(self):
        framing = cycle_mod.SemanticConcern(
            "semantic",
            UNFRAMED_OPINION,
            "AI_DEV_031",
            "Derived opinion stated as a bare assertion.",
            True,
            "Add one conclusion frame to that sentence: My conclusion.",
        )
        editorial = cycle_mod.SemanticConcern(
            "editorial",
            "A visible demo alone does not tell you the surrounding workflow got better.",
            "",
            "Weak contrast.",
            True,
            "Sharpen the contrast.",
        )
        evidence = cycle_mod.SemanticConcern(
            "semantic",
            WIN_SENTENCE,
            "AI_DEV_027",
            "States a comparative win the fact does not support.",
            False,
            "Remove the comparative win.",
        )
        self.assertEqual(cycle_mod.resolve_semantic_verdict("BLOCKED", "PASS", None, "fine")[0], "BLOCKED")
        self.assertEqual(cycle_mod.resolve_semantic_verdict("NEEDS_EDIT", "PASS", None, "fine")[0], "NEEDS_EDIT")
        self.assertEqual(
            cycle_mod.resolve_semantic_verdict("PASS", "NEEDS_EDIT", editorial, "The contrast is weak."),
            ("PASS", "script"),
        )
        self.assertEqual(
            cycle_mod.resolve_semantic_verdict("PASS", "NEEDS_EDIT", None, "The contrast is weak and the hook is soft."),
            ("PASS", "script"),
        )
        self.assertEqual(
            cycle_mod.resolve_semantic_verdict("PASS", "NEEDS_EDIT", framing, "Needs a frame."),
            ("NEEDS_EDIT", "retain"),
        )
        self.assertEqual(
            cycle_mod.resolve_semantic_verdict("PASS", "BLOCKED", evidence, "Unsupported win."),
            ("BLOCKED", "retain"),
        )
        self.assertEqual(
            cycle_mod.resolve_semantic_verdict("PASS", "NEEDS_EDIT", None, "Derived claim AI_DEV_031 needs a frame."),
            ("NEEDS_EDIT", "undiagnosed"),
        )
        self.assertTrue(cycle_mod.repair_applied(SPLIT_OPINION_FIXED, framing))
        self.assertFalse(cycle_mod.repair_applied(SPLIT_OPINION_BODY, framing))
        beside = SPLIT_OPINION_BODY.rstrip() + (
            "\n\nMy conclusion: enough persistent project context to preserve decisions "
            "was preferable here to a larger orchestration layer.\n"
        )
        self.assertFalse(cycle_mod.repair_applied(beside, framing))
        prefixed = SPLIT_OPINION_BODY.replace(UNFRAMED_OPINION, "My conclusion: " + UNFRAMED_OPINION)
        self.assertTrue(cycle_mod.repair_applied(prefixed, framing))
        self.assertIn(UNFRAMED_OPINION.casefold(), prefixed.casefold())

    def test_editorial_objection_does_not_block_semantic_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.tmp = SimpleNamespace(name=tmp)
            content_root = self._prepare()
            fake = [
                _compose(SPLIT_OPINION_BODY, _opinion_claims()),
                {
                    "verdict": "NEEDS_EDIT",
                    "findings": "The contrast is weak and the hook is soft.",
                    "concern": {
                        "kind": "editorial",
                        "sentence": "A visible demo alone does not tell you the surrounding workflow got better.",
                        "claim_id": "",
                        "exceeds": "Weak contrast.",
                        "recoverable": True,
                        "correction": "Sharpen the contrast.",
                    },
                },
                CRITIC_PASS,
            ]
            runtime = RecordingRuntime(fake)
            state = cycle_mod.Orchestrator(content_root, ROOT, runtime, max_revisions=2).run()
            self.assertEqual(state.revision_count, 0)
            self.assertFalse(any(prompt.startswith("Revise this") for prompt in runtime.prompts))
            draft = (content_root / "drafts" / f"{state.post_id}.md").read_text(encoding="utf-8")
            fields, rest = engine.parse_frontmatter(draft)
            self.assertEqual(fields["review_verdict"], "PASS")
            self.assertNotIn("weak contrast", rest.casefold())
            ready, reasons = cycle_mod.readiness_report(content_root, state.post_id)
            self.assertTrue(ready, reasons)

    def test_failed_critic_revision_does_not_count_as_applied(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.tmp = SimpleNamespace(name=tmp)
            content_root = self._prepare()

            class FailRevise(RecordingRuntime):
                def ask(self, prompt, **kwargs):
                    if prompt.startswith("Revise this"):
                        self.prompts.append(prompt)
                        raise runtime_mod.CursorRuntimeError("no JSON object in agent output")
                    return super().ask(prompt, **kwargs)

            fake = [
                _compose(SPLIT_OPINION_BODY, _opinion_claims()),
                {"verdict": "PASS", "findings": "script pass"},
                {
                    "verdict": "REVISE",
                    "blocking_issues": ["The draft reads like a research note."],
                    "non_blocking_issues": [],
                    "revision_instructions": ["Cut the meta paragraph."],
                    "reason": "Not publishable yet.",
                },
            ]
            runtime = FailRevise(fake)
            with self.assertRaises(cycle_mod.CycleError) as raised:
                cycle_mod.Orchestrator(content_root, ROOT, runtime, max_revisions=2).run()
            self.assertEqual(raised.exception.code, "RUNTIME_FAILURE")
            self.assertTrue(any(prompt.startswith("Revise this") for prompt in runtime.prompts))
            post_id = "POST-" + engine.today_stamp() + "-01"
            state = cycle_mod.load_cycle(content_root, post_id)
            self.assertEqual(state.revision_count, 0)
            self.assertEqual(state.outcome, "RUNTIME_FAILURE")
            self.assertFalse(any(str(event).startswith("revision ") for event in state.events))
            draft = (content_root / "drafts" / f"{post_id}.md").read_text(encoding="utf-8")
            fields, rest = engine.parse_frontmatter(draft)
            self.assertEqual(fields["critic_verdict"], "REVISE")
            self.assertEqual(fields["critic_body_sha256"], fields["body_sha256"])
            self.assertEqual(fields["body_sha256"], engine.body_hash(engine.section_map(rest)["Body"]))

    def test_humanize_decodes_non_cp1252_report_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "post.md"
            body = "A verified protocol \u2014 still needs a scope \U0001f512.\n"
            path.write_text(
                "---\nid: POST-UTF8\nkind: post\nstatus: drafted\n---\n\n## Body\n\n" + body,
                encoding="utf-8",
            )
            try:
                cycle_mod.humanize_post(path)
            except cycle_mod.CycleError as exc:
                self.assertNotIn("charmap", str(exc))
            text = path.read_text(encoding="utf-8")
            self.assertIn("Style signal only", text)
            self.assertIn("\U0001f512", text)

    def test_model_evidence_problem_blocks_despite_script_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.tmp = SimpleNamespace(name=tmp)
            content_root = self._prepare()
            by_id = {claim["id"]: claim for claim in engine.load_claims()}
            fake = [
                _compose(WIN_BODY, claim_lines([(by_id["AI_DEV_027"], "fact")])),
                {
                    "verdict": "BLOCKED",
                    "findings": "The comparison does not establish a win.",
                    "concern": {
                        "kind": "semantic",
                        "sentence": WIN_SENTENCE,
                        "claim_id": "AI_DEV_027",
                        "exceeds": "States a comparative win the fact does not support.",
                        "recoverable": False,
                        "correction": "Remove the comparative win. Do not invent a ranking.",
                    },
                },
            ]
            runtime = RecordingRuntime(fake)
            with self.assertRaises(cycle_mod.CycleError) as raised:
                cycle_mod.Orchestrator(content_root, ROOT, runtime, max_revisions=2).run()
            self.assertEqual(raised.exception.code, "SEMANTIC_FAILURE")
            self.assertIn(WIN_SENTENCE, str(raised.exception))
            self.assertIn("AI_DEV_027", str(raised.exception))
            post_id = "POST-" + engine.today_stamp() + "-01"
            state = cycle_mod.load_cycle(content_root, post_id)
            self.assertEqual(state.outcome, "SEMANTIC_BLOCKED")
            self.assertEqual(state.revision_count, 0)
            draft = (content_root / "drafts" / f"{post_id}.md").read_text(encoding="utf-8")
            fields, _rest = engine.parse_frontmatter(draft)
            self.assertEqual(fields["review_verdict"], "BLOCKED")
            self.assertEqual(fields.get("editorial_verdict", ""), "")
            self.assertEqual(fields.get("critic_verdict", ""), "")
            self.assertFalse((content_root / "previews" / f"{post_id}.html").is_file())
            ready, reasons = cycle_mod.readiness_report(content_root, post_id)
            self.assertFalse(ready)
            self.assertTrue(any(reason.startswith("skipped gate:") for reason in reasons))
            self.assertFalse(any("editorial review not current PASS" in reason for reason in reasons))
            self.assertFalse(any(prompt.startswith("Revise this") for prompt in runtime.prompts))

    def test_source_contradiction_blocks_despite_script_pass(self):
        overclaim = (
            "On 28 Oct 2026, GitHub Copilot Fusion became generally available "
            "with guaranteed sub-second routing on every request."
        )
        body = (
            "Default traces may show that a tool ran without what it was asked or returned.\n\n"
            f"{overclaim}"
        )
        bundle = (ROOT / "research/trends/2026-11-12-reconciliation.md").read_text(encoding="utf-8")
        verdict, reasons = engine.review_body(body, [], engine.load_claims(), source_bundle=bundle)
        self.assertEqual(verdict, "PASS", reasons)
        with tempfile.TemporaryDirectory() as tmp:
            self.tmp = SimpleNamespace(name=tmp)
            content_root = Path(tmp)
            write_telemetry_strategy(content_root)
            for name in ("drafts", "ideas", "plans", "previews", "cycles"):
                (content_root / name).mkdir(parents=True, exist_ok=True)
            cycle_mod.ACTIVE_CONTENT_ROOT = content_root
            engine.CONTENT = content_root
            fake = [
                {
                    "insight": "Separate model, tool, and completion signals.",
                    "hook": "myth",
                    "hooks_considered": "myth",
                    "body": body,
                    "claims_yaml": "- none",
                    "lane": "TECHNICAL_DEEP_DIVE",
                    "audience": "AI_ENGINEERING",
                },
                {
                    "verdict": "BLOCKED",
                    "findings": "Routing guarantee is not in the topic research.",
                    "concern": {
                        "kind": "semantic",
                        "sentence": overclaim,
                        "claim_id": "",
                        "exceeds": "The research does not state guaranteed sub-second routing on every request.",
                        "recoverable": False,
                        "correction": "State only what the changelog and reconciliation note report about Fusion GA.",
                    },
                },
            ]
            runtime = RecordingRuntime(fake)
            with self.assertRaises(cycle_mod.CycleError) as raised:
                cycle_mod.Orchestrator(content_root, ROOT, runtime, max_revisions=2).run()
            self.assertEqual(raised.exception.code, "SEMANTIC_FAILURE")
            sem_prompt = next(p for p in runtime.prompts if p.startswith("Semantic review"))
            self.assertIn("Token overlap does not prove the assertion", sem_prompt)
            post_id = "POST-" + engine.today_stamp() + "-01"
            state = cycle_mod.load_cycle(content_root, post_id)
            self.assertEqual(state.outcome, "SEMANTIC_BLOCKED")
            ready, reasons_ready = cycle_mod.readiness_report(content_root, post_id)
            self.assertFalse(ready)
            self.assertTrue(any(reason.startswith("skipped gate:") for reason in reasons_ready))

    def test_topical_overlap_without_substantiation_blocks(self):
        overclaim = "On 12 Nov 2026, OpenTelemetry GenAI conventions reached Stable status worldwide."
        body = (
            "Operators should design traces around invoke-agent and execute-tool boundaries.\n\n"
            f"{overclaim}"
        )
        bundle = (ROOT / "research/trends/2026-11-12-reconciliation.md").read_text(encoding="utf-8")
        verdict, reasons = engine.review_body(body, [], engine.load_claims(), source_bundle=bundle)
        self.assertEqual(verdict, "PASS", reasons)
        with tempfile.TemporaryDirectory() as tmp:
            self.tmp = SimpleNamespace(name=tmp)
            content_root = Path(tmp)
            write_telemetry_strategy(content_root)
            for name in ("drafts", "ideas", "plans", "previews", "cycles"):
                (content_root / name).mkdir(parents=True, exist_ok=True)
            cycle_mod.ACTIVE_CONTENT_ROOT = content_root
            engine.CONTENT = content_root
            fake = [
                {
                    "insight": "Separate model, tool, and completion signals.",
                    "hook": "myth",
                    "hooks_considered": "myth",
                    "body": body,
                    "claims_yaml": "- none",
                    "lane": "TECHNICAL_DEEP_DIVE",
                    "audience": "AI_ENGINEERING",
                },
                {
                    "verdict": "BLOCKED",
                    "findings": "Stable status is not stated in the topic research.",
                    "concern": {
                        "kind": "semantic",
                        "sentence": overclaim,
                        "claim_id": "",
                        "exceeds": "The reconciliation note reports Development status, not Stable worldwide.",
                        "recoverable": False,
                        "correction": "Keep Development status and retrieval date from the research note.",
                    },
                },
            ]
            runtime = RecordingRuntime(fake)
            with self.assertRaises(cycle_mod.CycleError) as raised:
                cycle_mod.Orchestrator(content_root, ROOT, runtime, max_revisions=2).run()
            self.assertEqual(raised.exception.code, "SEMANTIC_FAILURE")
            sem_prompt = next(p for p in runtime.prompts if p.startswith("Semantic review"))
            self.assertIn("does not state what the sentence asserts", sem_prompt)
            post_id = "POST-" + engine.today_stamp() + "-01"
            state = cycle_mod.load_cycle(content_root, post_id)
            self.assertEqual(state.outcome, "SEMANTIC_BLOCKED")

    def test_model_pass_cannot_clear_a_measured_opinion(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.tmp = SimpleNamespace(name=tmp)
            content_root = self._prepare()
            by_id = {claim["id"]: claim for claim in engine.load_claims()}
            fake = [
                _compose(PROVED_SENTENCE, claim_lines([(by_id["AI_DEV_017"], "opinion")])),
                {"verdict": "PASS", "findings": "Looks grounded."},
            ]
            with self.assertRaises(cycle_mod.CycleError) as raised:
                cycle_mod.Orchestrator(content_root, ROOT, cycle_mod.FakeRuntime(fake), max_revisions=2).run()
            self.assertIn(PROVED_SENTENCE, str(raised.exception))
            post_id = "POST-" + engine.today_stamp() + "-01"
            state = cycle_mod.load_cycle(content_root, post_id)
            self.assertEqual(state.outcome, "SEMANTIC_BLOCKED")
            draft = (content_root / "drafts" / f"{post_id}.md").read_text(encoding="utf-8")
            fields, _rest = engine.parse_frontmatter(draft)
            self.assertEqual(fields["review_verdict"], "BLOCKED")

    def test_repeated_framing_request_is_authoritative_until_the_sentence_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.tmp = SimpleNamespace(name=tmp)
            content_root = self._prepare()
            concern = _framing_concern()
            first = SPLIT_OPINION_BODY.replace("A visible demo", "A visible public demo", 1)
            second = first.replace("The lesson I take:", "The lesson I take from that work:", 1)
            fake = [
                _compose(SPLIT_OPINION_BODY, _opinion_claims()),
                {"verdict": "NEEDS_EDIT", "findings": "The derived sentence has no frame.", "concern": concern},
                {"body": first},
                {"verdict": "NEEDS_EDIT", "findings": "The derived sentence has no frame.", "concern": concern},
                {"body": second},
                {"verdict": "NEEDS_EDIT", "findings": "The derived sentence has no frame.", "concern": concern},
            ]
            runtime = RecordingRuntime(fake)
            with self.assertRaises(cycle_mod.CycleError) as raised:
                cycle_mod.Orchestrator(content_root, ROOT, runtime, max_revisions=2).run()
            message = str(raised.exception)
            self.assertEqual(raised.exception.code, "REVISION_EXHAUSTED")
            self.assertIn("after revision limit", message)
            self.assertIn(UNFRAMED_OPINION, message)
            self.assertIn("AI_DEV_031", message)
            revises = [prompt for prompt in runtime.prompts if prompt.startswith("Revise this")]
            self.assertEqual(len(revises), 2)
            for prompt in revises:
                self.assertIn("Blocking semantic concern", prompt)
                self.assertIn(UNFRAMED_OPINION, prompt)
                self.assertIn("AI_DEV_031", prompt)
                self.assertNotIn("not authoritative", prompt)
            post_id = "POST-" + engine.today_stamp() + "-01"
            state = cycle_mod.load_cycle(content_root, post_id)
            self.assertEqual(state.revision_count, 2)
            draft = (content_root / "drafts" / f"{post_id}.md").read_text(encoding="utf-8")
            fields, rest = engine.parse_frontmatter(draft)
            self.assertEqual(fields["review_verdict"], "NEEDS_EDIT")
            self.assertEqual(fields["review_body_sha256"], engine.body_hash(engine.section_map(rest)["Body"]))
            self.assertNotEqual(fields["review_body_sha256"], engine.body_hash(SPLIT_OPINION_BODY))
            self.assertEqual(fields.get("editorial_verdict", ""), "")
            self.assertFalse((content_root / "previews" / f"{post_id}.html").is_file())
            ready, reasons = cycle_mod.readiness_report(content_root, post_id)
            self.assertFalse(ready)
            self.assertTrue(any(UNFRAMED_OPINION in reason for reason in reasons))
            self.assertTrue(any("skipped gate: editorial review after semantic NEEDS_EDIT" in reason for reason in reasons))
            self.assertFalse(any("editorial review not current PASS" in reason for reason in reasons))

    def test_framed_paraphrase_does_not_clear_the_bare_sentence(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.tmp = SimpleNamespace(name=tmp)
            content_root = self._prepare()
            concern = _framing_concern()
            paraphrase = (
                "My conclusion: enough persistent project context to preserve decisions "
                "was preferable here to a larger orchestration layer."
            )
            first = SPLIT_OPINION_BODY.rstrip() + "\n\n" + paraphrase + "\n"
            second = first.replace("The lesson I take:", "The lesson I take from that work:", 1)
            fake = [
                _compose(SPLIT_OPINION_BODY, _opinion_claims()),
                {"verdict": "NEEDS_EDIT", "findings": "The derived sentence has no frame.", "concern": concern},
                {"body": first},
                {"verdict": "NEEDS_EDIT", "findings": "The derived sentence has no frame.", "concern": concern},
                {"body": second},
                {"verdict": "NEEDS_EDIT", "findings": "The derived sentence has no frame.", "concern": concern},
            ]
            runtime = RecordingRuntime(fake)
            with self.assertRaises(cycle_mod.CycleError) as raised:
                cycle_mod.Orchestrator(content_root, ROOT, runtime, max_revisions=2).run()
            message = str(raised.exception)
            self.assertEqual(raised.exception.code, "REVISION_EXHAUSTED")
            self.assertIn(UNFRAMED_OPINION, message)
            self.assertIn("AI_DEV_031", message)
            self.assertNotIn("satisfied repair", message.casefold())
            revises = [prompt for prompt in runtime.prompts if prompt.startswith("Revise this")]
            self.assertEqual(len(revises), 2)
            for prompt in revises:
                self.assertIn("Blocking semantic concern", prompt)
                self.assertIn(UNFRAMED_OPINION, prompt)
                self.assertNotIn("not authoritative", prompt)
            post_id = "POST-" + engine.today_stamp() + "-01"
            state = cycle_mod.load_cycle(content_root, post_id)
            self.assertEqual(state.revision_count, 2)
            self.assertNotEqual(state.outcome, cycle_mod.OUTCOME_READY)
            draft = (content_root / "drafts" / f"{post_id}.md").read_text(encoding="utf-8")
            self.assertIn(UNFRAMED_OPINION, draft)
            self.assertNotIn("Reviewer repeated a satisfied repair", draft)
            fields, rest = engine.parse_frontmatter(draft)
            self.assertEqual(fields["review_verdict"], "NEEDS_EDIT")
            self.assertEqual(fields["review_body_sha256"], engine.body_hash(engine.section_map(rest)["Body"]))
            self.assertEqual(fields.get("editorial_verdict", ""), "")
            ready, reasons = cycle_mod.readiness_report(content_root, post_id)
            self.assertFalse(ready)
            self.assertTrue(any(UNFRAMED_OPINION in reason for reason in reasons))
            self.assertTrue(any("skipped gate: editorial review after semantic NEEDS_EDIT" in reason for reason in reasons))

    def test_recorded_leadfinder_exhaustion_is_a_sentence_frame_gap(self):
        content = ROOT / "content"
        draft_path = content / "drafts" / "POST-20261020-01.md"
        draft = draft_path.read_text(encoding="utf-8")
        fields, rest = engine.parse_frontmatter(draft)
        sections = engine.section_map(rest)
        body = sections["Body"]
        sentence = (
            "That is not the same as treating deterministic scoring and LLM scoring "
            "as one result; they are different mechanisms."
        )
        self.assertIn(sentence, body)
        self.assertIn("The lesson I take:", body)
        records = engine.claim_records(sections["Claims"])
        verdict, reasons = engine.review_body(body, records, engine.load_claims())
        self.assertEqual((verdict, reasons), ("PASS", []))
        self.assertEqual(engine.classify_statement(sentence, engine.load_claims()), "unclassified")
        self.assertEqual(fields["review_verdict"], "NEEDS_EDIT")
        self.assertEqual(fields["review_body_sha256"], engine.body_hash(body))
        self.assertIn("LEADFINDER_010", sections["Review"])
        self.assertIn("personal-conclusion frame", sections["Review"])
        self.assertNotIn("Semantic concern:", sections["Review"])
        self.assertEqual(fields.get("editorial_verdict", ""), "")
        self.assertEqual(fields.get("critic_verdict", ""), "")
        self.assertEqual(fields.get("preview_body_sha256", ""), "")
        findings = cycle_mod.editorial_mod.editorial_review(body, sections.get("Evidence gaps", ""))
        self.assertNotIn("Weakest dimension: contrast", findings.weakest)
        self.assertIn(findings.verdict, {"PASS", "REVISE"})
        claim = next(item for item in engine.load_claims() if item["id"] == "LEADFINDER_010")
        self.assertEqual(claim["verification"], "derived")
        self.assertIs(claim["public_safe"], True)
        state = cycle_mod.load_cycle(content, "POST-20261020-01")
        self.assertEqual(state.revision_count, 2)
        self.assertEqual(state.outcome, "REVISION_EXHAUSTED")
        self.assertEqual(state.stage, "semantic")
        self.assertEqual(state.error, "semantic NEEDS_EDIT after revision limit")
        self.assertEqual(state.events, [])
        ready, reasons = cycle_mod.readiness_report(content, "POST-20261020-01")
        self.assertFalse(ready)
        self.assertTrue(any("LEADFINDER_010" in reason for reason in reasons))
        self.assertTrue(any("skipped gate: editorial review after semantic NEEDS_EDIT" in reason for reason in reasons))
        self.assertTrue(any("skipped gate: critic review after semantic NEEDS_EDIT" in reason for reason in reasons))
        self.assertTrue(any("skipped gate: preview after semantic NEEDS_EDIT" in reason for reason in reasons))
        self.assertFalse((content / "previews" / "POST-20261020-01.html").is_file())

    def test_repeated_request_after_the_repair_does_not_spend_another_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.tmp = SimpleNamespace(name=tmp)
            content_root = self._prepare()
            concern = _framing_concern()
            fake = [
                _compose(SPLIT_OPINION_BODY, _opinion_claims()),
                {"verdict": "NEEDS_EDIT", "findings": "The derived sentence has no frame.", "concern": concern},
                {"body": SPLIT_OPINION_FIXED},
                {"verdict": "NEEDS_EDIT", "findings": "The derived sentence has no frame.", "concern": concern},
                CRITIC_PASS,
            ]
            runtime = RecordingRuntime(fake)
            state = cycle_mod.Orchestrator(content_root, ROOT, runtime, max_revisions=2).run()
            self.assertEqual(state.revision_count, 1)
            self.assertEqual(state.outcome, cycle_mod.OUTCOME_READY)
            draft = (content_root / "drafts" / f"{state.post_id}.md").read_text(encoding="utf-8")
            self.assertIn("Reviewer repeated a satisfied repair", draft)
            self.assertIn("My conclusion:", draft)
            fields, rest = engine.parse_frontmatter(draft)
            self.assertEqual(fields["review_verdict"], "PASS")
            self.assertEqual(fields["review_body_sha256"], engine.body_hash(engine.section_map(rest)["Body"]))
            ready, reasons = cycle_mod.readiness_report(content_root, state.post_id)
            self.assertTrue(ready, reasons)

    def test_unchanged_body_is_not_counted_as_a_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.tmp = SimpleNamespace(name=tmp)
            content_root = self._prepare()
            concern = _framing_concern()
            fake = [
                _compose(SPLIT_OPINION_BODY, _opinion_claims()),
                {"verdict": "NEEDS_EDIT", "findings": "The derived sentence has no frame.", "concern": concern},
                {"body": SPLIT_OPINION_BODY},
            ]
            runtime = RecordingRuntime(fake)
            with self.assertRaises(cycle_mod.CycleError) as raised:
                cycle_mod.Orchestrator(content_root, ROOT, runtime, max_revisions=2).run()
            message = str(raised.exception)
            self.assertEqual(raised.exception.code, "REVISION_EXHAUSTED")
            self.assertIn("left the blocking sentence unchanged", message)
            self.assertIn(UNFRAMED_OPINION, message)
            self.assertEqual(len([prompt for prompt in runtime.prompts if prompt.startswith("Revise this")]), 1)
            post_id = "POST-" + engine.today_stamp() + "-01"
            state = cycle_mod.load_cycle(content_root, post_id)
            self.assertEqual(state.revision_count, 0)
            ready, _reasons = cycle_mod.readiness_report(content_root, post_id)
            self.assertFalse(ready)

    def test_undiagnosed_grounding_note_does_not_loop_or_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.tmp = SimpleNamespace(name=tmp)
            content_root = self._prepare()
            fake = [
                _compose(SPLIT_OPINION_BODY, _opinion_claims()),
                {"verdict": "NEEDS_EDIT", "findings": "Derived claim AI_DEV_031 is an unframed conclusion."},
            ]
            runtime = RecordingRuntime(fake)
            with self.assertRaises(cycle_mod.CycleError) as raised:
                cycle_mod.Orchestrator(content_root, ROOT, runtime, max_revisions=2).run()
            self.assertEqual(raised.exception.code, "SEMANTIC_FAILURE")
            self.assertIn("not diagnosable", str(raised.exception))
            self.assertIn("AI_DEV_031", str(raised.exception))
            self.assertFalse(any(prompt.startswith("Revise this") for prompt in runtime.prompts))
            post_id = "POST-" + engine.today_stamp() + "-01"
            state = cycle_mod.load_cycle(content_root, post_id)
            self.assertEqual(state.revision_count, 0)
            self.assertNotEqual(state.outcome, cycle_mod.OUTCOME_READY)

    def test_needs_edit_readiness_names_the_review_and_skips_later_gates(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.tmp = SimpleNamespace(name=tmp)
            content_root = self._prepare()
            by_id = {claim["id"]: claim for claim in engine.load_claims()}
            body = (
                "A visible demo alone does not tell you the surrounding workflow got better.\n\n"
                "My conclusion: more agent scaffolding does not automatically improve developer experience.\n"
            )
            note = (
                "Semantic concern:\n"
                f"sentence: {UNFRAMED_OPINION}; claim_id: AI_DEV_031; "
                "exceeds: Derived opinion stated as a bare assertion.; recoverable: yes; "
                "correction: Add My conclusion to that sentence."
            )
            text = post_text(
                by_id["AI_DEV_017"],
                body,
                status="reviewed",
                review="NEEDS_EDIT",
                use="opinion",
            )
            text = text.replace("## Review\n\nNEEDS_EDIT\n", f"## Review\n\n{note}\n")
            post_id = "POST-20260923-01"
            (content_root / "drafts" / f"{post_id}.md").write_text(text, encoding="utf-8")
            ready, reasons = cycle_mod.readiness_report(content_root, post_id)
            self.assertFalse(ready)
            self.assertTrue(any("AI_DEV_031" in reason for reason in reasons))
            self.assertTrue(any("skipped gate: editorial review after semantic NEEDS_EDIT" in reason for reason in reasons))
            self.assertTrue(any("skipped gate: critic review after semantic NEEDS_EDIT" in reason for reason in reasons))
            self.assertTrue(any("skipped gate: preview after semantic NEEDS_EDIT" in reason for reason in reasons))
            self.assertFalse(any("editorial review not current PASS" in reason for reason in reasons))


MCP_ANALYSIS = (
    "MCP specification 2026-07-28 moves servers to a stateless request and response core.\n\n"
    "The protocol retires session-centric initialize flows. Identity rides with each request instead of a pinned session.\n\n"
    "A load balancer therefore treats every call as standalone. Sticky affinity is no longer the free default.\n\n"
    "That deployment change is the part an operator can act on."
)

CIRCULAR_CLOSE = (
    "Hiring managers want the bridge from distributed systems discipline to modern AI systems.\n\n"
    "The stronger version is production engineering habit: evaluate under constraints, "
    "drop what adds friction, and keep systems accountable when the stack changes.\n\n"
    "I tried that habit on one service boundary. It did not stay abstract.\n\n"
    "My conclusion: production engineering habit means evaluate under constraints, "
    "drop what adds friction, and keep systems accountable when the stack changes."
)

CAREER_TOPIC = {
    "topic": "Backend platform habits in Applied AI work",
    "lane": "CAREER_ENGINEERING",
    "audience": "BROAD_TECH",
    "technical_depth": "ACCESSIBLE",
    "why_now": "Many engineers pivoting to AI need a credible story that is not fake AI tenure.",
    "why_this_author": "Verified backend career plus Applied AI areas RAG agents MCP evaluation alongside that career.",
    "why_reader_cares": "Hiring managers want the bridge from distributed systems discipline to modern AI systems.",
}

MCP_TOPIC = {
    "topic": "MCP stateless core changes deployment assumptions",
    "lane": "TREND_REACTION",
    "audience": "AI_ENGINEERING",
    "timeliness": "CURRENT",
    "standalone_value": "HIGH",
    "personal_evidence_strength": "LOW",
    "project_context_required": "NONE",
    "technical_depth": "INTERMEDIATE",
    "why_now": "MCP 2026-07-28 retires session-centric initialize flows in favor of stateless requests (see research/trends/2026-10-07.md).",
    "why_this_author": "Applied AI scope includes MCP and tool integrations; can discuss routing and auth trade-offs at engineering level.",
    "why_reader_cares": "Anyone operating MCP servers behind load balancers needs to know session state is no longer the default model.",
}


def write_telemetry_strategy(content_root: Path) -> None:
    folder = content_root / "strategy"
    folder.mkdir(parents=True, exist_ok=True)
    text = """---
id: STRATEGY-20261112-01
kind: strategy
status: proposed
created: 2026-11-12
concentration_risk: false
---

## Topics

- topic: Separate model steps, tool executions, and final answers in telemetry
  lane: TECHNICAL_DEEP_DIVE
  audience: AI_ENGINEERING
  timeliness: CURRENT
  standalone_value: HIGH
  personal_evidence_strength: LOW
  project_context_required: NONE
  technical_depth: INTERMEDIATE
  visual_potential: NONE
  why_now: OpenTelemetry GenAI Development conventions and Fusion per-step routing. See research/trends/2026-11-12-reconciliation.md.
  why_this_author: Operations thinking only. No tracing rollout claimed.
  why_reader_cares: Default traces may show that a tool ran without what it was asked or returned.

## Mix notes

One sourced trend.
"""
    (folder / "STRATEGY-20261112-01.md").write_text(text, encoding="utf-8")


def write_mcp_strategy(content_root: Path) -> None:
    folder = content_root / "strategy"
    folder.mkdir(parents=True, exist_ok=True)
    lines = [
        "---",
        "id: STRATEGY-20261110-01",
        "kind: strategy",
        "status: proposed",
        "created: 2026-11-10",
        "concentration_risk: false",
        "---",
        "",
        "## Topics",
        "",
    ]
    for key, value in MCP_TOPIC.items():
        prefix = "- topic: " if key == "topic" else f"  {key}: "
        lines.append(prefix + str(value))
    lines.extend(["", "## Mix notes", "", "One sourced trend.", ""])
    (folder / "STRATEGY-20261110-01.md").write_text("\n".join(lines), encoding="utf-8")


class EvidenceFitTests(unittest.TestCase):
    def setUp(self):
        self._content = engine.CONTENT
        self._active = cycle_mod.ACTIVE_CONTENT_ROOT

    def tearDown(self):
        engine.CONTENT = self._content
        cycle_mod.ACTIVE_CONTENT_ROOT = self._active

    def test_irrelevant_personal_claims_are_not_offered_for_the_thesis(self):
        claims = engine.load_claims()
        by_id = {claim["id"]: claim for claim in claims}
        self.assertTrue(cycle_mod.strategy_mod.topic_substance_gap(CAREER_TOPIC, claims))
        self.assertFalse(cycle_mod.strategy_mod.claim_develops_topic(CAREER_TOPIC, by_id["AI_DEV_027"]))
        self.assertFalse(cycle_mod.strategy_mod.claim_develops_topic(MCP_TOPIC, by_id["AI_DEV_022"]))
        self.assertFalse(cycle_mod.strategy_mod.claim_develops_topic(MCP_TOPIC, by_id["CAREER_AI_005"]))
        hit = {
            "topic": "Hit Rate can hide rank quality in RAG",
            "why_now": "Teams still report Hit Rate alone as retrieval quality.",
            "why_this_author": "Scoped nine-question evaluation at k=5 with Hit Rate 1.0 and MRR 0.75 on a RAG project.",
            "why_reader_cares": "A retrieval stack can place a relevant document in the top five while still ranking it below the best slot.",
        }
        self.assertTrue(cycle_mod.strategy_mod.claim_develops_topic(hit, by_id["LEGAL_RAG_007"]))
        prompt = cycle_mod.compose_prompt(MCP_TOPIC)
        self.assertIn("General technical analysis may be the whole post.", prompt)
        self.assertNotIn("some MCP tools behaved as router-only tools", prompt)
        self.assertNotIn("Applied AI work includes MCP and tool integrations.", prompt)
        career_prompt = cycle_mod.compose_prompt(CAREER_TOPIC)
        self.assertNotIn("did not improve that workflow", career_prompt)

    def test_mismatched_intellij_claim_stops_before_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            content_root = Path(tmp)
            write_mcp_strategy(content_root)
            cycle_mod.ACTIVE_CONTENT_ROOT = content_root
            engine.CONTENT = content_root
            by_id = {claim["id"]: claim for claim in engine.load_claims()}
            claims_yaml = claim_lines(
                [(by_id["CAREER_AI_005"], "fact"), (by_id["AI_DEV_022"], "fact")]
            )
            fake = [
                {
                    "insight": "Session state is no longer the default.",
                    "hook": "callout",
                    "hooks_considered": "callout",
                    "body": (
                        "Anyone operating MCP servers behind load balancers needs to know session state is no longer the default model.\n\n"
                        "Applied AI work includes MCP and tool integrations.\n\n"
                        "In that tested IntelliJ/Copilot environment, some MCP tools behaved as router-only tools.\n"
                    ),
                    "claims_yaml": claims_yaml,
                    "lane": "TREND_REACTION",
                    "audience": "AI_ENGINEERING",
                }
            ]
            runtime = RecordingRuntime(fake)
            with self.assertRaises(cycle_mod.CycleError) as raised:
                cycle_mod.Orchestrator(content_root, ROOT, runtime, max_revisions=2).run()
            self.assertEqual(raised.exception.code, "PLAN_MISMATCH")
            self.assertIn("AI_DEV_022", str(raised.exception))
            self.assertIn("CAREER_AI_005", str(raised.exception))
            self.assertEqual(runtime.calls, 1)
            self.assertFalse(any(prompt.startswith("Semantic review") for prompt in runtime.prompts))
            self.assertFalse(any(prompt.startswith("Revise this") for prompt in runtime.prompts))
            post_id = "POST-" + engine.today_stamp() + "-01"
            state = cycle_mod.load_cycle(content_root, post_id)
            self.assertEqual(state.revision_count, 0)
            self.assertEqual(state.outcome, "PLAN_MISMATCH")

    def test_general_analysis_does_not_require_a_personal_anecdote(self):
        with tempfile.TemporaryDirectory() as tmp:
            content_root = Path(tmp)
            write_mcp_strategy(content_root)
            for name in ("drafts", "ideas", "plans", "previews", "cycles"):
                (content_root / name).mkdir(parents=True, exist_ok=True)
            cycle_mod.ACTIVE_CONTENT_ROOT = content_root
            engine.CONTENT = content_root
            verdict, reasons = engine.review_body(MCP_ANALYSIS, [], engine.load_claims())
            self.assertEqual(verdict, "PASS", reasons)
            fake = [
                {
                    "insight": "Stateless MCP calls change what a load balancer can assume.",
                    "hook": "callout",
                    "hooks_considered": "callout names the deployment change.",
                    "body": MCP_ANALYSIS,
                    "claims_yaml": "- none",
                    "lane": "TREND_REACTION",
                    "audience": "AI_ENGINEERING",
                },
                {"verdict": "PASS", "findings": "General technical analysis."},
                {
                    "verdict": "PASS",
                    "blocking_issues": [],
                    "non_blocking_issues": [],
                    "revision_instructions": [],
                    "reason": "ok",
                },
            ]
            runtime = RecordingRuntime(fake)
            state = cycle_mod.Orchestrator(content_root, ROOT, runtime, max_revisions=2).run()
            ready, ready_reasons = cycle_mod.readiness_report(content_root, state.post_id)
            self.assertTrue(ready, ready_reasons)
            self.assertEqual(state.revision_count, 0)
            draft = (content_root / "drafts" / f"{state.post_id}.md").read_text(encoding="utf-8")
            self.assertNotIn("IntelliJ", draft)
            self.assertNotIn("AI_DEV_022", draft)
            self.assertIn("- none", draft)

    def test_career_topic_without_a_practice_claim_is_deferred(self):
        with tempfile.TemporaryDirectory() as tmp:
            content_root = Path(tmp)
            folder = content_root / "strategy"
            folder.mkdir(parents=True)
            text = """---
id: STRATEGY-20261110-02
kind: strategy
status: proposed
created: 2026-11-10
concentration_risk: false
---

## Topics

- topic: Backend platform habits in Applied AI work
  lane: CAREER_ENGINEERING
  audience: BROAD_TECH
  timeliness: EVERGREEN
  standalone_value: HIGH
  personal_evidence_strength: MEDIUM
  project_context_required: NONE
  technical_depth: ACCESSIBLE
  visual_potential: NONE
  why_now: Many engineers pivoting to AI need a credible story that is not fake AI tenure.
  why_this_author: Verified backend career plus Applied AI areas RAG agents MCP evaluation alongside that career.
  why_reader_cares: Hiring managers want the bridge from distributed systems discipline to modern AI systems.

- topic: Desk notes stay on the local machine
  lane: POSITIONING
  audience: BROAD_TECH
  timeliness: EVERGREEN
  standalone_value: HIGH
  personal_evidence_strength: LOW
  project_context_required: NONE
  technical_depth: ACCESSIBLE
  visual_potential: NONE
  why_now: Local notes remain an engineering habit.
  why_this_author: The habit is ordinary practice.
  why_reader_cares: A reader can keep a note without a platform.

## Mix notes

Career topic plus one fallback.
"""
            (folder / "STRATEGY-20261110-02.md").write_text(text, encoding="utf-8")
            chosen = cycle_mod.select_topic(content_root)
            self.assertEqual(chosen["topic"], "Desk notes stay on the local machine")
            deferred = " ".join(chosen["_deferred"])
            self.assertIn("Backend platform habits", deferred)
            self.assertIn("no concrete practice", deferred)
            with self.assertRaises(cycle_mod.CycleError) as raised:
                cycle_mod.select_topic(content_root, "Backend platform habits in Applied AI work")
            self.assertEqual(raised.exception.code, "STRATEGY_MISALIGNED")
            self.assertIn("no concrete practice", str(raised.exception))

    def test_published_anecdote_stays_similar_after_the_window_moves(self):
        with tempfile.TemporaryDirectory() as tmp:
            content_root = Path(tmp)
            folder = content_root / "strategy"
            folder.mkdir(parents=True)
            (folder / "STRATEGY-20261110-03.md").write_text(
                """---
id: STRATEGY-20261110-03
kind: strategy
status: proposed
created: 2026-11-10
concentration_risk: false
---

## Topics

- topic: Complexity has to earn its place in agent workflows
  lane: POSITIONING
  audience: BROAD_TECH
  timeliness: EVERGREEN
  standalone_value: HIGH
  personal_evidence_strength: HIGH
  project_context_required: LIGHT
  technical_depth: ACCESSIBLE
  visual_potential: ADDITIVE
  why_now: Agent demos accumulate tools faster than evidence.
  why_this_author: Tested workflow where extra orchestration increased friction without improving the use case.
  why_reader_cares: Readers learn a reusable decision frame for agent tooling not a product pitch.

- topic: Desk notes stay on the local machine
  lane: POSITIONING
  audience: BROAD_TECH
  timeliness: EVERGREEN
  standalone_value: HIGH
  personal_evidence_strength: LOW
  project_context_required: NONE
  technical_depth: ACCESSIBLE
  visual_potential: NONE
  why_now: Local notes remain an engineering habit.
  why_this_author: The habit is ordinary practice.
  why_reader_cares: A reader can keep a note without a platform.

## Mix notes

One repeated anecdote and one fallback.
""",
                encoding="utf-8",
            )
            ideas = content_root / "ideas"
            ideas.mkdir()
            (ideas / "IDEA-20260927-01.md").write_text(
                """---
id: IDEA-20260927-01
kind: idea
status: proposed
created: 2026-09-27
---

## Insight

On the tested IntelliJ workflow, an additional agent orchestration and scaffolding layer was evaluated. It did not improve that workflow, and it increased friction for the tested use case. More scaffolding did not automatically improve usability in this IntelliJ experimentation.

## Why it fits

One workflow decision.

## Claims

- none

## Evidence gaps

- none

## Local overlap

- none
""",
                encoding="utf-8",
            )
            published = content_root / "published"
            published.mkdir()
            (published / "POST-20260927-01.md").write_text(
                """---
id: POST-20260927-01
kind: post
status: published
created: 2026-09-27
idea: IDEA-20260927-01
published_at: 2026-10-05T09:54:03Z
---

## Body

Published scaffolding note.

## Claims

- none
""",
                encoding="utf-8",
            )
            fillers = (
                ("POST-20261101-01", "Monday invoice letters for clients", "2026-11-01T00:00:00Z"),
                ("POST-20261102-01", "Garden watering logs for summer", "2026-11-02T00:00:00Z"),
                ("POST-20261103-01", "Library shelf labels for fiction", "2026-11-03T00:00:00Z"),
                ("POST-20261104-01", "Bicycle chain oiling each quarter", "2026-11-04T00:00:00Z"),
            )
            for post_id, title, stamp in fillers:
                plant_recent_topic(content_root, post_id, title, "POSITIONING", stamp)
            window = cycle_mod.recent_topic_records(content_root, [])
            self.assertNotIn("intellij", " ".join(item.get("topic", "") for item in window).casefold())
            chosen = cycle_mod.select_topic(content_root)
            self.assertEqual(chosen["topic"], "Desk notes stay on the local machine")
            deferred = " ".join(chosen["_deferred"])
            self.assertIn("Complexity has to earn its place", deferred)
            self.assertIn("too similar", deferred)

    def test_repeated_close_is_editorial_revise(self):
        findings = cycle_mod.editorial_mod.editorial_review(CIRCULAR_CLOSE)
        self.assertEqual(findings.verdict, "REVISE")
        self.assertIn("repeats", findings.weakest.casefold())
        strong = cycle_mod.editorial_mod.editorial_review(STRONG_P5_BODY)
        self.assertEqual(strong.verdict, "PASS")


class ClaimsStructureTests(unittest.TestCase):
    def test_blank_claims_stop_before_semantic_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            content_root = Path(tmp)
            write_mcp_strategy(content_root)
            for name in ("drafts", "ideas", "plans", "previews", "cycles"):
                (content_root / name).mkdir(parents=True, exist_ok=True)
            day = engine.today_stamp()
            post_id = f"POST-{day}-01"
            idea_id = f"IDEA-{day}-01"
            idea_path = content_root / "ideas" / f"{idea_id}.md"
            idea_path.write_text(
                (ROOT / "content/ideas/IDEA-20261112-02.md")
                .read_text(encoding="utf-8")
                .replace("IDEA-20261112-02", idea_id),
                encoding="utf-8",
            )
            body = MCP_ANALYSIS
            digest = engine.body_hash(body)
            post_path = content_root / "drafts" / f"{post_id}.md"
            post_path.write_text(
                f"""---
id: {post_id}
kind: post
status: styled
created: {day[:4]}-{day[4:6]}-{day[6:8]}
idea: {idea_id}
plan: PLAN-{day}-01
hook: callout
body_sha256: {digest}
review_verdict:
review_body_sha256:
---

## Hooks considered

callout

## Body

{body}

## Claims



## Evidence gaps

- none

## Style report

Style cleanup applied.
""",
                encoding="utf-8",
            )
            runtime = RecordingRuntime([])
            cycle_mod.ACTIVE_CONTENT_ROOT = content_root
            engine.CONTENT = content_root
            with self.assertRaises(cycle_mod.CycleError) as raised:
                cycle_mod.Orchestrator(content_root, ROOT, runtime, max_revisions=2).run(
                    topic_name="MCP stateless core changes deployment assumptions",
                    post_id=post_id,
                )
            self.assertEqual(raised.exception.code, "SEMANTIC_FAILURE")
            self.assertIn("use - none", str(raised.exception))
            self.assertFalse(any(p.startswith("Semantic review") for p in runtime.prompts))


class HumanReviseResumeTests(unittest.TestCase):
    REVISED_BROAD = BROAD_CONCRETE_BODY.replace("lighter artifacts", "lighter local artifacts")

    def setUp(self):
        self._content = engine.CONTENT
        self._active = cycle_mod.ACTIVE_CONTENT_ROOT
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = SimpleNamespace(name=self._tmpdir.name)

    def tearDown(self):
        engine.CONTENT = self._content
        cycle_mod.ACTIVE_CONTENT_ROOT = self._active
        self._tmpdir.cleanup()

    def _prepare(self) -> Path:
        content_root = Path(self.tmp.name)
        write_strategy(content_root)
        for name in ("drafts", "ideas", "plans", "previews", "cycles"):
            (content_root / name).mkdir(parents=True, exist_ok=True)
        cycle_mod.ACTIVE_CONTENT_ROOT = content_root
        engine.CONTENT = content_root
        return content_root

    def _pass_fake(self) -> list[dict]:
        return [
            {"verdict": "PASS", "findings": "Grounded."},
            {
                "verdict": "PASS",
                "blocking_issues": [],
                "non_blocking_issues": [],
                "revision_instructions": [],
                "reason": "ok",
            },
        ]

    def _seed_ready_post(self, content_root: Path) -> str:
        by_id = {claim["id"]: claim for claim in engine.load_claims()}
        cited = [("AI_DEV_027", "fact"), ("AI_DEV_017", "opinion"), ("AI_DEV_031", "opinion")]
        claims_yaml = claim_lines([(by_id[claim_id], use) for claim_id, use in cited])
        fake = [
            {
                "insight": "Extra scaffolding has to earn its place.",
                "hook": "receipt",
                "hooks_considered": "receipt fits the observed workflow.",
                "body": BROAD_CONCRETE_BODY,
                "claims_yaml": claims_yaml,
                "lane": "POSITIONING",
                "audience": "BROAD_TECH",
            },
            *self._pass_fake(),
        ]
        state = cycle_mod.Orchestrator(content_root, ROOT, cycle_mod.FakeRuntime(fake), max_revisions=0).run(
            topic_name=BROAD_TOPIC_TITLE
        )
        post_id = state.post_id
        path = content_root / "drafts" / f"{post_id}.md"
        text = path.read_text(encoding="utf-8")
        fields, rest = engine.parse_frontmatter(text)
        sections = engine.section_map(rest)
        fields["human_editorial_decision"] = "REVISE"
        sections["Human revision request"] = "Tighten the opening and fix dated facts.\n"
        engine.write_atomic(
            path,
            engine.render_frontmatter(fields, engine.POST_FIELD_ORDER) + cycle_mod.rebuild_post_rest(sections),
        )
        ready_state = cycle_mod.load_cycle(content_root, post_id)
        self.assertIsNotNone(ready_state)
        ready_state.outcome = cycle_mod.OUTCOME_READY
        cycle_mod.save_cycle(content_root, ready_state)
        return post_id

    def test_resume_subcommand_without_topic(self):
        content_root = self._prepare()
        post_id = self._seed_ready_post(content_root)
        fake_path = content_root / "fake-human.json"
        fake_path.write_text(
            json.dumps([{"body": self.REVISED_BROAD}, *self._pass_fake()]),
            encoding="utf-8",
        )
        note = "Fix the dated fact."
        code = cycle_mod.main(
            [
                "--content-root",
                str(content_root),
                "--repo-root",
                str(ROOT),
                "resume",
                post_id,
                "--human-note",
                note,
                "--fake-responses",
                str(fake_path),
            ]
        )
        self.assertEqual(code, 0)
        fields, _rest = engine.parse_frontmatter(
            (content_root / "drafts" / f"{post_id}.md").read_text(encoding="utf-8")
        )
        self.assertEqual(fields.get("human_editorial_decision", ""), "")

    def test_human_revise_changed_body_clears_pending_and_reaches_ready(self):
        content_root = self._prepare()
        post_id = self._seed_ready_post(content_root)
        path = content_root / "drafts" / f"{post_id}.md"
        pre_hash = engine.parse_frontmatter(path.read_text(encoding="utf-8"))[0]["body_sha256"]
        note = "Tighten the opening and fix dated facts."
        fake = [{"body": self.REVISED_BROAD}, *self._pass_fake()]
        runtime = RecordingRuntime(fake)
        state = cycle_mod.Orchestrator(content_root, ROOT, runtime, max_revisions=0).run(
            post_id=post_id,
            human_note=note,
        )
        ready, reasons = cycle_mod.readiness_report(content_root, post_id)
        self.assertTrue(ready, reasons)
        self.assertEqual(state.outcome, cycle_mod.OUTCOME_READY)
        fields, rest = engine.parse_frontmatter(path.read_text(encoding="utf-8"))
        self.assertEqual(fields.get("human_editorial_decision", ""), "")
        self.assertNotIn("Human revision request", rest)
        self.assertNotEqual(fields["body_sha256"], pre_hash)
        self.assertIn("lighter local artifacts", rest)
        self.assertEqual(list((content_root / "approved").glob("*.md")) if (content_root / "approved").is_dir() else [], [])
        self.assertEqual(list((content_root / "published").glob("*.md")) if (content_root / "published").is_dir() else [], [])
        self.assertTrue(runtime.prompts[0].startswith("Revise this"))

    def test_human_revise_unchanged_body_keeps_pending(self):
        content_root = self._prepare()
        post_id = self._seed_ready_post(content_root)
        path = content_root / "drafts" / f"{post_id}.md"
        body = engine.section_map(engine.parse_frontmatter(path.read_text(encoding="utf-8"))[1])["Body"]
        pre_fields, _ = engine.parse_frontmatter(path.read_text(encoding="utf-8"))
        runtime = RecordingRuntime([{"body": body}])
        with self.assertRaises(cycle_mod.CycleError) as raised:
            cycle_mod.Orchestrator(content_root, ROOT, runtime, max_revisions=0).run(
                post_id=post_id,
                human_note="Fix the opening.",
            )
        self.assertEqual(raised.exception.code, "REVISION_UNCHANGED")
        fields, _rest = engine.parse_frontmatter(path.read_text(encoding="utf-8"))
        self.assertEqual(fields["human_editorial_decision"], "REVISE")
        self.assertEqual(fields["review_body_sha256"], pre_fields["review_body_sha256"])
        self.assertEqual(fields["editorial_body_sha256"], pre_fields["editorial_body_sha256"])
        self.assertEqual(fields["critic_body_sha256"], pre_fields["critic_body_sha256"])
        state = cycle_mod.load_cycle(content_root, post_id)
        self.assertEqual(state.outcome, "REVISION_UNCHANGED")
        ready, _reasons = cycle_mod.readiness_report(content_root, post_id)
        self.assertFalse(ready)

    def test_human_revise_runtime_failure_keeps_feedback(self):
        content_root = self._prepare()
        post_id = self._seed_ready_post(content_root)

        class TimeoutHuman:
            def ask(self, prompt, **kwargs):
                if prompt.startswith("Revise this"):
                    raise runtime_mod.CursorRuntimeError("agent timed out after 180s")
                raise runtime_mod.CursorRuntimeError("unexpected ask")

        with self.assertRaises(cycle_mod.CycleError) as raised:
            cycle_mod.Orchestrator(content_root, ROOT, TimeoutHuman(), max_revisions=0).run(
                post_id=post_id,
                human_note="Fix the opening.",
            )
        self.assertEqual(raised.exception.code, "RUNTIME_FAILURE")
        path = content_root / "drafts" / f"{post_id}.md"
        fields, rest = engine.parse_frontmatter(path.read_text(encoding="utf-8"))
        self.assertEqual(fields["human_editorial_decision"], "REVISE")
        self.assertIn("Fix the opening.", rest)

    def test_human_revise_malformed_output_keeps_feedback(self):
        content_root = self._prepare()
        post_id = self._seed_ready_post(content_root)
        runtime = RecordingRuntime([{"verdict": "PASS"}])
        with self.assertRaises(cycle_mod.CycleError) as raised:
            cycle_mod.Orchestrator(content_root, ROOT, runtime, max_revisions=0).run(
                post_id=post_id,
                human_note="Fix the opening.",
            )
        self.assertEqual(raised.exception.code, "RUNTIME_FAILURE")
        fields, _rest = engine.parse_frontmatter(
            (content_root / "drafts" / f"{post_id}.md").read_text(encoding="utf-8")
        )
        self.assertEqual(fields["human_editorial_decision"], "REVISE")

    def test_human_revise_blocked_pipeline_keeps_pending_decision(self):
        content_root = self._prepare()
        post_id = self._seed_ready_post(content_root)
        fake = [
            {"body": self.REVISED_BROAD},
            {"verdict": "BLOCKED", "findings": "Unsupported external date.", "concern": {}},
        ]
        with self.assertRaises(cycle_mod.CycleError) as raised:
            cycle_mod.Orchestrator(content_root, ROOT, cycle_mod.FakeRuntime(fake), max_revisions=0).run(
                post_id=post_id,
                human_note="Fix the GA date.",
            )
        self.assertEqual(raised.exception.code, "SEMANTIC_FAILURE")
        fields, _rest = engine.parse_frontmatter(
            (content_root / "drafts" / f"{post_id}.md").read_text(encoding="utf-8")
        )
        self.assertEqual(fields["human_editorial_decision"], "REVISE")
        ready, _reasons = cycle_mod.readiness_report(content_root, post_id)
        self.assertFalse(ready)

    def test_set_human_revise_marks_cycle_not_ready(self):
        content_root = self._prepare()
        post_id = self._seed_ready_post(content_root)
        ok, _message = inbox.set_human_decision(content_root, post_id, "REVISE", "Adjust the close.")
        self.assertTrue(ok)
        state = cycle_mod.load_cycle(content_root, post_id)
        self.assertEqual(state.outcome, "NOT_READY")

    def test_overlapping_generate_starts_only_one_job(self):
        content_root = self._prepare()
        entered = []
        release = threading.Event()

        def slow_spawn(*_args, **_kwargs):
            entered.append(threading.get_ident())
            self.assertTrue(release.wait(2), "spawn was not released")
            return SimpleNamespace(pid=os.getpid())

        results: list[tuple[bool, str]] = []

        def worker() -> None:
            results.append(inbox.start_generation(content_root, "run"))

        with patch("inbox.spawn_cycle", side_effect=slow_spawn):
            threads = [threading.Thread(target=worker) for _ in range(2)]
            for thread in threads:
                thread.start()
            deadline = time.time() + 2
            while time.time() < deadline and len(entered) < 1:
                time.sleep(0.01)
            self.assertEqual(len(entered), 1)
            time.sleep(0.05)
            self.assertEqual(len(entered), 1)
            release.set()
            for thread in threads:
                thread.join(2)
        self.assertEqual(len(results), 2)
        self.assertEqual(sorted(ok for ok, _message in results), [False, True])
        self.assertEqual(inbox.load_job(content_root)["status"], "running")

    def test_overlapping_resume_is_refused_while_lock_held(self):
        content_root = self._prepare()
        post_id = self._seed_ready_post(content_root)
        lock = content_root / "cycles" / f"{post_id}.lock"
        lock.write_text(str(os.getpid()), encoding="ascii")
        with patch.object(cycle_mod, "pid_alive", return_value=True):
            ok, message = inbox.start_generation(content_root, "resume", post_id=post_id, human_note="retry")
        self.assertFalse(ok)
        self.assertIn("already running", message)

    def test_candidate_page_shows_resume_job_error(self):
        content_root = self._prepare()
        post_id = self._seed_ready_post(content_root)
        inbox.save_job(
            content_root,
            {
                **inbox.empty_job(),
                "status": "error",
                "post_id": post_id,
                "error": "AttributeError: 'Namespace' object has no attribute 'topic'",
            },
        )
        html = inbox.render_candidate(content_root, post_id)
        self.assertIn("AttributeError", html)


class HumanRevisionHistoryTests(unittest.TestCase):
    def _prepare(self, content_root: Path) -> None:
        for name in ("drafts", "cycles", "ideas", "plans", "previews", "strategy"):
            (content_root / name).mkdir(parents=True, exist_ok=True)
        write_strategy(content_root)

    def _write_ready_draft(self, content_root: Path, post_id: str, body: str = "Body text.\n", **extra) -> str:
        digest = engine.body_hash(body if body.endswith("\n") else body + "\n")
        fields = {
            "id": post_id,
            "kind": "post",
            "status": "reviewed",
            "created": "2026-01-01",
            "body_sha256": digest,
            "review_verdict": "PASS",
            "review_body_sha256": digest,
            "editorial_verdict": "PASS",
            "editorial_body_sha256": digest,
            "critic_verdict": "PASS",
            "critic_body_sha256": digest,
            "preview_body_sha256": digest,
            "human_editorial_decision": "",
        }
        fields.update(extra)
        text = engine.render_frontmatter(fields, engine.POST_FIELD_ORDER) + f"## Body\n{body}\n"
        path = content_root / "drafts" / f"{post_id}.md"
        path.write_text(text, encoding="utf-8")
        (content_root / "previews" / f"{post_id}.html").write_text("<html></html>", encoding="utf-8")
        return digest

    def _save_cycle(self, content_root: Path, post_id: str, **kwargs) -> cycle_mod.CycleState:
        state = cycle_mod.CycleState(
            post_id=post_id,
            topic="topic",
            lane="POSITIONING",
            audience="BROAD_TECH",
            events=list(kwargs.get("events") or []),
            human_revisions=list(kwargs.get("human_revisions") or []),
            outcome=str(kwargs.get("outcome") or cycle_mod.OUTCOME_READY),
        )
        cycle_mod.save_cycle(content_root, state)
        return state

    def test_no_human_revisions_history_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._prepare(root)
            post_id = "POST-20260101-01"
            self._write_ready_draft(root, post_id)
            self._save_cycle(root, post_id)
            html = inbox.render_candidate(root, post_id)
            self.assertIn("Human-requested revisions</dt><dd>0</dd>", html)
            self.assertIn("No human-requested revisions recorded.", html)
            self.assertIn("Human decision</dt><dd>—</dd>", html)

    def test_one_structured_human_revision_marked_current(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._prepare(root)
            post_id = "POST-20260101-02"
            digest = self._write_ready_draft(root, post_id)
            record = cycle_mod._human_revision_record(
                feedback="Sharpen the opening.",
                status="applied",
                pre_body_sha256="aaa",
                post_body_sha256=digest,
                requested_at="2026-01-01T10:00:00Z",
                applied_at="2026-01-01T10:05:00Z",
                sequence=2,
                reviews={"semantic": "PASS", "editorial": "PASS", "critic": "PASS"},
            )
            self._save_cycle(root, post_id, human_revisions=[record])
            html = inbox.render_candidate(root, post_id)
            self.assertIn("Human-requested revisions</dt><dd>1</dd>", html)
            self.assertIn("Sharpen the opening.", html)
            self.assertIn("Current version</dt><dd>yes</dd>", html)
            self.assertIn("Awaiting human decision", html)

    def test_multiple_human_revisions_in_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._prepare(root)
            post_id = "POST-20260101-03"
            digest = self._write_ready_draft(root, post_id)
            records = [
                cycle_mod._human_revision_record(
                    feedback="First note.",
                    status="applied",
                    post_body_sha256="old-hash",
                    requested_at="2026-01-01T10:00:00Z",
                    sequence=1,
                ),
                cycle_mod._human_revision_record(
                    feedback="Second note.",
                    status="applied",
                    post_body_sha256=digest,
                    requested_at="2026-01-01T11:00:00Z",
                    sequence=2,
                    reviews={"semantic": "PASS", "editorial": "PASS", "critic": "PASS"},
                ),
            ]
            self._save_cycle(root, post_id, human_revisions=records)
            html = inbox.render_candidate(root, post_id)
            first = html.index("First note.")
            second = html.index("Second note.")
            self.assertLess(first, second)
            self.assertEqual(html.count("Human revision "), 2)

    def test_feedback_order_matches_requests(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._prepare(root)
            post_id = "POST-20260101-04"
            self._write_ready_draft(root, post_id)
            records = [
                cycle_mod._human_revision_record(feedback="Zebra", status="applied", requested_at="t1"),
                cycle_mod._human_revision_record(feedback="Alpha", status="applied", requested_at="t2"),
            ]
            self._save_cycle(root, post_id, human_revisions=records)
            html = inbox.render_candidate(root, post_id)
            self.assertLess(html.index("Zebra"), html.index("Alpha"))

    def test_only_matching_hash_is_current_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._prepare(root)
            post_id = "POST-20260101-05"
            digest = self._write_ready_draft(root, post_id)
            records = [
                cycle_mod._human_revision_record(
                    feedback="a",
                    status="applied",
                    post_body_sha256="not-current",
                ),
                cycle_mod._human_revision_record(
                    feedback="b",
                    status="applied",
                    post_body_sha256=digest,
                ),
            ]
            self._save_cycle(root, post_id, human_revisions=records)
            summary = inbox.candidate_summary(root, post_id)
            flags = [e["is_current_version"] for e in summary["revision_history"]["entries"]]
            self.assertEqual(flags, [False, True])

    def test_consumed_revise_vs_pending_revise(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._prepare(root)
            post_id = "POST-20260101-06"
            digest = self._write_ready_draft(root, post_id)
            self._save_cycle(
                root,
                post_id,
                events=["revision 2: human", "human revision applied: Pending feedback"],
            )
            consumed_html = inbox.render_candidate(root, post_id)
            self.assertIn("Previous human decision", consumed_html)
            self.assertIn("Awaiting human decision", consumed_html)

            post_id2 = "POST-20260101-07"
            self._write_ready_draft(root, post_id2, human_editorial_decision="REVISE")
            path = root / "drafts" / f"{post_id2}.md"
            text = path.read_text(encoding="utf-8")
            path.write_text(text + "\n## Human revision request\nStill open.\n", encoding="utf-8")
            self._save_cycle(root, post_id2, events=["revision 1: human"])
            revise_html = inbox.render_candidate(root, post_id2)
            self.assertIn("Human decision</dt><dd>REVISE</dd>", revise_html)
            self.assertNotIn("Current version</dt><dd>Awaiting human decision</dd>", revise_html)

    def test_legacy_events_truncated_without_inventing_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._prepare(root)
            post_id = "POST-20260101-08"
            self._write_ready_draft(root, post_id)
            events = ["revision 1: critic", "revision 2: human", "human revision applied: Short note"]
            self._save_cycle(root, post_id, events=events)
            state = cycle_mod.load_cycle(root, post_id)
            self.assertEqual(state.human_revisions, [])
            html = inbox.render_candidate(root, post_id)
            self.assertNotIn("truncated in the cycle log", html)
            self.assertIn("Short note", html)
            self.assertIn("not recorded", html)
            self.assertIn("Previous body text was not retained", html)
            summary = inbox.candidate_summary(root, post_id)
            entry = summary["revision_history"]["entries"][0]
            self.assertEqual(entry["feedback"], "Short note")
            self.assertFalse(entry["feedback_truncated"])
            self.assertEqual(entry["requested_at"], "")
            self.assertEqual(entry["post_body_sha256"], "")
            self.assertEqual(entry["reviews"], {})

    def test_legacy_applied_feedback_marks_truncated_only_at_storage_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._prepare(root)
            post_id = "POST-20260101-08b"
            self._write_ready_draft(root, post_id)
            note = "x" * 240
            self._save_cycle(
                root,
                post_id,
                events=["revision 1: human", f"human revision applied: {note}"],
            )
            html = inbox.render_candidate(root, post_id)
            self.assertIn("truncated in the cycle log", html)
            summary = inbox.candidate_summary(root, post_id)
            self.assertTrue(summary["revision_history"]["entries"][0]["feedback_truncated"])
            self.assertEqual(len(summary["revision_history"]["entries"][0]["feedback"]), 240)

    def test_history_survives_cycle_save_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._prepare(root)
            post_id = "POST-20260101-09"
            digest = self._write_ready_draft(root, post_id)
            record = cycle_mod._human_revision_record(
                feedback="Persist me.",
                status="applied",
                post_body_sha256=digest,
            )
            self._save_cycle(root, post_id, human_revisions=[record])
            cycle_mod.load_cycle(root, post_id)
            html = inbox.render_candidate(root, post_id)
            self.assertIn("Persist me.", html)

    def test_readiness_and_approve_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._prepare(root)
            post_id = "POST-20260101-10"
            digest = self._write_ready_draft(root, post_id)
            before = (root / "drafts" / f"{post_id}.md").read_text(encoding="utf-8")
            record = cycle_mod._human_revision_record(
                feedback="Done.",
                status="applied",
                post_body_sha256=digest,
                reviews={"semantic": "PASS", "editorial": "PASS", "critic": "PASS"},
            )
            self._save_cycle(root, post_id, human_revisions=[record])
            inbox.render_candidate(root, post_id)
            after = (root / "drafts" / f"{post_id}.md").read_text(encoding="utf-8")
            self.assertEqual(before, after)

            pending_id = "POST-20260101-11"
            self._write_ready_draft(root, pending_id, human_editorial_decision="REVISE")
            path = root / "drafts" / f"{pending_id}.md"
            path.write_text(path.read_text(encoding="utf-8") + "## Human revision request\nWait.\n", encoding="utf-8")
            ready2, reasons2 = cycle_mod.readiness_report(root, pending_id)
            self.assertFalse(ready2)
            self.assertTrue(any("human" in item.lower() for item in reasons2))

    def test_feedback_html_escaped_in_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._prepare(root)
            post_id = "POST-20260101-12"
            self._write_ready_draft(root, post_id)
            record = cycle_mod._human_revision_record(
                feedback='<script>alert("x")</script>',
                status="applied",
            )
            self._save_cycle(root, post_id, human_revisions=[record])
            html = inbox.render_candidate(root, post_id)
            self.assertNotIn("<script>alert", html)
            self.assertIn("&lt;script&gt;", html)

    def test_live_post_20261118_02_legacy_history(self):
        post_id = "POST-20261118-02"
        path = ROOT / "content" / "drafts" / f"{post_id}.md"
        if not path.is_file():
            self.skipTest("fixture absent")
        html = inbox.render_candidate(ROOT / "content", post_id)
        self.assertIn("Revision history", html)
        self.assertIn("Human-requested revisions</dt><dd>1</dd>", html)
        self.assertIn("Awaiting human decision", html)
        ready, _ = cycle_mod.readiness_report(ROOT / "content", post_id)
        self.assertTrue(ready)


class PublishIsolationTests(unittest.TestCase):
    def test_cycle_does_not_import_publish(self):
        source = (SCRIPTS / "cycle.py").read_text(encoding="utf-8")
        self.assertNotIn("linkedin_publish", source)
        source_inbox = (SCRIPTS / "inbox.py").read_text(encoding="utf-8")
        self.assertNotIn("linkedin_publish", source_inbox)


if __name__ == "__main__":
    unittest.main()
