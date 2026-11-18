"""Windows Task Scheduler entry point: run cycle.py under guard with operational records. Stdlib only."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cursor_runtime as cursor_runtime_mod  # noqa: E402
import cycle as cycle_mod  # noqa: E402
import cycle_guard as cycle_guard_mod  # noqa: E402
import inbox as inbox_mod  # noqa: E402
import ops_runs as ops_mod  # noqa: E402

DEFAULT_CONFIG = ROOT / "config" / "scheduler.json"


def load_config(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def resolve_paths(args: argparse.Namespace) -> tuple[Path, Path, Path]:
    repo_root = Path(args.repo_root).resolve()
    content_root = Path(args.content_root).resolve()
    config_path = Path(args.config).resolve() if args.config else DEFAULT_CONFIG.resolve()
    return repo_root, content_root, config_path


def build_cycle_command(python_exe: Path, repo_root: Path, content_root: Path) -> list[str]:
    return [
        str(python_exe),
        str(repo_root / "scripts" / "cycle.py"),
        "--content-root",
        str(content_root),
        "--repo-root",
        str(repo_root),
        "run",
    ]


def new_running_record(run_id: str, log_path: Path, trigger: str) -> dict:
    now = ops_mod.utc_now()
    local_now = datetime.now().astimezone().isoformat(timespec="seconds")
    return {
        "run_id": run_id,
        "trigger": trigger,
        "started_at": now,
        "local_started": local_now,
        "ended_at": "",
        "duration_sec": 0,
        "process_result": ops_mod.STATUS_RUNNING,
        "exit_code": None,
        "outcome": "",
        "classification": ops_mod.STATUS_RUNNING,
        "post_id": "",
        "error": "",
        "log": str(log_path),
        "attempt": 1,
        "pid": os.getpid(),
    }


def finalize_record(
    content_root: Path,
    run_id: str,
    *,
    exit_code: int,
    payload: dict | None,
    log_text: str,
    process_result: str = ops_mod.STATUS_COMPLETE,
    error: str = "",
) -> dict:
    ended = ops_mod.utc_now()
    payload = payload or {}
    outcome = str(payload.get("outcome") or "")
    post_id = str(payload.get("post_id") or "")
    err = error or str(payload.get("error") or "")
    err = cursor_runtime_mod.redact_secrets(err)
    classification = ops_mod.classify(
        outcome=outcome,
        post_id=post_id,
        exit_code=exit_code,
        process_result=process_result,
    )
    # Re-read running record timing from log append order — use wall clock from env
    duration = 0
    record = {
        "run_id": run_id,
        "trigger": ops_mod.TRIGGER_SCHEDULED,
        "ended_at": ended,
        "duration_sec": duration,
        "process_result": process_result,
        "exit_code": exit_code,
        "outcome": outcome,
        "classification": classification,
        "post_id": post_id,
        "error": err[:2000],
        "log": "",
        "attempt": 1,
        "pid": os.getpid(),
    }
    return record


def run_cycle_subprocess(
    repo_root: Path,
    content_root: Path,
    log_path: Path,
    max_duration_sec: int,
) -> tuple[int, str, dict | None]:
    python_exe = Path(sys.executable).resolve()
    cmd = build_cycle_command(python_exe, repo_root, content_root)
    env = os.environ.copy()
    env["CONTENT_ROOT"] = str(content_root)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log_handle:
        proc = subprocess.Popen(
            cmd,
            cwd=str(repo_root),
            env=env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
        )
        try:
            proc.wait(timeout=max_duration_sec if max_duration_sec > 0 else None)
        except subprocess.TimeoutExpired:
            cursor_runtime_mod.kill_process_tree(proc.pid)
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                pass
            text = log_path.read_text(encoding="utf-8", errors="replace")
            text = cursor_runtime_mod.redact_secrets(text)
            log_path.write_text(text, encoding="utf-8")
            return -1, text, {"outcome": "TIMEOUT", "error": f"exceeded {max_duration_sec}s limit"}
    text = log_path.read_text(encoding="utf-8", errors="replace")
    text = cursor_runtime_mod.redact_secrets(text)
    log_path.write_text(text, encoding="utf-8")
    payload = inbox_mod.extract_last_json_object(text)
    return proc.returncode if proc.returncode is not None else 1, text, payload


def run_test_mode(content_root: Path, hold_seconds: int) -> int:
    ops_mod.reconcile_interrupted(content_root)
    guard = cycle_guard_mod.CycleGuard(content_root)
    ok, err = guard.acquire()
    if not ok:
        run_id = ops_mod.new_run_id()
        log_path = ops_mod.runs_dir(content_root) / f"{run_id}.log"
        record = new_running_record(run_id, log_path, ops_mod.TRIGGER_SCHEDULED)
        record.update(
            {
                "ended_at": ops_mod.utc_now(),
                "process_result": ops_mod.STATUS_COMPLETE,
                "exit_code": 1,
                "outcome": cycle_guard_mod.SKIPPED_OUTCOME,
                "classification": cycle_guard_mod.SKIPPED_OUTCOME,
                "error": err,
                "duration_sec": 0,
            }
        )
        ops_mod.append_record(content_root, record)
        print(json.dumps({"outcome": cycle_guard_mod.SKIPPED_OUTCOME, "error": err}, ensure_ascii=False))
        return 1
    try:
        if hold_seconds > 0:
            time.sleep(hold_seconds)
    finally:
        guard.release()
    run_id = ops_mod.new_run_id()
    log_path = ops_mod.runs_dir(content_root) / f"{run_id}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text('{"outcome":"TEST_OK"}\n', encoding="utf-8")
    started = ops_mod.utc_now()
    record = {
        "run_id": run_id,
        "trigger": ops_mod.TRIGGER_SCHEDULED,
        "started_at": started,
        "local_started": datetime.now().astimezone().isoformat(timespec="seconds"),
        "ended_at": ops_mod.utc_now(),
        "duration_sec": hold_seconds,
        "process_result": ops_mod.STATUS_COMPLETE,
        "exit_code": 0,
        "outcome": "TEST_OK",
        "classification": "TEST_OK",
        "post_id": "",
        "error": "",
        "log": str(log_path),
        "attempt": 1,
        "pid": os.getpid(),
    }
    ops_mod.append_record(content_root, record)
    print(json.dumps({"outcome": "TEST_OK", "run_id": run_id}, ensure_ascii=False))
    return 0


def cmd_main(args: argparse.Namespace) -> int:
    repo_root, content_root, config_path = resolve_paths(args)
    os.chdir(repo_root)
    config = load_config(config_path)
    max_duration = int(config.get("max_duration_sec") or args.max_duration_sec or 5400)
    max_runs = int(config.get("max_runs_per_day") or 1)

    if args.test_mode:
        return run_test_mode(content_root, int(args.test_hold_seconds or 0))

    ops_mod.reconcile_interrupted(content_root)
    local_day = datetime.now().astimezone().strftime("%Y-%m-%d")
    if max_runs > 0 and ops_mod.count_scheduled_today(content_root, local_day) >= max_runs:
        run_id = ops_mod.new_run_id()
        record = {
            "run_id": run_id,
            "trigger": ops_mod.TRIGGER_SCHEDULED,
            "started_at": ops_mod.utc_now(),
            "local_started": datetime.now().astimezone().isoformat(timespec="seconds"),
            "ended_at": ops_mod.utc_now(),
            "duration_sec": 0,
            "process_result": ops_mod.STATUS_COMPLETE,
            "exit_code": 0,
            "outcome": "DAILY_LIMIT_REACHED",
            "classification": "DAILY_LIMIT_REACHED",
            "post_id": "",
            "error": f"max_runs_per_day={max_runs} already reached for {local_day}",
            "log": "",
            "attempt": 1,
            "pid": os.getpid(),
        }
        ops_mod.append_record(content_root, record)
        print(json.dumps({"outcome": "DAILY_LIMIT_REACHED", "error": record["error"]}, ensure_ascii=False))
        return 0

    run_id = ops_mod.new_run_id()
    log_path = ops_mod.runs_dir(content_root) / f"{run_id}.log"
    started_mono = time.monotonic()
    running = new_running_record(run_id, log_path, ops_mod.TRIGGER_SCHEDULED)
    ops_mod.append_record(content_root, running)

    exit_code, _text, payload = run_cycle_subprocess(repo_root, content_root, log_path, max_duration)
    duration = int(time.monotonic() - started_mono)
    if payload is None and exit_code == -1:
        process_result = "TIMEOUT"
    elif payload is None:
        process_result = "INVALID_OUTPUT"
        payload = {"outcome": "INVALID_OUTPUT", "error": "no JSON status in cycle log"}
    else:
        process_result = ops_mod.STATUS_COMPLETE

    final = finalize_record(
        content_root,
        run_id,
        exit_code=exit_code if exit_code >= 0 else 1,
        payload=payload,
        log_text=_text,
        process_result=process_result,
    )
    final["started_at"] = running["started_at"]
    final["local_started"] = running.get("local_started", "")
    final["duration_sec"] = duration
    final["log"] = str(log_path)
    ops_mod.append_record(content_root, final)

    print(json.dumps({"run_id": run_id, "outcome": final["outcome"], "classification": final["classification"]}, ensure_ascii=False))
    if final["outcome"] == cycle_mod.OUTCOME_READY:
        return 0
    return 1 if exit_code != 0 else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Scheduled editorial cycle runner.")
    parser.add_argument("--repo-root", default=str(ROOT))
    parser.add_argument("--content-root", default=str(ROOT / "content"))
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--max-duration-sec", type=int, default=0)
    parser.add_argument("--test-mode", action="store_true", help="lock probe only; no cycle.py")
    parser.add_argument("--test-hold-seconds", type=int, default=0)
    parser.set_defaults(func=cmd_main)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())