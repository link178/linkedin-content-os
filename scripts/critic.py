"""Critic review persistence helpers. Stdlib only."""

from __future__ import annotations

import re
from dataclasses import dataclass

CRITIC_VERDICTS = frozenset({"PASS", "REVISE", "DROP"})


@dataclass
class CriticFindings:
    verdict: str
    blocking_issues: list[str]
    non_blocking_issues: list[str]
    revision_instructions: list[str]
    reason: str


def findings_from_payload(data: dict) -> CriticFindings:
    verdict = str(data.get("verdict", "")).strip().upper()
    if verdict not in CRITIC_VERDICTS:
        raise ValueError(f"critic verdict must be PASS, REVISE, or DROP, got {verdict!r}")
    blocking = [str(x).strip() for x in data.get("blocking_issues") or [] if str(x).strip()]
    non_blocking = [str(x).strip() for x in data.get("non_blocking_issues") or [] if str(x).strip()]
    instructions = [str(x).strip() for x in data.get("revision_instructions") or [] if str(x).strip()]
    reason = str(data.get("reason") or "").strip()
    if verdict == "PASS" and blocking:
        raise ValueError("critic PASS requires empty blocking_issues")
    if verdict == "REVISE" and not blocking and not instructions:
        raise ValueError("critic REVISE requires blocking issues or revision instructions")
    if verdict == "DROP" and not reason and not blocking:
        raise ValueError("critic DROP requires a reason or blocking issues")
    return CriticFindings(
        verdict=verdict,
        blocking_issues=blocking,
        non_blocking_issues=non_blocking,
        revision_instructions=instructions,
        reason=reason,
    )


def render_critic_section(findings: CriticFindings) -> str:
    lines = [
        "## Critic review",
        "",
        f"Verdict: {findings.verdict}",
        "",
        "blocking_issues:",
    ]
    if findings.blocking_issues:
        for item in findings.blocking_issues:
            lines.append(f"- {item}")
    else:
        lines.append("- none")
    lines.extend(["", "non_blocking_issues:"])
    if findings.non_blocking_issues:
        for item in findings.non_blocking_issues:
            lines.append(f"- {item}")
    else:
        lines.append("- none")
    lines.extend(["", "revision_instructions:"])
    if findings.revision_instructions:
        for item in findings.revision_instructions:
            lines.append(f"- {item}")
    else:
        lines.append("- none")
    lines.extend(["", "reason:", findings.reason or "None stated.", ""])
    return "\n".join(lines)


def remove_critic_section(rest: str) -> str:
    if "## Critic review" not in rest:
        return rest
    return re.sub(r"\n## Critic review\n.*?(?=^## |\Z)", "\n", rest, count=1, flags=re.MULTILINE | re.DOTALL)


def upsert_critic_section(rest: str, section: str) -> str:
    rest = remove_critic_section(rest)
    if not section.strip():
        return rest
    return rest.rstrip() + "\n\n" + section


def parse_critic_section(section: str) -> CriticFindings | None:
    if not section.strip():
        return None
    verdict = ""
    blocking: list[str] = []
    non_blocking: list[str] = []
    instructions: list[str] = []
    reason = ""
    mode = ""
    for line in section.splitlines():
        stripped = line.strip()
        if stripped.startswith("Verdict:"):
            verdict = stripped.split(":", 1)[1].strip().upper()
            continue
        if stripped.endswith(":") and stripped.lower().rstrip(":") in {
            "blocking_issues",
            "non_blocking_issues",
            "revision_instructions",
            "reason",
        }:
            mode = stripped.lower().rstrip(":")
            continue
        if stripped.startswith("- ") and mode == "blocking_issues":
            item = stripped[2:].strip()
            if item.lower() != "none":
                blocking.append(item)
        elif stripped.startswith("- ") and mode == "non_blocking_issues":
            item = stripped[2:].strip()
            if item.lower() != "none":
                non_blocking.append(item)
        elif stripped.startswith("- ") and mode == "revision_instructions":
            item = stripped[2:].strip()
            if item.lower() != "none":
                instructions.append(item)
        elif mode == "reason" and stripped and not stripped.startswith("-"):
            reason = stripped if not reason else reason + " " + stripped
    if not verdict:
        return None
    return CriticFindings(
        verdict=verdict,
        blocking_issues=blocking,
        non_blocking_issues=non_blocking,
        revision_instructions=instructions,
        reason=reason,
    )
