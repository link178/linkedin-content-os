"""P5 editorial, visual, and preview tests. No LinkedIn calls."""

from __future__ import annotations

import re
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
import editorial as editorial_mod  # noqa: E402
import preview as preview_mod  # noqa: E402
import visual as visual_mod  # noqa: E402
from test_content_engine import (  # noqa: E402
    APPROVED_AT,
    ApprovalFixture,
    STRONG_P5_BODY,
    post_text,
    run_engine,
    SCOPED_HIT_RATE,
)

GENERIC_AI_BODY = (
    "AI agents are changing software engineering and context is important for orchestration and productivity."
)
COMPARISON_BODY = (
    "I tested extra orchestration on one IntelliJ workflow and observed no improvement but more friction. "
    "I kept lightweight project context instead. Lesson: complexity has to earn its place."
)


class P5EditorialTests(ApprovalFixture, unittest.TestCase):
    def test_a_strong_post_editorial_pass(self):
        findings = editorial_mod.editorial_review(STRONG_P5_BODY)
        self.assertEqual(findings.verdict, "PASS")

    def test_b_generic_commentary_not_pass(self):
        findings = editorial_mod.editorial_review(GENERIC_AI_BODY)
        self.assertNotEqual(findings.verdict, "PASS")

    def test_c_semantic_pass_can_coexist_with_editorial_revise(self):
        findings = editorial_mod.editorial_review(SCOPED_HIT_RATE)
        self.assertEqual(findings.verdict, "REVISE")

    def test_d_evidence_too_thin(self):
        gaps = "We cannot invent per-question ranks; evidence is insufficient for stronger detail."
        findings = editorial_mod.editorial_review("Context matters for agents in software engineering.", gaps)
        self.assertEqual(findings.verdict, "EVIDENCE_TOO_THIN")
        self.assertIn("Do not invent specificity", findings.structural_change)

    def test_e_stale_editorial_after_body_edit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self.p5_ready(root)
            stale = path.read_text(encoding="utf-8").replace(
                STRONG_P5_BODY, STRONG_P5_BODY + " Extra sentence."
            )
            path.write_text(stale, encoding="utf-8")
            errors = engine.check_artifact(path, self.claims, self.hooks, root)
            self.assertTrue(any("editorial PASS is stale" in error or "editorial hash is stale" in error for error in errors))


class P5VisualTests(ApprovalFixture, unittest.TestCase):
    def test_f_visual_none_for_strong_linear_post(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self.p5_ready(root)
            code, result = visual_mod.generate_visual_for_post(root, "POST-20260923-01", strategy="NONE")
            self.assertEqual(code, 0)
            assert result is not None
            self.assertEqual(result["strategy"], "NONE")

    def test_g_visual_generated_for_comparison(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.p5_ready(root)
            code, result = visual_mod.generate_visual_for_post(
                root, "POST-20260923-01", strategy="COMPARISON"
            )
            self.assertEqual(code, 0)
            assert result is not None
            self.assertEqual(result["strategy"], "COMPARISON")
            svg = root / "visuals" / "POST-20260923-01" / "visual.svg"
            self.assertTrue(svg.is_file())
            self.assertEqual(result["visual_sha256"], visual_mod.bytes_sha256(svg.read_bytes()))


class P5PreviewApprovalTests(ApprovalFixture, unittest.TestCase):
    def test_h_visual_stale_blocks_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self.p5_ready(root)
            visual_mod.generate_visual_for_post(root, "POST-20260923-01", strategy="COMPARISON")
            preview_mod.generate_preview_for_post(root, root, "POST-20260923-01")
            fields, _ = engine.parse_frontmatter(path.read_text(encoding="utf-8"))
            self.assertTrue(fields.get("preview_visual_sha256"))
            svg = root / "visuals" / "POST-20260923-01" / "visual.svg"
            svg.write_bytes(svg.read_bytes() + b"\n")
            code, _, stderr = run_engine(engine.approve_post, path, root, now=APPROVED_AT)
            self.assertEqual(code, 1)
            self.assertIn("preview visual is stale", stderr)

    def test_i_preview_contains_exact_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self.p5_ready(root)
            code, out = preview_mod.generate_preview_for_post(root, root, "POST-20260923-01")
            self.assertEqual(code, 0)
            assert out is not None
            html = out.read_text(encoding="utf-8")
            for block in STRONG_P5_BODY.split("\n\n"):
                if block.strip():
                    self.assertIn(block.strip().replace("&", "&amp;"), html)

    def test_j_stale_preview_blocks_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self.p5_ready(root)
            stale = path.read_text(encoding="utf-8").replace(
                STRONG_P5_BODY, STRONG_P5_BODY + " Another line."
            )
            path.write_text(stale, encoding="utf-8")
            code, _, stderr = run_engine(engine.approve_post, path, root, now=APPROVED_AT)
            self.assertEqual(code, 1)
            self.assertTrue("preview is stale" in stderr or "review is stale" in stderr)

    def test_k_approval_without_editorial_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self.reviewed(root)
            code, _, stderr = run_engine(engine.approve_post, path, root, now=APPROVED_AT)
            self.assertEqual(code, 1)
            self.assertIn("editorial review must run first", stderr)

    def test_l_approval_with_editorial_revise_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self.reviewed(root)
            run_engine(engine.apply_editorial, path, root, now=APPROVED_AT)
            code, _, stderr = run_engine(engine.approve_post, path, root, now=APPROVED_AT)
            self.assertEqual(code, 1)
            self.assertIn("editorial_verdict is REVISE", stderr)

    def test_m_approval_without_preview_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "drafts"
            folder.mkdir(parents=True)
            path = folder / "POST-20260923-01.md"
            path.write_text(
                post_text(
                    self.claim,
                    STRONG_P5_BODY,
                    status="reviewed",
                    review="PASS",
                    claims=self.p5_claims(),
                ),
                encoding="utf-8",
            )
            run_engine(engine.apply_editorial, path, root, now=APPROVED_AT)
            code, _, stderr = run_engine(engine.approve_post, path, root, now=APPROVED_AT)
            self.assertEqual(code, 1)
            self.assertIn("preview must run first", stderr)

    def test_n_approval_after_all_p5_gates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self.p5_ready(root)
            code, _, stderr = run_engine(engine.approve_post, path, root, now=APPROVED_AT)
            self.assertEqual(code, 0, stderr)

    def test_o_published_historical_post_still_valid(self):
        path = ROOT / "content" / "published" / "POST-20260927-01.md"
        self.assertTrue(path.is_file())
        errors = engine.check_artifact(path, engine.load_claims(), engine.load_hooks(), ROOT / "content")
        self.assertEqual(errors, [])


class P5PreviewSafetyTests(ApprovalFixture, unittest.TestCase):
    def test_preview_html_safety(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.p5_ready(root)
            code, out = preview_mod.generate_preview_for_post(root, root, "POST-20260923-01")
            self.assertEqual(code, 0)
            assert out is not None
            html = out.read_text(encoding="utf-8")
            self.assertNotIn("api.linkedin.com", html)
            self.assertNotIn("<script", html.lower())
            self.assertNotIn("LINKEDIN_ACCESS_TOKEN", html)
            self.assertIn("@media", html)
            self.assertIn("Approximate LinkedIn preview", html)


if __name__ == "__main__":
    unittest.main()
