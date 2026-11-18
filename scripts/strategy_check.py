"""Validation helpers for strategy artifacts. Stdlib only."""

from __future__ import annotations

import re

CONTENT_LANES = frozenset(
    {
        "POSITIONING",
        "TREND_REACTION",
        "PROJECT_SHOWCASE",
        "TECHNICAL_DEEP_DIVE",
        "CAREER_ENGINEERING",
    }
)
STRATEGY_AUDIENCE = frozenset({"BROAD_TECH", "AI_ENGINEERING", "SPECIALIST"})
TIMELINESS = frozenset({"EVERGREEN", "CURRENT", "TIME_SENSITIVE"})
STANDALONE_VALUE = frozenset({"HIGH", "MEDIUM", "LOW"})
EVIDENCE_STRENGTH = frozenset({"HIGH", "MEDIUM", "LOW", "NONE"})
PROJECT_CONTEXT = frozenset({"NONE", "LIGHT", "HIGH"})
TECHNICAL_DEPTH = frozenset({"ACCESSIBLE", "INTERMEDIATE", "DEEP"})
VISUAL_POTENTIAL = frozenset({"ADDITIVE", "DECORATIVE", "NONE"})

STRATEGY_TOPIC_FIELDS = (
    "topic",
    "lane",
    "audience",
    "timeliness",
    "standalone_value",
    "personal_evidence_strength",
    "project_context_required",
    "technical_depth",
    "visual_potential",
    "why_now",
    "why_this_author",
    "why_reader_cares",
)

IDEA_STRATEGY_FIELDS = (
    "lane",
    "audience",
    "standalone_value",
    "project_context_required",
    "technical_depth",
    "why_now",
    "why_this_author",
)

HUMAN_EDITORIAL_DECISIONS = frozenset({"APPROVE", "REVISE", "DROP"})


def compute_concentration_risk(topics: list[dict]) -> bool:
    if len(topics) < 3:
        return False
    specialist_count = sum(1 for t in topics if t.get("audience") == "SPECIALIST")
    if len(topics) >= 5 and specialist_count / len(topics) > 0.35:
        return True
    consecutive = 0
    for topic in topics:
        if topic.get("audience") == "SPECIALIST":
            consecutive += 1
            if consecutive >= 3:
                return True
        else:
            consecutive = 0
    return False


def validate_strategy_topic(record: dict, label: str) -> list[str]:
    errors: list[str] = []
    for key in STRATEGY_TOPIC_FIELDS:
        if not str(record.get(key, "")).strip():
            errors.append(f"{label}: missing topic field {key}")
    lane = record.get("lane", "")
    if lane and lane not in CONTENT_LANES:
        errors.append(f"{label}: lane must be one of {sorted(CONTENT_LANES)}")
    audience = record.get("audience", "")
    if audience and audience not in STRATEGY_AUDIENCE:
        errors.append(f"{label}: audience must be one of {sorted(STRATEGY_AUDIENCE)}")
    for field, allowed in (
        ("timeliness", TIMELINESS),
        ("standalone_value", STANDALONE_VALUE),
        ("personal_evidence_strength", EVIDENCE_STRENGTH),
        ("project_context_required", PROJECT_CONTEXT),
        ("technical_depth", TECHNICAL_DEPTH),
        ("visual_potential", VISUAL_POTENTIAL),
    ):
        value = record.get(field, "")
        if value and value not in allowed:
            errors.append(f"{label}: {field} has invalid value {value!r}")
    return errors


# Tokens that show up in almost every engineering topic and must not
# decide audience fit or distinctiveness by themselves.
GENERIC_TOKENS = frozenset(
    {
        "about",
        "after",
        "along",
        "alongside",
        "applied",
        "author",
        "based",
        "been",
        "before",
        "could",
        "defined",
        "does",
        "engineering",
        "from",
        "general",
        "have",
        "into",
        "named",
        "need",
        "needs",
        "only",
        "over",
        "personal",
        "project",
        "quality",
        "rather",
        "reader",
        "readers",
        "result",
        "results",
        "should",
        "software",
        "still",
        "system",
        "systems",
        "team",
        "teams",
        "technical",
        "than",
        "that",
        "their",
        "there",
        "this",
        "used",
        "using",
        "verified",
        "when",
        "where",
        "which",
        "while",
        "with",
        "without",
        "work",
        "would",
    }
)
SHORT_KEEP = frozenset({"rag", "mrr", "rrf", "mcp"})
TOPIC_TEXT_FIELDS = ("topic", "why_this_author", "why_now")
ARGUMENT_FIELDS = ("topic", "why_now", "why_reader_cares")
SIMILARITY_TOKEN_FLOOR = 3


def topic_tokens(record: dict, fields: tuple[str, ...] = ("topic", "why_this_author")) -> set[str]:
    text = " ".join(str(record.get(field, "")) for field in fields)
    tokens: set[str] = set()
    for word in re.findall(r"[a-z0-9]+", text.casefold()):
        if word in GENERIC_TOKENS:
            continue
        if word in SHORT_KEEP or len(word) >= 5:
            tokens.add(word)
    return tokens


def _eligible_claim(claim: dict) -> bool:
    if claim.get("public_safe") is not True:
        return False
    return claim.get("verification") in {"verified", "derived"}


def _statement_tokens(claim: dict) -> set[str]:
    return topic_tokens({"topic": str(claim.get("statement") or "")})


def claim_develops_topic(topic: dict, claim: dict) -> bool:
    """True when this claim develops the thesis, not merely the author's resume.

    Argument fields are the lesson a reader is offered. ``why_this_author`` may
    name the project or metric evidence for that lesson. One shared area word
    does not attach a different project. Career and profile labels develop a
    topic only when the argument itself uses their wording.
    """
    if not _eligible_claim(claim):
        return False
    statement = _statement_tokens(claim)
    if len(statement & topic_tokens(topic, ARGUMENT_FIELDS)) >= 2:
        return True
    category = str(claim.get("category") or "")
    named_evidence = category in {"project", "metric"} or (category == "opinion" and claim.get("project"))
    if not named_evidence:
        return False
    floor = 2 if category == "metric" else 3
    return len(statement & topic_tokens(topic, ("why_this_author",))) >= floor


def topic_substance_gap(topic: dict, claims: list[dict]) -> str:
    """A career topic with no developing practice claim is not ready to compose.

    Sourced trend analysis and a positioning argument may proceed on general
    technical knowledge. Profile labels are not a substitute for the missing
    practice, and an unrelated project anecdote is not one either.
    """
    if str(topic.get("lane") or "") != "CAREER_ENGINEERING":
        return ""
    if any(
        claim_develops_topic(topic, claim) and str(claim.get("category") or "") in {"project", "metric"}
        for claim in claims
    ):
        return ""
    return (
        "CAREER_ENGINEERING has no concrete practice claim that develops the thesis. "
        "Profile labels and positioning opinions are not that practice. "
        "Defer the topic instead of attaching an unrelated project anecdote."
    )


def supporting_claims(topic: dict, claims: list[dict]) -> list[dict]:
    """Claims whose statement shares distinctive wording with the topic record.

    Personal evidence can support a topic. A match does not make that claim the topic.
    """
    blob = topic_tokens(topic, TOPIC_TEXT_FIELDS)
    matched: list[dict] = []
    for claim in claims:
        if not _eligible_claim(claim):
            continue
        overlap = topic_tokens({"topic": str(claim.get("statement") or "")}) & blob
        if len(overlap) >= 2:
            matched.append(claim)
    return matched


def metric_evidence(topic: dict, claims: list[dict]) -> bool:
    return any(str(claim.get("category") or "") == "metric" for claim in supporting_claims(topic, claims))


def topic_audience_conflict(topic: dict, claims: list[dict]) -> str:
    """Return why this labeled topic cannot be composed for that audience.

    An empty string means the label is selectable. Retrieval metrics stay valid
    for AI_ENGINEERING and SPECIALIST. They are not rejected by the word RAG.
    """
    lane = str(topic.get("lane") or "")
    audience = str(topic.get("audience") or "")
    depth = str(topic.get("technical_depth") or "")
    context = str(topic.get("project_context_required") or "")
    standalone = str(topic.get("standalone_value") or "")
    if audience == "BROAD_TECH" and depth == "DEEP":
        return (
            "BROAD_TECH cannot carry DEEP technical depth. "
            "Relabel the audience as AI_ENGINEERING or SPECIALIST."
        )
    if lane == "TECHNICAL_DEEP_DIVE" and audience == "BROAD_TECH":
        return (
            "TECHNICAL_DEEP_DIVE is not a BROAD_TECH topic. "
            "Use audience AI_ENGINEERING or SPECIALIST."
        )
    if lane == "POSITIONING" and context == "HIGH":
        return (
            "POSITIONING cannot require HIGH project context. "
            "Use PROJECT_SHOWCASE or TECHNICAL_DEEP_DIVE."
        )
    if lane == "POSITIONING" and audience == "BROAD_TECH" and metric_evidence(topic, claims):
        return (
            "POSITIONING for BROAD_TECH is not a project-metric topic. "
            "The stated author evidence matches a retrieval-metric claim. "
            "Defer it, or relabel it as TECHNICAL_DEEP_DIVE for AI_ENGINEERING or SPECIALIST. "
            "Do not rewrite the metric lesson as general positioning."
        )
    if standalone == "LOW" and not (
        lane == "TECHNICAL_DEEP_DIVE" and audience in {"AI_ENGINEERING", "SPECIALIST"}
    ):
        return "standalone_value LOW is selectable only as a framed specialist deep dive."
    return ""


def topics_too_similar(candidate: dict, prior: dict, claims: list[dict]) -> bool:
    """True when two topics would repeat the same recent metric result or decision."""
    candidate_metrics = {
        str(claim.get("id"))
        for claim in supporting_claims(candidate, claims)
        if str(claim.get("category") or "") == "metric"
    }
    prior_metrics = {
        str(claim.get("id"))
        for claim in supporting_claims(prior, claims)
        if str(claim.get("category") or "") == "metric"
    }
    if candidate_metrics and prior_metrics and (candidate_metrics & prior_metrics):
        return True
    overlap = topic_tokens(candidate) & topic_tokens(prior)
    return len(overlap) >= SIMILARITY_TOKEN_FLOOR


def trend_source_problem(topic: dict, trend_file_exists) -> str:
    """CURRENT trend topics need a real source. trend_file_exists takes a repo-relative path."""
    if str(topic.get("lane") or "") != "TREND_REACTION":
        return ""
    why = str(topic.get("why_now") or "")
    match = re.search(r"research/trends/[A-Za-z0-9._/-]+\.md", why)
    if match:
        if not trend_file_exists(match.group(0)):
            return f"trend source file is missing: {match.group(0)}"
        return ""
    if "http://" in why or "https://" in why:
        return ""
    if str(topic.get("timeliness") or "") in {"CURRENT", "TIME_SENSITIVE"}:
        return "TREND_REACTION has no sourced development in why_now. Do not invent one from memory."
    return ""


SOFT_LANE_MIX = {
    "POSITIONING": 0.35,
    "TREND_REACTION": 0.25,
    "PROJECT_SHOWCASE": 0.20,
    "TECHNICAL_DEEP_DIVE": 0.15,
    "CAREER_ENGINEERING": 0.05,
}


def lane_gap(lane: str, recent_lanes: list[str]) -> float:
    """Positive when a lane is under its soft mix. Not a quota."""
    target = SOFT_LANE_MIX.get(lane, 0.0)
    if not recent_lanes:
        return target
    actual = recent_lanes.count(lane) / len(recent_lanes)
    return target - actual


def choose_topic(eligible: list[dict], recent_lanes: list[str]) -> dict:
    """Prefer the underrepresented lane, then the earlier strategy record."""
    return max(
        enumerate(eligible),
        key=lambda pair: (lane_gap(str(pair[1].get("lane") or ""), recent_lanes), -pair[0]),
    )[1]
