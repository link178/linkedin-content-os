"""Editorial discovery: sourced developments, editorial assessment, topic choice. Stdlib only.

Python fetches a bounded list of configured public feeds and one public discussion
API. Dates and URLs come from those responses, never from the model. One model
call judges every candidate on editorial dimensions and states, separately, what
the source already concludes and what the post would add. Python then applies the
freshness, grounding, contribution, originality, and feasibility gates, orders the
survivors on qualitative tiers with editorial contribution ahead of news reach,
and uses lane balance only to break a tie.
"""

from __future__ import annotations

import gzip
import hashlib
import html
import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable

import content_engine as engine
import cursor_runtime as cursor_runtime_mod
import strategy_check as strategy_mod

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "editorial-discovery.json"
RESEARCH_DIR = ROOT / "research" / "trends"
OPPORTUNITY_DIR = ROOT / "research" / "opportunities"
RESEARCH_LABEL = "research/trends"
USER_AGENT = "linkedin-content-os-editorial-research/1.0 (local, low-volume feed reader)"
MAX_RESPONSE_BYTES = 4_000_000
PROMPT_VERSION = "editorial-discovery-5"

DECISIONS = frozenset({"PURSUE", "QUALIFY", "REJECT"})
SIGNIFICANCE = ("MINOR", "MODERATE", "MAJOR")
ANGLE = ("WEAK", "ADEQUATE", "STRONG")
GROUNDING = ("INSUFFICIENT", "PARTIAL", "SOLID")
READER_VALUE = ("LOW", "MEDIUM", "HIGH")
CONTRIBUTION_TYPES = frozenset(
    {"CONSEQUENCE", "TRADE_OFF", "DECISION_CRITERION", "COMPARISON", "SYNTHESIS", "IMPLICATION"}
)
NO_CONTRIBUTION_TYPES = frozenset({"RESTATES_SOURCE", "GENERIC"})
OVERLAP = frozenset({"NONE", "PARTIAL", "SAME_THESIS"})
AUDIENCE_ORDER = ("SPECIALIST", "AI_ENGINEERING", "BROAD_TECH")
TIMELINESS_ORDER = ("EVERGREEN", "CURRENT")
CONTRIBUTION_SCOPES = frozenset({"GENERAL_DECISION", "SPECIALIST_GUIDANCE"})
# Eligible trends only reach COVERAGE_ONLY or CURRENT_DEVELOPMENT; STALE is a gate.
DEVELOPMENT_KIND_ORDER = ("COVERAGE_ONLY", "CURRENT_DEVELOPMENT")
TREND_TOPIC_FIELDS = (
    "topic",
    "lane",
    "standalone_value",
    "personal_evidence_strength",
    "project_context_required",
    "technical_depth",
    "visual_potential",
    "why_this_author",
    "why_reader_cares",
)
# Editorial dimensions in priority order. Lane balance is applied after all of them.
# Unattempted sources and verified support count before reach or headline significance.
RANK_DIMENSIONS = (
    "prior_attempt",
    "angle_strength",
    "reader_value",
    "development_kind",
    "audience",
    "significance",
    "grounding",
    "timeliness",
)
RANK_ORDERS = {
    "prior_attempt": (True, False),
    "angle_strength": ANGLE,
    "reader_value": READER_VALUE,
    "development_kind": DEVELOPMENT_KIND_ORDER,
    "audience": AUDIENCE_ORDER,
    "significance": SIGNIFICANCE,
    "grounding": GROUNDING,
    "timeliness": TIMELINESS_ORDER,
}
URL_IN_TEXT = re.compile(r"https?://[^\s\)>\"]+")

TITLE_STOP = frozenset(
    {
        "about", "after", "again", "against", "also", "amid", "announces", "being", "best", "better",
        "between", "build", "building", "can't", "could", "does", "doesn't", "down", "every", "first",
        "from", "gets", "give", "going", "have", "here", "into", "just", "launch", "launches", "like",
        "make", "makes", "many", "more", "most", "much", "need", "news", "next", "only", "over", "says",
        "should", "some", "than", "that", "their", "them", "then", "there", "these", "they", "this",
        "those", "today", "update", "updates", "using", "very", "want", "week", "what", "when", "where",
        "which", "while", "will", "with", "without", "would", "year", "your", "2025", "2026", "2027",
    }
)
RETRIEVAL_PREFIX = re.compile(r"retriev\w*\W{1,3}(?:on\W{1,2})?$", re.IGNORECASE)
MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
ISO_DATE = re.compile(r"\b(20\d\d)-(\d\d)-(\d\d)\b")
DAY_MONTH = re.compile(r"\b(\d{1,2}) (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.? (20\d\d)\b")
MONTH_DAY = re.compile(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.? (\d{1,2}),? (20\d\d)\b")
DAY_MONTH_NO_YEAR = re.compile(
    r"\b(\d{1,2}) (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\b"
)
MONTH_DAY_NO_YEAR = re.compile(
    r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.? (\d{1,2})\b"
)


class DiscoveryError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(text: str) -> datetime | None:
    try:
        value = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def default_fetch(url: str, timeout: int) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip", "Accept": "*/*"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = response.read(MAX_RESPONSE_BYTES)
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return data


@dataclass
class DiscoverySettings:
    config: dict
    research_dir: Path = RESEARCH_DIR
    opportunity_dir: Path = OPPORTUNITY_DIR
    fetch: Callable[[str, int], bytes] = default_fetch
    now: Callable[[], datetime] = utc_now
    force_refresh: bool = False
    allow_refresh: bool = True

    @classmethod
    def load(cls, path: Path = CONFIG_PATH, **kwargs) -> "DiscoverySettings":
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise DiscoveryError("DISCOVERY_FAILURE", f"discovery config unreadable: {path}: {exc}") from exc
        return cls(config=config, **kwargs)

    def get(self, key: str, default):
        return self.config.get(key, default)


# --------------------------------------------------------------------------- parsing


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def html_to_text(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", " ", fragment or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def parse_date(text: str) -> datetime | None:
    text = (text or "").strip()
    if not text:
        return None
    try:
        value = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        value = parse_iso(text)
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def parse_feed(data: bytes) -> list[dict]:
    """RSS 2.0 and Atom items as title, url, published, summary."""
    root = ET.fromstring(data)
    items: list[dict] = []
    for element in root.iter():
        if _local(element.tag) not in {"item", "entry"}:
            continue
        title = link = summary = published = updated = ""
        for child in element:
            name = _local(child.tag)
            text = (child.text or "").strip()
            if name == "title":
                title = html_to_text(text)
            elif name == "link":
                href = child.attrib.get("href", "")
                if href and child.attrib.get("rel", "alternate") == "alternate" and not link:
                    link = href
                elif text and not link:
                    link = text
            elif name in {"pubdate", "published", "date", "issued"} and not published:
                published = text
            elif name == "updated" and not updated:
                updated = text
            elif name in {"description", "summary"} and not summary:
                summary = html_to_text(text)
            elif name in {"encoded", "content"} and not summary:
                summary = html_to_text(text)
        when = parse_date(published) or parse_date(updated)
        if title and link and when:
            items.append({"title": title, "url": link.strip(), "published": when, "summary": summary[:800]})
    return items


class _ParagraphParser(HTMLParser):
    SKIP = frozenset({"script", "style", "nav", "header", "footer", "aside", "noscript", "svg", "form", "button"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.depth = 0
        self.buffer: list[str] = []
        self.paragraphs: list[str] = []

    def _flush(self) -> None:
        text = re.sub(r"\s+", " ", "".join(self.buffer)).strip()
        if len(text) >= 60:
            self.paragraphs.append(text)
        self.buffer = []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag == "p":
            if self.depth:
                self._flush()
            self.depth = 1

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        elif tag == "p" and self.depth:
            self.depth = 0
            self._flush()

    def handle_data(self, data):
        if self.depth and not self.skip:
            self.buffer.append(data)


SCOPE_WORD_RE = re.compile(
    r"\b(?:internal|test|preview|production|customer|burst|sustained|rollout)\b",
    re.IGNORECASE,
)
QUANTITY_HINT_RE = re.compile(
    r"\d+|\bdozens?\b|\bhundreds?\b|\bthousands?\b|\bper second\b",
    re.IGNORECASE,
)


def _head_excerpt(paragraphs: list[str], limit: int) -> tuple[str, int]:
    """Return a paragraph-aligned head slice and how many paragraphs it fully includes."""
    if not paragraphs:
        return "", 0
    parts: list[str] = []
    total = 0
    full_count = 0
    for para in paragraphs:
        sep = 1 if parts else 0
        if total + sep + len(para) <= limit:
            parts.append(para)
            total += sep + len(para)
            full_count += 1
            continue
        if not parts:
            return para[:limit], 0
        break
    return " ".join(parts), full_count


def _is_footer_paragraph(paragraph: str) -> bool:
    folded = paragraph.casefold()
    if len(paragraph) < 120:
        return True
    if re.search(r"\bis a (?:principal|senior|staff|director|engineer|writer)\b", folded):
        return True
    return "working on" in folded and len(paragraph) < 400


def _paragraph_evidence_priority(paragraph: str) -> int:
    folded = paragraph.casefold()
    if "private preview" in folded or "limited private preview" in folded:
        return 4
    has_qty = bool(QUANTITY_HINT_RE.search(folded))
    has_scope = bool(SCOPE_WORD_RE.search(folded))
    if has_qty and has_scope:
        return 3
    if has_scope or has_qty:
        return 2
    return 1


def _evidence_span(paragraphs: list[str], head_full_count: int, budget: int) -> str:
    if budget <= 0 or head_full_count >= len(paragraphs):
        return ""
    later = paragraphs[head_full_count:]
    if not later:
        return ""
    chosen: list[str] = []
    remaining = budget

    def add_para(para: str) -> None:
        nonlocal remaining
        if not para or remaining <= 0:
            return
        sep = 2 if chosen else 0
        need = len(para) + sep
        if need > remaining:
            return
        chosen.append(para)
        remaining -= need

    tail = list(later)
    while tail and _is_footer_paragraph(tail[-1]):
        tail.pop()
    if tail:
        add_para(tail[-1])
    ranked = sorted(
        (p for p in later if p not in chosen),
        key=lambda item: (_paragraph_evidence_priority(item), len(item)),
        reverse=True,
    )
    for para in ranked:
        add_para(para)
    return "  ".join(chosen)


def article_excerpt(data: bytes, limit: int, evidence_limit: int = 1600) -> str:
    parser = _ParagraphParser()
    try:
        parser.feed(data.decode("utf-8", errors="replace"))
        parser.close()
    except Exception:  # noqa: BLE001 - malformed HTML is an empty excerpt, not a crash
        return ""
    paragraphs = parser.paragraphs
    if not paragraphs:
        return ""
    head, head_count = _head_excerpt(paragraphs, limit)
    evidence = _evidence_span(paragraphs, head_count, evidence_limit)
    if evidence:
        return f"{head}  {evidence}" if head else evidence
    return head


def normalize_url(url: str) -> str:
    parts = urllib.parse.urlsplit(url.strip())
    host = parts.netloc.casefold()
    if host.startswith("www."):
        host = host[4:]
    return f"{host}{parts.path.rstrip('/')}"


def urls_in_text(text: str) -> list[str]:
    """Extract and normalize http(s) URLs from prose such as a strategy why_now or idea note."""
    urls: list[str] = []
    for match in URL_IN_TEXT.findall(text or ""):
        cleaned = match.rstrip(".,;:")
        if cleaned:
            urls.append(normalize_url(cleaned))
    return urls


def domain(url: str) -> str:
    host = urllib.parse.urlsplit(url).netloc.casefold()
    return host[4:] if host.startswith("www.") else host


def title_tokens(text: str) -> set[str]:
    return {
        word
        for word in re.findall(r"[a-z0-9][a-z0-9'.-]*[a-z0-9]|[a-z0-9]", text.casefold())
        if len(word) >= 4 and word not in TITLE_STOP and word not in strategy_mod.GENERIC_TOKENS
    }


def _same_story(a: set[str], b: set[str], floor: int = 3, ratio: float = 0.5) -> bool:
    shared = a & b
    return len(shared) >= floor and len(shared) / max(1, min(len(a), len(b))) >= ratio


# --------------------------------------------------------------------------- research


def collect_items(settings: DiscoverySettings, now: datetime) -> tuple[list[dict], list[dict]]:
    window = timedelta(days=int(settings.get("item_window_days", 10)))
    per_source = int(settings.get("max_items_per_source", 12))
    timeout = int(settings.get("request_timeout_sec", 15))
    items: list[dict] = []
    report: list[dict] = []
    for source in settings.get("sources", []):
        entry = {"id": source.get("id"), "name": source.get("name"), "url": source.get("url"), "status": "ok", "items": 0, "error": ""}
        try:
            parsed = parse_feed(settings.fetch(str(source["url"]), timeout))
        except Exception as exc:  # noqa: BLE001 - one failed source is recorded, not fatal
            entry["status"] = "error"
            entry["error"] = f"{type(exc).__name__}: {exc}"[:200]
            report.append(entry)
            continue
        fresh = [
            item
            for item in parsed
            if now - window <= item["published"] <= now + timedelta(days=1)
        ]
        fresh.sort(key=lambda item: item["published"], reverse=True)
        for item in fresh[:per_source]:
            item.update(
                {
                    "source_id": source.get("id"),
                    "source_name": source.get("name"),
                    "tier": source.get("tier", "reporting"),
                    "categories": list(source.get("categories", [])),
                }
            )
            items.append(item)
        entry["items"] = len(fresh[:per_source])
        report.append(entry)
    return items, report


COMMON_CAPITALS = frozenset(
    {
        "the", "how", "why", "what", "when", "who", "new", "now", "its", "is", "are", "with", "for", "and", "after",
        "from", "this", "that", "will", "can", "your", "our", "inside", "introducing", "beyond", "into", "here",
    }
)


def name_pairs(title: str) -> set[str]:
    """Adjacent capitalized words such as a person or product name. Title-cased headlines carry no signal."""
    words = [re.sub(r"['’]s$", "", word) for word in re.findall(r"[A-Za-z][A-Za-z'’]+", title)]
    if not words or sum(1 for word in words if word[0].isupper()) / len(words) >= 0.7:
        return set()
    pairs = set()
    for first, second in zip(words, words[1:]):
        if not (first[0].isupper() and second[0].isupper()):
            continue
        if len(first) < 3 or len(second) < 3:
            continue
        if first.casefold() in COMMON_CAPITALS or second.casefold() in COMMON_CAPITALS:
            continue
        pairs.add(f"{first.casefold()} {second.casefold()}")
    return pairs


def cluster_items(items: list[dict]) -> list[dict]:
    """Group coverage of one development: same URL, a shared name pair, or three shared title words."""
    clusters: list[dict] = []
    for item in sorted(items, key=lambda entry: entry["published"], reverse=True):
        tokens = title_tokens(item["title"])
        names = name_pairs(item["title"])
        url_key = normalize_url(item["url"])

        def related(cluster: dict) -> bool:
            if url_key in cluster["urls"]:
                return True
            return any(names & member["names"] or _same_story(tokens, member["tokens"]) for member in cluster["members"])

        home = next((cluster for cluster in clusters if related(cluster)), None)
        if home is None:
            home = {"tokens": set(), "urls": set(), "items": [], "members": []}
            clusters.append(home)
        home["tokens"] |= tokens
        home["members"].append({"tokens": tokens, "names": names})
        if url_key not in home["urls"]:
            home["items"].append(item)
        home["urls"].add(url_key)
    return clusters


def fetch_discussion(settings: DiscoverySettings, now: datetime) -> tuple[list[dict], dict]:
    conf = settings.get("discussion", {}) or {}
    since = int((now - timedelta(days=int(settings.get("item_window_days", 10)))).timestamp())
    points = int(conf.get("min_points", 100))
    url = (
        "https://hn.algolia.com/api/v1/search_by_date?tags=story&hitsPerPage=500"
        f"&numericFilters=created_at_i%3E{since},points%3E%3D{points}"
    )
    status = {"provider": conf.get("provider", "Hacker News (Algolia search API)"), "url": url, "status": "ok", "error": "", "stories": 0}
    try:
        payload = json.loads(settings.fetch(url, int(settings.get("request_timeout_sec", 15))).decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        status["status"] = "error"
        status["error"] = f"{type(exc).__name__}: {exc}"[:200]
        return [], status
    stories = []
    for hit in payload.get("hits", []) if isinstance(payload, dict) else []:
        title = str(hit.get("title") or "")
        if not title:
            continue
        stories.append(
            {
                "title": title,
                "url": str(hit.get("url") or ""),
                "points": int(hit.get("points") or 0),
                "comments": int(hit.get("num_comments") or 0),
                "created_at": str(hit.get("created_at") or ""),
                "discussion_url": f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
            }
        )
    status["stories"] = len(stories)
    return stories, status


def match_discussion(cluster: dict, stories: list[dict]) -> list[dict]:
    matched = []
    members = cluster.get("members") or [{"tokens": cluster["tokens"], "names": set()}]
    for story in stories:
        tokens, names = title_tokens(story["title"]), name_pairs(story["title"])
        if story["url"] and normalize_url(story["url"]) in cluster["urls"]:
            matched.append(story)
        elif any(names & m["names"] or _same_story(tokens, m["tokens"], floor=3, ratio=0.6) for m in members):
            matched.append(story)
    matched.sort(key=lambda story: story["points"], reverse=True)
    return matched[:3]


def bound_candidates(clusters: list[dict], settings: DiscoverySettings) -> list[dict]:
    """Bounded research scope. This orders what the assessor reads, not what wins."""
    limit = int(settings.get("max_candidates", 16))
    per_category = int(settings.get("max_candidates_per_category", 3))

    def order(cluster: dict):
        domains = {domain(item["url"]) for item in cluster["items"]}
        points = max((story["points"] for story in cluster["discussion"]), default=0)
        latest = max(item["published"] for item in cluster["items"])
        return (-len(domains), -points, -latest.timestamp())

    ranked = sorted(clusters, key=order)
    chosen: list[dict] = []
    counts: dict[str, int] = {}
    for cluster in ranked:
        category = (cluster["items"][0].get("categories") or ["UNCATEGORIZED"])[0]
        if counts.get(category, 0) >= per_category:
            continue
        counts[category] = counts.get(category, 0) + 1
        chosen.append(cluster)
        if len(chosen) >= limit:
            return chosen
    for cluster in ranked:
        if len(chosen) >= limit:
            break
        if cluster not in chosen:
            chosen.append(cluster)
    return chosen


def refresh_research(settings: DiscoverySettings, snapshot_id: str) -> dict:
    now = settings.now()
    items, source_report = collect_items(settings, now)
    stories, discussion_status = fetch_discussion(settings, now)
    clusters = cluster_items(items)
    for cluster in clusters:
        cluster["discussion"] = match_discussion(cluster, stories)
    bounded = bound_candidates(clusters, settings)
    excerpt_chars = int(settings.get("excerpt_chars", 1600))
    evidence_chars = int(settings.get("evidence_excerpt_chars", 1600))
    per_candidate = int(settings.get("max_sources_per_candidate", 3))
    timeout = int(settings.get("request_timeout_sec", 15))
    tier_rank = {"primary": 0, "reporting": 1, "analysis": 2}
    candidates: list[dict] = []
    for index, cluster in enumerate(bounded, start=1):
        ordered = sorted(cluster["items"], key=lambda item: (tier_rank.get(item["tier"], 3), item["published"]))
        seen_domains: set[str] = set()
        members = []
        for item in ordered:
            if domain(item["url"]) not in seen_domains:
                seen_domains.add(domain(item["url"]))
                members.append(item)
        members += [item for item in ordered if item not in members]
        sources = []
        for item in members[:per_candidate]:
            excerpt, origin, error = "", "feed_summary", ""
            try:
                excerpt = article_excerpt(settings.fetch(item["url"], timeout), excerpt_chars, evidence_chars)
            except Exception as exc:  # noqa: BLE001
                error = f"{type(exc).__name__}: {exc}"[:160]
            if len(excerpt) >= 200:
                origin = "article"
            else:
                excerpt = item["summary"][:excerpt_chars]
            sources.append(
                {
                    "source_id": item["source_id"],
                    "source_name": item["source_name"],
                    "tier": item["tier"],
                    "title": item["title"],
                    "url": item["url"],
                    "published": iso(item["published"]),
                    "excerpt": excerpt,
                    "excerpt_origin": origin,
                    "fetch_error": error,
                }
            )
        categories: list[str] = []
        for item in cluster["items"]:
            for category in item.get("categories", []):
                if category not in categories:
                    categories.append(category)
        dates = [item["published"] for item in cluster["items"]]
        candidates.append(
            {
                "id": f"C{index:02d}",
                "kind": "TREND",
                "title": members[0]["title"],
                "categories": categories,
                "first_published": iso(min(dates)),
                "latest_published": iso(max(dates)),
                "independent_sources": len({domain(item["url"]) for item in cluster["items"]}),
                "sources": sources,
                "discussion": cluster["discussion"],
            }
        )
    ok_sources = [entry for entry in source_report if entry["status"] == "ok"]
    if not ok_sources:
        status = "FAILED"
    elif len(ok_sources) < len(source_report) or discussion_status["status"] != "ok":
        status = "PARTIAL"
    else:
        status = "OK"
    return {
        "snapshot_id": snapshot_id,
        "kind": "editorial_discovery_snapshot",
        "retrieved_at": iso(now),
        "status": status,
        "window_days": int(settings.get("item_window_days", 10)),
        "sources": source_report,
        "discussion": discussion_status,
        "items_in_window": len(items),
        "clusters": len(clusters),
        "candidates": candidates,
    }


def next_snapshot_id(research_dir: Path, now: datetime) -> str:
    day = now.strftime("%Y-%m-%d")
    highest = 0
    if research_dir.is_dir():
        for path in research_dir.glob(f"{day}-discovery-*.json"):
            match = re.fullmatch(rf"{day}-discovery-(\d\d)", path.stem)
            if match:
                highest = max(highest, int(match.group(1)))
    return f"{day}-discovery-{highest + 1:02d}"


def render_snapshot_markdown(snapshot: dict) -> str:
    lines = [
        f"# Editorial discovery research — {snapshot['snapshot_id']}",
        "",
        f"Retrieved: {snapshot['retrieved_at']} (UTC). Status: {snapshot['status']}. Window: {snapshot['window_days']} days.",
        "Facts below are what each listed source states at retrieval. Discussion lines are Hacker News counts, not LinkedIn activity.",
        "This note contains no interpretation. Editorial assessment is stored separately under research/opportunities/.",
        "",
        "## Sources checked",
        "",
    ]
    for source in snapshot["sources"]:
        detail = f"{source['items']} items in window" if source["status"] == "ok" else f"ERROR {source['error']}"
        lines.append(f"- {source['name']} — {source['url']} — {detail}")
    discussion = snapshot.get("discussion", {})
    lines.append(f"- {discussion.get('provider', 'discussion')} — {discussion.get('status')} — {discussion.get('stories', 0)} stories")
    for candidate in snapshot["candidates"]:
        lines += ["", "---", "", f"## {candidate['id']} — {candidate['title']}", ""]
        lines.append(f"- **independent sources:** {candidate['independent_sources']}")
        lines.append(f"- **first published:** {candidate['first_published']}; latest: {candidate['latest_published']}")
        for source in candidate["sources"]:
            lines.append(f"- **source:** {source['source_name']} — {source['title']}")
            lines.append(f"  - url: {source['url']}")
            lines.append(f"  - date: {source['published']}")
            lines.append(f"  - excerpt ({source['excerpt_origin']}): {source['excerpt']}")
        for story in candidate.get("discussion", []):
            lines.append(
                f"- **discussion:** Hacker News {story['points']} points, {story['comments']} comments, {story['created_at']} — {story['discussion_url']}"
            )
    return "\n".join(lines) + "\n"


def write_snapshot(settings: DiscoverySettings, snapshot: dict) -> None:
    base = settings.research_dir / snapshot["snapshot_id"]
    engine.write_atomic(base.with_suffix(".json"), json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n")
    engine.write_atomic(base.with_suffix(".md"), render_snapshot_markdown(snapshot))


def latest_snapshot(research_dir: Path) -> dict | None:
    if not research_dir.is_dir():
        return None
    for path in sorted(research_dir.glob("*-discovery-*.json"), reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("kind") == "editorial_discovery_snapshot":
            return data
    return None


def ensure_research(settings: DiscoverySettings) -> tuple[dict, str]:
    """Reuse a fresh snapshot, otherwise refresh. A failed refresh is not replaced by an old one."""
    now = settings.now()
    max_age = timedelta(hours=float(settings.get("snapshot_max_age_hours", 24)))
    previous = latest_snapshot(settings.research_dir)
    if previous and not settings.force_refresh and previous.get("status") != "FAILED":
        retrieved = parse_iso(str(previous.get("retrieved_at") or ""))
        if retrieved and now - retrieved <= max_age:
            return previous, "reused"
    if not settings.allow_refresh:
        return (
            {
                "snapshot_id": "",
                "kind": "editorial_discovery_snapshot",
                "retrieved_at": "",
                "status": "UNAVAILABLE",
                "sources": [],
                "discussion": {},
                "candidates": [],
                "note": "no fresh snapshot and refresh disabled",
            },
            "unavailable",
        )
    snapshot = refresh_research(settings, next_snapshot_id(settings.research_dir, now))
    write_snapshot(settings, snapshot)
    return snapshot, "refreshed" if snapshot["status"] != "FAILED" else "failed"


# --------------------------------------------------------------------------- dates in strategy text


def development_date(text: str, now: datetime) -> datetime | None:
    """Most recent development date named in a why_now, ignoring retrieval dates."""
    found: list[datetime] = []

    def keep(match: re.Match, value: datetime | None) -> None:
        if value is None:
            return
        before = text[match.start() - 1] if match.start() else " "
        after = text[match.end()] if match.end() < len(text) else " "
        if before in "/_" or after in "-_/":
            return
        prefix = text[max(0, match.start() - 24) : match.start()]
        if RETRIEVAL_PREFIX.search(prefix):
            return
        if value <= now + timedelta(days=1):
            found.append(value)

    def build(year: str, month: int, day: str) -> datetime | None:
        try:
            return datetime(int(year), month, int(day), tzinfo=timezone.utc)
        except ValueError:
            return None

    for match in ISO_DATE.finditer(text):
        keep(match, build(match.group(1), int(match.group(2)), match.group(3)))
    for match in DAY_MONTH.finditer(text):
        keep(match, build(match.group(3), MONTHS[match.group(2)[:3].casefold()], match.group(1)))
    for match in MONTH_DAY.finditer(text):
        keep(match, build(match.group(3), MONTHS[match.group(1)[:3].casefold()], match.group(2)))
    return max(found) if found else None


def _resolve_year(month: int, day: int, publication: datetime) -> int:
    """Pick the calendar year for a body date without an explicit year."""
    candidate = datetime(publication.year, month, day, tzinfo=timezone.utc)
    pub = publication.replace(hour=0, minute=0, second=0, microsecond=0)
    cand = candidate.replace(hour=0, minute=0, second=0, microsecond=0)
    if cand > pub:
        return publication.year - 1
    return publication.year


def _body_dates_from_text(text: str, publication: datetime, stale_after: timedelta) -> list[datetime]:
    """Calendar dates named in article body text, not feed timestamps."""
    found: list[datetime] = []
    seen: set[tuple[int, int, int]] = set()
    pub_day = publication.replace(hour=0, minute=0, second=0, microsecond=0)

    def keep(value: datetime, explicit_year: bool) -> bool:
        day = value.replace(hour=0, minute=0, second=0, microsecond=0)
        if day > pub_day + timedelta(days=1):
            return False
        if explicit_year:
            return True
        return pub_day - day <= stale_after

    def add(year: int, month: int, day: str, *, explicit_year: bool) -> None:
        try:
            value = datetime(int(year), month, int(day), tzinfo=timezone.utc)
        except ValueError:
            return
        if not keep(value, explicit_year):
            return
        key = (value.year, value.month, value.day)
        if key in seen:
            return
        seen.add(key)
        found.append(value)

    for match in ISO_DATE.finditer(text):
        add(match.group(1), int(match.group(2)), match.group(3), explicit_year=True)
    for match in DAY_MONTH.finditer(text):
        add(match.group(3), MONTHS[match.group(2)[:3].casefold()], match.group(1), explicit_year=True)
    for match in MONTH_DAY.finditer(text):
        add(match.group(3), MONTHS[match.group(1)[:3].casefold()], match.group(2), explicit_year=True)
    for match in DAY_MONTH_NO_YEAR.finditer(text):
        if re.search(r"20\d\d", text[match.end() : match.end() + 8]):
            continue
        year = _resolve_year(MONTHS[match.group(2)[:3].casefold()], int(match.group(1)), publication)
        add(year, MONTHS[match.group(2)[:3].casefold()], match.group(1), explicit_year=False)
    for match in MONTH_DAY_NO_YEAR.finditer(text):
        if re.search(r"20\d\d", text[match.end() : match.end() + 8]):
            continue
        year = _resolve_year(MONTHS[match.group(1)[:3].casefold()], int(match.group(2)), publication)
        add(year, MONTHS[match.group(1)[:3].casefold()], match.group(2), explicit_year=False)
    found.sort()
    return found


def trend_excerpt_text(candidate: dict) -> str:
    parts = [str(source.get("excerpt") or "") for source in candidate.get("sources", [])]
    return " ".join(parts)


def trend_development_timeline(candidate: dict, now: datetime, stale_after: timedelta) -> dict:
    """Separate article publication from event and material-update dates in body text."""
    publication = parse_iso(str(candidate.get("latest_published") or ""))
    if publication is None:
        publication = now
    publication = publication.replace(hour=0, minute=0, second=0, microsecond=0)
    body_dates = _body_dates_from_text(trend_excerpt_text(candidate), publication, stale_after)
    pub_date = publication.date().isoformat()
    if not body_dates:
        return {
            "publication_date": pub_date,
            "event_date": pub_date,
            "latest_development": pub_date,
            "development_kind": "NEW_EVENT",
            "rank_development_kind": "CURRENT_DEVELOPMENT",
            "stale_event": False,
        }
    event = body_dates[0]
    latest = body_dates[-1]
    event_date = event.date().isoformat()
    latest_dev = latest.date().isoformat()
    age_latest = now - latest.replace(hour=0, minute=0, second=0, microsecond=0)
    age_event = now - event.replace(hour=0, minute=0, second=0, microsecond=0)
    if len(body_dates) >= 2 and latest > event:
        if age_latest > stale_after:
            kind = "STALE"
        else:
            kind = "MATERIAL_UPDATE"
    elif publication.date() > event.date():
        if age_event > stale_after:
            kind = "STALE"
        else:
            kind = "COVERAGE_ONLY"
    else:
        kind = "NEW_EVENT"
    rank_kind = "CURRENT_DEVELOPMENT" if kind in {"NEW_EVENT", "MATERIAL_UPDATE"} else "COVERAGE_ONLY"
    return {
        "publication_date": pub_date,
        "event_date": event_date,
        "latest_development": latest_dev,
        "development_kind": kind,
        "rank_development_kind": rank_kind,
        "stale_event": kind == "STALE",
    }


# --------------------------------------------------------------------------- assessment


QUANTITY_WORDS = frozenset(
    {"half", "quarter", "third", "majority", "most", "all", "every", "doubled", "tripled", "percent", "million", "billion", "thousand"}
)


def statement_supported(text: str, excerpt: str) -> bool:
    """A factual statement is supported when the article excerpt carries its content and its quantities.

    Headlines are excluded: a headline implication is exactly what has to be checked.
    Shared vocabulary alone is not enough; numbers and quantity words must appear too.
    """
    if not excerpt.strip():
        return False
    folded = excerpt.casefold()
    words = set(re.findall(r"[a-z]+", folded))
    for number in engine.statement_numbers(text):
        if not engine.number_in_text(number, excerpt):
            return False
    for word in re.findall(r"[a-z]+", text.casefold()):
        if word in QUANTITY_WORDS and word not in words:
            return False
    tokens = engine.coverage_tokens(text)
    if len(tokens) < 3:
        return False
    hits = sum(1 for token in tokens if token in folded)
    return hits / len(tokens) >= 0.7


def thesis_tokens(*parts: str) -> set[str]:
    return strategy_mod.topic_tokens({"topic": " ".join(part for part in parts if part)})


def same_thesis(candidate_text: str, prior_text: str) -> bool:
    """Deterministic backstop for a recycled conclusion under a different title."""
    a = thesis_tokens(candidate_text)
    b = thesis_tokens(prior_text)
    shared = a & b
    return len(shared) >= 5 and len(shared) / max(1, min(len(a), len(b))) >= 0.5


def restates_source(contribution: str, source_conclusion: str) -> bool:
    """Backstop for a contribution that rewords the source. Caps the angle; it never rejects alone."""
    own = thesis_tokens(contribution)
    if len(own) < 5:
        return False
    return len(own & thesis_tokens(source_conclusion)) / len(own) >= 0.7


def cap_angle(angle: str, ceiling: str, entry: dict, note: str) -> str:
    if _tier(ANGLE, angle) <= _tier(ANGLE, ceiling):
        return angle
    if note:
        entry["notes"].append(f"{note}; angle {angle} capped to {ceiling}")
    return ceiling


def assessment_key(snapshot: dict, strategy_candidates: list[dict], priors: list[dict] | None = None) -> str:
    prior_digest = [
        [p.get("topic", ""), p.get("status", ""), sorted(p.get("source_urls") or [])]
        for p in sorted(priors or [], key=lambda item: str(item.get("topic", "")).casefold())
    ]
    material = {
        "version": PROMPT_VERSION,
        "snapshot": snapshot.get("snapshot_id", ""),
        "trend": [[c["id"], [s["url"] for s in c["sources"]]] for c in snapshot.get("candidates", [])],
        "strategy": [[c["id"], c["topic"].get("topic", "")] for c in strategy_candidates],
        "priors": prior_digest,
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode("utf-8")).hexdigest()


def _compact_claims(claims: list[dict]) -> list[str]:
    lines = []
    for claim in claims:
        if claim.get("public_safe") is not True or claim.get("verification") not in {"verified", "derived"}:
            continue
        statement = re.sub(r"\s+", " ", str(claim.get("statement") or ""))[:170]
        lines.append(f"- {claim.get('id')} ({claim.get('category')}): {statement}")
    return lines


def assessment_prompt(
    snapshot: dict,
    strategy_candidates: list[dict],
    priors: list[dict],
    claims: list[dict],
    settings: DiscoverySettings,
    today: str,
) -> str:
    categories = settings.get("categories", {})
    trend_blocks = []
    for candidate in snapshot.get("candidates", []):
        sources = "\n".join(
            f"  - [{s['source_name']}, {s['tier']}] {s['title']}\n    url: {s['url']}\n    published: {s['published']}\n    excerpt ({s['excerpt_origin']}): {s['excerpt']}"
            for s in candidate["sources"]
        )
        discussion = "\n".join(
            f"  - Hacker News: {d['points']} points, {d['comments']} comments ({d['created_at']}) {d['discussion_url']}"
            for d in candidate.get("discussion", [])
        ) or "  - none recorded"
        trend_blocks.append(
            f"{candidate['id']} (TREND) {candidate['title']}\n"
            f" independent source domains: {candidate['independent_sources']}; latest published {candidate['latest_published']}\n"
            f" sources:\n{sources}\n discussion evidence:\n{discussion}"
        )
    strategy_blocks = [
        f"{c['id']} (STRATEGY, timeliness {c['topic'].get('timeliness')}) {c['topic'].get('topic')}\n"
        f" lane {c['topic'].get('lane')}, audience {c['topic'].get('audience')}\n"
        f" why_now: {c['topic'].get('why_now')}\n why_reader_cares: {c['topic'].get('why_reader_cares')}\n"
        f" why_this_author: {c['topic'].get('why_this_author')}"
        for c in strategy_candidates
    ]
    prior_lines = [
        f"- [{p.get('status', 'attempted')}] {p.get('topic', '')[:160]} — {p.get('insight', '')[:240]}" for p in priors
    ]
    category_lines = [f"- {key}: {value}" for key, value in categories.items()]
    return f"""You are the editorial desk for one author's LinkedIn feed. Today is {today} (UTC).
Do not write files. Do not browse. Every development you may use is quoted below with its source and date.
Read knowledge/content-strategy.md, knowledge/positioning.md, and knowledge/guardrails.md in the workspace.

Task: assess every candidate below as a possible next post. Python will apply gates and choose. Do not rank and do not give numeric scores.
Judge editorial value for a broad technical and professionally interested audience, not popularity and not novelty alone.

Keep four things separate:
A. Verified development: only what the listed source excerpts state, with their dates.
B. Public discussion: only the discussion evidence listed. Never say a subject is trending on LinkedIn.
C. Interpretation: why it matters and the angle. This is the author's judgment, labeled as such.
D. Evergreen: STRATEGY candidates that do not depend on today's news.

Categories (pick the closest; none is mandatory):
{chr(10).join(category_lines)}

Newsworthiness, a compelling source summary, and a worthwhile contribution are three different things. A big or viral event can have a weak contribution; a modest engineering write-up can have a strong one. Hacker News points, headline reach, and brand names are not evidence of angle strength or reader value.

Contribution rules (judge these before angle_strength):
- source_contribution: what the cited reporting already establishes, concludes, or recommends, including any lesson its headline or framing already states. For STRATEGY candidates: what the familiar version of this argument already says.
- editorial_contribution: the additional understanding the post would give a reader who has already read the source. Valid forms: a practical consequence for engineers or businesses, an overlooked trade-off, a decision criterion, a meaningful comparison, a synthesis connecting sources, a defensible broader implication. A clear synthesis counts; artificial novelty is not required.
- contribution_type: CONSEQUENCE|TRADE_OFF|DECISION_CRITERION|COMPARISON|SYNTHESIS|IMPLICATION when the contribution adds understanding. RESTATES_SOURCE when it repeats source_contribution in other words (a headline that already says "X is not Y" makes "X is not Y" a restatement). GENERIC when the conclusion holds for almost any subject or is tautological ("verify outputs", "humans must stay in the loop", "quality matters").
- contribution_support: 0-based indexes into factual_statements that the contribution relies on.
- contribution_premise: index of the one factual_statement the contribution cannot stand without (often the distinction readers are most likely to miss). Must be one of contribution_support.
- contribution_overreach: outcomes, causes, scale, or behavior the angle could be read to imply but the sources do not establish. They are passed to composition as claims to avoid. If editorial_contribution or thesis asserts one of them, the candidate is rejected, so rewrite the contribution to stand without it.
- source_limitations: caveats, scope limits, or distinctions stated in or carried by the sources that a post must keep visible to stay accurate (for example an internal test versus a customer guarantee, or "not in a registry" versus "unknown"). Prefer the distinction the reader is most likely to miss.
- reader_value: HIGH when the target reader learns something they can use to decide, evaluate, or explain; MEDIUM when useful but familiar to most of that audience; LOW when the reader learns little beyond the headline. reader_learns: one sentence naming what they learn or reconsider.
- holds_without_headline: false when the post is only worth reading because of a sensational headline, a famous name, vendor branding, or a viral thread.
- why_publish_now: why this candidate now rather than the other candidates listed here. For evergreen topics say so plainly.

Field rules:
- significance: MAJOR only when the development has practical consequences for many technology professionals or changes a meaningful decision. A small vendor feature is MINOR even when it is the newest item. MODERATE is in between. Significance is about the news, not about the post.
- audience: BROAD_TECH only when a technical or professionally interested reader cares without specialist knowledge. specialist_only true when the impact is limited to specialists.
- contribution_scope: GENERAL_DECISION when the post teaches a decision, trade-off, or consequence a technical reader can apply without stack-specific procedure. SPECIALIST_GUIDANCE when the reader must follow patch steps, CVE detail, vendor commands, or other specialist procedure to benefit. SPECIALIST_GUIDANCE is not BROAD_TECH even when the headline sounds general.
- angle_strength: STRONG only when editorial_contribution is grounded, is not a restatement of source_contribution, and a reader who read the source would still learn from it. ADEQUATE when the contribution is real but modest or familiar. WEAK when the post would be a news recap, a restatement, or a generic lesson.
- thesis: the post's central claim. It carries editorial_contribution; it must not be source_contribution reworded.
- grounding: SOLID when the facts the thesis needs are stated in the excerpts; PARTIAL when some are; INSUFFICIENT otherwise. For STRATEGY candidates judge the why_now sources and general technical knowledge.
- factual_statements: short sentences each stated by the excerpt of a cited source. source_urls must be urls listed for that same candidate. Do not add facts from memory.
- unsupported_implications: what a headline or framing implies that the excerpts do not establish (causation, scale, permanence, who is affected). If the thesis would depend on one, return QUALIFY with a thesis that does not, or REJECT.
- overlap: compare with the prior topics. SAME_THESIS when the conclusion would be substantially the same as a prior one, even under a different title or news hook. PARTIAL needs a concrete difference.
- requires_invented_experience: true when the post only works with a personal anecdote or result the author does not have. The author's eligible claims are listed below. General engineering analysis needs no anecdote.
- composition_feasible: false when the evidence is too thin or the thesis is not worth a coherent post.
- topic (TREND candidates only): fields for composition. lane is one of POSITIONING, TREND_REACTION, PROJECT_SHOWCASE, TECHNICAL_DEEP_DIVE, CAREER_ENGINEERING. standalone_value HIGH|MEDIUM|LOW. personal_evidence_strength HIGH|MEDIUM|LOW|NONE. project_context_required NONE|LIGHT|HIGH. technical_depth ACCESSIBLE|INTERMEDIATE|DEEP. visual_potential ADDITIVE|DECORATIVE|NONE. why_reader_cares must not use a project name as the reason. why_this_author must not claim experience the claims do not state.

Return ONE JSON object: {{"opportunities": [ ... ]}} with exactly one entry per candidate id, each:
{{"id": "C01", "decision": "PURSUE|QUALIFY|REJECT", "category": "<category>", "what_happened": "", "why_now": "",
 "who_is_affected": "", "audience": "BROAD_TECH|AI_ENGINEERING|SPECIALIST", "specialist_only": false,
 "contribution_scope": "GENERAL_DECISION|SPECIALIST_GUIDANCE",
 "significance": "MAJOR|MODERATE|MINOR", "practical_consequence": "", "decision_changed": "",
 "source_contribution": "", "editorial_contribution": "",
 "contribution_type": "CONSEQUENCE|TRADE_OFF|DECISION_CRITERION|COMPARISON|SYNTHESIS|IMPLICATION|RESTATES_SOURCE|GENERIC",
 "contribution_support": [0], "contribution_premise": 0, "contribution_overreach": [""], "source_limitations": [""],
 "reader_value": "HIGH|MEDIUM|LOW", "reader_learns": "", "holds_without_headline": true, "why_publish_now": "",
 "angle_strength": "STRONG|ADEQUATE|WEAK", "thesis": "", "tension": "", "beyond_summary": "",
 "grounding": "SOLID|PARTIAL|INSUFFICIENT", "factual_statements": [{{"text": "", "source_urls": [""]}}],
 "interpretations": [""], "unsupported_implications": [""], "unresolved_questions": [""],
 "overlap": {{"level": "NONE|PARTIAL|SAME_THESIS", "prior": "", "difference": ""}},
 "composition_feasible": true, "requires_invented_experience": false, "relevant_claim_ids": [""],
 "topic": {{"topic": "", "lane": "", "standalone_value": "", "personal_evidence_strength": "", "project_context_required": "",
 "technical_depth": "", "visual_potential": "", "why_this_author": "", "why_reader_cares": ""}},
 "reasons": "one or two sentences of editorial judgment"}}

TREND candidates (research snapshot {snapshot.get('snapshot_id') or 'none'}, status {snapshot.get('status')}):
{chr(10).join(trend_blocks) or '(none: no sourced development is available for this run)'}

STRATEGY candidates:
{chr(10).join(strategy_blocks) or '(none)'}

Prior topics already published, approved, dropped, or attempted:
{chr(10).join(prior_lines) or '- none'}

Author eligible claims (id, category, statement):
{chr(10).join(_compact_claims(claims)) or '- none'}
"""


def _enum(value, allowed) -> str:
    text = str(value or "").strip().upper()
    return text if text in allowed else ""


def parse_assessment(payload: dict, valid_ids: set[str]) -> dict[str, dict]:
    """Validate model output. Unknown shape is a failure; a bad entry is rejected alone."""
    entries = payload.get("opportunities") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        raise DiscoveryError("DISCOVERY_FAILURE", "assessment response has no opportunities list")
    parsed: dict[str, dict] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        cid = str(entry.get("id") or "").strip()
        if cid not in valid_ids or cid in parsed:
            continue
        problems = []
        record = {
            "id": cid,
            "decision": _enum(entry.get("decision"), DECISIONS),
            "category": str(entry.get("category") or ""),
            "audience": _enum(entry.get("audience"), set(AUDIENCE_ORDER)),
            "significance": _enum(entry.get("significance"), set(SIGNIFICANCE)),
            "angle_strength": _enum(entry.get("angle_strength"), set(ANGLE)),
            "grounding": _enum(entry.get("grounding"), set(GROUNDING)),
            "reader_value": _enum(entry.get("reader_value"), set(READER_VALUE)),
            "contribution_type": _enum(entry.get("contribution_type"), CONTRIBUTION_TYPES | NO_CONTRIBUTION_TYPES),
            "holds_without_headline": entry.get("holds_without_headline") is not False,
            "specialist_only": entry.get("specialist_only") is True,
            "composition_feasible": entry.get("composition_feasible") is not False,
            "requires_invented_experience": entry.get("requires_invented_experience") is True,
            "contribution_scope": _enum(entry.get("contribution_scope"), CONTRIBUTION_SCOPES),
        }
        for key in ("decision", "audience", "significance", "angle_strength", "grounding"):
            if not record[key]:
                problems.append(f"{key} missing or invalid")
        for key in (
            "what_happened", "why_now", "who_is_affected", "practical_consequence", "decision_changed",
            "thesis", "tension", "beyond_summary", "reasons",
            "source_contribution", "editorial_contribution", "reader_learns", "why_publish_now",
        ):
            record[key] = re.sub(r"\s+", " ", str(entry.get(key) or "")).strip()
        if record["decision"] != "REJECT":
            for key in ("thesis", "source_contribution", "editorial_contribution", "reader_learns"):
                if not record[key]:
                    problems.append(f"{key} missing")
            for key in ("contribution_type", "reader_value"):
                if not record[key]:
                    problems.append(f"{key} missing or invalid")
        for key in (
            "interpretations", "unsupported_implications", "unresolved_questions", "relevant_claim_ids",
            "contribution_overreach", "source_limitations",
        ):
            value = entry.get(key) or []
            record[key] = [str(item).strip() for item in value if str(item).strip()] if isinstance(value, list) else []
        support = entry.get("contribution_support") or []
        record["contribution_support"] = (
            [item for item in support if isinstance(item, int) and not isinstance(item, bool) and item >= 0]
            if isinstance(support, list)
            else []
        )
        premise = entry.get("contribution_premise")
        record["contribution_premise"] = (
            premise if isinstance(premise, int) and not isinstance(premise, bool) and premise >= 0 else None
        )
        statements = []
        for item in entry.get("factual_statements") or []:
            if isinstance(item, dict) and str(item.get("text") or "").strip():
                urls = item.get("source_urls") or []
                statements.append(
                    {
                        "text": str(item["text"]).strip(),
                        "source_urls": [str(url).strip() for url in urls if str(url).strip()] if isinstance(urls, list) else [],
                    }
                )
        record["factual_statements"] = statements
        overlap = entry.get("overlap") if isinstance(entry.get("overlap"), dict) else {}
        record["overlap"] = {
            "level": _enum(overlap.get("level"), OVERLAP) or "NONE",
            "prior": str(overlap.get("prior") or ""),
            "difference": str(overlap.get("difference") or ""),
        }
        topic = entry.get("topic") if isinstance(entry.get("topic"), dict) else {}
        record["topic"] = {key: re.sub(r"\s+", " ", str(topic.get(key) or "")).strip() for key in TREND_TOPIC_FIELDS}
        record["problems"] = problems
        parsed[cid] = record
    if valid_ids and not parsed:
        raise DiscoveryError("DISCOVERY_FAILURE", "assessment response covered none of the candidates")
    return parsed


def run_assessment(
    runtime,
    settings: DiscoverySettings,
    snapshot: dict,
    strategy_candidates: list[dict],
    priors: list[dict],
    claims: list[dict],
) -> tuple[dict[str, dict], str, str]:
    """Return assessments, how they were obtained, and the cache key."""
    valid = {c["id"] for c in snapshot.get("candidates", [])} | {c["id"] for c in strategy_candidates}
    if not valid:
        return {}, "skipped", ""
    key = assessment_key(snapshot, strategy_candidates, priors)
    cache = settings.opportunity_dir / f"{snapshot.get('snapshot_id') or 'no-research'}.assessment.json"
    if cache.is_file():
        try:
            stored = json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            stored = {}
        if stored.get("key") == key and isinstance(stored.get("raw"), dict):
            return parse_assessment(stored["raw"], valid), "cached", key
    prompt = assessment_prompt(snapshot, strategy_candidates, priors, claims, settings, settings.now().strftime("%Y-%m-%d"))
    try:
        result = runtime.ask(
            prompt,
            validate=cursor_runtime_mod.require_keys("opportunities"),
            timeout_sec=int(settings.get("assessment_timeout_sec", 600)),
        )
    except cursor_runtime_mod.CursorRuntimeError as exc:
        raise DiscoveryError("DISCOVERY_FAILURE", f"editorial assessment failed: {exc}") from exc
    parsed = parse_assessment(result.parsed, valid)
    engine.write_atomic(
        cache,
        json.dumps(
            {"key": key, "assessed_at": iso(settings.now()), "prompt_version": PROMPT_VERSION, "raw": result.parsed},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )
    return parsed, "model", key


# --------------------------------------------------------------------------- gates and choice


def _tier(order: tuple[str, ...], value: str) -> int:
    return order.index(value) if value in order else -1


def rank_key(entry: dict, recent_lanes: list[str]) -> tuple:
    dims = entry["dims"]
    parts: list = []
    for dim in RANK_DIMENSIONS:
        if dim == "development_kind":
            parts.append(_tier(RANK_ORDERS[dim], dims.get("rank_development_kind") or dims.get(dim, "")))
        else:
            parts.append(_tier(RANK_ORDERS[dim], dims[dim]))
    return tuple(parts) + (strategy_mod.lane_gap(str(entry["topic"].get("lane") or ""), recent_lanes),)


def _timeline_facts(entry: dict) -> str:
    timeline = entry.get("timeline") or {}
    parts = []
    if timeline.get("event_date"):
        parts.append(f"event {timeline['event_date']}")
    if timeline.get("latest_development"):
        parts.append(f"latest development {timeline['latest_development']}")
    if timeline.get("publication_date"):
        parts.append(f"article {timeline['publication_date']}")
    anchored = entry.get("dims", {}).get("anchored_support")
    if anchored is not None:
        parts.append(f"anchored_support {anchored}")
    return "; ".join(parts)


def explain_win(winner: dict, other: dict, recent_lanes: list[str]) -> str:
    for dim in RANK_DIMENSIONS:
        if dim == "development_kind":
            a_rank = winner["dims"].get("rank_development_kind") or winner["dims"].get("development_kind", "")
            b_rank = other["dims"].get("rank_development_kind") or other["dims"].get("development_kind", "")
            if a_rank == b_rank:
                continue
            a = winner["dims"].get("development_kind", "")
            b = other["dims"].get("development_kind", "")
        else:
            a, b = winner["dims"][dim], other["dims"][dim]
        if a != b:
            facts = f" ({_timeline_facts(winner)} vs {_timeline_facts(other)})"
            if dim == "prior_attempt":
                a_label, b_label = ("prior attempt" if a else "fresh"), ("prior attempt" if b else "fresh")
                return f"won over {other['id']} ({other['title']}) on {dim}: {a_label} vs {b_label}{facts}"
            return f"won over {other['id']} ({other['title']}) on {dim}: {a} vs {b}{facts}"
    a_lane = str(winner["topic"].get("lane") or "")
    b_lane = str(other["topic"].get("lane") or "")
    if strategy_mod.lane_gap(a_lane, recent_lanes) != strategy_mod.lane_gap(b_lane, recent_lanes):
        return (
            f"tied with {other['id']} ({other['title']}) on every editorial dimension; "
            f"lane balance broke the tie ({a_lane} is further below its soft mix than {b_lane})"
        )
    return f"tied with {other['id']} ({other['title']}) on every editorial dimension and lane balance; earlier candidate kept"


def _research_note_name(snapshot_id: str, cid: str) -> str:
    return f"{snapshot_id}-{cid.casefold()}.md"


def _build_trend_topic(candidate: dict, assessment: dict, snapshot_id: str, grounded: list[dict], dropped: list[str]) -> dict:
    topic = dict(assessment["topic"])
    topic["audience"] = "SPECIALIST" if assessment["specialist_only"] else assessment["audience"]
    topic["timeliness"] = "CURRENT"
    cited = []
    for source in candidate["sources"]:
        cited.append(f"{source['source_name']} ({source['published'][:10]}) {source['url']}")
    note = f"{RESEARCH_LABEL}/{_research_note_name(snapshot_id, candidate['id'])}"
    topic["why_now"] = re.sub(
        r"\s+",
        " ",
        f"{assessment['what_happened']} Sources: {'; '.join(cited)}. See {note}.",
    ).strip()
    topic["editorial_thesis"] = assessment["thesis"]
    topic["editorial_angle"] = assessment["editorial_contribution"] or assessment["beyond_summary"] or assessment["tension"]
    topic["source_conclusion"] = assessment["source_contribution"]
    topic["source_limitations"] = list(assessment["source_limitations"])
    topic["grounded_facts"] = [item["text"] for item in grounded]
    topic["do_not_claim"] = list(assessment["unsupported_implications"]) + list(assessment["contribution_overreach"]) + dropped
    topic["unresolved_questions"] = list(assessment["unresolved_questions"])
    topic["opportunity_id"] = candidate["id"]
    topic["research_note"] = note
    return topic


def evaluate(
    settings: DiscoverySettings,
    snapshot: dict,
    strategy_candidates: list[dict],
    assessments: dict[str, dict],
    *,
    priors: list[dict],
    used_titles: set[str],
    claims: list[dict],
) -> list[dict]:
    now = settings.now()
    stale_after = timedelta(days=int(settings.get("stale_after_days", 14)))
    discussion_conf = settings.get("discussion", {}) or {}
    major_points = int(discussion_conf.get("major_points", 300))
    snapshot_id = str(snapshot.get("snapshot_id") or "")
    results: list[dict] = []
    pool = [(c, "TREND") for c in snapshot.get("candidates", [])] + [(c, "STRATEGY") for c in strategy_candidates]
    for candidate, kind in pool:
        cid = candidate["id"]
        assessment = assessments.get(cid)
        title = candidate["title"] if kind == "TREND" else str(candidate["topic"].get("topic") or "")
        entry: dict = {
            "id": cid,
            "kind": kind,
            "title": title,
            "status": "REJECTED",
            "codes": [],
            "notes": [],
            "dims": {},
            "topic": {},
            "sources": [
                {"name": s["source_name"], "url": s["url"], "published": s["published"], "tier": s["tier"]}
                for s in candidate.get("sources", [])
            ],
            "discussion": candidate.get("discussion", []),
        }
        results.append(entry)
        if assessment is None:
            entry["codes"].append("NOT_ASSESSED")
            continue
        entry["assessment"] = {
            key: assessment[key]
            for key in (
                "decision", "category", "what_happened", "why_now", "who_is_affected", "practical_consequence",
                "decision_changed", "thesis", "tension", "beyond_summary", "interpretations",
                "unsupported_implications", "unresolved_questions", "overlap", "reasons",
                "source_contribution", "editorial_contribution", "contribution_type", "contribution_support",
                "contribution_premise", "contribution_overreach", "source_limitations", "reader_learns",
                "holds_without_headline", "why_publish_now",
            )
        }
        if assessment["problems"]:
            entry["codes"].append("MALFORMED_ASSESSMENT")
            entry["notes"].extend(assessment["problems"])
            continue
        significance = assessment["significance"]
        grounding = assessment["grounding"]
        audience = "SPECIALIST" if assessment["specialist_only"] else assessment["audience"]
        scope = str(assessment.get("contribution_scope") or "").strip().upper()
        if scope == "SPECIALIST_GUIDANCE" and audience == "BROAD_TECH":
            audience = "SPECIALIST" if assessment["specialist_only"] else "AI_ENGINEERING"
            entry["notes"].append("AUDIENCE_RELABELED: SPECIALIST_GUIDANCE is not BROAD_TECH")
        decision = assessment["decision"]
        angle = assessment["angle_strength"]
        grounded: list[dict] = []
        dropped: list[str] = []
        verified: set[int] = set()
        anchored_support = 0
        prior_attempt = False
        if assessment["contribution_type"] in NO_CONTRIBUTION_TYPES:
            entry["codes"].append("NO_CONTRIBUTION")
            entry["notes"].append(
                f"contribution is {assessment['contribution_type']}: the post would not add to what the source concludes"
            )
            angle = cap_angle(angle, "WEAK", entry, "")
        elif decision != "REJECT" and restates_source(
            assessment["editorial_contribution"], f"{title} {assessment['source_contribution']}"
        ):
            angle = cap_angle(
                angle, "ADEQUATE", entry, "CONTRIBUTION_CAPPED: the editorial contribution reuses the source conclusion"
            )
        if assessment["contribution_overreach"]:
            if decision == "PURSUE":
                decision = "QUALIFY"
                entry["notes"].append("FORCED_QUALIFY: the angle could be read to imply what the sources do not establish")
            claimed = thesis_tokens(assessment["editorial_contribution"], assessment["thesis"])
            for item in assessment["contribution_overreach"]:
                tokens = thesis_tokens(item)
                if len(tokens) >= 3 and len(tokens & claimed) / len(tokens) >= 0.6:
                    entry["codes"].append("UNSUPPORTED_CONTRIBUTION")
                    entry["notes"].append(f"contribution asserts what the sources do not establish: {item}")
                    break
        if decision != "REJECT" and assessment["reader_value"] == "LOW":
            entry["notes"].append("reader value LOW: the reader learns little beyond the headline")
        if not assessment["holds_without_headline"]:
            entry["codes"].append("HEADLINE_DEPENDENT")

        development_kind = "NEW_EVENT"
        rank_development_kind = "CURRENT_DEVELOPMENT"
        if kind == "TREND":
            latest = parse_iso(candidate["latest_published"])
            if latest is None:
                entry["codes"].append("STALE")
                entry["notes"].append("publication date missing")
            elif latest > now + timedelta(days=1):
                entry["codes"].append("STALE")
                entry["notes"].append("publication date is in the future")
            elif now - latest > stale_after:
                entry["codes"].append("STALE")
                entry["notes"].append("article publication is older than stale window")
            timeline = trend_development_timeline(candidate, now, stale_after)
            entry["timeline"] = {
                key: timeline[key]
                for key in ("publication_date", "event_date", "latest_development", "development_kind")
            }
            development_kind = timeline["development_kind"]
            rank_development_kind = timeline["rank_development_kind"]
            if timeline["stale_event"]:
                if "STALE" not in entry["codes"]:
                    entry["codes"].append("STALE")
                entry["notes"].append(
                    f"event or latest development dated {timeline['latest_development']} is outside the fresh window"
                )
            allowed = {s["url"]: s for s in candidate["sources"]}
            for index, statement in enumerate(assessment["factual_statements"]):
                cited = [allowed[url] for url in statement["source_urls"] if url in allowed]
                if any(statement_supported(statement["text"], source["excerpt"]) for source in cited):
                    grounded.append(statement)
                    verified.add(index)
                else:
                    dropped.append(statement["text"])
            anchored_support = len(verified & set(assessment["contribution_support"]))
            if grounded and not verified & set(assessment["contribution_support"]):
                angle = cap_angle(angle, "ADEQUATE", entry, "CONTRIBUTION_UNANCHORED: the contribution cites no verified statement")
            premise = assessment.get("contribution_premise")
            if (
                decision != "REJECT"
                and assessment["factual_statements"]
                and premise is not None
                and premise not in verified
            ):
                angle = cap_angle(angle, "ADEQUATE", entry, "PREMISE_UNVERIFIED: key factual premise not verified")
            cand_urls = {normalize_url(s["url"]) for s in candidate.get("sources", [])}
            for prior in priors:
                prior_urls = {normalize_url(u) for u in (prior.get("source_urls") or [])}
                if cand_urls & prior_urls:
                    prior_attempt = True
                    label = str(prior.get("post_id") or prior.get("topic", "")[:80])
                    entry["notes"].append(f"PRIOR_ATTEMPT: {label} ({prior.get('status', 'attempted')})")
                    break
            if dropped:
                entry["notes"].append(f"UNVERIFIED_STATEMENTS_DROPPED: {len(dropped)} statement(s) not stated by the cited excerpts")
            if not grounded:
                entry["codes"].append("UNGROUNDED")
                grounding = "INSUFFICIENT"
            elif len(grounded) == 1 and grounding == "SOLID":
                grounding = "PARTIAL"
                entry["notes"].append("GROUNDING_CAPPED: one verified statement supports at most PARTIAL")
            points = max((story["points"] for story in candidate.get("discussion", [])), default=0)
            if significance == "MAJOR" and candidate["independent_sources"] < 2 and points < major_points:
                significance = "MODERATE"
                entry["notes"].append(
                    "SIGNIFICANCE_CAPPED: MAJOR needs two independent source domains or substantial public discussion"
                )
            if assessment["unsupported_implications"]:
                if decision == "PURSUE":
                    decision = "QUALIFY"
                    entry["notes"].append("FORCED_QUALIFY: unsupported implications exist, the thesis is qualified")
                thesis = thesis_tokens(assessment["thesis"])
                for implication in assessment["unsupported_implications"]:
                    tokens = thesis_tokens(implication)
                    if len(tokens) >= 3 and len(tokens & thesis) / len(tokens) >= 0.6:
                        entry["codes"].append("UNSUPPORTED_THESIS")
                        entry["notes"].append(f"thesis restates an unsupported implication: {implication}")
                        break
            timeliness = "CURRENT"
            topic = _build_trend_topic(candidate, assessment, snapshot_id, grounded, dropped)
            topic["audience"] = audience
            errors = strategy_mod.validate_strategy_topic(topic, cid)
            if errors:
                entry["codes"].append("INVALID_TOPIC")
                entry["notes"].extend(errors)
        else:
            topic = dict(candidate["topic"])
            timeliness = "CURRENT" if topic.get("timeliness") in {"CURRENT", "TIME_SENSITIVE"} else "EVERGREEN"
            why_now = str(topic.get("why_now") or "")
            for index, statement in enumerate(assessment["factual_statements"]):
                if statement_supported(statement["text"], why_now):
                    grounded.append(statement)
                    verified.add(index)
                else:
                    dropped.append(statement["text"])
            anchored_support = len(verified & set(assessment["contribution_support"]))
            if grounded and not verified & set(assessment["contribution_support"]):
                angle = cap_angle(angle, "ADEQUATE", entry, "CONTRIBUTION_UNANCHORED: the contribution cites no verified statement")
            premise = assessment.get("contribution_premise")
            if (
                decision != "REJECT"
                and assessment["factual_statements"]
                and premise is not None
                and premise not in verified
            ):
                angle = cap_angle(angle, "ADEQUATE", entry, "PREMISE_UNVERIFIED: key factual premise not verified")
            if dropped:
                entry["notes"].append(
                    f"UNVERIFIED_STATEMENTS_DROPPED: {len(dropped)} statement(s) not stated in why_now sources"
                )
            if assessment["factual_statements"] and not grounded:
                entry["codes"].append("UNGROUNDED")
                grounding = "INSUFFICIENT"
            if timeliness == "CURRENT":
                when = development_date(why_now, now)
                if when is None:
                    entry["codes"].append("UNDATED")
                    entry["notes"].append("CURRENT strategy topic names no development date")
                elif now - when > stale_after:
                    entry["codes"].append("STALE")
                    entry["notes"].append(f"development dated {when.date().isoformat()} is older than {stale_after.days} days")
            elif grounding == "INSUFFICIENT":
                entry["codes"].append("UNGROUNDED")
            if audience != topic.get("audience"):
                entry["notes"].append(f"assessed audience {audience} differs from the strategy label {topic.get('audience')}")
            entry["timeline"] = {}
        entry["topic"] = topic
        entry["dims"] = {
            "prior_attempt": prior_attempt,
            "significance": significance,
            "audience": audience,
            "angle_strength": angle,
            "reader_value": assessment["reader_value"],
            "anchored_support": anchored_support,
            "development_kind": development_kind,
            "rank_development_kind": rank_development_kind,
            "grounding": grounding,
            "timeliness": timeliness,
            "decision": decision,
        }

        if decision == "REJECT":
            entry["codes"].append("MODEL_REJECTED")
        if assessment["requires_invented_experience"]:
            entry["codes"].append("INVENTS_EXPERIENCE")
        if not assessment["composition_feasible"]:
            entry["codes"].append("NOT_FEASIBLE")
        if significance == "MINOR" or angle == "WEAK" or assessment["reader_value"] == "LOW":
            entry["codes"].append("BELOW_BAR")
        overlap = assessment["overlap"]
        if overlap["level"] == "SAME_THESIS" or (overlap["level"] == "PARTIAL" and not overlap["difference"].strip()):
            entry["codes"].append("REPETITIVE")
            entry["notes"].append(f"assessor overlap {overlap['level']} with: {overlap['prior']}")
        text = " ".join([title, str(topic.get("topic") or ""), assessment["thesis"]])
        for prior in priors:
            if same_thesis(text, f"{prior.get('topic', '')} {prior.get('insight', '')}"):
                entry["codes"].append("REPETITIVE")
                entry["notes"].append(f"thesis overlaps prior {prior.get('status', 'attempted')} topic: {prior.get('topic', '')[:120]}")
                break
        if str(topic.get("topic") or "").casefold() in used_titles:
            entry["codes"].append("ALREADY_USED")
        if kind == "TREND" and "INVALID_TOPIC" not in entry["codes"]:
            conflict = strategy_mod.topic_audience_conflict(topic, claims) or strategy_mod.topic_substance_gap(topic, claims)
            if conflict:
                entry["codes"].append("AUDIENCE_CONFLICT")
                entry["notes"].append(conflict)
        entry["codes"] = list(dict.fromkeys(entry["codes"]))
        if not entry["codes"]:
            entry["status"] = "ELIGIBLE"
    return results


def choose(results: list[dict], recent_lanes: list[str]) -> tuple[dict | None, list[str]]:
    eligible = [entry for entry in results if entry["status"] == "ELIGIBLE"]
    if not eligible:
        return None, []
    ordered = sorted(
        enumerate(eligible),
        key=lambda pair: (rank_key(pair[1], recent_lanes), -pair[0]),
        reverse=True,
    )
    winner = ordered[0][1]
    winner["status"] = "SELECTED"
    return winner, [explain_win(winner, other, recent_lanes) for _index, other in ordered[1:]]


def render_opportunity_note(entry: dict, snapshot: dict) -> str:
    assessment = entry["assessment"]
    sources = entry["sources"]
    lines = [
        f"# Trend research — {entry['title']}",
        "",
        f"Research snapshot: {snapshot.get('snapshot_id')} retrieved {snapshot.get('retrieved_at')} (UTC). Opportunity {entry['id']}.",
        "Facts are the statements the cited excerpts support. Interpretation lines are editorial judgment, not product behavior.",
        "",
        f"## {entry['title']}",
        "",
        f"- **development:** {assessment['what_happened']}",
    ]
    for source in sources:
        lines.append(f"- **source:** {source['name']}")
        lines.append(f"- **url:** {source['url']}")
        lines.append(f"- **date:** {source['published'][:10]}")
    lines.append("- **what changed:** " + " ".join(entry["topic"].get("grounded_facts", [])))
    lines.append(f"- **source already concludes:** {assessment['source_contribution']}")
    lines.append(f"- **post adds ({assessment['contribution_type']}):** {assessment['editorial_contribution']}")
    lines.append("- **limitations to keep visible:** " + (" ".join(assessment["source_limitations"]) or "none recorded"))
    lines.append(f"- **reader learns:** {assessment['reader_learns']}")
    lines.append(f"- **why now:** {assessment['why_publish_now']}")
    lines.append(f"- **why it may matter:** {assessment['practical_consequence']} {assessment['decision_changed']}".rstrip())
    lines.append(f"- **possible personal angle:** {entry['topic'].get('why_this_author', '')}")
    lines.append("- **evidence needed from author:** None beyond the listed claims. Do not invent a personal result.")
    uncertainty = assessment["unresolved_questions"] + [f"Not established: {item}" for item in entry["topic"].get("do_not_claim", [])]
    lines.append("- **remaining uncertainty:** " + (" ".join(uncertainty) or "none recorded"))
    lines.append(f"- **interpretation:** {assessment['thesis']} {assessment['tension']}".rstrip())
    for story in entry.get("discussion", []):
        lines.append(
            f"- **discussion:** Hacker News {story['points']} points, {story['comments']} comments — {story['discussion_url']}"
        )
    lines += ["", "## Source excerpts", ""]
    for candidate in snapshot.get("candidates", []):
        if candidate["id"] != entry["id"]:
            continue
        for source in candidate["sources"]:
            lines.append(f"- {source['source_name']} ({source['published'][:10]}): {source['excerpt']}")
    return "\n".join(lines) + "\n"


def decide(
    settings: DiscoverySettings,
    runtime,
    *,
    strategy_topics: list[dict],
    strategy_deferred: list[str],
    priors: list[dict],
    used_titles: set[str],
    recent_lanes: list[str],
    claims: list[dict],
    legacy_choice: dict | None,
) -> dict:
    """Full editorial decision. Writes the snapshot, assessment cache, decision, and selected note."""
    snapshot, research_mode = ensure_research(settings)
    strategy_candidates = [
        {"id": f"S{index:02d}", "kind": "STRATEGY", "title": str(topic.get("topic") or ""), "topic": dict(topic)}
        for index, topic in enumerate(strategy_topics, start=1)
    ]
    assessments, assessment_mode, key = run_assessment(runtime, settings, snapshot, strategy_candidates, priors, claims)
    results = evaluate(
        settings,
        snapshot,
        strategy_candidates,
        assessments,
        priors=priors,
        used_titles=used_titles,
        claims=claims,
    )
    winner, comparisons = choose(results, recent_lanes)
    now = settings.now()
    snapshot_id = str(snapshot.get("snapshot_id") or "")
    decision = {
        "kind": "editorial_decision",
        "decided_at": iso(now),
        "outcome": "SELECTED" if winner else "NO_EDITORIAL_OPPORTUNITY",
        "research": {
            "snapshot_id": snapshot_id,
            "status": snapshot.get("status"),
            "mode": research_mode,
            "retrieved_at": snapshot.get("retrieved_at", ""),
            "failed_sources": [s["name"] for s in snapshot.get("sources", []) if s.get("status") != "ok"],
            "discussion_status": (snapshot.get("discussion") or {}).get("status", ""),
            "trend_candidates": len(snapshot.get("candidates", [])),
        },
        "assessment": {"mode": assessment_mode, "key": key, "prompt_version": PROMPT_VERSION},
        "selection_rule": (
            "Gates first (freshness, grounding, contribution beyond the source, originality, feasibility, audience fit). "
            "Survivors ordered by "
            + " > ".join(RANK_DIMENSIONS)
            + "; lane balance only breaks a full tie."
        ),
        "strategy_deferred": strategy_deferred,
        "legacy_lane_choice": (
            {"title": legacy_choice.get("topic", ""), "lane": legacy_choice.get("lane", "")} if legacy_choice else None
        ),
        "recent_lanes": recent_lanes,
        "candidates": results,
        "selected": None,
    }
    if winner:
        if winner["kind"] == "TREND":
            note = settings.research_dir / _research_note_name(snapshot_id, winner["id"])
            engine.write_atomic(note, render_opportunity_note(winner, snapshot))
        reasons = [winner["assessment"].get("reasons", "")] + comparisons
        decision["selected"] = {
            "id": winner["id"],
            "kind": winner["kind"],
            "title": winner["title"],
            "category": winner["assessment"].get("category", ""),
            "dims": winner["dims"],
            "why_selected": [reason for reason in reasons if reason],
            "topic": winner["topic"],
            "sources": winner["sources"],
        }
    else:
        decision["no_opportunity_reason"] = summarize_rejections(results, snapshot)
    stem = snapshot_id or f"{now.strftime('%Y-%m-%d')}-no-research"
    engine.write_atomic(
        settings.opportunity_dir / f"{stem}.decision.json",
        json.dumps(decision, ensure_ascii=False, indent=2) + "\n",
    )
    return decision


def summarize_rejections(results: list[dict], snapshot: dict) -> str:
    if not results:
        status = snapshot.get("status")
        return f"no candidates: research status {status}, no eligible strategy topic"
    counts: dict[str, int] = {}
    for entry in results:
        for code in entry["codes"]:
            counts[code] = counts.get(code, 0) + 1
    detail = ", ".join(f"{code} x{count}" for code, count in sorted(counts.items()))
    return f"{len(results)} candidate(s) assessed, none passed the gates ({detail}); research status {snapshot.get('status')}"


def decision_summary(decision: dict) -> dict:
    """Compact view for CLI output and the Inbox."""
    rows = []
    for entry in decision.get("candidates", []):
        rows.append(
            {
                "id": entry["id"],
                "kind": entry["kind"],
                "status": entry["status"],
                "title": entry["title"],
                "dims": entry.get("dims", {}),
                "codes": entry["codes"],
                "thesis": (entry.get("assessment") or {}).get("thesis", ""),
                "contribution_type": (entry.get("assessment") or {}).get("contribution_type", ""),
                "editorial_contribution": (entry.get("assessment") or {}).get("editorial_contribution", ""),
                "sources": [f"{s['name']} {s['published'][:10]} {s['url']}" for s in entry["sources"]],
            }
        )
    selected = decision.get("selected") or {}
    return {
        "outcome": decision.get("outcome"),
        "research": decision.get("research"),
        "assessment": decision.get("assessment"),
        "selected": {key: selected.get(key) for key in ("id", "kind", "title", "category", "dims", "why_selected")}
        if selected
        else None,
        "legacy_lane_choice": decision.get("legacy_lane_choice"),
        "no_opportunity_reason": decision.get("no_opportunity_reason", ""),
        "candidates": rows,
    }
