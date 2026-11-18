"""Deterministic editorial quality review for post bodies. Stdlib only."""

from __future__ import annotations

import re
from dataclasses import dataclass

EDITORIAL_VERDICTS = frozenset({"PASS", "REVISE", "EVIDENCE_TOO_THIN"})

STANDALONE_VALUES = frozenset({"HIGH", "MEDIUM", "LOW"})
AUDIENCE_BREADTH = frozenset({"BROAD_TECH", "AI_ENGINEERING", "SPECIALIST"})
PROJECT_CONTEXT = frozenset({"NONE", "LIGHT", "HIGH"})

ABSTRACT_NOUNS = (
    "orchestration",
    "scalability",
    "innovation",
    "context",
    "agents",
    "architecture",
    "productivity",
)

CONCRETE_VERBS = re.compile(
    r"\b(?:kept|rejected|removed|chose|chosen|measured|observed|compared|tested|tried|added|dropped|saw|found|built|shipped|evaluated|decided)\b",
    re.IGNORECASE,
)

CONTRAST_MARKERS = re.compile(
    r"\b(?:but|however|instead|rather than|did not|does not|no improvement|exposed|unlike|whereas|yet)\b",
    re.IGNORECASE,
)

EXPLICIT_TENSION_RE = re.compile(
    r"\b(?:contrast that matters|is not the same(?:\s+as)?)\b",
    re.IGNORECASE,
)

# "An agent can flag a coordinate. That is not a discovery." Only counts after an
# affirmative sentence; a demonstrative negation on its own is not an opposition.
REFRAME_DENIAL_RE = re.compile(
    r"^(?:That|This)\s+(?:is|was)\s+not\s+(?:a|an|the)\s+\w|^(?:That|This)\s+(?:isn['’]t|wasn['’]t)\s+(?:a|an|the)\s+\w",
    re.IGNORECASE,
)

# "treat it as a hypothesis, not a discovery"; "is a bounded index, not a truth store".
COPULAR_NOT_RE = re.compile(
    r"\b(?:is|are|was|were|as)\s+(?:a|an|the)\s+[\w-]+(?:\s+[\w-]+)?,\s+not\s+(?:a|an|the)\s+"
    r"(?!(?:single|lot|bit|few|little|moment|chance)\b)[\w-]+",
    re.IGNORECASE,
)

NEGATION_RE = re.compile(r"\b(?:not|no|never|nothing|none|cannot)\b|n['’]t\b", re.IGNORECASE)

CAUSAL_IDENTITY_RE = re.compile(
    r"\bis a (?:model|dependency) problem\b",
    re.IGNORECASE,
)

FIRST_PERSON = re.compile(r"\b(?:I|my|we|our)\b", re.IGNORECASE)

LESSON_FRAMES = re.compile(
    r"\b(?:lesson|takeaway|trade-off|tradeoff|decision|learned|conclusion|matters because|worth measuring|compare)\b",
    re.IGNORECASE,
)

HOOK_FAILURES = (
    (re.compile(r"you won'?t believe", re.IGNORECASE), "engagement bait"),
    (re.compile(r"thoughts\?", re.IGNORECASE), "engagement bait"),
    (re.compile(r"agree\?", re.IGNORECASE), "engagement bait"),
    (re.compile(r"follow me for more", re.IGNORECASE), "influencer filler"),
    (re.compile(r"\bclaim id\b", re.IGNORECASE), "evidence bookkeeping"),
    (re.compile(r"proof[- ]registry", re.IGNORECASE), "evidence bookkeeping"),
)

THIN_EVIDENCE_MARKERS = re.compile(
    r"\b(?:cannot say|cannot invent|no eligible|insufficient|too thin|not recorded|do not generalize|evidence gap|without inventing)\b",
    re.IGNORECASE,
)

NUMBER_RE = re.compile(r"\b\d+(?:\.\d+)?\b")

BARE_METRIC_LINE = re.compile(
    r"^\s*Hit Rate\s+[\d.]+\s*,\s*MRR\s+[\d.]+\.?\s*$",
    re.IGNORECASE | re.MULTILINE,
)

EVIDENCE_INFLATION = re.compile(
    r"\b(?:every|all)\s+(?:rag|retrieval|agent)\s+systems?\b|\b(?:always|universally)\s+(?:proves?|shows?|means?)\b",
    re.IGNORECASE,
)

PROJECT_SHOWCASE_MARKERS = re.compile(
    r"\b(?:I built|built to solve|the problem was|to solve)\b",
    re.IGNORECASE,
)

READER_FIRST_FRAMING = re.compile(
    r"\b(?:can hit|can hide|ranking quality|different questions|without knowing|"
    r"alone does not|does not tell|matters because|why .+ care)\b",
    re.IGNORECASE,
)

SPECIALIST_METRIC_DEPTH = re.compile(
    r"\b(?:hit rate|mrr|reciprocal rank|rrf|mean reciprocal)\b",
    re.IGNORECASE,
)

# Personal-project anchors. Career experience without one of these does not
# require the reader to know a project. Canonical value: project context NONE.
PROJECT_CONTEXT_ANCHOR = re.compile(
    r"\b(?:while building|this project|the project|my project|nine-question|leadfinder|legal rag)\b",
    re.IGNORECASE,
)


@dataclass
class EditorialFindings:
    verdict: str
    strongest: str
    weakest: str
    reader_value: str
    too_generic: str
    missing_concrete: str
    structural_change: str
    notes: list[str]
    standalone_value: str = "MEDIUM"
    audience_breadth: str = "AI_ENGINEERING"
    project_context_required: str = "LIGHT"


def opening_text(body: str, max_chars: int = 320) -> str:
    stripped = body.strip()
    if not stripped:
        return ""
    first_block = stripped.split("\n\n")[0]
    return first_block[:max_chars]


def is_bare_metric_opening(body: str) -> bool:
    """True when the first line leads with a specialist metric and no reader frame.

    Length is not an exception. A longer sentence that still opens on Hit Rate
    or MRR is the same failure as a short score line.
    """
    stripped = body.strip()
    if not stripped:
        return False
    first_line = stripped.split("\n")[0].strip()
    if BARE_METRIC_LINE.match(first_line):
        return True
    if not (SPECIALIST_METRIC_DEPTH.search(first_line) and NUMBER_RE.search(first_line)):
        return False
    if READER_FIRST_FRAMING.search(first_line) or LESSON_FRAMES.search(first_line):
        return False
    return True


def assess_standalone(body: str) -> tuple[str, str, str]:
    """Return standalone_value, project_context_required, audience_breadth."""
    if is_bare_metric_opening(body):
        return "LOW", "HIGH", "SPECIALIST"
    opening = opening_text(body).casefold()
    if READER_FIRST_FRAMING.search(opening) and not is_bare_metric_opening(body):
        if PROJECT_SHOWCASE_MARKERS.search(body[:600]):
            return "MEDIUM", "LIGHT", "BROAD_TECH"
        specialist = bool(
            SPECIALIST_METRIC_DEPTH.search(body) and not PROJECT_SHOWCASE_MARKERS.search(body[:500])
        )
        if specialist:
            depth = "SPECIALIST"
            context = "LIGHT"
        elif PROJECT_CONTEXT_ANCHOR.search(body):
            depth = "AI_ENGINEERING"
            context = "LIGHT"
        else:
            # Accessible engineering principle. No specialist metric, no project.
            depth = "BROAD_TECH"
            context = "NONE"
        if LESSON_FRAMES.search(body[:500]) or CONTRAST_MARKERS.search(body[:500]):
            return "HIGH", context, depth
        return "MEDIUM", context, depth
    if PROJECT_SHOWCASE_MARKERS.search(body[:400]):
        return "MEDIUM", "LIGHT", "BROAD_TECH"
    if SPECIALIST_METRIC_DEPTH.search(body[:200]) and NUMBER_RE.search(body[:200]):
        return "LOW", "HIGH", "SPECIALIST"
    return "MEDIUM", "LIGHT", "AI_ENGINEERING"


def specialist_deep_dive_framed(body: str) -> bool:
    if is_bare_metric_opening(body):
        return False
    opening = opening_text(body, 500)
    return bool(READER_FIRST_FRAMING.search(opening) or LESSON_FRAMES.search(opening))


def has_concrete_signal(body: str) -> bool:
    if NUMBER_RE.search(body):
        return True
    return CONCRETE_VERBS.search(body) is not None


def marked_tension(body: str) -> bool:
    return CONTRAST_MARKERS.search(body) is not None or EXPLICIT_TENSION_RE.search(body) is not None


def tension_present(body: str) -> bool:
    """True when the body states opposition, not only contrast-marker words."""
    if marked_tension(body) or COPULAR_NOT_RE.search(body):
        return True
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", body.strip()) if part.strip()]
    for previous, sentence in zip(sentences, sentences[1:]):
        if REFRAME_DENIAL_RE.search(sentence) and not NEGATION_RE.search(previous):
            return True
    return False


def causal_identity_sentence(body: str) -> str:
    """Return the first sentence that assigns a failure to model or dependency by identity."""
    for part in re.split(r"(?<=[.!?])\s+", body.strip()):
        line = part.strip()
        if line and CAUSAL_IDENTITY_RE.search(line):
            return line
    return ""


def contrast_structural_change(body: str) -> str:
    opening = opening_text(body, 220).replace("\n", " ").strip()
    return (
        "State the opposition the reader needs before the lesson. "
        f"Current opening: {opening}"
    )


def abstract_without_concrete(body: str) -> bool:
    lower = body.casefold()
    for noun in ABSTRACT_NOUNS:
        if noun in lower and not has_concrete_signal(body):
            return True
    return False


def count_abstract_nouns(body: str) -> int:
    lower = body.casefold()
    return sum(1 for noun in ABSTRACT_NOUNS if noun in lower)


_CLOSE_STOP = frozenset(
    {
        "about",
        "after",
        "again",
        "being",
        "could",
        "does",
        "from",
        "have",
        "into",
        "that",
        "their",
        "there",
        "these",
        "this",
        "those",
        "under",
        "when",
        "where",
        "which",
        "while",
        "with",
        "would",
    }
)


def _content_tokens(text: str) -> set[str]:
    tokens: set[str] = set()
    for word in re.findall(r"[a-z0-9]+", text.casefold()):
        if len(word) >= 5 and word not in _CLOSE_STOP:
            tokens.add(word)
    return tokens


def conclusion_repeats_earlier(body: str) -> bool:
    """True when the last paragraph restates an earlier one.

    A short lesson line is not this failure. The close has to reuse most of
    a substantial paragraph and add almost no new wording.
    """
    paragraphs = [part.strip() for part in body.split("\n\n") if part.strip()]
    if len(paragraphs) < 3:
        return False
    last = _content_tokens(paragraphs[-1])
    if len(last) < 6:
        return False
    earlier: set[str] = set()
    for paragraph in paragraphs[:-1]:
        earlier |= _content_tokens(paragraph)
    overlap = last & earlier
    if len(overlap) / len(last) < 0.7:
        return False
    return len(last - earlier) < 3


def evidence_too_thin(evidence_gaps: str, body: str) -> bool:
    if not evidence_gaps.strip():
        return False
    if not THIN_EVIDENCE_MARKERS.search(evidence_gaps):
        return False
    return not has_concrete_signal(body)


def readability_issues(body: str) -> list[str]:
    issues: list[str] = []
    paragraphs = [p for p in body.split("\n\n") if p.strip()]
    for paragraph in paragraphs:
        if len(paragraph) > 900:
            issues.append("overly dense paragraph")
    if len(body) > 40 and body.count("\n\n") == 0 and len(body) > 500:
        issues.append("insufficient whitespace")
    return issues


def hook_issues(body: str) -> list[str]:
    issues: list[str] = []
    for pattern, label in HOOK_FAILURES:
        if pattern.search(body):
            issues.append(label)
    return issues


def apply_standalone_gate(findings: EditorialFindings, body: str) -> EditorialFindings:
    standalone, context_req, audience = assess_standalone(body)
    findings.standalone_value = standalone
    findings.project_context_required = context_req
    findings.audience_breadth = audience

    if EVIDENCE_INFLATION.search(body):
        findings.verdict = "REVISE"
        findings.weakest = "Evidence inflation: scoped result reads as universal law."
        findings.structural_change = "Separate general technical explanation from measured project outcomes."
        findings.notes = list(findings.notes) + ["Do not convert one evaluation into a universal empirical claim."]
        return findings

    if findings.verdict == "EVIDENCE_TOO_THIN":
        return findings

    if standalone == "LOW" and findings.verdict == "PASS" and specialist_deep_dive_framed(body):
        findings.standalone_value = "MEDIUM"
        findings.project_context_required = "LIGHT"
        return findings

    if standalone == "LOW":
        if findings.verdict == "PASS":
            findings.verdict = "REVISE"
        findings.weakest = (
            "Standalone value is LOW: the opening leads with a project metric before the reader knows why it matters."
        )
        findings.reader_value = "A reader may not know why the metric matters before the project is introduced."
        findings.structural_change = (
            "Open with why this matters to the intended audience, then introduce the scoped evidence. "
            "Do not lead with Hit Rate, MRR, or the project result."
        )
        findings.notes = list(findings.notes) + ["project_context_required: HIGH without adequate framing"]
        return findings

    if context_req == "HIGH" and not PROJECT_SHOWCASE_MARKERS.search(body[:500]) and not READER_FIRST_FRAMING.search(opening_text(body)):
        if findings.verdict == "PASS":
            findings.verdict = "REVISE"
            findings.weakest = "High project-context dependence without problem framing."
            findings.structural_change = "Establish problem, purpose, and why the detail matters in the opening section."

    return findings


def editorial_review(body: str, evidence_gaps: str = "") -> EditorialFindings:
    """Evaluate editorial quality. Does not change semantic PASS meaning."""
    notes: list[str] = []
    concrete = has_concrete_signal(body)
    contrast = tension_present(body)
    author = FIRST_PERSON.search(body) is not None and concrete
    reader = LESSON_FRAMES.search(body) is not None
    abstract_count = count_abstract_nouns(body)
    generic = abstract_count >= 2 and not concrete

    if evidence_too_thin(evidence_gaps, body):
        findings = EditorialFindings(
            verdict="EVIDENCE_TOO_THIN",
            strongest="The draft stays inside known evidence limits.",
            weakest="The source material does not support a stronger public post.",
            reader_value="A reader would not learn a specific engineering outcome from this evidence alone.",
            too_generic="Topic-level wording without a retained observation or decision.",
            missing_concrete="A measured result, decision, or observed behavior tied to this project.",
            structural_change="Do not invent specificity. Gather or record stronger evidence before drafting again.",
            notes=["Do not add unsupported numbers, incidents, or results."],
        )
        return apply_standalone_gate(findings, body)

    hooks = hook_issues(body)
    read = readability_issues(body)
    notes.extend(hooks)
    notes.extend(read)

    thesis_ok = len(body.strip()) >= 80 and (concrete or contrast)
    substance_ok = concrete and not generic
    tension_ok = contrast
    novelty_ok = author or (concrete and not generic)
    # A structural reframe satisfies contrast only; it is not evidence of reader value.
    value_ok = reader or (concrete and marked_tension(body))
    hook_ok = not hooks
    read_ok = not read

    scores = {
        "thesis": thesis_ok,
        "concrete": substance_ok,
        "contrast": tension_ok,
        "author-specific": novelty_ok,
        "reader value": value_ok,
        "hook": hook_ok,
        "readability": read_ok,
    }

    if tension_ok:
        causal = causal_identity_sentence(body)
        if causal:
            findings = EditorialFindings(
                verdict="REVISE",
                strongest="The post separates signals the reader can follow.",
                weakest=f"Causal overclaim: {causal}",
                reader_value="A reader may treat the identity as measured ops doctrine rather than design guidance.",
                too_generic="",
                missing_concrete="",
                structural_change=(
                    "Keep the boundary between signals. Do not assign the failure to the model or the dependency "
                    "unless the post states scoped evidence for that identity."
                ),
                notes=notes,
            )
            return apply_standalone_gate(findings, body)

    if all(scores.values()) and conclusion_repeats_earlier(body):
        findings = EditorialFindings(
            verdict="REVISE",
            strongest="The opening states a point a reader can follow.",
            weakest="The closing paragraph repeats an earlier paragraph.",
            reader_value="A reader reaches the end and gets the same sentence again.",
            too_generic="The close restates the opening instead of advancing it.",
            missing_concrete="",
            structural_change=(
                "End on a consequence the earlier paragraphs did not already state. "
                "Do not add an unrelated personal anecdote."
            ),
            notes=notes,
        )
        return apply_standalone_gate(findings, body)

    if all(scores.values()):
        findings = EditorialFindings(
            verdict="PASS",
            strongest="Concrete observation with a clear engineering takeaway.",
            weakest="None blocking publication.",
            reader_value="A technical reader can reuse the comparison or decision frame.",
            too_generic="",
            missing_concrete="",
            structural_change="Optional polish only.",
            notes=notes,
        )
        return apply_standalone_gate(findings, body)

    weakest_key = next((k for k, ok in scores.items() if not ok), "composition")
    if not substance_ok:
        structural_change = "Name one observed behavior, metric, or decision in the opening."
    elif not tension_ok:
        structural_change = contrast_structural_change(body)
    else:
        structural_change = "Tighten the hook and remove generic framing."
    findings = EditorialFindings(
        verdict="REVISE",
        strongest="Grounded wording that can stay public-safe." if concrete else "Clear topic focus.",
        weakest=f"Weakest dimension: {weakest_key}.",
        reader_value="Reader value is thin until a specific outcome or decision is foregrounded."
        if not value_ok
        else "Reader value is present but buried.",
        too_generic="Abstract nouns without a concrete event." if generic else "",
        missing_concrete="Name one observed behavior, metric, or decision in the opening."
        if not substance_ok
        else "",
        structural_change=structural_change,
        notes=notes,
    )
    return apply_standalone_gate(findings, body)


def render_editorial_section(findings: EditorialFindings) -> str:
    lines = [
        "## Editorial review",
        "",
        f"Verdict: {findings.verdict}",
        "",
        f"standalone_value: {findings.standalone_value}",
        f"audience_breadth: {findings.audience_breadth}",
        f"project_context_required: {findings.project_context_required}",
        "",
        f"Strongest element:",
        findings.strongest,
        "",
        f"Weakest element:",
        findings.weakest,
        "",
        f"Why a reader should care:",
        findings.reader_value,
        "",
        f"Too generic:",
        findings.too_generic or "None flagged.",
        "",
        f"Missing concrete detail:",
        findings.missing_concrete or "None flagged.",
        "",
        f"Suggested structural change:",
        findings.structural_change,
        "",
        "Editorial questions:",
        "1. Does the reader know why this matters within the opening section?",
        "2. Does the post assume familiarity with a personal project?",
        "3. Is the technical depth appropriate for the intended audience?",
        "4. Could a more general framing preserve the evidence while increasing relevance?",
        "5. Is the project used as evidence, or are readers asked to care about project internals by default?",
    ]
    if findings.notes:
        lines.extend(["", "Notes:"])
        for note in findings.notes:
            lines.append(f"- {note}")
    lines.append("")
    return "\n".join(lines)


def remove_editorial_section(rest: str) -> str:
    if "## Editorial review" not in rest:
        return rest
    return re.sub(r"\n## Editorial review\n.*?(?=^## |\Z)", "\n", rest, count=1, flags=re.MULTILINE | re.DOTALL)


def upsert_editorial_section(rest: str, section: str) -> str:
    rest = remove_editorial_section(rest)
    if not section.strip():
        return rest
    return rest.rstrip() + "\n\n" + section
