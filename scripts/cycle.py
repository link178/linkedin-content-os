"""Headless content cycle orchestrator. Stdlib only."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import content_engine as engine  # noqa: E402
import critic as critic_mod  # noqa: E402
import cycle_guard as cycle_guard_mod  # noqa: E402
import cursor_runtime as cursor_runtime_mod  # noqa: E402
import editorial as editorial_mod  # noqa: E402
import editorial_discovery as discovery_mod  # noqa: E402
import preview as preview_mod  # noqa: E402
import strategy_check as strategy_mod  # noqa: E402
import visual as visual_mod  # noqa: E402

MAX_REVISION_CYCLES = 2
CYCLES_DIR = "cycles"
ACTIVE_CONTENT_ROOT: Path | None = None
LOCK_SUFFIX = ".lock"
OUTCOME_READY = "READY_FOR_HUMAN"
HUMAN_REVISION_HEADING = "## Human revision request"


class LLMRuntime(Protocol):
    def ask(self, prompt: str, **kwargs) -> cursor_runtime_mod.AgentResult: ...


@dataclass
class CycleState:
    post_id: str
    topic: str
    lane: str
    audience: str
    stage: str = "init"
    revision_count: int = 0
    outcome: str = ""
    error: str = ""
    pid: int = 0
    started_at: str = ""
    updated_at: str = ""
    events: list[str] = field(default_factory=list)
    human_revisions: list[dict] = field(default_factory=list)


HUMAN_REV_EVENT_RE = re.compile(r"^revision (\d+): human$")
AUTO_REV_EVENT_RE = re.compile(r"^revision (\d+): (semantic|editorial|critic|alignment)$")
HUMAN_APPLIED_PREFIX = "human revision applied: "


def utc_now() -> str:
    return engine.utc_timestamp()


def cycle_path(content_root: Path, post_id: str) -> Path:
    return content_root / CYCLES_DIR / f"{post_id}.yaml"


def lock_path(content_root: Path, post_id: str) -> Path:
    return content_root / CYCLES_DIR / f"{post_id}{LOCK_SUFFIX}"


def _json_string(raw: str) -> str:
    """Decode a json.dumps scalar. Older logs store \\u escapes inside quotes."""
    if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return raw.strip('"')
        if isinstance(value, str):
            return value
    return raw.strip('"')


def load_cycle(content_root: Path, post_id: str) -> CycleState | None:
    path = cycle_path(content_root, post_id)
    if not path.is_file():
        return None
    data: dict = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, raw = line.split(":", 1)
        key = key.strip()
        raw = raw.strip()
        if key == "revision_count":
            data[key] = int(raw or "0")
        elif key == "pid":
            data[key] = int(raw or "0")
        elif key == "events":
            data[key] = json.loads(raw) if raw else []
        elif key == "human_revisions":
            data[key] = json.loads(raw) if raw else []
        elif key in {"topic", "outcome", "error"}:
            data[key] = _json_string(raw)
        else:
            data[key] = raw.strip('"')
    if "post_id" not in data:
        return None
    return CycleState(
        post_id=str(data.get("post_id", "")),
        topic=str(data.get("topic", "")),
        lane=str(data.get("lane", "")),
        audience=str(data.get("audience", "")),
        stage=str(data.get("stage", "")),
        revision_count=int(data.get("revision_count", 0)),
        outcome=str(data.get("outcome", "")),
        error=str(data.get("error", "")),
        pid=int(data.get("pid", 0)),
        started_at=str(data.get("started_at", "")),
        updated_at=str(data.get("updated_at", "")),
        events=list(data.get("events", [])),
        human_revisions=list(data.get("human_revisions", [])),
    )


def save_cycle(content_root: Path, state: CycleState) -> None:
    folder = content_root / CYCLES_DIR
    folder.mkdir(parents=True, exist_ok=True)
    state.updated_at = utc_now()
    lines = [
        f"post_id: {state.post_id}",
        f"topic: {json.dumps(state.topic)}",
        f"lane: {state.lane}",
        f"audience: {state.audience}",
        f"stage: {state.stage}",
        f"revision_count: {state.revision_count}",
        f"outcome: {json.dumps(state.outcome)}",
        f"error: {json.dumps(state.error)}",
        f"pid: {state.pid}",
        f"started_at: {state.started_at}",
        f"updated_at: {state.updated_at}",
        f"events: {json.dumps(state.events, ensure_ascii=True)}",
        f"human_revisions: {json.dumps(state.human_revisions, ensure_ascii=True)}",
    ]
    engine.write_atomic(cycle_path(content_root, state.post_id), "\n".join(lines) + "\n")


def _human_revision_record(
    *,
    feedback: str,
    status: str,
    pre_body_sha256: str = "",
    post_body_sha256: str = "",
    requested_at: str = "",
    applied_at: str = "",
    sequence: int = 0,
    reviews: dict | None = None,
    feedback_truncated: bool = False,
) -> dict:
    return {
        "decision": "REVISE",
        "feedback": feedback,
        "status": status,
        "pre_body_sha256": pre_body_sha256,
        "post_body_sha256": post_body_sha256,
        "requested_at": requested_at or "",
        "applied_at": applied_at or "",
        "sequence": sequence,
        "reviews": dict(reviews or {}),
        "feedback_truncated": feedback_truncated,
    }


def append_pending_human_revision(
    state: CycleState,
    feedback: str,
    pre_body_sha256: str,
    *,
    requested_at: str | None = None,
) -> None:
    note = feedback.strip()
    if state.human_revisions:
        last = state.human_revisions[-1]
        if last.get("status") == "pending" and last.get("feedback", "").strip() == note:
            if pre_body_sha256 and not last.get("pre_body_sha256"):
                last["pre_body_sha256"] = pre_body_sha256
            if requested_at and not last.get("requested_at"):
                last["requested_at"] = requested_at
            return
    state.human_revisions.append(
        _human_revision_record(
            feedback=note,
            status="pending",
            pre_body_sha256=pre_body_sha256,
            requested_at=requested_at or utc_now(),
        )
    )


def _last_pending_human_revision(state: CycleState) -> dict | None:
    for record in reversed(state.human_revisions):
        if record.get("status") == "pending":
            return record
    return None


def mark_pending_human_revision_status(state: CycleState, status: str) -> None:
    record = _last_pending_human_revision(state)
    if record is not None:
        record["status"] = status


def complete_pending_human_revision(
    state: CycleState,
    *,
    post_body_sha256: str,
    reviews: dict,
    applied_at: str | None = None,
    sequence: int = 0,
) -> None:
    record = _last_pending_human_revision(state)
    if record is None:
        return
    record["status"] = "applied"
    record["post_body_sha256"] = post_body_sha256
    record["reviews"] = dict(reviews)
    record["applied_at"] = applied_at or utc_now()
    if sequence:
        record["sequence"] = sequence


def human_revisions_from_events(events: list[str]) -> list[dict]:
    entries: list[dict] = []
    for event in events:
        human_match = HUMAN_REV_EVENT_RE.match(event)
        if human_match:
            entries.append(
                _human_revision_record(
                    feedback="",
                    status="pending",
                    sequence=int(human_match.group(1)),
                )
            )
            continue
        if event == "human revision unchanged":
            if entries and entries[-1]["status"] == "pending":
                entries[-1]["status"] = "unchanged"
            continue
        if event.startswith(HUMAN_APPLIED_PREFIX):
            note = event[len(HUMAN_APPLIED_PREFIX) :]
            for entry in reversed(entries):
                if entry["status"] == "pending":
                    entry["status"] = "applied"
                    entry["feedback"] = note
                    # The event stores at most 240 characters. Shorter text was
                    # stored whole; do not claim truncation without that evidence.
                    entry["feedback_truncated"] = len(note) >= 240
                    break
    return entries


def automatic_revision_labels(events: list[str]) -> list[str]:
    labels: list[str] = []
    for event in events:
        match = AUTO_REV_EVENT_RE.match(event)
        if match:
            labels.append(f"{match.group(1)}: {match.group(2)}")
    return labels


def human_revision_history_view(
    state: CycleState | None,
    post_fields: dict,
    sections: dict,
) -> dict:
    events = list(state.events) if state else []
    body_sha = str(post_fields.get("body_sha256") or "")
    structured = list(state.human_revisions) if state else []
    if structured:
        entries = [dict(item) for item in structured]
    else:
        entries = human_revisions_from_events(events)
    auto_labels = automatic_revision_labels(events)
    human_requested_count = sum(1 for entry in entries if entry.get("decision") == "REVISE")
    applied_entries = [e for e in entries if e.get("status") == "applied"]
    heading = HUMAN_REVISION_HEADING.lstrip("#").strip()
    decision = str(post_fields.get("human_editorial_decision") or "")
    pending_section = sections.get(heading, "").strip()
    pending_decision = decision in {"REVISE", "DROP"} or bool(pending_section)
    consumed_latest = (
        human_requested_count > 0
        and not pending_decision
        and decision == ""
        and bool(applied_entries)
        and entries[-1].get("status") == "applied"
    )
    last_applied = applied_entries[-1] if applied_entries else None
    follows_human = False
    if last_applied is not None:
        post_hash = str(last_applied.get("post_body_sha256") or "")
        if post_hash and body_sha:
            follows_human = post_hash == body_sha
        elif consumed_latest:
            follows_human = True
    for entry in entries:
        post_hash = str(entry.get("post_body_sha256") or "")
        entry["is_current_version"] = bool(body_sha and post_hash and post_hash == body_sha)
    if entries and body_sha and not any(e.get("is_current_version") for e in entries):
        if consumed_latest and applied_entries:
            entries[-1]["is_current_version"] = True
    return {
        "human_requested_revision_count": human_requested_count,
        "automatic_revisions": auto_labels,
        "entries": entries,
        "follows_human_revision": follows_human,
        "pending_human_decision": pending_decision,
        "consumed_human_revise": consumed_latest,
        "human_decision_label": decision,
        "previous_human_decision": "REVISE" if consumed_latest else "",
        "current_version_status": "Awaiting human decision" if consumed_latest else "",
    }


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        completed = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}"],
            capture_output=True,
            text=True,
            check=False,
        )
        return str(pid) in (completed.stdout or "")
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def acquire_lock(content_root: Path, post_id: str, pid: int) -> tuple[bool, str]:
    path = lock_path(content_root, post_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            recorded = int(path.read_text(encoding="utf-8").strip() or "0")
        except ValueError:
            recorded = 0
        if pid_alive(recorded):
            return False, f"cycle already running for {post_id} (pid {recorded})"
        path.unlink(missing_ok=True)
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(pid).encode("ascii"))
        os.close(fd)
        return True, ""
    except FileExistsError:
        return False, f"cycle lock held for {post_id}"


def release_lock(content_root: Path, post_id: str) -> None:
    lock_path(content_root, post_id).unlink(missing_ok=True)


def latest_strategy(content_root: Path) -> tuple[Path | None, list[dict]]:
    folder = content_root / "strategy"
    if not folder.is_dir():
        return None, []
    files = sorted(folder.glob("STRATEGY-*.md"), reverse=True)
    for path in files:
        text = path.read_text(encoding="utf-8")
        fields, rest = engine.parse_frontmatter(text)
        if fields.get("status") != "proposed":
            continue
        sections = engine.section_map(rest)
        try:
            topics = engine.parse_records(sections.get("Topics", ""), strategy_mod.STRATEGY_TOPIC_FIELDS)
        except ValueError:
            continue
        if topics:
            return path, topics
    return None, []


def used_topics(content_root: Path) -> set[str]:
    used: set[str] = set()
    for path in engine.iter_artifact_paths(content_root):
        if path.stem.startswith("POST-"):
            try:
                fields, _ = engine.parse_frontmatter(path.read_text(encoding="utf-8"))
            except ValueError:
                continue
            idea = fields.get("idea", "")
            if idea:
                idea_path = content_root / "ideas" / f"{idea}.md"
                if idea_path.is_file():
                    idea_fields, idea_rest = engine.parse_frontmatter(idea_path.read_text(encoding="utf-8"))
                    insight = engine.section_map(idea_rest).get("Insight", "").strip()
                    if insight:
                        used.add(insight.casefold())
    for path in (content_root / CYCLES_DIR).glob("*.yaml") if (content_root / CYCLES_DIR).is_dir() else []:
        state = load_cycle(content_root, path.stem)
        if state and state.topic:
            used.add(state.topic.casefold())
    return used


def _selection_claims() -> list[dict]:
    try:
        return engine.load_claims()
    except RuntimeError as exc:
        raise CycleError("RUNTIME_FAILURE", str(exc)) from exc


def recent_topic_records(content_root: Path, topics: list[dict]) -> list[dict]:
    """Last few consumed topics, oldest first. Used for distinctiveness, not a ban list."""
    dated: list[tuple[str, dict]] = []
    by_title = {str(topic.get("topic", "")).casefold(): topic for topic in topics}
    cycles = content_root / CYCLES_DIR
    if cycles.is_dir():
        for path in cycles.glob("POST-*.yaml"):
            state = load_cycle(content_root, path.stem)
            if not state or not state.topic:
                continue
            record = dict(by_title.get(state.topic.casefold(), {"topic": state.topic, "why_this_author": ""}))
            record["lane"] = record.get("lane") or state.lane
            dated.append((state.started_at or "", record))
    for folder, stamp_key in (("published", "published_at"), ("approved", "approved_at")):
        directory = content_root / folder
        if not directory.is_dir():
            continue
        for path in directory.glob("POST-*.md"):
            try:
                fields, _rest = engine.parse_frontmatter(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            idea_id = str(fields.get("idea") or "")
            record = {"topic": "", "why_this_author": "", "lane": "", "audience": ""}
            if idea_id:
                idea_path = content_root / "ideas" / f"{idea_id}.md"
                if idea_path.is_file():
                    try:
                        idea_fields, idea_rest = engine.parse_frontmatter(idea_path.read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        idea_fields, idea_rest = {}, ""
                    insight = engine.section_map(idea_rest).get("Insight", "").strip()
                    record = {
                        "topic": insight[:400],
                        "why_this_author": str(idea_fields.get("why_this_author") or insight[:400]),
                        "lane": str(idea_fields.get("lane") or ""),
                        "audience": str(idea_fields.get("audience") or ""),
                    }
            if not record["topic"]:
                continue
            dated.append((str(fields.get(stamp_key) or fields.get("created") or ""), record))
    dated.sort(key=lambda item: item[0])
    unique: list[dict] = []
    seen: set[str] = set()
    for _stamp, record in dated:
        key = str(record.get("topic", "")).casefold()[:180]
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(record)
    return unique[-4:]


def published_topic_records(content_root: Path) -> list[dict]:
    """Every published or approved idea, including ones outside the recent window."""
    records: list[dict] = []
    for folder in ("published", "approved"):
        directory = content_root / folder
        if not directory.is_dir():
            continue
        for path in directory.glob("POST-*.md"):
            try:
                fields, _rest = engine.parse_frontmatter(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            idea_id = str(fields.get("idea") or "")
            if not idea_id:
                continue
            idea_path = content_root / "ideas" / f"{idea_id}.md"
            if not idea_path.is_file():
                continue
            try:
                idea_fields, idea_rest = engine.parse_frontmatter(idea_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            insight = engine.section_map(idea_rest).get("Insight", "").strip()
            if not insight:
                continue
            records.append(
                {
                    "topic": insight[:400],
                    "why_this_author": str(idea_fields.get("why_this_author") or insight[:400]),
                    "lane": str(idea_fields.get("lane") or ""),
                    "audience": str(idea_fields.get("audience") or ""),
                }
            )
    return records


def similarity_priors(content_root: Path, topics: list[dict]) -> list[dict]:
    """Recent topics, plus published and approved work that has left that window.

    Lane mix still uses the fixed window. A published anecdote does not.
    """
    window = recent_topic_records(content_root, topics)
    priors = list(window)
    seen = {str(item.get("topic", "")).casefold()[:180] for item in window}
    for record in published_topic_records(content_root):
        key = str(record.get("topic", "")).casefold()[:180]
        if not key or key in seen:
            continue
        seen.add(key)
        priors.append(record)
    return priors


def trend_file_exists(relative: str) -> bool:
    return (ROOT / relative).is_file()


def selection_path(content_root: Path, post_id: str) -> Path:
    return content_root / CYCLES_DIR / f"{post_id}.selection.json"


def stored_selection_topic(content_root: Path, topic_name: str) -> dict | None:
    """Topic record of an earlier editorial selection, so resume does not need a strategy file."""
    folder = content_root / CYCLES_DIR
    if not folder.is_dir():
        return None
    for path in sorted(folder.glob("POST-*.selection.json"), reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        topic = (data.get("selected") or {}).get("topic") if isinstance(data, dict) else None
        if isinstance(topic, dict) and str(topic.get("topic", "")).casefold() == topic_name.casefold():
            return dict(topic)
    return None


def select_topic(content_root: Path, topic_name: str | None = None) -> dict:
    """Pick a strategy topic whose label matches its evidence and recent history.

    An explicit topic name may repeat a recent subject. That request is the reason.
    It may not compose a project-metric subject under a BROAD_TECH positioning label.
    """
    strategy_path, topics = latest_strategy(content_root)
    if topic_name:
        claims = _selection_claims()
        candidates = [topic for topic in topics if str(topic.get("topic", "")).strip().casefold() == topic_name.casefold()]
        stored = None if candidates else stored_selection_topic(content_root, topic_name)
        if stored is not None:
            candidates = [stored]
        for topic in candidates:
            title = str(topic.get("topic", "")).strip()
            reason = strategy_mod.topic_audience_conflict(topic, claims) or strategy_mod.topic_substance_gap(topic, claims)
            if reason:
                raise CycleError("STRATEGY_MISALIGNED", f"topic deferred: {title}. {reason}")
            chosen = dict(topic)
            chosen["_deferred"] = []
            return chosen
        if not strategy_path or not topics:
            raise CycleError("INVALID_STRATEGY", "no proposed strategy with topics")
        raise CycleError("INVALID_STRATEGY", f"topic not found: {topic_name}")
    if not strategy_path or not topics:
        raise CycleError("INVALID_STRATEGY", "no proposed strategy with topics")
    claims = _selection_claims()
    recent = recent_topic_records(content_root, topics)
    eligible, deferred = strategy_eligibility(content_root, topics, claims)
    if not eligible:
        detail = "no eligible strategy topics"
        if deferred:
            detail += ". " + " | ".join(deferred)
        raise CycleError("INVALID_STRATEGY", detail)
    chosen = dict(strategy_mod.choose_topic(eligible, [str(item.get("lane") or "") for item in recent]))
    chosen["_deferred"] = deferred
    return chosen


def strategy_eligibility(content_root: Path, topics: list[dict], claims: list[dict]) -> tuple[list[dict], list[str]]:
    """Strategy topics that survive audience, similarity, substance, source, and reuse checks."""
    priors = similarity_priors(content_root, topics)
    used = used_topics(content_root)
    deferred: list[str] = []
    eligible: list[dict] = []
    for topic in topics:
        title = str(topic.get("topic", "")).strip()
        reason = strategy_mod.topic_audience_conflict(topic, claims)
        if reason:
            deferred.append(f"{title}: {reason}")
            continue
        if any(strategy_mod.topics_too_similar(topic, prior, claims) for prior in priors):
            deferred.append(f"{title}: too similar to a recent topic")
            continue
        substance = strategy_mod.topic_substance_gap(topic, claims)
        if substance:
            deferred.append(f"{title}: {substance}")
            continue
        source = strategy_mod.trend_source_problem(topic, trend_file_exists)
        if source:
            deferred.append(f"{title}: {source}")
            continue
        if title.casefold() in used:
            deferred.append(f"{title}: already used")
            continue
        eligible.append(topic)
    return eligible, deferred


def originality_priors(content_root: Path) -> list[dict]:
    """Every attempted, dropped, approved, or published thesis. Not limited to the lane window."""
    priors: list[dict] = []
    seen: set[str] = set()
    cycles = content_root / CYCLES_DIR
    for path in sorted(cycles.glob("POST-*.yaml")) if cycles.is_dir() else []:
        state = load_cycle(content_root, path.stem)
        if not state or not state.topic:
            continue
        insight = ""
        source_urls: list[str] = []
        status = "dropped" if state.outcome == "DROPPED" else "attempted"
        for draft in engine.find_posts(content_root, state.post_id):
            try:
                fields, _rest = engine.parse_frontmatter(draft.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if fields.get("human_editorial_decision") == "DROP":
                status = "dropped"
            if draft.parent.name in {"approved", "published"}:
                status = draft.parent.name
            idea_path = content_root / "ideas" / f"{fields.get('idea', '')}.md"
            if fields.get("idea") and idea_path.is_file():
                try:
                    _idea_fields, idea_rest = engine.parse_frontmatter(idea_path.read_text(encoding="utf-8"))
                    idea_sections = engine.section_map(idea_rest)
                    insight = idea_sections.get("Insight", "").strip()
                    why_now = str(_idea_fields.get("why_now") or "").strip()
                    if not why_now:
                        why_now = idea_sections.get("Why now", "") or idea_sections.get("Why Now", "")
                    source_urls = discovery_mod.urls_in_text(why_now)
                except (OSError, ValueError):
                    pass
        key = state.topic.casefold()
        if key not in seen:
            seen.add(key)
            priors.append(
                {
                    "topic": state.topic,
                    "insight": re.sub(r"\s+", " ", insight)[:400],
                    "status": status,
                    "lane": state.lane,
                    "post_id": state.post_id,
                    "source_urls": source_urls,
                }
            )
    for record in published_topic_records(content_root):
        key = str(record.get("topic", "")).casefold()[:180]
        if key in seen:
            continue
        seen.add(key)
        priors.append(
            {
                "topic": record["topic"][:180],
                "insight": record["topic"],
                "status": "published",
                "lane": record.get("lane", ""),
                "post_id": "",
                "source_urls": [],
            }
        )
    return priors


def editorial_selection(content_root: Path, runtime: "LLMRuntime", settings: discovery_mod.DiscoverySettings) -> dict:
    """Run research reuse or refresh, assessment, gates, and choice. Outcome may be NO_EDITORIAL_OPPORTUNITY."""
    _strategy_path, topics = latest_strategy(content_root)
    claims = _selection_claims()
    eligible, deferred = strategy_eligibility(content_root, topics, claims) if topics else ([], [])
    recent_lanes = [str(item.get("lane") or "") for item in recent_topic_records(content_root, topics)]
    legacy = strategy_mod.choose_topic(eligible, recent_lanes) if eligible else None
    try:
        decision = discovery_mod.decide(
            settings,
            runtime,
            strategy_topics=eligible,
            strategy_deferred=deferred,
            priors=originality_priors(content_root),
            used_titles=used_topics(content_root),
            recent_lanes=recent_lanes,
            claims=claims,
            legacy_choice=legacy,
        )
    except discovery_mod.DiscoveryError as exc:
        raise CycleError(exc.code, str(exc)) from exc
    return decision


class CycleError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def run_subprocess(
    args: list[str],
    *,
    cwd: Path | None = None,
    content_root: Path | None = None,
) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    root = content_root or ACTIVE_CONTENT_ROOT
    if root is not None:
        env["CONTENT_ROOT"] = str(root)
    completed = subprocess.run(
        [sys.executable, *args],
        cwd=str(cwd or ROOT),
        capture_output=True,
        check=False,
        env=env,
    )
    try:
        stdout = (completed.stdout or b"").decode("utf-8")
        stderr = (completed.stderr or b"").decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CycleError("RUNTIME_FAILURE", f"subprocess output is not valid UTF-8: {exc}") from exc
    return subprocess.CompletedProcess(completed.args, completed.returncode, stdout, stderr)


def load_post_draft(content_root: Path, post_id: str) -> Path:
    matches = [p for p in engine.find_posts(content_root, post_id) if p.parent.name == "drafts"]
    if not matches:
        raise CycleError("STALE_ARTIFACT", f"{post_id}: draft not found")
    if len(matches) > 1:
        raise CycleError("STALE_ARTIFACT", f"{post_id}: duplicate drafts")
    return matches[0]


def upsert_section(rest: str, heading: str, body: str) -> str:
    pattern = re.compile(rf"\n{re.escape(heading)}\n.*?(?=^## |\Z)", re.MULTILINE | re.DOTALL)
    if heading in rest:
        rest = pattern.sub("\n", rest, count=1)
    return rest.rstrip() + f"\n\n{heading}\n\n{body.strip()}\n"


def write_idea_plan_post(
    content_root: Path,
    topic: dict,
    idea_id: str,
    plan_id: str,
    post_id: str,
    compose: dict,
) -> Path:
    day = post_id.split("-")[1]
    idea_path = content_root / "ideas" / f"{idea_id}.md"
    plan_path = content_root / "plans" / f"{plan_id}.md"
    post_path = content_root / "drafts" / f"{post_id}.md"
    insight = str(compose.get("insight") or topic.get("topic", "")).strip()
    hook = str(compose.get("hook") or "receipt").strip()
    body = str(compose.get("body", "")).strip() + "\n"
    claims_yaml = compose.get("claims_yaml") or "- none\n"
    hooks_considered = str(compose.get("hooks_considered") or "receipt — grounded opening.")
    idea_front = {
        "id": idea_id,
        "kind": "idea",
        "status": "proposed",
        "created": f"{day[:4]}-{day[4:6]}-{day[6:8]}",
        "lane": topic.get("lane", ""),
        "audience": topic.get("audience", ""),
        "standalone_value": topic.get("standalone_value", ""),
        "project_context_required": topic.get("project_context_required", ""),
        "technical_depth": topic.get("technical_depth", ""),
        "why_now": topic.get("why_now", ""),
        "why_this_author": topic.get("why_this_author", ""),
    }
    idea_body = f"""## Insight

{insight}

## Why it fits

{topic.get('why_reader_cares', '')}

## Claims

{claims_yaml}

## Evidence gaps

- none

## Local overlap

- none
"""
    plan_body = f"""## Slots

- order: 1
  idea: {idea_id}
  hook: {hook}
  angle: "{insight[:120]}"

## Not scheduled

No posting time is assigned.
"""
    post_body = f"""---
id: {post_id}
kind: post
status: drafted
created: {idea_front['created']}
idea: {idea_id}
plan: {plan_id}
hook: {hook}
body_sha256:
review_verdict:
review_body_sha256:
editorial_verdict:
editorial_body_sha256:
editorial_reviewed_at:
critic_verdict:
critic_body_sha256:
critic_reviewed_at:
human_editorial_decision:
preview_body_sha256:
preview_visual_sha256:
preview_generated_at:
---

## Hooks considered

{hooks_considered}

## Body

{body}

## Claims

{claims_yaml}

## Evidence gaps

- none
"""
    engine.write_atomic(idea_path, engine.render_frontmatter(idea_front, ("id", "kind", "status", "created", "lane", "audience", "standalone_value", "project_context_required", "technical_depth", "why_now", "why_this_author")) + idea_body)
    engine.write_atomic(plan_path, engine.render_frontmatter({"id": plan_id, "kind": "plan", "status": "proposed", "created": idea_front["created"]}, ("id", "kind", "status", "created")) + plan_body)
    engine.write_atomic(post_path, post_body)
    stamp = run_subprocess([str(SCRIPTS / "content_engine.py"), "stamp", str(post_path)])
    if stamp.returncode != 0:
        raise CycleError("RUNTIME_FAILURE", stamp.stderr or "stamp failed")
    return post_path


def humanize_post(post_path: Path) -> None:
    text = post_path.read_text(encoding="utf-8")
    fields, rest = engine.parse_frontmatter(text)
    sections = engine.section_map(rest)
    body = sections.get("Body", "")
    with tempfile.TemporaryDirectory() as tmp:
        body_file = Path(tmp) / "body.txt"
        body_file.write_text(body, encoding="utf-8")
        humanize = ROOT / ".cursor" / "skills" / "li-human" / "humanize.py"
        detect = ROOT / ".cursor" / "skills" / "li-human" / "detect.py"
        out_file = Path(tmp) / "clean.txt"
        style_env = os.environ.copy()
        style_env["PYTHONIOENCODING"] = "utf-8"
        style_env["PYTHONUTF8"] = "1"
        cleaned = subprocess.run(
            [sys.executable, str(humanize), str(body_file), "-o", str(out_file), "--report"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=style_env,
            check=False,
        )
        if cleaned.returncode != 0:
            raise CycleError("RUNTIME_FAILURE", cleaned.stderr or "humanize failed")
        if out_file.is_file():
            body = out_file.read_text(encoding="utf-8")
        subprocess.run(
            [sys.executable, str(detect), str(body_file)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=style_env,
        )
        report = (cleaned.stdout or "").strip() or "Style cleanup applied."
        sections["Body"] = body if body.endswith("\n") else body + "\n"
        sections["Style report"] = report + "\n\nStyle signal only. Not an approval gate.\n"
        new_rest = rebuild_post_rest(sections)
        engine.write_atomic(post_path, engine.render_frontmatter(fields, engine.POST_FIELD_ORDER) + new_rest)
    stamp = run_subprocess(
        [str(SCRIPTS / "content_engine.py"), "stamp", "--invalidate", "--status", "styled", str(post_path)]
    )
    if stamp.returncode != 0:
        raise CycleError("RUNTIME_FAILURE", stamp.stderr or "stamp after humanize failed")


def rebuild_post_rest(sections: dict[str, str]) -> str:
    order = ("Hooks considered", "Body", "Claims", "Evidence gaps", "Style report", "Review", "Editorial review", "Critic review", HUMAN_REVISION_HEADING.lstrip("#").strip())
    parts: list[str] = []
    for name in order:
        heading = name if name.startswith("##") else f"## {name}"
        key = name.replace("## ", "")
        if key in sections and sections[key].strip():
            parts.append(f"{heading}\n\n{sections[key].rstrip()}\n")
    for key, value in sections.items():
        if key in {x.lstrip("# ").strip() for x in order}:
            continue
        if value.strip():
            parts.append(f"## {key}\n\n{value.rstrip()}\n")
    return "\n" + "\n".join(parts)


def semantic_script_verdict(post_path: Path) -> tuple[str, list[str]]:
    completed = run_subprocess([str(SCRIPTS / "content_engine.py"), "review", str(post_path)])
    lines = (completed.stdout or "").splitlines()
    verdict = lines[0].strip() if lines else "BLOCKED"
    reasons = [line[2:].strip() for line in lines[1:] if line.startswith("- ")]
    return verdict, reasons


def record_semantic_review(post_path: Path, verdict: str, findings: str) -> None:
    text = post_path.read_text(encoding="utf-8")
    fields, rest = engine.parse_frontmatter(text)
    sections = engine.section_map(rest)
    digest = engine.body_hash(sections["Body"])
    fields["status"] = "reviewed"
    fields["review_verdict"] = verdict
    fields["review_body_sha256"] = digest
    fields["body_sha256"] = digest
    sections["Review"] = findings.strip() + "\n"
    rest = rebuild_post_rest(sections)
    engine.write_atomic(post_path, engine.render_frontmatter(fields, engine.POST_FIELD_ORDER) + rest)
    check = run_subprocess([str(SCRIPTS / "content_engine.py"), "check", str(post_path)])
    if check.returncode != 0:
        raise CycleError("SEMANTIC_FAILURE", check.stderr or "semantic check failed")


def clear_human_revise_when_ready(content_root: Path, post_id: str, pre_revision_digest: str, state: CycleState) -> bool:
    """Clear pending human REVISE only after a changed body passes all gates except the human decision."""
    if not pre_revision_digest:
        return False
    post_path = load_post_draft(content_root, post_id)
    text = post_path.read_text(encoding="utf-8")
    fields, rest = engine.parse_frontmatter(text)
    if fields.get("human_editorial_decision") != "REVISE":
        return False
    sections = engine.section_map(rest)
    digest = engine.body_hash(sections.get("Body", ""))
    if digest == pre_revision_digest:
        return False
    ready, _reasons = readiness_report(content_root, post_id, ignore_human_pending=True)
    if not ready:
        return False
    heading = HUMAN_REVISION_HEADING.lstrip("#").strip()
    note = sections.get(heading, "").strip()
    if note:
        state.events.append(f"human revision applied: {note[:240]}")
    reviews = {
        "semantic": str(fields.get("review_verdict") or ""),
        "editorial": str(fields.get("editorial_verdict") or ""),
        "critic": str(fields.get("critic_verdict") or ""),
    }
    sequence = 0
    for event in reversed(state.events):
        match = HUMAN_REV_EVENT_RE.match(event)
        if match:
            sequence = int(match.group(1))
            break
    complete_pending_human_revision(
        state,
        post_body_sha256=digest,
        reviews=reviews,
        sequence=sequence,
    )
    fields["human_editorial_decision"] = ""
    if heading in sections:
        del sections[heading]
    engine.write_atomic(
        post_path,
        engine.render_frontmatter(fields, engine.POST_FIELD_ORDER) + rebuild_post_rest(sections),
    )
    save_cycle(content_root, state)
    return True


def apply_body_revision(post_path: Path, new_body: str) -> None:
    text = post_path.read_text(encoding="utf-8")
    fields, rest = engine.parse_frontmatter(text)
    sections = engine.section_map(rest)
    sections["Body"] = new_body if new_body.endswith("\n") else new_body + "\n"
    rest = rebuild_post_rest(sections)
    engine.write_atomic(post_path, engine.render_frontmatter(fields, engine.POST_FIELD_ORDER) + rest)
    stamp = run_subprocess(
        [str(SCRIPTS / "content_engine.py"), "stamp", "--invalidate", "--status", "styled", str(post_path)]
    )
    if stamp.returncode != 0:
        raise CycleError("RUNTIME_FAILURE", stamp.stderr or "stamp after revision failed")


def merge_verdict(script: str, model: str) -> str:
    order = {"PASS": 0, "NEEDS_EDIT": 1, "REVISE": 1, "BLOCKED": 2, "DROP": 2, "EVIDENCE_TOO_THIN": 2}
    if model not in order:
        return script if script in order else "BLOCKED"
    if script not in order:
        return "BLOCKED"
    return script if order[script] >= order[model] else model


CLAIM_ID_RE = re.compile(r"\b[A-Z][A-Z0-9]+_\d+\b")
GROUNDING_HINTS = (
    "derived",
    "evidence",
    "unsupported",
    "qualification",
    "confidential",
    "inflat",
    "measured",
    "conclusion frame",
    "personal conclusion",
    "claim",
)
FRAME_TOKENS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "for",
        "from",
        "in",
        "is",
        "it",
        "not",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "was",
    }
)


@dataclass
class SemanticConcern:
    """A model semantic finding that can override a deterministic PASS."""

    kind: str
    sentence: str
    claim_id: str
    exceeds: str
    recoverable: bool
    correction: str

    def complete(self) -> bool:
        return bool(self.sentence.strip() and self.exceeds.strip() and self.correction.strip())

    def key(self) -> str:
        return " ".join(self.sentence.casefold().split())


def parse_semantic_concern(payload: dict) -> SemanticConcern | None:
    raw = payload.get("concern")
    if not isinstance(raw, dict):
        return None
    recoverable = raw.get("recoverable", True)
    if isinstance(recoverable, str):
        recoverable = recoverable.strip().casefold() in {"true", "yes", "1"}
    return SemanticConcern(
        kind=str(raw.get("kind", "")).strip().casefold(),
        sentence=str(raw.get("sentence", "")).strip(),
        claim_id=str(raw.get("claim_id", "")).strip(),
        exceeds=str(raw.get("exceeds", "")).strip(),
        recoverable=bool(recoverable),
        correction=str(raw.get("correction", "")).strip(),
    )


def format_concern(concern: SemanticConcern) -> str:
    claim = concern.claim_id or "none"
    recoverable = "yes" if concern.recoverable else "no"
    return (
        f"sentence: {concern.sentence}; "
        f"claim_id: {claim}; "
        f"exceeds: {concern.exceeds}; "
        f"recoverable: {recoverable}; "
        f"correction: {concern.correction}"
    )


def looks_like_grounding(findings: str) -> bool:
    """True when free text names evidence or a claim rather than presentation."""
    if CLAIM_ID_RE.search(findings or ""):
        return True
    folded = (findings or "").casefold()
    return any(hint in folded for hint in GROUNDING_HINTS)


def resolve_semantic_verdict(
    script: str,
    model: str,
    concern: SemanticConcern | None,
    findings: str,
) -> tuple[str, str]:
    """Combine script and model verdicts.

    The model cannot weaken a deterministic BLOCKED or NEEDS_EDIT.
    A deterministic PASS stands unless the model returns a complete semantic
    concern. Editorial objections and undiagnosable wording notes do not
    override PASS. An undiagnosable grounding objection stays a stop.
    """
    model_verdict = (model or "").strip().upper()
    if model_verdict not in {"PASS", "NEEDS_EDIT", "BLOCKED"}:
        model_verdict = script if script in {"PASS", "NEEDS_EDIT", "BLOCKED"} else "BLOCKED"
    if script == "BLOCKED":
        return "BLOCKED", "script"
    if script == "NEEDS_EDIT":
        if model_verdict == "BLOCKED" and concern and concern.kind == "semantic" and concern.complete():
            return "BLOCKED", "retain"
        return "NEEDS_EDIT", "script"
    if script != "PASS":
        return "BLOCKED", "script"
    if model_verdict == "PASS":
        return "PASS", "script"
    if concern and concern.kind == "editorial":
        return "PASS", "script"
    if concern and concern.kind == "semantic" and concern.complete():
        if model_verdict == "BLOCKED" or not concern.recoverable:
            return "BLOCKED", "retain"
        return "NEEDS_EDIT", "retain"
    if model_verdict == "BLOCKED" or looks_like_grounding(findings):
        kept = "BLOCKED" if model_verdict == "BLOCKED" else "NEEDS_EDIT"
        return kept, "undiagnosed"
    return "PASS", "script"


def _content_tokens(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9']+", text.casefold())
    return [word for word in words if len(word) > 3 and word not in FRAME_TOKENS]


def _sentence_framed(text: str) -> bool:
    folded = text.casefold()
    return any(frame in folded for frame in engine.CONCLUSION_FRAMES)


def repair_applied(body: str, concern: SemanticConcern) -> bool:
    """True when the named sentence now carries the requested frame, or is gone.

    A conclusion frame on a different sentence does not count. A framed
    paraphrase does not count while the named sentence is still bare.
    """
    sentence = concern.sentence.strip()
    if not sentence:
        return False
    exact = sentence.casefold().rstrip(" .")
    correction = concern.correction.casefold()
    wants_frame = (
        any(frame in correction for frame in engine.CONCLUSION_FRAMES)
        or "conclusion frame" in correction
        or "personal conclusion" in correction
    )
    parts = engine.split_sentences(body)
    if wants_frame:
        containers = [part for part in parts if exact and exact in part.casefold()]
        if containers:
            return all(_sentence_framed(part) for part in containers)
        needles = _content_tokens(sentence)
        if len(needles) < 3:
            return False
        needed = min(4, len(needles))
        framed_hit = False
        unframed_hit = False
        for part in parts:
            part_fold = part.casefold()
            if sum(1 for token in needles if token in part_fold) < needed:
                continue
            if _sentence_framed(part):
                framed_hit = True
            else:
                unframed_hit = True
        return framed_hit and not unframed_hit
    if not exact:
        return False
    return all(exact not in part.casefold() for part in parts)


def semantic_revision_issues(
    script_reasons: list[str],
    model_findings: str,
    concern: SemanticConcern | None = None,
) -> list[str]:
    """Turn a retained semantic edit into a repair the reviser can execute."""
    issues: list[str] = []
    for reason in script_reasons:
        issues.append(reason)
        if reason == "derived statement is not framed as a personal conclusion":
            issues.append(
                "Add one of these frames to the derived judgment: "
                "'My conclusion', 'I treat this as', or 'The lesson I take'. "
                "Other first-person sentences do not satisfy this check. "
                "A frame on a different sentence does not cover this one. "
                "Do not move project metrics into the opening to fix it."
            )
    if concern and concern.kind == "semantic" and concern.complete():
        issues.append(
            "Blocking semantic concern. Apply this repair only. Do not invent facts. "
            + format_concern(concern)
        )
    note = (model_findings or "").strip()
    if note and script_reasons:
        issues.append(
            "Reviewer note, not authoritative. Ignore it when it conflicts with the deterministic reasons above: "
            + note
        )
    return issues


def format_semantic_findings(
    script_reasons: list[str],
    model_findings: str,
    concern: SemanticConcern | None = None,
) -> str:
    """Keep the deterministic reasons and any retained concern ahead of a paraphrase."""
    lines: list[str] = []
    if script_reasons:
        lines.append("Deterministic review:")
        lines.extend(f"- {reason}" for reason in script_reasons)
    if concern and concern.kind == "semantic" and concern.complete():
        lines.append("Semantic concern:")
        lines.append(format_concern(concern))
    note = (model_findings or "").strip()
    if note:
        lines.append("Reviewer note:")
        lines.append(note)
    return "\n".join(lines).strip() or "PASS"


def semantic_exhaustion_detail(
    script_reasons: list[str],
    concern: SemanticConcern | None,
    findings: str,
    *,
    unchanged: bool = False,
) -> str:
    """Name the unresolved claim. A retry count alone is not the diagnosis."""
    if unchanged:
        lead = "semantic NEEDS_EDIT unresolved; revision left the blocking sentence unchanged"
    else:
        lead = "semantic NEEDS_EDIT after revision limit"
    parts = [lead]
    if script_reasons:
        parts.append("; ".join(script_reasons))
    if concern and concern.complete():
        parts.append(format_concern(concern))
    elif (findings or "").strip() and not script_reasons:
        parts.append(findings.strip())
    if len(parts) == 1:
        return lead
    return parts[0] + ": " + " ".join(parts[1:])


def unresolved_wording(script_reasons: list[str], review_text: str) -> list[str]:
    if script_reasons:
        return [f"recoverable wording: {reason}" for reason in script_reasons]
    summary = " ".join((review_text or "").split())
    if summary and summary != "PASS":
        return [f"recoverable wording: {summary[:500]}"]
    return ["recoverable wording: semantic NEEDS_EDIT"]


def semantic_diagnostics(
    sections: dict,
    claims: list[dict],
    *,
    source_bundle: str = "",
) -> tuple[str, list[str]]:
    records = engine.claim_records(sections.get("Claims", ""))
    if records is None:
        return "BLOCKED", ["claims section could not be read"]
    return engine.review_body(
        sections.get("Body", ""),
        records,
        claims,
        source_bundle=source_bundle,
    )


def claims_structure_error(claims_section: str) -> str:
    """Return a terminal error when Claims are blank or unreadable."""
    status, _records = engine.claims_section_status(claims_section)
    if status == "empty":
        return "claims section is empty; use - none when no personal claim is cited."
    if status == "malformed":
        return "claims section could not be read"
    return ""


def run_editorial(content_root: Path, post_id: str) -> str:
    completed = run_subprocess([str(SCRIPTS / "content_engine.py"), "editorial", post_id])
    if completed.returncode != 0:
        raise CycleError("EDITORIAL_FAILURE", completed.stderr or completed.stdout or "editorial failed")
    return (completed.stdout or "").strip().splitlines()[0]


def run_critic(
    runtime: LLMRuntime,
    content_root: Path,
    post_path: Path,
    topic: dict,
    body: str,
) -> critic_mod.CriticFindings:
    skill = ROOT / ".cursor" / "skills" / "li-critic" / "SKILL.md"
    criteria = skill.read_text(encoding="utf-8") if skill.is_file() else ""
    prompt = f"""You are an independent adversarial editorial critic. Do not edit files.
Read docs/content-contract.md rules in the workspace snapshot.

Topic: {topic.get('topic')}
Lane: {topic.get('lane')}
Audience: {topic.get('audience')}

Criteria:
{criteria}

Post body:
---
{body.strip()}
---

Return ONE JSON object only (prose allowed outside it) with keys:
verdict (PASS|REVISE|DROP), blocking_issues (array), non_blocking_issues (array),
revision_instructions (array), reason (string).
Do NOT repeat editorial scoring; focus on publish-worthiness and reader insight.
"""
    result = runtime.ask(prompt, validate=cursor_runtime_mod.require_keys("verdict"))
    findings = critic_mod.findings_from_payload(result.parsed)
    code = engine.apply_critic(post_path, content_root, findings)
    if code != 0:
        raise CycleError("CRITIC_FAILURE", "failed to persist critic verdict")
    return findings


def run_visual_preview(content_root: Path, post_id: str) -> None:
    vis = run_subprocess([str(SCRIPTS / "visual.py"), post_id])
    if vis.returncode != 0:
        raise CycleError("VISUAL_FAILURE", vis.stderr or "visual failed")
    prev = run_subprocess([str(SCRIPTS / "preview.py"), post_id])
    if prev.returncode != 0:
        raise CycleError("PREVIEW_FAILURE", prev.stderr or "preview failed")


def readiness_report(content_root: Path, post_id: str, *, ignore_human_pending: bool = False) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    lock_file = lock_path(content_root, post_id)
    if lock_file.exists():
        try:
            pid = int(lock_file.read_text(encoding="utf-8").strip() or "0")
        except ValueError:
            pid = 0
        if pid != os.getpid() and pid_alive(pid):
            reasons.append("cycle lock held")
    try:
        post_path = load_post_draft(content_root, post_id)
    except CycleError as exc:
        return False, [str(exc)]
    text = post_path.read_text(encoding="utf-8")
    fields, rest = engine.parse_frontmatter(text)
    sections = engine.section_map(rest)
    body = sections.get("Body", "")
    digest = engine.body_hash(body)
    claims = engine.load_claims()
    idea_id = str(fields.get("idea") or "")
    source_bundle = engine.load_source_bundle(content_root, idea_id) if idea_id else ""
    script_verdict, script_reasons = semantic_diagnostics(sections, claims, source_bundle=source_bundle)
    recorded = fields.get("review_verdict", "")
    review_hash = fields.get("review_body_sha256", "")
    terminal_block = recorded == "BLOCKED" and review_hash == digest
    terminal_needs_edit = recorded == "NEEDS_EDIT" and review_hash == digest
    terminal_stop = terminal_block or terminal_needs_edit
    stop_label = "BLOCKED" if terminal_block else "NEEDS_EDIT"
    if fields.get("status") != "reviewed":
        reasons.append("status must be reviewed")
    if terminal_block:
        if script_reasons:
            reasons.extend(f"unsupported claim: {reason}" for reason in script_reasons)
        else:
            note = " ".join(sections.get("Review", "").split())
            if note and note != "BLOCKED":
                reasons.append(f"unsupported claim: {note[:500]}")
            else:
                reasons.append("unsupported claim: semantic review is BLOCKED")
    elif terminal_needs_edit:
        reasons.extend(unresolved_wording(script_reasons, sections.get("Review", "")))
    elif recorded != "PASS" or review_hash != digest:
        reasons.append("semantic review not current PASS")
    if terminal_stop and not fields.get("editorial_verdict"):
        reasons.append(f"skipped gate: editorial review after semantic {stop_label}")
    elif fields.get("editorial_verdict") != "PASS" or fields.get("editorial_body_sha256") != digest:
        reasons.append("editorial review not current PASS")
    critic_v = fields.get("critic_verdict", "")
    if terminal_stop and not critic_v:
        reasons.append(f"skipped gate: critic review after semantic {stop_label}")
    elif critic_v != "PASS" or fields.get("critic_body_sha256") != digest:
        reasons.append("critic review not current PASS")
    decision = fields.get("human_editorial_decision", "")
    if not ignore_human_pending:
        if decision in {"REVISE", "DROP"}:
            reasons.append(f"human_editorial_decision is {decision}")
        heading = HUMAN_REVISION_HEADING.lstrip("#").strip()
        if heading in sections and sections.get(heading, "").strip():
            reasons.append("human revision pending")
    preview_hash = fields.get("preview_body_sha256", "")
    preview_file = content_root / "previews" / f"{post_id}.html"
    preview_absent = (not preview_hash or preview_hash != digest) and not preview_file.is_file()
    if terminal_stop and preview_absent:
        reasons.append(f"skipped gate: preview after semantic {stop_label}")
    else:
        if not preview_hash or preview_hash != digest:
            reasons.append("preview missing or stale")
        if not preview_file.is_file():
            reasons.append("preview file missing")
    on_disk_visual, _ = visual_mod.read_visual_state(content_root, post_id)
    visual_stale = (fields.get("preview_visual_sha256") or "") != (on_disk_visual or "")
    if visual_stale and not (terminal_stop and preview_absent):
        reasons.append("preview visual stale")
    hooks = engine.load_hooks()
    errors = engine.check_artifact(post_path, claims, hooks, content_root)
    reasons.extend(f"invalid artifact: {error}" for error in errors)
    if script_verdict == "BLOCKED" and recorded not in {"BLOCKED", ""} and not terminal_block:
        reasons.extend(f"unsupported claim: {reason}" for reason in script_reasons)
    return len(reasons) == 0, reasons


def grounding_context(topic: dict) -> str:
    """Personal claims that develop this topic. Blocked ids are named, not quoted.

    The rest of the registry stays out of the prompt. An eligible claim is not
    a relevant claim, and a post may be general technical analysis with no
    personal anecdote.
    """
    claims = engine.load_claims()
    lines = [
        "Use a personal claim only when it develops the thesis. An eligible claim is not automatically a relevant claim.",
        "General technical analysis may be the whole post. It does not need a personal anecdote.",
        "Do not cite, copy, or narrate a claim that is not listed below.",
        "A sentence is a project outcome when it reports a personal result or contains I tested, I chose, I observed, I implemented, I improved, I fixed, I reduced, or I proved.",
        "That sentence must match one listed fact statement. Do not paraphrase it.",
        "Do not combine claims into a ranking. Did not improve, plus kept the lighter artifacts, is not a comparative win such as 'the lighter option won' or 'X beat Y'.",
        "An opinion must use a conclusion frame on that sentence (My conclusion, I treat this as, The lesson I take) and must not be stronger than its statement. A frame on a different sentence does not cover it. Preferable is not beat.",
        "Do not invent metrics, employers, repositories, scores, or tool names a claim does not state.",
        "Do not paste a career or profile label into the body to look grounded.",
    ]
    developing = [claim for claim in claims if strategy_mod.claim_develops_topic(topic, claim)]
    if developing:
        lines.append("Personal claims that develop this topic:")
    else:
        lines.append(
            "No personal claim develops this topic. claims_yaml may be `- none`. "
            "Do not add a personal anecdote to look grounded."
        )
    for claim in developing:
        kind = engine.classify(claim)
        lines.append(f"- {claim.get('id', '')} ({kind}): {claim.get('statement', '')}")
        qualification = claim.get("qualification") or ""
        if qualification:
            lines.append(f"  qualification: {qualification}")
    for claim in claims:
        if engine.classify(claim) == "blocked":
            lines.append(f"- {claim.get('id', '')} (blocked): not eligible")
    return "\n".join(lines)


def citation_mismatches(topic: dict, claims_section: str) -> list[str]:
    """Cited project, metric, career, or profile claims that do not develop the topic.

    Opinions stay with semantic review. A mismatch is not a wording revision:
    the reviser is told to preserve facts, so the wrong claim would remain.
    """
    records = engine.claim_records(claims_section)
    if records is None:
        return ["claims section could not be read"]
    by_id = {claim["id"]: claim for claim in engine.load_claims() if isinstance(claim.get("id"), str)}
    mismatches: list[str] = []
    for item in records:
        claim_id = str(item.get("id") or "")
        claim = by_id.get(claim_id)
        if claim is None or strategy_mod.claim_develops_topic(topic, claim):
            continue
        category = str(claim.get("category") or "")
        if category not in {"project", "metric", "career", "profile"}:
            continue
        mismatches.append(
            f"{claim_id} does not develop the thesis '{topic.get('topic', '')}'. "
            "An eligible claim is not a relevant claim. "
            "Do not revise this draft into a different anecdote."
        )
    return mismatches


def composition_constraints(topic: dict) -> str:
    """Tell composition to follow the topic, not the most concrete metric."""
    lane = str(topic.get("lane", ""))
    audience = str(topic.get("audience", ""))
    depth = str(topic.get("technical_depth", ""))
    lines = [
        "Listed claims are optional evidence for the thesis. They are not the outline, not the opening, and not a quota.",
        "When a sentence reports a project outcome, copy one fact statement. Do not paraphrase that sentence.",
        "Place those fact sentences after the reader-facing opening when the topic is positioning or accessible broad tech.",
        "If claims_yaml cites an opinion, each derived sentence needs one of these frames: "
        "My conclusion, I treat this as, The lesson I take. "
        "A frame on a different sentence does not cover it. "
        "Other first-person sentences do not satisfy that check.",
    ]
    reader = str(topic.get("why_reader_cares", "")).strip()
    if reader:
        lines.append(f"First paragraph reader stake, with no project metric in that paragraph: {reader}")
    thesis = str(topic.get("editorial_thesis") or "").strip()
    if thesis:
        lines.append(f"Thesis to develop as the author's interpretation, not as a reported fact: {thesis}")
    facts = [str(item) for item in topic.get("grounded_facts") or [] if str(item).strip()]
    if facts:
        lines.append(
            "External facts verified against the cited sources. State only these as facts, attributed to the dated source: "
            + " | ".join(facts)
        )
    concluded = str(topic.get("source_conclusion") or "").strip()
    if concluded:
        lines.append(
            "The source already concludes this. Attribute it; do not present it as the post's own insight: " + concluded
        )
    if thesis and concluded:
        lines.append(
            "Develop this editorial thesis as additional understanding beyond the source conclusion, not as a repeat of it: "
            + thesis
        )
        lines.append(
            "A sentence that only restates the source conclusion belongs in attributed reporting, "
            "not voiced as a new personal lesson."
        )
    limits = [str(item) for item in topic.get("source_limitations") or [] if str(item).strip()]
    if limits:
        lines.append("Keep these limitations visible where the related fact appears: " + " | ".join(limits))
    avoid = [str(item) for item in topic.get("do_not_claim") or [] if str(item).strip()]
    if avoid:
        lines.append("The sources do not establish these. Do not assert or imply them: " + " | ".join(avoid))
    if audience == "BROAD_TECH":
        lines.append(
            "Audience is BROAD_TECH: engineering managers and senior engineers who do not know this project. "
            "The main point has to land before specialist vocabulary. "
            "Do not teach Hit Rate, MRR, reciprocal rank, or RRF in this post. "
            "Those subjects remain valid for AI_ENGINEERING and SPECIALIST topics. "
            "A general engineering point does not need a personal project in every paragraph. "
            "A personal fact, when you include one, must still copy one eligible fact statement."
        )
    if lane == "POSITIONING" or (audience == "BROAD_TECH" and depth == "ACCESSIBLE"):
        lines.append(
            "This topic is positioning or accessible BROAD_TECH. The project is evidence, not the headline. "
            "Do not open with a project name, Hit Rate, MRR, or an evaluation score. "
            "A hiring lead should understand the point before any metric appears."
        )
    if lane == "PROJECT_SHOWCASE":
        lines.append(
            "PROJECT_SHOWCASE introduces one project from the outside in: the reader problem, "
            "what was built, one engineering choice, then a scoped result. "
            "Do not assume the reader knows the project. Do not open with a metric."
        )
    if lane == "CAREER_ENGINEERING":
        lines.append(
            "CAREER_ENGINEERING is the bridge from backend or platform practice to Applied AI. "
            "Do not turn it into a retrieval-metric lesson."
        )
    if lane == "TECHNICAL_DEEP_DIVE":
        lines.append(
            "TECHNICAL_DEEP_DIVE may use a metric as evidence. The first paragraph must say why that metric matters. "
            "Do not open with a bare score."
        )
    return "\n".join(lines)


def opening_is_metric_first(body: str) -> bool:
    if editorial_mod.is_bare_metric_opening(body):
        return True
    opening = editorial_mod.opening_text(body)
    if editorial_mod.READER_FIRST_FRAMING.search(opening) or editorial_mod.LESSON_FRAMES.search(opening):
        return False
    return bool(editorial_mod.SPECIALIST_METRIC_DEPTH.search(opening) and editorial_mod.NUMBER_RE.search(opening))


def strategy_alignment_issues(body: str, topic: dict) -> list[str]:
    """Return mismatches between the body and the selected topic.

    A reader-first opening does not make specialist metric pedagogy count as
    BROAD_TECH. Moving Hit Rate or MRR below the first paragraph is not a repair
    for that audience mismatch. Metric-first openings, LOW standalone value, and
    HIGH project context remain revisable when the audience itself still fits.
    """
    standalone, context, audience = editorial_mod.assess_standalone(body)
    issues: list[str] = []
    lane = str(topic.get("lane", ""))
    intended_audience = str(topic.get("audience", ""))
    intended_standalone = str(topic.get("standalone_value", ""))
    intended_context = str(topic.get("project_context_required", ""))
    depth = str(topic.get("technical_depth", ""))
    if intended_audience == "BROAD_TECH" and audience == "SPECIALIST":
        issues.append(
            "Audience mismatch: body assesses SPECIALIST while the topic intends BROAD_TECH. "
            "Specialist metric explanation does not satisfy that audience. "
            "Do not fix this by moving Hit Rate, MRR, or reciprocal rank below a general opening."
        )
    accessible = lane == "POSITIONING" or (intended_audience == "BROAD_TECH" and depth == "ACCESSIBLE")
    if accessible and opening_is_metric_first(body):
        issues.append(
            "Opening leads with a specialist metric or project result. "
            f"Topic '{topic.get('topic', '')}' is {lane} for {intended_audience}. "
            "Open with why a reader should care, then use the scoped result as evidence. "
            "Do not lead with Hit Rate, MRR, or the project name."
        )
    if intended_standalone in {"HIGH", "MEDIUM"} and standalone == "LOW":
        issues.append(
            f"Standalone value mismatch: topic expects {intended_standalone}, body assesses LOW "
            f"(assessed audience {audience}, project context {context})."
        )
    if intended_context in {"NONE", "LIGHT"} and context == "HIGH":
        issues.append(
            f"Project context mismatch: topic expects {intended_context}, body requires HIGH. "
            "The reader should not need prior knowledge of the project."
        )
    reader = str(topic.get("why_reader_cares", "")).strip()
    if issues and reader:
        issues.append(f"Reader stake to restore: {reader}")
    return issues


def alignment_is_terminal(issues: list[str]) -> bool:
    """Specialist substance for a broad audience is not a wording revision."""
    return any(item.startswith("Audience mismatch:") for item in issues)


def format_alignment_failure(body: str, issues: list[str], *, capped: bool) -> str:
    opening = editorial_mod.opening_text(body, 180).replace("\n", " ")
    label = "strategy misalignment after revision limit: " if capped else "strategy misalignment: "
    return label + " ".join(issues) + f" Opening: {opening}"


def editorial_revision_issues(findings: editorial_mod.EditorialFindings, topic: dict) -> list[str]:
    issues = [
        f"Editorial verdict: {findings.verdict}",
        f"Weakest element: {findings.weakest}",
        f"Suggested structural change: {findings.structural_change}",
        (
            f"Assessed standalone_value={findings.standalone_value}, "
            f"audience_breadth={findings.audience_breadth}, "
            f"project_context_required={findings.project_context_required}."
        ),
        (
            f"Intended audience={topic.get('audience', '')}, "
            f"standalone_value={topic.get('standalone_value', '')}, "
            f"project_context_required={topic.get('project_context_required', '')}, "
            f"lane={topic.get('lane', '')}."
        ),
    ]
    if findings.reader_value:
        issues.append(f"Reader value: {findings.reader_value}")
    if findings.missing_concrete:
        issues.append(f"Missing concrete detail: {findings.missing_concrete}")
    issues.extend(note for note in findings.notes if note)
    reader = str(topic.get("why_reader_cares", "")).strip()
    if reader:
        issues.append(f"Reader stake to restore: {reader}")
    return issues


def compose_prompt(topic: dict) -> str:
    return f"""Compose one grounded LinkedIn post artifact proposal. Do not write files.
Read knowledge/guardrails.md, knowledge/content-strategy.md, and docs/content-contract.md in the workspace.
The claim list below is the grounding context. Do not invent facts beyond it.

{grounding_context(topic)}

{composition_constraints(topic)}

Topic record:
{json.dumps(topic, ensure_ascii=False)}

Return ONE JSON object with:
insight (string), hook (slug from hooks.json), hooks_considered (string),
body (public post text only), claims_yaml (markdown list under Claims section rules),
thesis (string), lane (must equal {topic.get('lane')}), audience (must equal {topic.get('audience')}).
Keep each fact qualification as the claim scope, copied exactly.
"""


def revise_prompt(body: str, issues: list[str], human_note: str = "", topic: dict | None = None) -> str:
    note = f"\nHuman note:\n{human_note}\n" if human_note else ""
    strategy = ""
    if topic:
        strategy = (
            "Strategy context. Keep this topic, lane, and audience. You may change the opening and paragraph order.\n"
            f"topic: {topic.get('topic', '')}\n"
            f"lane: {topic.get('lane', '')}\n"
            f"audience: {topic.get('audience', '')}\n"
            f"standalone_value: {topic.get('standalone_value', '')}\n"
            f"project_context_required: {topic.get('project_context_required', '')}\n"
            f"why_reader_cares: {topic.get('why_reader_cares', '')}\n"
        )
    return f"""Revise this LinkedIn post body so it resolves the issues below.
Keep the strategy topic, lane, and audience. You may change the opening and paragraph order.
Preserve verified facts and their qualification scopes. Do not invent metrics, employers, repositories, scores, or tool names.
{strategy}
Issues:
{json.dumps(issues, ensure_ascii=False)}
{note}
Body:
---
{body.strip()}
---

Return ONE JSON object with key body (string, full revised public text).
"""


@dataclass
class Orchestrator:
    content_root: Path
    repo_root: Path
    runtime: LLMRuntime
    max_revisions: int = MAX_REVISION_CYCLES
    discovery: discovery_mod.DiscoverySettings | None = None

    def allocate_ids(self) -> tuple[str, str, str]:
        day = engine.today_stamp()
        idea = engine.next_id(self.content_root, "idea", day)
        plan = engine.next_id(self.content_root, "plan", day)
        post = engine.next_id(self.content_root, "post", day)
        return idea, plan, post

    def run(self, topic_name: str | None = None, post_id: str | None = None, human_note: str = "") -> CycleState:
        existing = load_cycle(self.content_root, post_id) if post_id else None
        if post_id and not topic_name and existing and existing.topic:
            topic_name = existing.topic
        decision: dict | None = None
        if self.discovery is not None and not topic_name and not post_id:
            decision = editorial_selection(self.content_root, self.runtime, self.discovery)
            if decision["outcome"] != "SELECTED":
                raise CycleError("NO_EDITORIAL_OPPORTUNITY", decision.get("no_opportunity_reason", "no worthwhile topic"))
            selected = decision["selected"]
            topic = dict(selected["topic"])
            deferred = [
                f"editorial selection {selected['id']} ({selected['kind']}, research {decision['research']['status']}): "
                + "; ".join(selected["why_selected"][:3])
            ]
        else:
            topic = select_topic(self.content_root, topic_name)
            deferred = [str(item) for item in topic.pop("_deferred", [])]
        events = list(existing.events) if existing else []
        for item in deferred:
            if item not in events:
                events.append(item)
        state = CycleState(
            post_id=post_id or "",
            topic=str(topic.get("topic", "")),
            lane=str(topic.get("lane", "")),
            audience=str(topic.get("audience", "")),
            stage="starting",
            revision_count=existing.revision_count if existing else 0,
            started_at=existing.started_at if existing and existing.started_at else utc_now(),
            pid=os.getpid(),
            events=events,
            human_revisions=list(existing.human_revisions) if existing else [],
        )
        if not state.post_id:
            idea_id, plan_id, post_id_new = self.allocate_ids()
            state.post_id = post_id_new
            if decision is not None:
                engine.write_atomic(
                    selection_path(self.content_root, state.post_id),
                    json.dumps({**decision, "post_id": state.post_id}, ensure_ascii=False, indent=2) + "\n",
                )
            compose = self.runtime.ask(
                compose_prompt(topic),
                validate=cursor_runtime_mod.require_keys("body", "hook", "claims_yaml"),
            ).parsed
            if str(compose.get("lane", topic.get("lane"))) != str(topic.get("lane")):
                raise CycleError("RUNTIME_FAILURE", "compose changed lane")
            if str(compose.get("audience", topic.get("audience"))) != str(topic.get("audience")):
                raise CycleError("RUNTIME_FAILURE", "compose changed audience")
            post_path = write_idea_plan_post(self.content_root, topic, idea_id, plan_id, state.post_id, compose)
        else:
            post_path = load_post_draft(self.content_root, state.post_id)
        save_cycle(self.content_root, state)
        ok, lock_msg = acquire_lock(self.content_root, state.post_id, os.getpid())
        if not ok:
            raise CycleError("RUNTIME_FAILURE", lock_msg)
        pre_revision_digest = ""
        try:
            if post_path is not None and human_note:
                text = post_path.read_text(encoding="utf-8")
                fields, rest = engine.parse_frontmatter(text)
                sections = engine.section_map(rest)
                heading = HUMAN_REVISION_HEADING.lstrip("#").strip()
                sections[heading] = human_note.strip() + "\n"
                engine.write_atomic(
                    post_path,
                    engine.render_frontmatter(fields, engine.POST_FIELD_ORDER) + rebuild_post_rest(sections),
                )
                sections = engine.section_map(engine.parse_frontmatter(post_path.read_text(encoding="utf-8"))[1])
                body = sections["Body"]
                pre_revision_digest = engine.body_hash(body)
                append_pending_human_revision(state, human_note, pre_revision_digest)
                save_cycle(self.content_root, state)
                try:
                    revised = self.runtime.ask(
                        revise_prompt(body, ["human revision request"], human_note, topic=topic),
                        validate=cursor_runtime_mod.require_keys("body"),
                    ).parsed
                except cursor_runtime_mod.CursorRuntimeError as exc:
                    state.error = str(exc)
                    state.outcome = "RUNTIME_FAILURE"
                    mark_pending_human_revision_status(state, "failed")
                    save_cycle(self.content_root, state)
                    raise CycleError("RUNTIME_FAILURE", str(exc)) from exc
                new_body = revised.get("body")
                if not isinstance(new_body, str) or not new_body.strip():
                    message = "human revision response missing body"
                    state.error = message
                    state.outcome = "RUNTIME_FAILURE"
                    mark_pending_human_revision_status(state, "failed")
                    save_cycle(self.content_root, state)
                    raise CycleError("RUNTIME_FAILURE", message)
                if engine.body_hash(new_body) == pre_revision_digest:
                    message = "human revision returned an unchanged body"
                    state.error = message
                    state.outcome = "REVISION_UNCHANGED"
                    state.events.append("human revision unchanged")
                    mark_pending_human_revision_status(state, "unchanged")
                    save_cycle(self.content_root, state)
                    raise CycleError("REVISION_UNCHANGED", message)
                apply_body_revision(post_path, new_body)
                state.revision_count += 1
                state.events.append(f"revision {state.revision_count}: human")
                save_cycle(self.content_root, state)
            try:
                state = self._pipeline(state, topic, post_path, human_note=human_note)
                if pre_revision_digest and clear_human_revise_when_ready(
                    self.content_root, state.post_id, pre_revision_digest, state
                ):
                    ready, reasons = readiness_report(self.content_root, state.post_id)
                    if ready:
                        state.stage = "complete"
                        state.outcome = OUTCOME_READY
                        state.error = ""
                    else:
                        state.stage = "failed"
                        state.outcome = "NOT_READY"
                        state.error = "; ".join(reasons)
                    save_cycle(self.content_root, state)
                return state
            except cursor_runtime_mod.CursorRuntimeError as exc:
                failure = CycleError("RUNTIME_FAILURE", str(exc))
                state.error = str(failure)
                if not state.outcome:
                    state.outcome = failure.code
                save_cycle(self.content_root, state)
                raise failure from exc
            except CycleError as exc:
                state.error = str(exc)
                if not state.outcome:
                    state.outcome = exc.code
                save_cycle(self.content_root, state)
                raise
        finally:
            release_lock(self.content_root, state.post_id)

    def _pipeline(self, state: CycleState, topic: dict, post_path: Path, human_note: str = "") -> CycleState:
        auto_revisions = 0
        previous_concern: SemanticConcern | None = None
        opened = engine.section_map(engine.parse_frontmatter(post_path.read_text(encoding="utf-8"))[1])
        mismatches = citation_mismatches(topic, opened.get("Claims", ""))
        if mismatches:
            state.stage = "plan"
            state.outcome = "PLAN_MISMATCH"
            state.error = " ".join(mismatches)
            save_cycle(self.content_root, state)
            raise CycleError("PLAN_MISMATCH", state.error)
        structural = claims_structure_error(opened.get("Claims", ""))
        if structural:
            state.stage = "compose"
            state.outcome = "SEMANTIC_FAILURE"
            state.error = structural
            save_cycle(self.content_root, state)
            raise CycleError("SEMANTIC_FAILURE", structural)
        while True:
            state.stage = "humanize"
            save_cycle(self.content_root, state)
            humanize_post(post_path)

            sections = engine.section_map(engine.parse_frontmatter(post_path.read_text(encoding="utf-8"))[1])
            body = sections["Body"]
            alignment = strategy_alignment_issues(body, topic)
            if alignment:
                capped = auto_revisions >= self.max_revisions
                if alignment_is_terminal(alignment) or capped:
                    detail = format_alignment_failure(
                        body,
                        alignment,
                        capped=capped and not alignment_is_terminal(alignment),
                    )
                    state.stage = "alignment"
                    state.outcome = "STRATEGY_MISALIGNED"
                    state.error = detail
                    save_cycle(self.content_root, state)
                    raise CycleError("STRATEGY_MISALIGNED", detail)
                state.stage = "alignment"
                save_cycle(self.content_root, state)
                revised = self.runtime.ask(
                    revise_prompt(body, alignment, topic=topic),
                    validate=cursor_runtime_mod.require_keys("body"),
                ).parsed
                apply_body_revision(post_path, str(revised["body"]))
                auto_revisions += 1
                state.revision_count = auto_revisions
                state.events.append(f"revision {auto_revisions}: alignment")
                continue

            state.stage = "semantic"
            save_cycle(self.content_root, state)
            post_text = post_path.read_text(encoding="utf-8")
            post_fields, post_rest = engine.parse_frontmatter(post_text)
            sections = engine.section_map(post_rest)
            structural = claims_structure_error(sections.get("Claims", ""))
            if structural:
                raise CycleError("SEMANTIC_FAILURE", structural)
            idea_id = str(post_fields.get("idea") or "")
            source_bundle = engine.load_source_bundle(self.content_root, idea_id) if idea_id else ""
            script_verdict, script_reasons = semantic_script_verdict(post_path)
            body = sections["Body"]
            research_excerpt = source_bundle.strip()[:12000]
            sem_prompt = f"""Semantic review this styled post body against the content contract.
Script verdict: {script_verdict}. Reasons: {script_reasons}.
Topic research for external attribution (not live verification; URLs do not prove sentences absent here):
---
{research_excerpt or "(no topic research bundle)"}
---
A framed architectural risk or engineering interpretation is not an observed security incident.
Do not treat a changelog URL as proof of a bypass the research does not state.
Shared words between a sentence and the topic research excerpt are not substantiation. Token overlap does not prove the assertion.
If a factual sentence contradicts the excerpt, or the excerpt does not state what the sentence asserts, return a semantic concern naming that sentence, what exceeds the excerpt, and a correction that stays inside the excerpt.
General engineering interpretation with no personal claim and no measured result may pass when it does not upgrade a design boundary into a deterministic causal identity (for example stating that a symptom is definitively a model problem or a dependency problem when the research only separates signals).
The script verdict is authoritative for what it checked. You may keep it or make it stricter on semantic grounds. Never weaken BLOCKED or NEEDS_EDIT to PASS.
If the script verdict is BLOCKED, return BLOCKED and repeat those reasons. Do not replace them with a demand for metrics, logs, or reproduction steps.
If the script verdict is NEEDS_EDIT, the evidence is usable and the wording is recoverable. Do not call that BLOCKED unless the sentence asserts a result the evidence does not support.
If the script reason is that a derived statement is not framed as a personal conclusion, the only repair is to add one of: My conclusion, I treat this as, The lesson I take. Do not ask for a different repair.
A conclusion frame on one derived sentence does not frame a different derived sentence. Do not request another frame when the affected sentence already contains My conclusion, I treat this as, or The lesson I take.
Semantic kind is only for unsupported personal or project outcomes, missing or incompatible evidence, evidence inflation, confidential or non-public claims, a derived interpretation presented as a measured result, a material claim past its qualifier, a derived sentence that is not framed as the author's conclusion, or an external or interpretive sentence that exceeds what the topic research states.
Editorial kind is for reader relevance, hook, contrast, readability, substance, repetition, unnecessary detail, or presentation. Editorial kind must not override a script PASS.
When verdict is PASS, omit concern. When verdict is NEEDS_EDIT or BLOCKED, include concern.
Return JSON: verdict (PASS|NEEDS_EDIT|BLOCKED), findings (string), concern (object with kind semantic|editorial, sentence, claim_id, exceeds, recoverable, correction).
claim_id may be empty. recoverable is true only when wording can fix the sentence without new facts. correction is the minimal wording change and must not invent information.
Body:
{body}
"""
            sem = self.runtime.ask(sem_prompt, validate=cursor_runtime_mod.require_keys("verdict", "findings")).parsed
            model_findings = str(sem.get("findings", ""))
            model_verdict = str(sem.get("verdict", script_verdict)).upper()
            concern = parse_semantic_concern(sem)
            final_sem, disposition = resolve_semantic_verdict(
                script_verdict,
                model_verdict,
                concern,
                model_findings,
            )
            retained = concern if disposition == "retain" else None
            findings = format_semantic_findings(script_reasons, model_findings, retained)
            if disposition == "undiagnosed":
                record_semantic_review(post_path, final_sem, findings)
                if final_sem == "BLOCKED":
                    detail = "semantic BLOCKED without a diagnosable claim"
                    state.outcome = "SEMANTIC_BLOCKED"
                else:
                    detail = "semantic NEEDS_EDIT was not diagnosable"
                if model_findings.strip():
                    detail += ": " + model_findings.strip()
                state.stage = "semantic"
                state.error = detail
                save_cycle(self.content_root, state)
                raise CycleError("SEMANTIC_FAILURE", detail)
            if final_sem == "BLOCKED":
                record_semantic_review(post_path, "BLOCKED", findings)
                detail = "semantic BLOCKED"
                extras: list[str] = []
                if script_reasons:
                    extras.append("; ".join(script_reasons))
                if retained and retained.complete():
                    extras.append(format_concern(retained))
                if extras:
                    detail += ": " + " ".join(extras)
                state.stage = "semantic"
                state.outcome = "SEMANTIC_BLOCKED"
                state.error = detail
                save_cycle(self.content_root, state)
                raise CycleError("SEMANTIC_FAILURE", detail)
            repeated_satisfied = (
                final_sem == "NEEDS_EDIT"
                and script_verdict == "PASS"
                and concern is not None
                and previous_concern is not None
                and concern.key() == previous_concern.key()
                and repair_applied(body, concern)
            )
            if repeated_satisfied and concern is not None:
                record_semantic_review(
                    post_path,
                    "PASS",
                    "Reviewer repeated a satisfied repair. " + format_concern(concern),
                )
                final_sem = "PASS"
            if final_sem == "NEEDS_EDIT":
                record_semantic_review(post_path, "NEEDS_EDIT", findings)
                if auto_revisions >= self.max_revisions:
                    raise CycleError(
                        "REVISION_EXHAUSTED",
                        semantic_exhaustion_detail(script_reasons, retained or concern, model_findings),
                    )
                revised = self.runtime.ask(
                    revise_prompt(
                        body,
                        semantic_revision_issues(script_reasons, model_findings, retained or concern),
                        human_note,
                        topic=topic,
                    ),
                    validate=cursor_runtime_mod.require_keys("body"),
                ).parsed
                new_body = str(revised["body"])
                if engine.body_hash(new_body) == engine.body_hash(body):
                    raise CycleError(
                        "REVISION_EXHAUSTED",
                        semantic_exhaustion_detail(
                            script_reasons,
                            retained or concern,
                            model_findings,
                            unchanged=True,
                        ),
                    )
                apply_body_revision(post_path, new_body)
                auto_revisions += 1
                state.revision_count = auto_revisions
                state.events.append(f"revision {auto_revisions}: semantic")
                if concern and concern.kind == "semantic" and concern.complete():
                    previous_concern = concern
                human_note = ""
                continue
            if not repeated_satisfied:
                note = model_findings.strip() or "PASS"
                if model_verdict in {"NEEDS_EDIT", "BLOCKED"}:
                    note = "PASS"
                record_semantic_review(post_path, "PASS", note)

            state.stage = "editorial"
            save_cycle(self.content_root, state)
            editorial = run_editorial(self.content_root, state.post_id)
            if editorial == "EVIDENCE_TOO_THIN":
                raise CycleError("EDITORIAL_FAILURE", "EVIDENCE_TOO_THIN")
            if editorial == "REVISE":
                sections = engine.section_map(engine.parse_frontmatter(post_path.read_text(encoding="utf-8"))[1])
                findings = editorial_mod.editorial_review(sections["Body"], sections.get("Evidence gaps", ""))
                issues = editorial_revision_issues(findings, topic)
                if auto_revisions >= self.max_revisions:
                    detail = "editorial REVISE after revision limit: " + " ".join(issues)
                    raise CycleError("REVISION_EXHAUSTED", detail)
                revised = self.runtime.ask(
                    revise_prompt(sections["Body"], issues, topic=topic),
                    validate=cursor_runtime_mod.require_keys("body"),
                ).parsed
                apply_body_revision(post_path, str(revised["body"]))
                auto_revisions += 1
                state.revision_count = auto_revisions
                state.events.append(f"revision {auto_revisions}: editorial")
                continue

            state.stage = "critic"
            save_cycle(self.content_root, state)
            sections = engine.section_map(engine.parse_frontmatter(post_path.read_text(encoding="utf-8"))[1])
            findings = run_critic(self.runtime, self.content_root, post_path, topic, sections["Body"])
            if findings.verdict == "DROP":
                raise CycleError("CRITIC_REJECTION", findings.reason or "critic DROP")
            if findings.verdict == "REVISE":
                if auto_revisions >= self.max_revisions:
                    detail = "critic REVISE after revision limit"
                    if findings.blocking_issues:
                        detail += ": " + " ".join(findings.blocking_issues)
                    raise CycleError("REVISION_EXHAUSTED", detail)
                issues = findings.blocking_issues + findings.revision_instructions
                revised = self.runtime.ask(
                    revise_prompt(sections["Body"], issues, topic=topic),
                    validate=cursor_runtime_mod.require_keys("body"),
                ).parsed
                apply_body_revision(post_path, str(revised["body"]))
                auto_revisions += 1
                state.revision_count = auto_revisions
                state.events.append(f"revision {auto_revisions}: critic")
                continue

            state.stage = "visual"
            save_cycle(self.content_root, state)
            run_visual_preview(self.content_root, state.post_id)

            ready, reasons = readiness_report(self.content_root, state.post_id)
            if ready:
                state.stage = "complete"
                state.outcome = OUTCOME_READY
                state.error = ""
            else:
                state.stage = "failed"
                state.outcome = "NOT_READY"
                state.error = "; ".join(reasons)
            save_cycle(self.content_root, state)
            return state


class FakeRuntime:
    """Deterministic runtime for unit tests."""

    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)
        self.calls = 0

    def ask(self, prompt: str, **kwargs) -> cursor_runtime_mod.AgentResult:
        if self.calls >= len(self.responses):
            raise cursor_runtime_mod.CursorRuntimeError("fake runtime exhausted")
        payload = self.responses[self.calls]
        self.calls += 1
        return cursor_runtime_mod.AgentResult(
            text=json.dumps(payload),
            parsed=payload,
            raw_envelope={"result": json.dumps(payload)},
        )


def cmd_run(args: argparse.Namespace) -> int:
    global ACTIVE_CONTENT_ROOT
    ACTIVE_CONTENT_ROOT = Path(args.content_root)
    engine.CONTENT = ACTIVE_CONTENT_ROOT
    pipeline_guard = cycle_guard_mod.CycleGuard(ACTIVE_CONTENT_ROOT)
    acquired, guard_error = pipeline_guard.acquire()
    if not acquired:
        print(
            json.dumps(
                {"outcome": cycle_guard_mod.SKIPPED_OUTCOME, "error": guard_error},
                ensure_ascii=False,
            )
        )
        return 1
    runtime: LLMRuntime
    if args.fake_responses:
        responses = json.loads(Path(args.fake_responses).read_text(encoding="utf-8"))
        runtime = FakeRuntime(responses)
    else:
        runtime = cursor_runtime_mod.CursorRuntime(repo_root=Path(args.repo_root))
    try:
        discovery = None
        try:
            # Programmatic callers that omit selection keep strategy selection; the CLI default is editorial.
            if getattr(args, "selection", "strategy") == "editorial" and not args.topic and not args.post_id:
                discovery = discovery_mod.DiscoverySettings.load(force_refresh=bool(getattr(args, "refresh_research", False)))
        except discovery_mod.DiscoveryError as exc:
            print(json.dumps({"outcome": exc.code, "error": str(exc)}, ensure_ascii=False))
            return 1
        orch = Orchestrator(
            Path(args.content_root),
            Path(args.repo_root),
            runtime,
            max_revisions=args.max_revisions,
            discovery=discovery,
        )
        try:
            state = orch.run(topic_name=args.topic, post_id=args.post_id, human_note=args.human_note or "")
        except CycleError as exc:
            print(json.dumps({"outcome": exc.code, "error": str(exc)}, ensure_ascii=False))
            return 1
        except cursor_runtime_mod.CursorRuntimeError as exc:
            print(json.dumps({"outcome": "RUNTIME_FAILURE", "error": str(exc)}, ensure_ascii=False))
            return 1
        ready, _ = readiness_report(Path(args.content_root), state.post_id)
        print(
            json.dumps(
                {
                    "post_id": state.post_id,
                    "topic": state.topic,
                    "lane": state.lane,
                    "audience": state.audience,
                    "stage": state.stage,
                    "revision_count": state.revision_count,
                    "outcome": state.outcome or (OUTCOME_READY if ready else "NOT_READY"),
                    "semantic": "PASS",
                    "editorial": "PASS",
                    "critic": "PASS",
                    "visual": "complete",
                    "preview": str(Path(args.content_root) / "previews" / f"{state.post_id}.html"),
                    "next_action": "inspect inbox; APPROVE sets human_editorial_decision only",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if state.outcome == OUTCOME_READY else 1
    finally:
        pipeline_guard.release()


def cmd_discover(args: argparse.Namespace) -> int:
    """Editorial decision only. No idea, plan, draft, or lifecycle change."""
    global ACTIVE_CONTENT_ROOT
    ACTIVE_CONTENT_ROOT = Path(args.content_root)
    engine.CONTENT = ACTIVE_CONTENT_ROOT
    pipeline_guard = cycle_guard_mod.CycleGuard(ACTIVE_CONTENT_ROOT)
    acquired, guard_error = pipeline_guard.acquire()
    if not acquired:
        print(
            json.dumps(
                {"outcome": cycle_guard_mod.SKIPPED_OUTCOME, "error": guard_error},
                ensure_ascii=False,
            )
        )
        return 1
    if args.fake_responses:
        runtime: LLMRuntime = FakeRuntime(json.loads(Path(args.fake_responses).read_text(encoding="utf-8")))
    else:
        runtime = cursor_runtime_mod.CursorRuntime(repo_root=Path(args.repo_root))
    try:
        try:
            settings = discovery_mod.DiscoverySettings.load(force_refresh=args.refresh_research)
            decision = editorial_selection(Path(args.content_root), runtime, settings)
        except (CycleError, discovery_mod.DiscoveryError) as exc:
            print(json.dumps({"outcome": getattr(exc, "code", "DISCOVERY_FAILURE"), "error": str(exc)}, ensure_ascii=False))
            return 1
        print(json.dumps(discovery_mod.decision_summary(decision), ensure_ascii=False, indent=2))
        return 0 if decision["outcome"] == "SELECTED" else 1
    finally:
        pipeline_guard.release()


def cmd_ready(args: argparse.Namespace) -> int:
    global ACTIVE_CONTENT_ROOT
    ACTIVE_CONTENT_ROOT = Path(args.content_root)
    engine.CONTENT = ACTIVE_CONTENT_ROOT
    ready, reasons = readiness_report(Path(args.content_root), args.post_id)
    print(OUTCOME_READY if ready else "NOT_READY")
    for reason in reasons:
        print(f"- {reason}")
    return 0 if ready else 1


def cmd_status(args: argparse.Namespace) -> int:
    global ACTIVE_CONTENT_ROOT
    ACTIVE_CONTENT_ROOT = Path(args.content_root)
    engine.CONTENT = ACTIVE_CONTENT_ROOT
    state = load_cycle(Path(args.content_root), args.post_id)
    ready, reasons = readiness_report(Path(args.content_root), args.post_id)
    payload = {
        "post_id": args.post_id,
        "cycle": state.__dict__ if state else None,
        "ready": ready,
        "reasons": reasons,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Headless LinkedIn content cycle.")
    parser.add_argument("--content-root", default=str(ROOT / "content"))
    parser.add_argument("--repo-root", default=str(ROOT))
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run full cycle")
    run.add_argument("--topic")
    run.add_argument("--post-id")
    run.add_argument("--human-note", default="")
    run.add_argument("--max-revisions", type=int, default=MAX_REVISION_CYCLES)
    run.add_argument("--fake-responses", help="JSON file of fake LLM responses for tests")
    run.add_argument(
        "--selection",
        choices=("editorial", "strategy"),
        default="editorial",
        help="editorial: sourced research plus assessment; strategy: latest strategy file and lane mix only",
    )
    run.add_argument("--refresh-research", action="store_true", help="refetch sources even if the snapshot is fresh")
    run.set_defaults(func=cmd_run)
    discover = sub.add_parser("discover", help="refresh or reuse research and record an editorial decision; no draft")
    discover.add_argument("--refresh-research", action="store_true")
    discover.add_argument("--fake-responses")
    discover.set_defaults(func=cmd_discover)
    resume = sub.add_parser("resume", help="resume cycle for one post")
    resume.add_argument("post_id")
    resume.add_argument("--topic", default=None)
    resume.add_argument("--human-note", default="")
    resume.add_argument("--fake-responses")
    resume.add_argument("--max-revisions", type=int, default=MAX_REVISION_CYCLES)

    def cmd_resume(args: argparse.Namespace) -> int:
        return cmd_run(args)

    resume.set_defaults(func=cmd_resume)
    ready = sub.add_parser("ready", help="print readiness")
    ready.add_argument("post_id")
    ready.set_defaults(func=cmd_ready)
    status = sub.add_parser("status", help="cycle status")
    status.add_argument("post_id")
    status.set_defaults(func=cmd_status)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
