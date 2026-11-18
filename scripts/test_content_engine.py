"""Tests for the P2 content engine and the adapted style scripts."""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import content_engine as engine  # noqa: E402
import cycle as cycle_mod  # noqa: E402
import editorial as editorial_mod  # noqa: E402
import preview as preview_mod  # noqa: E402


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class EligibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.claims = {claim["id"]: claim for claim in engine.load_claims()}

    def test_fact_opinion_and_blocked(self):
        self.assertEqual(engine.classify(self.claims["LEGAL_RAG_007"]), "fact")
        self.assertEqual(engine.classify(self.claims["POSITIONING_001"]), "opinion")
        self.assertEqual(engine.classify(self.claims["CAREER_AI_011"]), "blocked")
        self.assertEqual(engine.classify(self.claims["IGREADER_004"]), "blocked")

    def test_metric_qualification_prohibitions(self):
        phrases = engine.prohibited_phrases(self.claims["LEGAL_RAG_007"]["qualification"])
        self.assertIn("perfect retrieval", phrases)
        self.assertIn("100% accuracy", phrases)
        self.assertIn("production-grade accuracy", phrases)


class HashAndIdTests(unittest.TestCase):
    def test_hash_ignores_outer_blank_lines_and_crlf(self):
        one = engine.body_hash("In a nine-question evaluation, Hit Rate was 1.0.\n")
        two = engine.body_hash("\r\nIn a nine-question evaluation, Hit Rate was 1.0.  \r\n\r\n")
        self.assertEqual(one, two)

    def test_next_id_reads_existing_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "ideas"
            folder.mkdir()
            (folder / "IDEA-20260923-01.md").write_text("x", encoding="utf-8")
            (folder / "IDEA-20260923-03.md").write_text("x", encoding="utf-8")
            self.assertEqual(engine.next_id(root, "idea", "20260923"), "IDEA-20260923-04")
            self.assertEqual(engine.next_id(root, "post", "20260923"), "POST-20260923-01")

    def test_next_id_skips_approved_posts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            approved = root / "approved"
            approved.mkdir()
            (approved / "POST-20260923-01.md").write_text("x", encoding="utf-8")
            (root / "published").mkdir()
            (root / "published" / "POST-20260923-02.md").write_text("x", encoding="utf-8")
            self.assertEqual(engine.next_id(root, "post", "20260923"), "POST-20260923-03")


def claim_lines(items: list[tuple[dict, str]]) -> str:
    blocks = []
    for claim, use in items:
        lines = [f"- id: {claim['id']}", f"  use: {use}"]
        if claim.get("qualification"):
            lines.append(f'  scope: "{claim["qualification"]}"')
        blocks.append("\n".join(lines))
    return "\n".join(blocks)


def post_text(
    claim: dict,
    body: str,
    status: str = "drafted",
    review: str = "",
    use: str = "fact",
    claims: str = "",
) -> str:
    scope = ""
    if claim.get("qualification"):
        scope = f'\n  scope: "{claim["qualification"]}"'
    digest = engine.body_hash(body)
    review_hash = digest if review else ""
    claim_block = claims or f"- id: {claim['id']}\n  use: {use}{scope}"
    tail = ""
    if status in {"styled", "reviewed"}:
        tail += "\n## Style report\n\nStyle signal only. Not an approval gate.\n"
    if status == "reviewed":
        tail += f"\n## Review\n\n{review}\n"
    return f"""---
id: POST-20260923-01
kind: post
status: {status}
created: 2026-09-23
hook: receipt
body_sha256: {digest}
review_verdict: {review}
review_body_sha256: {review_hash}
---

## Hooks considered

receipt fits the measured hit rate.

## Body

{body}

## Claims

{claim_block}

## Evidence gaps

- none
{tail}"""


class ArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.claims = engine.load_claims()
        cls.by_id = {claim["id"]: claim for claim in cls.claims}
        cls.hooks = engine.load_hooks()

    def check(self, text: str, filename: str = "POST-20260923-01.md") -> list[str]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "drafts" / filename
            path.parent.mkdir()
            path.write_text(text, encoding="utf-8")
            return engine.check_artifact(path, self.claims, self.hooks, root)

    def test_scoped_metric_passes(self):
        claim = self.by_id["LEGAL_RAG_007"]
        body = "In a nine-question retrieval evaluation, the system achieved a Hit Rate of 1.0."
        self.assertEqual(self.check(post_text(claim, body)), [])

    def test_widened_metric_fails(self):
        claim = self.by_id["LEGAL_RAG_007"]
        body = "The RAG system achieved perfect retrieval."
        errors = self.check(post_text(claim, body))
        self.assertTrue(any("perfect retrieval" in error for error in errors))
        self.assertTrue(any("1.0" in error for error in errors))

    def test_derived_cannot_be_fact(self):
        claim = self.by_id["POSITIONING_002"]
        text = post_text(claim, "I treat production engineering discipline as the stance.")
        text = text.replace("use: fact", "use: fact")
        errors = self.check(text)
        self.assertTrue(any("cannot be stated as fact" in error for error in errors))

    def test_blocked_claim_rejected(self):
        claim = self.by_id["CAREER_AI_011"]
        body = "The evaluation scope stays explicit."
        text = post_text(claim, body).replace("use: fact", "use: opinion")
        errors = self.check(text)
        self.assertTrue(any("cannot be used as an opinion" in error for error in errors))

    def test_stale_review_fails(self):
        claim = self.by_id["LEGAL_RAG_007"]
        body = "In a nine-question retrieval evaluation, the system achieved a Hit Rate of 1.0."
        text = post_text(claim, body, status="reviewed", review="PASS")
        text = text.replace(body, body + " Extra sentence.")
        errors = self.check(text)
        self.assertTrue(any("body_sha256" in error for error in errors))
        self.assertTrue(any("previous PASS is stale" in error for error in errors))

    def test_stamp_invalidates_a_reviewed_body(self):
        claim = self.by_id["LEGAL_RAG_007"]
        body = "In a nine-question retrieval evaluation, the system achieved a Hit Rate of 1.0."
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "POST-20260923-01.md"
            path.write_text(post_text(claim, body, status="reviewed", review="PASS"), encoding="utf-8")
            fields, rest = engine.parse_frontmatter(path.read_text(encoding="utf-8"))
            sections = engine.section_map(rest)
            sections_body = sections["Body"] + "\nStill the same 1.0 result."
            # Rewrite the body section in place via a simple replace of the original sentence.
            path.write_text(path.read_text(encoding="utf-8").replace(body, body + " Still scoped to that set."), encoding="utf-8")
            self.assertEqual(engine.stamp(path, invalidate=False, status=None), 1)
            self.assertEqual(engine.stamp(path, invalidate=True, status="styled"), 0)
            fields, rest = engine.parse_frontmatter(path.read_text(encoding="utf-8"))
            self.assertEqual(fields["status"], "styled")
            self.assertEqual(fields["review_verdict"], "")
            self.assertEqual(fields["body_sha256"], engine.body_hash(engine.section_map(rest)["Body"]))
            self.assertIn("1.0", sections_body)


SCOPED_HIT_RATE = "In a nine-question retrieval evaluation, the system achieved a Hit Rate of 1.0."
STRONG_P5_BODY = (
    "In a nine-question retrieval evaluation at k=5, I measured Hit Rate 1.0 and MRR 0.75. "
    "Hit Rate looked perfect, but MRR 0.75 exposed that the first relevant document was not always rank 1. "
    "I compare both metrics on that defined set rather than calling it general accuracy. "
    "Lesson: do not trust a headline Hit Rate without checking rank-1 behavior."
)


class SemanticReviewTests(unittest.TestCase):
    """Acceptance cases A-H. Fixtures are temporary and are not written under content/."""

    @classmethod
    def setUpClass(cls):
        cls.claims = engine.load_claims()
        cls.by_id = {claim["id"]: claim for claim in cls.claims}
        cls.hooks = engine.load_hooks()

    def check(self, text: str, filename: str = "POST-20260923-01.md") -> list[str]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "drafts" / filename
            path.parent.mkdir()
            path.write_text(text, encoding="utf-8")
            return engine.check_artifact(path, self.claims, self.hooks, root)

    def review(self, body: str, cited: list[tuple[str, str]]) -> tuple[str, list[str]]:
        items = [{"id": claim_id, "use": use} for claim_id, use in cited]
        return engine.review_body(body, items, self.claims)

    def test_a_nine_question_hit_rate_is_eligible(self):
        claim = self.by_id["LEGAL_RAG_007"]
        self.assertEqual(claim["verification"], "verified")
        self.assertIs(claim["public_safe"], True)
        self.assertEqual(engine.classify(claim), "fact")
        verdict, reasons = self.review(SCOPED_HIT_RATE, [("LEGAL_RAG_007", "fact")])
        self.assertEqual(verdict, "PASS", reasons)
        self.assertEqual(self.check(post_text(claim, SCOPED_HIT_RATE)), [])

    def test_b_hundred_percent_accuracy_is_blocked(self):
        claim = self.by_id["LEGAL_RAG_007"]
        body = "My RAG system achieved 100% accuracy."
        verdict, reasons = self.review(body, [("LEGAL_RAG_007", "fact")])
        self.assertEqual(verdict, "BLOCKED", reasons)
        self.assertTrue(any("inflated metric" in reason for reason in reasons))
        blocked = post_text(claim, body, status="reviewed", review="BLOCKED")
        self.assertEqual(self.check(blocked), [])
        as_pass = post_text(claim, body, status="reviewed", review="PASS")
        errors = self.check(as_pass)
        self.assertTrue(any("review_verdict must be BLOCKED" in error for error in errors))

    def test_c_years_working_in_ai_is_blocked(self):
        body = "After more than ten years working in AI, I scope every evaluation."
        verdict, reasons = self.review(body, [("CAREER_BACKEND_001", "fact")])
        self.assertEqual(verdict, "BLOCKED", reasons)
        self.assertTrue(any("years of AI experience" in reason for reason in reasons))
        self.assertEqual(engine.classify(self.by_id["CAREER_AI_011"]), "blocked")

    def test_d_framed_context_conclusion_is_eligible(self):
        body = (
            "After experimenting with coding agents on legacy codebases, "
            "my conclusion is that more context is not automatically better."
        )
        self.assertEqual(engine.classify(self.by_id["AI_DEV_003"]), "fact")
        self.assertEqual(engine.classify(self.by_id["AI_DEV_011"]), "fact")
        self.assertEqual(engine.classify(self.by_id["AI_DEV_013"]), "opinion")
        cited = [
            (self.by_id["AI_DEV_003"], "fact"),
            (self.by_id["AI_DEV_011"], "fact"),
            (self.by_id["AI_DEV_013"], "opinion"),
        ]
        verdict, reasons = self.review(body, [(claim["id"], use) for claim, use in cited])
        self.assertEqual(verdict, "PASS", reasons)
        text = post_text(cited[0][0], body, claims=claim_lines(cited))
        self.assertEqual(self.check(text), [])
        misread, _ = self.review(body, [("AI_DEV_013", "fact")])
        self.assertEqual(misread, "BLOCKED")

    def test_framed_derived_opinion_passes(self):
        body = (
            "A visible demo alone does not tell you the surrounding workflow got better. "
            "My conclusion: more agent scaffolding does not automatically improve developer experience."
        )
        verdict, reasons = self.review(body, [("AI_DEV_017", "opinion")])
        self.assertEqual(verdict, "PASS", reasons)

    def test_opinion_stated_as_measured_fact_is_blocked(self):
        body = "Benchmarks prove that more agent scaffolding is always better than the lighter setup."
        verdict, reasons = self.review(body, [("AI_DEV_017", "opinion")])
        self.assertEqual(verdict, "BLOCKED", reasons)
        self.assertTrue(any("universal factual premise" in reason for reason in reasons))

    def test_unsupported_personal_outcome_is_blocked(self):
        sentence = "I proved that more agent scaffolding improved the workflow result."
        verdict, reasons = self.review(sentence, [("AI_DEV_017", "opinion")])
        self.assertEqual(verdict, "BLOCKED", reasons)
        self.assertTrue(any(sentence in reason for reason in reasons))

    def test_e_benchmarks_prove_always_better_is_blocked(self):
        body = "Benchmarks prove that semantic navigation is always better than repository exploration."
        verdict, reasons = self.review(body, [("AI_DEV_015", "opinion")])
        self.assertNotEqual(verdict, "PASS")
        self.assertEqual(verdict, "BLOCKED", reasons)
        self.assertTrue(any("universal factual premise" in reason for reason in reasons))

    def test_f_leadfinder_paying_customers_is_blocked(self):
        body = "After validating LeadFinder with paying customers..."
        verdict, reasons = self.review(body, [("LEADFINDER_009", "fact")])
        self.assertEqual(verdict, "BLOCKED", reasons)
        self.assertNotEqual(verdict, "NEEDS_EDIT")
        self.assertTrue(any("commercial success" in reason for reason in reasons))
        self.assertEqual(engine.classify(self.by_id["LEADFINDER_011"]), "blocked")

    def test_g_thoughts_on_an_evidenced_draft_needs_edit(self):
        claim = self.by_id["LEGAL_RAG_007"]
        body = SCOPED_HIT_RATE + "\n\nThoughts?"
        verdict, reasons = self.review(body, [("LEGAL_RAG_007", "fact")])
        self.assertEqual(verdict, "NEEDS_EDIT", reasons)
        self.assertTrue(any("Thoughts?" in reason for reason in reasons))
        self.assertNotEqual(verdict, "BLOCKED")
        needs_edit = post_text(claim, body, status="reviewed", review="NEEDS_EDIT")
        self.assertEqual(self.check(needs_edit), [])
        as_pass = post_text(claim, body, status="reviewed", review="PASS")
        errors = self.check(as_pass)
        self.assertTrue(any("review_verdict must be NEEDS_EDIT" in error for error in errors))

    def test_h_pass_becomes_stale_when_the_body_changes(self):
        claim = self.by_id["LEGAL_RAG_007"]
        original = post_text(claim, SCOPED_HIT_RATE, status="reviewed", review="PASS")
        self.assertEqual(self.check(original), [])
        changed_body = SCOPED_HIT_RATE + " The same evaluation still had nine questions."
        changed = original.replace(SCOPED_HIT_RATE, changed_body)
        errors = self.check(changed)
        self.assertTrue(any("previous PASS is stale" in error for error in errors))
        self.assertTrue(any("body_sha256" in error for error in errors))
        fields, rest = engine.parse_frontmatter(changed)
        self.assertEqual(fields["review_verdict"], "PASS")
        current = engine.body_hash(engine.section_map(rest)["Body"])
        self.assertNotEqual(fields["review_body_sha256"], current)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "POST-20260923-01.md"
            path.write_text(changed, encoding="utf-8")
            self.assertEqual(engine.stamp(path, invalidate=False, status=None), 1)
            self.assertEqual(engine.stamp(path, invalidate=True, status="styled"), 0)
            fields, _ = engine.parse_frontmatter(path.read_text(encoding="utf-8"))
            self.assertEqual(fields["status"], "styled")
            self.assertEqual(fields["review_verdict"], "")
            self.assertEqual(fields["review_body_sha256"], "")

    def test_legacy_review_aliases_are_rejected(self):
        claim = self.by_id["LEGAL_RAG_007"]
        text = post_text(claim, SCOPED_HIT_RATE, status="reviewed", review="pass")
        errors = self.check(text)
        self.assertTrue(any("PASS, NEEDS_EDIT, or BLOCKED" in error for error in errors))

    def test_style_score_cannot_override_a_blocked_body(self):
        import inspect

        parameters = list(inspect.signature(engine.review_body).parameters)
        self.assertEqual(parameters, ["body", "cited", "claims", "source_bundle"])
        verdict, _ = self.review("My RAG system achieved 100% accuracy.", [("LEGAL_RAG_007", "fact")])
        self.assertEqual(verdict, "BLOCKED")
        self.assertNotIn(verdict, {"HIGH", "REVIEW", "FLAGGED"})


class StyleScriptTests(unittest.TestCase):
    def test_humanize_keeps_dashes_and_drops_invisible_characters(self):
        humanize = load_module(ROOT / ".cursor" / "skills" / "li-human" / "humanize.py", "li_humanize")
        lex = humanize.load_lexicon(ROOT / ".cursor" / "skills" / "li-human" / "slop.json")
        raw = "The cutoff \u2014 k=5 \u2014 stays.\u200b Hit Rate 1.0.\nSo, still, the scope holds.\n"
        clean, report = humanize.humanize(raw, lex)
        self.assertNotIn("\u200b", clean)
        self.assertIn("\u2014", clean)
        self.assertIn("1.0", clean)
        self.assertIn("So, still, the scope holds.", clean)
        self.assertTrue(report["invisible"])

    def test_detect_exits_zero_when_the_score_is_low(self):
        path = ROOT / ".cursor" / "skills" / "li-human" / "detect.py"
        with tempfile.TemporaryDirectory() as tmp:
            draft = Path(tmp) / "draft.txt"
            draft.write_text("This is one sentence. This is one sentence. This is one sentence. This is one sentence.\n", encoding="utf-8")
            panel = subprocess.run([sys.executable, str(path), str(draft)], capture_output=True, text=True)
            completed = subprocess.run(
                [sys.executable, str(path), str(draft), "--json"],
                capture_output=True,
                text=True,
            )
        self.assertEqual(panel.returncode, 0)
        self.assertIn("Style signal only", panel.stdout)
        self.assertEqual(completed.returncode, 0)
        payload = json.loads(completed.stdout)
        self.assertIn(payload["verdict"], {"HIGH", "REVIEW", "FLAGGED"})
        self.assertNotIn(payload["verdict"], engine.REVIEW_VERDICTS)


# Phrases li-post must keep out of ## Body. The bare word "evidence" is intentionally absent.
INTERNAL_LANGUAGE = (
    "proof registry",
    "eligible claim",
    "claim id",
    "evidence map",
    "public_safe",
    "verification status",
    "the evidence records",
    "no eligible claim",
    "no score is recorded",
    "does not record a score",
    "not as a benchmark score",
    "no benchmark score",
    "not a measured result",
    "this claim is derived",
    "this is allowed because",
    "blocked claim",
    "general_technical_knowledge",
)

# IDEA-20260923-04. The leaking body is the public-prose failure from the first draft of that idea.
IDEA_20260923_04 = (
    ("AI_DEV_008", "fact"),
    ("AI_DEV_009", "fact"),
    ("AI_DEV_011", "fact"),
    ("AI_DEV_012", "fact"),
    ("AI_DEV_013", "opinion"),
    ("AI_DEV_014", "opinion"),
)

LEAKING_CONTEXT_BODY = """My conclusion is that more context is not automatically better for a coding agent, and that excessive context can reduce usefulness.

I treat this as an engineering judgment from the work, not as a benchmark score.

What I actually did was practical experimentation: context engineering, agent benchmarking, and evaluating AI coding tools on real legacy software repositories. It is research and experimentation on real enterprise or legacy codebases.

Benchmarking was part of that experimentation. It does not record a score."""

COMPOSED_CONTEXT_BODY = """After experimenting with context engineering and AI coding tools on real legacy repositories, my conclusion is that more context is not automatically better.

Excessive context can reduce usefulness. The same experimentation included agent benchmarking. The work is research and experimentation on real enterprise or legacy codebases."""


def internal_language_phrases(skill_text: str) -> list[str]:
    marker = "### Internal language"
    start = skill_text.find(marker)
    if start < 0:
        raise AssertionError("li-post skill is missing the Internal language section")
    phrases = []
    for line in skill_text[start + len(marker):].splitlines():
        if line.startswith("#"):
            break
        if line.startswith("- "):
            phrases.append(line[2:].strip().strip("`"))
    return phrases


def internal_language_hits(body: str, phrases: list[str]) -> list[str]:
    folded = body.casefold()
    return [phrase for phrase in phrases if phrase.casefold() in folded]


class PostCompositionTests(unittest.TestCase):
    """P2.5 drafting contract for IDEA-20260923-04.

    The phrase screen flags obvious internal language. It does not score writing
    quality, and it does not change PASS, NEEDS_EDIT, or BLOCKED.
    """

    @classmethod
    def setUpClass(cls):
        cls.claims = engine.load_claims()
        cls.by_id = {claim["id"]: claim for claim in cls.claims}
        cls.hooks = engine.load_hooks()
        cls.skill = (ROOT / ".cursor" / "skills" / "li-post" / "SKILL.md").read_text(encoding="utf-8")
        cls.phrases = internal_language_phrases(cls.skill)

    def review(self, body: str) -> tuple[str, list[str]]:
        cited = [{"id": claim_id, "use": use} for claim_id, use in IDEA_20260923_04]
        return engine.review_body(body, cited, self.claims)

    def check(self, body: str) -> list[str]:
        items = [(self.by_id[claim_id], use) for claim_id, use in IDEA_20260923_04]
        text = post_text(items[0][0], body, claims=claim_lines(items))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "drafts" / "POST-20260923-01.md"
            path.parent.mkdir()
            path.write_text(text, encoding="utf-8")
            return engine.check_artifact(path, self.claims, self.hooks, root)

    def test_skill_states_the_composition_contract(self):
        self.assertNotIn("still carries that claim's qualification", self.skill)
        self.assertEqual(self.phrases, list(INTERNAL_LANGUAGE))
        self.assertNotIn("evidence", self.phrases)
        for needle in (
            "materially misleading",
            "claim inventory",
            "derived lesson",
            "You may not invent",
            "After experimenting with context engineering and coding agents on legacy repositories, my conclusion is that more context is not automatically better.",
            "I started by loading entire repositories into the context window, but performance dropped.",
            "nine-question",
            'The bare word "evidence" is ordinary English.',
        ):
            self.assertIn(needle, self.skill)
        contract = (ROOT / "docs" / "content-contract.md").read_text(encoding="utf-8")
        self.assertIn("materially misleading", contract)
        self.assertIn("A withheld score is honored by not stating a score.", contract)

    def test_idea_20260923_04_leakage_stays_out_of_review(self):
        hits = internal_language_hits(LEAKING_CONTEXT_BODY, self.phrases)
        self.assertIn("not as a benchmark score", hits)
        self.assertIn("does not record a score", hits)
        self.assertEqual(internal_language_hits(COMPOSED_CONTEXT_BODY, self.phrases), [])
        ordinary = COMPOSED_CONTEXT_BODY + "\n\nThe evidence from those experiments is the work on legacy repositories."
        self.assertEqual(internal_language_hits(ordinary, self.phrases), [])
        self.assertEqual(internal_language_hits(SCOPED_HIT_RATE, self.phrases), [])

        leaking_verdict, leaking_reasons = self.review(LEAKING_CONTEXT_BODY)
        composed_verdict, composed_reasons = self.review(COMPOSED_CONTEXT_BODY)
        self.assertEqual(leaking_verdict, "PASS", leaking_reasons)
        self.assertEqual(composed_verdict, "PASS", composed_reasons)
        self.assertEqual(self.check(LEAKING_CONTEXT_BODY), [])
        self.assertEqual(self.check(COMPOSED_CONTEXT_BODY), [])

        idea_path = ROOT / "content" / "ideas" / "IDEA-20260923-04.md"
        if idea_path.exists():
            idea = idea_path.read_text(encoding="utf-8")
            cited = re.findall(r"(?m)^- id: (\S+)", idea.split("## Claims", 1)[1].split("## Evidence gaps", 1)[0])
            self.assertEqual(cited, [claim_id for claim_id, _use in IDEA_20260923_04])


MRR_DEFINITION = "Mean Reciprocal Rank uses the reciprocal rank of the first relevant result."
RRF_IMPROVED_MRR = "RRF improved the Legal RAG Engine's MRR."
RRF_CAUSED_MRR = "RRF caused the MRR 0.75 result."

GENERAL_DEFINITIONS = (
    "Hit Rate@5 asks whether a relevant item appears within the top five results.",
    MRR_DEFINITION,
    "RRF combines ranked result lists using reciprocal rank-based scores.",
    "Kafka preserves record ordering within a partition.",
)

PROJECT_OUTCOMES = (
    RRF_IMPROVED_MRR,
    "Increasing Kafka partitions fixed our throughput problem.",
    "Extra context reduced agent accuracy by 20%.",
    "We chose sparse retrieval because dense retrieval failed.",
    "Our MRR improved because relevant documents moved closer to rank 1.",
)


def explanation_post(body: str, claims: str = "- none", evidence: str = "- none") -> str:
    digest = engine.body_hash(body)
    return f"""---
id: POST-20260923-01
kind: post
status: drafted
created: 2026-09-23
hook: receipt
body_sha256: {digest}
review_verdict:
review_body_sha256:
---

## Hooks considered

distinction fits a definition.

## Body

{body}

## Claims

{claims}

## Evidence gaps

{evidence}
"""


class GeneralTechnicalKnowledgeTests(unittest.TestCase):
    """P2.6a. General explanations are not personal claims. Project outcomes stay evidenced."""

    @classmethod
    def setUpClass(cls):
        cls.claims = engine.load_claims()
        cls.by_id = {claim["id"]: claim for claim in cls.claims}
        cls.hooks = engine.load_hooks()
        cls.review_skill = (ROOT / ".cursor" / "skills" / "li-review" / "SKILL.md").read_text(encoding="utf-8")
        cls.post_skill = (ROOT / ".cursor" / "skills" / "li-post" / "SKILL.md").read_text(encoding="utf-8")
        cls.contract = (ROOT / "docs" / "content-contract.md").read_text(encoding="utf-8")

    def review(self, body: str, cited: list[tuple[str, str]] | None = None, claims: list[dict] | None = None):
        items = [{"id": claim_id, "use": use} for claim_id, use in (cited or [])]
        return engine.review_body(body, items, claims if claims is not None else self.claims)

    def check(self, text: str) -> list[str]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "drafts" / "POST-20260923-01.md"
            path.parent.mkdir()
            path.write_text(text, encoding="utf-8")
            return engine.check_artifact(path, self.claims, self.hooks, root)

    def test_contract_distinguishes_the_three_classes(self):
        for needle in (
            "Project or personal fact",
            "Derived personal conclusion",
            "General technical explanation",
            "general_technical_knowledge",
            "general capability != observed project outcome",
            MRR_DEFINITION,
            RRF_IMPROVED_MRR,
            "RRF caused the MRR 0.75 result",
        ):
            self.assertIn(needle, self.contract)
        self.assertIn("general technical explanation", self.review_skill)
        self.assertIn("does not support", self.review_skill)
        self.assertIn(MRR_DEFINITION, self.post_skill)
        self.assertIn("general_technical_knowledge", self.post_skill)

    def test_general_definition_passes_without_a_personal_claim_id(self):
        self.assertEqual(engine.classify_statement(MRR_DEFINITION, self.claims), "general_technical")
        for sentence in GENERAL_DEFINITIONS:
            self.assertEqual(engine.classify_statement(sentence, self.claims), "general_technical", sentence)
        verdict, reasons = self.review(MRR_DEFINITION, [])
        self.assertEqual(verdict, "PASS", reasons)
        self.assertEqual(self.check(explanation_post(MRR_DEFINITION)), [])
        marked = explanation_post(
            MRR_DEFINITION,
            evidence=f'- general_technical_knowledge: "{MRR_DEFINITION}"',
        )
        self.assertEqual(self.check(marked), [])

    def test_engineering_analysis_passes_without_a_personal_claim(self):
        analysis = (
            "MCP specification 2026-07-28 moves servers to a stateless request and response core.\n\n"
            "The protocol retires session-centric initialize flows. Identity rides with each request instead of a pinned session.\n\n"
            "A load balancer therefore treats every call as standalone."
        )
        verdict, reasons = self.review(analysis, [])
        self.assertEqual(verdict, "PASS", reasons)
        copied = "Applied AI work includes MCP and tool integrations."
        blocked, blocked_reasons = self.review(copied, [])
        self.assertEqual(blocked, "BLOCKED", blocked_reasons)
        self.assertTrue(any("no eligible claim is cited" in reason for reason in blocked_reasons))

    def test_project_outcome_requires_its_own_evidence(self):
        for sentence in PROJECT_OUTCOMES:
            self.assertEqual(engine.classify_statement(sentence, self.claims), "project_fact", sentence)
            self.assertTrue(engine.is_project_outcome(sentence), sentence)
        verdict, reasons = self.review(RRF_IMPROVED_MRR, [])
        self.assertEqual(verdict, "BLOCKED", reasons)
        cited = [("LEGAL_RAG_005", "fact"), ("LEGAL_RAG_008", "fact")]
        verdict, reasons = self.review(RRF_IMPROVED_MRR, cited)
        self.assertEqual(verdict, "BLOCKED", reasons)
        self.assertTrue(any("project outcome has no eligible proof" in reason for reason in reasons))
        self.assertTrue(any(RRF_IMPROVED_MRR in reason for reason in reasons))
        items = [(self.by_id["LEGAL_RAG_005"], "fact"), (self.by_id["LEGAL_RAG_008"], "fact")]
        errors = self.check(post_text(items[0][0], RRF_IMPROVED_MRR, claims=claim_lines(items)))
        self.assertTrue(any("project outcome has no eligible proof" in error for error in errors))
        synthetic = {
            "id": "LEGAL_RAG_OUTCOME",
            "statement": RRF_IMPROVED_MRR,
            "verification": "verified",
            "public_safe": True,
            "category": "result",
        }
        passed, pass_reasons = self.review(
            RRF_IMPROVED_MRR,
            [("LEGAL_RAG_OUTCOME", "fact")],
            self.claims + [synthetic],
        )
        self.assertEqual(passed, "PASS", pass_reasons)
        laundered = explanation_post(
            RRF_IMPROVED_MRR,
            evidence=f'- general_technical_knowledge: "{RRF_IMPROVED_MRR}"',
        )
        marker_errors = self.check(laundered)
        self.assertTrue(any("general_technical_knowledge cannot cover" in error for error in marker_errors))

    def test_use_plus_metric_does_not_infer_a_cause(self):
        used = self.by_id["LEGAL_RAG_005"]
        reported = self.by_id["LEGAL_RAG_008"]
        self.assertIn("Reciprocal Rank Fusion", used["statement"])
        self.assertIn("0.75", reported["statement"])
        self.assertFalse(engine.project_outcome_supported(RRF_CAUSED_MRR, [used]))
        self.assertFalse(engine.project_outcome_supported(RRF_CAUSED_MRR, [reported]))
        self.assertFalse(engine.project_outcome_supported(RRF_CAUSED_MRR, [used, reported]))
        verdict, reasons = self.review(RRF_CAUSED_MRR, [("LEGAL_RAG_005", "fact"), ("LEGAL_RAG_008", "fact")])
        self.assertEqual(verdict, "BLOCKED", reasons)
        self.assertNotEqual(verdict, "PASS")
        self.assertTrue(any("project outcome has no eligible proof" in reason for reason in reasons))


AGENT_MERGE_BODY = (
    "I treat an unattended process that commits to clear a failing workflow as a write. "
    "Owners of merge policy need to decide who may change `.github/workflows/` and test expectations, "
    "and whether an approval given before those commits still counts.\n\n"
    "On 28 Oct 2026, GitHub integrated Copilot Agent Merge into pull requests. "
    "It can fix failed GitHub Actions workflows and merge conflicts in a cloud Actions environment, "
    "can be enabled by default for the user's own pull requests, and can combine with auto-merge.\n\n"
    "Docs retrieved 2026-11-12 state Agent Merge does not replace review, and auto-merge still waits "
    "for required reviews and status checks."
)

RECONCILIATION_BUNDLE = (ROOT / "research/trends/2026-11-12-reconciliation.md").read_text(encoding="utf-8")


class SourceGroundedPostTests(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
      cls.claims = engine.load_claims()
      cls.hooks = engine.load_hooks()

  def check(self, text: str) -> list[str]:
      with tempfile.TemporaryDirectory() as tmp:
          root = Path(tmp)
          path = root / "drafts" / "POST-20260923-01.md"
          path.parent.mkdir(parents=True)
          path.write_text(text, encoding="utf-8")
          return engine.check_artifact(path, self.claims, self.hooks, root)

  def test_none_token_is_distinct_from_blank_claims(self):
      self.assertEqual(engine.claims_section_status("- none")[0], "none")
      self.assertEqual(engine.claims_section_status("")[0], "empty")
      self.assertEqual(engine.claim_records("- none"), [])

  def test_blank_claims_section_fails_with_actionable_error(self):
      errors = engine.check_claims("POST-x.md", "", {}, True, "text", idea_artifact=False)
      self.assertTrue(any("use - none" in error for error in errors))

  def test_malformed_claims_section_fails(self):
      errors = engine.check_claims("POST-x.md", "- not a valid record", {}, True, "text")
      self.assertTrue(any("could not be read" in error for error in errors))

  def test_idea_with_none_claims_passes_check(self):
      text = """---
id: IDEA-20261112-99
kind: idea
status: proposed
created: 2026-11-12
---
## Insight
x
## Why it fits
x
## Claims

- none

## Evidence gaps

- none

## Local overlap

- none
"""
      with tempfile.TemporaryDirectory() as tmp:
          root = Path(tmp)
          path = root / "ideas" / "IDEA-20261112-99.md"
          path.parent.mkdir(parents=True)
          path.write_text(text, encoding="utf-8")
          self.assertEqual(engine.check_artifact(path, self.claims, self.hooks, root), [])

  def test_sourced_external_analysis_with_none_claims_passes(self):
      verdict, reasons = engine.review_body(
          AGENT_MERGE_BODY,
          [],
          self.claims,
          source_bundle=RECONCILIATION_BUNDLE,
      )
      self.assertEqual(verdict, "PASS", reasons)
      with tempfile.TemporaryDirectory() as tmp:
          root = Path(tmp)
          idea_path = root / "ideas" / "IDEA-20261112-02.md"
          idea_path.parent.mkdir(parents=True)
          idea_path.write_text((ROOT / "content/ideas/IDEA-20261112-02.md").read_text(encoding="utf-8"))
          post_path = root / "drafts" / "POST-20261112-99.md"
          post_path.parent.mkdir(parents=True)
          post_text = explanation_post(AGENT_MERGE_BODY, claims="- none")
          post_text = post_text.replace("POST-20260923-01", "POST-20261112-99")
          post_text = post_text.replace(
              "created: 2026-09-23",
              "created: 2026-11-12\nidea: IDEA-20261112-02",
          )
          post_path.write_text(post_text, encoding="utf-8")
          errors = engine.check_artifact(post_path, self.claims, self.hooks, root)
          self.assertEqual(errors, [])

  def test_author_interpretation_allowed_without_personal_claim(self):
      sentence = "I treat an unattended process that commits to clear a failing workflow as a write."
      self.assertEqual(engine.classify_statement(sentence, self.claims), "author_interpretation")
      verdict, _ = engine.review_body(sentence, [], self.claims)
      self.assertEqual(verdict, "PASS")

  def test_framed_external_interpretation_passes_with_none_claims(self):
      body = (
          "I treat this as showing that agent concurrency turns repos into high-churn write systems. "
          "The lesson I take is that ref updates remain the scarce agreement surface."
      )
      verdict, reasons = engine.review_body(body, [], self.claims)
      self.assertEqual(verdict, "PASS", reasons)
      post = explanation_post(body, claims="- none")
      self.assertEqual(self.check(post), [])

  def test_framed_personal_outcome_still_blocked_without_claim(self):
      body = "I treat this as proof that we improved our throughput by 40%."
      verdict, reasons = engine.review_body(body, [], self.claims)
      self.assertEqual(verdict, "BLOCKED", reasons)
      self.assertTrue(any("no eligible claim is cited" in reason for reason in reasons))

  def test_reviewed_blocked_with_none_claims_is_structurally_valid(self):
      body = "I shipped a 40% latency drop on our service."
      digest = engine.body_hash(body)
      text = explanation_post(body, claims="- none").replace("status: drafted", "status: reviewed", 1)
      text = text.replace("review_verdict:", "review_verdict: BLOCKED", 1)
      text = text.replace("review_body_sha256:", f"review_body_sha256: {digest}", 1)
      text = text.replace(
          f"body_sha256: {digest}",
          f"body_sha256: {digest}\nidea: IDEA-20260923-01",
          1,
      )
      text += "\n## Style report\n\nsignal only\n\n## Review\n\nDeterministic review:\n- no eligible claim is cited\n"
      self.assertEqual(self.check(text), [])

  def test_blank_claims_section_still_fails(self):
      post = explanation_post(MRR_DEFINITION, claims="")
      self.assertTrue(any("claims section is empty" in error for error in self.check(post)))

  def test_personal_action_still_blocked_without_claim(self):
      body = "I tested the new workflow and it improved our throughput."
      verdict, reasons = engine.review_body(body, [], self.claims)
      self.assertEqual(verdict, "BLOCKED", reasons)
      self.assertTrue(any("no eligible claim is cited" in reason for reason in reasons))

  def test_first_person_measured_result_without_action_verb_still_blocked(self):
      body = "I shipped a 40% latency drop on our service."
      verdict, reasons = engine.review_body(body, [], self.claims)
      self.assertEqual(verdict, "BLOCKED", reasons)
      self.assertTrue(any("no eligible claim is cited" in reason for reason in reasons))

  def test_external_fact_without_research_coverage_is_blocked(self):
      body = "On 28 Oct 2099, Contoso integrated Quantum Widget into pull requests worldwide."
      verdict, reasons = engine.review_body(body, [], self.claims, source_bundle="unrelated note")
      self.assertEqual(verdict, "BLOCKED", reasons)
      self.assertTrue(any("external factual statement has no source" in reason for reason in reasons))

  def test_changelog_url_does_not_verify_unsourced_bypass_claim(self):
      body = (
          "On 15 Mar 2031, Contoso integrated Quantum Delete into every CI pipeline. "
          "https://github.blog/changelog/2026-10-28-agent-merge-watches-and-fixes-pull-requests-in-the-cloud/"
      )
      verdict, reasons = engine.review_body(body, [], self.claims, source_bundle=RECONCILIATION_BUNDLE)
      self.assertEqual(verdict, "BLOCKED", reasons)
      self.assertTrue(any("external factual statement has no source" in reason for reason in reasons))

  def test_review_hash_binds_same_body(self):
      body = AGENT_MERGE_BODY
      digest = engine.body_hash(body)
      self.assertEqual(digest, engine.body_hash(body))


APPROVED_AT = "2026-09-27T16:00:00Z"


def run_engine(func, *args, **kwargs):
    stdout, stderr = StringIO(), StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = func(*args, **kwargs)
    return code, stdout.getvalue(), stderr.getvalue()


class ApprovalFixture:
    @classmethod
    def setUpClass(cls):
        cls.claims = engine.load_claims()
        cls.by_id = {claim["id"]: claim for claim in cls.claims}
        cls.hooks = engine.load_hooks()
        cls.claim = cls.by_id["LEGAL_RAG_007"]

    def reviewed(self, root: Path, body: str = SCOPED_HIT_RATE, review: str = "PASS", status: str = "reviewed") -> Path:
        folder = root / "drafts"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "POST-20260923-01.md"
        path.write_text(post_text(self.claim, body, status=status, review=review), encoding="utf-8")
        return path

    def p5_claims(self) -> str:
        eight = self.by_id["LEGAL_RAG_008"]
        return claim_lines([(self.claim, "fact"), (eight, "fact")])

    def p5_ready(
        self,
        root: Path,
        body: str = STRONG_P5_BODY,
        review: str = "PASS",
        status: str = "reviewed",
    ) -> Path:
        folder = root / "drafts"
        folder.mkdir(parents=True, exist_ok=True)
        (root / "previews").mkdir(parents=True, exist_ok=True)
        path = folder / "POST-20260923-01.md"
        path.write_text(
            post_text(self.claim, body, status=status, review=review, claims=self.p5_claims()),
            encoding="utf-8",
        )
        self.assertEqual(engine.check_artifact(path, self.claims, self.hooks, root), [])
        code, _, err = run_engine(engine.apply_editorial, path, root, now=APPROVED_AT)
        self.assertEqual(code, 0, err)
        preview_code, _ = preview_mod.generate_preview_for_post(root, root, "POST-20260923-01")
        self.assertEqual(preview_code, 0)
        return path


class ApprovalTests(ApprovalFixture, unittest.TestCase):
    """P3: review PASS is not human approval, and approval is bound to that body."""

    def test_pass_is_not_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self.reviewed(root)
            self.assertEqual(engine.check_artifact(path, self.claims, self.hooks, root), [])
            fields, _ = engine.parse_frontmatter(path.read_text(encoding="utf-8"))
            self.assertEqual(fields["status"], "reviewed")
            self.assertEqual(fields["review_verdict"], "PASS")
            self.assertNotIn("approval_status", fields)
            self.assertNotIn("approved_body_sha256", fields)
            self.assertFalse((root / "approved" / path.name).exists())
            self.assertFalse((root / "history.jsonl").exists())

    def test_approve_binds_the_reviewed_body_and_moves_one_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self.p5_ready(root)
            before_body = engine.section_map(engine.parse_frontmatter(path.read_text(encoding="utf-8"))[1])["Body"]
            code, stdout, stderr = run_engine(engine.approve_post, path, root, now=APPROVED_AT)
            self.assertEqual(code, 0, stderr)
            self.assertFalse(path.exists())
            approved = root / "approved" / "POST-20260923-01.md"
            self.assertTrue(approved.is_file())
            self.assertIn("approved POST-20260923-01", stdout)
            fields, rest = engine.parse_frontmatter(approved.read_text(encoding="utf-8"))
            digest = engine.body_hash(engine.section_map(rest)["Body"])
            self.assertEqual(engine.section_map(rest)["Body"], before_body)
            self.assertEqual(fields["status"], "approved")
            self.assertEqual(fields["approval_status"], "approved")
            self.assertEqual(fields["review_verdict"], "PASS")
            self.assertEqual(fields["body_sha256"], digest)
            self.assertEqual(fields["review_body_sha256"], digest)
            self.assertEqual(fields["approved_body_sha256"], digest)
            self.assertEqual(fields["approved_at"], APPROVED_AT)
            self.assertEqual(engine.check_artifact(approved, self.claims, self.hooks, root), [])
            self.assertEqual(engine.next_id(root, "post", "20260923"), "POST-20260923-02")
            lines = (root / "history.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            event = json.loads(lines[0])
            self.assertEqual(list(event), ["event", "post_id", "body_sha256", "timestamp"])
            self.assertEqual(event["event"], "approved")
            self.assertEqual(event["post_id"], "POST-20260923-01")
            self.assertEqual(event["body_sha256"], digest)
            self.assertEqual(event["timestamp"], APPROVED_AT)
            self.assertNotIn("@", lines[0])

            again, again_out, again_err = run_engine(
                engine.approve_post, approved, root, now="2026-09-28T00:00:00Z"
            )
            self.assertEqual(again, 0, again_err)
            self.assertIn("already approved", again_out)
            fields_again, _ = engine.parse_frontmatter(approved.read_text(encoding="utf-8"))
            self.assertEqual(fields_again["approved_at"], APPROVED_AT)
            self.assertEqual(len((root / "history.jsonl").read_text(encoding="utf-8").splitlines()), 1)

    def test_approve_by_id_and_refuses_a_missing_or_foreign_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.p5_ready(root)
            code, _, stderr = run_engine(engine.approve_from_spec, "POST-20260923-01", root, now=APPROVED_AT)
            self.assertEqual(code, 0, stderr)
            self.assertTrue((root / "approved" / "POST-20260923-01.md").is_file())
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            missing, _, stderr = run_engine(engine.approve_post, root / "drafts" / "POST-20260923-01.md", root)
            self.assertEqual(missing, 1)
            self.assertIn("artifact does not exist", stderr)
            self.assertFalse((root / "history.jsonl").exists())
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as other:
            root = Path(tmp)
            path = self.p5_ready(root)
            code, _, stderr = run_engine(engine.approve_post, path, Path(other), now=APPROVED_AT)
            self.assertEqual(code, 1)
            self.assertIn("outside content/", stderr)
            self.assertTrue(path.is_file())

    def test_approval_requires_editorial_and_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self.reviewed(root)
            code, _, stderr = run_engine(engine.approve_post, path, root, now=APPROVED_AT)
            self.assertEqual(code, 1)
            self.assertIn("editorial review must run first", stderr)

    def test_approval_requires_a_current_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            drafted = self.reviewed(root, review="", status="drafted")
            code, _, stderr = run_engine(engine.approve_post, drafted, root, now=APPROVED_AT)
            self.assertEqual(code, 1)
            self.assertIn("review must run first", stderr)
            self.assertIn("does not run li-review", stderr)
            self.assertTrue(drafted.is_file())
            self.assertFalse((root / "history.jsonl").exists())

            styled = self.reviewed(root, review="", status="styled")
            code, _, stderr = run_engine(engine.approve_post, styled, root, now=APPROVED_AT)
            self.assertEqual(code, 1)
            self.assertIn("review must run first", stderr)

            needs_edit = self.reviewed(root, review="NEEDS_EDIT")
            code, _, stderr = run_engine(engine.approve_post, needs_edit, root, now=APPROVED_AT)
            self.assertEqual(code, 1)
            self.assertIn("review_verdict is NEEDS_EDIT", stderr)

            blocked = self.reviewed(root, review="BLOCKED")
            code, _, stderr = run_engine(engine.approve_post, blocked, root, now=APPROVED_AT)
            self.assertEqual(code, 1)
            self.assertIn("review_verdict is BLOCKED", stderr)
            self.assertFalse((root / "approved").exists())

    def test_stale_or_inconsistent_review_is_not_rewritten_into_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self.reviewed(root)
            stale = path.read_text(encoding="utf-8").replace(
                SCOPED_HIT_RATE, SCOPED_HIT_RATE + " The same evaluation still had nine questions."
            )
            path.write_text(stale, encoding="utf-8")
            code, _, stderr = run_engine(engine.approve_post, path, root, now=APPROVED_AT)
            self.assertEqual(code, 1)
            self.assertIn("review is stale", stderr)
            self.assertIn("review must run first", stderr)
            self.assertEqual(path.read_text(encoding="utf-8"), stale)
            self.assertFalse((root / "approved" / path.name).exists())
            self.assertFalse((root / "history.jsonl").exists())

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            body = SCOPED_HIT_RATE + "\n\nThoughts?"
            path = self.reviewed(root, body=body, review="PASS")
            before = path.read_text(encoding="utf-8")
            errors = engine.check_artifact(path, self.claims, self.hooks, root)
            self.assertTrue(any("review_verdict must be NEEDS_EDIT" in error for error in errors))
            code, _, stderr = run_engine(engine.approve_post, path, root, now=APPROVED_AT)
            self.assertEqual(code, 1)
            self.assertIn("editorial review must run first", stderr)
            self.assertEqual(path.read_text(encoding="utf-8"), before)
            self.assertFalse((root / "history.jsonl").exists())

    def test_published_and_non_posts_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self.reviewed(root)
            published = path.read_text(encoding="utf-8").replace("status: reviewed", "status: published", 1)
            path.write_text(published, encoding="utf-8")
            code, _, stderr = run_engine(engine.approve_post, path, root, now=APPROVED_AT)
            self.assertEqual(code, 1)
            self.assertIn("already published", stderr)

            folder = root / "published"
            folder.mkdir()
            moved = folder / path.name
            path.replace(moved)
            restored = moved.read_text(encoding="utf-8").replace("status: published", "status: reviewed", 1)
            moved.write_text(restored, encoding="utf-8")
            code, _, stderr = run_engine(engine.approve_post, moved, root, now=APPROVED_AT)
            self.assertEqual(code, 1)
            self.assertIn("already published", stderr)

            idea = root / "ideas"
            idea.mkdir()
            idea_path = idea / "IDEA-20260923-01.md"
            idea_path.write_text(
                "---\nid: IDEA-20260923-01\nkind: idea\nstatus: proposed\ncreated: 2026-09-23\n---\n\n## Insight\n\n",
                encoding="utf-8",
            )
            code, _, stderr = run_engine(engine.approve_post, idea_path, root, now=APPROVED_AT)
            self.assertEqual(code, 1)
            self.assertIn("not a post artifact", stderr)
            self.assertFalse((root / "history.jsonl").exists())

    def test_reviewed_edit_message_stays_about_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self.reviewed(root)
            path.write_text(
                path.read_text(encoding="utf-8").replace(
                    SCOPED_HIT_RATE, SCOPED_HIT_RATE + " The same evaluation still had nine questions."
                ),
                encoding="utf-8",
            )
            code, _, stderr = run_engine(engine.stamp, path, False, None)
            self.assertEqual(code, 1)
            self.assertIn("body changed after review", stderr)
            self.assertNotIn("after approval", stderr)

    def test_approval_metadata_cannot_sit_on_an_unapproved_post(self):
        text = post_text(self.claim, SCOPED_HIT_RATE, status="reviewed", review="PASS")
        fields, rest = engine.parse_frontmatter(text)
        digest = fields["review_body_sha256"]
        text = text.replace(
            f"review_body_sha256: {digest}",
            "\n".join(
                [
                    f"review_body_sha256: {digest}",
                    "approval_status: approved",
                    f"approved_body_sha256: {digest}",
                    f"approved_at: {APPROVED_AT}",
                ]
            ),
            1,
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "drafts" / "POST-20260923-01.md"
            path.parent.mkdir()
            path.write_text(text, encoding="utf-8")
            errors = engine.check_artifact(path, self.claims, self.hooks, root)
            self.assertTrue(any("approval fields are set but status is not approved" in error for error in errors))
            relocated = text.replace("status: reviewed", "status: approved", 1)
            path.write_text(relocated, encoding="utf-8")
            errors = engine.check_artifact(path, self.claims, self.hooks, root)
            self.assertTrue(any("approved posts live in content/approved/" in error for error in errors))
            approved = root / "approved" / path.name
            approved.parent.mkdir()
            approved.write_text(relocated, encoding="utf-8")
            self.assertEqual(engine.check_artifact(approved, self.claims, self.hooks, root), [])

    def test_duplicate_copies_fail_the_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self.reviewed(root)
            mirror = root / "approved" / path.name
            mirror.parent.mkdir()
            shutil.copy(path, mirror)
            code, _, stderr = run_engine(engine.approve_from_spec, path.stem, root, now=APPROVED_AT)
            self.assertEqual(code, 1)
            self.assertIn("duplicate artifacts", stderr)
            self.assertFalse((root / "history.jsonl").exists())

            mirror.unlink()
            path = self.p5_ready(root)
            code, _, stderr = run_engine(engine.approve_post, path, root, now=APPROVED_AT)
            self.assertEqual(code, 0, stderr)
            shutil.copy(root / "approved" / path.name, path)
            errors = engine.check_content(root, self.claims, self.hooks)
            self.assertTrue(any("duplicate id POST-20260923-01" in error for error in errors))

    def test_cli_refuses_an_unknown_post_without_writing_history(self):
        history = ROOT / "content" / "history.jsonl"
        before = history.read_bytes()
        completed = subprocess.run(
            [sys.executable, str(SCRIPTS / "content_engine.py"), "approve", "POST-19990101-01"],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        self.assertEqual(completed.returncode, 1)
        self.assertIn("artifact does not exist", completed.stderr)
        self.assertNotIn("review must run first", completed.stderr)
        self.assertEqual(history.read_bytes(), before)
        parser = engine.build_parser()
        with redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args(["publish", "POST-20260923-01"])

    def test_approve_skill_is_explicit_only(self):
        text = (ROOT / ".cursor" / "skills" / "li-approve" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("disable-model-invocation: true", text)
        self.assertIn("POST-YYYYMMDD-NN", text)
        self.assertIn("does not run li-review", text.casefold())
        self.assertNotIn("confirm?", text.casefold())


REOPENED_AT = "2026-09-27T18:00:00Z"
BLOCKED_BODY = "My RAG system achieved 100% accuracy."
NEEDS_EDIT_BODY = SCOPED_HIT_RATE + "\n\nThoughts?"


class LifecycleContractTests(ApprovalFixture, unittest.TestCase):
    """Cases A-I. Approval is explicit, and an approved file is not edited in place."""

    def test_a_valid_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self.p5_ready(root)
            code, stdout, stderr = run_engine(engine.approve_post, path, root, now=APPROVED_AT)
            self.assertEqual(code, 0, stderr)
            self.assertIn("approved POST-20260923-01", stdout)
            self.assertFalse(path.exists())
            self.assertTrue((root / "approved" / path.name).is_file())

    def test_b_missing_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self.reviewed(root, review="", status="drafted")
            before = path.read_text(encoding="utf-8")
            code, _, stderr = run_engine(engine.approve_post, path, root, now=APPROVED_AT)
            self.assertEqual(code, 1)
            self.assertIn("review must run first", stderr)
            self.assertEqual(path.read_text(encoding="utf-8"), before)
            self.assertFalse((root / "approved").exists())
            self.assertFalse((root / "history.jsonl").exists())

    def test_c_needs_edit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self.reviewed(root, body=NEEDS_EDIT_BODY, review="NEEDS_EDIT")
            self.assertEqual(engine.check_artifact(path, self.claims, self.hooks, root), [])
            before = path.read_text(encoding="utf-8")
            code, _, stderr = run_engine(engine.approve_post, path, root, now=APPROVED_AT)
            self.assertEqual(code, 1)
            self.assertIn("review_verdict is NEEDS_EDIT", stderr)
            self.assertEqual(path.read_text(encoding="utf-8"), before)
            self.assertFalse((root / "history.jsonl").exists())

    def test_d_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self.reviewed(root, body=BLOCKED_BODY, review="BLOCKED")
            self.assertEqual(engine.check_artifact(path, self.claims, self.hooks, root), [])
            before = path.read_text(encoding="utf-8")
            code, _, stderr = run_engine(engine.approve_post, path, root, now=APPROVED_AT)
            self.assertEqual(code, 1)
            self.assertIn("review_verdict is BLOCKED", stderr)
            self.assertEqual(path.read_text(encoding="utf-8"), before)
            self.assertFalse((root / "approved").exists())

    def test_e_stale_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self.reviewed(root)
            stale = path.read_text(encoding="utf-8").replace(
                SCOPED_HIT_RATE, SCOPED_HIT_RATE + " The same evaluation still had nine questions."
            )
            path.write_text(stale, encoding="utf-8")
            code, _, stderr = run_engine(engine.approve_post, path, root, now=APPROVED_AT)
            self.assertEqual(code, 1)
            self.assertIn("review is stale", stderr)
            self.assertEqual(path.read_text(encoding="utf-8"), stale)
            self.assertFalse((root / "approved" / path.name).exists())
            self.assertFalse((root / "history.jsonl").exists())

    def test_f_approval_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self.p5_ready(root)
            code, _, stderr = run_engine(engine.approve_post, path, root, now=APPROVED_AT)
            self.assertEqual(code, 0, stderr)
            approved = root / "approved" / path.name
            fields, rest = engine.parse_frontmatter(approved.read_text(encoding="utf-8"))
            digest = engine.body_hash(engine.section_map(rest)["Body"])
            self.assertEqual(fields["body_sha256"], digest)
            self.assertEqual(fields["review_body_sha256"], digest)
            self.assertEqual(fields["approved_body_sha256"], digest)
            self.assertEqual(fields["editorial_body_sha256"], digest)
            self.assertEqual(fields["preview_body_sha256"], digest)

    def test_g_external_mutation_fails_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self.p5_ready(root)
            code, _, stderr = run_engine(engine.approve_post, path, root, now=APPROVED_AT)
            self.assertEqual(code, 0, stderr)
            approved = root / "approved" / path.name
            original = approved.read_text(encoding="utf-8")
            fields, _ = engine.parse_frontmatter(original)
            mutated = original.replace(
                STRONG_P5_BODY, STRONG_P5_BODY + " The same evaluation still had nine questions."
            )
            approved.write_text(mutated, encoding="utf-8")
            errors = engine.check_artifact(approved, self.claims, self.hooks, root)
            self.assertTrue(errors)
            self.assertTrue(
                any(
                    phrase in error
                    for error in errors
                    for phrase in (
                        "approval is stale",
                        "previous PASS is stale",
                        "editorial PASS is stale",
                        "preview is stale",
                    )
                )
            )
            refused, _, refuse_err = run_engine(engine.reopen_post, approved, root, now=REOPENED_AT)
            self.assertEqual(refused, 1)
            self.assertIn("not a valid approved artifact", refuse_err)
            self.assertEqual(approved.read_text(encoding="utf-8"), mutated)
            self.assertFalse((root / "drafts" / path.name).exists())
            still, _ = engine.parse_frontmatter(approved.read_text(encoding="utf-8"))
            self.assertEqual(still["approval_status"], fields["approval_status"])
            self.assertEqual(still["approved_body_sha256"], fields["approved_body_sha256"])
            self.assertEqual(still["approved_at"], fields["approved_at"])
            self.assertEqual(len((root / "history.jsonl").read_text(encoding="utf-8").splitlines()), 1)

    def test_h_humanizer_refuses_an_approved_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self.p5_ready(root)
            code, _, stderr = run_engine(engine.approve_post, path, root, now=APPROVED_AT)
            self.assertEqual(code, 0, stderr)
            approved = root / "approved" / path.name
            before = approved.read_text(encoding="utf-8")
            fields_before, _ = engine.parse_frontmatter(before)
            for invalidate in (False, True):
                held, stdout, hold_err = run_engine(engine.stamp, approved, invalidate, "styled")
                self.assertEqual(held, 1)
                self.assertEqual(stdout, "")
                self.assertIn(
                    "POST-20260923-01 is approved. Reopen it explicitly before editing.",
                    hold_err,
                )
                self.assertEqual(approved.read_text(encoding="utf-8"), before)
                self.assertFalse((root / "drafts" / path.name).exists())
            fields_after, _ = engine.parse_frontmatter(approved.read_text(encoding="utf-8"))
            for key in (
                "status",
                "body_sha256",
                "review_verdict",
                "review_body_sha256",
                "approval_status",
                "approved_body_sha256",
                "approved_at",
            ):
                self.assertEqual(fields_after[key], fields_before[key])
            self.assertEqual(len((root / "history.jsonl").read_text(encoding="utf-8").splitlines()), 1)

        human = (ROOT / ".cursor" / "skills" / "li-human" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("POST-... is approved. Reopen it explicitly before editing.", human)
        self.assertIn("do not stamp it", human.casefold())
        self.assertIn("this skill does not reopen", human.casefold())
        self.assertNotIn("approval_invalidated", human)

    def test_i_explicit_reopen(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self.p5_ready(root)
            code, _, stderr = run_engine(engine.approve_post, path, root, now=APPROVED_AT)
            self.assertEqual(code, 0, stderr)
            approved = root / "approved" / "POST-20260923-01.md"
            approved_fields, approved_rest = engine.parse_frontmatter(approved.read_text(encoding="utf-8"))
            approved_body = engine.section_map(approved_rest)["Body"]
            approved_hash = approved_fields["approved_body_sha256"]

            code, stdout, stderr = run_engine(engine.reopen_from_spec, "POST-20260923-01", root, now=REOPENED_AT)
            self.assertEqual(code, 0, stderr)
            self.assertIn("reopened POST-20260923-01", stdout)
            self.assertFalse(approved.exists())
            draft = root / "drafts" / "POST-20260923-01.md"
            self.assertTrue(draft.is_file())
            fields, rest = engine.parse_frontmatter(draft.read_text(encoding="utf-8"))
            body = engine.section_map(rest)["Body"]
            digest = engine.body_hash(body)
            self.assertEqual(fields["id"], "POST-20260923-01")
            self.assertEqual(fields["status"], "styled")
            self.assertEqual(body, approved_body)
            self.assertEqual(digest, approved_hash)
            self.assertEqual(fields["body_sha256"], digest)
            self.assertEqual(fields["review_verdict"], "")
            self.assertEqual(fields["review_body_sha256"], "")
            self.assertEqual(fields["approval_status"], "")
            self.assertEqual(fields["approved_body_sha256"], "")
            self.assertEqual(fields["approved_at"], "")
            self.assertEqual(engine.check_artifact(draft, self.claims, self.hooks, root), [])

            lines = (root / "history.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 2)
            approved_event = json.loads(lines[0])
            reopened_event = json.loads(lines[1])
            self.assertEqual(approved_event["event"], "approved")
            self.assertEqual(approved_event["body_sha256"], approved_hash)
            self.assertEqual(list(reopened_event), ["event", "post_id", "body_sha256", "timestamp"])
            self.assertEqual(reopened_event["event"], "reopened")
            self.assertEqual(reopened_event["post_id"], "POST-20260923-01")
            self.assertEqual(reopened_event["body_sha256"], digest)
            self.assertEqual(reopened_event["timestamp"], REOPENED_AT)
            self.assertNotIn("@", lines[1])

            before_retry = draft.read_text(encoding="utf-8")
            blocked, _, blocked_err = run_engine(engine.approve_post, draft, root, now="2026-09-28T00:00:00Z")
            self.assertEqual(blocked, 1)
            self.assertIn("review must run first", blocked_err)
            self.assertEqual(draft.read_text(encoding="utf-8"), before_retry)
            self.assertEqual(len((root / "history.jsonl").read_text(encoding="utf-8").splitlines()), 2)

            reviewed = draft.read_text(encoding="utf-8")
            reviewed = reviewed.replace("status: styled", "status: reviewed", 1)
            reviewed = reviewed.replace("review_verdict:", "review_verdict: PASS", 1)
            reviewed = reviewed.replace("review_body_sha256:", f"review_body_sha256: {digest}", 1)
            draft.write_text(reviewed, encoding="utf-8")
            self.assertEqual(engine.check_artifact(draft, self.claims, self.hooks, root), [])
            run_engine(engine.apply_editorial, draft, root, now=APPROVED_AT)
            preview_mod.generate_preview_for_post(root, root, "POST-20260923-01")
            again, _, again_err = run_engine(engine.approve_post, draft, root, now="2026-09-28T00:00:00Z")
            self.assertEqual(again, 0, again_err)
            history = (root / "history.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(history), 3)
            self.assertEqual(json.loads(history[0])["event"], "approved")
            self.assertEqual(json.loads(history[1])["event"], "reopened")
            self.assertEqual(json.loads(history[2])["event"], "approved")
            self.assertEqual(json.loads(history[0])["timestamp"], APPROVED_AT)

    def test_reopen_skill_is_explicit_only(self):
        text = (ROOT / ".cursor" / "skills" / "li-reopen" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("disable-model-invocation: true", text)
        self.assertIn("/li-reopen POST-YYYYMMDD-NN", text)
        folded = text.casefold()
        self.assertIn("does not humanize, rewrite, review, approve, or publish", folded)
        self.assertNotIn("approval_invalidated", text)
        contract = (ROOT / "docs" / "content-contract.md").read_text(encoding="utf-8")
        self.assertIn('"event": "reopened"', contract)
        self.assertNotIn("approval_invalidated", contract)

    def test_reopen_cli_refuses_an_unknown_post_without_writing_history(self):
        history = ROOT / "content" / "history.jsonl"
        before = history.read_bytes()
        completed = subprocess.run(
            [sys.executable, str(SCRIPTS / "content_engine.py"), "reopen", "POST-19990101-01"],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        self.assertEqual(completed.returncode, 1)
        self.assertIn("artifact does not exist", completed.stderr)
        self.assertEqual(history.read_bytes(), before)


TELEMETRY_EXPLICIT_CONTRAST_BODY = (
    "Default traces often show that a tool ran—and still omit what it was asked or returned.\n\n"
    "The contrast that matters: seeing a tool ran is not the same as knowing why the user got that completion. "
    "Keep three signals apart in the trace.\n\n"
    "Decision: instrument those boundaries first."
)

TELEMETRY_CAUSAL_BODY = (
    "The contrast that matters: seeing a tool ran is not the same as knowing why the user got that completion.\n\n"
    "A clean tool return followed by a weak completion is a model problem. "
    "A tool that executed without a usable return is a dependency problem.\n\n"
    "Decision: instrument those boundaries first."
)

NO_OPPOSITION_BODY = (
    "Teams compared retrieval metrics on dashboards in 2026 without naming the evaluation set "
    "or the cutoff they used when the score was taken.\n\n"
    "Naming the evaluation set is the part a hiring lead can reuse when they compare candidates again.\n\n"
    "Lesson: judge retrieval on a defined question set and keep the cutoff explicit."
)


REFRAME_CONTRAST_BODY = (
    "An AI agent can flag a coordinate. That is not a discovery.\n\n"
    "On 2026-11-12 a journal record already listed the site the agents flagged.\n\n"
    "I treat an agent flag as a hypothesis until someone checks the prior record."
)

COPULAR_CONTRAST_BODY = (
    "Injected memory in 2026 agents is a bounded index, not a truth store.\n\n"
    "A wrong note kept for 30 sessions keeps steering every later answer.\n\n"
    "I treat correction as its own step."
)

# Opposition is present, but nothing concrete is observed or decided.
GENERIC_REFRAME_BODY = (
    "Agents can produce output. That is not a guarantee of quality.\n\n"
    "Orchestration, context, and architecture shape productivity and innovation across every team."
)


class EditorialContrastTests(unittest.TestCase):
    def test_reframe_without_contrast_keywords_is_contrast(self):
        self.assertIsNone(editorial_mod.CONTRAST_MARKERS.search(REFRAME_CONTRAST_BODY))
        self.assertTrue(editorial_mod.tension_present(REFRAME_CONTRAST_BODY))
        self.assertTrue(editorial_mod.tension_present(COPULAR_CONTRAST_BODY))
        for body in (REFRAME_CONTRAST_BODY, COPULAR_CONTRAST_BODY):
            findings = editorial_mod.editorial_review(body)
            self.assertNotIn("Weakest dimension: contrast", findings.weakest)
            self.assertNotIn("State the opposition", findings.structural_change)

    def test_a_bare_negative_statement_is_not_contrast(self):
        for body in (
            "That is not a discovery. The team posted a map of 2026 scans.",
            "The scan found nothing. That is not a failure of the 2026 tool.",
            "The scan finished in 2026. This is not ready for review.",
            "The scan finished in 2026. It is not a problem for anyone.",
            "The team shipped the build, not a single test was skipped in 2026.",
        ):
            self.assertFalse(editorial_mod.tension_present(body), body)

    def test_recorded_maya_draft_keeps_its_verdict_and_its_reframe_is_contrast(self):
        draft = (ROOT / "content" / "drafts" / "POST-20261113-01.md").read_text(encoding="utf-8")
        fields, rest = engine.parse_frontmatter(draft)
        body = engine.section_map(rest)["Body"]
        self.assertIn("An AI agent can flag a coordinate. That is not a discovery.", body)
        self.assertEqual(fields["editorial_verdict"], "REVISE")
        self.assertEqual(fields["editorial_body_sha256"], engine.body_hash(body))
        self.assertTrue(editorial_mod.tension_present(body))
        findings = editorial_mod.editorial_review(body, engine.section_map(rest).get("Evidence gaps", ""))
        self.assertEqual(findings.verdict, "REVISE")
        self.assertEqual(findings.weakest, "Weakest dimension: reader value.")

    def test_a_reframe_alone_does_not_supply_reader_value(self):
        findings = editorial_mod.editorial_review(REFRAME_CONTRAST_BODY)
        self.assertEqual(findings.verdict, "REVISE")
        self.assertEqual(findings.weakest, "Weakest dimension: reader value.")
        with_lesson = REFRAME_CONTRAST_BODY + "\n\nThe decision: name the record you checked before you call anything new."
        self.assertNotIn("reader value", editorial_mod.editorial_review(with_lesson).weakest)

    def test_contrast_with_a_generic_conclusion_is_still_revise(self):
        self.assertTrue(editorial_mod.tension_present(GENERIC_REFRAME_BODY))
        findings = editorial_mod.editorial_review(GENERIC_REFRAME_BODY)
        self.assertEqual(findings.verdict, "REVISE")
        self.assertNotIn("contrast", findings.weakest)
        self.assertEqual(findings.too_generic, "Abstract nouns without a concrete event.")

    def test_explicit_contrast_is_not_weakest_dimension(self):
        findings = editorial_mod.editorial_review(TELEMETRY_EXPLICIT_CONTRAST_BODY)
        self.assertNotIn("Weakest dimension: contrast", findings.weakest)
        self.assertNotIn("Lead with observation, then contrast or decision, then lesson.", findings.structural_change)

    def test_causal_identity_overclaim_quotes_the_sentence(self):
        findings = editorial_mod.editorial_review(TELEMETRY_CAUSAL_BODY)
        self.assertEqual(findings.verdict, "REVISE")
        self.assertIn("Causal overclaim:", findings.weakest)
        self.assertIn("model problem", findings.weakest)

    def test_missing_opposition_quotes_opening(self):
        findings = editorial_mod.editorial_review(NO_OPPOSITION_BODY)
        self.assertEqual(findings.verdict, "REVISE")
        self.assertEqual(findings.weakest, "Weakest dimension: contrast.")
        self.assertIn("Current opening:", findings.structural_change)
        self.assertNotIn("Lead with observation, then contrast or decision, then lesson.", findings.structural_change)


class P6ArtifactIntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.claims = engine.load_claims()
        cls.hooks = engine.load_hooks()

    def test_post_20261113_02_remains_blocked_but_structurally_valid(self):
        path = ROOT / "content/drafts/POST-20261113-02.md"
        fields, _rest = engine.parse_frontmatter(path.read_text(encoding="utf-8"))
        self.assertEqual(fields["review_verdict"], "BLOCKED")
        self.assertEqual(fields["body_sha256"], fields["review_body_sha256"])
        _fields, _body_rest = engine.parse_frontmatter(path.read_text(encoding="utf-8"))
        self.assertEqual(fields["body_sha256"], engine.body_hash(engine.section_map(_body_rest)["Body"]))
        errors = engine.check_artifact(path, self.claims, self.hooks, ROOT / "content")
        self.assertEqual(errors, [])
        ready, reasons = cycle_mod.readiness_report(ROOT / "content", "POST-20261113-02")
        self.assertFalse(ready)
        self.assertTrue(any("unsupported claim" in reason for reason in reasons))


if __name__ == "__main__":
    unittest.main()
