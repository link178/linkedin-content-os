"""Local Review Inbox for content cycle candidates. Stdlib only."""

from __future__ import annotations

import html
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import url2pathname

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import content_engine as engine  # noqa: E402
import critic as critic_mod  # noqa: E402
import cycle as cycle_mod  # noqa: E402
import cycle_guard as cycle_guard_mod  # noqa: E402
import ops_runs as ops_mod  # noqa: E402
import strategy_check as strategy_mod  # noqa: E402

POST_ID_RE = re.compile(r"^POST-\d{8}-\d{2}$")
DEFAULT_PORT = 8765
JOB_FILENAME = "inbox-job.json"
LOG_FILENAME = "inbox-run.log"
ALLOWED_ASSET_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
ASSET_ATTR_RE = re.compile(
    r"""(?P<attr>\b(?:src|href))=(?P<q>["'])(?P<url>[^"']+)(?P=q)""",
    re.IGNORECASE,
)
PREVIEW_FIT = (
    '<style id="inbox-preview-fit">'
    ".wrap{max-width:640px;margin-left:auto;margin-right:auto;}"
    ".content p{font-size:16px;line-height:1.5;}"
    ".meta .name{font-size:16px;}"
    ".meta .headline{font-size:13px;}"
    ".banner{font-size:15px;}"
    "</style>"
)
PAGE_STYLE = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { margin: 0; font-family: "Segoe UI", system-ui, sans-serif; font-size: 16px; line-height: 1.45; background: #f4f2ef; color: #1c1c1c; }
a { color: #0a66c2; }
main { max-width: 1120px; margin: 0 auto; padding: 20px 24px 64px; }
h1 { font-size: 28px; line-height: 1.2; margin: 0 0 8px; }
h2 { font-size: 18px; margin: 0 0 12px; }
h3 { font-size: 15px; margin: 14px 0 6px; }
.lede, .hint { color: #444; }
.lede { max-width: 70ch; margin: 0 0 18px; }
.panel { background: #fff; border: 1px solid #e2ddd6; border-radius: 10px; padding: 16px 18px; margin: 0 0 16px; }
.row { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
button { font: inherit; font-size: 15px; background: #0a66c2; color: #fff; border: 0; border-radius: 999px; padding: 8px 16px; cursor: pointer; }
button.secondary { background: #fff; color: #1c1c1c; border: 1px solid #c8c4be; }
button.warn { background: #8a3b12; }
button:disabled { background: #c5c1ba; color: #fff; cursor: not-allowed; }
table { width: 100%; border-collapse: collapse; }
th, td { text-align: left; padding: 10px 8px; border-bottom: 1px solid #eee; vertical-align: top; }
th { font-size: 13px; color: #555; font-weight: 650; }
.badge { display: inline-block; font-size: 13px; font-weight: 650; padding: 2px 8px; border-radius: 999px; background: #eee; }
.badge.ready { background: #e5f6e8; color: #146c2e; }
.badge.blocked { background: #fde8e4; color: #8a1f11; }
.banner { padding: 10px 12px; border-radius: 8px; background: #eef5fc; margin: 0 0 16px; }
.banner.error, .error { background: #fde8e4; color: #8a1f11; }
.split { display: grid; grid-template-columns: minmax(0, 680px) minmax(260px, 340px); gap: 20px; align-items: start; }
.preview-frame { width: 100%; max-width: 680px; background: #f3f2ef; border: 1px solid #e2ddd6; border-radius: 10px; overflow: auto; }
.preview-frame iframe { width: 100%; height: 1280px; border: 0; display: block; background: #f3f2ef; }
.actions { position: sticky; top: 12px; }
textarea { width: 100%; font: inherit; font-size: 15px; padding: 8px; border-radius: 8px; border: 1px solid #c8c4be; margin: 6px 0 10px; }
label { display: block; font-size: 14px; font-weight: 650; margin-top: 8px; }
.post-body { white-space: pre-wrap; font-size: 16px; line-height: 1.5; margin: 0; }
dl { display: grid; grid-template-columns: 150px 1fr; gap: 6px 12px; margin: 0; }
dt { color: #555; }
dd { margin: 0; }
ul.clean { margin: 8px 0 0; padding-left: 18px; }
details { margin-top: 8px; }
pre.diagnostics { overflow: auto; max-height: 280px; background: #f7f7f7; padding: 12px; font-size: 13px; }
.back { display: inline-block; margin-bottom: 8px; }
@media (max-width: 860px) {
  .split { grid-template-columns: 1fr; }
  .actions { position: static; }
  .preview-frame iframe { height: 75vh; min-height: 560px; }
  main { padding: 16px 14px 40px; }
  dl { grid-template-columns: 1fr; }
}
"""


class InboxState:
    csrf_token: str = secrets.token_urlsafe(32)


_generation_guard = threading.Lock()


def list_candidates(content_root: Path) -> list[str]:
    ids: set[str] = set()
    cycles = content_root / cycle_mod.CYCLES_DIR
    if cycles.is_dir():
        for path in cycles.glob("POST-*.yaml"):
            ids.add(path.stem)
    drafts = content_root / "drafts"
    if drafts.is_dir():
        for path in drafts.glob("POST-*.md"):
            try:
                fields, _ = engine.parse_frontmatter(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if fields.get("critic_verdict") or fields.get("editorial_verdict"):
                ids.add(path.stem)
    return sorted(ids, reverse=True)


def _idea_fields(content_root: Path, idea_id: str) -> dict:
    if not idea_id or not re.fullmatch(r"IDEA-\d{8}-\d{2}", idea_id):
        return {}
    path = content_root / "ideas" / f"{idea_id}.md"
    if not path.is_file():
        return {}
    try:
        fields, _ = engine.parse_frontmatter(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return fields


def _editorial_notes(section: str, verdict: str) -> list[str]:
    if not section.strip() or verdict == "PASS":
        return []
    notes: list[str] = []
    if verdict:
        notes.append(f"Editorial verdict: {verdict}")
    lines = section.splitlines()
    labels = ("Weakest element:", "Suggested structural change:", "Too generic:", "Missing concrete detail:")
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped not in labels:
            continue
        value = ""
        for follow in lines[index + 1 :]:
            follow = follow.strip()
            if follow:
                value = follow
                break
        if value and value.lower() != "none flagged.":
            notes.append(f"{stripped[:-1]}: {value}")
    return notes


def _blocking_issues(fields: dict, sections: dict) -> list[str]:
    issues: list[str] = []
    recorded = fields.get("review_verdict", "")
    semantic_stop = recorded in {"BLOCKED", "NEEDS_EDIT"}
    if recorded and recorded != "PASS":
        try:
            script_verdict, reasons = cycle_mod.semantic_diagnostics(sections, engine.load_claims())
        except (OSError, RuntimeError, ValueError):
            script_verdict, reasons = "", []
        if reasons and (recorded == "BLOCKED" or script_verdict == "BLOCKED"):
            issues.extend(f"unsupported claim: {reason}" for reason in reasons)
        elif recorded == "BLOCKED":
            issues.append("unsupported claim: semantic review is BLOCKED")
        elif recorded == "NEEDS_EDIT":
            if reasons:
                issues.extend(f"recoverable wording: {reason}" for reason in reasons)
            else:
                issues.append("recoverable wording: semantic NEEDS_EDIT")
        else:
            issues.append(f"semantic verdict is {recorded}")
        note = sections.get("Review", "").strip()
        if note:
            issues.append(f"recorded reviewer note: {note}")
    critic_text = sections.get("Critic review", "")
    parsed = critic_mod.parse_critic_section(critic_text) if critic_text.strip() else None
    if not fields.get("critic_verdict"):
        if not semantic_stop:
            issues.append("critic review missing")
    elif parsed:
        issues.extend(parsed.blocking_issues)
        if parsed.verdict in {"REVISE", "DROP"} and parsed.reason and parsed.reason.lower() != "none stated.":
            issues.append(parsed.reason)
    issues.extend(_editorial_notes(sections.get("Editorial review", ""), fields.get("editorial_verdict", "")))
    return issues


def candidate_summary(content_root: Path, post_id: str) -> dict:
    cycle_mod.ACTIVE_CONTENT_ROOT = content_root
    engine.CONTENT = content_root
    state = cycle_mod.load_cycle(content_root, post_id)
    ready, reasons = cycle_mod.readiness_report(content_root, post_id)
    post_fields: dict = {}
    sections: dict = {}
    try:
        post_path = cycle_mod.load_post_draft(content_root, post_id)
        post_fields, rest = engine.parse_frontmatter(post_path.read_text(encoding="utf-8"))
        sections = engine.section_map(rest)
    except cycle_mod.CycleError:
        post_path = None
    idea = _idea_fields(content_root, str(post_fields.get("idea", "")))
    lane = (state.lane if state else "") or str(post_fields.get("lane", "") or idea.get("lane", ""))
    audience = (state.audience if state else "") or str(post_fields.get("audience", "") or idea.get("audience", ""))
    preview_path = content_root / "previews" / f"{post_id}.html"
    lock = (content_root / cycle_mod.CYCLES_DIR / f"{post_id}{cycle_mod.LOCK_SUFFIX}").exists()
    revision_history = cycle_mod.human_revision_history_view(state, post_fields, sections)
    return {
        "post_id": post_id,
        "topic": state.topic if state else "",
        "lane": lane,
        "audience": audience,
        "stage": state.stage if state else "",
        "outcome": state.outcome if state else (cycle_mod.OUTCOME_READY if ready else "NOT_READY"),
        "error": state.error if state else "",
        "ready": ready,
        "reasons": reasons,
        "review_verdict": post_fields.get("review_verdict", ""),
        "editorial_verdict": post_fields.get("editorial_verdict", ""),
        "critic_verdict": post_fields.get("critic_verdict", ""),
        "human_decision": post_fields.get("human_editorial_decision", ""),
        "body": sections.get("Body", "").strip(),
        "blocking_issues": _blocking_issues(post_fields, sections),
        "critic_section": sections.get("Critic review", ""),
        "human_revision": sections.get("Human revision request", ""),
        "revision_history": revision_history,
        "body_sha256": post_fields.get("body_sha256", ""),
        "preview_exists": preview_path.is_file(),
        "lock": lock,
    }


def empty_job() -> dict:
    return {
        "status": "idle",
        "command": "",
        "post_id": "",
        "error": "",
        "message": "",
        "pid": 0,
        "log": "",
        "started_at": "",
        "updated_at": "",
    }


def job_path(content_root: Path) -> Path:
    return content_root / cycle_mod.CYCLES_DIR / JOB_FILENAME


def load_job(content_root: Path) -> dict:
    path = job_path(content_root)
    if not path.is_file():
        return empty_job()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        job = empty_job()
        job["status"] = "error"
        job["error"] = "generation status file is unreadable"
        return job
    job = empty_job()
    if isinstance(data, dict):
        job.update({key: data.get(key, job[key]) for key in job})
    return job


def save_job(content_root: Path, job: dict) -> None:
    engine.write_atomic(job_path(content_root), json.dumps(job, indent=2) + "\n")


def extract_last_json_object(text: str) -> dict | None:
    decoder = json.JSONDecoder()
    found: dict | None = None
    index = 0
    while True:
        start = text.find("{", index)
        if start < 0:
            return found
        try:
            obj, _end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            index = start + 1
            continue
        if isinstance(obj, dict):
            found = obj
        index = start + 1


def refresh_job(content_root: Path) -> dict:
    job = load_job(content_root)
    if job.get("status") != "running":
        return job
    pid = int(job.get("pid") or 0)
    if cycle_mod.pid_alive(pid):
        return job
    log_file = Path(str(job.get("log") or ""))
    text = ""
    if log_file.is_file():
        text = log_file.read_text(encoding="utf-8", errors="replace")
    payload = extract_last_json_object(text) or {}
    if payload.get("post_id"):
        job["status"] = "success"
        job["post_id"] = str(payload.get("post_id") or "")
        job["message"] = str(payload.get("outcome") or "completed")
        job["error"] = str(payload.get("error") or "")
    elif payload.get("error") or payload.get("outcome"):
        job["status"] = "error"
        job["message"] = str(payload.get("outcome") or "failed")
        job["error"] = str(payload.get("error") or payload.get("outcome") or "generation failed")
    else:
        job["status"] = "error"
        job["error"] = (text.strip()[-500:] if text.strip() else "generation ended without a status report")
    job["updated_at"] = cycle_mod.utc_now()
    save_job(content_root, job)
    return job


def set_human_decision(content_root: Path, post_id: str, decision: str, feedback: str = "") -> tuple[bool, str]:
    if decision not in strategy_mod.HUMAN_EDITORIAL_DECISIONS:
        return False, "invalid decision"
    post_path = cycle_mod.load_post_draft(content_root, post_id)
    text = post_path.read_text(encoding="utf-8")
    fields, rest = engine.parse_frontmatter(text)
    sections = engine.section_map(rest)
    fields["human_editorial_decision"] = decision
    if feedback and decision == "REVISE":
        sections["Human revision request"] = feedback.strip() + "\n"
    engine.write_atomic(post_path, engine.render_frontmatter(fields, engine.POST_FIELD_ORDER) + cycle_mod.rebuild_post_rest(sections))
    if decision in {"DROP", "REVISE"}:
        state = cycle_mod.load_cycle(content_root, post_id) or cycle_mod.CycleState(
            post_id=post_id, topic="", lane="", audience=""
        )
        if decision == "DROP":
            state.events.append(f"dropped: {feedback.strip()}")
            state.outcome = "DROPPED"
        else:
            state.outcome = "NOT_READY"
            state.error = ""
            pre_hash = str(fields.get("body_sha256") or "")
            if not pre_hash:
                body = sections.get("Body", "")
                if body:
                    pre_hash = engine.body_hash(body)
            cycle_mod.append_pending_human_revision(state, feedback, pre_hash)
        cycle_mod.save_cycle(content_root, state)
    return True, decision


def spawn_cycle(
    content_root: Path,
    command: str,
    post_id: str = "",
    human_note: str = "",
    log_path: Path | None = None,
) -> subprocess.Popen:
    args = [
        sys.executable,
        str(SCRIPTS / "cycle.py"),
        "--content-root",
        str(content_root),
        "--repo-root",
        str(ROOT),
        command,
    ]
    if command == "resume":
        args.append(post_id)
        if human_note:
            args.extend(["--human-note", human_note])
    else:
        if post_id:
            args.extend(["--post-id", post_id])
        if human_note:
            args.extend(["--human-note", human_note])
    env = os.environ.copy()
    env["CONTENT_ROOT"] = str(content_root)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    stdout = subprocess.DEVNULL
    stderr = subprocess.DEVNULL
    handle = None
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handle = log_path.open("w", encoding="utf-8")
        stdout = handle
        stderr = subprocess.STDOUT
    try:
        return subprocess.Popen(
            args,
            cwd=str(ROOT),
            env=env,
            stdout=stdout,
            stderr=stderr,
            creationflags=creationflags,
        )
    finally:
        if handle is not None:
            handle.close()


def start_generation(content_root: Path, command: str, post_id: str = "", human_note: str = "") -> tuple[bool, str]:
    with _generation_guard:
        current = refresh_job(content_root)
        if current.get("status") == "running":
            return False, "A generation is already running."
        if command == "resume" and post_id and _cycle_lock_held(content_root, post_id):
            return False, "A cycle is already running for this candidate."
        if cycle_guard_mod.is_busy(content_root):
            return False, "A generation is already running."
        log_path = content_root / cycle_mod.CYCLES_DIR / LOG_FILENAME
        try:
            proc = spawn_cycle(content_root, command, post_id=post_id, human_note=human_note, log_path=log_path)
        except OSError as exc:
            job = empty_job()
            job.update(
                {
                    "status": "error",
                    "command": command,
                    "post_id": post_id,
                    "error": str(exc),
                    "updated_at": cycle_mod.utc_now(),
                }
            )
            save_job(content_root, job)
            return False, str(exc)
        job = empty_job()
        job.update(
            {
                "status": "running",
                "command": command,
                "post_id": post_id,
                "pid": proc.pid,
                "log": str(log_path),
                "started_at": cycle_mod.utc_now(),
                "updated_at": cycle_mod.utc_now(),
                "message": "started",
            }
        )
        save_job(content_root, job)
        return True, ""


def path_from_file_url(url: str) -> Path | None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "file":
        return None
    raw = url2pathname(urllib.parse.unquote(parsed.path))
    if os.name == "nt" and raw.startswith("\\") and len(raw) > 3 and raw[2] == ":":
        raw = raw.lstrip("\\")
    return Path(raw)


def public_asset_url(path: Path, content_root: Path) -> str | None:
    try:
        resolved = path.resolve()
    except OSError:
        return None
    if not resolved.is_file() or resolved.suffix.lower() not in ALLOWED_ASSET_SUFFIXES:
        return None
    bases = (("content", content_root.resolve()), ("repo", ROOT.resolve()))
    for kind, base in bases:
        if resolved.is_relative_to(base):
            rel = resolved.relative_to(base).as_posix()
            if ".." in rel.split("/"):
                return None
            return f"/local-asset/{kind}/{urllib.parse.quote(rel, safe='/')}"
    return None


def rewrite_asset_url(url: str, preview_file: Path, content_root: Path) -> str | None:
    url = url.strip()
    if not url or url.startswith(("data:", "mailto:", "#")):
        return url
    if url.startswith(("http://", "https://")):
        return url
    if url.startswith("file:"):
        local = path_from_file_url(url)
        if local is None:
            return None
        return public_asset_url(local, content_root)
    if url.startswith("/"):
        if url.startswith("/local-asset/") or url.startswith("/preview/"):
            return url
        return None
    return public_asset_url((preview_file.parent / url).resolve(), content_root)


def sanitize_preview_html(text: str, preview_file: Path, content_root: Path) -> str:
    def repl(match: re.Match[str]) -> str:
        rewritten = rewrite_asset_url(match.group("url"), preview_file, content_root)
        if rewritten is None:
            return ""
        if rewritten == match.group("url"):
            return match.group(0)
        quoted = html.escape(rewritten, quote=True)
        return f"{match.group('attr')}={match.group('q')}{quoted}{match.group('q')}"

    return ASSET_ATTR_RE.sub(repl, text)


def prepare_preview_document(text: str, preview_file: Path, content_root: Path) -> str:
    cleaned = sanitize_preview_html(text, preview_file, content_root)
    if 'id="inbox-preview-fit"' in cleaned:
        return cleaned
    if "</head>" in cleaned:
        return cleaned.replace("</head>", PREVIEW_FIT + "</head>", 1)
    return PREVIEW_FIT + cleaned


def _esc(value: object) -> str:
    return html.escape("" if value is None else str(value))


def _dash(value: str) -> str:
    return _esc(value) if value else "—"


def page_shell(title: str, inner: str, *, refresh: bool = False) -> str:
    refresh_tag = '<meta http-equiv="refresh" content="4"/>' if refresh else ""
    return (
        "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n"
        '<meta charset="utf-8"/>\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1"/>\n'
        f"<title>{_esc(title)}</title>\n"
        f"{refresh_tag}\n"
        f"<style>{PAGE_STYLE}</style>\n"
        "</head>\n<body>\n<main>\n"
        f"{inner}\n"
        "</main>\n</body>\n</html>\n"
    )


def _banner(notice: str, error: str) -> str:
    parts: list[str] = []
    notices = {
        "started": "Generation started. This page updates while it runs.",
        "approved": "Human editorial decision set to APPROVE. Lifecycle approval remains python scripts/content_engine.py approve.",
        "revised": "Revision feedback saved. Resume started.",
        "dropped": "Human editorial decision set to DROP.",
    }
    if notice in notices:
        parts.append(f'<p class="banner">{_esc(notices[notice])}</p>')
    if error:
        parts.append(f'<p class="banner error">{_esc(error)}</p>')
    return "".join(parts)


def _csrf_input() -> str:
    return f'<input type="hidden" name="csrf" value="{_esc(InboxState.csrf_token)}"/>'


def render_scheduled_runs(content_root: Path) -> str:
    ops_mod.reconcile_interrupted(content_root)
    records = ops_mod.load_records(content_root, limit=80)
    if not records:
        return (
            '<section class="panel"><h2>Scheduled runs</h2>'
            '<p class="hint">No scheduled runs recorded yet. '
            "Weekday automation uses scripts/scheduled_cycle.py via Task Scheduler.</p></section>"
        )
    rows: list[str] = []
    for record in records:
        if record.get("trigger") != ops_mod.TRIGGER_SCHEDULED:
            continue
        started = _dash(str(record.get("local_started") or record.get("started_at") or ""))
        classification = str(record.get("classification") or record.get("outcome") or "")
        label = _esc(ops_mod.human_label(classification))
        post_id = str(record.get("post_id") or "")
        candidate = (
            f' <a href="/candidate/{_esc(post_id)}">{_esc(post_id)}</a>'
            if POST_ID_RE.fullmatch(post_id)
            else ""
        )
        duration = record.get("duration_sec")
        dur = f", {duration}s" if isinstance(duration, int) and duration >= 0 else ""
        rows.append(
            f"<tr><td>{started}</td><td>{label}{candidate}</td>"
            f"<td>{_esc(record.get('run_id') or '')}{_esc(dur)}</td></tr>"
        )
    if not rows:
        return (
            '<section class="panel"><h2>Scheduled runs</h2>'
            '<p class="hint">No scheduled runs recorded yet.</p></section>'
        )
    body = "".join(rows)
    return (
        '<section class="panel"><h2>Scheduled runs</h2>'
        "<table><thead><tr><th>Started</th><th>Outcome</th><th>Run</th></tr></thead>"
        f"<tbody>{body}</tbody></table></section>"
    )


def render_generation(job: dict) -> str:
    status = str(job.get("status") or "idle")
    post_id = str(job.get("post_id") or "")
    summary = ops_mod.generation_job_summary(job)
    if status == "success" and POST_ID_RE.fullmatch(post_id):
        link = f'<a href="/candidate/{_esc(post_id)}">{_esc(post_id)}</a>'
        outcome = str(job.get("message") or "")
        label = ops_mod.human_label(outcome) if outcome else "completed"
        text = f"Generation finished: {link}. {_esc(label)}"
    else:
        text = _esc(summary)
    error = ""
    if status == "error":
        detail = str(job.get("error") or job.get("message") or "")
        if detail:
            error = f'<p class="error">{_esc(detail)}</p>'
    elif status == "success" and job.get("error"):
        error = f'<p class="error">{_esc(job.get("error"))}</p>'
    return (
        '<section class="panel" id="generation"><h2>Generation</h2>'
        '<form method="post" action="/generate">'
        f"{_csrf_input()}"
        '<button type="submit">Generate Candidate</button></form>'
        f'<p id="generation-status" class="status" data-status="{_esc(status)}">{text}</p>'
        f"{error}</section>"
    )


def render_dashboard(content_root: Path, *, notice: str = "", error: str = "") -> str:
    job = refresh_job(content_root)
    rows: list[str] = []
    for post_id in list_candidates(content_root):
        summary = candidate_summary(content_root, post_id)
        label = "READY_FOR_HUMAN" if summary["ready"] else "NOT_READY"
        badge = "ready" if summary["ready"] else "blocked"
        extra = f'<div class="hint">{_esc(summary["error"])}</div>' if summary.get("error") else ""
        rows.append(
            "<tr>"
            f'<td><a href="/candidate/{_esc(post_id)}">{_esc(post_id)}</a></td>'
            f"<td>{_dash(summary['lane'])}</td>"
            f"<td>{_dash(summary['audience'])}</td>"
            f'<td><span class="badge {badge}">{label}</span>{extra}</td>'
            "</tr>"
        )
    body_rows = "".join(rows) if rows else '<tr><td colspan="4">No candidates yet.</td></tr>'
    inner = (
        "<h1>Review Inbox</h1>"
        '<p class="lede">Generate a candidate, inspect readiness, and record a human editorial decision. '
        "Lifecycle approval stays a separate command.</p>"
        f"{_banner(notice, error)}"
        f"{render_generation(job)}"
        f"{render_scheduled_runs(content_root)}"
        '<section class="panel"><h2>Candidates</h2>'
        "<table><thead><tr><th>Candidate</th><th>Strategy lane</th><th>Audience</th><th>Readiness</th></tr></thead>"
        f"<tbody>{body_rows}</tbody></table></section>"
    )
    return page_shell("Review Inbox", inner, refresh=job.get("status") == "running")


def _issue_list(items: list[str], empty: str) -> str:
    if not items:
        return f'<p class="hint">{_esc(empty)}</p>'
    listed = "".join(f"<li>{_esc(item)}</li>" for item in items)
    return f'<ul class="clean">{listed}</ul>'


def _render_reason_groups(reasons: list[str]) -> str:
    groups = (
        ("Unsupported factual claims", "unsupported claim: "),
        ("Recoverable wording", "recoverable wording: "),
        ("Skipped gates", "skipped gate: "),
        ("Invalid artifact structure", "invalid artifact: "),
    )
    used: set[str] = set()
    parts: list[str] = []
    for title, prefix in groups:
        items = [item for item in reasons if item.startswith(prefix)]
        used.update(items)
        if items:
            parts.append(f"<h3>{_esc(title)}</h3>{_issue_list(items, '')}")
    rest = [item for item in reasons if item not in used]
    if rest:
        parts.append(f"<h3>Other gates</h3>{_issue_list(rest, '')}")
    if not parts:
        return '<p class="hint">Derived readiness is READY_FOR_HUMAN.</p>'
    return "".join(parts)


def _human_decision_cell(history: dict, raw_decision: str) -> str:
    if raw_decision:
        return _dash(raw_decision)
    if history.get("consumed_human_revise"):
        return _esc("Awaiting human decision")
    return "—"


def render_revision_history(summary: dict) -> str:
    history = summary.get("revision_history") or {}
    entries: list[dict] = list(history.get("entries") or [])
    auto = list(history.get("automatic_revisions") or [])
    count = int(history.get("human_requested_revision_count") or 0)
    parts: list[str] = [
        "<dl>",
        f"<dt>Human-requested revisions</dt><dd>{count}</dd>",
    ]
    follows = history.get("follows_human_revision")
    if count:
        parts.append(
            f"<dt>Current body after human feedback</dt><dd>{_esc('yes' if follows else 'no')}</dd>"
        )
    if auto:
        parts.append(f"<dt>Automatic revisions</dt><dd>{_esc(', '.join(auto))}</dd>")
    parts.append("</dl>")
    if not entries:
        parts.append('<p class="hint">No human-requested revisions recorded.</p>')
    else:
        blocks: list[str] = []
        for index, entry in enumerate(entries, start=1):
            status = str(entry.get("status") or "unknown")
            timestamp = str(entry.get("requested_at") or entry.get("applied_at") or "").strip()
            ts_label = _esc(timestamp) if timestamp else "not recorded"
            feedback = str(entry.get("feedback") or "").strip()
            truncated = bool(entry.get("feedback_truncated"))
            seq = entry.get("sequence")
            version = f"revision {seq}" if seq else f"human revision {index}"
            post_hash = str(entry.get("post_body_sha256") or "")
            hash_bit = f", body {_esc(post_hash[:12])}…" if post_hash else ""
            reviews = entry.get("reviews") or {}
            review_bits: list[str] = []
            for key in ("semantic", "editorial", "critic"):
                val = str(reviews.get(key) or "").strip()
                if val:
                    review_bits.append(f"{key} {val}")
            review_line = ", ".join(review_bits) if review_bits else "—"
            current = "yes" if entry.get("is_current_version") else "no"
            blocks.append(
                "<div class=\"panel\" style=\"margin:12px 0;padding:12px;border-style:dashed\">"
                f"<h3>Human revision {index}</h3><dl>"
                f"<dt>Requested</dt><dd>{ts_label}</dd>"
                f"<dt>Decision</dt><dd>REVISE</dd>"
                f"<dt>Status</dt><dd>{_esc(status)}</dd>"
                f"<dt>Resulting version</dt><dd>{_esc(version)}{hash_bit}</dd>"
                f"<dt>Review outcomes</dt><dd>{_esc(review_line)}</dd>"
                f"<dt>Current version</dt><dd>{_esc(current)}</dd>"
                "</dl>"
                + (
                    f"<p><strong>Feedback</strong></p><pre class=\"post-body\">{_esc(feedback)}</pre>"
                    + (
                        '<p class="hint">Stored feedback is truncated in the cycle log.</p>' if truncated else ""
                    )
                    if feedback
                    else "<p class=\"hint\">Feedback not recorded.</p>"
                )
                + (
                    "<p class=\"hint\">Previous body text was not retained.</p>"
                    if status == "applied" and not post_hash
                    else ""
                )
                + "</div>"
            )
        parts.append("".join(blocks))
    body = "".join(parts)
    return (
        '<section class="panel"><details open><summary>Revision history</summary>'
        f"{body}</details></section>"
    )


def render_selection(content_root: Path, post_id: str) -> str:
    path = cycle_mod.selection_path(content_root, post_id)
    if not path.is_file():
        return ""
    try:
        decision = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return '<section class="panel"><h2>Editorial selection</h2><p class="hint">Selection record unreadable.</p></section>'
    selected = decision.get("selected") or {}
    research = decision.get("research") or {}
    dims = selected.get("dims") or {}
    reasons = _issue_list([str(item) for item in selected.get("why_selected") or []], "No reason recorded.")
    sources = _issue_list(
        [f"{s.get('name')} ({str(s.get('published', ''))[:10]}) {s.get('url')}" for s in selected.get("sources") or []],
        "No external source: evergreen strategy topic.",
    )
    others = [
        f"{entry.get('id')} {entry.get('status')} {entry.get('title')} — {', '.join(entry.get('codes') or []) or 'eligible'}"
        for entry in decision.get("candidates") or []
        if entry.get("id") != selected.get("id")
    ]
    legacy = decision.get("legacy_lane_choice") or {}
    return (
        '<section class="panel"><h2>Editorial selection</h2><dl>'
        f"<dt>Selected</dt><dd>{_esc(selected.get('id'))} {_esc(selected.get('kind'))} — {_esc(selected.get('title'))}</dd>"
        f"<dt>Category</dt><dd>{_dash(selected.get('category'))}</dd>"
        f"<dt>Assessment</dt><dd>{_esc(', '.join(f'{k} {v}' for k, v in dims.items()))}</dd>"
        f"<dt>Research</dt><dd>{_esc(research.get('snapshot_id') or 'none')} status {_esc(research.get('status'))}, "
        f"{_esc(research.get('mode'))}, retrieved {_dash(research.get('retrieved_at'))}</dd>"
        f"<dt>Lane-mix choice</dt><dd>{_dash(legacy.get('title'))}</dd>"
        "</dl>"
        f"<h3>Why it was chosen</h3>{reasons}<h3>Sources</h3>{sources}"
        f"<details><summary>Other candidates ({len(others)})</summary>{_issue_list(others, 'none')}</details>"
        "</section>"
    )


def render_candidate(content_root: Path, post_id: str, *, notice: str = "", error: str = "") -> str:
    summary = candidate_summary(content_root, post_id)
    job = refresh_job(content_root)
    if not error and job.get("status") == "error" and str(job.get("post_id") or "") == post_id:
        job_error = str(job.get("error") or "").strip()
        if job_error:
            error = job_error
    ready = bool(summary["ready"])
    label = "READY_FOR_HUMAN" if ready else "NOT_READY"
    approve_disabled = "" if ready else " disabled"
    if summary["preview_exists"]:
        preview = (
            '<div class="preview-frame">'
            f'<iframe title="LinkedIn preview { _esc(post_id) }" src="/preview/{_esc(post_id)}" scrolling="yes"></iframe>'
            "</div>"
        )
    else:
        preview = '<p class="hint">No preview file yet.</p>'
    revision = ""
    if summary.get("human_revision"):
        revision = (
            '<section class="panel"><h2>Revision request</h2>'
            f'<pre class="post-body">{_esc(summary["human_revision"].strip())}</pre></section>'
        )
    diagnostics = html.escape(json.dumps(summary, indent=2))
    reasons = summary["reasons"] if not ready else []
    history = summary.get("revision_history") or {}
    human_decision_extra = ""
    if history.get("consumed_human_revise"):
        human_decision_extra = (
            f"<dt>Previous human decision</dt><dd>REVISE</dd>"
            f"<dt>Current version</dt><dd>{_esc(history.get('current_version_status') or 'Awaiting human decision')}</dd>"
        )
    inner = (
        f'<a class="back" href="/">Review Inbox</a>'
        f"<h1>{_esc(post_id)}</h1>"
        f"{_banner(notice, error)}"
        '<section class="panel"><h2>Readiness</h2>'
        f'<p><span class="badge {"ready" if ready else "blocked"}">{label}</span></p>'
        f"{_render_reason_groups(reasons)}"
        "</section>"
        '<section class="panel"><h2>Verdicts</h2><dl>'
        f"<dt>Semantic</dt><dd>{_dash(summary['review_verdict'])}</dd>"
        f"<dt>Editorial</dt><dd>{_dash(summary['editorial_verdict'])}</dd>"
        f"<dt>Critic</dt><dd>{_dash(summary['critic_verdict'])}</dd>"
        f"<dt>Human decision</dt><dd>{_human_decision_cell(history, str(summary.get('human_decision') or ''))}</dd>"
        f"{human_decision_extra}"
        f"<dt>Human-requested revisions</dt><dd>{int(history.get('human_requested_revision_count') or 0)}</dd>"
        f"<dt>Lane</dt><dd>{_dash(summary['lane'])}</dd>"
        f"<dt>Audience</dt><dd>{_dash(summary['audience'])}</dd>"
        "</dl></section>"
        '<section class="panel"><h2>Blocking issues</h2>'
        f"{_issue_list(summary['blocking_issues'], 'No blocking issues recorded.')}"
        "</section>"
        f"{render_selection(content_root, post_id)}"
        f"{render_revision_history(summary)}"
        '<section class="panel"><h2>Body</h2>'
        f'<pre class="post-body">{_esc(summary["body"] or "No body recorded.")}</pre></section>'
        f"{revision}"
        '<div class="split">'
        f'<section class="panel"><h2>LinkedIn preview</h2>{preview}</section>'
        '<aside class="actions panel"><h2>Human actions</h2>'
        '<form method="post" action="/action">'
        f"{_csrf_input()}"
        f'<input type="hidden" name="post_id" value="{_esc(post_id)}"/>'
        f'<button type="submit" name="action" value="approve"{approve_disabled}>APPROVE</button>'
        "</form>"
        '<p class="hint">APPROVE records human_editorial_decision only. '
        f"Lifecycle approval: <code>python scripts/content_engine.py approve {_esc(post_id)}</code></p>"
        '<form method="post" action="/action">'
        f"{_csrf_input()}"
        f'<input type="hidden" name="post_id" value="{_esc(post_id)}"/>'
        '<label for="feedback">Revision feedback</label>'
        '<textarea id="feedback" name="feedback" rows="4" required></textarea>'
        '<button class="secondary" type="submit" name="action" value="revise">REVISE</button>'
        "</form>"
        '<form method="post" action="/action">'
        f"{_csrf_input()}"
        f'<input type="hidden" name="post_id" value="{_esc(post_id)}"/>'
        '<label for="drop-note">Drop note</label>'
        '<textarea id="drop-note" name="feedback" rows="3"></textarea>'
        '<button class="warn" type="submit" name="action" value="drop">DROP</button>'
        "</form></aside></div>"
        "<details><summary>Diagnostics</summary>"
        f'<pre class="diagnostics">{diagnostics}</pre></details>'
    )
    return page_shell(post_id, inner, refresh=_cycle_lock_held(content_root, post_id) or job.get("status") == "running")


def _cycle_lock_held(content_root: Path, post_id: str) -> bool:
    path = content_root / cycle_mod.CYCLES_DIR / f"{post_id}{cycle_mod.LOCK_SUFFIX}"
    if not path.is_file():
        return False
    try:
        pid = int(path.read_text(encoding="utf-8").strip() or "0")
    except (OSError, ValueError):
        return False
    return cycle_mod.pid_alive(pid)


def resolve_local_asset(kind: str, rel: str, content_root: Path) -> Path | None:
    if kind not in {"content", "repo"}:
        return None
    rel = urllib.parse.unquote(rel).strip()
    if not rel or rel.startswith(("/", "\\")) or "\\" in rel:
        return None
    if ".." in Path(rel).parts:
        return None
    base = content_root if kind == "content" else ROOT
    try:
        target = (base / rel).resolve()
    except OSError:
        return None
    if not target.is_file() or not target.is_relative_to(base.resolve()):
        return None
    if target.suffix.lower() not in ALLOWED_ASSET_SUFFIXES:
        return None
    return target


class InboxHandler(BaseHTTPRequestHandler):
    content_root: Path = ROOT / "content"
    server_version = "li-inbox/0.2"

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return

    def _bind(self) -> None:
        cycle_mod.ACTIVE_CONTENT_ROOT = self.content_root
        engine.CONTENT = self.content_root

    def _cookie(self) -> None:
        self.send_header("Set-Cookie", f"csrf={InboxState.csrf_token}; Path=/; SameSite=Strict")
        self.send_header("Cache-Control", "no-store")

    def _same_origin(self) -> bool:
        origin = self.headers.get("Origin", "")
        host = self.headers.get("Host", "")
        if not origin:
            return True
        return host in origin

    def _send_html(self, status: int, body: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self._cookie()
        self.end_headers()
        self.wfile.write(encoded)

    def _send_json(self, status: int, payload: dict) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self._cookie()
        self.end_headers()
        self.wfile.write(encoded)

    def _redirect(self, location: str) -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self._cookie()
        self.end_headers()

    def _send_bytes(self, data: bytes, content_type: str) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _query(self) -> dict[str, str]:
        parsed = urllib.parse.urlparse(self.path)
        data = urllib.parse.parse_qs(parsed.query)
        return {key: values[0] for key, values in data.items() if values}

    def do_GET(self) -> None:  # noqa: N802
        self._bind()
        parsed = urllib.parse.urlparse(self.path)
        query = self._query()
        if parsed.path == "/":
            self._send_html(HTTPStatus.OK, render_dashboard(self.content_root, notice=query.get("notice", "")))
            return
        if parsed.path == "/generation":
            self._send_json(HTTPStatus.OK, refresh_job(self.content_root))
            return
        if parsed.path.startswith("/candidate/"):
            post_id = parsed.path.split("/candidate/", 1)[1].strip("/")
            if not POST_ID_RE.fullmatch(post_id):
                self._send_html(HTTPStatus.BAD_REQUEST, page_shell("Invalid id", "<h1>Invalid id</h1>"))
                return
            self._send_html(
                HTTPStatus.OK,
                render_candidate(self.content_root, post_id, notice=query.get("notice", "")),
            )
            return
        if parsed.path.startswith("/preview/"):
            post_id = parsed.path.split("/preview/", 1)[1].strip("/")
            if not POST_ID_RE.fullmatch(post_id):
                self.send_error(HTTPStatus.BAD_REQUEST)
                return
            preview_file = self.content_root / "previews" / f"{post_id}.html"
            if not preview_file.is_file():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            text = preview_file.read_text(encoding="utf-8")
            prepared = prepare_preview_document(text, preview_file, self.content_root)
            self._send_bytes(prepared.encode("utf-8"), "text/html; charset=utf-8")
            return
        if parsed.path.startswith("/local-asset/"):
            rest = parsed.path[len("/local-asset/") :]
            kind, _, rel = rest.partition("/")
            target = resolve_local_asset(kind, rel, self.content_root)
            if target is None:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            types = {
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".gif": "image/gif",
                ".webp": "image/webp",
                ".svg": "image/svg+xml",
            }
            self._send_bytes(target.read_bytes(), types.get(target.suffix.lower(), "application/octet-stream"))
            return
        if parsed.path.startswith("/status/"):
            post_id = parsed.path.split("/status/", 1)[1].strip("/")
            if not POST_ID_RE.fullmatch(post_id):
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid id"})
                return
            self._send_json(HTTPStatus.OK, candidate_summary(self.content_root, post_id))
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def _form(self) -> dict[str, str]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        data = urllib.parse.parse_qs(raw)
        return {key: values[0] for key, values in data.items() if values}

    def _csrf_valid(self, token: str) -> bool:
        if not token:
            return False
        return secrets.compare_digest(token, InboxState.csrf_token)

    def do_POST(self) -> None:  # noqa: N802
        self._bind()
        if not self._same_origin():
            self._send_html(HTTPStatus.FORBIDDEN, page_shell("Forbidden", "<h1>Origin mismatch</h1>"))
            return
        data = self._form()
        if not self._csrf_valid(data.get("csrf", "")):
            self._send_html(HTTPStatus.FORBIDDEN, page_shell("Forbidden", "<h1>CSRF check failed</h1>"))
            return
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/generate":
            ok, message = start_generation(self.content_root, "run")
            if not ok:
                self._send_html(HTTPStatus.CONFLICT, render_dashboard(self.content_root, error=message))
                return
            self._redirect("/?notice=started")
            return
        if parsed.path == "/action":
            post_id = data.get("post_id", "")
            action = data.get("action", "")
            feedback = data.get("feedback", "")
            if not POST_ID_RE.fullmatch(post_id):
                self._send_html(HTTPStatus.BAD_REQUEST, page_shell("Invalid id", "<h1>Invalid id</h1>"))
                return
            try:
                if action == "approve":
                    ready, reasons = cycle_mod.readiness_report(self.content_root, post_id)
                    if not ready:
                        self._send_html(
                            HTTPStatus.CONFLICT,
                            render_candidate(
                                self.content_root,
                                post_id,
                                error="Approval is unavailable until readiness is READY_FOR_HUMAN. " + "; ".join(reasons),
                            ),
                        )
                        return
                    ok, message = set_human_decision(self.content_root, post_id, "APPROVE")
                    if not ok:
                        self._send_html(HTTPStatus.BAD_REQUEST, render_candidate(self.content_root, post_id, error=message))
                        return
                    self._redirect(f"/candidate/{post_id}?notice=approved")
                    return
                if action == "drop":
                    ok, message = set_human_decision(self.content_root, post_id, "DROP", feedback)
                    if not ok:
                        self._send_html(HTTPStatus.BAD_REQUEST, render_candidate(self.content_root, post_id, error=message))
                        return
                    self._redirect(f"/candidate/{post_id}?notice=dropped")
                    return
                if action == "revise":
                    if not feedback.strip():
                        self._send_html(
                            HTTPStatus.BAD_REQUEST,
                            render_candidate(self.content_root, post_id, error="Revision feedback is required."),
                        )
                        return
                    ok, message = set_human_decision(self.content_root, post_id, "REVISE", feedback)
                    if not ok:
                        self._send_html(HTTPStatus.BAD_REQUEST, render_candidate(self.content_root, post_id, error=message))
                        return
                    started, message = start_generation(
                        self.content_root, "resume", post_id=post_id, human_note=feedback
                    )
                    if not started:
                        self._send_html(HTTPStatus.CONFLICT, render_candidate(self.content_root, post_id, error=message))
                        return
                    self._redirect(f"/candidate/{post_id}?notice=revised")
                    return
            except cycle_mod.CycleError as exc:
                self._send_html(HTTPStatus.CONFLICT, render_candidate(self.content_root, post_id, error=str(exc)))
                return
        self._send_html(HTTPStatus.NOT_FOUND, page_shell("Not found", "<h1>Not found</h1>"))


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Local Review Inbox")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--content-root", default=str(ROOT / "content"))
    args = parser.parse_args(argv)
    InboxHandler.content_root = Path(args.content_root)
    server = ThreadingHTTPServer((args.host, args.port), InboxHandler)
    print(f"Review Inbox at http://{args.host}:{args.port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
