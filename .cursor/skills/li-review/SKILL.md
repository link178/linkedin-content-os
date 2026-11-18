---
name: li-review
description: >-
  Reviews one styled local LinkedIn draft for facts, confidentiality, and
  voice, and records PASS, NEEDS_EDIT, or BLOCKED without approving
  publication. Invoke only when the user explicitly runs li-review.
  Mentioning LinkedIn is not a trigger.
disable-model-invocation: true
---

# li-review

Record a verdict on one styled post. Follow [the content contract](../../../docs/content-contract.md). Do not rewrite `## Body`. Do not publish. Do not set approval fields, and do not move the file to `content/approved/`. `PASS` is not permission to post and not human approval.

## Read first

- `docs/content-contract.md`
- `knowledge/guardrails.md`
- `knowledge/voice.md`
- `knowledge/positioning.md`
- the post file the user named
- `python scripts/content_engine.py eligible --json`
- the source file for every cited claim

The post must already be `styled` and must have a `## Style report`. If it is still `drafted`, stop and ask for `li-human` first.

If the post is in `content/published/`, stop. Published artifacts are immutable.

If the post is in `content/approved/`, stop. Do not edit it and do not record a new verdict on it. Say `POST-... is approved. Reopen it explicitly before editing.`

## Judge the body

Run:

```text
python scripts/content_engine.py review content/drafts/POST-YYYYMMDD-NN.md
```

The command prints `PASS`, `NEEDS_EDIT`, or `BLOCKED`. A style score is not an input. It cannot produce those states and it cannot override this judgment.

The script does not understand every paraphrase. Read `## Body` yourself.

Classify each material sentence as one of:

- project or personal fact
- derived personal conclusion
- general technical explanation
- author interpretation (`I treat … as` on a public system, not a project outcome)
- external factual attribution (dated vendor or docs lines; must appear in topic research)

A general technical explanation does not need a proof-registry claim. The content contract lists the six conditions. Stable definitions are in that class:

> Mean Reciprocal Rank uses the reciprocal rank of the first relevant result.

Project-specific wording is not in that class. It needs an eligible claim:

> Our MRR improved because relevant documents moved closer to rank 1.

Do not launder evidence. A use fact plus a metric fact plus a general capability do not become a cause. "The project uses RRF" and "MRR 0.75" do not support "RRF caused the MRR 0.75 result." If the command misses that paraphrase, the verdict is still `BLOCKED`.

`## Evidence gaps` may contain `general_technical_knowledge` lines with no claim id. The marker does not grant eligibility. If the quoted sentence is a project fact, a project outcome, or a derived conclusion, do not treat it as general knowledge.

`BLOCKED` when the public wording depends on something that must not be published:

- a factual career, project, or metric sentence with no eligible `fact` claim
- a project outcome or causal explanation that no single eligible fact claim states
- a `fact` claim whose qualification is dropped or widened, including "100% accuracy" for the nine-question Hit Rate
- a `derived` claim written as measured proof, including "benchmarks prove" or "always better"
- unsupported years of AI experience, including "more than ten years working in AI"
- unsupported commercial success, including "paying customers"
- a `blocked` claim, or a paraphrase of one
- an employer, client, repository, or other identifier `knowledge/guardrails.md` forbids
- a personal project described as client work, a commercial product, or a production deployment the evidence does not support
- invented customer evidence, ownership, leadership, or production scale

Name the missing evidence. Do not invent a safe replacement premise.

`NEEDS_EDIT` when the evidence is sufficient and the wording can be corrected without changing that evidence:

- weak framing, or a derived sentence that is not spoken as the author's conclusion
- generic AI language, or an unnecessary "Thoughts?"
- a qualification that needs clearer wording while the underlying claim stays usable
- a voice mismatch in `knowledge/voice.md`

A conclusion frame on a different sentence does not frame this one. Name the sentence, the claim id when there is one, what exceeds the evidence, whether wording can repair it, and the minimal correction. Contrast, hook, readability, repetition, and presentation are editorial. They are not a semantic `NEEDS_EDIT` unless the sentence also breaks this factual contract.

`PASS` only when the current body is sufficiently grounded, uses only allowed factual claims for project and personal facts, preserves qualifications, frames derived claims correctly, treats general technical explanation as explanation rather than project evidence, complies with confidentiality, and has no material misleading wording.

If your own reading finds a wording or evidence problem that the command missed, record `NEEDS_EDIT` or `BLOCKED`. Do not record a weaker state than the command printed.

## Record the verdict

Leave the body bytes unchanged so the hash stays valid.

Set `status: reviewed`. Set `review_verdict` to the state above. Set `review_body_sha256` to the current `body_sha256`. Write `## Review` with the findings.

```text
python scripts/content_engine.py check content/drafts/POST-YYYYMMDD-NN.md
```

If `check` fails, the verdict is not current. Fix the frontmatter or the claims table without changing the body. Do not leave `PASS` on a body the checker rejects.

Show the verdict and the findings. `PASS` does not approve the post. Approval is a later explicit `/li-approve POST-YYYYMMDD-NN` invocation. Do not run `li-approve` from this skill. Do not run `li-publish`.
