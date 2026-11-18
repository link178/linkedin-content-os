"""P4.2 tests. HTTP is mocked. These tests do not call LinkedIn."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import content_engine as engine
import linkedin_access as access
import linkedin_publish as publish

ROOT = Path(__file__).resolve().parent.parent
APPROVED_ID = "POST-20260927-01"
APPROVED_FIXTURE = ROOT / "scripts" / "fixtures" / f"{APPROVED_ID}.approved.md"
REAL_APPROVED = APPROVED_FIXTURE
REAL_HISTORY = ROOT / "content" / "history.jsonl"
TOKEN = "fixture-token"
SUBJECT = "fixture-sub"
SHARE_URN = "urn:li:share:6844785523593134080"
UGC_URN = "urn:li:ugcPost:6844785523593134080"
PUBLISHED_AT = "2026-10-05T08:15:00Z"
POSTS_ARGV = [APPROVED_ID, "--transport", publish.POSTS_API]


class FakeResponse:
    def __init__(self, status: int, body: bytes, headers: dict | None = None):
        self.status = status
        self.headers = headers or {}
        self._body = body

    def read(self) -> bytes:
        return self._body

    def getcode(self) -> int:
        return self.status

    def close(self) -> None:
        return None


class PublishOpener:
    def __init__(self, posts_status: int = 201, posts_headers: dict | None = None, posts_body: bytes = b"", userinfo_status: int = 200, userinfo_body: bytes | None = None, fail_post: BaseException | None = None):
        self.posts_status = posts_status
        self.posts_headers = posts_headers or {}
        self.posts_body = posts_body
        self.userinfo_status = userinfo_status
        self.userinfo_body = userinfo_body if userinfo_body is not None else json.dumps({"sub": SUBJECT, "name": "Example Member"}).encode()
        self.fail_post = fail_post
        self.calls: list[tuple[str, str, dict, bytes | None]] = []
        self.authorization_locations: list[str] = []

    def __call__(self, request, timeout=None):
        method = request.get_method()
        headers = {key.lower(): value for key, value in request.header_items()}
        self.calls.append((method, request.full_url, headers, request.data))
        if "authorization" in {key.lower() for key in request.headers}:
            self.authorization_locations.append("headers")
        if "Authorization" in request.unredirected_hdrs or "authorization" in {key.lower() for key in request.unredirected_hdrs}:
            self.authorization_locations.append("unredirected")
        if request.full_url == access.PROFILE_API_URL:
            raise AssertionError(request.full_url)
        if request.full_url == access.DISCOVERY_URL:
            if method != "GET":
                raise AssertionError(method)
            return FakeResponse(200, json.dumps({"userinfo_endpoint": access.USERINFO_URL}).encode())
        if request.full_url == access.USERINFO_URL:
            if method != "GET":
                raise AssertionError(method)
            if self.userinfo_status >= 400:
                raise urllib.error.HTTPError(request.full_url, self.userinfo_status, "error", {"Content-Type": "application/json"}, io.BytesIO(self.userinfo_body))
            return FakeResponse(self.userinfo_status, self.userinfo_body)
        if request.full_url in {publish.POSTS_URL, publish.UGC_POSTS_URL}:
            if method != "POST":
                raise AssertionError(method)
            if self.fail_post is not None:
                raise self.fail_post
            if self.posts_status >= 400:
                raise urllib.error.HTTPError(
                    request.full_url,
                    self.posts_status,
                    "error",
                    {"Content-Type": "application/json"},
                    io.BytesIO(self.posts_body),
                )
            return FakeResponse(self.posts_status, self.posts_body, self.posts_headers)
        raise AssertionError(request.full_url)


def post_calls(opener: PublishOpener) -> list[tuple]:
    return [call for call in opener.calls if call[0] == "POST"]


def run_publish(argv: list[str], **kwargs):
    # Keep the developer process token and repository .env out of these tests.
    isolated = tempfile.TemporaryDirectory() if "env_file" not in kwargs else None
    if "environ" not in kwargs:
        kwargs["environ"] = {}
    if isolated is not None:
        kwargs["env_file"] = Path(isolated.name) / ".env"
    stdout, stderr = io.StringIO(), io.StringIO()
    try:
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = publish.main(argv, **kwargs)
    finally:
        if isolated is not None:
            isolated.cleanup()
    return code, stdout.getvalue(), stderr.getvalue()


def temp_approved(test: unittest.TestCase, text: str | None = None) -> Path:
    folder = tempfile.TemporaryDirectory()
    test.addCleanup(folder.cleanup)
    root = Path(folder.name)
    (root / "approved").mkdir()
    (root / "drafts").mkdir()
    (root / "published").mkdir()
    body = text if text is not None else REAL_APPROVED.read_text(encoding="utf-8")
    (root / "approved" / f"{APPROVED_ID}.md").write_text(body, encoding="utf-8", newline="\n")
    return root


def approved_text() -> str:
    return REAL_APPROVED.read_text(encoding="utf-8")


class PublishTests(unittest.TestCase):
    def test_a_valid_approved_post_is_accepted_for_preparation(self):
        before = REAL_APPROVED.read_bytes()
        history = REAL_HISTORY.read_bytes()
        root = temp_approved(self)
        prepared, errors = publish.prepare_publication(root, APPROVED_ID)
        self.assertEqual(errors, [])
        self.assertIsNotNone(prepared)
        assert prepared is not None
        self.assertEqual(prepared["body_sha256"], "fc976ed98478e5717ff61772cb6b39786aa34027587e738a47786f27cc9ba141")
        self.assertEqual(engine.body_hash(prepared["commentary"]), prepared["body_sha256"])
        self.assertFalse((root / "published" / f"{APPROVED_ID}.md").exists())
        self.assertEqual(REAL_APPROVED.read_bytes(), before)
        self.assertEqual(REAL_HISTORY.read_bytes(), history)

    def test_b_draft_rejected(self):
        root = temp_approved(self)
        approved = root / "approved" / f"{APPROVED_ID}.md"
        draft = root / "drafts" / f"{APPROVED_ID}.md"
        draft.write_text(approved.read_text(encoding="utf-8"), encoding="utf-8")
        approved.unlink()
        prepared, errors = publish.prepare_publication(root, APPROVED_ID)
        self.assertIsNone(prepared)
        self.assertTrue(any("draft rejected" in error for error in errors))
        reopened = approved_text().replace("status: approved", "status: styled").replace("approval_status: approved", "approval_status:")
        (root / "approved").mkdir(exist_ok=True)
        approved.write_text(reopened, encoding="utf-8")
        draft.unlink()
        prepared, errors = publish.prepare_publication(root, APPROVED_ID)
        self.assertIsNone(prepared)
        self.assertTrue(any("reopened" in error for error in errors))

    def test_c_stale_review_rejected(self):
        root = temp_approved(self)
        path = root / "approved" / f"{APPROVED_ID}.md"
        path.write_text(path.read_text(encoding="utf-8").replace("review_body_sha256: fc976ed98478e5717ff61772cb6b39786aa34027587e738a47786f27cc9ba141", "review_body_sha256: " + "0" * 64), encoding="utf-8")
        prepared, errors = publish.prepare_publication(root, APPROVED_ID)
        self.assertIsNone(prepared)
        self.assertTrue(any("stale review" in error for error in errors))
        self.assertTrue(path.is_file())
        self.assertFalse((root / "published" / f"{APPROVED_ID}.md").exists())

    def test_d_stale_approval_rejected(self):
        root = temp_approved(self)
        path = root / "approved" / f"{APPROVED_ID}.md"
        path.write_text(path.read_text(encoding="utf-8").replace("approved_body_sha256: fc976ed98478e5717ff61772cb6b39786aa34027587e738a47786f27cc9ba141", "approved_body_sha256: " + "1" * 64), encoding="utf-8")
        prepared, errors = publish.prepare_publication(root, APPROVED_ID)
        self.assertIsNone(prepared)
        self.assertTrue(any("stale approval" in error for error in errors))
        edited = approved_text().replace(
            "I evaluated a synthetic retrieval checklist on a local demo workflow.",
            "I evaluated a different synthetic retrieval checklist on a local demo workflow.",
        )
        path.write_text(edited, encoding="utf-8")
        prepared, errors = publish.prepare_publication(root, APPROVED_ID)
        self.assertIsNone(prepared)
        self.assertTrue(any("externally edited" in error for error in errors))

    def test_e_oidc_failure_blocks_publish(self):
        root = temp_approved(self)
        opener = PublishOpener(userinfo_status=401, userinfo_body=b"")
        code, out, err = run_publish([APPROVED_ID], opener=opener, content_root=root, prompt_fn=lambda _prompt: TOKEN, stdin_is_tty=True, now=PUBLISHED_AT)
        self.assertEqual(code, 1)
        self.assertIn("OIDC failure blocks publish.", err)
        self.assertEqual(post_calls(opener), [])
        self.assertNotIn(publish.POSTS_URL, [url for _method, url, _headers, _body in opener.calls])
        self.assertTrue((root / "approved" / f"{APPROVED_ID}.md").is_file())
        self.assertFalse((root / "published" / f"{APPROVED_ID}.md").exists())
        self.assertNotIn(TOKEN, out + err)

    def test_f_valid_oidc_sub_creates_candidate_person_urn(self):
        state = publish.candidate_from_oidc_sub(SUBJECT)
        self.assertEqual(state["candidate_author"], "urn:li:person:" + SUBJECT)
        self.assertEqual(state["author_resolution"], publish.PERSON_URN_CANDIDATE_FROM_OIDC)
        opener = PublishOpener(posts_headers={"x-restli-id": SHARE_URN})
        root = temp_approved(self)
        code, out, err = run_publish([APPROVED_ID], opener=opener, content_root=root, prompt_fn=lambda _prompt: TOKEN, stdin_is_tty=True, now=PUBLISHED_AT)
        self.assertEqual(code, 0, err)
        sent = post_calls(opener)
        self.assertEqual([call[1] for call in sent], [publish.UGC_POSTS_URL])
        body = json.loads(sent[0][3].decode("utf-8"))
        self.assertEqual(body["author"], "urn:li:person:" + SUBJECT)

    def test_g_candidate_is_constructed_exactly_from_the_subject(self):
        for sub in (SUBJECT, "AbC_123-xyz"):
            state = publish.candidate_from_oidc_sub(sub)
            self.assertEqual(state["candidate_author"], f"urn:li:person:{sub}")

    def test_h_candidate_is_not_verified_before_successful_create(self):
        state = publish.candidate_from_oidc_sub(SUBJECT)
        self.assertFalse(state["live_verified"])
        self.assertNotEqual(state["author_resolution"], publish.PERSON_URN_LIVE_VERIFIED)
        root = temp_approved(self)
        opener = PublishOpener(
            posts_status=400,
            posts_body=json.dumps({"status": 400, "code": "INVALID_URN_ID", "message": "author urn was rejected"}).encode(),
        )
        result_out = io.StringIO()
        with redirect_stdout(result_out):
            result = publish.publish_approved_post(APPROVED_ID, root, TOKEN, opener=opener, now=PUBLISHED_AT)
        self.assertEqual(result.author_resolution, publish.PERSON_URN_CANDIDATE_FROM_OIDC)
        self.assertNotEqual(result.author_resolution, publish.PERSON_URN_LIVE_VERIFIED)
        self.assertEqual(result.outcome, "failed")
        self.assertIn(publish.PERSON_URN_CANDIDATE_FROM_OIDC, result_out.getvalue())

    def test_i_posts_api_request_shape(self):
        commentary = "Exact body.\n"
        body = publish.posts_body("urn:li:person:" + SUBJECT, commentary)
        self.assertEqual(
            list(body),
            [
                "author",
                "commentary",
                "visibility",
                "distribution",
                "lifecycleState",
                "isReshareDisabledByAuthor",
            ],
        )
        self.assertEqual(body["visibility"], "PUBLIC")
        self.assertEqual(
            body["distribution"],
            {"feedDistribution": "MAIN_FEED", "targetEntities": [], "thirdPartyDistributionChannels": []},
        )
        self.assertEqual(body["lifecycleState"], "PUBLISHED")
        self.assertIs(body["isReshareDisabledByAuthor"], False)
        self.assertNotIn("content", body)
        for banned in ("adContext", "contentLandingPage", "reshareContext", "container"):
            self.assertNotIn(banned, body)
        self.assertFalse(str(body["author"]).startswith("urn:li:organization:"))

    def test_j_exact_approved_body_becomes_commentary(self):
        root = temp_approved(self)
        prepared, errors = publish.prepare_publication(root, APPROVED_ID)
        self.assertEqual(errors, [])
        assert prepared is not None
        text = (root / "approved" / f"{APPROVED_ID}.md").read_text(encoding="utf-8")
        _fields, rest = engine.parse_frontmatter(text)
        expected = engine.normalize_body(engine.section_map(rest)["Body"])
        self.assertEqual(prepared["commentary"], expected)
        opener = PublishOpener(posts_headers={"x-restli-id": SHARE_URN})
        code, out, err = run_publish(POSTS_ARGV, opener=opener, content_root=root, prompt_fn=lambda _prompt: TOKEN, stdin_is_tty=True, now=PUBLISHED_AT)
        self.assertEqual(code, 0, err)
        self.assertEqual([call[1] for call in post_calls(opener)], [publish.POSTS_URL])
        self.assertNotIn(publish.UGC_POSTS_URL, [url for _method, url, _headers, _data in opener.calls])
        sent = json.loads(post_calls(opener)[0][3].decode("utf-8"))
        self.assertEqual(sent["commentary"], expected)
        self.assertEqual(sent["author"], "urn:li:person:" + SUBJECT)
        self.assertIn("Author resolution:\n" + publish.PERSON_URN_LIVE_VERIFIED, out)
        stored = (root / "published" / f"{APPROVED_ID}.md").read_text(encoding="utf-8")
        self.assertNotIn(TOKEN, stored)
        self.assertNotIn(SUBJECT, stored)
        self.assertNotIn("urn:li:person:", stored)

    def test_k_required_headers(self):
        opener = PublishOpener(posts_headers={"x-restli-id": SHARE_URN})
        root = temp_approved(self)
        code, _out, err = run_publish(POSTS_ARGV, opener=opener, content_root=root, prompt_fn=lambda _prompt: TOKEN, stdin_is_tty=True, now=PUBLISHED_AT)
        self.assertEqual(code, 0, err)
        self.assertEqual([call[1] for call in post_calls(opener)], [publish.POSTS_URL])
        headers = post_calls(opener)[0][2]
        self.assertEqual(headers["authorization"], f"Bearer {TOKEN}")
        self.assertEqual(headers["linkedin-version"], access.DOCUMENTED_LINKEDIN_VERSION)
        self.assertEqual(headers["x-restli-protocol-version"], "2.0.0")
        self.assertEqual(headers["content-type"], "application/json")
        self.assertEqual(access.DOCUMENTED_LINKEDIN_VERSION, "202609")
        self.assertEqual(access.VERSION_CHECKED_ON, "2026-10-05")
        self.assertIn("unredirected", opener.authorization_locations)
        self.assertNotIn("headers", opener.authorization_locations)

    def test_l_no_mutation_retry_after_network_ambiguity(self):
        root = temp_approved(self)
        opener = PublishOpener(fail_post=urllib.error.URLError("timed out"))
        code, out, err = run_publish(POSTS_ARGV, opener=opener, content_root=root, prompt_fn=lambda _prompt: TOKEN, stdin_is_tty=True, now=PUBLISHED_AT)
        self.assertEqual(code, 2)
        self.assertIn("publication outcome uncertain", err)
        self.assertEqual([call[1] for call in post_calls(opener)], [publish.POSTS_URL])
        self.assertNotIn(publish.UGC_POSTS_URL, [url for _method, url, _headers, _data in opener.calls])
        self.assertTrue((root / "approved" / f"{APPROVED_ID}.md").is_file())
        self.assertFalse((root / "published" / f"{APPROVED_ID}.md").exists())
        self.assertFalse((root / "history.jsonl").exists())
        self.assertNotIn(SUBJECT, out + err)
        self.assertNotIn("urn:li:person:", out + err)

    def _failed_status(self, status: int, body: bytes = b"") -> tuple[int, str, str, Path]:
        root = temp_approved(self)
        opener = PublishOpener(posts_status=status, posts_body=body)
        code, out, err = run_publish(POSTS_ARGV, opener=opener, content_root=root, prompt_fn=lambda _prompt: TOKEN, stdin_is_tty=True, now=PUBLISHED_AT)
        self.assertEqual([call[1] for call in post_calls(opener)], [publish.POSTS_URL])
        self.assertNotIn(publish.UGC_POSTS_URL, [url for _method, url, _headers, _data in opener.calls])
        self.assertNotIn(access.PROFILE_API_URL, [url for _method, url, _headers, _data in opener.calls])
        self.assertTrue((root / "approved" / f"{APPROVED_ID}.md").is_file())
        self.assertFalse((root / "published" / f"{APPROVED_ID}.md").exists())
        self.assertNotIn(TOKEN, out + err)
        self.assertNotIn(SUBJECT, out + err)
        self.assertNotIn("urn:li:person:", out + err)
        return code, out, err, root

    def test_m_400_leaves_artifact_approved(self):
        code, out, err, _root = self._failed_status(
            400,
            json.dumps({"status": 400, "code": "INVALID_URN_ID", "message": f"bad urn:li:person:{SUBJECT}"}).encode(),
        )
        self.assertEqual(code, 1)
        self.assertIn("HTTP:\n400", err)
        self.assertIn("remains approved", err)
        self.assertIn(publish.PERSON_URN_CANDIDATE_FROM_OIDC, err)
        self.assertIn("not marked verified", err)
        self.assertIn("INVALID_URN_ID", err)
        self.assertIn("Author:\nPERSON_URN_CANDIDATE_FROM_OIDC", out)

    def test_n_401_leaves_artifact_approved(self):
        code, _out, err, _root = self._failed_status(401, b"")
        self.assertEqual(code, 1)
        self.assertIn("HTTP:\n401", err)
        self.assertIn("invalid, expired, or revoked", err)
        self.assertIn("remains approved", err)

    def test_o_403_leaves_artifact_approved(self):
        code, _out, err, _root = self._failed_status(403, json.dumps({"status": 403, "code": "ACCESS_DENIED", "message": "denied"}).encode())
        self.assertEqual(code, 1)
        self.assertIn("HTTP:\n403", err)
        self.assertIn("Token:\nauthenticated", err)
        self.assertIn("w_member_social", err)
        self.assertIn("capability rejected", err)
        self.assertIn("UGC API:\nnot called", err)
        self.assertIn("remains approved", err)
        self.assertIn(publish.PERSON_URN_CANDIDATE_FROM_OIDC, err)

    def test_p_successful_create_marks_author_live_verified(self):
        root = temp_approved(self)
        opener = PublishOpener(posts_headers={"x-restli-id": SHARE_URN})
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            result = publish.publish_approved_post(APPROVED_ID, root, TOKEN, opener=opener, now=PUBLISHED_AT)
        self.assertEqual(result.outcome, "published")
        self.assertEqual(result.author_resolution, publish.PERSON_URN_LIVE_VERIFIED)
        self.assertEqual(result.oidc_state, access.OIDC_AUTHENTICATED)
        rendered = publish.render_publish(result, token=TOKEN, sub=SUBJECT)
        self.assertIn("Author resolution:\nPERSON_URN_LIVE_VERIFIED", rendered)
        self.assertNotIn("urn:li:person:", rendered)
        self.assertNotIn(SUBJECT, rendered)
        self.assertNotIn(TOKEN, rendered)

    def test_q_successful_create_moves_approved_to_published(self):
        before = REAL_APPROVED.read_bytes()
        root = temp_approved(self)
        opener = PublishOpener(posts_headers={"x-restli-id": SHARE_URN})
        code, _out, err = run_publish([APPROVED_ID], opener=opener, content_root=root, prompt_fn=lambda _prompt: TOKEN, stdin_is_tty=True, now=PUBLISHED_AT)
        self.assertEqual(code, 0, err)
        self.assertFalse((root / "approved" / f"{APPROVED_ID}.md").exists())
        published = root / "published" / f"{APPROVED_ID}.md"
        self.assertTrue(published.is_file())
        self.assertEqual(REAL_APPROVED.read_bytes(), before)
        fields, rest = engine.parse_frontmatter(published.read_text(encoding="utf-8"))
        self.assertEqual(fields["status"], "published")
        self.assertEqual(engine.section_map(rest)["Body"], engine.section_map(engine.parse_frontmatter(approved_text())[1])["Body"])

    def test_r_publication_hash_equals_all_previous_hashes(self):
        root = temp_approved(self)
        opener = PublishOpener(posts_headers={"x-restli-id": SHARE_URN})
        code, _out, err = run_publish([APPROVED_ID], opener=opener, content_root=root, prompt_fn=lambda _prompt: TOKEN, stdin_is_tty=True, now=PUBLISHED_AT)
        self.assertEqual(code, 0, err)
        published = root / "published" / f"{APPROVED_ID}.md"
        fields, rest = engine.parse_frontmatter(published.read_text(encoding="utf-8"))
        digest = engine.body_hash(engine.section_map(rest)["Body"])
        self.assertEqual(fields["body_sha256"], digest)
        self.assertEqual(fields["review_body_sha256"], digest)
        self.assertEqual(fields["approved_body_sha256"], digest)
        self.assertEqual(fields["published_body_sha256"], digest)
        self.assertEqual(digest, "fc976ed98478e5717ff61772cb6b39786aa34027587e738a47786f27cc9ba141")
        errors = engine.check_artifact(published, engine.load_claims(), engine.load_hooks(), root)
        self.assertEqual(errors, [])
        before = published.read_text(encoding="utf-8")
        held, hold_err = io.StringIO(), io.StringIO()
        with redirect_stdout(held), redirect_stderr(hold_err):
            code = engine.stamp(published, invalidate=True, status="styled")
        self.assertEqual(code, 1)
        self.assertIn("is published. Published artifacts are immutable.", hold_err.getvalue())
        self.assertEqual(published.read_text(encoding="utf-8"), before)

    def test_s_published_history_event(self):
        root = temp_approved(self)
        opener = PublishOpener(posts_headers={"x-restli-id": SHARE_URN})
        code, _out, err = run_publish([APPROVED_ID], opener=opener, content_root=root, prompt_fn=lambda _prompt: TOKEN, stdin_is_tty=True, now=PUBLISHED_AT)
        self.assertEqual(code, 0, err)
        lines = (root / "history.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        event = json.loads(lines[0])
        self.assertEqual(
            list(event),
            ["event", "post_id", "body_sha256", "linkedin_post_urn", "timestamp"],
        )
        self.assertEqual(event["event"], "published")
        self.assertEqual(event["post_id"], APPROVED_ID)
        self.assertEqual(event["body_sha256"], "fc976ed98478e5717ff61772cb6b39786aa34027587e738a47786f27cc9ba141")
        self.assertEqual(event["linkedin_post_urn"], SHARE_URN)
        self.assertEqual(event["timestamp"], PUBLISHED_AT)

    def test_t_token_sub_and_author_person_urn_are_not_persisted(self):
        root = temp_approved(self)
        opener = PublishOpener(posts_headers={"x-restli-id": SHARE_URN})
        code, out, err = run_publish([APPROVED_ID], opener=opener, content_root=root, prompt_fn=lambda _prompt: TOKEN, stdin_is_tty=True, now=PUBLISHED_AT)
        self.assertEqual(code, 0, err)
        self.assertEqual([call[1] for call in post_calls(opener)], [publish.UGC_POSTS_URL])
        stored = (root / "published" / f"{APPROVED_ID}.md").read_text(encoding="utf-8")
        stored += (root / "history.jsonl").read_text(encoding="utf-8")
        self.assertNotIn(TOKEN, stored)
        self.assertNotIn(SUBJECT, stored)
        self.assertNotIn("urn:li:person:", stored)
        self.assertNotIn(TOKEN, out + err)
        self.assertNotIn(SUBJECT, out + err)
        self.assertNotIn("urn:li:person:", out + err)
        self.assertIn(SHARE_URN, stored)

    def test_u_publishing_an_already_published_post_is_rejected(self):
        root = temp_approved(self)
        opener = PublishOpener(posts_headers={"x-restli-id": SHARE_URN})
        code, _out, err = run_publish([APPROVED_ID], opener=opener, content_root=root, prompt_fn=lambda _prompt: TOKEN, stdin_is_tty=True, now=PUBLISHED_AT)
        self.assertEqual(code, 0, err)
        again, out, err = run_publish([APPROVED_ID], opener=opener, content_root=root, prompt_fn=lambda _prompt: TOKEN, stdin_is_tty=True, now=PUBLISHED_AT)
        self.assertEqual(again, 1)
        self.assertIn("already published", err)
        self.assertEqual(len(post_calls(opener)), 1)
        self.assertNotIn("Post:\n", out)

    def test_v_no_scheduler_or_background_path(self):
        source = Path(publish.__file__).read_text(encoding="utf-8")
        folded = source.casefold()
        for banned in ("apscheduler", "import threading", "import asyncio", "time.sleep", "cron", "background", "publish_at", "schedule."):
            self.assertNotIn(banned, folded)
        self.assertNotIn("while ", source)
        self.assertEqual(source.count("open_fn(request, timeout=timeout)"), 1)
        self.assertEqual(source.count("send_create_once("), 2)
        self.assertEqual(publish.FIRST_LIVE_TRANSPORT, publish.UGC_SELF_SERVICE)
        skill = (ROOT / ".cursor" / "skills" / "li-publish" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("disable-model-invocation: true", skill)
        self.assertIn("No scheduler", skill)
        self.assertIn("do not retry", skill.casefold())
        self.assertIn("Transport: UGC_SELF_SERVICE", skill)
        self.assertIn("Endpoint: POST /v2/ugcPosts", skill)
        self.assertIn("does not fall back", skill.casefold())
        for name in ("li-review", "li-approve", "li-human", "li-post", "li-ideas", "li-plan", "li-reopen"):
            text = (ROOT / ".cursor" / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
            self.assertNotIn("linkedin_publish.py", text)
        engine_source = Path(engine.__file__).read_text(encoding="utf-8")
        self.assertNotIn('add_parser("publish"', engine_source)
        self.assertNotIn("linkedin_publish", engine_source)
        self.assertNotIn("import threading", engine_source)

    def test_token_argument_is_rejected(self):
        code, _out, err = run_publish([APPROVED_ID, f"--token={TOKEN}"])
        self.assertEqual(code, 1)
        self.assertIn("Do not pass the access token as an argument.", err)
        self.assertNotIn(TOKEN, err)

    def test_conflict_is_not_retried(self):
        code, _out, err, _root = self._failed_status(409, json.dumps({"status": 409, "message": "CONFLICT"}).encode())
        self.assertEqual(code, 1)
        self.assertIn("HTTP:\n409", err)
        self.assertIn("does not retry", err)
        self.assertIn("remains approved", err)

    def test_redirect_cannot_forward_the_publish_token(self):
        payload = b"{}"
        request = publish._build_post_request(payload, publish.posts_headers(TOKEN))
        self.assertNotIn("Authorization", request.headers)
        self.assertEqual(request.unredirected_hdrs.get("Authorization"), f"Bearer {TOKEN}")
        handler = access._RefuseRedirect()
        with self.assertRaises(urllib.error.HTTPError):
            handler.redirect_request(request, io.BytesIO(b""), 302, "Found", {}, "https://evil.example/posts")


def publish_kwargs(root: Path, opener: PublishOpener) -> dict:
    return {
        "opener": opener,
        "content_root": root,
        "prompt_fn": lambda _prompt: TOKEN,
        "stdin_is_tty": True,
        "now": PUBLISHED_AT,
    }


def mutating_urls(opener: PublishOpener) -> list[str]:
    return [url for method, url, _headers, _body in opener.calls if method == "POST"]


class UgcSelfServiceTests(unittest.TestCase):
    def test_first_live_transport_uses_exactly_ugc_posts(self):
        self.assertEqual(publish.FIRST_LIVE_TRANSPORT, publish.UGC_SELF_SERVICE)
        root = temp_approved(self)
        opener = PublishOpener(posts_headers={"x-restli-id": UGC_URN})
        code, out, err = run_publish([APPROVED_ID], **publish_kwargs(root, opener))
        self.assertEqual(code, 0, err)
        self.assertEqual(mutating_urls(opener), [publish.UGC_POSTS_URL])
        self.assertEqual(mutating_urls(opener), ["https://api.linkedin.com/v2/ugcPosts"])
        self.assertNotIn(publish.POSTS_URL, [url for _method, url, _headers, _body in opener.calls])
        self.assertIn("Transport:\nUGC_SELF_SERVICE", out)
        self.assertIn("Endpoint:\nPOST /v2/ugcPosts", out)
        self.assertNotIn("urn:li:person:", out + err)
        self.assertNotIn(SUBJECT, out + err)
        self.assertNotIn(TOKEN, out + err)

    def test_request_body_matches_share_on_linkedin_text_schema(self):
        text = "Exact body.\n"
        author = "urn:li:person:" + SUBJECT
        body = publish.ugc_self_service_body(author, text)
        self.assertEqual(list(body), ["author", "lifecycleState", "specificContent", "visibility"])
        share = body["specificContent"]["com.linkedin.ugc.ShareContent"]
        self.assertEqual(list(share), ["shareCommentary", "shareMediaCategory"])
        self.assertEqual(share["shareCommentary"], {"text": text})
        self.assertEqual(share["shareMediaCategory"], "NONE")
        self.assertNotIn("media", share)
        self.assertEqual(body["visibility"], {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"})
        self.assertEqual(body["lifecycleState"], "PUBLISHED")
        self.assertNotIn("commentary", body)
        self.assertNotIn("distribution", body)
        headers = publish.ugc_headers(TOKEN)
        self.assertEqual(headers["X-Restli-Protocol-Version"], "2.0.0")
        self.assertNotIn("Linkedin-Version", headers)
        root = temp_approved(self)
        opener = PublishOpener(posts_headers={"x-restli-id": UGC_URN})
        code, _out, err = run_publish([APPROVED_ID], **publish_kwargs(root, opener))
        self.assertEqual(code, 0, err)
        sent_headers = post_calls(opener)[0][2]
        self.assertEqual(sent_headers["authorization"], f"Bearer {TOKEN}")
        self.assertEqual(sent_headers["x-restli-protocol-version"], "2.0.0")
        self.assertEqual(sent_headers["content-type"], "application/json")
        self.assertNotIn("linkedin-version", sent_headers)
        request = publish._create_request(publish.UGC_SELF_SERVICE, author, text, TOKEN)
        self.assertNotIn("Authorization", request.headers)
        self.assertNotIn("Linkedin-Version", request.headers)
        self.assertEqual(request.unredirected_hdrs.get("Authorization"), f"Bearer {TOKEN}")

    def test_exact_approved_body_becomes_share_commentary_text(self):
        root = temp_approved(self)
        prepared, errors = publish.prepare_publication(root, APPROVED_ID)
        self.assertEqual(errors, [])
        assert prepared is not None
        text = (root / "approved" / f"{APPROVED_ID}.md").read_text(encoding="utf-8")
        _fields, rest = engine.parse_frontmatter(text)
        expected = engine.normalize_body(engine.section_map(rest)["Body"])
        opener = PublishOpener(posts_headers={"x-restli-id": UGC_URN})
        code, _out, err = run_publish([APPROVED_ID], **publish_kwargs(root, opener))
        self.assertEqual(code, 0, err)
        sent = json.loads(post_calls(opener)[0][3].decode("utf-8"))
        commentary = sent["specificContent"]["com.linkedin.ugc.ShareContent"]["shareCommentary"]["text"]
        self.assertEqual(commentary, expected)
        self.assertEqual(commentary, prepared["commentary"])
        self.assertEqual(engine.body_hash(commentary), prepared["body_sha256"])

    def test_author_is_person_urn_from_the_oidc_subject(self):
        root = temp_approved(self)
        opener = PublishOpener(posts_headers={"x-restli-id": UGC_URN})
        code, out, err = run_publish([APPROVED_ID], **publish_kwargs(root, opener))
        self.assertEqual(code, 0, err)
        sent = json.loads(post_calls(opener)[0][3].decode("utf-8"))
        self.assertEqual(sent["author"], "urn:li:person:" + SUBJECT)
        self.assertNotIn("urn:li:person:", out + err)

    def test_author_remains_candidate_before_http_success(self):
        state = publish.candidate_from_oidc_sub(SUBJECT)
        self.assertEqual(state["author_resolution"], publish.PERSON_URN_CANDIDATE_FROM_OIDC)
        self.assertFalse(state["live_verified"])
        root = temp_approved(self)
        opener = PublishOpener(
            posts_status=400,
            posts_body=json.dumps({"status": 400, "code": "INVALID_URN_ID", "message": "author urn was rejected"}).encode(),
        )
        code, out, err = run_publish([APPROVED_ID], **publish_kwargs(root, opener))
        self.assertEqual(code, 1)
        self.assertIn("Author:\nPERSON_URN_CANDIDATE_FROM_OIDC", out)
        self.assertNotIn(publish.PERSON_URN_LIVE_VERIFIED, out + err)
        self.assertIn(publish.PERSON_URN_CANDIDATE_FROM_OIDC, err)
        self.assertIn("not marked verified", err)
        self.assertIn("INVALID_URN_ID", err)
        self.assertNotIn(SUBJECT, out + err)
        self.assertNotIn("urn:li:person:", out + err)
        self.assertTrue((root / "approved" / f"{APPROVED_ID}.md").is_file())
        self.assertEqual(publish.candidate_from_oidc_sub(SUBJECT)["live_verified"], False)

    def test_http_201_marks_runtime_author_live_verified(self):
        root = temp_approved(self)
        opener = PublishOpener(posts_headers={"X-RestLi-Id": UGC_URN})
        code, out, err = run_publish([APPROVED_ID], **publish_kwargs(root, opener))
        self.assertEqual(code, 0, err)
        self.assertIn("Author:\nPERSON_URN_CANDIDATE_FROM_OIDC", out)
        self.assertIn("Author resolution:\nPERSON_URN_LIVE_VERIFIED", out)
        published = root / "published" / f"{APPROVED_ID}.md"
        fields, rest = engine.parse_frontmatter(published.read_text(encoding="utf-8"))
        digest = engine.body_hash(engine.section_map(rest)["Body"])
        self.assertEqual(fields["linkedin_post_urn"], UGC_URN)
        self.assertEqual(fields["published_body_sha256"], digest)
        self.assertEqual(fields["body_sha256"], digest)
        self.assertEqual(fields["review_body_sha256"], digest)
        self.assertEqual(fields["approved_body_sha256"], digest)
        self.assertNotIn(publish.PERSON_URN_LIVE_VERIFIED, published.read_text(encoding="utf-8"))
        self.assertNotIn(SUBJECT, published.read_text(encoding="utf-8"))
        self.assertNotIn("urn:li:person:", published.read_text(encoding="utf-8"))
        self.assertFalse(state_was_persisted(root))
        again = publish.candidate_from_oidc_sub(SUBJECT)
        self.assertEqual(again["author_resolution"], publish.PERSON_URN_CANDIDATE_FROM_OIDC)
        self.assertFalse(again["live_verified"])

    def test_ugc_failure_does_not_call_posts_api(self):
        for status in (400, 401, 403, 409, 500):
            root = temp_approved(self)
            opener = PublishOpener(posts_status=status, posts_body=b"{}")
            code, _out, err = run_publish([APPROVED_ID], **publish_kwargs(root, opener))
            self.assertNotEqual(code, 0)
            self.assertEqual(mutating_urls(opener), [publish.UGC_POSTS_URL])
            self.assertNotIn(publish.POSTS_URL, [url for _method, url, _headers, _body in opener.calls])
            self.assertTrue((root / "approved" / f"{APPROVED_ID}.md").is_file())
            self.assertIn("remains approved", err)

    def test_ugc_403_reports_share_on_linkedin_capability_denied(self):
        root = temp_approved(self)
        opener = PublishOpener(posts_status=403, posts_body=json.dumps({"status": 403, "message": "denied"}).encode())
        code, _out, err = run_publish([APPROVED_ID], **publish_kwargs(root, opener))
        self.assertEqual(code, 1)
        self.assertIn("HTTP:\n403", err)
        self.assertIn("Share on LinkedIn capability denied", err)
        self.assertIn("Posts API:\nnot called", err)
        self.assertIn(publish.PERSON_URN_CANDIDATE_FROM_OIDC, err)
        self.assertEqual(mutating_urls(opener), [publish.UGC_POSTS_URL])

    def test_posts_api_failure_does_not_call_ugc(self):
        for status in (400, 401, 403, 409, 500):
            root = temp_approved(self)
            opener = PublishOpener(posts_status=status, posts_body=b"{}")
            code, _out, err = run_publish(POSTS_ARGV, **publish_kwargs(root, opener))
            self.assertNotEqual(code, 0)
            self.assertEqual(mutating_urls(opener), [publish.POSTS_URL])
            self.assertNotIn(publish.UGC_POSTS_URL, [url for _method, url, _headers, _body in opener.calls])
            self.assertTrue((root / "approved" / f"{APPROVED_ID}.md").is_file())

    def test_one_invocation_sends_at_most_one_mutating_request(self):
        cases = (
            ([APPROVED_ID], {"posts_headers": {"x-restli-id": UGC_URN}}, publish.UGC_POSTS_URL),
            (POSTS_ARGV, {"posts_headers": {"x-restli-id": SHARE_URN}}, publish.POSTS_URL),
            ([APPROVED_ID], {"posts_status": 400, "posts_body": b"{}"}, publish.UGC_POSTS_URL),
            (POSTS_ARGV, {"posts_status": 400, "posts_body": b"{}"}, publish.POSTS_URL),
            ([APPROVED_ID], {"fail_post": urllib.error.URLError("timed out")}, publish.UGC_POSTS_URL),
            (POSTS_ARGV, {"fail_post": TimeoutError("timed out")}, publish.POSTS_URL),
        )
        for argv, opener_kwargs, expected in cases:
            root = temp_approved(self)
            opener = PublishOpener(**opener_kwargs)
            run_publish(argv, **publish_kwargs(root, opener))
            self.assertEqual(mutating_urls(opener), [expected])

    def test_ugc_timeout_and_conflict_are_not_retried(self):
        root = temp_approved(self)
        opener = PublishOpener(fail_post=TimeoutError("timed out"))
        code, _out, err = run_publish([APPROVED_ID], **publish_kwargs(root, opener))
        self.assertEqual(code, 2)
        self.assertIn("publication outcome uncertain", err)
        self.assertIn("does not retry", err)
        self.assertEqual(mutating_urls(opener), [publish.UGC_POSTS_URL])
        self.assertTrue((root / "approved" / f"{APPROVED_ID}.md").is_file())
        self.assertFalse((root / "published" / f"{APPROVED_ID}.md").exists())
        self.assertFalse((root / "history.jsonl").exists())

        root = temp_approved(self)
        opener = PublishOpener(posts_status=409, posts_body=b"{}")
        code, _out, err = run_publish([APPROVED_ID], **publish_kwargs(root, opener))
        self.assertEqual(code, 1)
        self.assertIn("HTTP:\n409", err)
        self.assertIn("does not retry", err)
        self.assertIn("Posts API:\nnot called", err)
        self.assertEqual(mutating_urls(opener), [publish.UGC_POSTS_URL])
        self.assertTrue((root / "approved" / f"{APPROVED_ID}.md").is_file())

    def test_unknown_transport_sends_nothing(self):
        root = temp_approved(self)
        opener = PublishOpener()
        result = publish.publish_approved_post(
            APPROVED_ID,
            root,
            TOKEN,
            opener=opener,
            now=PUBLISHED_AT,
            transport="BOTH",
        )
        self.assertEqual(result.outcome, "refused")
        self.assertEqual(opener.calls, [])
        self.assertTrue((root / "approved" / f"{APPROVED_ID}.md").is_file())

    def test_no_credentials_or_member_identifier_persisted(self):
        root = temp_approved(self)
        opener = PublishOpener(posts_headers={"x-restli-id": UGC_URN})
        code, out, err = run_publish([APPROVED_ID], **publish_kwargs(root, opener))
        self.assertEqual(code, 0, err)
        stored = (root / "published" / f"{APPROVED_ID}.md").read_text(encoding="utf-8")
        stored += (root / "history.jsonl").read_text(encoding="utf-8")
        self.assertNotIn(TOKEN, stored)
        self.assertNotIn(SUBJECT, stored)
        self.assertNotIn("urn:li:person:", stored)
        self.assertNotIn(TOKEN, out + err)
        self.assertNotIn(SUBJECT, out + err)
        self.assertNotIn("urn:li:person:", out + err)
        self.assertIn(UGC_URN, stored)


def write_env(test: unittest.TestCase, text: str) -> Path:
    folder = tempfile.TemporaryDirectory()
    test.addCleanup(folder.cleanup)
    path = Path(folder.name) / ".env"
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def absent_env(test: unittest.TestCase) -> Path:
    folder = tempfile.TemporaryDirectory()
    test.addCleanup(folder.cleanup)
    return Path(folder.name) / ".env"


def assert_output_hides_token(test: unittest.TestCase, token: str, out: str, err: str) -> None:
    if token in out or token in err:
        test.fail("access token appeared in command output")


def forbid_prompt(_prompt: str) -> str:
    raise AssertionError("token prompt was used")


def bears(opener: PublishOpener, secret: str) -> bool:
    matched = False
    for _method, _url, headers, _body in opener.calls:
        auth = headers.get("authorization")
        if not auth:
            continue
        matched = True
        if auth != "Bearer " + secret:
            return False
    return matched


def assert_not_persisted(test: unittest.TestCase, root: Path, secret: str) -> None:
    published = root / "published" / f"{APPROVED_ID}.md"
    history = root / "history.jsonl"
    blob = ""
    if published.is_file():
        blob += published.read_text(encoding="utf-8")
    if history.is_file():
        blob += history.read_text(encoding="utf-8")
    if secret in blob:
        test.fail("access token was written to an artifact")


class PublishTokenResolutionTests(unittest.TestCase):
    def setUp(self):
        self._approved = REAL_APPROVED.read_bytes()
        self._history = REAL_HISTORY.read_bytes()

    def tearDown(self):
        self.assertEqual(REAL_APPROVED.read_bytes(), self._approved)
        self.assertEqual(REAL_HISTORY.read_bytes(), self._history)

    def test_token_from_process_environment(self):
        secret = "fake-process-token"
        root = temp_approved(self)
        opener = PublishOpener(posts_headers={"x-restli-id": UGC_URN})
        code, out, err = run_publish(
            [APPROVED_ID],
            opener=opener,
            content_root=root,
            prompt_fn=forbid_prompt,
            stdin_is_tty=False,
            now=PUBLISHED_AT,
            environ={access.TOKEN_ENV_NAME: secret},
            env_file=absent_env(self),
        )
        self.assertEqual(code, 0)
        self.assertTrue(bears(opener, secret))
        assert_output_hides_token(self, secret, out, err)
        assert_not_persisted(self, root, secret)

    def test_token_from_dotenv(self):
        secret = "fake-file-token"
        root = temp_approved(self)
        opener = PublishOpener(posts_headers={"x-restli-id": UGC_URN})
        before = os.environ.get(access.TOKEN_ENV_NAME)
        code, out, err = run_publish(
            [APPROVED_ID],
            opener=opener,
            content_root=root,
            prompt_fn=forbid_prompt,
            stdin_is_tty=False,
            now=PUBLISHED_AT,
            environ={},
            env_file=write_env(self, 'LINKEDIN_ACCESS_TOKEN="fake-file-token"\n'),
        )
        self.assertEqual(code, 0)
        self.assertTrue(bears(opener, secret))
        self.assertTrue(os.environ.get(access.TOKEN_ENV_NAME) is before)
        assert_output_hides_token(self, secret, out, err)
        assert_not_persisted(self, root, secret)

    def test_process_environment_overrides_dotenv(self):
        process = "fake-process-token"
        file_token = "fake-file-token"
        root = temp_approved(self)
        opener = PublishOpener(posts_headers={"x-restli-id": UGC_URN})
        code, out, err = run_publish(
            [APPROVED_ID],
            opener=opener,
            content_root=root,
            prompt_fn=forbid_prompt,
            stdin_is_tty=False,
            now=PUBLISHED_AT,
            environ={access.TOKEN_ENV_NAME: process},
            env_file=write_env(self, "LINKEDIN_ACCESS_TOKEN=fake-file-token\n"),
        )
        self.assertEqual(code, 0)
        self.assertTrue(bears(opener, process))
        self.assertFalse(bears(opener, file_token))
        assert_output_hides_token(self, process, out, err)
        assert_output_hides_token(self, file_token, out, err)
        assert_not_persisted(self, root, process)
        assert_not_persisted(self, root, file_token)

    def test_missing_both_falls_back_to_getpass(self):
        root = temp_approved(self)
        opener = PublishOpener(posts_headers={"x-restli-id": UGC_URN})
        secret = "fake-getpass-token"
        with mock.patch("linkedin_access.getpass.getpass", return_value=secret) as ask:
            code, out, err = run_publish(
                [APPROVED_ID],
                opener=opener,
                content_root=root,
                stdin_is_tty=True,
                now=PUBLISHED_AT,
                environ={},
                env_file=absent_env(self),
            )
        ask.assert_called_once_with("LinkedIn access token: ")
        self.assertEqual(code, 0)
        self.assertTrue(bears(opener, secret))
        assert_output_hides_token(self, secret, out, err)
        assert_not_persisted(self, root, secret)

    def test_token_never_appears_in_stdout_or_stderr(self):
        secret = "fake-stream-token"
        root = temp_approved(self)
        opener = PublishOpener(fail_post=OSError("Authorization: Bearer " + secret))
        code, out, err = run_publish(
            [APPROVED_ID],
            opener=opener,
            content_root=root,
            prompt_fn=forbid_prompt,
            stdin_is_tty=False,
            now=PUBLISHED_AT,
            environ={},
            env_file=write_env(self, "LINKEDIN_ACCESS_TOKEN=fake-stream-token\n"),
        )
        self.assertNotEqual(code, 0)
        assert_output_hides_token(self, secret, out, err)
        self.assertNotIn("Bearer", out + err)
        assert_not_persisted(self, root, secret)
        self.assertTrue((root / "approved" / f"{APPROVED_ID}.md").is_file())


def state_was_persisted(root: Path) -> bool:
    published = (root / "published" / f"{APPROVED_ID}.md").read_text(encoding="utf-8")
    history = (root / "history.jsonl").read_text(encoding="utf-8") if (root / "history.jsonl").is_file() else ""
    blob = published + history
    return publish.PERSON_URN_CANDIDATE_FROM_OIDC in blob or "urn:li:person:" in blob


if __name__ == "__main__":
    unittest.main()
