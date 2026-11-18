---
name: li-plan
description: >-
  Turns selected local idea files into a short content plan with no calendar
  and no publishing step. Invoke only when the user explicitly runs li-plan.
  Mentioning LinkedIn is not a trigger.
disable-model-invocation: true
---

# li-plan

Write one plan under `content/plans/` from idea files that already exist. Follow [the content contract](../../../docs/content-contract.md).

## Read first

- `docs/content-contract.md`
- `knowledge/guardrails.md`
- `knowledge/positioning.md`
- the idea files the user named
- `content/drafts/` and `content/approved/`, so the plan does not repeat a post already drafted or approved
- `.cursor/skills/li-post/hooks.json` for shape slugs only

If the user does not name ideas, list the `proposed` ideas and ask which ones to include. Do not invent a week of topics to fill a quota.

## What a plan is

A short ordered list. Each slot has an existing idea id, one angle, and one hook slug that fits that evidence.

Do not assign a weekday, a clock time, a posting frequency, or an engagement list. Upstream advice of that kind is a hypothesis recorded in `docs/upstream-audit.md`, not a rule. The plan body includes the sentence `No posting time is assigned.`

Do not treat hook `fit` text or upstream examples as performance data or as facts about the author.

## Write the plan

Allocate the id with `python scripts/content_engine.py next-id plan`. Use the plan shape in the contract. Status stays `proposed`. A plan does not approve its ideas.

Run `python scripts/content_engine.py check` on the new file and fix it until the checker prints `ok`.

Show the slots to the author. Drafting a slot is a separate explicit `li-post` invocation. Do not run `li-publish`.
