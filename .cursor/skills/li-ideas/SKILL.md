---
name: li-ideas
description: >-
  Writes grounded LinkedIn idea files from content strategy, topics, and personal
  evidence. Invoke only when the user explicitly runs li-ideas. Mentioning
  LinkedIn is not a trigger.
disable-model-invocation: true
---

# li-ideas

Generate grounded content ideas and write them under `content/ideas/`. Follow [the content contract](../../../docs/content-contract.md). Do not draft the public post, do not publish, and do not approve.

## Read first

- `knowledge/content-strategy.md`
- `docs/content-contract.md`
- `knowledge/guardrails.md`
- `knowledge/positioning.md`
- `knowledge/voice.md`
- a strategy artifact if the user named one, otherwise `knowledge/content-strategy.md`
- `python scripts/content_engine.py eligible --json`
- the source file for every claim you might use
- existing `content/strategy/`, `content/ideas/`, `content/drafts/`, and `content/approved/`

`vendor/` is not evidence about the author. Do not treat posts outside this repository as known history.

## Input order

Prefer:

```text
content strategy
  ↓
topic
  ↓
personal evidence
  ↓
idea
```

Do not default to inspecting the proof registry and turning interesting claims into posts without strategy context.

## How many

Default to about five ideas. If the user names another count, use that. Stop early when further ideas would repeat a local artifact or rest on a weak claim. Quality outranks filling the count.

## What to prefer

Prefer topics with high standalone value for the intended audience, strong eligible evidence, a specific engineering insight, fit with positioning, concrete technical substance, novelty against local artifacts, and a real trade-off, failure, decision, or lesson.

Use project evidence as support for a reader-first point. The project is evidence, not the headline, unless the lane is `PROJECT_SHOWCASE` and the post introduces the project from zero context.

Use a `fact` claim as fact only with its qualification. Use a `derived` claim only as the author's conclusion. Skip `blocked` claims. If the interesting sentence has no eligible claim, record an evidence gap and do not write that sentence as the insight.

## Write each idea

Allocate ids with `python scripts/content_engine.py next-id idea`. One file per id, using the idea shape in the contract. Copy qualifications into `scope` exactly.

Required frontmatter for new ideas:

- `lane`
- `audience` — `BROAD_TECH`, `AI_ENGINEERING`, or `SPECIALIST`
- `standalone_value` — `HIGH`, `MEDIUM`, or `LOW`
- `project_context_required` — `NONE`, `LIGHT`, or `HIGH`
- `technical_depth` — `ACCESSIBLE`, `INTERMEDIATE`, or `DEEP`
- `why_now`
- `why_this_author`

`## Why it fits` may use positioning language only as stance. Answer why the reader should care without using a project name as the reason.

`## Local overlap` names the existing artifact ids that cover similar ground, or says that no local ideas, drafts, or approved posts cover it.

Optional `editorial_potential: HIGH | MEDIUM | LOW` from evidence already present.

Run `python scripts/content_engine.py check` on each new file and fix it until the checker prints `ok`.

Show the author the id, the insight, lane, audience, and claim ids. Do not turn an idea into a post in this skill. Do not run `li-publish`.
