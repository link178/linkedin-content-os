"""Offline demonstration of the real validation gates.

Uses the synthetic public corpus. Does not call a model, LinkedIn, or the network.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import content_engine as engine  # noqa: E402
import cycle as cycle_mod  # noqa: E402

HIT = "In a nine-question retrieval evaluation, the system achieved a Hit Rate of 1.0."
CAUSE = "RRF caused the MRR 0.75 result."


def main() -> int:
    claims = engine.load_claims()
    by_id = {claim["id"]: claim for claim in claims}
    supported, supported_reasons = engine.review_body(
        HIT, [{"id": "LEGAL_RAG_007", "use": "fact"}], claims
    )
    inflated, inflated_reasons = engine.review_body(
        "The RAG system achieved perfect retrieval.",
        [{"id": "LEGAL_RAG_007", "use": "fact"}],
        claims,
    )
    caused, caused_reasons = engine.review_body(
        CAUSE,
        [{"id": "LEGAL_RAG_005", "use": "fact"}, {"id": "LEGAL_RAG_008", "use": "fact"}],
        claims,
    )
    ready, ready_reasons = cycle_mod.readiness_report(ROOT / "content", "POST-20261113-02")
    print("supported_claim", supported)
    print("inflated_metric", inflated)
    print("unsupported_cause", caused)
    print("blocked_draft_ready", ready)
    print("human_publication", "not invoked")
    problems = []
    if supported != "PASS":
        problems.append(f"supported claim did not pass: {supported_reasons}")
    if inflated != "BLOCKED":
        problems.append(f"inflated metric was not blocked: {inflated_reasons}")
    if caused != "BLOCKED":
        problems.append(f"unsupported cause was not blocked: {caused_reasons}")
    if ready:
        problems.append("blocked draft was reported ready")
    if not any("unsupported claim" in reason for reason in ready_reasons):
        problems.append(f"readiness reasons missing unsupported claim: {ready_reasons}")
    if by_id["CAREER_AI_011"] and engine.classify(by_id["CAREER_AI_011"]) != "blocked":
        problems.append("unverified claim was eligible")
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1
    print("demo_ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
