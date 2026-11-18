"""Mechanical checks for local content artifacts, plus explicit human approval.

Stdlib only. Claim meaning and paraphrases are still reviewed by li-review.
A review PASS is not human approval. Run from anywhere:

    python scripts/content_engine.py eligible --json
    python scripts/content_engine.py next-id post
    python scripts/content_engine.py stamp content/drafts/POST-YYYYMMDD-NN.md
    python scripts/content_engine.py review content/drafts/POST-YYYYMMDD-NN.md
    python scripts/content_engine.py check content/drafts/POST-YYYYMMDD-NN.md
    python scripts/content_engine.py approve POST-YYYYMMDD-NN
    python scripts/content_engine.py reopen POST-YYYYMMDD-NN
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import critic as critic_mod  # noqa: E402
import editorial as editorial_mod  # noqa: E402
import strategy_check as strategy_mod  # noqa: E402
import validate_proof_registry as registry  # noqa: E402

def content_root() -> Path:
    override = os.environ.get("CONTENT_ROOT", "").strip()
    if override:
        return Path(override)
    return ROOT / "content"


CONTENT = content_root()
HOOKS = ROOT / ".cursor" / "skills" / "li-post" / "hooks.json"

KIND_PREFIX = {"idea": "IDEA", "plan": "PLAN", "post": "POST", "strategy": "STRATEGY"}
KIND_DIR = {"idea": "ideas", "plan": "plans", "post": "drafts", "strategy": "strategy"}
PREFIX_KIND = {value: key for key, value in KIND_PREFIX.items()}
ID_RE = re.compile(r"^(IDEA|PLAN|POST|STRATEGY)-(\d{8})-(\d{2})$")
FM_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)
HEADING_RE = re.compile(r"^## ([^\n]+)\n(.*?)(?=^## |\Z)", re.MULTILINE | re.DOTALL)
APPROVED_AT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

IDEA_STATUSES = {"proposed", "withdrawn"}
PLAN_STATUSES = {"proposed", "withdrawn"}
STRATEGY_STATUSES = {"proposed", "withdrawn"}
POST_STATUSES = {"drafted", "styled", "reviewed", "approved", "published"}
APPROVED_DIR = "approved"
PUBLISHED_DIR = "published"
HISTORY_NAME = "history.jsonl"
HISTORY_FIELDS = ("event", "post_id", "body_sha256", "timestamp")
PUBLISHED_HISTORY_FIELDS = ("event", "post_id", "body_sha256", "linkedin_post_urn", "timestamp")
HISTORY_EVENTS = frozenset({"approved", "reopened", "published"})
CREATED_POST_URN_RE = re.compile(r"^urn:li:(?:share|ugcPost):[0-9]+$")
POST_FIELD_ORDER = (
    "id",
    "kind",
    "status",
    "created",
    "idea",
    "plan",
    "hook",
    "body_sha256",
    "review_verdict",
    "review_body_sha256",
    "editorial_verdict",
    "editorial_body_sha256",
    "editorial_reviewed_at",
    "critic_verdict",
    "critic_body_sha256",
    "critic_reviewed_at",
    "human_editorial_decision",
    "preview_body_sha256",
    "preview_visual_sha256",
    "preview_generated_at",
    "approval_status",
    "approved_body_sha256",
    "approved_visual_sha256",
    "approved_at",
    "publication_status",
    "published_body_sha256",
    "published_at",
    "linkedin_post_urn",
)

REQUIRED_KEYS = {
    "idea": ("id", "kind", "status", "created"),
    "plan": ("id", "kind", "status", "created"),
    "strategy": ("id", "kind", "status", "created"),
    "post": ("id", "kind", "status", "created", "hook", "body_sha256"),
}
IDEA_EDITORIAL_POTENTIAL = frozenset({"HIGH", "MEDIUM", "LOW"})
EDITORIAL_VERDICTS = frozenset({"PASS", "REVISE", "EVIDENCE_TOO_THIN"})

OPTIONAL_KEYS = {
    "idea": {
        "project",
        "editorial_potential",
        "lane",
        "audience",
        "standalone_value",
        "project_context_required",
        "technical_depth",
        "why_now",
        "why_this_author",
    },
    "plan": set(),
    "strategy": {"concentration_risk"},
    "post": {
        "idea",
        "plan",
        "review_verdict",
        "review_body_sha256",
        "editorial_verdict",
        "editorial_body_sha256",
        "editorial_reviewed_at",
        "critic_verdict",
        "critic_body_sha256",
        "critic_reviewed_at",
        "human_editorial_decision",
        "preview_body_sha256",
        "preview_visual_sha256",
        "preview_generated_at",
        "approval_status",
        "approved_body_sha256",
        "approved_visual_sha256",
        "approved_at",
        "publication_status",
        "published_body_sha256",
        "published_at",
        "linkedin_post_urn",
    },
}

CRITIC_VERDICTS = critic_mod.CRITIC_VERDICTS
SECTIONS = {
    "idea": ("Insight", "Why it fits", "Claims", "Evidence gaps", "Local overlap"),
    "plan": ("Slots", "Not scheduled"),
    "strategy": ("Topics", "Mix notes"),
    "post": ("Body", "Claims", "Evidence gaps", "Hooks considered"),
}

# Content-review states. A style score cannot produce or override these.
REVIEW_VERDICTS = frozenset({"PASS", "NEEDS_EDIT", "BLOCKED"})

# Material patterns: the public wording depends on something that must not be published.
MATERIAL_RULES = (
    (re.compile(r"perfect retrieval", re.IGNORECASE), "materially inflated metric"),
    (re.compile(r"100\s*%\s*accura", re.IGNORECASE), "materially inflated metric"),
    (re.compile(r"production-grade accuracy", re.IGNORECASE), "materially inflated metric"),
    (
        re.compile(
            r"\b(?:more than\s+)?(?:\d+|ten)\s+years\b(?:\s+\w+){0,6}\s+(?:in|of)\s+(?:applied\s+)?ai\b",
            re.IGNORECASE,
        ),
        "unsupported years of AI experience",
    ),
    (re.compile(r"\bpaying customers\b", re.IGNORECASE), "unsupported commercial success"),
    (re.compile(r"\bbenchmarks?\s+prove\b", re.IGNORECASE), "unsupported universal factual premise"),
    (re.compile(r"(?<!not )\balways better\b", re.IGNORECASE), "unsupported universal factual premise"),
)

# Voice patterns: the evidence can stay, and the public wording still needs a correction.
VOICE_RULES = (
    (re.compile(r"thoughts\?", re.IGNORECASE), "unnecessary Thoughts?"),
    (re.compile(r"agree\?", re.IGNORECASE), "engagement bait"),
    (re.compile(r"follow me for more", re.IGNORECASE), "engagement bait"),
    (re.compile(r"i couldn't believe what happened next", re.IGNORECASE), "invented storytelling"),
)
CONCLUSION_FRAMES = ("my conclusion", "i treat this as", "the lesson i take")
AUTHOR_INTERPRETATION_RE = re.compile(r"\bi\s+treat\b.*\bas\b", re.IGNORECASE)
RESEARCH_NOTE_RE = re.compile(r"research/trends/[A-Za-z0-9._/-]+\.md")
EXTERNAL_FACT_RE = re.compile(
    r"\bon\s+\d{1,2}\s+\w+\s+\d{4}\b"
    r"|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2},?\s+\d{4}\b"
    r"|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s+\d{4}\b"
    r"|\bdocs retrieved\b"
    r"|\bgenerally available\b"
    r"|\bintegrated\b.*\binto\b",
    re.IGNORECASE,
)
COVERAGE_SKIP_TOKENS = frozenset(
    {
        "about",
        "after",
        "also",
        "been",
        "both",
        "can",
        "from",
        "into",
        "just",
        "like",
        "more",
        "most",
        "not",
        "only",
        "other",
        "over",
        "same",
        "some",
        "such",
        "than",
        "that",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "under",
        "very",
        "when",
        "where",
        "which",
        "while",
        "with",
        "without",
        "would",
        "your",
    }
)
LESSONS_RE = re.compile(r"\b\d+\s+lessons from\b", re.IGNORECASE)
NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
EXPLANATION_RE = re.compile(r'(?m)^- general_technical_knowledge:\s+"([^"]*)"\s*$')
# Observed project results. Present-tense definitions ("uses", "combines") are not in this list.
OBSERVED_OUTCOME_RE = re.compile(
    r"(?<!not )(?<!not to )\b(?:improved|caused|causes|causing|fixed|reduced|proved|prove)\b",
    re.IGNORECASE,
)
METRIC_OR_RESULT_RE = re.compile(r"\b(?:mrr|hit rate|accuracy|throughput|result)\b", re.IGNORECASE)
PERSONAL_ACTION_RE = re.compile(
    r"\b(?:i|we|our|my)\b(?:\s+\w+){0,6}\s+(?:chose|chosen|implemented|tested|observed|improved|fixed|reduced|proved)\b"
    r"|"
    r"\b(?:chose|chosen|implemented|tested|observed|improved|fixed|reduced|proved)\b(?:\s+\w+){0,6}\s+(?:our|we|my)\b",
    re.IGNORECASE,
)
DEFINITIONAL_RE = re.compile(
    r"\b(?:uses|asks whether|means|records|combines|preserves|is defined as|refers to)\b",
    re.IGNORECASE,
)
PERSONAL_ANCHOR_RE = re.compile(r"\b(?:i|we|our|my|me)\b", re.IGNORECASE)


def fail_out(messages: list[str]) -> int:
    for message in messages:
        print(message, file=sys.stderr)
    print(f"{len(messages)} error(s)", file=sys.stderr)
    return 1


def load_claims() -> list[dict]:
    registry.errors.clear()
    claims = registry.parse_registry(registry.REGISTRY.read_text(encoding="utf-8"))
    if registry.errors:
        raise RuntimeError("proof registry failed to parse: " + "; ".join(registry.errors))
    return claims


def classify(claim: dict) -> str:
    public = claim.get("public_safe") is True
    verification = claim.get("verification")
    if public and verification == "verified":
        return "fact"
    if public and verification == "derived":
        return "opinion"
    return "blocked"


def load_hooks(path: Path | None = None) -> dict:
    return json.loads((path or HOOKS).read_text(encoding="utf-8"))


def hook_slugs(hooks: dict) -> set[str]:
    return {item["slug"] for item in hooks.get("hooks", [])}


def kind_folders(content_root: Path, kind: str) -> list[Path]:
    folders = [content_root / KIND_DIR[kind]]
    if kind == "post":
        # Approved and published copies keep their ids, so the next draft must not reuse them.
        folders.append(content_root / APPROVED_DIR)
        folders.append(content_root / PUBLISHED_DIR)
    return folders


def next_id(content_root: Path, kind: str, day: str) -> str:
    if kind not in KIND_PREFIX:
        raise ValueError(f"unknown kind {kind}")
    if not re.fullmatch(r"\d{8}", day):
        raise ValueError(f"date must be YYYYMMDD, got {day}")
    prefix = KIND_PREFIX[kind]
    highest = 0
    for folder in kind_folders(content_root, kind):
        if not folder.is_dir():
            continue
        for path in folder.glob(f"{prefix}-{day}-*.md"):
            match = ID_RE.fullmatch(path.stem)
            if match and match.group(2) == day:
                highest = max(highest, int(match.group(3)))
    number = highest + 1
    if number > 99:
        raise ValueError(f"no remaining {prefix} ids for {day}")
    return f"{prefix}-{day}-{number:02d}"


def find_posts(content_root: Path, post_id: str) -> list[Path]:
    found: list[Path] = []
    for folder in kind_folders(content_root, "post"):
        candidate = folder / f"{post_id}.md"
        if candidate.is_file():
            found.append(candidate)
    return found


def utc_timestamp() -> str:
    """Approval timestamps are UTC, written as YYYY-MM-DDTHH:MM:SSZ."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def valid_utc_timestamp(value: str) -> bool:
    if not APPROVED_AT_RE.fullmatch(value):
        return False
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return False
    return True


def approval_is_effective(fields: dict) -> bool:
    if fields.get("status") == "approved":
        return True
    if fields.get("approval_status") == "approved":
        return True
    if fields.get("approved_body_sha256") or fields.get("approved_at"):
        return True
    return False


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    temporary.replace(path)


def append_history(content_root: Path, event: dict) -> None:
    """Append one audit line. The artifact remains the source of truth."""
    name = event.get("event")
    if name == "published":
        expected = PUBLISHED_HISTORY_FIELDS
    elif name in {"approved", "reopened"}:
        expected = HISTORY_FIELDS
    else:
        raise ValueError(f"unknown history event {name}")
    if tuple(event) != expected:
        raise ValueError("history event fields are fixed")
    if name not in HISTORY_EVENTS:
        raise ValueError(f"unknown history event {name}")
    if not valid_utc_timestamp(event["timestamp"]):
        raise ValueError("history timestamp must be UTC")
    if name == "published" and CREATED_POST_URN_RE.fullmatch(event["linkedin_post_urn"]) is None:
        raise ValueError("linkedin_post_urn must identify the created post")
    path = content_root / HISTORY_NAME
    line = json.dumps(event, ensure_ascii=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(line + "\n")


def normalize_body(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    while lines and lines[0] == "":
        lines.pop(0)
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines) + "\n"


def body_hash(text: str) -> str:
    return hashlib.sha256(normalize_body(text).encode("utf-8")).hexdigest()


def parse_frontmatter(text: str) -> tuple[dict, str]:
    match = FM_RE.match(text)
    if not match:
        raise ValueError("missing frontmatter")
    fields: dict[str, str] = {}
    for line_no, line in enumerate(match.group(1).splitlines(), start=2):
        if not line.strip():
            continue
        if line.startswith(" ") or ":" not in line:
            raise ValueError(f"line {line_no}: unsupported frontmatter")
        key, raw = line.split(":", 1)
        if not re.fullmatch(r"[a-z0-9_]+", key) or key in fields:
            raise ValueError(f"line {line_no}: bad field {key}")
        fields[key] = raw.strip()
    return fields, text[match.end() :]


def section_map(body: str) -> dict[str, str]:
    return {name.strip(): content for name, content in HEADING_RE.findall(body)}


def parse_records(section: str, fields: tuple[str, ...]) -> list[dict]:
    records: list[dict] = []
    current: dict | None = None
    for line in section.splitlines():
        if not line.strip():
            continue
        item = re.fullmatch(r"- ([a-z_]+): (.*)", line.strip())
        nested = re.fullmatch(r"([a-z_]+): (.*)", line.strip()) if line.startswith(" ") else None
        if line.startswith("- "):
            if current is not None:
                records.append(current)
            if not item:
                raise ValueError(f"unsupported record line: {line}")
            key, raw = item.group(1), item.group(2)
            current = {key: parse_scalar(raw)}
            continue
        if nested and current is not None:
            key, raw = nested.group(1), nested.group(2)
            if key in current:
                raise ValueError(f"duplicate field {key}")
            current[key] = parse_scalar(raw)
            continue
        raise ValueError(f"unsupported record line: {line}")
    if current is not None:
        records.append(current)
    for record in records:
        unknown = set(record) - set(fields)
        if unknown:
            raise ValueError(f"unknown record fields {sorted(unknown)}")
    return records


def parse_scalar(raw: str):
    value = raw.strip()
    if value == "":
        return ""
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1]
    if re.fullmatch(r"[A-Za-z0-9_./-]+", value):
        return value
    if '"' not in value:
        return value
    raise ValueError(f"quote this value: {value}")


def statement_numbers(statement: str) -> list[str]:
    return NUMBER_RE.findall(statement)


def number_in_text(number: str, text: str) -> bool:
    return number in statement_numbers(text)


def prohibited_phrases(qualification: str) -> list[str]:
    match = re.search(r"Do not generalize to (.+?)\.", qualification)
    if not match:
        return []
    text = re.sub(r",?\s+or\s+", ", ", match.group(1))
    return [part.strip(" .") for part in text.split(",") if part.strip(" .")]


def normalize_statement(text: str) -> str:
    return re.sub(r"\s+", " ", text.casefold()).strip(" .")


def split_sentences(text: str) -> list[str]:
    sentences: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        for part in re.split(r"(?<=[.!?])\s+", line):
            part = part.strip()
            if part:
                sentences.append(part)
    return sentences


def project_labels(claims: list[dict]) -> list[str]:
    labels: list[str] = []
    seen: set[str] = set()
    for claim in claims:
        project = claim.get("project")
        if not isinstance(project, str) or not project.strip():
            continue
        for label in (project.casefold(), project.replace("-", " ").casefold()):
            if len(label) < 4 or label in seen:
                continue
            seen.add(label)
            labels.append(label)
    return labels


def mentions_registered_project(text: str, claims: list[dict]) -> bool:
    folded = text.casefold()
    return any(label in folded for label in project_labels(claims))


def is_project_outcome(text: str) -> bool:
    """A project result or cause. A definition of the same metric is not one."""
    if PERSONAL_ACTION_RE.search(text):
        return True
    return bool(OBSERVED_OUTCOME_RE.search(text) and METRIC_OR_RESULT_RE.search(text))


def strip_interpretation_frame(text: str) -> str:
    """Remove a conclusion frame prefix so remaining text can be checked for personal substance."""
    folded = text.casefold().strip()
    original = text.strip()
    for frame in CONCLUSION_FRAMES:
        idx = folded.find(frame)
        if idx >= 0:
            rest = original[idx + len(frame) :].lstrip(" :,\u2014\u2013-")
            return rest
    match = AUTHOR_INTERPRETATION_RE.search(text)
    if match:
        return text[match.end() :].lstrip(" :,\u2014\u2013-")
    return text


def has_framed_interpretation(text: str) -> bool:
    folded = text.casefold()
    if any(frame in folded for frame in CONCLUSION_FRAMES):
        return True
    return bool(AUTHOR_INTERPRETATION_RE.search(text))


def personal_project_substance(text: str, claims: list[dict] | None) -> bool:
    """True when the sentence states personal or project experience, not only a public-system view."""
    if is_project_outcome(text):
        return True
    stripped = strip_interpretation_frame(text)
    if is_project_outcome(stripped):
        return True
    if claims and mentions_registered_project(text, claims):
        return True
    return bool(PERSONAL_ANCHOR_RE.search(stripped))


def is_author_interpretation(text: str) -> bool:
    """Framed engineering judgment about a public system, not a project fact."""
    if has_framed_interpretation(text):
        return not personal_project_substance(text, None)
    return bool(AUTHOR_INTERPRETATION_RE.search(text))


def is_external_fact_sentence(text: str) -> bool:
    """Dated vendor capability or explicit docs attribution in the body."""
    return bool(EXTERNAL_FACT_RE.search(text))


def coverage_tokens(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", text.casefold())
    return [word for word in words if word not in COVERAGE_SKIP_TOKENS and (len(word) >= 5 or word.isdigit())]


def external_sentence_covered(sentence: str, bundle: str) -> bool:
    """True when enough content tokens from the sentence appear in the topic research."""
    if not bundle.strip():
        return False
    tokens = coverage_tokens(sentence)
    if len(tokens) < 4:
        return True
    bundle_fold = bundle.casefold()
    hits = sum(1 for token in tokens if token in bundle_fold)
    needed = min(5, max(3, (len(tokens) + 1) // 2))
    return hits >= needed


def load_source_bundle(content_root: Path, idea_id: str) -> str:
    """why_now from the idea plus the cited research/trends note, when present."""
    parts: list[str] = []
    idea_path = content_root / "ideas" / f"{idea_id}.md"
    if not idea_path.is_file():
        return ""
    try:
        fields, _rest = parse_frontmatter(idea_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    why_now = str(fields.get("why_now") or "")
    if why_now:
        parts.append(why_now)
    match = RESEARCH_NOTE_RE.search(why_now)
    if match:
        note_path = ROOT / match.group(0)
        if note_path.is_file():
            try:
                parts.append(note_path.read_text(encoding="utf-8"))
            except OSError:
                pass
    return "\n".join(parts)


def claims_section_status(section: str) -> tuple[str, list[dict] | None]:
    """Return ok, empty, or malformed for a Claims section."""
    stripped = section.strip()
    if stripped == "":
        return "empty", None
    if stripped == "- none":
        return "none", []
    records = claim_records(section)
    if records is None:
        return "malformed", None
    return "listed", records


def classify_statement(text: str, claims: list[dict] | None = None) -> str:
    """Classify one material sentence.

    project_fact and derived_conclusion still need an eligible registry claim.
    author_interpretation and general_technical do not. external_fact needs
    topic research coverage. unclassified is other engineering prose.
    """
    if is_project_outcome(text):
        return "project_fact"
    if has_framed_interpretation(text):
        if personal_project_substance(text, claims):
            return "derived_conclusion"
        return "author_interpretation"
    if is_author_interpretation(text):
        return "author_interpretation"
    if claims and mentions_registered_project(text, claims):
        return "project_fact"
    if PERSONAL_ANCHOR_RE.search(text):
        return "project_fact"
    if is_external_fact_sentence(text):
        return "external_fact"
    if DEFINITIONAL_RE.search(text):
        return "general_technical"
    return "unclassified"


def general_technical_body(body: str, claims: list[dict]) -> bool:
    sentences = split_sentences(body)
    return bool(sentences) and all(classify_statement(sentence, claims) == "general_technical" for sentence in sentences)


def copies_eligible_statement(sentence: str, claims: list[dict]) -> bool:
    """True when the sentence is a registry statement, not an analysis of one."""
    target = normalize_statement(sentence)
    if not target:
        return False
    for claim in claims:
        if classify(claim) == "blocked":
            continue
        statement = str(claim.get("statement") or "")
        if statement and normalize_statement(statement) == target:
            return True
    return False


def uncited_analysis_allowed(body: str, claims: list[dict]) -> bool:
    """An uncited body may explain a technical subject.

    It may not state a personal or project fact, a derived conclusion, or a
    sentence copied from the registry. A definition is one allowed shape.
    An engineering analysis that adds none of those is allowed too.
    """
    sentences = split_sentences(body)
    if not sentences:
        return False
    for sentence in sentences:
        kind = classify_statement(sentence, claims)
        if kind in {"project_fact", "derived_conclusion"}:
            return False
        if copies_eligible_statement(sentence, claims):
            return False
    return True


def uncited_body_needs_claim(body: str, claims: list[dict]) -> bool:
    """True when Claims must list personal proof rather than - none."""
    return not uncited_analysis_allowed(body, claims)


def project_outcome_supported(sentence: str, claims: list[dict]) -> bool:
    """True only when one eligible fact claim states this outcome.

    Each claim is compared on its own. A use fact and a metric fact are never
    combined into a cause. General technical knowledge is not a premise.
    """
    target = normalize_statement(sentence)
    if not target:
        return False
    for claim in claims:
        if classify(claim) != "fact":
            continue
        if normalize_statement(str(claim.get("statement") or "")) == target:
            return True
    return False


def explanation_markers(section: str) -> list[str]:
    return EXPLANATION_RE.findall(section)


def claim_records(section: str) -> list[dict] | None:
    stripped = section.strip()
    if stripped in {"", "- none"}:
        return []
    try:
        return parse_records(section, ("id", "use", "scope"))
    except ValueError:
        return None


def review_body(
    body: str,
    cited: list[dict],
    claims: list[dict],
    *,
    source_bundle: str = "",
) -> tuple[str, list[str]]:
    """Review the public body. Returns PASS, NEEDS_EDIT, or BLOCKED.

    PASS means this body passed semantic review. It does not approve
    publication. A style score is not an argument and cannot change the result.
    """
    by_id = {claim["id"]: claim for claim in claims if isinstance(claim.get("id"), str)}
    blocked: list[str] = []
    edits: list[str] = []
    if not cited and not uncited_analysis_allowed(body, claims):
        blocked.append("no eligible claim is cited")
    for sentence in split_sentences(body):
        if classify_statement(sentence, claims) == "external_fact" and not external_sentence_covered(
            sentence, source_bundle
        ):
            blocked.append(f"external factual statement has no source in the topic research: {sentence}")
            break
    saw_opinion = False
    for item in cited:
        claim_id = str(item.get("id", ""))
        use = str(item.get("use", ""))
        claim = by_id.get(claim_id)
        if claim is None or use not in {"fact", "opinion"}:
            blocked.append(f"no eligible proof for {claim_id}")
            continue
        kind = classify(claim)
        if kind == "blocked":
            blocked.append(f"{claim_id} is not eligible for public content")
            continue
        if use == "fact" and kind != "fact":
            blocked.append(f"{claim_id} is a derived claim stated as fact")
            continue
        if use == "opinion" and kind == "opinion":
            saw_opinion = True
        elif use == "opinion" and kind == "fact":
            edits.append(f"{claim_id} is a fact and the wording should keep that scope")
        if kind == "fact":
            for phrase in prohibited_phrases(str(claim.get("qualification") or "")):
                if phrase.casefold() in body.casefold():
                    blocked.append(f"prohibited generalization {phrase}")
    for claim in by_id.values():
        statement = str(claim.get("statement") or "")
        if classify(claim) == "blocked" and statement and statement in body:
            blocked.append(f"body copies blocked claim {claim['id']}")
    seen_material: set[str] = set()
    for pattern, reason in MATERIAL_RULES:
        if pattern.search(body) and reason not in seen_material:
            seen_material.add(reason)
            blocked.append(reason)
    cited_facts = []
    for item in cited:
        claim = by_id.get(str(item.get("id", "")))
        if claim is not None and str(item.get("use", "")) == "fact" and classify(claim) == "fact":
            cited_facts.append(claim)
    for sentence in split_sentences(body):
        if is_project_outcome(sentence) and not project_outcome_supported(sentence, cited_facts):
            blocked.append(f"project outcome has no eligible proof: {sentence}")
            break
    if blocked:
        return "BLOCKED", blocked
    for pattern, reason in VOICE_RULES:
        if pattern.search(body):
            edits.append(reason)
    if LESSONS_RE.search(body):
        edits.append("N-lessons pattern")
    if saw_opinion and not any(frame in body.casefold() for frame in CONCLUSION_FRAMES):
        edits.append("derived statement is not framed as a personal conclusion")
    if edits:
        return "NEEDS_EDIT", edits
    return "PASS", []


def check_artifact(path: Path, claims: list[dict], hooks: dict, content_root: Path) -> list[str]:
    errors: list[str] = []
    by_id = {claim["id"]: claim for claim in claims if isinstance(claim.get("id"), str)}
    try:
        text = path.read_text(encoding="utf-8")
        fields, rest = parse_frontmatter(text)
        sections = section_map(rest)
    except (OSError, ValueError) as exc:
        return [f"{path.name}: {exc}"]

    kind = fields.get("kind", "")
    if kind not in REQUIRED_KEYS:
        return [f"{path.name}: kind must be idea, plan, strategy, or post"]

    for key in REQUIRED_KEYS[kind]:
        if not fields.get(key):
            errors.append(f"{path.name}: missing {key}")
    unknown = set(fields) - set(REQUIRED_KEYS[kind]) - OPTIONAL_KEYS[kind]
    if unknown:
        errors.append(f"{path.name}: unknown fields {sorted(unknown)}")

    artifact_id = fields.get("id", "")
    match = ID_RE.fullmatch(artifact_id)
    if not match or PREFIX_KIND.get(match.group(1)) != kind:
        errors.append(f"{path.name}: id does not match kind")
    elif path.stem != artifact_id:
        errors.append(f"{path.name}: filename must be {artifact_id}.md")
    else:
        created = fields.get("created", "")
        expected = f"{match.group(2)[:4]}-{match.group(2)[4:6]}-{match.group(2)[6:]}"
        if created != expected:
            errors.append(f"{path.name}: created must be {expected}")

    statuses = {
        "idea": IDEA_STATUSES,
        "plan": PLAN_STATUSES,
        "strategy": STRATEGY_STATUSES,
        "post": POST_STATUSES,
    }[kind]
    if fields.get("status") and fields["status"] not in statuses:
        errors.append(f"{path.name}: unsupported status {fields['status']}")
    if kind == "post":
        parent = path.parent.name
        if fields.get("status") == "approved" and parent != APPROVED_DIR:
            errors.append(f"{path.name}: approved posts live in content/approved/")
        if parent == APPROVED_DIR and fields.get("status") != "approved":
            errors.append(f"{path.name}: content/approved/ only holds approved posts")
        if fields.get("status") == "published" and parent != PUBLISHED_DIR:
            errors.append(f"{path.name}: published posts live in content/published/")
        if parent == PUBLISHED_DIR and fields.get("status") != "published":
            errors.append(f"{path.name}: content/published/ only holds published posts")

    for name in SECTIONS[kind]:
        if name not in sections:
            errors.append(f"{path.name}: missing section {name}")

    if kind == "post":
        errors.extend(check_post(path.name, fields, sections, by_id, hooks, content_root))
    elif kind == "idea":
        potential = fields.get("editorial_potential", "")
        if potential and potential not in IDEA_EDITORIAL_POTENTIAL:
            errors.append(f"{path.name}: editorial_potential must be HIGH, MEDIUM, or LOW")
        errors.extend(check_idea_strategy_fields(path.name, fields))
        errors.extend(
            check_claims(
                path.name,
                sections.get("Claims", ""),
                by_id,
                require_body=False,
                body="",
                idea_artifact=True,
            )
        )
    elif kind == "strategy":
        errors.extend(check_strategy(path.name, fields, sections.get("Topics", ""), sections.get("Mix notes", "")))
    elif kind == "plan":
        errors.extend(check_plan(path.name, sections.get("Slots", ""), sections.get("Not scheduled", ""), hooks, content_root))
    return errors


def check_claims(
    label: str,
    section: str,
    by_id: dict,
    require_body: bool,
    body: str,
    *,
    idea_artifact: bool = False,
    waive_none_consistency: bool = False,
) -> list[str]:
    errors: list[str] = []
    status, records = claims_section_status(section)
    if status == "empty":
        return [f"{label}: claims section is empty; use - none when no personal claim is cited."]
    if status == "malformed":
        return [f"{label}: claims section could not be read"]
    if status == "none":
        if idea_artifact or waive_none_consistency:
            return []
        if uncited_analysis_allowed(body, list(by_id.values())):
            return []
        return [f"{label}: - none is inconsistent with personal or project claims in the body"]
    assert records is not None
    if not records:
        errors.append(f"{label}: claims section is empty; use - none when no personal claim is cited.")
        return errors
    seen: set[str] = set()
    for record in records:
        claim_id = record.get("id", "")
        use = record.get("use", "")
        claim = by_id.get(claim_id)
        if claim_id in seen:
            errors.append(f"{label}: duplicate claim {claim_id}")
        seen.add(claim_id)
        if claim is None:
            errors.append(f"{label}: unknown claim {claim_id}")
            continue
        expected = classify(claim)
        if use == "fact" and expected != "fact":
            errors.append(f"{label}: {claim_id} cannot be stated as fact")
        elif use == "opinion" and expected != "opinion":
            errors.append(f"{label}: {claim_id} cannot be used as an opinion claim")
        elif use not in {"fact", "opinion"}:
            errors.append(f"{label}: {claim_id} use must be fact or opinion")
        qualification = claim.get("qualification") or ""
        scope = record.get("scope", "")
        if qualification and scope != qualification:
            errors.append(f"{label}: {claim_id} scope must copy the qualification exactly")
        if not qualification and scope:
            errors.append(f"{label}: {claim_id} has no qualification to scope")
        if require_body and use == "fact" and claim.get("category") == "metric":
            for number in statement_numbers(str(claim.get("statement", ""))):
                if not number_in_text(number, body):
                    errors.append(f"{label}: {claim_id} number {number} is missing from the body")
            for phrase in prohibited_phrases(qualification):
                if phrase.casefold() in body.casefold():
                    errors.append(f"{label}: body uses prohibited generalization {phrase!r}")
    return errors


def check_post(
    label: str,
    fields: dict,
    sections: dict,
    by_id: dict,
    hooks: dict,
    content_root: Path,
) -> list[str]:
    errors: list[str] = []
    body = sections.get("Body", "")
    digest = body_hash(body)
    if fields.get("body_sha256") and fields["body_sha256"] != digest:
        errors.append(f"{label}: body_sha256 does not match the body")
    slug = fields.get("hook", "")
    if slug and slug not in hook_slugs(hooks):
        errors.append(f"{label}: unknown hook {slug}")
    human_decision = fields.get("human_editorial_decision", "")
    if human_decision and human_decision not in strategy_mod.HUMAN_EDITORIAL_DECISIONS:
        errors.append(f"{label}: human_editorial_decision must be APPROVE, REVISE, or DROP")
    status = fields.get("status")
    verdict = fields.get("review_verdict", "")
    review_hash = fields.get("review_body_sha256", "")
    # A recorded BLOCKED review keeps the unsafe body. The other states still
    # have to satisfy the metric and qualification checks.
    accept_blocked_body = status == "reviewed" and verdict == "BLOCKED"
    waive_none = accept_blocked_body and review_hash == digest
    errors.extend(
        check_claims(
            label,
            sections.get("Claims", ""),
            by_id,
            require_body=not accept_blocked_body,
            body=body,
            waive_none_consistency=waive_none,
        )
    )
    for statement in explanation_markers(sections.get("Evidence gaps", "")):
        kind = classify_statement(statement, list(by_id.values()))
        if kind != "general_technical":
            errors.append(f"{label}: general_technical_knowledge cannot cover a project or derived claim")
        elif normalize_statement(statement) not in normalize_statement(body):
            errors.append(f"{label}: general_technical_knowledge statement is not in the body")
    computed = ""
    reasons: list[str] = []
    records = claim_records(sections.get("Claims", ""))
    idea_id = str(fields.get("idea") or "")
    source_bundle = load_source_bundle(content_root, idea_id) if idea_id else ""
    if records is not None:
        computed, reasons = review_body(
            body,
            records,
            list(by_id.values()),
            source_bundle=source_bundle,
        )
        if computed != "PASS" and status not in {"reviewed", "approved"}:
            errors.append(f"{label}: body review is {computed}: {'; '.join(reasons)}")
    if status in {"reviewed", "approved", "published"}:
        if verdict not in REVIEW_VERDICTS:
            errors.append(f"{label}: review_verdict must be PASS, NEEDS_EDIT, or BLOCKED")
        if review_hash != digest:
            if verdict == "PASS":
                errors.append(f"{label}: previous PASS is stale")
            else:
                errors.append(f"{label}: review hash is stale")
        if computed == "BLOCKED" and verdict != "BLOCKED":
            errors.append(f"{label}: review_verdict must be BLOCKED")
        elif computed == "NEEDS_EDIT" and verdict != "NEEDS_EDIT":
            errors.append(f"{label}: review_verdict must be NEEDS_EDIT")
        if "Review" not in sections:
            errors.append(f"{label}: missing section Review")
    elif verdict or review_hash:
        errors.append(f"{label}: review fields are set but status is not reviewed")
    approval_status = fields.get("approval_status", "")
    approved_hash = fields.get("approved_body_sha256", "")
    approved_at = fields.get("approved_at", "")
    if status in {"approved", "published"}:
        if approval_status != "approved":
            errors.append(f"{label}: approval_status must be approved")
        if approved_hash != digest:
            errors.append(f"{label}: approval is stale")
        if review_hash != digest or fields.get("body_sha256") != digest:
            errors.append(f"{label}: approved body hash does not match the review hash")
        if verdict != "PASS":
            errors.append(f"{label}: approved posts require review_verdict PASS")
        if not valid_utc_timestamp(approved_at):
            errors.append(f"{label}: approved_at must be a UTC timestamp YYYY-MM-DDTHH:MM:SSZ")
    elif approval_status or approved_hash or approved_at:
        errors.append(f"{label}: approval fields are set but status is not approved")
    publication_status = fields.get("publication_status", "")
    published_hash = fields.get("published_body_sha256", "")
    published_at = fields.get("published_at", "")
    post_urn = fields.get("linkedin_post_urn", "")
    if status == "published":
        if publication_status != "published":
            errors.append(f"{label}: publication_status must be published")
        if published_hash != digest:
            errors.append(f"{label}: publication hash does not match the body")
        hashes = (
            fields.get("body_sha256", ""),
            review_hash,
            approved_hash,
            published_hash,
        )
        if any(value != digest for value in hashes):
            errors.append(f"{label}: published body hash does not match the earlier hashes")
        if CREATED_POST_URN_RE.fullmatch(post_urn) is None:
            errors.append(f"{label}: linkedin_post_urn must identify the created post")
        if not valid_utc_timestamp(published_at):
            errors.append(f"{label}: published_at must be a UTC timestamp YYYY-MM-DDTHH:MM:SSZ")
    elif publication_status or published_hash or published_at or post_urn:
        errors.append(f"{label}: publication fields are set but status is not published")
    if status in {"styled", "reviewed", "approved", "published"} and "Style report" not in sections:
        errors.append(f"{label}: styled posts need a Style report section")

    editorial_verdict = fields.get("editorial_verdict", "")
    editorial_hash = fields.get("editorial_body_sha256", "")
    editorial_at = fields.get("editorial_reviewed_at", "")
    preview_body_hash = fields.get("preview_body_sha256", "")
    preview_visual_hash = fields.get("preview_visual_sha256", "")
    preview_at = fields.get("preview_generated_at", "")
    approved_visual = fields.get("approved_visual_sha256", "")

    if editorial_verdict or editorial_hash or editorial_at:
        if editorial_verdict not in EDITORIAL_VERDICTS:
            errors.append(f"{label}: editorial_verdict must be PASS, REVISE, or EVIDENCE_TOO_THIN")
        if editorial_hash != digest:
            if editorial_verdict == "PASS":
                errors.append(f"{label}: editorial PASS is stale")
            else:
                errors.append(f"{label}: editorial hash is stale")
        if editorial_at and not valid_utc_timestamp(editorial_at):
            errors.append(f"{label}: editorial_reviewed_at must be a UTC timestamp YYYY-MM-DDTHH:MM:SSZ")
        if "Editorial review" not in sections:
            errors.append(f"{label}: missing section Editorial review")
        elif editorial_verdict:
            computed = editorial_mod.editorial_review(body, sections.get("Evidence gaps", ""))
            if computed.verdict != editorial_verdict:
                errors.append(f"{label}: editorial_verdict does not match computed {computed.verdict}")
    elif any(fields.get(key) for key in ("editorial_body_sha256", "editorial_reviewed_at")):
        errors.append(f"{label}: partial editorial fields without editorial_verdict")

    if preview_body_hash or preview_visual_hash or preview_at:
        if preview_body_hash != digest:
            errors.append(f"{label}: preview is stale")
        if preview_at and not valid_utc_timestamp(preview_at):
            errors.append(f"{label}: preview_generated_at must be a UTC timestamp YYYY-MM-DDTHH:MM:SSZ")
        post_id = fields.get("id", "")
        if post_id:
            import visual as visual_mod  # noqa: WPS433

            on_disk_visual, _ = visual_mod.read_visual_state(content_root, post_id)
            expected_visual = on_disk_visual or ""
            recorded_visual = preview_visual_hash or ""
            if recorded_visual != expected_visual:
                errors.append(f"{label}: preview visual hash does not match the current visual")
    elif any(fields.get(key) for key in ("preview_body_sha256", "preview_visual_sha256")):
        errors.append(f"{label}: partial preview fields without preview_generated_at")

    critic_verdict = fields.get("critic_verdict", "")
    critic_hash = fields.get("critic_body_sha256", "")
    critic_at = fields.get("critic_reviewed_at", "")
    if critic_verdict or critic_hash or critic_at:
        if critic_verdict not in CRITIC_VERDICTS:
            errors.append(f"{label}: critic_verdict must be PASS, REVISE, or DROP")
        if critic_hash != digest:
            if critic_verdict == "PASS":
                errors.append(f"{label}: critic PASS is stale")
            else:
                errors.append(f"{label}: critic hash is stale")
        if critic_at and not valid_utc_timestamp(critic_at):
            errors.append(f"{label}: critic_reviewed_at must be a UTC timestamp YYYY-MM-DDTHH:MM:SSZ")
        if "Critic review" not in sections:
            errors.append(f"{label}: missing section Critic review")
        elif critic_verdict:
            parsed = critic_mod.parse_critic_section(sections.get("Critic review", ""))
            if parsed is None or parsed.verdict != critic_verdict:
                errors.append(f"{label}: critic section does not match critic_verdict")
            elif critic_verdict == "PASS" and parsed.blocking_issues:
                errors.append(f"{label}: critic PASS requires no blocking issues")
    elif any(fields.get(key) for key in ("critic_body_sha256", "critic_reviewed_at")):
        errors.append(f"{label}: partial critic fields without critic_verdict")

    if status in {"approved", "published"} and preview_body_hash:
        if editorial_verdict != "PASS" or editorial_hash != digest:
            errors.append(f"{label}: approved post with preview requires editorial PASS on the current body")
        if approved_visual != (preview_visual_hash or ""):
            errors.append(f"{label}: approved visual hash does not match preview")
    elif status in {"approved", "published"} and approved_visual:
        errors.append(f"{label}: approved_visual_sha256 set without preview metadata")
    return errors


def check_plan(label: str, slots: str, schedule: str, hooks: dict, content_root: Path) -> list[str]:
    errors: list[str] = []
    if "No posting time is assigned." not in schedule:
        errors.append(f"{label}: plan must say that no posting time is assigned")
    if re.search(r"\b\d{1,2}:\d{2}\s*(am|pm)\b", schedule, re.IGNORECASE):
        errors.append(f"{label}: plan assigns a clock time")
    try:
        records = parse_records(slots, ("order", "idea", "hook", "angle"))
    except ValueError as exc:
        return errors + [f"{label}: slots: {exc}"]
    if not records:
        errors.append(f"{label}: plan has no slots")
    known = hook_slugs(hooks)
    for record in records:
        idea = str(record.get("idea", ""))
        hook = str(record.get("hook", ""))
        if not ID_RE.fullmatch(idea) or not idea.startswith("IDEA-"):
            errors.append(f"{label}: slot idea is not an idea id")
        elif not (content_root / "ideas" / f"{idea}.md").is_file():
            errors.append(f"{label}: missing idea file {idea}.md")
        if hook not in known:
            errors.append(f"{label}: unknown hook {hook}")
        if not str(record.get("angle", "")).strip():
            errors.append(f"{label}: slot angle is empty")
    return errors


def render_frontmatter(fields: dict, order: tuple[str, ...]) -> str:
    lines = ["---"]
    seen = set()
    for key in order:
        if key in fields:
            lines.append(f"{key}: {fields[key]}")
            seen.add(key)
    for key in fields:
        if key not in seen:
            lines.append(f"{key}: {fields[key]}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def approved_edit_message(post_id: str) -> str:
    return f"{post_id} is approved. Reopen it explicitly before editing."


def published_edit_message(post_id: str) -> str:
    return f"{post_id} is published. Published artifacts are immutable."


def targets_approved_artifact(path: Path, fields: dict) -> bool:
    """True for the canonical approved copy, or a post still marked approved."""
    return path.parent.name == APPROVED_DIR or fields.get("status") == "approved"


def stamp(path: Path, invalidate: bool, status: str | None) -> int:
    text = path.read_text(encoding="utf-8")
    fields, rest = parse_frontmatter(text)
    if fields.get("kind") != "post":
        print("stamp only applies to posts", file=sys.stderr)
        return 1
    post_id = fields.get("id") or path.stem
    if path.parent.name == PUBLISHED_DIR or fields.get("status") == "published" or fields.get("publication_status"):
        print(published_edit_message(post_id), file=sys.stderr)
        return 1
    if targets_approved_artifact(path, fields):
        print(approved_edit_message(post_id), file=sys.stderr)
        return 1
    sections = section_map(rest)
    if "Body" not in sections:
        print("missing Body section", file=sys.stderr)
        return 1
    digest = body_hash(sections["Body"])
    reviewed = fields.get("status") == "reviewed" or bool(fields.get("review_verdict") or fields.get("review_body_sha256"))
    had_approval = approval_is_effective(fields)
    protected = reviewed or had_approval
    changed = fields.get("body_sha256") not in ("", digest)
    if changed and protected and not invalidate:
        print("body changed after review; rerun with --invalidate", file=sys.stderr)
        return 1
    fields["body_sha256"] = digest
    if invalidate and protected:
        fields["status"] = status or "styled"
        fields["review_verdict"] = ""
        fields["review_body_sha256"] = ""
        fields["editorial_verdict"] = ""
        fields["editorial_body_sha256"] = ""
        fields["editorial_reviewed_at"] = ""
        fields["critic_verdict"] = ""
        fields["critic_body_sha256"] = ""
        fields["critic_reviewed_at"] = ""
        fields["preview_body_sha256"] = ""
        fields["preview_visual_sha256"] = ""
        fields["preview_generated_at"] = ""
        if had_approval:
            fields["approval_status"] = ""
            fields["approved_body_sha256"] = ""
            fields["approved_visual_sha256"] = ""
            fields["approved_at"] = ""
        if fields["status"] not in {"drafted", "styled"}:
            print("invalidated status must be drafted or styled", file=sys.stderr)
            return 1
        rest = editorial_mod.remove_editorial_section(rest)
        rest = critic_mod.remove_critic_section(rest)
    rendered = render_frontmatter(fields, POST_FIELD_ORDER) + rest
    write_atomic(path, rendered)
    print(digest)
    return 0


def apply_critic(path: Path, content_root: Path, findings: critic_mod.CriticFindings, now: str | None = None) -> int:
    if not path.is_file():
        return fail_out([f"{path}: artifact does not exist"])
    try:
        text = path.read_text(encoding="utf-8")
        fields, rest = parse_frontmatter(text)
        sections = section_map(rest)
    except (OSError, ValueError) as exc:
        return fail_out([f"{path.name}: {exc}"])
    if fields.get("kind") != "post":
        return fail_out([f"{path.name}: not a post artifact"])
    if path.parent.name != KIND_DIR["post"]:
        return fail_out([f"{path.name}: critic applies to drafts only"])
    if "Body" not in sections:
        return fail_out([f"{path.name}: missing Body section"])
    digest = body_hash(sections["Body"])
    if fields.get("editorial_verdict") != "PASS" or fields.get("editorial_body_sha256") != digest:
        return fail_out([f"{path.name}: editorial PASS required on current body"])
    timestamp = now or utc_timestamp()
    if not valid_utc_timestamp(timestamp):
        return fail_out([f"{path.name}: critic_reviewed_at must be UTC YYYY-MM-DDTHH:MM:SSZ"])
    fields["critic_verdict"] = findings.verdict
    fields["critic_body_sha256"] = digest
    fields["critic_reviewed_at"] = timestamp
    section = critic_mod.render_critic_section(findings)
    rest = critic_mod.upsert_critic_section(rest, section)
    rendered = render_frontmatter(fields, POST_FIELD_ORDER) + rest
    write_atomic(path, rendered)
    claims = load_claims()
    hooks = load_hooks()
    errors = check_artifact(path, claims, hooks, content_root)
    if errors:
        return fail_out(errors)
    print(findings.verdict)
    return 0


def approve_post(path: Path, content_root: Path, now: str | None = None) -> int:
    """Record explicit human approval and move the canonical post to content/approved/.

    Does not run li-review, does not rewrite the body, and does not publish.
    """
    if not path.is_file():
        return fail_out([f"{path}: artifact does not exist"])
    try:
        path.resolve().relative_to(content_root.resolve())
    except ValueError:
        return fail_out([f"{path.name}: artifact is outside content/"])
    try:
        text = path.read_text(encoding="utf-8")
        fields, rest = parse_frontmatter(text)
        sections = section_map(rest)
    except (OSError, ValueError) as exc:
        return fail_out([f"{path.name}: {exc}"])
    if fields.get("kind") != "post":
        return fail_out([f"{path.name}: not a post artifact"])
    if path.parent.name == PUBLISHED_DIR or fields.get("status") == "published":
        return fail_out([f"{path.name}: already published"])

    verdict = fields.get("review_verdict", "")
    review_hash = fields.get("review_body_sha256", "")
    status = fields.get("status", "")
    if "Body" not in sections:
        return fail_out([f"{path.name}: missing Body section", "review must run first"])
    digest = body_hash(sections["Body"])
    if status not in {"reviewed", "approved"} or not verdict or not review_hash:
        return fail_out([
            f"{path.name}: review must run first",
            "A review PASS is required before approval. This command does not run li-review.",
        ])
    if verdict != "PASS":
        return fail_out([
            f"{path.name}: review_verdict is {verdict}",
            "Approval requires review_verdict PASS. This command does not run li-review.",
        ])
    if review_hash != digest or fields.get("body_sha256") != digest:
        return fail_out([
            f"{path.name}: review is stale",
            "review must run first. This command does not run li-review.",
        ])
    editorial_verdict = fields.get("editorial_verdict", "")
    editorial_hash = fields.get("editorial_body_sha256", "")
    if not editorial_verdict or not editorial_hash:
        return fail_out([
            f"{path.name}: editorial review must run first",
            "This command does not run li-editorial.",
        ])
    if editorial_verdict != "PASS":
        return fail_out([
            f"{path.name}: editorial_verdict is {editorial_verdict}",
            "Approval requires editorial_verdict PASS.",
        ])
    if editorial_hash != digest:
        return fail_out([
            f"{path.name}: editorial review is stale",
            "Run li-editorial on the current body.",
        ])
    critic_verdict = fields.get("critic_verdict", "")
    critic_hash = fields.get("critic_body_sha256", "")
    if critic_verdict or critic_hash:
        if critic_verdict != "PASS":
            return fail_out([
                f"{path.name}: critic_verdict is {critic_verdict or 'missing'}",
                "Approval requires critic_verdict PASS when critic metadata is present.",
            ])
        if critic_hash != digest:
            return fail_out([
                f"{path.name}: critic review is stale",
                "Run li-critic on the current body.",
            ])
        critic_section = sections.get("Critic review", "")
        parsed_critic = critic_mod.parse_critic_section(critic_section)
        if parsed_critic and parsed_critic.blocking_issues:
            return fail_out([
                f"{path.name}: critic has blocking issues",
                "Resolve critic feedback before approval.",
            ])
    human_decision = fields.get("human_editorial_decision", "")
    if human_decision in {"REVISE", "DROP"}:
        return fail_out([
            f"{path.name}: human_editorial_decision is {human_decision}",
            "Human judgment blocked approval. Clear or set APPROVE after revising.",
        ])
    if human_decision and human_decision not in strategy_mod.HUMAN_EDITORIAL_DECISIONS:
        return fail_out([f"{path.name}: human_editorial_decision must be APPROVE, REVISE, or DROP"])
    preview_hash = fields.get("preview_body_sha256", "")
    preview_at = fields.get("preview_generated_at", "")
    if not preview_hash or not preview_at:
        return fail_out([
            f"{path.name}: preview must run first",
            "This command does not run li-preview.",
        ])
    if preview_hash != digest:
        return fail_out([
            f"{path.name}: preview is stale",
            "Run li-preview on the current body and visual.",
        ])
    import visual as visual_mod  # noqa: WPS433

    post_id = fields.get("id", path.stem)
    on_disk_visual, _ = visual_mod.read_visual_state(content_root, post_id)
    preview_visual = fields.get("preview_visual_sha256", "") or ""
    expected_visual = on_disk_visual or ""
    if preview_visual != expected_visual:
        return fail_out([
            f"{path.name}: preview visual is stale",
            "Run li-preview after the current visual state.",
        ])
    if status == "approved" and fields.get("approved_body_sha256") != digest:
        return fail_out([
            f"{path.name}: approval is stale",
            "review must run first. This command does not run li-review.",
        ])

    claims = load_claims()
    hooks = load_hooks()
    errors = check_artifact(path, claims, hooks, content_root)
    if errors:
        return fail_out(errors)

    if (
        status == "approved"
        and fields.get("approval_status") == "approved"
        and fields.get("approved_body_sha256") == digest
        and path.parent.name == APPROVED_DIR
    ):
        print(f"already approved {fields.get('id', path.stem)}")
        return 0

    timestamp = now or utc_timestamp()
    if not valid_utc_timestamp(timestamp):
        return fail_out([f"{path.name}: approved_at must be a UTC timestamp YYYY-MM-DDTHH:MM:SSZ"])

    post_id = fields.get("id", path.stem)
    destination = content_root / APPROVED_DIR / f"{post_id}.md"
    if destination.exists() and destination.resolve() != path.resolve():
        return fail_out([f"{path.name}: duplicate id {post_id} also at {destination}"])

    fields["status"] = "approved"
    fields["approval_status"] = "approved"
    fields["approved_body_sha256"] = digest
    fields["approved_visual_sha256"] = expected_visual
    fields["approved_at"] = timestamp
    rendered = render_frontmatter(fields, POST_FIELD_ORDER) + rest
    write_atomic(destination, rendered)
    errors = check_artifact(destination, claims, hooks, content_root)
    if errors:
        if destination.resolve() != path.resolve() and destination.exists():
            destination.unlink()
        elif destination.resolve() == path.resolve():
            write_atomic(path, text)
        return fail_out(errors)

    try:
        append_history(
            content_root,
            {
                "event": "approved",
                "post_id": post_id,
                "body_sha256": digest,
                "timestamp": timestamp,
            },
        )
    except (OSError, ValueError) as exc:
        if destination.resolve() != path.resolve() and destination.exists():
            destination.unlink()
        return fail_out([f"{path.name}: history was not recorded: {exc}"])

    if destination.resolve() != path.resolve():
        try:
            path.unlink()
        except OSError as exc:
            return fail_out([
                f"{path.name}: approved copy is at {destination}, and the draft could not be removed: {exc}",
            ])
    print(f"approved {post_id}")
    print(destination)
    return 0


def reopen_post(path: Path, content_root: Path, now: str | None = None) -> int:
    """Move one valid approved post back to drafts without editing the body.

    Clears human approval and the semantic review. Does not humanize, rewrite,
    review, approve, or publish. The previous approval event stays in history.
    """
    if not path.is_file():
        return fail_out([f"{path}: artifact does not exist"])
    approved_root = (content_root / APPROVED_DIR).resolve()
    try:
        path.resolve().relative_to(approved_root)
    except ValueError:
        label = path.stem if ID_RE.fullmatch(path.stem) else path.name
        return fail_out([f"{label} is not in content/approved/. Reopen only applies to an approved post."])
    try:
        text = path.read_text(encoding="utf-8")
        fields, rest = parse_frontmatter(text)
        sections = section_map(rest)
    except (OSError, ValueError) as exc:
        return fail_out([f"{path.name}: {exc}"])
    post_id = fields.get("id") or path.stem
    if fields.get("kind") != "post":
        return fail_out([f"{path.name}: not a post artifact"])
    if "Body" not in sections:
        return fail_out([f"{post_id} is not a valid approved artifact.", f"{path.name}: missing Body section"])

    claims = load_claims()
    hooks = load_hooks()
    errors = check_artifact(path, claims, hooks, content_root)
    if errors:
        return fail_out([f"{post_id} is not a valid approved artifact."] + errors)

    destination = content_root / KIND_DIR["post"] / f"{post_id}.md"
    if destination.exists():
        return fail_out([f"{post_id}: drafts already contains this post"])
    if (content_root / PUBLISHED_DIR / f"{post_id}.md").exists():
        return fail_out([f"{post_id}: already published"])

    original_body = sections["Body"]
    digest = body_hash(original_body)
    timestamp = now or utc_timestamp()
    if not valid_utc_timestamp(timestamp):
        return fail_out([f"{path.name}: timestamp must be a UTC timestamp YYYY-MM-DDTHH:MM:SSZ"])

    fields["status"] = "styled"
    fields["body_sha256"] = digest
    fields["review_verdict"] = ""
    fields["review_body_sha256"] = ""
    fields["editorial_verdict"] = ""
    fields["editorial_body_sha256"] = ""
    fields["editorial_reviewed_at"] = ""
    fields["critic_verdict"] = ""
    fields["critic_body_sha256"] = ""
    fields["critic_reviewed_at"] = ""
    fields["preview_body_sha256"] = ""
    fields["preview_visual_sha256"] = ""
    fields["preview_generated_at"] = ""
    fields["approval_status"] = ""
    fields["approved_body_sha256"] = ""
    fields["approved_visual_sha256"] = ""
    fields["approved_at"] = ""
    rest = editorial_mod.remove_editorial_section(rest)
    rest = critic_mod.remove_critic_section(rest)
    rendered = render_frontmatter(fields, POST_FIELD_ORDER) + rest
    try:
        rendered_body = section_map(parse_frontmatter(rendered)[1])["Body"]
    except (ValueError, KeyError) as exc:
        return fail_out([f"{post_id}: reopen would have changed the body: {exc}"])
    if rendered_body != original_body:
        return fail_out([f"{post_id}: reopen would have changed the body"])

    write_atomic(destination, rendered)
    errors = check_artifact(destination, claims, hooks, content_root)
    if errors:
        destination.unlink()
        return fail_out(errors)

    try:
        append_history(
            content_root,
            {
                "event": "reopened",
                "post_id": post_id,
                "body_sha256": digest,
                "timestamp": timestamp,
            },
        )
    except (OSError, ValueError) as exc:
        destination.unlink()
        return fail_out([f"{path.name}: history was not recorded: {exc}"])

    try:
        path.unlink()
    except OSError as exc:
        return fail_out([
            f"{path.name}: reopened copy is at {destination}, and the approved file could not be removed: {exc}",
        ])
    print(f"reopened {post_id}")
    print(destination)
    return 0


def reopen_from_spec(spec: str, content_root: Path, now: str | None = None) -> int:
    if ID_RE.fullmatch(spec) and spec.startswith("POST-"):
        approved = content_root / APPROVED_DIR / f"{spec}.md"
        if not approved.is_file():
            if find_posts(content_root, spec):
                return fail_out([
                    f"{spec} is not in content/approved/. Reopen only applies to an approved post.",
                ])
            return fail_out([f"{spec}: artifact does not exist"])
        extras = [match for match in find_posts(content_root, spec) if match.resolve() != approved.resolve()]
        if extras:
            listed = ", ".join(str(match) for match in extras)
            return fail_out([f"{spec}: duplicate artifacts: {listed}"])
        return reopen_post(approved, content_root, now=now)
    path = Path(spec)
    if not path.is_file():
        return fail_out([f"{spec}: artifact does not exist"])
    return reopen_post(path, content_root, now=now)


def approve_from_spec(spec: str, content_root: Path, now: str | None = None) -> int:
    if ID_RE.fullmatch(spec) and spec.startswith("POST-"):
        matches = find_posts(content_root, spec)
        if not matches:
            return fail_out([f"{spec}: artifact does not exist"])
        if len(matches) > 1:
            listed = ", ".join(str(match) for match in matches)
            return fail_out([f"{spec}: duplicate artifacts: {listed}"])
        return approve_post(matches[0], content_root, now=now)
    path = Path(spec)
    if not path.is_file():
        return fail_out([f"{spec}: artifact does not exist"])
    try:
        path.resolve().relative_to(content_root.resolve())
    except ValueError:
        return fail_out([f"{path.name}: artifact is outside content/"])
    return approve_post(path, content_root, now=now)


def check_idea_strategy_fields(label: str, fields: dict) -> list[str]:
    errors: list[str] = []
    present = [key for key in strategy_mod.IDEA_STRATEGY_FIELDS if fields.get(key)]
    if not present:
        return errors
    for key in strategy_mod.IDEA_STRATEGY_FIELDS:
        if not fields.get(key):
            errors.append(f"{label}: partial strategy metadata; missing {key}")
    lane = fields.get("lane", "")
    if lane and lane not in strategy_mod.CONTENT_LANES:
        errors.append(f"{label}: lane must be one of {sorted(strategy_mod.CONTENT_LANES)}")
    audience = fields.get("audience", "")
    if audience and audience not in strategy_mod.STRATEGY_AUDIENCE:
        errors.append(f"{label}: audience must be one of {sorted(strategy_mod.STRATEGY_AUDIENCE)}")
    for field, allowed in (
        ("standalone_value", strategy_mod.STANDALONE_VALUE),
        ("project_context_required", strategy_mod.PROJECT_CONTEXT),
        ("technical_depth", strategy_mod.TECHNICAL_DEPTH),
    ):
        value = fields.get(field, "")
        if value and value not in allowed:
            errors.append(f"{label}: {field} has invalid value {value!r}")
    return errors


def check_strategy(label: str, fields: dict, topics_section: str, mix_notes: str) -> list[str]:
    errors: list[str] = []
    if not mix_notes.strip():
        errors.append(f"{label}: Mix notes section is empty")
    try:
        topics = parse_records(topics_section, strategy_mod.STRATEGY_TOPIC_FIELDS)
    except ValueError as exc:
        return errors + [f"{label}: Topics: {exc}"]
    if not topics:
        errors.append(f"{label}: strategy has no topics")
    for index, topic in enumerate(topics, start=1):
        errors.extend(strategy_mod.validate_strategy_topic(topic, f"{label}: topic {index}"))
    computed_risk = strategy_mod.compute_concentration_risk(topics)
    recorded = fields.get("concentration_risk", "")
    if recorded in {"true", "false"}:
        expected = "true" if computed_risk else "false"
        if recorded != expected:
            errors.append(f"{label}: concentration_risk must be {expected}")
    elif recorded:
        errors.append(f"{label}: concentration_risk must be true or false")
    return errors


def iter_artifact_paths(content_root: Path) -> list[Path]:
    paths: list[Path] = []
    for name in ("ideas", "plans", "strategy", "drafts", APPROVED_DIR, PUBLISHED_DIR):
        folder = content_root / name
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.md")):
            if path.name == "README.md":
                continue
            paths.append(path)
    return paths


def check_content(content_root: Path, claims: list[dict], hooks: dict) -> list[str]:
    errors: list[str] = []
    seen: dict[str, str] = {}
    for path in iter_artifact_paths(content_root):
        errors.extend(check_artifact(path, claims, hooks, content_root))
        stem = path.stem
        if stem in seen:
            errors.append(f"duplicate id {stem} in {seen[stem]} and {path}")
        seen[stem] = str(path)
    return errors


def public_view(claim: dict) -> dict:
    return {
        "id": claim.get("id"),
        "class": classify(claim),
        "category": claim.get("category"),
        "project": claim.get("project"),
        "statement": claim.get("statement"),
        "qualification": claim.get("qualification", ""),
        "source": claim.get("source"),
        "verification": claim.get("verification"),
        "public_safe": claim.get("public_safe"),
    }


def cmd_eligible(args: argparse.Namespace) -> int:
    rows = [public_view(claim) for claim in load_claims()]
    if args.only:
        rows = [row for row in rows if row["class"] == args.only]
    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return 0
    counts = {name: sum(row["class"] == name for row in rows) for name in ("fact", "opinion", "blocked")}
    print(f"fact: {counts['fact']}")
    print(f"opinion: {counts['opinion']}")
    print(f"blocked: {counts['blocked']}")
    for row in rows:
        print(f"\n{row['class'].upper()} {row['id']} {row['category']}")
        print(f"  statement: {row['statement']}")
        if row["qualification"]:
            print(f"  qualification: {row['qualification']}")
    return 0


def cmd_next_id(args: argparse.Namespace) -> int:
    day = args.date or today_stamp()
    print(next_id(CONTENT, args.kind, day))
    return 0


def today_stamp() -> str:
    from datetime import date

    return date.today().strftime("%Y%m%d")


def cmd_hash(args: argparse.Namespace) -> int:
    text = sys.stdin.read() if args.path == "-" else Path(args.path).read_text(encoding="utf-8")
    if args.artifact:
        _, rest = parse_frontmatter(text)
        sections = section_map(rest)
        if "Body" not in sections:
            print("missing Body section", file=sys.stderr)
            return 1
        text = sections["Body"]
    print(body_hash(text))
    return 0


def cmd_stamp(args: argparse.Namespace) -> int:
    return stamp(Path(args.path), invalidate=args.invalidate, status=args.status)


def apply_editorial(path: Path, content_root: Path, now: str | None = None) -> int:
    if path.parent.name == PUBLISHED_DIR or path.parent.name == APPROVED_DIR:
        return fail_out([f"{path.name}: editorial review applies only to drafts"])
    try:
        text = path.read_text(encoding="utf-8")
        fields, rest = parse_frontmatter(text)
        sections = section_map(rest)
    except (OSError, ValueError) as exc:
        return fail_out([f"{path.name}: {exc}"])
    if fields.get("kind") != "post":
        return fail_out([f"{path.name}: not a post artifact"])
    if fields.get("status") != "reviewed":
        return fail_out([f"{path.name}: editorial review requires status reviewed"])
    if fields.get("review_verdict") != "PASS":
        return fail_out([f"{path.name}: editorial review requires review_verdict PASS"])
    if "Body" not in sections:
        return fail_out([f"{path.name}: missing Body section"])
    digest = body_hash(sections["Body"])
    if fields.get("review_body_sha256") != digest or fields.get("body_sha256") != digest:
        return fail_out([f"{path.name}: semantic review is stale"])
    findings = editorial_mod.editorial_review(sections["Body"], sections.get("Evidence gaps", ""))
    timestamp = now or utc_timestamp()
    if not valid_utc_timestamp(timestamp):
        return fail_out([f"{path.name}: editorial_reviewed_at must be UTC YYYY-MM-DDTHH:MM:SSZ"])
    fields["editorial_verdict"] = findings.verdict
    fields["editorial_body_sha256"] = digest
    fields["editorial_reviewed_at"] = timestamp
    section = editorial_mod.render_editorial_section(findings)
    rest = editorial_mod.upsert_editorial_section(rest, section)
    rendered = render_frontmatter(fields, POST_FIELD_ORDER) + rest
    write_atomic(path, rendered)
    claims = load_claims()
    hooks = load_hooks()
    errors = check_artifact(path, claims, hooks, content_root)
    if errors:
        return fail_out(errors)
    print(findings.verdict)
    return 0


def editorial_from_spec(spec: str, content_root: Path, now: str | None = None) -> int:
    if ID_RE.fullmatch(spec) and spec.startswith("POST-"):
        matches = [p for p in find_posts(content_root, spec) if p.parent.name == KIND_DIR["post"]]
        if not matches:
            return fail_out([f"{spec}: draft post does not exist"])
        if len(matches) > 1:
            return fail_out([f"{spec}: duplicate draft artifacts"])
        return apply_editorial(matches[0], content_root, now=now)
    path = Path(spec)
    if not path.is_file():
        return fail_out([f"{spec}: artifact does not exist"])
    return apply_editorial(path, content_root, now=now)


def apply_preview_metadata(
    path: Path,
    content_root: Path,
    body_sha256: str,
    visual_sha256: str,
    now: str | None = None,
) -> None:
    text = path.read_text(encoding="utf-8")
    fields, rest = parse_frontmatter(text)
    timestamp = now or utc_timestamp()
    fields["preview_body_sha256"] = body_sha256
    fields["preview_visual_sha256"] = visual_sha256 or ""
    fields["preview_generated_at"] = timestamp
    write_atomic(path, render_frontmatter(fields, POST_FIELD_ORDER) + rest)


def cmd_editorial(args: argparse.Namespace) -> int:
    return editorial_from_spec(args.post, CONTENT, now=getattr(args, "now", None))


def cmd_review(args: argparse.Namespace) -> int:
    path = Path(args.path)
    text = path.read_text(encoding="utf-8")
    fields, rest = parse_frontmatter(text)
    if fields.get("kind") != "post":
        print("review only applies to posts", file=sys.stderr)
        return 1
    sections = section_map(rest)
    if "Body" not in sections:
        print("missing Body section", file=sys.stderr)
        return 1
    records = claim_records(sections.get("Claims", ""))
    if records is None:
        print("claims section could not be read", file=sys.stderr)
        return 1
    idea_id = str(fields.get("idea") or "")
    source_bundle = load_source_bundle(CONTENT, idea_id) if idea_id else ""
    verdict, reasons = review_body(
        sections["Body"],
        records,
        load_claims(),
        source_bundle=source_bundle,
    )
    print(verdict)
    for reason in reasons:
        print(f"- {reason}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    claims = load_claims()
    hooks = load_hooks()
    errors = check_artifact(Path(args.path), claims, hooks, CONTENT)
    if errors:
        return fail_out(errors)
    print("ok")
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    return approve_from_spec(args.post, CONTENT)


def cmd_reopen(args: argparse.Namespace) -> int:
    return reopen_from_spec(args.post, CONTENT)


def cmd_check_all(args: argparse.Namespace) -> int:
    claims = load_claims()
    hooks = load_hooks()
    errors = check_content(CONTENT, claims, hooks)
    if errors:
        return fail_out(errors)
    print(f"artifacts: {len(iter_artifact_paths(CONTENT))}")
    print("ok")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check and index local LinkedIn content artifacts.")
    sub = parser.add_subparsers(dest="command", required=True)

    eligible = sub.add_parser("eligible", help="classify proof-registry claims")
    eligible.add_argument("--json", action="store_true")
    eligible.add_argument("--only", choices=("fact", "opinion", "blocked"))
    eligible.set_defaults(func=cmd_eligible)

    nxt = sub.add_parser("next-id", help="next IDEA, PLAN, or POST id for a date")
    nxt.add_argument("kind", choices=tuple(KIND_PREFIX))
    nxt.add_argument("--date", help="YYYYMMDD; default is today")
    nxt.set_defaults(func=cmd_next_id)

    digest = sub.add_parser("hash", help="SHA-256 of normalized text, or of a post body")
    digest.add_argument("path", help="file path, or - for stdin")
    digest.add_argument("--artifact", action="store_true", help="hash the Body section")
    digest.set_defaults(func=cmd_hash)

    stamper = sub.add_parser("stamp", help="write body_sha256 for a post")
    stamper.add_argument("path")
    stamper.add_argument("--invalidate", action="store_true")
    stamper.add_argument("--status", choices=("drafted", "styled"))
    stamper.set_defaults(func=cmd_stamp)

    review = sub.add_parser("review", help="print PASS, NEEDS_EDIT, or BLOCKED for a post body")
    review.add_argument("path")
    review.set_defaults(func=cmd_review)

    editorial = sub.add_parser("editorial", help="record editorial PASS, REVISE, or EVIDENCE_TOO_THIN")
    editorial.add_argument("post", help="POST-YYYYMMDD-NN in content/drafts/")
    editorial.set_defaults(func=cmd_editorial)

    approve = sub.add_parser("approve", help="record explicit human approval for one reviewed post")
    approve.add_argument("post", help="POST-YYYYMMDD-NN or a path under content/")
    approve.set_defaults(func=cmd_approve)

    reopen = sub.add_parser("reopen", help="return one approved post to drafts for editing")
    reopen.add_argument("post", help="POST-YYYYMMDD-NN or a path under content/approved/")
    reopen.set_defaults(func=cmd_reopen)

    check = sub.add_parser("check", help="check one artifact")
    check.add_argument("path")
    check.set_defaults(func=cmd_check)

    check_all = sub.add_parser("check-all", help="check every artifact under content/")
    check_all.set_defaults(func=cmd_check_all)
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
