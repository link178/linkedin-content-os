"""P4.1 tests. HTTP is mocked. These tests do not call LinkedIn."""

from __future__ import annotations

import inspect
import io
import json
import os
import tempfile
import unittest
import urllib.error
import urllib.parse
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import linkedin_access as access

ROOT = Path(__file__).resolve().parent.parent
APPROVED_ID = "POST-20260927-01"
APPROVED_FIXTURE = ROOT / "scripts" / "fixtures" / f"{APPROVED_ID}.approved.md"
REAL_APPROVED = APPROVED_FIXTURE
REAL_HISTORY = ROOT / "content" / "history.jsonl"
TOKEN = "fixture-token"
SUBJECT = "example-subject"
PROFILE_ID = "profile-id"
OIDC_SUB = "fixture-sub"


def assert_person_urn_comes_only_from_profile_id(test: unittest.TestCase) -> None:
    source = Path(access.__file__).read_text(encoding="utf-8")
    test.assertEqual(source.count("urn:li:person:"), 1)
    test.assertIn('_PERSON_URN_PREFIX = "urn:li:person:"', source)
    test.assertIn("return _PERSON_URN_PREFIX + profile_id", source)
    test.assertNotIn("urn:li:person:", inspect.getsource(access.resolve_author))
    author_source = inspect.getsource(access.resolve_person_author)
    test.assertNotIn("urn:li:person:", author_source)
    test.assertIn("del oidc_subject", author_source)
    test.assertNotRegex(source, r"urn:li:person:\{")
    test.assertNotIn('f"urn:li:person:', source)
    test.assertNotIn("f'urn:li:person:", source)
    test.assertNotIn("_PERSON_URN_PREFIX + oidc_subject", source)
    test.assertNotIn("_PERSON_URN_PREFIX + subject", source)


class FakeResponse:
    def __init__(self, status: int, body: bytes):
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body

    def getcode(self) -> int:
        return self.status

    def close(self) -> None:
        return None


class FakeOpener:
    def __init__(self, routes: dict[str, tuple]):
        self.routes = routes
        self.calls: list[tuple[str, str, dict]] = []
        self.bodies: list[bytes | None] = []
        self.header_names: list[list[str]] = []
        self.redirect_copies: list[dict] = []

    def __call__(self, request, timeout=None):
        method = request.get_method()
        header_items = list(request.header_items())
        headers = {key.lower(): value for key, value in header_items}
        self.calls.append((method, request.full_url, headers))
        self.bodies.append(request.data)
        self.header_names.append([key for key, _value in header_items])
        copied = {
            key: value
            for key, value in request.headers.items()
            if key.lower() not in {"content-length", "content-type"}
        }
        self.redirect_copies.append(copied)
        if method != "GET":
            raise AssertionError(method)
        route = self.routes[request.full_url]
        status, body = route[0], route[1]
        response_headers = route[2] if len(route) > 2 else None
        if status >= 400:
            raise urllib.error.HTTPError(
                request.full_url, status, "error", response_headers, io.BytesIO(body)
            )
        return FakeResponse(status, body)


def discovery_body() -> bytes:
    return json.dumps({"userinfo_endpoint": access.USERINFO_URL, "subject_types_supported": ["pairwise"]}).encode()


def userinfo_body() -> bytes:
    return json.dumps(
        {
            "sub": SUBJECT,
            "name": "Example Member",
            "given_name": "Example",
            "family_name": "Member",
            "picture": "https://example.test/picture",
            "locale": "en-US",
        }
    ).encode()


def success_opener() -> FakeOpener:
    return FakeOpener(
        {
            access.DISCOVERY_URL: (200, discovery_body()),
            access.USERINFO_URL: (200, userinfo_body()),
        }
    )


def run_cli(argv: list[str], **kwargs):
    # Keep the developer process token and repository .env out of these tests.
    isolated = tempfile.TemporaryDirectory() if "env_file" not in kwargs else None
    if "environ" not in kwargs:
        kwargs["environ"] = {}
    if isolated is not None:
        kwargs["env_file"] = Path(isolated.name) / ".env"
    stdout, stderr = io.StringIO(), io.StringIO()
    try:
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = access.main(argv, **kwargs)
    finally:
        if isolated is not None:
            isolated.cleanup()
    return code, stdout.getvalue(), stderr.getvalue()


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


def temp_content(test: unittest.TestCase) -> tuple[Path, str]:
    folder = tempfile.TemporaryDirectory()
    test.addCleanup(folder.cleanup)
    root = Path(folder.name)
    (root / "approved").mkdir()
    (root / "drafts").mkdir()
    return root, REAL_APPROVED.read_text(encoding="utf-8")


def approved_content_root(test: unittest.TestCase) -> Path:
    root, text = temp_content(test)
    (root / "approved" / f"{APPROVED_ID}.md").write_text(text, encoding="utf-8", newline="\n")
    return root


class ApprovedBoundaryTests(unittest.TestCase):
    def test_a_approved_artifact_accepted_by_dry_run(self):
        before = REAL_APPROVED.read_bytes()
        history = REAL_HISTORY.read_bytes()
        cli_root = approved_content_root(self)
        code, out, err = run_cli(
            ["dry-run", APPROVED_ID],
            content_root=cli_root,
            stdin_is_tty=False,
        )
        self.assertEqual(code, 0, err)
        self.assertIn("Approved:\nyes", out)
        self.assertIn("Review:\nPASS", out)
        self.assertIn("fc976ed98478e5717ff61772cb6b39786aa34027587e738a47786f27cc9ba141", out)
        self.assertIn("I evaluated a synthetic retrieval checklist on a local demo workflow.", out)
        self.assertEqual(REAL_APPROVED.read_bytes(), before)
        self.assertEqual(REAL_HISTORY.read_bytes(), history)
        root, text = temp_content(self)
        (root / "approved" / f"{APPROVED_ID}.md").write_text(text, encoding="utf-8", newline="\n")
        result = access.dry_run(APPROVED_ID, root)
        self.assertTrue(result.valid)
        self.assertEqual(result.body_sha256, "fc976ed98478e5717ff61772cb6b39786aa34027587e738a47786f27cc9ba141")
        self.assertEqual(access.engine.body_hash(result.commentary), result.body_sha256)
        self.assertFalse((root / "history.jsonl").exists())

    def test_b_draft_rejected(self):
        root, text = temp_content(self)
        draft_id = "POST-20260924-01"
        (root / "drafts" / f"{draft_id}.md").write_text(text, encoding="utf-8", newline="\n")
        code, out, err = run_cli(["dry-run", draft_id], content_root=root, stdin_is_tty=False)
        self.assertEqual(code, 1)
        self.assertIn("draft rejected", err)
        self.assertNotIn("Ready for live publication:\nYES", out + err)
        self.assertNotIn("Commentary:", out)

    def test_b_reopened_artifact_rejected(self):
        root, text = temp_content(self)
        reopened = text.replace("status: approved", "status: styled", 1)
        reopened = reopened.replace("review_verdict: PASS", "review_verdict:")
        reopened = reopened.replace("approval_status: approved", "approval_status:")
        (root / "drafts" / f"{APPROVED_ID}.md").write_text(reopened, encoding="utf-8", newline="\n")
        code, _out, err = run_cli(["dry-run", APPROVED_ID], content_root=root, stdin_is_tty=False)
        self.assertEqual(code, 1)
        self.assertIn("Reopened posts are not accepted", err)

    def test_c_stale_approved_artifact_rejected(self):
        root, text = temp_content(self)
        stale = text.replace(
            "and it increased friction for the tested use case.",
            "and it increased drag for the tested use case.",
            1,
        )
        path = root / "approved" / f"{APPROVED_ID}.md"
        path.write_text(stale, encoding="utf-8", newline="\n")
        before = path.read_bytes()
        code, out, err = run_cli(["dry-run", APPROVED_ID], content_root=root, stdin_is_tty=False)
        self.assertEqual(code, 1)
        self.assertIn("stale approved artifact", err)
        self.assertNotIn("Ready for live publication:\nYES", out + err)
        self.assertEqual(path.read_bytes(), before)


class IdentityProbeTests(unittest.TestCase):
    def test_d_identity_success(self):
        opener = success_opener()
        code, out, err = run_cli(["identity"], prompt_fn=lambda _prompt: TOKEN, opener=opener, stdin_is_tty=False)
        self.assertEqual(code, 0, err)
        self.assertIn("OIDC authentication:\nOIDC_AUTHENTICATED", out)
        self.assertIn("Name: Example Member", out)
        self.assertIn(f"OIDC subject: {SUBJECT}", out)
        self.assertIn("Author resolution:\nPERSON_URN_UNRESOLVED", out)
        self.assertNotIn(TOKEN, out)
        self.assertNotIn(TOKEN, err)
        self.assertEqual([call[0] for call in opener.calls], ["GET", "GET"])
        self.assertEqual([call[1] for call in opener.calls], [access.DISCOVERY_URL, access.USERINFO_URL])
        self.assertNotIn("authorization", opener.calls[0][2])
        self.assertEqual(opener.calls[1][2].get("authorization"), f"Bearer {TOKEN}")
        self.assertNotIn("?", opener.calls[1][1])
        self.assertEqual(opener.bodies, [None, None])
        self.assertNotIn("content-type", opener.calls[1][2])
        self.assertNotIn("x-restli-protocol-version", opener.calls[1][2])
        self.assertNotIn("linkedin-version", opener.calls[1][2])
        self.assertNotIn(TOKEN, json.dumps(opener.redirect_copies))

    def test_e_missing_access_token(self):
        code, out, err = run_cli(["identity"], stdin_is_tty=False)
        self.assertEqual(code, 1)
        self.assertIn("No access token was provided.", err)
        self.assertNotIn("Traceback", err)
        self.assertEqual(out, "")
        code, _out, err = run_cli(["identity"], prompt_fn=lambda _prompt: "  ", stdin_is_tty=True)
        self.assertEqual(code, 1)
        self.assertIn("No access token was provided.", err)

    def test_f_invalid_token_401(self):
        opener = FakeOpener(
            {
                access.DISCOVERY_URL: (200, discovery_body()),
                access.USERINFO_URL: (401, b""),
            }
        )
        code, out, err = run_cli(["identity"], prompt_fn=lambda _prompt: TOKEN, opener=opener, stdin_is_tty=True)
        self.assertEqual(code, 1)
        self.assertIn("invalid, expired, or revoked", err)
        self.assertNotIn(TOKEN, out)
        self.assertNotIn(TOKEN, err)
        self.assertNotIn("Bearer", err)

        def leaky(_request, timeout=None):
            raise OSError(f"Authorization: Bearer {TOKEN}")

        with self.assertRaises(access.NetworkFailure) as caught:
            access.probe_identity(TOKEN, opener=leaky)
        self.assertNotIn(TOKEN, str(caught.exception))

    def test_g_insufficient_access_403(self):
        opener = FakeOpener(
            {
                access.DISCOVERY_URL: (200, discovery_body()),
                access.USERINFO_URL: (403, b'{"message":"Not enough permissions"}'),
            }
        )
        code, out, err = run_cli(["identity"], prompt_fn=lambda _prompt: TOKEN, opener=opener, stdin_is_tty=True)
        self.assertEqual(code, 1)
        self.assertIn("Insufficient scope or access", err)
        self.assertIn("openid", err)
        self.assertIn("profile", err)
        self.assertIn("w_member_social", err)
        self.assertNotIn(TOKEN, out + err)
        self.assertNotIn("Not enough permissions", err)

    def test_h_malformed_userinfo(self):
        for body in (b"not-json", b"[]", b'{"name":"No Subject"}', b'{"sub":""}', b'{"sub":"ok","name":1}'):
            opener = FakeOpener(
                {
                    access.DISCOVERY_URL: (200, discovery_body()),
                    access.USERINFO_URL: (200, body),
                }
            )
            with self.assertRaises(access.MalformedIdentity):
                access.probe_identity(TOKEN, opener=opener)
        wrong = FakeOpener({access.DISCOVERY_URL: (200, b'{"userinfo_endpoint":"https://example.test/userinfo"}')})
        with self.assertRaises(access.MalformedIdentity):
            access.probe_identity(TOKEN, opener=wrong)
        self.assertEqual(len(wrong.calls), 1)

    def test_i_oidc_sub_is_not_a_person_urn(self):
        resolved = access.resolve_author(authenticated=True, oidc_subject=SUBJECT)
        self.assertEqual(resolved["author_resolution"], access.PERSON_URN_UNRESOLVED)
        self.assertIsNone(resolved["person_urn"])
        self.assertNotIn(f"urn:li:person:{SUBJECT}", json.dumps(resolved))
        assert_person_urn_comes_only_from_profile_id(self)
        opener = success_opener()
        root, text = temp_content(self)
        (root / "approved" / f"{APPROVED_ID}.md").write_text(text, encoding="utf-8", newline="\n")
        result = access.dry_run(APPROVED_ID, root, token=TOKEN, opener=opener)
        rendered = access.render_dry_run(result)
        preview = json.dumps(result.preview)
        self.assertNotIn(f"urn:li:person:{SUBJECT}", rendered)
        self.assertNotIn(f"urn:li:person:{SUBJECT}", preview)
        self.assertEqual(result.preview["body"]["author"], "UNRESOLVED")
        with self.assertRaises(access.LinkedInAccessError):
            access.IdentityResult(
                authenticated=True,
                state=access.OIDC_AUTHENTICATED,
                author_resolution=access.PERSON_URN_VERIFIED,
            )

    def test_j_dry_run_unresolved_person_urn_not_ready(self):
        opener = success_opener()
        root, text = temp_content(self)
        (root / "approved" / f"{APPROVED_ID}.md").write_text(text, encoding="utf-8", newline="\n")
        code, out, err = run_cli(
            ["dry-run", APPROVED_ID],
            content_root=root,
            opener=opener,
            prompt_fn=lambda _prompt: TOKEN,
            stdin_is_tty=True,
        )
        self.assertEqual(code, 0, err)
        self.assertIn("Authenticated member:\nyes", out)
        self.assertIn("Author resolution:\nPERSON_URN_UNRESOLVED", out)
        self.assertIn("Ready for live publication:\nNO", out)
        self.assertIn("Person URN has not been verified.", out)
        self.assertNotIn(TOKEN, out + err)
        self.assertFalse(access.publication_ready())

    def test_k_no_mutating_http_method(self):
        def explode(request, timeout=None):
            raise AssertionError(f"network {request.get_method()} {request.full_url}")

        with mock.patch("linkedin_access.urllib.request.urlopen", side_effect=AssertionError("urlopen")) as opened:
            for method in ("POST", "PUT", "PATCH", "DELETE"):
                with self.assertRaises(access.LinkedInMutationBlocked):
                    access.linkedin_http(method, "https://api.linkedin.com/rest/posts", {"Authorization": f"Bearer {TOKEN}"})
                with self.assertRaises(access.LinkedInMutationBlocked):
                    access.linkedin_http(method, access.USERINFO_URL, opener=explode)
            opened.assert_not_called()
        with self.assertRaises(access.LinkedInAccessError):
            access.linkedin_http("GET", "https://api.linkedin.com/rest/posts", opener=explode)
        with self.assertRaises(access.LinkedInAccessError):
            access.linkedin_http("GET", "https://api.linkedin.com/v2/ugcPosts", opener=explode)
        opener = success_opener()
        root, text = temp_content(self)
        (root / "approved" / f"{APPROVED_ID}.md").write_text(text, encoding="utf-8", newline="\n")
        access.dry_run(APPROVED_ID, root, token=TOKEN, opener=opener)
        self.assertTrue(opener.calls)
        self.assertTrue(all(method == "GET" for method, _url, _headers in opener.calls))
        self.assertNotIn("https://api.linkedin.com/rest/posts", [url for _method, url, _headers in opener.calls])
        skill = ROOT / ".cursor" / "skills" / "li-publish" / "SKILL.md"
        self.assertIn("disable-model-invocation: true", skill.read_text(encoding="utf-8"))


class StatusAndFailureTests(unittest.TestCase):
    def test_status_reports_the_p4_1_boundary(self):
        code, out, err = run_cli(["status"])
        self.assertEqual(code, 0, err)
        self.assertIn("OAuth/token access bootstrap: implemented", out)
        self.assertIn("OIDC identity: implemented", out)
        self.assertIn("Person URN resolution: unresolved", out)
        self.assertIn("Dry-run: implemented", out)
        self.assertIn("Live publishing: explicit /li-publish only", out)
        self.assertIn("First-live transport: UGC_SELF_SERVICE", out)
        self.assertIn("ugcPosts production path: selected by the explicit publish command", out)
        self.assertIn("Posts API production path: implemented, used only when --transport POSTS_API is explicit", out)
        self.assertNotIn("ugcPosts production path: not selected", out)

    def test_network_failure_keeps_the_local_dry_run(self):
        def down(_request, timeout=None):
            raise urllib.error.URLError("timed out")

        cli_root = approved_content_root(self)
        code, out, err = run_cli(
            ["dry-run", APPROVED_ID],
            content_root=cli_root,
            opener=down,
            prompt_fn=lambda _prompt: TOKEN,
            stdin_is_tty=True,
        )
        self.assertEqual(code, 0, err)
        self.assertIn("Approved:\nyes", out)
        self.assertIn("Authenticated member:\nno", out)
        self.assertIn("Identity is unverified", out)
        self.assertIn("Ready for live publication:\nNO", out)
        self.assertNotIn(TOKEN, out + err)

    def test_token_argument_is_rejected(self):
        code, _out, err = run_cli(["identity", f"--token={TOKEN}"])
        self.assertEqual(code, 1)
        self.assertIn("Do not pass the access token as an argument.", err)
        self.assertNotIn(TOKEN, err)


class UserInfoDiagnosticTests(unittest.TestCase):
    def test_userinfo_400_json_surfaces_safe_fields_only(self):
        secret = "fix/token+secret="
        quoted = urllib.parse.quote(secret, safe="")
        body = json.dumps(
            {
                "status": 400,
                "serviceErrorCode": 65601,
                "code": "INVALID_REQUEST",
                "message": f"Rejected Authorization: Bearer {secret} encoded {quoted}",
                "request": {
                    "headers": {"Authorization": f"Bearer {secret}"},
                    "client_secret": "portal-secret",
                },
                "stackTrace": secret,
            }
        ).encode()
        opener = FakeOpener(
            {
                access.DISCOVERY_URL: (200, discovery_body()),
                access.USERINFO_URL: (400, body, {"Content-Type": "application/json"}),
            }
        )
        code, out, err = run_cli(["identity"], prompt_fn=lambda _prompt: secret, opener=opener, stdin_is_tty=True)
        self.assertEqual(code, 1)
        self.assertIn("UserInfo returned HTTP 400:", err)
        self.assertIn("INVALID_REQUEST", err)
        self.assertIn("serviceErrorCode=65601", err)
        self.assertIn("status=400", err)
        self.assertNotIn(secret, out + err)
        self.assertNotIn(quoted, out + err)
        self.assertNotIn("portal-secret", out + err)
        self.assertNotIn("stackTrace", out + err)
        self.assertNotIn("client_secret=portal-secret", out + err)
        self.assertNotIn("Bearer", out + err)
        userinfo_calls = [call for call in opener.calls if call[1] == access.USERINFO_URL]
        self.assertEqual(len(userinfo_calls), 1)
        self.assertNotIn("x-restli-protocol-version", userinfo_calls[0][2])
        self.assertNotIn("linkedin-version", userinfo_calls[0][2])
        self.assertEqual(userinfo_calls[0][2].get("authorization"), f"Bearer {secret}")
        self.assertEqual(userinfo_calls[0][2].get("accept"), "application/json")
        self.assertTrue(all(method == "GET" for method, _url, _headers in opener.calls))
        self.assertTrue(all(body is None for body in opener.bodies))
        self.assertTrue(all("content-type" not in headers for _method, _url, headers in opener.calls))
        self.assertTrue(all("linkedin-version" not in headers for _method, _url, headers in opener.calls))
        self.assertNotIn(secret, json.dumps(opener.redirect_copies))
        self.assertTrue(all("?" not in url for _method, url, _headers in opener.calls))

    def test_userinfo_400_non_json_is_sanitized(self):
        page = f"<html>Authorization: Bearer {TOKEN} client_secret=portal-secret</html>".encode()
        opener = FakeOpener(
            {
                access.DISCOVERY_URL: (200, discovery_body()),
                access.USERINFO_URL: (400, page, {"Content-Type": "text/html; charset=utf-8"}),
            }
        )
        code, out, err = run_cli(["identity"], prompt_fn=lambda _prompt: TOKEN, opener=opener, stdin_is_tty=True)
        self.assertEqual(code, 1)
        self.assertIn("UserInfo returned HTTP 400: Content-Type: text/html;", err)
        self.assertNotIn(TOKEN, out + err)
        self.assertNotIn("portal-secret", out + err)
        self.assertNotIn("Bearer", out + err)
        self.assertIn("[redacted]", err)

    def test_auth_failures_do_not_retry_or_echo_bodies(self):
        for status in (401, 403):
            opener = FakeOpener(
                {
                    access.DISCOVERY_URL: (200, discovery_body()),
                    access.USERINFO_URL: (
                        status,
                        json.dumps({"message": f"Authorization: Bearer {TOKEN}", "status": status}).encode(),
                    ),
                }
            )
            code, out, err = run_cli(["identity"], prompt_fn=lambda _prompt: TOKEN, opener=opener, stdin_is_tty=True)
            self.assertEqual(code, 1)
            self.assertNotIn(TOKEN, out + err)
            self.assertEqual(len([call for call in opener.calls if call[1] == access.USERINFO_URL]), 1)

    def test_redirect_cannot_forward_the_access_token(self):
        request = access._build_get_request(
            access.USERINFO_URL,
            {"Authorization": f"Bearer {TOKEN}", "Accept": "application/json"},
        )
        self.assertIsNone(request.data)
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.full_url, access.USERINFO_URL)
        self.assertNotIn("Authorization", request.headers)
        self.assertEqual(request.unredirected_hdrs.get("Authorization"), f"Bearer {TOKEN}")
        handler = access._RefuseRedirect()
        with self.assertRaises(urllib.error.HTTPError) as caught:
            handler.redirect_request(
                request,
                io.BytesIO(b""),
                302,
                "Found",
                None,
                "https://evil.example/collect",
            )
        self.assertEqual(caught.exception.code, 302)
        self.assertEqual(caught.exception.url, access.USERINFO_URL)
        self.assertNotIn(TOKEN, str(caught.exception))

        captured = {}

        def fake_build(*handlers):
            captured["handlers"] = handlers

            class Dummy:
                def open(self, _request, timeout=None):
                    return FakeResponse(200, discovery_body())

            return Dummy()

        with mock.patch("linkedin_access.urllib.request.build_opener", side_effect=fake_build):
            status, body, _content_type = access.linkedin_http("GET", access.DISCOVERY_URL)
        self.assertEqual(status, 200)
        self.assertTrue(body)
        self.assertEqual(len(captured["handlers"]), 1)
        self.assertIsInstance(captured["handlers"][0], access._RefuseRedirect)

    def test_content_type_and_mutating_methods_are_refused(self):
        def explode(request, timeout=None):
            raise AssertionError(f"network {request.get_method()} {request.full_url}")

        with self.assertRaises(access.LinkedInAccessError):
            access.linkedin_http(
                "GET",
                access.USERINFO_URL,
                {"Content-Type": "application/json", "Authorization": f"Bearer {TOKEN}"},
                opener=explode,
            )
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            with self.assertRaises(access.LinkedInMutationBlocked):
                access.linkedin_http(method, access.USERINFO_URL, opener=explode)

    def test_dry_run_hides_a_token_echoed_by_linkedin(self):
        body = json.dumps(
            {
                "status": 400,
                "message": f"request header Authorization: Bearer {TOKEN}",
                "request": {"Authorization": f"Bearer {TOKEN}"},
            }
        ).encode()
        opener = FakeOpener(
            {
                access.DISCOVERY_URL: (200, discovery_body()),
                access.USERINFO_URL: (400, body, {"Content-Type": "application/json"}),
            }
        )
        root, text = temp_content(self)
        (root / "approved" / f"{APPROVED_ID}.md").write_text(text, encoding="utf-8", newline="\n")
        result = access.dry_run(APPROVED_ID, root, token=TOKEN, opener=opener)
        rendered = access.render_dry_run(result)
        self.assertIn("UserInfo returned HTTP 400:", rendered)
        self.assertNotIn(TOKEN, rendered)
        self.assertFalse(result.ready)
        self.assertTrue(all(method == "GET" for method, _url, _headers in opener.calls))
        self.assertEqual(len([call for call in opener.calls if call[1] == access.USERINFO_URL]), 1)


class UserInfoLocaleTests(unittest.TestCase):
    def _parsed(self, payload: dict) -> dict[str, str]:
        return access.parse_userinfo(json.dumps(payload).encode())

    def test_a_string_locale_accepted(self):
        parsed = self._parsed({"sub": "fixture-sub", "locale": "en-US"})
        self.assertEqual(parsed["locale"], "en-US")
        self.assertEqual(parsed["sub"], "fixture-sub")

    def test_b_object_locale_with_country_normalized(self):
        parsed = self._parsed({"sub": "fixture-sub", "locale": {"language": "en", "country": "US"}})
        self.assertEqual(parsed["locale"], "en-US")

    def test_c_object_locale_language_only(self):
        parsed = self._parsed({"sub": "fixture-sub", "locale": {"language": "en"}})
        self.assertEqual(parsed["locale"], "en")

    def test_d_malformed_locale_rejected(self):
        profiles = (
            {"sub": "fixture-sub", "locale": {}},
            {"sub": "fixture-sub", "locale": {"country": "US"}},
            {"sub": "fixture-sub", "locale": {"language": 123}},
            {"sub": "fixture-sub", "locale": {"language": "en", "country": 123}},
            {"sub": "fixture-sub", "locale": {"language": "", "country": "US"}},
            {"sub": "fixture-sub", "locale": ["en", "US"]},
        )
        for payload in profiles:
            with self.assertRaises(access.MalformedIdentity) as caught:
                self._parsed(payload)
            self.assertEqual(str(caught.exception), "UserInfo response was malformed. Identity is unresolved.")
            self.assertNotIn("fixture-sub", str(caught.exception))

    def test_e_live_shaped_fixture_authenticates_without_a_person_urn(self):
        body = json.dumps(
            {
                "name": "Fixture User",
                "sub": "fixture-sub",
                "locale": {"language": "es", "country": "ES"},
                "given_name": "Fixture",
                "family_name": "User",
                "picture": "https://example.invalid/picture",
            }
        ).encode()
        opener = FakeOpener(
            {
                access.DISCOVERY_URL: (200, discovery_body()),
                access.USERINFO_URL: (200, body, {"Content-Type": "application/json"}),
            }
        )
        code, out, err = run_cli(["identity"], prompt_fn=lambda _prompt: TOKEN, opener=opener, stdin_is_tty=True)
        self.assertEqual(code, 0, err)
        self.assertIn("OIDC authentication:\nOIDC_AUTHENTICATED", out)
        self.assertIn("Author resolution:\nPERSON_URN_UNRESOLVED", out)
        self.assertIn("OIDC subject: fixture-sub", out)
        self.assertIn("locale", out)
        self.assertNotIn("urn:li:person:fixture-sub", out + err)
        self.assertNotIn(TOKEN, out + err)
        parsed = access.parse_userinfo(body)
        self.assertEqual(parsed["locale"], "es-ES")
        self.assertEqual(parsed["sub"], "fixture-sub")
        self.assertEqual(
            [key for key in access.PROFILE_FIELDS if key in parsed],
            ["sub", "name", "given_name", "family_name", "picture", "locale"],
        )

    def test_f_sub_is_never_a_person_urn(self):
        parsed = self._parsed(
            {"sub": "fixture-sub", "locale": {"language": "es", "country": "ES"}}
        )
        rendered = json.dumps(parsed)
        self.assertNotIn("urn:li:person:", rendered)
        assert_person_urn_comes_only_from_profile_id(self)
        author = access.resolve_author(authenticated=True, oidc_subject=parsed["sub"])
        self.assertEqual(author["author_resolution"], access.PERSON_URN_UNRESOLVED)
        self.assertIsNone(author["person_urn"])

    def test_g_one_userinfo_get(self):
        opener = success_opener()
        result = access.probe_identity(TOKEN, opener=opener)
        self.assertEqual(result.state, access.OIDC_AUTHENTICATED)
        self.assertEqual(result.author_resolution, access.PERSON_URN_UNRESOLVED)
        userinfo_calls = [call for call in opener.calls if call[1] == access.USERINFO_URL]
        self.assertEqual(len(userinfo_calls), 1)
        self.assertEqual(userinfo_calls[0][0], "GET")
        self.assertIsNone(opener.bodies[opener.calls.index(userinfo_calls[0])])
        headers = userinfo_calls[0][2]
        self.assertEqual(headers.get("authorization"), f"Bearer {TOKEN}")
        self.assertEqual(headers.get("accept"), "application/json")
        self.assertNotIn("content-type", headers)
        self.assertNotIn("x-restli-protocol-version", headers)
        self.assertNotIn("linkedin-version", headers)

    def test_identity_fields_stay_strict(self):
        with self.assertRaises(access.MalformedIdentity):
            self._parsed({"sub": "", "locale": "en-US"})
        with self.assertRaises(access.MalformedIdentity):
            self._parsed({"sub": "fixture-sub", "name": 1, "locale": {"language": "en", "country": "US"}})
        with self.assertRaises(access.MalformedIdentity):
            self._parsed({"locale": {"language": "es", "country": "ES"}})


class ProfileProbeTests(unittest.TestCase):
    def _opener(self, status: int, body: bytes, headers: dict | None = None) -> FakeOpener:
        route = (status, body) if headers is None else (status, body, headers)
        return FakeOpener({access.PROFILE_API_URL: route})

    def test_a_valid_id_verifies_person_urn_from_profile_api(self):
        body = json.dumps({"id": PROFILE_ID, "localizedFirstName": "Hidden"}).encode()
        opener = self._opener(200, body, {"Content-Type": "application/json"})
        code, out, err = run_cli(
            ["profile-probe"],
            prompt_fn=lambda _prompt: TOKEN,
            opener=opener,
            stdin_is_tty=False,
        )
        self.assertEqual(code, 0, err)
        self.assertIn("Profile API:\navailable", out)
        self.assertIn("Person ID:\nverified", out)
        self.assertIn("Person URN:\nverified", out)
        self.assertIn("Author resolution:\nPERSON_URN_VERIFIED", out)
        self.assertIn("PROFILE_ID_VERIFIED", out)
        self.assertNotIn(PROFILE_ID, out + err)
        self.assertNotIn("Hidden", out + err)
        self.assertNotIn("urn:li:person:", out + err)
        self.assertNotIn(TOKEN, out + err)
        self.assertNotIn("Bearer", out + err)
        self.assertEqual([(method, url) for method, url, _headers in opener.calls], [("GET", access.PROFILE_API_URL)])
        self.assertNotIn("?", opener.calls[0][1])
        self.assertIsNone(opener.bodies[0])
        self.assertEqual(opener.calls[0][2].get("authorization"), f"Bearer {TOKEN}")
        self.assertEqual(opener.calls[0][2].get("accept"), "application/json")
        self.assertNotIn("content-type", opener.calls[0][2])
        self.assertNotIn("linkedin-version", opener.calls[0][2])
        self.assertNotIn("x-restli-protocol-version", opener.calls[0][2])
        self.assertNotIn(TOKEN, json.dumps(opener.redirect_copies))
        result = access.probe_profile(TOKEN, opener=self._opener(200, body), oidc_subject=OIDC_SUB)
        self.assertEqual(result.profile_id, PROFILE_ID)
        self.assertEqual(result.person_urn, "urn:li:person:" + PROFILE_ID)
        self.assertNotEqual(result.person_urn, "urn:li:person:" + OIDC_SUB)
        self.assertEqual(result.author_resolution, access.PERSON_URN_VERIFIED)
        self.assertTrue(result.person_id_verified)

    def test_b_missing_id_stays_unresolved(self):
        for body in (b"{}", b'{"firstName":"Hidden"}'):
            opener = self._opener(200, body)
            result = access.probe_profile(TOKEN, opener=opener, oidc_subject=OIDC_SUB)
            rendered = access.render_profile_probe(result)
            self.assertEqual(result.author_resolution, access.PERSON_URN_UNRESOLVED)
            self.assertIsNone(result.person_urn)
            self.assertIsNone(result.profile_id)
            self.assertFalse(result.person_id_verified)
            self.assertIn("Author resolution:\nPERSON_URN_UNRESOLVED", rendered)
            self.assertNotIn("PROFILE_ID_VERIFIED", rendered)
            self.assertNotIn("Hidden", rendered)
            self.assertNotIn(OIDC_SUB, rendered)
            self.assertNotIn("urn:li:person:", rendered)

    def test_c_empty_id_stays_unresolved(self):
        for body in (b'{"id":""}', b'{"id":"   "}'):
            result = access.probe_profile(TOKEN, opener=self._opener(200, body), oidc_subject=OIDC_SUB)
            rendered = access.render_profile_probe(result)
            self.assertEqual(result.author_resolution, access.PERSON_URN_UNRESOLVED)
            self.assertIsNone(result.person_urn)
            self.assertFalse(result.person_id_verified)
            self.assertIn("Person ID:\nunverified", rendered)
            self.assertNotIn("urn:li:person:", rendered)

    def test_d_non_string_id_stays_unresolved(self):
        bodies = (
            b'{"id":123}',
            b'{"id":null}',
            b'{"id":["profile-id"]}',
            b'{"id":true}',
        )
        for body in bodies:
            result = access.probe_profile(TOKEN, opener=self._opener(200, body), oidc_subject=OIDC_SUB)
            rendered = access.render_profile_probe(result)
            self.assertEqual(result.author_resolution, access.PERSON_URN_UNRESOLVED)
            self.assertIsNone(result.person_urn)
            self.assertIsNone(result.profile_id)
            self.assertNotIn("urn:li:person:", rendered)
            self.assertNotIn("profile-id", rendered)

    def test_e_forbidden_is_profile_access_not_authentication_failure(self):
        opener = self._opener(403, json.dumps({"message": f"Authorization: Bearer {TOKEN}"}).encode())
        code, out, err = run_cli(
            ["profile-probe"],
            prompt_fn=lambda _prompt: TOKEN,
            opener=opener,
            stdin_is_tty=True,
        )
        self.assertEqual(code, 0, err)
        self.assertIn("Profile API:\nunavailable", out)
        self.assertIn("HTTP:\n403", out)
        self.assertIn("openid profile w_member_social", out)
        self.assertIn("Person URN:\nunresolved", out)
        self.assertIn("Author resolution:\nPERSON_URN_UNRESOLVED", out)
        self.assertIn(access.PROFILE_API_DENIED_REASON, out)
        self.assertNotIn("invalid, expired, or revoked", out + err)
        self.assertNotIn("not_authenticated", out + err)
        self.assertNotIn("Insufficient scope or access", out + err)
        self.assertNotIn("PROFILE_ID_VERIFIED", out + err)
        self.assertNotIn("PERSON_URN_VERIFIED", out + err)
        self.assertNotIn(TOKEN, out + err)
        self.assertNotIn("Bearer", out + err)
        self.assertEqual(len(opener.calls), 1)
        self.assertEqual(opener.calls[0][0], "GET")

    def test_f_unauthorized_reports_revoked_token_without_leaking_it(self):
        opener = self._opener(
            401,
            json.dumps({"message": f"Authorization: Bearer {TOKEN}", "status": 401}).encode(),
            {"Content-Type": "application/json"},
        )
        code, out, err = run_cli(
            ["profile-probe"],
            prompt_fn=lambda _prompt: TOKEN,
            opener=opener,
            stdin_is_tty=True,
        )
        self.assertEqual(code, 1)
        self.assertIn("invalid, expired, or revoked", err)
        self.assertNotIn(TOKEN, out + err)
        self.assertNotIn("Bearer", out + err)
        self.assertNotIn("PERSON_URN_VERIFIED", out + err)
        self.assertNotIn("urn:li:person:", out + err)
        self.assertEqual(len(opener.calls), 1)

    def test_g_other_errors_are_sanitized_diagnostics(self):
        secret = "fix/token+secret="
        quoted = urllib.parse.quote(secret, safe="")
        body = json.dumps(
            {
                "status": 400,
                "serviceErrorCode": 65601,
                "code": "INVALID_REQUEST",
                "message": f"Rejected Authorization: Bearer {secret} encoded {quoted}",
                "request": {"client_secret": "portal-secret"},
                "stackTrace": secret,
                "id": PROFILE_ID,
            }
        ).encode()
        opener = self._opener(400, body, {"Content-Type": "application/json"})
        code, out, err = run_cli(
            ["profile-probe"],
            prompt_fn=lambda _prompt: secret,
            opener=opener,
            stdin_is_tty=True,
        )
        self.assertEqual(code, 1)
        self.assertIn("Profile API returned HTTP 400:", err)
        self.assertIn("INVALID_REQUEST", err)
        self.assertIn("serviceErrorCode=65601", err)
        self.assertNotIn(secret, out + err)
        self.assertNotIn(quoted, out + err)
        self.assertNotIn("portal-secret", out + err)
        self.assertNotIn("stackTrace", out + err)
        self.assertNotIn(PROFILE_ID, out + err)
        self.assertNotIn("urn:li:person:", out + err)
        self.assertNotIn("Bearer", out + err)
        missing = self._opener(404, b"")
        with self.assertRaises(access.MalformedIdentity) as caught:
            access.probe_profile(TOKEN, opener=missing, oidc_subject=OIDC_SUB)
        self.assertIn("Profile API returned HTTP 404", str(caught.exception))
        self.assertNotIn(TOKEN, str(caught.exception))
        self.assertNotIn(OIDC_SUB, str(caught.exception))
        self.assertNotIn("urn:li:person:", str(caught.exception))
        malformed = self._opener(200, b"not-json")
        with self.assertRaises(access.MalformedIdentity) as caught:
            access.probe_profile(TOKEN, opener=malformed, oidc_subject=OIDC_SUB)
        self.assertIn("malformed", str(caught.exception))
        self.assertNotIn(TOKEN, str(caught.exception))
        self.assertNotIn("urn:li:person:", str(caught.exception))

    def test_h_oidc_sub_does_not_produce_a_person_urn_when_profile_is_unavailable(self):
        opener = self._opener(403, b"{}")
        result = access.probe_profile(TOKEN, opener=opener, oidc_subject=OIDC_SUB)
        rendered = access.render_profile_probe(result)
        resolved = access.resolve_person_author(profile_id=None, oidc_subject=OIDC_SUB)
        self.assertIsNone(result.person_urn)
        self.assertIsNone(result.profile_id)
        self.assertEqual(result.author_resolution, access.PERSON_URN_UNRESOLVED)
        self.assertIsNone(resolved["person_urn"])
        self.assertNotIn(OIDC_SUB, rendered)
        self.assertNotIn(OIDC_SUB, json.dumps(resolved))
        self.assertNotIn("urn:li:person:", rendered)
        self.assertNotIn("urn:li:person:", json.dumps(resolved))
        author = access.resolve_author(authenticated=True, oidc_subject=OIDC_SUB)
        self.assertIsNone(author["person_urn"])
        self.assertEqual(author["author_resolution"], access.PERSON_URN_UNRESOLVED)

    def test_i_profile_id_is_the_only_person_urn_source(self):
        result = access.probe_profile(
            TOKEN,
            opener=self._opener(200, json.dumps({"id": PROFILE_ID}).encode()),
            oidc_subject=OIDC_SUB,
        )
        self.assertEqual(result.profile_id, PROFILE_ID)
        self.assertEqual(result.person_urn, "urn:li:person:" + PROFILE_ID)
        self.assertNotEqual(result.person_urn, "urn:li:person:" + OIDC_SUB)
        self.assertEqual(result.author_resolution, access.PERSON_URN_VERIFIED)
        assert_person_urn_comes_only_from_profile_id(self)
        self.assertNotIn("urn:li:person:", inspect.getsource(access.resolve_author))
        self.assertNotIn(PROFILE_ID, access.render_profile_probe(result))

    def test_j_runtime_profile_id_is_not_written(self):
        before = {path: path.read_bytes() for path in (ROOT / "content").rglob("*") if path.is_file()}
        history = REAL_HISTORY.read_bytes()
        opener = self._opener(200, json.dumps({"id": PROFILE_ID, "vanityName": "hidden-name"}).encode())
        writes: list[str] = []
        real_open = open

        def guard(file, mode="r", *args, **kwargs):
            if any(flag in str(mode) for flag in ("w", "a", "x", "+")):
                writes.append(str(file))
                raise AssertionError(f"profile probe wrote {file}")
            return real_open(file, mode, *args, **kwargs)

        with mock.patch("builtins.open", guard):
            result = access.probe_profile(TOKEN, opener=opener, oidc_subject=OIDC_SUB)
            rendered = access.render_profile_probe(result)
        self.assertEqual(writes, [])
        self.assertEqual(result.person_urn, "urn:li:person:" + PROFILE_ID)
        self.assertNotIn(PROFILE_ID, rendered)
        self.assertNotIn("hidden-name", rendered)
        self.assertNotIn("urn:li:person:", rendered)
        after = {path: path.read_bytes() for path in (ROOT / "content").rglob("*") if path.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(REAL_HISTORY.read_bytes(), history)
        self.assertNotIn(PROFILE_ID.encode(), history)

    def test_k_probe_never_mutates(self):
        called: list[str] = []

        def explode(request, timeout=None):
            called.append(request.get_method())
            raise AssertionError(f"network {request.get_method()} {request.full_url}")

        for method in ("POST", "PUT", "PATCH", "DELETE"):
            with self.assertRaises(access.LinkedInMutationBlocked):
                access.linkedin_http(method, access.PROFILE_API_URL, opener=explode)
            with self.assertRaises(access.LinkedInMutationBlocked):
                access.linkedin_http(method, "https://api.linkedin.com/rest/posts", opener=explode)
            with self.assertRaises(access.LinkedInMutationBlocked):
                access.linkedin_http(method, "https://api.linkedin.com/v2/ugcPosts", opener=explode)
        self.assertEqual(called, [])
        with self.assertRaises(access.LinkedInAccessError):
            access.linkedin_http("GET", access.PROFILE_API_URL + "?projection=(id)", opener=explode)
        with self.assertRaises(access.LinkedInAccessError):
            access.linkedin_http("GET", "https://api.linkedin.com/rest/posts", opener=explode)
        opener = self._opener(200, json.dumps({"id": PROFILE_ID}).encode())
        access.probe_profile(TOKEN, opener=opener)
        self.assertEqual([method for method, _url, _headers in opener.calls], ["GET"])
        self.assertEqual([url for _method, url, _headers in opener.calls], [access.PROFILE_API_URL])
        self.assertTrue(all(body is None for body in opener.bodies))
        self.assertFalse(access.publication_ready())
        skill = ROOT / ".cursor" / "skills" / "li-publish" / "SKILL.md"
        self.assertIn("disable-model-invocation: true", skill.read_text(encoding="utf-8"))

    def test_status_does_not_call_linkedin_or_persist_identity(self):
        def explode(request, timeout=None):
            raise AssertionError(f"network {request.full_url}")

        code, out, err = run_cli(["status"], opener=explode)
        self.assertEqual(code, 0, err)
        self.assertIn("OIDC: unverified", out)
        self.assertIn("Profile API capability: unverified", out)
        self.assertIn("Person URN: unresolved", out)
        self.assertIn("Publishing: explicit single-post command", out)
        self.assertIn("Live publishing: explicit /li-publish only", out)
        self.assertIn("Person URN resolution: unresolved", out)

    def test_token_is_not_accepted_as_an_argument(self):
        code, out, err = run_cli(["profile-probe"], stdin_is_tty=False)
        self.assertEqual(code, 1)
        self.assertIn("No access token was provided.", err)
        self.assertEqual(out, "")
        code, out, err = run_cli(["profile-probe", f"--token={TOKEN}"])
        self.assertEqual(code, 1)
        self.assertIn("Do not pass the access token as an argument.", err)
        self.assertNotIn(TOKEN, out + err)

    def test_redirect_cannot_forward_the_profile_token(self):
        request = access._build_get_request(
            access.PROFILE_API_URL,
            {"Authorization": f"Bearer {TOKEN}", "Accept": "application/json"},
        )
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.full_url, access.PROFILE_API_URL)
        self.assertIsNone(request.data)
        self.assertNotIn("Authorization", request.headers)
        self.assertEqual(request.unredirected_hdrs.get("Authorization"), f"Bearer {TOKEN}")
        handler = access._RefuseRedirect()
        with self.assertRaises(urllib.error.HTTPError) as caught:
            handler.redirect_request(
                request,
                io.BytesIO(b""),
                302,
                "Found",
                None,
                "https://evil.example/collect",
            )
        self.assertEqual(caught.exception.code, 302)
        self.assertNotIn(TOKEN, str(caught.exception))


def uses_bearer(calls, secret: str) -> bool:
    matched = False
    for call in calls:
        auth = call[2].get("authorization")
        if not auth:
            continue
        matched = True
        if auth != "Bearer " + secret:
            return False
    return matched


class TokenResolutionTests(unittest.TestCase):
    def test_token_from_process_environment(self):
        secret = "fake-process-token"
        env_file = absent_env(self)
        before = os.environ.get(access.TOKEN_ENV_NAME)
        with mock.patch.dict(os.environ, {access.TOKEN_ENV_NAME: secret}):
            token = access.configured_access_token(env_file=env_file)
        self.assertEqual(token, secret)
        self.assertTrue(os.environ.get(access.TOKEN_ENV_NAME) is before)
        opener = success_opener()
        code, out, err = run_cli(
            ["identity"],
            opener=opener,
            stdin_is_tty=False,
            environ={access.TOKEN_ENV_NAME: secret},
            env_file=env_file,
            prompt_fn=forbid_prompt,
        )
        self.assertEqual(code, 0)
        self.assertTrue(uses_bearer(opener.calls, secret))
        assert_output_hides_token(self, secret, out, err)

    def test_token_from_dotenv(self):
        secret = "fake-file-token"
        env_file = write_env(self, "LINKEDIN_ACCESS_TOKEN=fake-file-token\n")
        before = os.environ.get(access.TOKEN_ENV_NAME)
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            with mock.patch.object(access, "default_env_file", return_value=env_file):
                token = access.configured_access_token(environ={})
        self.assertEqual(token, secret)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")
        self.assertTrue(os.environ.get(access.TOKEN_ENV_NAME) is before)
        opener = success_opener()
        approved_before = REAL_APPROVED.read_bytes()
        history_before = REAL_HISTORY.read_bytes()
        cli_root = approved_content_root(self)
        code, out, err = run_cli(
            ["dry-run", APPROVED_ID],
            content_root=cli_root,
            opener=opener,
            stdin_is_tty=False,
            environ={},
            env_file=env_file,
            prompt_fn=forbid_prompt,
        )
        self.assertEqual(code, 0)
        self.assertTrue(uses_bearer(opener.calls, secret))
        assert_output_hides_token(self, secret, out, err)
        self.assertEqual(REAL_APPROVED.read_bytes(), approved_before)
        self.assertEqual(REAL_HISTORY.read_bytes(), history_before)

    def test_process_environment_overrides_dotenv(self):
        process = "fake-process-token"
        file_token = "fake-file-token"
        env_file = write_env(self, "LINKEDIN_ACCESS_TOKEN=fake-file-token\n")
        token = access.configured_access_token(
            environ={access.TOKEN_ENV_NAME: process},
            env_file=env_file,
        )
        self.assertEqual(token, process)
        blank = access.configured_access_token(
            environ={access.TOKEN_ENV_NAME: "   "},
            env_file=env_file,
        )
        self.assertEqual(blank, file_token)
        opener = FakeOpener({access.PROFILE_API_URL: (403, b"{}")})
        code, out, err = run_cli(
            ["profile-probe"],
            opener=opener,
            stdin_is_tty=False,
            environ={access.TOKEN_ENV_NAME: process},
            env_file=env_file,
            prompt_fn=forbid_prompt,
        )
        self.assertEqual(code, 0)
        self.assertIn("Profile API:\nunavailable", out)
        assert_output_hides_token(self, process, out, err)
        assert_output_hides_token(self, file_token, out, err)
        self.assertTrue(uses_bearer(opener.calls, process))

    def test_missing_both_falls_back_to_getpass(self):
        env_file = absent_env(self)
        opener = success_opener()
        with mock.patch("linkedin_access.getpass.getpass", return_value="fake-getpass-token") as ask:
            token = access.read_access_token(environ={}, env_file=env_file)
            code, out, err = run_cli(
                ["identity"],
                opener=opener,
                stdin_is_tty=True,
                environ={},
                env_file=env_file,
            )
        self.assertEqual(token, "fake-getpass-token")
        ask.assert_called_with("LinkedIn access token: ")
        self.assertEqual(ask.call_count, 2)
        self.assertEqual(code, 0)
        self.assertTrue(uses_bearer(opener.calls, "fake-getpass-token"))
        assert_output_hides_token(self, "fake-getpass-token", out, err)

    def test_quoted_dotenv_value(self):
        secret = "fake-quoted-token"
        for raw in ('"fake-quoted-token"', "'fake-quoted-token'", '  "fake-quoted-token"  '):
            env_file = write_env(self, f"LINKEDIN_ACCESS_TOKEN={raw}\n")
            token = access.configured_access_token(environ={}, env_file=env_file)
            self.assertEqual(token, secret)
        env_file = write_env(self, 'LINKEDIN_ACCESS_TOKEN="fake-quoted-token"\n')
        opener = success_opener()
        code, out, err = run_cli(
            ["identity"],
            opener=opener,
            stdin_is_tty=False,
            environ={},
            env_file=env_file,
            prompt_fn=forbid_prompt,
        )
        self.assertEqual(code, 0)
        self.assertTrue(uses_bearer(opener.calls, secret))
        assert_output_hides_token(self, secret, out, err)

    def test_comments_and_blank_lines_are_ignored(self):
        text = "\r\n".join(
            [
                "",
                "# LINKEDIN_ACCESS_TOKEN=fake-comment-token",
                "   ",
                "   # another comment",
                "LINKEDIN_ACCESS_TOKEN=fake-file-token",
                "",
            ]
        )
        env_file = write_env(self, text)
        token = access.configured_access_token(environ={}, env_file=env_file)
        self.assertEqual(token, "fake-file-token")
        self.assertNotIn("fake-comment-token", token)

    def test_malformed_lines_are_not_executed(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        canary = root / "executed.txt"
        assignment = "__import__('pathlib').Path(" + repr(str(canary)) + ").write_text('executed')"
        text = "\n".join(
            [
                assignment,
                "print('executed')",
                "$(echo executed)",
                "`echo executed`",
                "os.system('echo executed')",
                "DANGER=" + assignment,
                "OTHER=fake-$HOME_SENTINEL",
                "export LINKEDIN_ACCESS_TOKEN=fake-export-token",
                "LINKEDIN_ACCESS_TOKEN=fake-$(echo leak)",
            ]
        ) + "\n"
        env_file = root / ".env"
        env_file.write_text(text, encoding="utf-8", newline="\n")
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            parsed = access.parse_dotenv(text)
            token = access.configured_access_token(environ={}, env_file=env_file)
        self.assertFalse(canary.exists())
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(token, "fake-$(echo leak)")
        self.assertEqual(parsed["OTHER"], "fake-$HOME_SENTINEL")
        self.assertEqual(parsed["DANGER"], assignment)
        self.assertNotIn("executed", token)
        exported = access.parse_dotenv('export LINKEDIN_ACCESS_TOKEN="fake-export-token"\n')
        self.assertEqual(exported[access.TOKEN_ENV_NAME], "fake-export-token")

    def test_token_never_appears_in_stdout_or_stderr(self):
        secret = "fake-stream-token"
        env = {access.TOKEN_ENV_NAME: secret}
        env_file = write_env(self, "LINKEDIN_ACCESS_TOKEN=fake-file-token\n")
        identity = success_opener()
        code, out, err = run_cli(
            ["identity"],
            opener=identity,
            stdin_is_tty=False,
            environ=env,
            env_file=env_file,
            prompt_fn=forbid_prompt,
        )
        self.assertEqual(code, 0)
        self.assertTrue(uses_bearer(identity.calls, secret))
        assert_output_hides_token(self, secret, out, err)
        assert_output_hides_token(self, "fake-file-token", out, err)

        profile = FakeOpener({access.PROFILE_API_URL: (200, json.dumps({"id": PROFILE_ID}).encode())})
        code, out, err = run_cli(
            ["profile-probe"],
            opener=profile,
            stdin_is_tty=False,
            environ=env,
            env_file=env_file,
            prompt_fn=forbid_prompt,
        )
        self.assertEqual(code, 0)
        self.assertTrue(uses_bearer(profile.calls, secret))
        assert_output_hides_token(self, secret, out, err)
        self.assertNotIn(PROFILE_ID, out + err)

        dry = success_opener()
        approved_before = REAL_APPROVED.read_bytes()
        history_before = REAL_HISTORY.read_bytes()
        cli_root = approved_content_root(self)
        code, out, err = run_cli(
            ["dry-run", APPROVED_ID],
            content_root=cli_root,
            opener=dry,
            stdin_is_tty=False,
            environ=env,
            env_file=env_file,
            prompt_fn=forbid_prompt,
        )
        self.assertEqual(code, 0)
        self.assertEqual(REAL_APPROVED.read_bytes(), approved_before)
        self.assertEqual(REAL_HISTORY.read_bytes(), history_before)
        self.assertTrue(uses_bearer(dry.calls, secret))
        assert_output_hides_token(self, secret, out, err)

        def leaky(_request, timeout=None):
            raise OSError("Authorization: Bearer " + secret)

        code, out, err = run_cli(
            ["identity"],
            opener=leaky,
            stdin_is_tty=False,
            environ={},
            env_file=write_env(self, "LINKEDIN_ACCESS_TOKEN=fake-stream-token\n"),
            prompt_fn=forbid_prompt,
        )
        self.assertEqual(code, 1)
        self.assertIn("could not be reached", err)
        assert_output_hides_token(self, secret, out, err)
        self.assertNotIn("Bearer", err)

    def test_env_example_has_no_credential_and_root_ignores_cwd(self):
        example = (ROOT / ".env.example").read_text(encoding="utf-8")
        self.assertEqual(example, "LINKEDIN_ACCESS_TOKEN=\n")
        rules = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("\n.env\n", "\n" + rules)
        self.assertIn(".env.*\n", rules)
        self.assertIn("!.env.example\n", rules)
        expected = Path(access.__file__).resolve().parent.parent
        self.assertEqual(access.repository_root(), expected)
        previous = os.getcwd()
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        os.chdir(folder.name)
        try:
            self.assertEqual(access.repository_root(), expected)
            self.assertEqual(access.default_env_file(), expected / ".env")
        finally:
            os.chdir(previous)


if __name__ == "__main__":
    unittest.main()
