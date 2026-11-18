"""LinkedIn access bootstrap, OIDC identity probe, and publication dry-run.

P4.1 does not publish. It does not schedule. It does not store tokens.
P4.1b adds one Profile API capability probe. It does not publish either.

The access token stays in process memory. It is opaque: this module does not
decode it, log it, or write it. Resolution order is the process environment
variable LINKEDIN_ACCESS_TOKEN, then repository-root .env, then getpass.

Run from the repository root:

    python scripts/linkedin_access.py status
    python scripts/linkedin_access.py identity
    python scripts/linkedin_access.py profile-probe
    python scripts/linkedin_access.py dry-run POST-YYYYMMDD-NN
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import content_engine as engine  # noqa: E402

DISCOVERY_URL = "https://www.linkedin.com/oauth/.well-known/openid-configuration"
USERINFO_URL = "https://api.linkedin.com/v2/userinfo"
# Retrieve Current Member's Profile. Checked 2026-10-05:
# https://learn.microsoft.com/en-us/linkedin/shared/integrations/people/profile-api
# That page documents projections, and it says a non-default field set needs
# partner permissions. This probe therefore sends the documented URL with no
# query. A 403 is Profile API access, not a projection-permission result.
# Only `id` is read. No other response field is printed or stored.
PROFILE_API_URL = "https://api.linkedin.com/v2/me"
ALLOWED_GET_URLS = frozenset({DISCOVERY_URL, USERINFO_URL, PROFILE_API_URL})

# Re-checked against the official versioning page on 2026-10-05:
# https://learn.microsoft.com/en-us/linkedin/marketing/versioning
# Latest documented Marketing version that day was still September 2026, header 202609.
# No live LinkedIn call has confirmed that header. Do not treat it as verified.
# This is the only LinkedIn-Version constant. Publish imports it.
DOCUMENTED_LINKEDIN_VERSION = "202609"
VERSION_CHECKED_ON = "2026-10-05"
VERSION_SOURCE = "https://learn.microsoft.com/en-us/linkedin/marketing/versioning"
API_VERSION_STATUS = "documented_not_live_verified"
LIVE_API_VERSION_VERIFIED = False

POSTS_ENDPOINT = "https://api.linkedin.com/rest/posts"
RESTLI_PROTOCOL_VERSION = "2.0.0"
CONFIGURED_SCOPES = ("openid", "profile", "w_member_social")

OIDC_AUTHENTICATED = "OIDC_AUTHENTICATED"
PERSON_URN_UNRESOLVED = "PERSON_URN_UNRESOLVED"
PERSON_URN_VERIFIED = "PERSON_URN_VERIFIED"
PROFILE_ID_VERIFIED = "PROFILE_ID_VERIFIED"
PROFILE_API_AVAILABLE = "available"
PROFILE_API_UNAVAILABLE = "unavailable"
PROFILE_API_UNVERIFIED = "unverified"
AUTHOR_PLACEHOLDER = "UNRESOLVED"
PROFILE_API_DENIED_REASON = (
    "The current application/token does not have access to the documented "
    "Profile API required to retrieve the Person ID."
)
PROFILE_ID_UNUSABLE_REASON = "The Profile API response did not include a usable person id."
# Joined only to a Profile API id. Never join this to an OIDC subject.
_PERSON_URN_PREFIX = "urn:li:person:"

PROFILE_FIELDS = ("sub", "name", "given_name", "family_name", "picture", "locale")
SAFE_ERROR_FIELDS = ("message", "code", "serviceErrorCode", "status")

# Credential-shaped substrings that may be echoed in an error body. The access
# token itself is removed separately by exact match.
_AUTHORIZATION_BEARER = re.compile(r"(?i)\bauthorization\s*[:=]\s*bearer\s+\S+")
_AUTHORIZATION_RAW = re.compile(r"(?i)\bauthorization\s*[:=]\s*\S+")
_BEARER_VALUE = re.compile(r"(?i)\bbearer\s+\S+")
_ASSIGNED_SECRET = re.compile(
    r"(?i)\b(client_secret|authorization_code|access_token|refresh_token)\b['\"]?\s*[=:]\s*['\"]?\S+"
)
TOKEN_ENV_NAME = "LINKEDIN_ACCESS_TOKEN"
_ENV_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*$")


class LinkedInAccessError(Exception):
    """Message is safe to print. It must not contain an access token."""


class LinkedInMutationBlocked(LinkedInAccessError):
    """P4.1 refused a request that would mutate LinkedIn."""


class MissingToken(LinkedInAccessError):
    pass


class InvalidToken(LinkedInAccessError):
    pass


class InsufficientAccess(LinkedInAccessError):
    pass


class MalformedIdentity(LinkedInAccessError):
    pass


class NetworkFailure(LinkedInAccessError):
    pass


@dataclass
class IdentityResult:
    authenticated: bool
    state: str
    name: str = ""
    subject: str = ""
    fields_returned: tuple[str, ...] = ()
    author_resolution: str = PERSON_URN_UNRESOLVED
    person_urn: str | None = None
    error: str = ""
    userinfo_reachable: bool = False

    def __post_init__(self) -> None:
        if self.person_urn is not None:
            raise LinkedInAccessError("P4.1 cannot store a Person URN.")
        if self.author_resolution == PERSON_URN_VERIFIED:
            raise LinkedInAccessError("P4.1 cannot mark a Person URN verified.")


@dataclass
class ProfileProbeResult:
    """In-memory Profile API capability. The person id is not rendered."""

    http_status: int
    profile_api: str
    person_id_verified: bool
    author_resolution: str
    detail: str = ""
    profile_id: str | None = field(default=None, repr=False)
    person_urn: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.person_id_verified:
            if self.profile_api != PROFILE_API_AVAILABLE:
                raise LinkedInAccessError("A verified person id requires an available Profile API response.")
            if not isinstance(self.profile_id, str) or not self.profile_id:
                raise LinkedInAccessError("A verified person id requires a Profile API id.")
            expected = person_urn_from_profile_id(self.profile_id)
            if self.person_urn != expected or self.author_resolution != PERSON_URN_VERIFIED:
                raise LinkedInAccessError("Person URN does not match the Profile API id.")
            return
        if self.person_urn is not None or self.profile_id is not None:
            raise LinkedInAccessError("Person URN requires a Profile API id.")
        if self.author_resolution == PERSON_URN_VERIFIED:
            raise LinkedInAccessError("Person URN is unresolved.")


@dataclass
class DryRunResult:
    post_id: str
    valid: bool
    errors: list[str] = field(default_factory=list)
    body_sha256: str = ""
    review_verdict: str = ""
    commentary: str = ""
    identity: IdentityResult | None = None
    ready: bool = False
    reasons: list[str] = field(default_factory=list)
    preview: dict | None = None

    def __post_init__(self) -> None:
        self.ready = False


def redact(text: str, secret: str) -> str:
    """Remove credentials from text that may be printed."""
    if not text:
        return text
    redacted = _ASSIGNED_SECRET.sub(lambda match: f"{match.group(1)}=[redacted]", text)
    redacted = _AUTHORIZATION_BEARER.sub("[redacted]", redacted)
    redacted = _AUTHORIZATION_RAW.sub("[redacted]", redacted)
    redacted = _BEARER_VALUE.sub("[redacted]", redacted)
    if secret:
        variants = {
            secret,
            urllib.parse.quote(secret, safe=""),
            urllib.parse.quote_plus(secret),
        }
        for variant in variants:
            if variant:
                redacted = redacted.replace(variant, "[redacted]")
    return redacted


def _clip(text: str, limit: int = 180) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[:limit].rstrip() + "..."


def _safe_field(value: object, secret: str) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        rendered = str(value)
    elif isinstance(value, str):
        rendered = value.strip()
        if not rendered:
            return None
    else:
        return None
    return _clip(redact(rendered, secret))


def _media_type(content_type: str) -> str:
    if not content_type:
        return ""
    media = content_type.split(";", 1)[0].strip().lower()
    if not re.fullmatch(r"[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+", media):
        return ""
    return media


def _content_type(headers) -> str:
    if not headers:
        return ""
    getter = getattr(headers, "get", None)
    if getter is None:
        return ""
    raw = getter("Content-Type")
    if raw is None:
        raw = getter("content-type")
    if raw is None:
        return ""
    return _media_type(str(raw))


def _json_error_detail(payload: dict, secret: str) -> str:
    allowed = {key: payload[key] for key in SAFE_ERROR_FIELDS if key in payload}
    message = _safe_field(allowed.get("message"), secret) if "message" in allowed else None
    parts: list[str] = []
    if message:
        parts.append(message)
    extras: list[str] = []
    for key in ("code", "serviceErrorCode", "status"):
        if key not in allowed:
            continue
        rendered = _safe_field(allowed[key], secret)
        if rendered is not None:
            extras.append(f"{key}={rendered}")
    if extras:
        parts.append("(" + ", ".join(extras) + ")" if message else ", ".join(extras))
    return " ".join(parts)


def linkedin_error_detail(body: bytes, content_type: str, secret: str) -> str:
    """Return a printable LinkedIn error detail, or an empty string.

    JSON bodies contribute only status, serviceErrorCode, code, and message.
    Other bodies contribute a content type and a short redacted snippet.
    """
    if not body:
        return ""
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        media = _media_type(content_type)
        return f"Content-Type: {media}" if media else ""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict):
        return _json_error_detail(payload, secret)
    if payload is not None:
        return ""
    media = _media_type(content_type) or "unknown"
    snippet = _clip(redact(text, secret), 120)
    if snippet:
        return f"Content-Type: {media}; {snippet}"
    return f"Content-Type: {media}"


def format_userinfo_failure(status: int, body: bytes, content_type: str, secret: str) -> str:
    if not isinstance(status, int):
        return "UserInfo returned an unexpected status. Identity is unresolved."
    detail = linkedin_error_detail(body, content_type, secret)
    if detail:
        return f"UserInfo returned HTTP {status}: {detail}"
    return f"UserInfo returned HTTP {status}. Identity is unresolved."


class _RefuseRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects so urllib cannot copy headers onto another URL.

    Python 3.13's redirect handler keeps Authorization and drops
    Content-Length and Content-Type. The token must stay on the allowlisted URL.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)


def _urlopen_no_redirect(request, timeout):
    opener = urllib.request.build_opener(_RefuseRedirect())
    return opener.open(request, timeout=timeout)


def _build_get_request(url: str, headers: dict[str, str]) -> urllib.request.Request:
    parts = urllib.parse.urlsplit(url)
    if parts.query or parts.fragment:
        raise LinkedInAccessError("Refusing a URL with a query or fragment.")
    request = urllib.request.Request(url, data=None, method="GET")
    request.headers.clear()
    request.unredirected_hdrs.clear()
    for key, value in headers.items():
        if key.lower() == "content-type":
            raise LinkedInAccessError("P4.1 GET does not send a Content-Type header.")
        if any(char in "\r\n" for char in key) or any(char in "\r\n" for char in value):
            raise LinkedInAccessError("Refusing a header that contains a line break.")
        # urllib copies request.headers onto a redirect and would forward a bearer
        # token. Authorization is sent on this request via header_items(), which
        # also includes unredirected headers, and is omitted from that copy.
        if key.lower() == "authorization":
            request.unredirected_hdrs[key] = value
        else:
            request.headers[key] = value
    if request.data is not None:
        raise LinkedInAccessError("P4.1 cannot send a request body.")
    return request


def publication_ready() -> bool:
    """Live publication is blocked for the whole P4.1 runtime."""
    return False


def resolve_author(*, authenticated: bool, oidc_subject: str | None) -> dict[str, str | None]:
    """Record OIDC authentication without treating the subject as a Person ID.

    LinkedIn documents `sub` as a pairwise subject identifier. The Posts API
    author is a Person URN from a member id. This function never builds that URN.
    """
    if authenticated and not isinstance(oidc_subject, str):
        raise MalformedIdentity("UserInfo response was malformed. Identity is unresolved.")
    return {
        "oidc_authentication": OIDC_AUTHENTICATED if authenticated else "not_authenticated",
        "author_resolution": PERSON_URN_UNRESOLVED,
        "person_urn": None,
    }


def _usable_profile_id(value: object) -> str | None:
    """Return a Profile API id, or None when it cannot identify a member."""
    if not isinstance(value, str) or not value or value.strip() != value:
        return None
    if any(char.isspace() or ord(char) < 32 for char in value):
        return None
    return value


def person_urn_from_profile_id(profile_id: str) -> str:
    """Build the Posts API author from a Profile API id field."""
    if _usable_profile_id(profile_id) is None:
        raise LinkedInAccessError("Profile API id is not usable. Person URN is unresolved.")
    return _PERSON_URN_PREFIX + profile_id


def resolve_person_author(*, profile_id: object, oidc_subject: str | None = None) -> dict[str, str | None]:
    """Map a Profile API id to author resolution.

    oidc_subject is discarded. An OIDC subject is not a person id, including
    when the Profile API call is unavailable.
    """
    del oidc_subject
    usable = _usable_profile_id(profile_id)
    if usable is None:
        return {
            "profile_id": None,
            "person_urn": None,
            "author_resolution": PERSON_URN_UNRESOLVED,
            "profile_id_state": None,
        }
    return {
        "profile_id": usable,
        "person_urn": person_urn_from_profile_id(usable),
        "author_resolution": PERSON_URN_VERIFIED,
        "profile_id_state": PROFILE_ID_VERIFIED,
    }


def linkedin_http(method: str, url: str, headers: dict[str, str] | None = None, opener=None, timeout: float = 20.0):
    """Perform one allowlisted GET, or refuse the call before any network I/O."""
    if method.upper() != "GET":
        raise LinkedInMutationBlocked(
            "P4.1 cannot send POST, PUT, PATCH, or DELETE requests to LinkedIn."
        )
    if url not in ALLOWED_GET_URLS:
        raise LinkedInAccessError("Refusing a URL outside the allowlisted LinkedIn GET endpoints.")
    return _perform_get(url, headers or {}, opener=opener, timeout=timeout)


def _perform_get(url: str, headers: dict[str, str], opener, timeout: float):
    request = _build_get_request(url, headers)
    open_fn = opener or _urlopen_no_redirect
    try:
        response = open_fn(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        raw = exc.read() if exc.fp is not None else b""
        code = exc.code if isinstance(exc.code, int) else 0
        return code, raw, _content_type(getattr(exc, "headers", None))
    except (urllib.error.URLError, TimeoutError, OSError):
        raise NetworkFailure("LinkedIn could not be reached. Identity is unverified.") from None
    try:
        body = response.read()
        status = getattr(response, "status", None) or response.getcode()
        content_type = _content_type(getattr(response, "headers", None))
    finally:
        close = getattr(response, "close", None)
        if close:
            close()
    return status, body, content_type


def _decode_json(body: bytes, malformed: str) -> dict:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise MalformedIdentity(malformed) from None
    if not isinstance(payload, dict):
        raise MalformedIdentity(malformed)
    return payload


def discover_userinfo(opener=None) -> str:
    status, body, _content_type = linkedin_http(
        "GET", DISCOVERY_URL, {"Accept": "application/json"}, opener=opener
    )
    if status != 200:
        raise MalformedIdentity("OIDC discovery failed. Identity is unresolved.")
    payload = _decode_json(body, "OIDC discovery was malformed. Identity is unresolved.")
    endpoint = payload.get("userinfo_endpoint")
    if endpoint != USERINFO_URL:
        raise MalformedIdentity(
            "OIDC discovery did not confirm the documented userinfo endpoint. Identity is unresolved."
        )
    return USERINFO_URL


_LOCALE_LANGUAGE = re.compile(r"^[A-Za-z]{2,8}$")
_LOCALE_COUNTRY = re.compile(r"^[A-Za-z]{2,3}$")
_LOCALE_STRING = re.compile(r"^[A-Za-z]{2,8}([_-][A-Za-z0-9]{1,8})*$")


def _malformed_userinfo() -> MalformedIdentity:
    return MalformedIdentity("UserInfo response was malformed. Identity is unresolved.")


def normalize_locale(value: object) -> str | None:
    """Return a display locale, or None when the optional field is absent.

    OIDC documents `locale` as text (`en-US`). The live UserInfo API can
    instead return LinkedIn's Locale object (`language`, optional `country`).
    A blank string is omitted, matching the other optional profile fields.
    A present but invalid locale fails validation.
    """
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if _LOCALE_STRING.fullmatch(text) is None:
            raise _malformed_userinfo()
        return text
    if isinstance(value, dict):
        language = value.get("language")
        if not isinstance(language, str) or _LOCALE_LANGUAGE.fullmatch(language.strip()) is None:
            raise _malformed_userinfo()
        language = language.strip()
        if "country" not in value:
            return language
        country = value.get("country")
        if not isinstance(country, str) or _LOCALE_COUNTRY.fullmatch(country.strip()) is None:
            raise _malformed_userinfo()
        return f"{language}-{country.strip()}"
    raise _malformed_userinfo()


def parse_userinfo(body: bytes) -> dict[str, str]:
    payload = _decode_json(body, "UserInfo response was malformed. Identity is unresolved.")
    subject = payload.get("sub")
    if not isinstance(subject, str) or not subject.strip():
        raise _malformed_userinfo()
    parsed: dict[str, str] = {"sub": subject}
    for key in ("name", "given_name", "family_name", "picture"):
        value = payload.get(key)
        if value is None:
            continue
        if not isinstance(value, str):
            raise _malformed_userinfo()
        if value.strip():
            parsed[key] = value
    if "locale" in payload:
        locale = normalize_locale(payload.get("locale"))
        if locale is not None:
            parsed["locale"] = locale
    return parsed


def _identity_from_status(status: int, body: bytes, content_type: str = "", secret: str = "") -> IdentityResult:
    if status == 401:
        raise InvalidToken("The access token is invalid, expired, or revoked.")
    if status == 403:
        raise InsufficientAccess(
            "Insufficient scope or access. "
            "Configured scopes are openid, profile, and w_member_social. "
            "The access token was not decoded, so its effective scopes are unknown."
        )
    if status != 200:
        raise MalformedIdentity(format_userinfo_failure(status, body, content_type, secret))
    profile = parse_userinfo(body)
    author = resolve_author(authenticated=True, oidc_subject=profile.get("sub"))
    return IdentityResult(
        authenticated=True,
        state=str(author["oidc_authentication"]),
        name=profile.get("name", ""),
        subject=profile.get("sub", ""),
        fields_returned=tuple(key for key in PROFILE_FIELDS if key in profile),
        author_resolution=str(author["author_resolution"]),
        person_urn=None,
        userinfo_reachable=True,
    )


def _bearer_accept_headers(secret: str) -> dict[str, str]:
    """Headers for one allowlisted GET.

    Live UserInfo calls succeeded with Authorization and Accept alone. The
    profile probe sends the same pair. Neither call sends LinkedIn-Version,
    Content-Type, or X-Restli-Protocol-Version.
    """
    return {
        "Authorization": f"Bearer {secret}",
        "Accept": "application/json",
    }


def probe_identity(token: str, opener=None) -> IdentityResult:
    if token is None or not str(token).strip():
        raise MissingToken("No access token was provided.")
    secret = str(token).strip()
    try:
        discover_userinfo(opener)
        status, body, content_type = linkedin_http(
            "GET",
            USERINFO_URL,
            _bearer_accept_headers(secret),
            opener=opener,
        )
        return _identity_from_status(status, body, content_type, secret)
    except LinkedInAccessError as exc:
        safe = redact(str(exc), secret)
        if safe != str(exc):
            raise type(exc)(safe) from None
        raise
    except Exception:
        raise NetworkFailure("LinkedIn could not be reached. Identity is unverified.") from None


def format_profile_failure(status: int, body: bytes, content_type: str, secret: str) -> str:
    if not isinstance(status, int):
        return "Profile API returned an unexpected status. Person URN is unresolved."
    detail = linkedin_error_detail(body, content_type, secret)
    if detail:
        return f"Profile API returned HTTP {status}: {detail}"
    return f"Profile API returned HTTP {status}. Person URN is unresolved."


def _profile_id_from_body(body: bytes) -> object:
    payload = _decode_json(body, "Profile API response was malformed. Person URN is unresolved.")
    if "id" not in payload:
        return None
    return payload.get("id")


def _profile_from_status(status: int, body: bytes, content_type: str = "", secret: str = "") -> ProfileProbeResult:
    if status == 401:
        raise InvalidToken("The access token is invalid, expired, or revoked.")
    if status == 403:
        return ProfileProbeResult(
            http_status=403,
            profile_api=PROFILE_API_UNAVAILABLE,
            person_id_verified=False,
            author_resolution=PERSON_URN_UNRESOLVED,
            detail=PROFILE_API_DENIED_REASON,
        )
    if status != 200:
        raise MalformedIdentity(format_profile_failure(status, body, content_type, secret))
    author = resolve_person_author(profile_id=_profile_id_from_body(body), oidc_subject=None)
    verified = author["author_resolution"] == PERSON_URN_VERIFIED
    return ProfileProbeResult(
        http_status=200,
        profile_api=PROFILE_API_AVAILABLE,
        person_id_verified=verified,
        author_resolution=str(author["author_resolution"]),
        detail="" if verified else PROFILE_ID_UNUSABLE_REASON,
        profile_id=author["profile_id"],
        person_urn=author["person_urn"],
    )


def probe_profile(token: str, opener=None, *, oidc_subject: str | None = None) -> ProfileProbeResult:
    """GET the Profile API once. An OIDC subject passed here is ignored."""
    del oidc_subject
    if token is None or not str(token).strip():
        raise MissingToken("No access token was provided.")
    secret = str(token).strip()
    try:
        status, body, content_type = linkedin_http(
            "GET",
            PROFILE_API_URL,
            _bearer_accept_headers(secret),
            opener=opener,
        )
        return _profile_from_status(status, body, content_type, secret)
    except LinkedInAccessError as exc:
        safe = redact(str(exc), secret)
        if safe != str(exc):
            raise type(exc)(safe) from None
        raise
    except Exception:
        raise NetworkFailure(
            "LinkedIn could not be reached. Profile API capability is unverified."
        ) from None


def repository_root() -> Path:
    """Repository root. This is the scripts directory's parent, not the working directory."""
    return ROOT


def default_env_file() -> Path:
    return repository_root() / ".env"


def parse_dotenv(text: str) -> dict[str, str]:
    """Parse KEY=value lines.

    Blank lines and full-line ``#`` comments are ignored. One matching pair of
    single or double quotes around a value is removed. Values stay literal:
    this does not expand variables, run commands, or interpolate expressions.
    """
    parsed: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if _ENV_KEY.fullmatch(key) is None:
            continue
        parsed[key] = _unquote_env_value(value.strip())
    return parsed


def _unquote_env_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def read_dotenv(path: Path) -> dict[str, str]:
    """Read one env file. Missing or unreadable files yield an empty mapping.

    Failures stay empty so a token in the file cannot be copied into an exception.
    """
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        return {}
    return parse_dotenv(text)


def _nonempty_token(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    token = value.strip()
    if not token:
        return None
    return token


def configured_access_token(
    *,
    environ: Mapping[str, str] | None = None,
    env_file: Path | None = None,
) -> str | None:
    """Return a token from the process environment, then from ``.env``.

    A non-empty process value wins and the file is not read. A missing or blank
    process value falls through to the file. This does not prompt and does not
    write the process environment.
    """
    source = os.environ if environ is None else environ
    from_process = _nonempty_token(source.get(TOKEN_ENV_NAME))
    if from_process is not None:
        return from_process
    path = default_env_file() if env_file is None else env_file
    if not path.is_file():
        return None
    return _nonempty_token(read_dotenv(path).get(TOKEN_ENV_NAME))


def prompt_for_access_token(prompt_fn=None) -> str:
    ask = prompt_fn or getpass.getpass
    try:
        value = ask("LinkedIn access token: ")
    except (EOFError, KeyboardInterrupt):
        raise MissingToken("No access token was provided.") from None
    token = _nonempty_token(value)
    if token is None:
        raise MissingToken("No access token was provided.")
    return token


def acquire_access_token(
    *,
    prompt_fn=None,
    interactive: bool = False,
    environ: Mapping[str, str] | None = None,
    env_file: Path | None = None,
) -> str | None:
    """Resolve the access token without printing it.

    Process environment, then repository-root ``.env``, then ``getpass`` when
    the caller is interactive or supplied a prompt function. Otherwise return
    None so a non-interactive dry-run can continue without a token.
    """
    found = configured_access_token(environ=environ, env_file=env_file)
    if found is not None:
        return found
    if interactive or prompt_fn is not None:
        return prompt_for_access_token(prompt_fn)
    return None


def read_access_token(
    prompt_fn=None,
    *,
    environ: Mapping[str, str] | None = None,
    env_file: Path | None = None,
) -> str:
    token = acquire_access_token(
        prompt_fn=prompt_fn,
        interactive=True,
        environ=environ,
        env_file=env_file,
    )
    if token is None:
        raise MissingToken("No access token was provided.")
    return token


def _dedupe(messages: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for message in messages:
        if message not in seen:
            seen.add(message)
            ordered.append(message)
    return ordered


def validate_approved_post(content_root: Path, post_id: str) -> tuple[dict | None, list[str]]:
    """Accept one current approved post. Do not review, approve, reopen, or edit it."""
    if not engine.ID_RE.fullmatch(post_id) or not post_id.startswith("POST-"):
        return None, [f"{post_id}: not a post id."]
    approved = content_root / engine.APPROVED_DIR / f"{post_id}.md"
    draft = content_root / engine.KIND_DIR["post"] / f"{post_id}.md"
    if draft.is_file():
        return None, [
            f"{post_id}: draft rejected. Reopened posts are not accepted. "
            "Dry-run only reads content/approved/."
        ]
    if not approved.is_file():
        return None, [f"{post_id}: not in content/approved/. Dry-run refuses it."]

    try:
        text = approved.read_text(encoding="utf-8")
        fields, rest = engine.parse_frontmatter(text)
        sections = engine.section_map(rest)
    except (OSError, ValueError) as exc:
        return None, [f"{post_id}: {exc}"]

    errors: list[str] = []
    if fields.get("status") != "approved" or fields.get("approval_status") != "approved":
        errors.append(f"{post_id}: reopened artifact rejected.")
    if fields.get("review_verdict") != "PASS":
        errors.append(f"{post_id}: review_verdict must be PASS.")
    body = sections.get("Body", "")
    digest = engine.body_hash(body)
    recorded = (
        fields.get("body_sha256", ""),
        fields.get("review_body_sha256", ""),
        fields.get("approved_body_sha256", ""),
    )
    if any(value != digest for value in recorded):
        errors.append(f"{post_id}: stale approved artifact.")
    try:
        claims = engine.load_claims()
        hooks = engine.load_hooks()
    except (OSError, RuntimeError, ValueError) as exc:
        errors.append(f"{post_id}: artifact check failed: {exc}")
        return None, _dedupe(errors)
    errors.extend(engine.check_artifact(approved, claims, hooks, content_root))
    if errors:
        return None, _dedupe(errors)
    return {
        "fields": fields,
        "body_sha256": digest,
        "commentary": engine.normalize_body(body),
        "review_verdict": fields.get("review_verdict", ""),
    }, []


def _blocked_reasons(identity: IdentityResult | None) -> list[str]:
    reasons = ["Person URN has not been verified."]
    if not LIVE_API_VERSION_VERIFIED or API_VERSION_STATUS != "live_verified":
        reasons.append("Live API version has not been verified.")
    reasons.append("P4.1 does not send publication requests.")
    if identity is not None and identity.error:
        reasons.append(identity.error)
    return reasons


def _failed_identity(error: LinkedInAccessError) -> IdentityResult:
    return IdentityResult(
        authenticated=False,
        state="not_authenticated",
        author_resolution=PERSON_URN_UNRESOLVED,
        person_urn=None,
        error=str(error),
    )


def dry_run(post_id: str, content_root: Path, token: str | None = None, opener=None) -> DryRunResult:
    """Build a publication preview. This function does not call a mutating API."""
    payload, errors = validate_approved_post(content_root, post_id)
    if payload is None:
        return DryRunResult(post_id=post_id, valid=False, errors=errors, ready=False)
    identity: IdentityResult | None = None
    if token is not None and str(token).strip():
        try:
            identity = probe_identity(token, opener=opener)
        except LinkedInAccessError as exc:
            identity = _failed_identity(exc)
    result = DryRunResult(
        post_id=post_id,
        valid=True,
        body_sha256=payload["body_sha256"],
        review_verdict=payload["review_verdict"],
        commentary=payload["commentary"],
        identity=identity,
        ready=False,
        reasons=_blocked_reasons(identity),
        preview=future_posts_preview(payload["commentary"]),
    )
    result.ready = False
    return result


def future_posts_preview(commentary: str) -> dict:
    """Document the Posts API shape. Nothing in this dict is sent."""
    author = resolve_author(authenticated=False, oidc_subject=None)
    return {
        "endpoint": POSTS_ENDPOINT,
        "method": "POST",
        "sent": False,
        "production_api": "posts",
        "ugc_posts_selected": False,
        "payload_structure": "understood",
        "live_api_version_verified": LIVE_API_VERSION_VERIFIED,
        "api_version_status": API_VERSION_STATUS,
        "documented_linkedin_version": DOCUMENTED_LINKEDIN_VERSION,
        "headers": {
            "Content-Type": "application/json",
            "X-Restli-Protocol-Version": RESTLI_PROTOCOL_VERSION,
            "Linkedin-Version": DOCUMENTED_LINKEDIN_VERSION,
        },
        "body": {
            "author": AUTHOR_PLACEHOLDER,
            "author_resolution": author["author_resolution"],
            "commentary": commentary,
            "visibility": "PUBLIC",
            "distribution": {
                "feedDistribution": "MAIN_FEED",
                "targetEntities": [],
                "thirdPartyDistributionChannels": [],
            },
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": False,
        },
    }


def render_profile_probe(result: ProfileProbeResult) -> str:
    """Capability report. The person id and Person URN stay off the page."""
    if result.profile_api == PROFILE_API_UNAVAILABLE:
        lines = [
            "Profile API:",
            "unavailable",
            "",
            "HTTP:",
            "403",
            "",
            "Current configured scopes:",
            " ".join(CONFIGURED_SCOPES),
            "",
            "Person URN:",
            "unresolved",
            "",
            "Author resolution:",
            PERSON_URN_UNRESOLVED,
            "",
            "Reason:",
            PROFILE_API_DENIED_REASON,
        ]
    elif result.person_id_verified:
        lines = [
            "Profile API:",
            "available",
            "",
            "Person ID:",
            "verified",
            "",
            "Person URN:",
            "verified",
            "",
            "Author resolution:",
            PERSON_URN_VERIFIED,
            "",
            PROFILE_ID_VERIFIED,
        ]
    else:
        lines = [
            "Profile API:",
            "available",
            "",
            "Person ID:",
            "unverified",
            "",
            "Person URN:",
            "unresolved",
            "",
            "Author resolution:",
            PERSON_URN_UNRESOLVED,
        ]
        if result.detail:
            lines.extend(["", "Reason:", result.detail])
    rendered = "\n".join(lines) + "\n"
    tokens = rendered.split()
    if result.profile_id and result.profile_id in tokens:
        raise LinkedInAccessError("Profile probe refused to print the person id.")
    if _PERSON_URN_PREFIX in rendered:
        raise LinkedInAccessError("Profile probe refused to print the Person URN.")
    return rendered


def render_identity(result: IdentityResult) -> str:
    lines = [
        "Authenticated LinkedIn member:",
        f"Name: {result.name or '(not returned)'}",
        f"OIDC subject: {result.subject}",
        "OIDC authentication:",
        result.state,
        "Author resolution:",
        result.author_resolution,
        "Fields returned:",
        ", ".join(result.fields_returned) if result.fields_returned else "(none)",
        "Configured scopes:",
        " ".join(CONFIGURED_SCOPES),
        "Runtime-proven capabilities:",
        "userinfo" if result.userinfo_reachable else "none",
        "Effective token scopes:",
        "unknown",
    ]
    return "\n".join(lines) + "\n"


def render_dry_run(result: DryRunResult) -> str:
    if publication_ready() or result.ready:
        raise LinkedInAccessError("P4.1 cannot report ready for live publication.")
    if not result.valid:
        reason = "\n".join(result.errors) if result.errors else "Dry-run refuses this artifact."
        return f"POST ID:\n{result.post_id}\n\nDry-run:\nrefused\n\nReason:\n{reason}\n"
    identity = result.identity
    authenticated = "yes" if identity is not None and identity.authenticated else "no"
    author = identity.author_resolution if identity is not None else PERSON_URN_UNRESOLVED
    reasons = "\n".join(result.reasons)
    lines = [
        "POST ID:",
        result.post_id,
        "",
        "Body SHA:",
        result.body_sha256,
        "",
        "Approved:",
        "yes",
        "",
        "Review:",
        result.review_verdict,
        "",
        "Authenticated member:",
        authenticated,
        "",
        "Author resolution:",
        author,
        "",
        "Commentary:",
        result.commentary.rstrip("\n"),
        "",
        "Ready for live publication:",
        "NO",
        "Reason:",
        reasons,
        "",
        "Payload structure:",
        "understood",
        "",
        "Live API version verified:",
        "no",
        "",
        "API version status:",
        API_VERSION_STATUS,
        "",
        "Documented LinkedIn-Version:",
        DOCUMENTED_LINKEDIN_VERSION,
        "",
        "Publication request sent:",
        "no",
    ]
    if identity is not None and identity.error:
        lines.extend(["", "Identity:", identity.error])
    return "\n".join(lines) + "\n"


def render_status() -> str:
    lines = [
        "OAuth/token access bootstrap: implemented",
        "OIDC identity: implemented",
        "Person URN resolution: unresolved",
        "Dry-run: implemented",
        "Live publishing: explicit /li-publish only",
        f"API version status: {API_VERSION_STATUS}",
        f"Documented LinkedIn-Version: {DOCUMENTED_LINKEDIN_VERSION}",
        "Live API version verified: no",
        "Configured scopes: " + " ".join(CONFIGURED_SCOPES),
        "Effective token scopes: unknown until a token response provides them",
        "First-live transport: UGC_SELF_SERVICE",
        "ugcPosts production path: selected by the explicit publish command",
        "Posts API production path: implemented, used only when --transport POSTS_API is explicit",
        "OIDC: unverified",
        "Profile API capability: " + PROFILE_API_UNVERIFIED,
        "Person URN: unresolved",
        "Publishing: explicit single-post command",
    ]
    return "\n".join(lines) + "\n"


def _content_root(path: Path | None) -> Path:
    return path if path is not None else ROOT / "content"


def cmd_status() -> int:
    print(render_status(), end="")
    return 0


def cmd_identity(token: str | None, opener=None) -> int:
    if token is None:
        token = read_access_token()
    result = probe_identity(token, opener=opener)
    print(render_identity(result), end="")
    return 0


def cmd_profile_probe(token: str, opener=None) -> int:
    result = probe_profile(token, opener=opener)
    print(render_profile_probe(result), end="")
    return 0


def cmd_dry_run(
    post_id: str,
    content_root: Path,
    token: str | None,
    opener=None,
    prompt: bool = False,
    prompt_fn=None,
    environ: Mapping[str, str] | None = None,
    env_file: Path | None = None,
) -> int:
    if token is None:
        try:
            token = acquire_access_token(
                prompt_fn=prompt_fn,
                interactive=prompt,
                environ=environ,
                env_file=env_file,
            )
        except MissingToken:
            token = None
    result = dry_run(post_id, content_root, token=token, opener=opener)
    stream = sys.stdout if result.valid else sys.stderr
    print(render_dry_run(result), end="", file=stream)
    return 0 if result.valid else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Probe LinkedIn identity and dry-run an approved post.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="show the P4.1 boundary without calling LinkedIn")
    sub.add_parser("identity", help="read OIDC userinfo using LINKEDIN_ACCESS_TOKEN or a hidden prompt")
    sub.add_parser("profile-probe", help="check Profile API id access using LINKEDIN_ACCESS_TOKEN or a hidden prompt")
    dry = sub.add_parser("dry-run", help="validate one approved post and preview a publication request")
    dry.add_argument("post_id", help="POST-YYYYMMDD-NN in content/approved/")
    return parser


def main(
    argv: list[str] | None = None,
    *,
    prompt_fn=None,
    opener=None,
    content_root: Path | None = None,
    stdin_is_tty: bool | None = None,
    environ: Mapping[str, str] | None = None,
    env_file: Path | None = None,
) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except (OSError, ValueError):
            pass
    args = list(sys.argv[1:] if argv is None else argv)
    if any(arg.startswith("-") and arg not in {"-h", "--help"} for arg in args):
        print("Do not pass the access token as an argument.", file=sys.stderr)
        return 1
    try:
        parsed = build_parser().parse_args(args)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 1
    interactive = sys.stdin.isatty() if stdin_is_tty is None else stdin_is_tty
    try:
        if parsed.command == "status":
            return cmd_status()
        if parsed.command == "identity":
            token = acquire_access_token(
                prompt_fn=prompt_fn,
                interactive=interactive,
                environ=environ,
                env_file=env_file,
            )
            if token is None:
                raise MissingToken("No access token was provided.")
            return cmd_identity(token, opener=opener)
        if parsed.command == "profile-probe":
            token = acquire_access_token(
                prompt_fn=prompt_fn,
                interactive=interactive,
                environ=environ,
                env_file=env_file,
            )
            if token is None:
                raise MissingToken("No access token was provided.")
            return cmd_profile_probe(token, opener=opener)
        if parsed.command == "dry-run":
            return cmd_dry_run(
                parsed.post_id,
                _content_root(content_root),
                token=None,
                opener=opener,
                prompt=interactive,
                prompt_fn=prompt_fn,
                environ=environ,
                env_file=env_file,
            )
    except LinkedInAccessError as exc:
        print(exc, file=sys.stderr)
        return 1
    print("LinkedIn access probe failed.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
