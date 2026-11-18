"""Explicit publication of one approved text post.

P4.2 sends one create when the user runs this command for one post id.
That invocation is the publication authorization. The default transport is
UGC_SELF_SERVICE, POST https://api.linkedin.com/v2/ugcPosts, because that is
the endpoint documented by Share on LinkedIn. POSTS_API remains implemented
and runs only when --transport POSTS_API is passed. A failure does not call
the other transport. There is no second prompt, no scheduler, no queue, and
no retry of a mutating request.

The access token and the OIDC subject stay in process memory. This module does
not write them, and it does not write the Person URN used as author. Token
resolution is the shared lookup in linkedin_access.py.

Run from the repository root:

    python scripts/linkedin_publish.py POST-YYYYMMDD-NN
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import content_engine as engine  # noqa: E402
import linkedin_access as access  # noqa: E402

# Author states for this command only. P4.1 identity still reports unresolved.
PERSON_URN_CANDIDATE_FROM_OIDC = "PERSON_URN_CANDIDATE_FROM_OIDC"
PERSON_URN_LIVE_VERIFIED = "PERSON_URN_LIVE_VERIFIED"

# One invocation selects one transport. There is no fallback between them.
UGC_SELF_SERVICE = "UGC_SELF_SERVICE"
POSTS_API = "POSTS_API"
FIRST_LIVE_TRANSPORT = UGC_SELF_SERVICE
TRANSPORTS = frozenset({UGC_SELF_SERVICE, POSTS_API})

# Posts API create success, read 2026-10-05 from the September 2026 Posts API page.
# https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api?view=li-lms-2026-09
# Share on LinkedIn text share, read 2026-10-05:
# https://learn.microsoft.com/en-us/linkedin/consumer/integrations/self-serve/share-on-linkedin
# That page requires X-Restli-Protocol-Version: 2.0.0 and does not require Linkedin-Version.
CREATE_SUCCESS_STATUSES = frozenset({201})
POSTS_URL = access.POSTS_ENDPOINT
UGC_POSTS_URL = "https://api.linkedin.com/v2/ugcPosts"

AUTHOR_ERROR_CODES = frozenset({"INVALID_URN_TYPE", "INVALID_URN_ID"})


class PublicationUncertain(access.LinkedInAccessError):
    """The create request may have reached LinkedIn. Do not send it again."""


@dataclass
class PublishResult:
    post_id: str
    outcome: str
    author_resolution: str = ""
    oidc_state: str = ""
    http_status: int | None = None
    body_sha256: str = ""
    linkedin_post_urn: str = ""
    published_at: str = ""
    detail: str = ""
    errors: list[str] = field(default_factory=list)
    candidate_author: str = field(default="", repr=False)


def usable_oidc_sub(value: object) -> str | None:
    """Return an OIDC subject that can be placed in one URN path segment."""
    if not isinstance(value, str) or not value or value.strip() != value:
        return None
    if any(char.isspace() or ord(char) < 32 or char in ":/?#%" for char in value):
        return None
    return value


def candidate_from_oidc_sub(sub: str) -> dict[str, object]:
    """Build the unpublished author candidate. This does not mark it verified."""
    usable = usable_oidc_sub(sub)
    if usable is None:
        raise access.MalformedIdentity("UserInfo response was malformed. Identity is unresolved.")
    return {
        "candidate_author": "urn:li:person:" + usable,
        "author_resolution": PERSON_URN_CANDIDATE_FROM_OIDC,
        "live_verified": False,
    }


def posts_body(author: str, commentary: str) -> dict:
    """Text-only organic member post, matching the current Posts API sample."""
    return {
        "author": author,
        "commentary": commentary,
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }


def posts_headers(token: str) -> dict[str, str]:
    """Headers required by the current Posts API. Version comes from linkedin_access."""
    return {
        "Authorization": f"Bearer {token}",
        "Linkedin-Version": access.DOCUMENTED_LINKEDIN_VERSION,
        "X-Restli-Protocol-Version": access.RESTLI_PROTOCOL_VERSION,
        "Content-Type": "application/json",
    }


def ugc_self_service_body(author: str, text: str) -> dict:
    """Text-only Share on LinkedIn body. Fields match the documented sample."""
    return {
        "author": author,
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary": {
                    "text": text,
                },
                "shareMediaCategory": "NONE",
            },
        },
        "visibility": {
            "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC",
        },
    }


def ugc_headers(token: str) -> dict[str, str]:
    """Headers required by Share on LinkedIn. That page does not require Linkedin-Version."""
    return {
        "Authorization": f"Bearer {token}",
        "X-Restli-Protocol-Version": access.RESTLI_PROTOCOL_VERSION,
        "Content-Type": "application/json",
    }


def render_preview(post_id: str, body_sha256: str, transport: str) -> str:
    endpoint = "POST /v2/ugcPosts" if transport == UGC_SELF_SERVICE else "POST /rest/posts"
    return (
        f"Post:\n{post_id}\n\n"
        f"Approved hash:\n{body_sha256}\n\n"
        "OIDC:\nauthenticated\n\n"
        f"Author:\n{PERSON_URN_CANDIDATE_FROM_OIDC}\n\n"
        f"Transport:\n{transport}\n\n"
        f"Endpoint:\n{endpoint}\n\n"
        "Visibility:\nPUBLIC\n"
    )


def prepare_publication(content_root: Path, post_id: str) -> tuple[dict | None, list[str]]:
    """Accept one current approved post. Do not review, approve, reopen, or edit it."""
    if not engine.ID_RE.fullmatch(post_id) or not post_id.startswith("POST-"):
        return None, [f"{post_id}: not a post id."]
    published = content_root / engine.PUBLISHED_DIR / f"{post_id}.md"
    approved = content_root / engine.APPROVED_DIR / f"{post_id}.md"
    draft = content_root / engine.KIND_DIR["post"] / f"{post_id}.md"
    if published.is_file():
        return None, [f"{post_id}: already published."]
    if draft.is_file():
        return None, [f"{post_id}: draft rejected. Reopened posts are not accepted."]
    if not approved.is_file():
        return None, [f"{post_id}: not in content/approved/."]

    try:
        text = approved.read_text(encoding="utf-8")
        fields, rest = engine.parse_frontmatter(text)
        sections = engine.section_map(rest)
    except (OSError, ValueError) as exc:
        return None, [f"{post_id}: {exc}"]

    errors: list[str] = []
    if fields.get("kind") != "post":
        return None, [f"{post_id}: not a post."]
    if fields.get("status") != "approved" or fields.get("approval_status") != "approved":
        return None, [f"{post_id}: reopened posts are not accepted."]
    if fields.get("review_verdict") != "PASS":
        errors.append(f"{post_id}: review_verdict must be PASS.")
    if "Body" not in sections:
        return None, [f"{post_id}: missing Body section."]
    body = sections["Body"]
    digest = engine.body_hash(body)
    if fields.get("review_body_sha256", "") != digest:
        errors.append(f"{post_id}: stale review.")
    if fields.get("approved_body_sha256", "") != digest:
        errors.append(f"{post_id}: stale approval.")
    if fields.get("body_sha256", "") != digest:
        errors.append(f"{post_id}: externally edited approved file.")
    if any(fields.get(key) for key in ("publication_status", "published_body_sha256", "published_at", "linkedin_post_urn")):
        errors.append(f"{post_id}: already published.")
    try:
        claims = engine.load_claims()
        hooks = engine.load_hooks()
    except (OSError, RuntimeError, ValueError) as exc:
        errors.append(f"{post_id}: artifact check failed: {exc}")
        return None, access._dedupe(errors)
    errors.extend(engine.check_artifact(approved, claims, hooks, content_root))
    if errors:
        return None, access._dedupe(errors)
    commentary = engine.normalize_body(body)
    if engine.body_hash(commentary) != digest:
        return None, [f"{post_id}: approved body hash does not match the commentary."]
    return {
        "fields": fields,
        "body_sha256": digest,
        "commentary": commentary,
        "source_text": text,
    }, []


def _build_create_request(url: str, payload: bytes, headers: dict[str, str]) -> urllib.request.Request:
    if url not in {POSTS_URL, UGC_POSTS_URL}:
        raise access.LinkedInAccessError("Refusing a request that is not the one selected create.")
    parts = urllib.parse.urlsplit(url)
    if parts.query or parts.fragment:
        raise access.LinkedInAccessError("Refusing a URL with a query or fragment.")
    request = urllib.request.Request(url, data=payload, method="POST")
    request.headers.clear()
    request.unredirected_hdrs.clear()
    for key, value in headers.items():
        if any(char in "\r\n" for char in key) or any(char in "\r\n" for char in value):
            raise access.LinkedInAccessError("Refusing a header that contains a line break.")
        if key.lower() == "authorization":
            request.unredirected_hdrs[key] = value
        else:
            request.headers[key] = value
    if request.get_method() != "POST" or request.full_url != url:
        raise access.LinkedInAccessError("Refusing a request that is not the one selected create.")
    return request


def _build_post_request(payload: bytes, headers: dict[str, str]) -> urllib.request.Request:
    return _build_create_request(POSTS_URL, payload, headers)


def _create_request(transport: str, author: str, commentary: str, token: str) -> urllib.request.Request:
    """Build the one mutating request for the selected transport."""
    if transport == UGC_SELF_SERVICE:
        payload = json.dumps(ugc_self_service_body(author, commentary), ensure_ascii=False).encode("utf-8")
        return _build_create_request(UGC_POSTS_URL, payload, ugc_headers(token))
    if transport == POSTS_API:
        payload = json.dumps(posts_body(author, commentary), ensure_ascii=False).encode("utf-8")
        return _build_create_request(POSTS_URL, payload, posts_headers(token))
    raise access.LinkedInAccessError("Unknown publication transport.")


def _response_header(headers, name: str) -> str:
    if not headers:
        return ""
    getter = getattr(headers, "get", None)
    if getter is None:
        return ""
    for candidate in (name, name.lower(), "X-RestLi-Id", "x-restli-id"):
        value = getter(candidate)
        if value:
            return str(value).strip()
    return ""


def send_create_once(request: urllib.request.Request, opener, timeout: float) -> tuple[int, bytes, object]:
    """Open the create request once. A network failure is uncertain and is not retried."""
    open_fn = opener or access._urlopen_no_redirect
    try:
        response = open_fn(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        raw = exc.read() if exc.fp is not None else b""
        code = exc.code if isinstance(exc.code, int) else 0
        return code, raw, getattr(exc, "headers", None)
    except (urllib.error.URLError, TimeoutError, OSError):
        raise PublicationUncertain("publication outcome uncertain") from None
    try:
        body = response.read()
        status = getattr(response, "status", None) or response.getcode()
        response_headers = getattr(response, "headers", None)
    finally:
        close = getattr(response, "close", None)
        if close:
            close()
    return int(status), body, response_headers


def _redact_sensitive(text: str, token: str, sub: str, candidate: str) -> str:
    redacted = access.redact(text, token)
    if candidate:
        redacted = redacted.replace(candidate, "[redacted]")
    if sub:
        redacted = redacted.replace(sub, "[redacted]")
    return redacted


def _author_was_rejected(detail: str) -> bool:
    folded = detail.casefold()
    if "invalid_urn_type" in folded or "invalid_urn_id" in folded:
        return True
    return "author" in folded and "urn" in folded


def _failure(result: PublishResult, detail: str) -> PublishResult:
    result.outcome = "failed"
    result.detail = detail
    result.author_resolution = PERSON_URN_CANDIDATE_FROM_OIDC
    return result


def move_approved_to_published(
    content_root: Path,
    post_id: str,
    body_sha256: str,
    linkedin_post_urn: str,
    now: str | None,
    token: str,
    sub: str,
) -> str:
    """Move one approved artifact after LinkedIn has already accepted the create."""
    if engine.CREATED_POST_URN_RE.fullmatch(linkedin_post_urn) is None:
        raise PublicationUncertain("publication outcome uncertain")
    approved = content_root / engine.APPROVED_DIR / f"{post_id}.md"
    published = content_root / engine.PUBLISHED_DIR / f"{post_id}.md"
    if published.exists():
        raise access.LinkedInAccessError(f"{post_id}: already published.")
    if not approved.is_file():
        raise PublicationUncertain("publication outcome uncertain")
    text = approved.read_text(encoding="utf-8")
    fields, rest = engine.parse_frontmatter(text)
    sections = engine.section_map(rest)
    body = sections["Body"]
    digest = engine.body_hash(body)
    recorded = (
        digest,
        fields.get("body_sha256", ""),
        fields.get("review_body_sha256", ""),
        fields.get("approved_body_sha256", ""),
    )
    if any(value != body_sha256 for value in recorded):
        raise PublicationUncertain("publication outcome uncertain")
    timestamp = now or engine.utc_timestamp()
    if not engine.valid_utc_timestamp(timestamp):
        raise access.LinkedInAccessError(f"{post_id}: published_at must be a UTC timestamp YYYY-MM-DDTHH:MM:SSZ")
    fields["status"] = "published"
    fields["publication_status"] = "published"
    fields["published_body_sha256"] = digest
    fields["published_at"] = timestamp
    fields["linkedin_post_urn"] = linkedin_post_urn
    rendered = engine.render_frontmatter(fields, engine.POST_FIELD_ORDER) + rest
    frontmatter, rendered_rest = engine.parse_frontmatter(rendered)
    if engine.section_map(rendered_rest)["Body"] != body:
        raise access.LinkedInAccessError(f"{post_id}: publication would have changed the body.")
    if frontmatter.get("published_body_sha256") != digest:
        raise access.LinkedInAccessError(f"{post_id}: publication hash does not match the body.")
    forbidden = {value for value in (token, sub, f"urn:li:person:{sub}" if sub else "") if value}
    for key, value in frontmatter.items():
        if key in {"sub", "access_token", "author", "person_urn"} or value in forbidden or "urn:li:person:" in value:
            raise access.LinkedInAccessError(f"{post_id}: publication metadata would have stored a credential.")
    engine.write_atomic(published, rendered)
    claims = engine.load_claims()
    hooks = engine.load_hooks()
    errors = engine.check_artifact(published, claims, hooks, content_root)
    if errors:
        published.unlink()
        raise access.LinkedInAccessError(f"{post_id}: published artifact failed validation.")
    try:
        engine.append_history(
            content_root,
            {
                "event": "published",
                "post_id": post_id,
                "body_sha256": digest,
                "linkedin_post_urn": linkedin_post_urn,
                "timestamp": timestamp,
            },
        )
    except (OSError, ValueError) as exc:
        published.unlink()
        raise access.LinkedInAccessError(f"{post_id}: history was not recorded: {exc}") from None
    try:
        approved.unlink()
    except OSError as exc:
        raise access.LinkedInAccessError(
            f"{post_id}: LinkedIn post {linkedin_post_urn} was created, the published copy is at {published}, "
            f"and the approved file could not be removed: {exc}"
        ) from None
    return timestamp


def _other_transport_lines(transport: str) -> list[str]:
    if transport == UGC_SELF_SERVICE:
        return ["Posts API:", "not called"]
    return ["UGC API:", "not called"]


def _classify_create(
    result: PublishResult,
    status: int,
    raw: bytes,
    response_headers,
    token: str,
    sub: str,
    content_root: Path,
    now: str | None,
    transport: str,
) -> PublishResult:
    result.http_status = status
    detail = access.linkedin_error_detail(raw, access._content_type(response_headers), token)
    detail = _redact_sensitive(detail, token, sub, result.candidate_author)
    if status in CREATE_SUCCESS_STATUSES:
        post_urn = _response_header(response_headers, "x-restli-id")
        if engine.CREATED_POST_URN_RE.fullmatch(post_urn) is None:
            result.outcome = "uncertain"
            result.detail = "publication outcome uncertain"
            result.author_resolution = PERSON_URN_CANDIDATE_FROM_OIDC
            return result
        try:
            published_at = move_approved_to_published(
                content_root,
                result.post_id,
                result.body_sha256,
                post_urn,
                now,
                token,
                sub,
            )
        except PublicationUncertain as exc:
            result.outcome = "uncertain"
            result.detail = str(exc)
            result.linkedin_post_urn = post_urn
            result.author_resolution = PERSON_URN_CANDIDATE_FROM_OIDC
            return result
        except access.LinkedInAccessError:
            result.outcome = "uncertain"
            result.detail = "publication outcome uncertain"
            result.linkedin_post_urn = post_urn
            result.author_resolution = PERSON_URN_CANDIDATE_FROM_OIDC
            return result
        result.outcome = "published"
        result.author_resolution = PERSON_URN_LIVE_VERIFIED
        result.linkedin_post_urn = post_urn
        result.published_at = published_at
        result.detail = ""
        return result

    result.author_resolution = PERSON_URN_CANDIDATE_FROM_OIDC
    if status == 400:
        author = _author_was_rejected(detail)
        lines = [
            "Publication:",
            "failed",
            "",
            "HTTP:",
            "400",
            "",
            "Artifact:",
            "remains approved",
            "",
            "Author resolution:",
            PERSON_URN_CANDIDATE_FROM_OIDC,
        ]
        if detail:
            lines.extend(["", "LinkedIn:", detail])
        if author:
            lines.extend([
                "",
                "The author candidate was rejected.",
                "Person URN was not marked verified.",
            ])
        lines.extend(["", *_other_transport_lines(transport)])
        return _failure(result, "\n".join(lines))
    if status == 401:
        return _failure(
            result,
            "\n".join([
                "Publication:",
                "failed",
                "",
                "HTTP:",
                "401",
                "",
                "The access token is invalid, expired, or revoked.",
                "",
                "Artifact:",
                "remains approved",
                "",
                "Author resolution:",
                PERSON_URN_CANDIDATE_FROM_OIDC,
                "",
                *_other_transport_lines(transport),
            ]),
        )
    if status == 403 and transport == UGC_SELF_SERVICE:
        return _failure(
            result,
            "\n".join([
                "Publication:",
                "failed",
                "",
                "HTTP:",
                "403",
                "",
                "Share on LinkedIn capability denied",
                "",
                "Posts API:",
                "not called",
                "",
                "Artifact:",
                "remains approved",
                "",
                "Author resolution:",
                PERSON_URN_CANDIDATE_FROM_OIDC,
            ]),
        )
    if status == 403:
        return _failure(
            result,
            "\n".join([
                "Publication:",
                "failed",
                "",
                "HTTP:",
                "403",
                "",
                "Token:",
                "authenticated",
                "",
                "Configured scope:",
                "w_member_social",
                "",
                "Posts API:",
                "capability rejected",
                "",
                "UGC API:",
                "not called",
                "",
                "Artifact:",
                "remains approved",
                "",
                "Author resolution:",
                PERSON_URN_CANDIDATE_FROM_OIDC,
            ]),
        )
    if status == 409 and transport == UGC_SELF_SERVICE:
        return _failure(
            result,
            "\n".join([
                "Publication:",
                "failed",
                "",
                "HTTP:",
                "409",
                "",
                "This command does not retry.",
                "",
                "Posts API:",
                "not called",
                "",
                "Artifact:",
                "remains approved",
                "",
                "Author resolution:",
                PERSON_URN_CANDIDATE_FROM_OIDC,
            ]),
        )
    if status == 409:
        return _failure(
            result,
            "\n".join([
                "Publication:",
                "failed",
                "",
                "HTTP:",
                "409",
                "",
                "Posts API:",
                "write conflict",
                "",
                "UGC API:",
                "not called",
                "",
                "LinkedIn documents a retry for this status. This command does not retry.",
                "",
                "Artifact:",
                "remains approved",
                "",
                "Author resolution:",
                PERSON_URN_CANDIDATE_FROM_OIDC,
            ]),
        )
    lines = [
        "Publication:",
        "failed",
        "",
        "HTTP:",
        str(status),
        "",
        "This command does not retry.",
        "",
        *_other_transport_lines(transport),
        "",
        "Artifact:",
        "remains approved",
        "",
        "Author resolution:",
        PERSON_URN_CANDIDATE_FROM_OIDC,
    ]
    if detail:
        lines.extend(["", "LinkedIn:", detail])
    return _failure(result, "\n".join(lines))


def publish_approved_post(
    post_id: str,
    content_root: Path,
    token: str | None,
    opener=None,
    now: str | None = None,
    timeout: float = 20.0,
    transport: str = FIRST_LIVE_TRANSPORT,
) -> PublishResult:
    """Validate, authenticate, preview, and send one create. Never retries that create."""
    if transport not in TRANSPORTS:
        return PublishResult(
            post_id=post_id,
            outcome="refused",
            errors=["Unknown publication transport."],
        )
    prepared, errors = prepare_publication(content_root, post_id)
    if prepared is None:
        return PublishResult(post_id=post_id, outcome="refused", errors=errors)
    if token is None or not str(token).strip():
        return PublishResult(
            post_id=post_id,
            outcome="blocked",
            errors=["No access token was provided."],
            body_sha256=prepared["body_sha256"],
        )
    secret = str(token).strip()
    try:
        identity = access.probe_identity(secret, opener=opener)
    except access.LinkedInAccessError as exc:
        return PublishResult(
            post_id=post_id,
            outcome="blocked",
            detail=_redact_sensitive(str(exc), secret, "", ""),
            errors=["OIDC failure blocks publish."],
            body_sha256=prepared["body_sha256"],
        )
    candidate_state = candidate_from_oidc_sub(identity.subject)
    if (
        not identity.authenticated
        or identity.state != access.OIDC_AUTHENTICATED
        or candidate_state["author_resolution"] != PERSON_URN_CANDIDATE_FROM_OIDC
        or candidate_state["live_verified"]
    ):
        return PublishResult(
            post_id=post_id,
            outcome="blocked",
            errors=["OIDC failure blocks publish."],
            body_sha256=prepared["body_sha256"],
        )
    confirmed, confirm_errors = prepare_publication(content_root, post_id)
    if confirmed is None or confirmed["body_sha256"] != prepared["body_sha256"]:
        return PublishResult(
            post_id=post_id,
            outcome="refused",
            errors=confirm_errors or [f"{post_id}: externally edited approved file."],
            body_sha256=prepared["body_sha256"],
        )
    candidate = str(candidate_state["candidate_author"])
    sub = identity.subject
    result = PublishResult(
        post_id=post_id,
        outcome="pending",
        author_resolution=PERSON_URN_CANDIDATE_FROM_OIDC,
        oidc_state=access.OIDC_AUTHENTICATED,
        body_sha256=confirmed["body_sha256"],
        candidate_author=candidate,
    )
    request = _create_request(transport, candidate, confirmed["commentary"], secret)
    expected_url = UGC_POSTS_URL if transport == UGC_SELF_SERVICE else POSTS_URL
    if request.get_method() != "POST" or request.full_url != expected_url:
        raise access.LinkedInAccessError("Refusing a request that is not the one selected create.")
    print(render_preview(post_id, confirmed["body_sha256"], transport), end="")
    try:
        status, raw, response_headers = send_create_once(request, opener, timeout)
    except PublicationUncertain as exc:
        result.outcome = "uncertain"
        result.detail = str(exc)
        result.author_resolution = PERSON_URN_CANDIDATE_FROM_OIDC
        return result
    return _classify_create(
        result,
        status,
        raw,
        response_headers,
        secret,
        sub,
        content_root,
        now,
        transport,
    )


def render_publish(result: PublishResult, token: str = "", sub: str = "") -> str:
    if result.outcome == "refused":
        reason = "\n".join(result.errors) if result.errors else "Publication refuses this artifact."
        text = f"Post:\n{result.post_id}\n\nPublication:\nrefused\n\nReason:\n{reason}\n"
    elif result.outcome == "blocked":
        lines = [f"Post:\n{result.post_id}", "", "Publication:", "blocked"]
        if result.errors:
            lines.extend(["", "Reason:", "\n".join(result.errors)])
        if result.detail:
            lines.extend(["", result.detail])
        text = "\n".join(lines) + "\n"
    elif result.outcome == "uncertain":
        lines = [
            f"Post:\n{result.post_id}",
            "",
            "publication outcome uncertain",
            "",
            "The approved artifact was left in place.",
            "This command does not retry.",
        ]
        if result.linkedin_post_urn:
            lines.extend(["", "LinkedIn post:", result.linkedin_post_urn])
        text = "\n".join(lines) + "\n"
    elif result.outcome == "published":
        text = "\n".join([
            f"Post:\n{result.post_id}",
            "",
            "Publication:",
            "published",
            "",
            "Author resolution:",
            PERSON_URN_LIVE_VERIFIED,
            "",
            "LinkedIn post:",
            result.linkedin_post_urn,
            "",
            "Published hash:",
            result.body_sha256,
            "",
            "Published at:",
            result.published_at,
            "",
        ])
    else:
        text = result.detail if result.detail.endswith("\n") else result.detail + "\n"
    text = _redact_sensitive(text, token, sub, result.candidate_author)
    if result.candidate_author and result.candidate_author in text:
        raise access.LinkedInAccessError("Publication output refused to print the Person URN.")
    if "urn:li:person:" in text:
        raise access.LinkedInAccessError("Publication output refused to print the Person URN.")
    return text


def _content_root(path: Path | None) -> Path:
    return path if path is not None else ROOT / "content"


def _option_is_allowed(arg: str) -> bool:
    if arg in {"-h", "--help", "--transport"}:
        return True
    if arg.startswith("--transport="):
        return arg.split("=", 1)[1] in TRANSPORTS
    return False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Publish one approved text post. The post id is the authorization. "
            "The default transport is UGC_SELF_SERVICE. There is no scheduler and no fallback."
        )
    )
    parser.add_argument("post_id", help="POST-YYYYMMDD-NN in content/approved/")
    parser.add_argument(
        "--transport",
        choices=tuple(sorted(TRANSPORTS)),
        default=FIRST_LIVE_TRANSPORT,
        help="Selected create transport. The default is UGC_SELF_SERVICE. A failure does not call the other transport.",
    )
    return parser


def main(
    argv: list[str] | None = None,
    *,
    prompt_fn=None,
    opener=None,
    content_root: Path | None = None,
    stdin_is_tty: bool | None = None,
    now: str | None = None,
    environ=None,
    env_file: Path | None = None,
) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except (OSError, ValueError):
            pass
    args = list(sys.argv[1:] if argv is None else argv)
    if any(arg.startswith("-") and not _option_is_allowed(arg) for arg in args):
        print("Do not pass the access token as an argument.", file=sys.stderr)
        return 1
    try:
        parsed = build_parser().parse_args(args)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 1
    interactive = sys.stdin.isatty() if stdin_is_tty is None else stdin_is_tty
    root = _content_root(content_root)
    prepared, errors = prepare_publication(root, parsed.post_id)
    if prepared is None:
        refused = PublishResult(post_id=parsed.post_id, outcome="refused", errors=errors)
        print(render_publish(refused), end="", file=sys.stderr)
        return 1
    try:
        token = access.acquire_access_token(
            prompt_fn=prompt_fn,
            interactive=interactive,
            environ=environ,
            env_file=env_file,
        )
        if token is None:
            raise access.MissingToken("No access token was provided.")
    except access.LinkedInAccessError as exc:
        print(exc, file=sys.stderr)
        return 1
    try:
        result = publish_approved_post(
            parsed.post_id,
            root,
            token,
            opener=opener,
            now=now,
            transport=parsed.transport,
        )
    except access.LinkedInAccessError as exc:
        print(_redact_sensitive(str(exc), token, "", ""), file=sys.stderr)
        return 1
    rendered = render_publish(result, token=token, sub="")
    if result.outcome == "published":
        print(rendered, end="")
        return 0
    print(rendered, end="", file=sys.stderr)
    if result.outcome == "uncertain":
        return 2
    return 1


if __name__ == "__main__":
    sys.exit(main())
