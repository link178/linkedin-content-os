---
name: li-critic
description: >-
  Independent adversarial editorial pass for one draft post after editorial PASS.
  Invoke explicitly or via scripts/cycle.py. Does not approve or publish.
disable-model-invocation: true
---

# li-critic

Answer: **Would an experienced software engineer publish this, and would the intended reader gain a useful insight?**

This pass is separate from `/li-editorial`. Do not repeat the editorial verdict. Do not edit `## Body`.

## Detect

- Artificial LLM prose, registry language in public copy, defensive wording
- Generic engineering observations and obvious explanations sold as insights
- Weak thesis, weak opening, excess length, repetition
- Poor standalone reader value and unnecessary project references
- Topic duplication against recent local candidates when verifiable

## Verdicts

| Verdict | Meaning |
| --- | --- |
| `PASS` | No blocking issues |
| `REVISE` | Fixable blocking issues; provide revision_instructions |
| `DROP` | Abandon; insufficient thesis or substance |

## Persist (Python)

Headless runs use `scripts/cycle.py`, which writes:

- `critic_verdict`, `critic_body_sha256`, `critic_reviewed_at`
- `## Critic review` with blocking/non-blocking issues and instructions

Manual recording must bind to the current body hash. Critic `PASS` is not human approval.
