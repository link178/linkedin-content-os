"""Headless Cursor CLI adapter for read-only agent calls. Stdlib only."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, NoReturn

ROOT = Path(__file__).resolve().parent.parent

SECRET_ENV_KEYS = frozenset(
    {
        "LINKEDIN_ACCESS_TOKEN",
        "LINKEDIN_CLIENT_SECRET",
        "LINKEDIN_REFRESH_TOKEN",
        "CURSOR_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
    }
)

SECRET_PATTERNS = (
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]+", re.IGNORECASE),
    re.compile(r"cursor_[A-Za-z0-9]+"),
    re.compile(r"AQ[A-Za-z0-9]{20,}"),
)

DEFAULT_TIMEOUT_SEC = 180


class CursorRuntimeError(Exception):
    """CLI invocation or parse failure."""


@dataclass
class AgentResult:
    text: str
    parsed: dict
    raw_envelope: dict | None


def default_agent_path() -> Path:
    override = os.environ.get("CURSOR_AGENT", "").strip()
    if override:
        candidate = Path(override)
        if candidate.is_file():
            return candidate
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        candidate = Path(local) / "cursor-agent" / "agent.cmd"
        if candidate.is_file():
            return candidate
    return Path("agent.cmd")


def sanitize_env(base: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(base or os.environ)
    for key in list(env):
        if key in SECRET_ENV_KEYS or key.upper().endswith("_TOKEN") or key.upper().endswith("_SECRET"):
            env.pop(key, None)
    return env


def redact_secrets(text: str) -> str:
    out = text
    for pattern in SECRET_PATTERNS:
        out = pattern.sub("[REDACTED]", out)
    return out


def extract_json_object(text: str) -> dict:
    """Return the first top-level JSON object found in text."""
    if not text or not text.strip():
        raise CursorRuntimeError("empty agent output")
    stripped = text.strip()
    try:
        direct = json.loads(stripped)
        if isinstance(direct, dict):
            return direct
    except json.JSONDecodeError:
        pass
    start = stripped.find("{")
    while start >= 0:
        depth = 0
        for index in range(start, len(stripped)):
            char = stripped[index]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    chunk = stripped[start : index + 1]
                    try:
                        obj = json.loads(chunk)
                    except json.JSONDecodeError:
                        break
                    if isinstance(obj, dict):
                        return obj
                    break
        start = stripped.find("{", start + 1)
    raise CursorRuntimeError("no JSON object in agent output")


def parse_agent_envelope(stdout: str) -> tuple[str, dict | None]:
    """Parse --output-format json envelope; return assistant text and envelope."""
    stdout = stdout.strip()
    if not stdout:
        raise CursorRuntimeError("empty CLI stdout")
    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise CursorRuntimeError(f"CLI output is not JSON: {exc}") from exc
    if not isinstance(envelope, dict):
        raise CursorRuntimeError("CLI envelope must be an object")
    text = ""
    for key in ("result", "text", "output", "message"):
        value = envelope.get(key)
        if isinstance(value, str) and value.strip():
            text = value
            break
    if not text:
        messages = envelope.get("messages")
        if isinstance(messages, list):
            for item in reversed(messages):
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "assistant":
                    content = item.get("content") or item.get("message")
                    if isinstance(content, str):
                        text = content
                        break
                    if isinstance(content, list):
                        parts = [
                            block.get("text", "")
                            for block in content
                            if isinstance(block, dict) and block.get("type") == "text"
                        ]
                        text = "".join(parts)
                        if text:
                            break
    if not text:
        text = json.dumps(envelope, ensure_ascii=False)
    return text, envelope


def kill_process_tree(pid: int) -> None:
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
    else:
        try:
            os.kill(pid, 9)
        except OSError:
            pass


def _reap_agent_process(process: subprocess.Popen) -> None:
    """Drain pipes after the agent has been killed so reader threads can finish."""
    try:
        process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except OSError:
            pass
        try:
            process.communicate(timeout=5)
        except (OSError, subprocess.TimeoutExpired, ValueError):
            pass
    except (OSError, ValueError):
        try:
            process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass


def _stop_agent_process(process: subprocess.Popen) -> None:
    """Stop the agent process and descendants, then reap it.

    On Windows, TerminateProcess kills only the direct child. agent.cmd
    starts further processes, and those stay alive if the parent is killed
    first. taskkill /T has to see the parent pid while it still exists.
    """
    pid = getattr(process, "pid", None)
    if pid:
        kill_process_tree(int(pid))
    _reap_agent_process(process)


def _raise_agent_timeout(
    timeout_sec: int,
    exc: subprocess.TimeoutExpired,
    process: subprocess.Popen | None = None,
) -> NoReturn:
    """Turn a timeout into CursorRuntimeError without reading a missing process attribute.

    subprocess.TimeoutExpired records cmd, timeout, and output. It has no process.
    """
    target = process if process is not None else getattr(exc, "process", None)
    if target is not None:
        _stop_agent_process(target)
    raise CursorRuntimeError(f"agent timed out after {timeout_sec}s") from exc


def _decode_cli_bytes(payload: bytes, label: str) -> str:
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CursorRuntimeError(f"{label} is not valid UTF-8: {exc}") from exc


def _run_agent(
    args: list[str],
    env: dict[str, str],
    timeout_sec: int,
) -> subprocess.CompletedProcess:
    """Run the agent and return its output.

    Popen is required so a timeout can kill the process tree. subprocess.run
    kills only the direct child before raising TimeoutExpired, and that
    exception does not keep the process handle.
    """
    process = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    try:
        stdout_b, stderr_b = process.communicate(timeout=timeout_sec)
    except subprocess.TimeoutExpired as exc:
        _raise_agent_timeout(timeout_sec, exc, process)
    stdout = _decode_cli_bytes(stdout_b or b"", "CLI output")
    stderr = _decode_cli_bytes(stderr_b or b"", "CLI stderr")
    returncode = process.returncode
    if returncode is None:
        returncode = process.wait()
    return subprocess.CompletedProcess(process.args, returncode, stdout, stderr)


def verify_cli_logged_in(agent_path: Path, env: dict[str, str]) -> None:
    if os.environ.get("CURSOR_RUNTIME_SKIP_AUTH") == "1":
        return
    completed = subprocess.run(
        [str(agent_path), "status"],
        capture_output=True,
        env=env,
        timeout=60,
    )
    stdout = _decode_cli_bytes(completed.stdout or b"", "CLI status output")
    stderr = _decode_cli_bytes(completed.stderr or b"", "CLI status stderr")
    combined = stdout + stderr
    if completed.returncode != 0 or "Logged in" not in combined:
        raise CursorRuntimeError("Cursor CLI is not logged in; run agent login")


def build_snapshot(
    repo_root: Path,
    extra_paths: list[Path] | None = None,
) -> Path:
    """Copy a sanitized workspace tree for --workspace."""
    snapshot = Path(tempfile.mkdtemp(prefix="li-cycle-snap-"))
    copy_dirs = ("knowledge", "docs", "research", "config")
    for name in copy_dirs:
        src = repo_root / name
        if src.is_dir():
            shutil.copytree(src, snapshot / name, dirs_exist_ok=True)
    skills_src = repo_root / ".cursor" / "skills"
    if skills_src.is_dir():
        dest_skills = snapshot / ".cursor" / "skills"
        dest_skills.mkdir(parents=True, exist_ok=True)
        shutil.copytree(skills_src, dest_skills, dirs_exist_ok=True)
    for rel in extra_paths or []:
        src = rel if rel.is_absolute() else repo_root / rel
        if not src.is_file():
            continue
        dest = snapshot / rel.as_posix().lstrip("/") if rel.is_absolute() else snapshot / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
    return snapshot


def remove_snapshot(path: Path | None) -> None:
    if path and path.is_dir():
        shutil.rmtree(path, ignore_errors=True)


class CursorRuntime:
    """Invoke Cursor agent in read-only ask mode."""

    def __init__(
        self,
        repo_root: Path | None = None,
        agent_path: Path | None = None,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
        invoke: Callable[..., subprocess.CompletedProcess] | None = None,
    ) -> None:
        self.repo_root = repo_root or ROOT
        self.agent_path = agent_path or default_agent_path()
        self.timeout_sec = timeout_sec
        self._invoke = invoke

    def ask(
        self,
        prompt: str,
        *,
        model: str | None = None,
        extra_paths: list[Path] | None = None,
        validate: Callable[[dict], None] | None = None,
        timeout_sec: int | None = None,
    ) -> AgentResult:
        timeout = int(timeout_sec or self.timeout_sec)
        if not self.agent_path.is_file():
            raise CursorRuntimeError(f"Cursor agent not found: {self.agent_path}")
        env = sanitize_env()
        verify_cli_logged_in(self.agent_path, env)
        snapshot = build_snapshot(self.repo_root, extra_paths=extra_paths)
        try:
            args = [
                str(self.agent_path),
                "-p",
                "--mode",
                "ask",
                "--output-format",
                "json",
                "--trust",
                "--workspace",
                str(snapshot),
            ]
            if model:
                args.extend(["--model", model])
            prompt_path = snapshot / "_cycle_prompt.txt"
            prompt_path.write_text(prompt, encoding="utf-8")
            args.append(
                "Read _cycle_prompt.txt in this workspace and follow it exactly. "
                "Return one JSON object as specified there."
            )
            if self._invoke is not None:
                try:
                    completed = self._invoke(args, env=env, timeout=timeout)
                except subprocess.TimeoutExpired as exc:
                    _raise_agent_timeout(timeout, exc)
            else:
                completed = _run_agent(args, env, timeout)
            if completed.returncode != 0:
                err = redact_secrets((completed.stderr or completed.stdout or "").strip())
                raise CursorRuntimeError(f"agent exit {completed.returncode}: {err or 'no output'}")
            combined_out = (completed.stdout or "").strip() or (completed.stderr or "").strip()
            text, envelope = parse_agent_envelope(combined_out)
            text = redact_secrets(text)
            parsed = extract_json_object(text)
            if validate:
                validate(parsed)
            return AgentResult(text=text, parsed=parsed, raw_envelope=envelope)
        finally:
            remove_snapshot(snapshot)


def require_keys(*keys: str) -> Callable[[dict], None]:
    def _validate(data: dict) -> None:
        missing = [key for key in keys if key not in data]
        if missing:
            raise CursorRuntimeError(f"response missing keys: {', '.join(missing)}")

    return _validate
