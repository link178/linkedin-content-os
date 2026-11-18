---
name: li-strategy
description: >-
  Selects and records content topics from audience value and positioning before
  idea generation. Invoke only when the user explicitly runs li-strategy.
  Produces strategy artifacts, not posts.
disable-model-invocation: true
---

# li-strategy

Plan what should be discussed before generating post ideas. Read [`knowledge/content-strategy.md`](../../../knowledge/content-strategy.md). Do not draft public posts, do not publish, and do not approve.

## Invocation

- `/li-strategy` — propose a balanced next batch (default about five topics).
- `/li-strategy next 10 topics` — use the count the user names.

## Read first

- `knowledge/content-strategy.md`
- `knowledge/positioning.md`
- `knowledge/guardrails.md`
- `python scripts/content_engine.py eligible --json`
- Existing `content/strategy/`, `content/ideas/`, `content/drafts/`, and `content/approved/`
- Optional `research/trends/` for `TREND_REACTION` candidates with sourced developments

Prefer strategy-first input order:

```text
content strategy
  ↓
topic
  ↓
personal evidence
  ↓
(later) idea
```

Do not default to turning every proof-registry claim into a post.

## Write the artifact

Allocate id with `python scripts/content_engine.py next-id strategy`. Write under `content/strategy/` as `STRATEGY-YYYYMMDD-NN.md`. Status stays `proposed`.

Each topic is one record under `## Topics` with:

- `topic` — short title
- `lane` — `POSITIONING`, `TREND_REACTION`, `PROJECT_SHOWCASE`, `TECHNICAL_DEEP_DIVE`, or `CAREER_ENGINEERING`
- `audience` — `BROAD_TECH`, `AI_ENGINEERING`, or `SPECIALIST`
- `timeliness` — `EVERGREEN`, `CURRENT`, or `TIME_SENSITIVE`
- `standalone_value` — `HIGH`, `MEDIUM`, or `LOW`
- `personal_evidence_strength` — `HIGH`, `MEDIUM`, `LOW`, or `NONE`
- `project_context_required` — `NONE`, `LIGHT`, or `HIGH`
- `technical_depth` — `ACCESSIBLE`, `INTERMEDIATE`, or `DEEP`
- `visual_potential` — `ADDITIVE`, `DECORATIVE`, or `NONE`
- `why_now`
- `why_this_author`
- `why_reader_cares` — no project name as the reason

Do not label a retrieval-metric lesson as `POSITIONING` / `BROAD_TECH`. If the author evidence is a scoped metric result, use `TECHNICAL_DEEP_DIVE` with `AI_ENGINEERING` or `SPECIALIST`. A hiring-lead sentence does not change that subject. RAG and MCP remain valid for the audience that can use them.

Optional frontmatter `concentration_risk: true` when three or more consecutive `SPECIALIST` topics appear or specialist share is well above the soft 15% mix. The checker validates this against the topic list.

Include `## Mix notes` with lane counts and whether the soft default mix was approximated. Do not fill weak slots merely to hit percentages.

For trend topics, cite a row in `research/trends/` or record source URL, publication date, and retrieval date in `why_now`. If browsing is unavailable, stop and ask the user for external sources. Do not invent trends from model memory.

A `CURRENT` topic needs a development date in `why_now`. Headless editorial selection rejects it as `STALE` after `stale_after_days` in `config/editorial-discovery.json`, or as `UNDATED` without a date. Strategy topics are candidates there, not a queue: they compete with fresh research on editorial relevance.

Run `python scripts/content_engine.py check` on the new file until it prints `ok`.

Show the artifact id and a one-line summary per topic. Idea generation is a separate `/li-ideas` invocation.
