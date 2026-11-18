---
name: li-human
description: >-
  Cleans the body of one local LinkedIn draft for voice and invisible
  characters, then reports a style score that is not an approval gate.
  Invoke only when the user explicitly runs li-human. Mentioning LinkedIn
  is not a trigger.
disable-model-invocation: true
---

# li-human

Style-clean the `## Body` of one post, then stop. Follow [the content contract](../../../docs/content-contract.md). Do not publish, do not mark the post reviewed, and do not approve it.

## Read first

- `docs/content-contract.md`
- `knowledge/voice.md`
- `knowledge/guardrails.md`
- the post file the user named
- its `## Claims` scopes, so a cleanup cannot drop a qualification

If the user does not name a `POST-` id, ask for one. Edit the file in `content/drafts/` only.

If the post is in `content/published/`, stop before any edit. Published artifacts are immutable. Do not reopen them and do not run `li-publish` from this skill.

If the post is in `content/approved/`, stop before any edit. Do not run the style scripts on it, do not rewrite `## Body`, and do not stamp it. Say:

`POST-... is approved. Reopen it explicitly before editing.`

Reopening is `/li-reopen POST-...`. This skill does not reopen.

## Run the scripts

Work on the body text only. Do not pass the whole artifact through the scripts.

```text
python .cursor/skills/li-human/humanize.py body.txt --report
python .cursor/skills/li-human/detect.py body.txt
```

`humanize.py` deletes invisible format characters. It rewrites curly quotes. It keeps dashes. It does not treat a lexicon or structure hit as a factual error.

Rewrite a flagged line only when the claims, numbers, and qualifications still mean the same thing afterwards. If a rewrite would weaken a qualification, leave the sentence and say so.

`detect.py` prints five style scores and an advisory label: `HIGH`, `REVIEW`, or `FLAGGED`. They are local heuristics. They are not GPTZero, Originality, Copyleaks, Winston, or Turnitin. The label is not a content-review state. It cannot produce `PASS`, `NEEDS_EDIT`, or `BLOCKED`, and it cannot override the factual review. A score under 70 does not fail the draft and does not block review. Always show the cleaned body together with the score. Exit status is not a gate.

## Write back

Replace `## Body` with the cleaned text. Add or replace `## Style report` with the script summary and the line `Style signal only. Not an approval gate.`

Do not change `scope` values. Do not add facts.

```text
python scripts/content_engine.py stamp --invalidate --status styled PATH
python scripts/content_engine.py check content/drafts/POST-YYYYMMDD-NN.md
```

`PATH` is the draft you edited. `--invalidate` clears any previous review on that draft because a style edit is a body edit. Status becomes `styled`. It does not apply to `content/approved/`. Check the drafts path. Fix the file until `check` prints `ok`. A previous `PASS` does not survive this step. An approved post is unchanged, because this skill never opens it.

The next explicit step, if the author wants it, is `li-review`.
