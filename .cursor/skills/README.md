# Cursor Agent Skills

The content skills are explicit. Each directory has a `SKILL.md` with `disable-model-invocation: true`. Mentioning LinkedIn does not run a skill. The user names it.

The skills read this repository. Only `li-publish` calls LinkedIn, and only when the user names one approved post id. Shared claim, artifact, hash, and review rules are in [`docs/content-contract.md`](../../docs/content-contract.md).

The vendored Claude skills under `vendor/linkedin-agent-skill/skills/` are reference material. They are not copied into this folder as the implementation. `li-human` and `li-post` adapt specific upstream files and say so in those directories.

## Knowledge contract

| Skill input | Rule |
| --- | --- |
| `knowledge/guardrails.md` | Wins on conflict. |
| `knowledge/proof-registry.yaml` | `fact` claims are `verified` and `public_safe: true`, with the qualification kept. `opinion` claims are `derived` and `public_safe: true`. `unverified` and `public_safe: false` stay out of public copy. |
| Source file for the claim | Evidence text. The registry is the index. |
| `knowledge/content-strategy.md` | Topic lanes, audience, standalone value. Not evidence. |
| `knowledge/positioning.md` | Which stance to take. Not evidence. |
| `knowledge/voice.md` | Diction and tone. Not evidence. |

`python scripts/content_engine.py eligible` classifies the registry. `python scripts/content_engine.py check` checks an artifact. `python scripts/content_engine.py approve POST-...` is the only command that records human approval. `python scripts/content_engine.py reopen POST-...` is the only command that returns an approved post to drafts. `check` and `review` do neither.

## Skills

| Skill | Job |
| --- | --- |
| `li-strategy` | Plan topics from audience value; write `content/strategy/` artifacts. |
| `li-trends` | Record sourced trend research under `research/trends/`. |
| `li-ideas` | Write grounded ideas under `content/ideas/` from strategy and evidence. |
| `li-plan` | Turn selected ideas into a short plan with no calendar. |
| `li-post` | Draft one post from an idea. Cite the registry. |
| `li-human` | Clean that draft's body and report a style score. The score is not a gate. |
| `li-review` | Record semantic `PASS`, `NEEDS_EDIT`, or `BLOCKED` for facts, privacy, and voice. |
| `li-editorial` | Record editorial `PASS`, `REVISE`, or `EVIDENCE_TOO_THIN` including standalone value. Independent from semantic review. |
| `li-critic` | Independent adversarial editorial pass (`PASS`, `REVISE`, `DROP`). Bound to body hash. |
| `li-visual` | Optional local SVG when information gain is `ADDITIVE`. `NONE` is valid. |
| `li-cycle` | Optional wrapper around `python scripts/cycle.py` (same as Inbox Generate). |
| `li-preview` | Local HTML preview of the exact body and optional visual before approval. |
| `li-approve` | Record explicit human approval after semantic PASS, editorial PASS, and a current preview. |
| `li-reopen` | Return one approved post to `content/drafts/` so it can be edited. Does not edit the body. |
| `li-publish` | Publish exactly one approved text post. The invocation names the post id. |

Workflow:

```text
/li-strategy → /li-ideas → /li-plan → /li-post → /li-human → /li-review → /li-editorial → /li-critic
  → optional /li-visual → /li-preview → (Inbox or /li-approve) → /li-publish

Headless default: `python scripts/inbox.py` → Generate Candidate → inspect → APPROVE / REVISE / DROP.
```

Optional `/li-trends` feeds strategy. Each step is a separate explicit invocation. The style score is not a gate. Editorial `PASS` is not approval. An approved post is edited only after `/li-reopen POST-...`, then human/style, semantic review, editorial, optional visual, preview, and approve again. A published post is not edited.

## Later skills

| Skill | Phase | Job |
| --- | --- | --- |
| `li-audit` | P8 | Read imported analytics and propose strategy changes. |

`li-review` is factual, privacy, and voice validation before any later approval. `li-audit` reads performance after publication. Those stay separate skills.

## Explicitly not in this engine

Upstream `li-comment`, `li-reply`, `li-dm`, `li-inbox`, `li-profile`, `li-carousel`, and `li-repurpose` are classified in [`docs/upstream-audit.md`](../../docs/upstream-audit.md). They are not part of the content engine.
