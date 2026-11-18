---
name: li-post
description: >-
  Drafts one grounded LinkedIn post from a local idea file and the proof
  registry. Invoke only when the user explicitly runs li-post. Mentioning
  LinkedIn is not a trigger.
disable-model-invocation: true
---

# li-post

Write one post under `content/drafts/`. Follow [the content contract](../../../docs/content-contract.md). Do not run `li-human` or `li-review` from here. Do not publish.

## Read first

- `docs/content-contract.md`
- `knowledge/guardrails.md`
- `knowledge/voice.md`
- `knowledge/positioning.md`
- the idea file the user named, and its plan file if they named one
- `python scripts/content_engine.py eligible --json` for the cited ids
- the source file for each cited claim
- `.cursor/skills/li-post/hooks.json`

If no idea id is given, ask for one. Do not draft from a topic that has no idea file.

## Draft

Pick three hook slugs that fit this evidence. Put them in `## Hooks considered` with one line each on why the evidence fits that shape. Choose one slug for the frontmatter `hook`.

Ignore example numbers, clients, and outcomes in `hooks.json`. Ignore character-count targets, link rules, hashtag caps, and posting times from the vendored skill. Line 1 still has to stand on its own, because that is a writing constraint.

`## Body` is the public text. One idea. No claim ids, no emoji, no "Thoughts?", no "Agree?", no "Follow me for more". Positioning words stay stance. They do not become metrics.

A reader who has never seen this repository, these skills, or the proof registry should read the body as an ordinary professional LinkedIn post. It should not read as an audit report, a compliance note, a provenance manifest, a test fixture, or a safety disclaimer.

`## Hooks considered`, `## Claims`, `scope`, and `## Evidence gaps` may name evidence limits. `## Body` may not.

### Argument

Compose an argument. The registry supports that argument from underneath. It does not set paragraph order, and the body is not a claim inventory.

Prefer posts with concrete observation, then contrast or decision, then technical meaning, then a derived lesson. Useful shapes include:

**Decision:** I tried X → observed Y → kept/rejected Z → lesson.

**Metric:** metric A looked strong → metric B exposed a weakness → explanation → lesson.

**Architecture trade-off:** problem → options → decision → trade-off.

**Failure / revision:** initial assumption → evidence against it → change made → lesson.

Do not force every post into one formula. Do not cite a personal claim that does not develop the thesis. General technical analysis does not need a personal anecdote to look grounded.

### What you may say

A project or personal fact maps to a `fact` claim. A derived conclusion maps to an `opinion` claim. When the distinction is material, speak the conclusion as the author's view: "My conclusion from those experiments is...".

You may combine verified facts, paraphrase them, connect a verified experience with a derived conclusion, and explain why the supported facts matter. Transitions and technical reasoning are allowed when they add no autobiographical or project fact.

A general technical explanation does not need a claim id. Use one only when every condition in the content contract's general-technical rule holds. It explains the concept. It does not add a fact, result, metric, implementation, or cause about the author or the project.

Allowed without a claim id:

> Mean Reciprocal Rank uses the reciprocal rank of the first relevant result.

Not allowed without an eligible project claim:

> RRF improved the Legal RAG Engine's MRR.

Do not build a project outcome out of a general explanation. "The project uses RRF" and "the evaluation reported MRR 0.75" do not support "RRF caused the MRR 0.75 result." General capability is not an observed project outcome.

You may not invent events, chronology, experiments, failures, attempted approaches, motivations, before/after stories, benchmark results, internal implementation details, user reactions, or business outcomes.

Allowed from the existing AI-assisted engineering evidence:

> After experimenting with context engineering and coding agents on legacy repositories, my conclusion is that more context is not automatically better.

Not allowed without further evidence:

> I started by loading entire repositories into the context window, but performance dropped.

The second sentence invents a specific experiment.

### Qualifications

Copy each qualification into `scope` exactly. Honor it in the body by meaning. Wording may change. Meaning may not.

State a qualification in public prose only when leaving it out would make the sentence materially misleading. The Legal RAG Hit Rate result keeps its nine-question evaluation scope. A withheld score is honored by not mentioning a score. Do not add a disclaimer only because the registry contains a qualification.

A missing personal or project fact goes under `## Evidence gaps` and stays out of the body.

When the body explains a general technical concept, sourced trend analysis, or other engineering prose without personal facts, `## Claims` must be exactly `- none` (not a blank section). A project fact in the same draft still gets its claim id. Dated vendor or docs sentences must match the idea's `why_now` and cited research note. The evidence section may mark the explanation, with no claim id:

```markdown
- general_technical_knowledge: "Mean Reciprocal Rank uses the reciprocal rank of the first relevant result."
```

The marker stays in `## Evidence gaps`. It does not make a project outcome publishable.

### Internal language

Bookkeeping stays internal. These phrases do not belong in `## Body` unless that concept is genuinely the subject of the post:

- proof registry
- eligible claim
- claim id
- evidence map
- public_safe
- verification status
- the evidence records
- no eligible claim
- no score is recorded
- does not record a score
- not as a benchmark score
- no benchmark score
- not a measured result
- this claim is derived
- this is allowed because
- blocked claim
- general_technical_knowledge

The bare word "evidence" is ordinary English. It is not on this list.

Status is `drafted`. Leave the review fields empty.

## Check

Allocate the id with `python scripts/content_engine.py next-id post`.

```text
python scripts/content_engine.py stamp content/drafts/POST-YYYYMMDD-NN.md
python scripts/content_engine.py check content/drafts/POST-YYYYMMDD-NN.md
```

Fix the artifact until `check` prints `ok`. Before that, read `## Body` against the internal-language list. Keep a listed phrase only when that concept is the subject of the post. Repair a hit by rewriting the argument, not by adding a disclaimer. Show the body and the claim ids. The next explicit step, if the author wants it, is `li-human`. Do not run `li-publish`.
