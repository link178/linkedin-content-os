"""P5.1 strategy-first editorial and visual tests. No LinkedIn calls."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import content_engine as engine  # noqa: E402
import editorial as editorial_mod  # noqa: E402
import strategy_check as strategy_mod  # noqa: E402
import visual as visual_mod  # noqa: E402
from test_content_engine import (  # noqa: E402
    ApprovalFixture,
    STRONG_P5_BODY,
    post_text,
    run_engine,
    SCOPED_HIT_RATE,
)

BARE_METRIC_BODY = (
    "Hit Rate 1.0, MRR 0.75.\n\n"
    "Nine-question evaluation, cutoff k=5.\n\n"
    "Hit Rate alone does not tell the rank inside the top 5.\n\n"
    "MRR reflects the rank of the first relevant document.\n\n"
    "The Legal RAG Engine was built as a technical RAG project. "
    "My conclusion is that RAG quality has to be judged on a defined evaluation set rather than described as general accuracy."
)

READER_FIRST_BODY = (
    "A RAG system can put a relevant document in the top five and still rank it below rank 1. "
    "Hit Rate and MRR answer different questions about the same cutoff. "
    "In a nine-question retrieval evaluation at k=5, I measured Hit Rate 1.0 and MRR 0.75 on that defined set. "
    "Lesson: compare both metrics rather than calling it general accuracy."
)

PROJECT_SHOWCASE_BODY = (
    "I built a technical RAG backend to explore hybrid retrieval over legal-style documents. "
    "The interesting problem was not picking a vector database label but combining dense and sparse signals with fusion. "
    "I chose PostgreSQL with pgvector, hybrid retrieval, and Reciprocal Rank Fusion behind FastAPI. "
    "The evaluation stayed on a defined question set rather than a vague accuracy claim."
)

TREND_RECAP_BODY = (
    "OpenAI launched new agent features this week. Here are five things to know about the announcement and why it matters for everyone."
)

TREND_ANGLE_BODY = (
    "MCP 2026-07-28 moves the protocol toward stateless requests instead of long-lived sessions. "
    "That changes how I think about routing MCP servers behind load balancers in workflows I test with MCP integrations. "
    "I kept lightweight tool wiring until a session model actually blocked deployment."
)

SPECIALIST_FRAMED_BODY = (
    "Why Hit Rate alone can hide ranking quality in RAG: Hit Rate@5 only checks presence in the top five. "
    "Mean Reciprocal Rank uses the rank of the first relevant hit. "
    "In a nine-question evaluation at k=5 I measured Hit Rate 1.0 and MRR 0.75 on that set. "
    "Compare both before calling retrieval good."
)

INFLATION_BODY = (
    "Every RAG system always proves that Hit Rate 1.0 means perfect ranking for all queries. "
    "In a nine-question retrieval evaluation, the system achieved a Hit Rate of 1.0."
)

BROAD_PRINCIPLE_BODY = (
    "A working demo alone does not tell you the software around it is production-ready.\n\n"
    "I compared approaches with benchmark tasks instead of trusting an impression.\n\n"
    "Lesson: an explicit boundary and a repeatable check are different bars."
)


class P51EditorialTests(unittest.TestCase):
    def test_a_internal_metric_without_framing(self):
        findings = editorial_mod.editorial_review(BARE_METRIC_BODY)
        self.assertEqual(findings.standalone_value, "LOW")
        self.assertEqual(findings.verdict, "REVISE")

    def test_a_long_metric_first_opening_names_the_failure(self):
        body = (
            "A nine-question retrieval evaluation of the Legal RAG Engine achieved Hit Rate 1.0 and MRR 0.75.\n\n"
            "The project was built as a technical RAG system. Evaluation used top-k retrieval with k=5.\n\n"
            "My conclusion: RAG quality has to be judged on a defined evaluation set—not described as general accuracy."
        )
        self.assertTrue(editorial_mod.is_bare_metric_opening(body))
        findings = editorial_mod.editorial_review(body)
        self.assertEqual(findings.verdict, "REVISE")
        self.assertEqual(findings.standalone_value, "LOW")
        self.assertEqual(findings.audience_breadth, "SPECIALIST")
        self.assertEqual(findings.project_context_required, "HIGH")
        self.assertIn("Do not lead with Hit Rate", findings.structural_change)
        self.assertNotIn("Lead with observation", findings.structural_change)

    def test_b_reader_first_framing_higher_standalone(self):
        findings = editorial_mod.editorial_review(READER_FIRST_BODY)
        self.assertIn(findings.standalone_value, {"HIGH", "MEDIUM"})
        self.assertNotEqual(findings.standalone_value, "LOW")

    def test_c_project_showcase_eligible(self):
        findings = editorial_mod.editorial_review(PROJECT_SHOWCASE_BODY)
        self.assertIn(findings.standalone_value, {"HIGH", "MEDIUM"})
        self.assertIn(findings.verdict, {"PASS", "REVISE"})

    def test_d_trend_summary_low_value(self):
        findings = editorial_mod.editorial_review(TREND_RECAP_BODY)
        self.assertNotEqual(findings.verdict, "PASS")

    def test_e_trend_with_engineering_angle(self):
        findings = editorial_mod.editorial_review(TREND_ANGLE_BODY)
        self.assertIn(findings.verdict, {"PASS", "REVISE"})

    def test_f_specialist_deep_dive_can_pass(self):
        findings = editorial_mod.editorial_review(SPECIALIST_FRAMED_BODY)
        self.assertIn(findings.audience_breadth, {"AI_ENGINEERING", "SPECIALIST"})
        self.assertIn(findings.standalone_value, {"HIGH", "MEDIUM"})
        self.assertIn(findings.verdict, {"PASS", "REVISE"})
        self.assertNotEqual(findings.standalone_value, "LOW")

    def test_g_specialist_concentration_flag(self):
        topics = [
            {"audience": "SPECIALIST"},
            {"audience": "SPECIALIST"},
            {"audience": "SPECIALIST"},
        ]
        self.assertTrue(strategy_mod.compute_concentration_risk(topics))

    def test_j_no_evidence_inflation(self):
        findings = editorial_mod.editorial_review(INFLATION_BODY)
        self.assertEqual(findings.verdict, "REVISE")

    def test_k_broad_tech_principle_needs_no_project_context(self):
        findings = editorial_mod.editorial_review(BROAD_PRINCIPLE_BODY)
        self.assertEqual(findings.audience_breadth, "BROAD_TECH")
        self.assertEqual(findings.project_context_required, "NONE")
        self.assertEqual(findings.standalone_value, "HIGH")

    def test_k_specialist_reader_first_keeps_light_context(self):
        findings = editorial_mod.editorial_review(SPECIALIST_FRAMED_BODY)
        self.assertIn(findings.audience_breadth, {"AI_ENGINEERING", "SPECIALIST"})
        self.assertEqual(findings.project_context_required, "LIGHT")
        self.assertNotEqual(findings.project_context_required, "NONE")


class P51VisualTests(unittest.TestCase):
    def test_h_decorative_metric_no_auto_svg(self):
        gain = visual_mod.classify_visual_information_gain(BARE_METRIC_BODY, "METRIC")
        self.assertEqual(gain, "DECORATIVE")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = visual_mod.write_visual_bundle(
                root,
                "POST-TEST-01",
                "abc",
                BARE_METRIC_BODY,
                strategy=None,
                force_strategy=False,
            )
            self.assertEqual(result["visual_information_gain"], "DECORATIVE")
            self.assertEqual(result["strategy"], "NONE")
            self.assertFalse((root / "visuals" / "POST-TEST-01" / "visual.svg").is_file())

    def test_i_additive_illustrative_rank(self):
        gain = visual_mod.classify_visual_information_gain(SPECIALIST_FRAMED_BODY, "ILLUSTRATIVE_RANK")
        self.assertEqual(gain, "ADDITIVE")
        svg = visual_mod.render_illustrative_rank_svg()
        self.assertIn(b"illustrative", svg.lower())
        self.assertIn(b"Not measured per-query results", svg)
        self.assertIn(b"RR = 1", svg)
        self.assertIn(b"RR = 1/3", svg)
        self.assertNotIn(b"MRR differs", svg)


class P51RegressionTests(ApprovalFixture, unittest.TestCase):
    def test_strong_p5_body_still_passes(self):
        findings = editorial_mod.editorial_review(STRONG_P5_BODY)
        self.assertEqual(findings.verdict, "PASS")

    def test_scoped_hit_rate_still_revise(self):
        findings = editorial_mod.editorial_review(SCOPED_HIT_RATE)
        self.assertEqual(findings.verdict, "REVISE")

    def test_strategy_artifact_checks(self):
        path = ROOT / "content" / "strategy" / "STRATEGY-20261007-01.md"
        errors = engine.check_artifact(path, engine.load_claims(), engine.load_hooks(), ROOT / "content")
        self.assertEqual(errors, [], errors)

    def test_human_revise_blocks_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self.p5_ready(root)
            text = path.read_text(encoding="utf-8")
            text = text.replace(
                "editorial_verdict: PASS",
                "editorial_verdict: PASS\nhuman_editorial_decision: REVISE",
            )
            path.write_text(text, encoding="utf-8")
            from test_content_engine import APPROVED_AT

            code, _, stderr = run_engine(engine.approve_post, path, root, now=APPROVED_AT)
            self.assertEqual(code, 1)
            self.assertIn("human_editorial_decision", stderr)


if __name__ == "__main__":
    unittest.main()
