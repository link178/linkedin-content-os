"""Operational run records for scheduled and cross-trigger visibility. Stdlib only."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cycle as cycle_mod
import cycle_guard as cycle_guard_mod

OPS_RUNS_NAME = "ops-runs.jsonl"
RUNS_SUBDIR = "runs"
TRIGGER_SCHEDULED = "SCHEDULED"
STATUS_RUNNING = "running"
STATUS_COMPLETE = "complete"

EDITORIAL_TERMINAL = frozenset(
    {
        "READY_FOR_HUMAN",
        "NOT_READY",
        "NO_EDITORIAL_OPPORTUNITY",
        "REVISION_EXHAUSTED",
        "SEMANTIC_BLOCKED",
        "SEMANTIC_FAILURE",
        "STRATEGY_MISALIGNED",
        "PLAN_MISMATCH",
        "EDITORIAL_FAILURE",
        "CRITIC_FAILURE",
        "CRITIC_REJECTION",
        "VISUAL_FAILURE",
        "PREVIEW_FAILURE",
        "REVISION_UNCHANGED",
        "DROPPED",
    }
)

CLASSIFICATION_LABELS = {
    "READY_FOR_HUMAN": "Candidate ready for human review.",
    "NOT_READY": "Candidate created but not ready for review.",
    "NO_EDITORIAL_OPPORTUNITY": "No editorial opportunity qualified.",
    "REVISION_EXHAUSTED": "Quality gates exhausted revisions.",
    "DISCOVERY_FAILURE": "Discovery or research failed.",
    "SKIPPED_ALREADY_RUNNING": "Skipped because another cycle is already running.",
    "RUNTIME_FAILURE": "Technical execution failure.",
    "TIMEOUT": "Cycle exceeded the scheduled time limit.",
    "INTERRUPTED": "Run interrupted before completion.",
    "INVALID_OUTPUT": "Cycle ended without a usable status report.",
    "TEST_OK": "Scheduler test completed without running the pipeline.",
    "DAILY_LIMIT_REACHED": "Daily scheduled run limit already reached.",
    "EDITORIAL_STOP": "Editorial or quality gate stop.",
    "TECHNICAL_FAILURE": "Technical execution failure.",
}


def ops_path(content_root: Path) -> Path:
    return content_root / cycle_mod.CYCLES_DIR / OPS_RUNS_NAME


def runs_dir(content_root: Path) -> Path:
    return content_root / cycle_mod.CYCLES_DIR / RUNS_SUBDIR


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"RUN-{stamp}-{os.getpid()}"


def classify(
    *,
    outcome: str,
    post_id: str,
    exit_code: int,
    process_result: str,
) -> str:
    outcome = (outcome or "").strip()
    if process_result == STATUS_RUNNING:
        return STATUS_RUNNING
    if process_result == "INTERRUPTED":
        return "INTERRUPTED"
    if process_result == "TIMEOUT":
        return "TIMEOUT"
    if process_result == "TEST_OK":
        return "TEST_OK"
    if process_result == "INVALID_OUTPUT":
        return "INVALID_OUTPUT"
    if outcome == cycle_guard_mod.SKIPPED_OUTCOME:
        return cycle_guard_mod.SKIPPED_OUTCOME
    if outcome in CLASSIFICATION_LABELS:
        return outcome
    if outcome == "DISCOVERY_FAILURE" or outcome.startswith("DISCOVERY"):
        return "DISCOVERY_FAILURE"
    if outcome in EDITORIAL_TERMINAL or post_id:
        if outcome:
            return outcome
        return "NOT_READY" if post_id else "EDITORIAL_STOP"
    if outcome in {"RUNTIME_FAILURE"} or (exit_code != 0 and not outcome):
        return "RUNTIME_FAILURE"
    if outcome:
        return outcome
    return "TECHNICAL_FAILURE"


def human_label(classification: str) -> str:
    if classification in CLASSIFICATION_LABELS:
        return CLASSIFICATION_LABELS[classification]
    if classification in EDITORIAL_TERMINAL:
        return f"Editorial outcome: {classification}."
    return f"Run ended with {classification}."


def generation_job_summary(job: dict) -> str:
    """Dashboard text for inbox-job.json without treating editorial stops as generic failures."""
    status = str(job.get("status") or "idle")
    post_id = str(job.get("post_id") or "")
    message = str(job.get("message") or "")
    error = str(job.get("error") or "")
    if status == "running":
        command = job.get("command") or "run"
        text = f"Generation running ({command})."
        if post_id:
            text += f" Candidate {post_id}."
        return text
    if status == "success":
        outcome = message or "completed"
        label = human_label(classify(outcome=outcome, post_id=post_id, exit_code=1, process_result=STATUS_COMPLETE))
        if post_id:
            return f"Generation finished for {post_id}. {label}"
        return f"Generation finished. {label}"
    if status == "error":
        outcome = message or error or "failed"
        label = human_label(classify(outcome=outcome, post_id=post_id, exit_code=1, process_result=STATUS_COMPLETE))
        if outcome in CLASSIFICATION_LABELS or outcome in EDITORIAL_TERMINAL or outcome == "NO_EDITORIAL_OPPORTUNITY":
            return label
        if error and error != outcome:
            return f"{label} {error}".strip()
        return label if label else "Technical execution failure."
    return "No generation running."


def append_record(content_root: Path, record: dict[str, Any]) -> None:
    path = ops_path(content_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False) + "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)


def _dedupe_run_ids(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the latest record for each run_id (running row replaced by completion)."""
    order: list[str] = []
    by_id: dict[str, dict[str, Any]] = {}
    for record in rows:
        run_id = str(record.get("run_id") or "")
        if not run_id:
            continue
        if run_id not in by_id:
            order.append(run_id)
        by_id[run_id] = record
    return [by_id[run_id] for run_id in order]


def load_records(content_root: Path, *, limit: int = 30) -> list[dict[str, Any]]:
    path = ops_path(content_root)
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                rows.append(obj)
    except OSError:
        return []
    rows = _dedupe_run_ids(rows)
    if limit > 0 and len(rows) > limit:
        rows = rows[-limit:]
    rows.reverse()
    return rows


def reconcile_interrupted(content_root: Path) -> None:
    """Close stale running records when the pid is gone and the global lock is free."""
    path = ops_path(content_root)
    if not path.is_file():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    changed = False
    out: list[str] = []
    busy = cycle_guard_mod.is_busy(content_root)
    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            out.append(line)
            continue
        if record.get("process_result") != STATUS_RUNNING:
            out.append(line)
            continue
        pid = int(record.get("pid") or 0)
        if cycle_mod.pid_alive(pid) or busy:
            out.append(line)
            continue
        record["process_result"] = "INTERRUPTED"
        record["classification"] = "INTERRUPTED"
        record["ended_at"] = utc_now()
        record["error"] = record.get("error") or "process ended before recording completion"
        started = record.get("started_at") or ""
        if started:
            try:
                start = datetime.strptime(started, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                end = datetime.now(timezone.utc)
                record["duration_sec"] = max(0, int((end - start).total_seconds()))
            except ValueError:
                pass
        out.append(json.dumps(record, ensure_ascii=False))
        changed = True
    if changed:
        engine_write_atomic(path, "\n".join(out) + ("\n" if out else ""))


def engine_write_atomic(path: Path, text: str) -> None:
    import content_engine as engine  # local import to avoid cycle at import time

    engine.write_atomic(path, text)


def _scheduled_local_day(record: dict[str, Any]) -> str:
    local_started = str(record.get("local_started") or "")
    day = local_started.split("T", 1)[0] if local_started else ""
    if day:
        return day
    started = str(record.get("started_at") or "")
    return started[:10] if len(started) >= 10 else ""


def count_scheduled_today(content_root: Path, local_day: str | None = None) -> int:
    """Count scheduled executions on the local day that consumed a daily slot.

    Reconcile can leave an INTERRUPTED row and a later completion for the same
    run_id. Those rows are one execution. The latest row wins. A still-running
    row and a SKIPPED_ALREADY_RUNNING row do not consume a slot.
    """
    if local_day is None:
        local_day = datetime.now().astimezone().strftime("%Y-%m-%d")
    path = ops_path(content_root)
    if not path.is_file():
        return 0
    latest: dict[str, dict[str, Any]] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            if not isinstance(record, dict):
                continue
            if record.get("trigger") != TRIGGER_SCHEDULED:
                continue
            run_id = str(record.get("run_id") or "")
            if not run_id:
                continue
            latest[run_id] = record
    except (OSError, json.JSONDecodeError):
        return 0
    count = 0
    for record in latest.values():
        if record.get("classification") == cycle_guard_mod.SKIPPED_OUTCOME:
            continue
        if record.get("process_result") == STATUS_RUNNING:
            continue
        if _scheduled_local_day(record) == local_day:
            count += 1
    return count