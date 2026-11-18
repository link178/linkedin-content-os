"""Validate knowledge/proof-registry.yaml against the P1 knowledge contract.

Stdlib only. The registry is restricted YAML: a `claims` list of flat mappings.
Free text is quoted. Ids, paths, categories, and true/false may be plain.
Run from anywhere:

    python scripts/validate_proof_registry.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "knowledge" / "proof-registry.yaml"
KNOWLEDGE = ROOT / "knowledge"

REQUIRED = ("id", "statement", "category", "source", "verification", "public_safe")
OPTIONAL = {"project", "qualification", "notes"}
VERIFICATION = {"verified", "derived", "unverified"}
CATEGORIES = {"profile", "career", "project", "metric", "opinion"}

PREFIX_PROJECT = {
    "LEGAL_RAG": "legal-rag-engine",
    "LEADFINDER": "leadfinder",
    "PORTFOLIO": "portfolio",
    "IGREADER": "igreader",
    "AI_DEV": "ai-assisted-software-engineering",
}
NO_PROJECT_PREFIXES = ("PROFILE", "CAREER_BACKEND", "CAREER_AI", "POSITIONING")
ID_RE = re.compile(
    r"^(PROFILE|CAREER_BACKEND|CAREER_AI|POSITIONING|LEGAL_RAG|LEADFINDER|PORTFOLIO|IGREADER|AI_DEV)_([0-9]{3})$"
)
MD_ID_RE = re.compile(
    r"\b(PROFILE|CAREER_BACKEND|CAREER_AI|POSITIONING|LEGAL_RAG|LEADFINDER|PORTFOLIO|IGREADER|AI_DEV)_[0-9]{3}\b"
)

errors: list[str] = []


def fail(message: str) -> None:
    errors.append(message)


def parse_scalar(raw: str, line_no: int):
    value = raw.strip()
    if value == "true":
        return True
    if value == "false":
        return False
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        inner = value[1:-1]
        if '"' in inner or "\\" in inner:
            fail(f"line {line_no}: registry strings do not use escapes")
        return inner
    if re.fullmatch(r"[A-Za-z0-9_./-]+", value):
        return value
    fail(f"line {line_no}: expected a quoted string, true/false, or a plain token, got {value!r}")
    return None


def parse_registry(text: str) -> list[dict]:
    claims: list[dict] = []
    current: dict | None = None
    saw_claims = False
    for line_no, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if line.startswith(" ") is False and stripped == "claims:":
            saw_claims = True
            continue
        item = re.fullmatch(r"  - ([A-Za-z_]+): (.*)", line)
        if item:
            if current is not None:
                claims.append(current)
            key, raw = item.group(1), item.group(2)
            current = {key: parse_scalar(raw, line_no)}
            continue
        field = re.fullmatch(r"    ([A-Za-z_]+): (.*)", line)
        if field:
            if current is None:
                fail(f"line {line_no}: field outside a claim")
                continue
            key = field.group(1)
            if key in current:
                fail(f"line {line_no}: duplicate field {key}")
            current[key] = parse_scalar(field.group(2), line_no)
            continue
        fail(f"line {line_no}: unsupported YAML syntax: {stripped}")
    if current is not None:
        claims.append(current)
    if not saw_claims:
        fail("missing top-level claims: key")
    return claims


def check_sequence(ids: list[str]) -> None:
    grouped: dict[str, list[int]] = {}
    for claim_id in ids:
        prefix, number = claim_id.rsplit("_", 1)
        grouped.setdefault(prefix, []).append(int(number))
    for prefix, numbers in grouped.items():
        expected = list(range(1, len(numbers) + 1))
        if sorted(numbers) != expected:
            fail(f"{prefix} ids must be 001..N without gaps, found {sorted(numbers)}")


def main() -> int:
    if not REGISTRY.is_file():
        print(f"missing {REGISTRY}", file=sys.stderr)
        return 1
    claims = parse_registry(REGISTRY.read_text(encoding="utf-8"))
    ids: list[str] = []
    for claim in claims:
        claim_id = claim.get("id")
        label = claim_id if isinstance(claim_id, str) else "<missing id>"
        unknown = set(claim) - set(REQUIRED) - OPTIONAL
        if unknown:
            fail(f"{label}: unknown fields {sorted(unknown)}")
        for key in REQUIRED:
            if key not in claim or claim[key] in ("", None):
                fail(f"{label}: missing {key}")
        if not isinstance(claim_id, str) or not ID_RE.fullmatch(claim_id):
            fail(f"{label}: id must be a known prefix plus three digits")
            continue
        ids.append(claim_id)
        prefix = claim_id.rsplit("_", 1)[0]
        if claim.get("verification") not in VERIFICATION:
            fail(f"{claim_id}: verification must be verified, derived, or unverified")
        if claim.get("category") not in CATEGORIES:
            fail(f"{claim_id}: unsupported category {claim.get('category')!r}")
        if not isinstance(claim.get("public_safe"), bool):
            fail(f"{claim_id}: public_safe must be true or false")
        if not isinstance(claim.get("statement"), str) or not claim["statement"].strip():
            fail(f"{claim_id}: statement must be a non-empty string")

        project = claim.get("project")
        if prefix in PREFIX_PROJECT:
            if project != PREFIX_PROJECT[prefix]:
                fail(f"{claim_id}: project must be {PREFIX_PROJECT[prefix]!r}")
        elif prefix in NO_PROJECT_PREFIXES:
            if "project" in claim:
                fail(f"{claim_id}: this prefix does not take a project field")
        else:
            fail(f"{claim_id}: no project rule for prefix {prefix}")

        source = claim.get("source")
        if not isinstance(source, str) or source.startswith(("/", "\\")) or ".." in source.split("/"):
            fail(f"{claim_id}: source must be a relative path under knowledge/")
            source_path = None
        else:
            source_path = ROOT / source
            if source_path.suffix.lower() not in {".md"} or not str(source).startswith("knowledge/"):
                fail(f"{claim_id}: source must be a markdown file under knowledge/")
            elif not source_path.is_file():
                fail(f"{claim_id}: source does not exist: {source}")
                source_path = None

        verification = claim.get("verification")
        if verification == "unverified" and claim.get("public_safe") is True:
            fail(f"{claim_id}: unverified claims must be public_safe false")
        if verification == "verified" and isinstance(source, str):
            allowed = source == "knowledge/profile.md" or source.startswith("knowledge/career/") or source.startswith("knowledge/projects/")
            if not allowed:
                fail(f"{claim_id}: verified claims must cite profile, career, or projects")
        if verification == "derived" and not (claim.get("notes") or claim.get("qualification")):
            fail(f"{claim_id}: derived claims need notes or a qualification")
        if claim.get("category") == "metric" and not claim.get("qualification"):
            fail(f"{claim_id}: metric claims need a qualification")
        if verification == "verified" and claim.get("category") == "metric" and not claim.get("qualification"):
            fail(f"{claim_id}: verified metrics need a qualification")

        if source_path is not None and isinstance(claim.get("statement"), str):
            text = source_path.read_text(encoding="utf-8")
            if claim["statement"] not in text:
                fail(f"{claim_id}: statement is not in {source}")
            qualification = claim.get("qualification")
            if isinstance(qualification, str) and qualification not in text:
                fail(f"{claim_id}: qualification is not in {source}")
            if verification == "verified" and "## Public-safe claims" not in text:
                fail(f"{claim_id}: source is missing a Public-safe claims section")
            if verification == "derived" and "## Derived claims" not in text:
                fail(f"{claim_id}: source is missing a Derived claims section")
            if verification == "unverified" and "## Unverified" not in text:
                fail(f"{claim_id}: source is missing an Unverified section")

    if len(ids) != len(set(ids)):
        fail("duplicate claim ids")
    check_sequence(ids)

    known = set(ids)
    if KNOWLEDGE.is_dir():
        for path in KNOWLEDGE.rglob("*.md"):
            text = path.read_text(encoding="utf-8")
            for found in MD_ID_RE.finditer(text):
                if found.group(0) not in known:
                    fail(f"{path.relative_to(ROOT)}: unknown claim id {found.group(0)}")

    counts = {name: 0 for name in ("verified", "derived", "unverified")}
    public = 0
    for claim in claims:
        status = claim.get("verification")
        if status in counts:
            counts[status] += 1
        if claim.get("public_safe") is True:
            public += 1

    if errors:
        for message in errors:
            print(message, file=sys.stderr)
        print(f"{len(errors)} error(s)", file=sys.stderr)
        return 1

    print(f"claims: {len(claims)}")
    print(f"verified: {counts['verified']}")
    print(f"derived: {counts['derived']}")
    print(f"unverified: {counts['unverified']}")
    print(f"public_safe: {public}")
    print(f"not_public_safe: {len(claims) - public}")
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
