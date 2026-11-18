# Content contract

Shared rules for `li-ideas`, `li-plan`, `li-post`, `li-human`, `li-review`, `li-approve`, and `li-reopen`. Skill files point here instead of copying these rules.

The engine writes local artifacts under `content/`. It does not publish, schedule, call LinkedIn, or store analytics. `li-approve` records human approval only when the user invokes it for one post. A review `PASS` is not that approval.

`vendor/` is not evidence about the author. It may supply hook shapes and the adapted style scripts only.

## Precedence

On conflict, use this order:

1. `knowledge/guardrails.md`
2. `knowledge/proof-registry.yaml` (`verification`, `public_safe`, `qualification`)
3. The claim's source file under `knowledge/`
4. `knowledge/positioning.md`
5. `knowledge/voice.md`

Guardrails win. Positioning and voice never turn a sentence into a fact. A `verified` flag does not override a confidentiality rule.

## Claim eligibility

`scripts/content_engine.py eligible` classifies the registry. Skills use that output and still read `knowledge/guardrails.md`.

| Registry | Public copy |
| --- | --- |
| `verification: verified` and `public_safe: true` | May be stated as fact. Keep the qualification's restriction. |
| `verification: derived` and `public_safe: true` | Author's conclusion, opinion, interpretation, recommendation, or lesson. Not measured evidence. |
| `verification: unverified` | Blocked. Do not publish. Do not paraphrase into a softer claim. |
| `public_safe: false` | Blocked, whatever the verification is. Read it only to understand a limit. |

A personal or project factual sentence with no eligible registry claim is omitted or recorded as an evidence gap. Do not soften it, infer it, approximate it, or invent a plausible version.

### Qualifications

The restriction stays intact. Wording may change. Meaning may not.

Allowed:

> In a nine-question retrieval evaluation, the system achieved a Hit Rate of 1.0.

Not allowed:

> The RAG system achieved perfect retrieval.

For a fact claim that has a `qualification`, the artifact `scope` must be that qualification copied exactly. `li-review` then checks that the public body still honors it. Honoring it means the public sentence does not drop or widen the restriction. Repeat the restriction in the body only when leaving it out would make the sentence materially misleading. A nine-question Hit Rate stays scoped to those nine questions. A withheld score is honored by not stating a score. Registry bookkeeping stays out of the public body.

### Derived framing

Name the sentence as the author's view. These are acceptable frames: "My conclusion is", "I treat this as", "The lesson I take is". The frame marks the author's view. It does not need a disclaimer that the sentence is not a measurement. The frame belongs on that derived sentence. A frame later in the post does not turn an earlier bare assertion into the author's conclusion. The script checks that some frame is present once an opinion is cited. It does not locate every derived sentence. `li-review` still judges each derived sentence.

Not acceptable:

> Benchmarks prove that more context makes agents worse.

when the supporting entry is `derived`.

### Missing evidence

Record the gap in the artifact's `Evidence gaps` section. Leave it out of the public body. A gap is not a draft sentence.

## Statement classes

`li-review` classifies each material public statement into one of four kinds. The proof registry remains the index of personal and project claims. It does not become a textbook.

| Class | What it is | Registry |
| --- | --- | --- |
| Project or personal fact | A factual assertion about the author, career, project, experiment, implementation, architecture actually used, result, metric observed, action taken, chronology, customer, employer or client, or benchmark performed. | One eligible claim: `verification: verified` and `public_safe: true`. |
| Derived personal conclusion | The author's conclusion, opinion, interpretation, recommendation, or engineering lesson, when it comes from personal or project experience. | One eligible claim: `verification: derived` and `public_safe: true`. Frame it as the author's view. |
| General technical explanation | An established explanation of a technical concept. | No proof-registry claim. No claim id. |
| Author interpretation (non-personal) | A framed engineering judgment about a public system, such as `I treat … as` when it is not a measured project result. | No proof-registry claim when the post has no personal facts. A linguistic frame alone does not establish evidence type; substance after the frame does. |
| External factual attribution | A dated vendor release, changelog line, or explicit docs attribution in the body. | No proof-registry claim. The idea's `why_now` and the cited `research/trends/…` note must cover the sentence. A URL is not verification. |

A blank `## Claims` section is invalid. When no personal claim is cited, write exactly:

```markdown
## Claims

- none
```

That token is not the same as a missing section.

### General technical knowledge

A sentence may be published as general technical knowledge only when all of these are true:

1. It explains a general technical concept.
2. It does not introduce a new fact about the author or the project.
3. It does not claim that a project implemented, tested, observed, or proved something.
4. It does not introduce a new project result or metric.
5. It does not create a causal explanation for a project result.
6. It is reasonably stable technical knowledge, not a disputed or time-sensitive claim.

Allowed:

> Hit Rate@5 asks whether a relevant item appears within the top five results.
>
> Mean Reciprocal Rank uses the reciprocal rank of the first relevant result.
>
> RRF combines ranked result lists using reciprocal rank-based scores.
>
> Kafka preserves record ordering within a partition.

Not allowed without an eligible project claim:

> RRF improved the Legal RAG Engine's MRR.
>
> Increasing Kafka partitions fixed our throughput problem.
>
> Extra context reduced agent accuracy by 20%.
>
> We chose sparse retrieval because dense retrieval failed.

The same topic becomes evidence-required as soon as the wording is about this author or this project.

Allowed as a general explanation:

> MRR uses the reciprocal rank of the first relevant result.

An engineering analysis may use the same class when it adds no fact about the author or a project. It does not have to be a one-sentence definition. Copying a registry statement into the body is not that analysis. An eligible personal claim is not automatically relevant to the thesis. Cite one only when it develops the argument. A trend or a general engineering point may be the whole post.

Evidence-required:

> Our MRR improved because relevant documents moved closer to rank 1.

### No personal evidence laundering

General technical knowledge never bridges an evidence gap. A project fact and a general capability do not combine into an observed outcome.

This chain is not allowed:

1. The project uses RRF.
2. General knowledge says RRF can improve fusion.
3. Therefore RRF improved this project's retrieval.

`general capability != observed project outcome`

A causal sentence such as "RRF caused the MRR 0.75 result" needs one eligible fact claim that states that outcome. Citing "the project uses RRF" and "the evaluation reported MRR 0.75" does not state that cause. The checker does not infer it. `li-review` does not infer it either.

## What the checker enforces

`python scripts/content_engine.py check PATH` is the mechanical gate:

- identifier, filename, kind, status, and date agree
- cited ids exist and match `fact` or `opinion`
- metric facts still contain the statement's numbers in the body
- prohibition phrases in a qualification (`Do not generalize to ...`) do not appear in the body
- a short phrase list taken from `knowledge/guardrails.md` and `knowledge/voice.md` does not appear in the body
- post `body_sha256` matches the body
- a review is current only when its hash matches the body
- `review_verdict` is `PASS`, `NEEDS_EDIT`, or `BLOCKED`
- a recorded `PASS` or `NEEDS_EDIT` is rejected when the body is `BLOCKED`
- a structurally valid post with `status: reviewed`, `review_verdict: BLOCKED`, and a current `review_body_sha256` may keep the body that produced `BLOCKED` for audit; that waiver does not make it ready for human approval or publication
- a recorded `PASS` is rejected when the body `NEEDS_EDIT`
- a clear project outcome or causal explanation is `BLOCKED` unless one cited fact claim states that outcome
- a body that only explains general technical knowledge may pass with no claim id
- approval fields are set only when `status` is `approved`
- an approved post lives in `content/approved/`, and `body_sha256`, `review_body_sha256`, and `approved_body_sha256` all equal the current body
- `approved_at` is UTC `YYYY-MM-DDTHH:MM:SSZ`

The outcome check looks at one claim at a time. It does not treat two claims as a cause. It does not understand every paraphrase. `li-review` still classifies the body.

`python scripts/content_engine.py review PATH` prints that verdict for the current body. It catches the material and voice patterns below. It does not understand every paraphrase. `li-review` still reads the body against the registry and the guardrails. A `PASS` is not publication approval.

Material patterns make the verdict `BLOCKED`. The public wording depends on something that must not be published:

- perfect retrieval
- 100% accurate
- 100% accuracy
- production-grade accuracy
- ten years of AI experience, 10 years of AI experience, or more than ten years working in AI
- paying customers
- "benchmarks prove" or "always better" used as a universal factual premise

Voice patterns make the verdict `NEEDS_EDIT` when the evidence itself is eligible:

- Thoughts?
- Agree?
- Follow me for more
- I couldn't believe what happened next
- a pattern of `N lessons from`

A style score is not one of these patterns. It cannot produce `PASS` or `BLOCKED`, and it cannot override this verdict.

## Artifacts

One markdown file per artifact. No database and no home-directory state.

| Kind | Path | Id |
| --- | --- | --- |
| strategy | `content/strategy/STRATEGY-YYYYMMDD-NN.md` | `STRATEGY-YYYYMMDD-NN` |
| idea | `content/ideas/IDEA-YYYYMMDD-NN.md` | `IDEA-YYYYMMDD-NN` |
| plan | `content/plans/PLAN-YYYYMMDD-NN.md` | `PLAN-YYYYMMDD-NN` |
| post | `content/drafts/POST-YYYYMMDD-NN.md` | `POST-YYYYMMDD-NN` |
| approved post | `content/approved/POST-YYYYMMDD-NN.md` | the same `POST-YYYYMMDD-NN` |

`NN` is two digits starting at `01`. The next number is one higher than the highest file of that kind and date already on disk. For a post, that scan includes `content/drafts/`, `content/approved/`, and `content/published/` if a later phase adds that directory. Allocate it with:

```text
python scripts/content_engine.py next-id strategy
python scripts/content_engine.py next-id idea
python scripts/content_engine.py next-id plan
python scripts/content_engine.py next-id post
```

`created` is `YYYY-MM-DD` for that same day.

### Lifecycle

| Kind | Status | Meaning |
| --- | --- | --- |
| idea, plan, strategy | `proposed` | Written for the author to accept or ignore. |
| idea, plan, strategy | `withdrawn` | Kept so later ideas can see it. Not a candidate. |
| post | `drafted` | `li-post` wrote the body. Style pass has not run. |
| post | `styled` | `li-human` has run on the current body. |
| post | `reviewed` | `li-review` recorded `PASS`, `NEEDS_EDIT`, or `BLOCKED` on the current body hash. |
| post | `approved` | `li-approve` bound an explicit human approval to this exact body. The only copy is `content/approved/`. |
| post | `published` | `/li-publish` recorded one LinkedIn create for this exact body. The only copy is `content/published/`. |

P3 does not publish. A reviewed post stays in `content/drafts/` until the author runs `li-approve` for that id. There is no `rejected` status: skipping approval leaves the post reviewed. `published` is written only by an explicit `/li-publish` after LinkedIn accepts the create.

```text
STRATEGY (content-strategy.md + STRATEGY artifacts)
  ↓
IDEA → plan → drafted → styled → reviewed → review PASS
  → editorial review → editorial PASS
  → optional visual (ADDITIVE information gain only by default)
  → preview
  → explicit human approval → approved
  → explicit /li-publish → published
```

Technical specificity ≠ reader relevance. Topic selection is strategy-first; the proof registry supplies evidence, not the default headline.

`SEMANTIC PASS` ≠ `EDITORIAL PASS` ≠ `PREVIEWED` ≠ `APPROVED` ≠ `PUBLISHED`.

`review_verdict` is exactly `PASS`, `NEEDS_EDIT`, or `BLOCKED`. Semantic `PASS` means grounded and public-safe. It does not mean the post is good, approved, or publishable.

Editorial verdicts are `PASS`, `REVISE`, and `EVIDENCE_TOO_THIN`. See [editorial-contract.md](editorial-contract.md).

Critic verdicts (P6) are `PASS`, `REVISE`, and `DROP`. See [cycle-contract.md](cycle-contract.md). Critic metadata binds to the same body hash as semantic and editorial review.

Post metadata may include `editorial_verdict`, `editorial_body_sha256`, `editorial_reviewed_at`, `critic_verdict`, `critic_body_sha256`, `critic_reviewed_at`, `human_editorial_decision`, `preview_body_sha256`, `preview_visual_sha256`, `preview_generated_at`, and `approved_visual_sha256`. Posts published before P5 may omit editorial and preview fields; posts without critic fields remain valid historical records.

### Strategy shape

Strategy artifacts live under `content/strategy/`. Each topic under `## Topics` records lane, audience, timeliness, standalone value, evidence strength, project context, technical depth, visual potential, and why now / why this author / why the reader should care. See [`knowledge/content-strategy.md`](../knowledge/content-strategy.md).

### Idea shape

```markdown
---
id: IDEA-YYYYMMDD-NN
kind: idea
status: proposed
created: YYYY-MM-DD
project: legal-rag-engine
lane: POSITIONING
audience: BROAD_TECH
standalone_value: HIGH
project_context_required: LIGHT
technical_depth: ACCESSIBLE
why_now: ...
why_this_author: ...
---

## Insight

## Why it fits

## Claims

- id: LEGAL_RAG_007
  use: fact
  scope: "exact qualification from the registry"

## Evidence gaps

- none

## Local overlap
```

`use` is `fact` or `opinion`. Omit `scope` when the registry entry has no qualification.

### Plan shape

```markdown
---
id: PLAN-YYYYMMDD-NN
kind: plan
status: proposed
created: YYYY-MM-DD
---

## Slots

- order: 1
  idea: IDEA-YYYYMMDD-NN
  hook: receipt
  angle: "one evidence-backed angle"

## Not scheduled

No posting time is assigned.
```

A plan is a short ordered list of existing ideas. It has no weekly quota, no clock time, and no engagement list. Those upstream defaults are hypotheses, not rules.

### Post shape

```markdown
---
id: POST-YYYYMMDD-NN
kind: post
status: drafted
created: YYYY-MM-DD
idea: IDEA-YYYYMMDD-NN
plan: PLAN-YYYYMMDD-NN
hook: receipt
body_sha256:
review_verdict:
review_body_sha256:
approval_status:
approved_body_sha256:
approved_at:
---

## Hooks considered

## Body

Public text only. No claim ids.

## Claims

- id: LEGAL_RAG_007
  use: fact
  scope: "exact qualification from the registry"

## Evidence gaps

- none

## Style report

## Review
```

`## Body` is the only text that could be pasted later. Claim ids stay in `## Claims`. `## Style report` and `## Review` are internal.

Leave `approval_status`, `approved_body_sha256`, and `approved_at` empty until `li-approve` writes them. Drafts that omit those keys are valid. `li-post` and `li-review` do not fill them.

`## Claims` may be `- none` when every material sentence in `## Body` is a general technical explanation. A personal or project fact still needs its claim id.

`## Evidence gaps` may mark an explanatory sentence without a claim id:

```markdown
- general_technical_knowledge: "Mean Reciprocal Rank uses the reciprocal rank of the first relevant result."
```

That marker is bookkeeping. It is not a registry claim, and it cannot cover a project fact, a project outcome, or a derived conclusion. The quoted sentence belongs in the body only as ordinary prose. The marker itself stays out of `## Body`.

`hook` is a slug from `.cursor/skills/li-post/hooks.json`. The catalog's examples are shape illustrations. They are not facts about the author, and their numbers must not be copied into a draft.

## Body hash

`body_sha256` is SHA-256 over the exact public post body, the `## Body` section only, after this normalization:

- newlines become `\n`
- trailing whitespace is removed from each line
- leading and trailing blank lines are removed
- the hashed text ends with one `\n`
- encoding is UTF-8

`python scripts/content_engine.py stamp PATH` writes that digest into `body_sha256`.

`review_body_sha256` is the hash of the exact body that the current `review_verdict` reviewed. Headings, claims, notes, the style report, and the review are not part of either hash.

`approved_body_sha256` uses that same normalization. While a post is approved, all three hashes are equal:

```text
current body hash
      ==
review_body_sha256
      ==
approved_body_sha256
```

`approved_at` is the UTC time the approval command recorded, formatted `YYYY-MM-DDTHH:MM:SSZ`. It is not a local time and it does not carry an offset other than `Z`. The artifact does not store an account id, an email address, or a credential.

## Review semantics

`li-review` runs after `li-human`, on a post whose status is `styled`.

The only review states are:

| State | When to use it |
| --- | --- |
| `PASS` | The current public draft is sufficiently grounded, uses only allowed factual claims for project and personal facts, preserves qualifications, frames derived claims as the author's conclusion, allows general technical explanation without a registry claim, complies with confidentiality, and contains no material misleading wording. |
| `NEEDS_EDIT` | The underlying evidence is sufficient, and the public wording still needs a correction that does not change the evidence basis. Weak framing, generic AI language, an unnecessary "Thoughts?", a derived sentence that is not framed as a personal conclusion, a qualification that needs clearer wording, or a voice mismatch. |
| `BLOCKED` | The proposed public content depends materially on something that must not be published. `verification: unverified`, `public_safe: false`, confidential employer or client information, unsupported years of AI experience, unsupported commercial success, unsupported production scale, unsupported ownership or leadership, invented customer evidence, a materially inflated metric, a personal or project factual assertion with no eligible proof, or a project outcome or cause that no single eligible fact claim states. |

`PASS` means only that the semantic review passed. It does not mean a human approved the draft, that the draft is publishable automatically, that it moves to `approved/`, or that it is published to LinkedIn. Moving it is `li-approve`, and only for a current `PASS`.

`BLOCKED` may name the missing evidence. It must not invent a safe replacement premise. "After validating LeadFinder with paying customers..." is `BLOCKED`.

A current review requires all of:

- `status: reviewed`, or `status: approved` after human approval
- `review_verdict` set to `PASS`, `NEEDS_EDIT`, or `BLOCKED`
- `review_body_sha256` equal to `body_sha256`
- `body_sha256` equal to the hash of the current body

`status: approved` also requires `review_verdict: PASS` and `approved_body_sha256` equal to that same hash.

The style score is advisory. It is not an input to `review_verdict`. It cannot produce `PASS` or `BLOCKED`, and it cannot override the factual review.

`NEEDS_EDIT` and `BLOCKED` list findings in `## Review` and do not rewrite the body.

## Human approval

`li-approve` is the approval action. The engine command is:

```text
python scripts/content_engine.py approve POST-YYYYMMDD-NN
```

It refuses to approve unless the artifact exists, it is a post, `review_verdict` is `PASS`, `review_body_sha256` is present, that hash equals the current body, `check` passes, the post is not published, and the review is not stale. It does not run `li-review` to produce those conditions. A missing or stale review stops the command.

On success it sets:

```yaml
status: approved
approval_status: approved
approved_body_sha256: <current body hash>
approved_at: YYYY-MM-DDTHH:MM:SSZ
```

It moves `content/drafts/POST-....md` to `content/approved/POST-....md`. There is one canonical file. The id does not change. Running the command again on that same body reports that it is already approved, keeps `approved_at`, and does not append another history line.

The approved file is immutable. `li-human`, `stamp`, and any other content edit refuse it. They do not change the body and they do not change approval metadata. Returning it to drafts is `li-reopen`, and only when the user names that post.

A published file is immutable too. `stamp` refuses `content/published/`. No editing operation may change it. Publication does not reopen it.

## History

`content/history.jsonl` is an append-only audit trail. It is not application state. The artifact remains the source of truth. The file is created on the first approval event. Do not invent events for earlier drafting or review steps.

`approved` and `reopened` use the four fields below. `published` adds `linkedin_post_urn` and no other field:

```json
{"event": "approved", "post_id": "POST-YYYYMMDD-NN", "body_sha256": "<body hash>", "timestamp": "YYYY-MM-DDTHH:MM:SSZ"}
{"event": "reopened", "post_id": "POST-YYYYMMDD-NN", "body_sha256": "<body hash>", "timestamp": "YYYY-MM-DDTHH:MM:SSZ"}
{"event": "published", "post_id": "POST-YYYYMMDD-NN", "body_sha256": "<body hash>", "linkedin_post_urn": "urn:li:share:...", "timestamp": "YYYY-MM-DDTHH:MM:SSZ"}
```

`approved` records the hash that was approved. `reopened` records the hash of the unchanged body when `li-reopen` returned that post to drafts. `published` records the hash and the LinkedIn post URN after one explicit create. The earlier lines stay in the file. Do not delete historical events. Lines do not contain an account id, an email address, an access token, an OIDC subject, or a Person URN.

## Modifying reviewed or approved content

An approved artifact is immutable. A published artifact is immutable. `li-human`, `stamp`, and any other content edit refuse a file in `content/approved/` or `content/published/`, and they refuse a post whose status is `approved` or `published`:

`POST-... is approved. Reopen it explicitly before editing.`

That attempt does not change the body, does not change approval metadata, and does not move the file. `--invalidate` does not reopen it.

A reviewed draft is different. Any edit to `## Body` makes that review stale. A changed body may not retain a valid `PASS`.

```text
review PASS
   ↓
hash exact public body
   ↓
explicit approval binds the same hash
   ↓
approved file stays unchanged until explicit reopen
```

- Do not leave `status: reviewed` on a changed draft body.
- `stamp` refuses to update a reviewed draft unless `--invalidate` is set.
- On a reviewed draft, invalidation sets `status` back to `styled` or `drafted`, clears `review_verdict` and `review_body_sha256`, and writes the new hash.
- `li-human` stamps a draft with `--invalidate` because a style edit is a body edit. It does not open `content/approved/`.
- Changing only the review section or other non-body fields does not change the hash. The verdict still applies only to the body it names.
- A direct edit of an approved body fails `check`. `approve` will not repair it, and `stamp` will not repair it.

## Reopen

`/li-reopen POST-YYYYMMDD-NN` is the user's authorization to reopen that approved artifact. The engine command is:

```text
python scripts/content_engine.py reopen POST-YYYYMMDD-NN
```

It refuses unless the only copy is a valid approved artifact in `content/approved/`. On success it:

1. Keeps the same post id.
2. Moves the file to `content/drafts/`.
3. Sets `status` to `styled`, because the body did not change and the style pass still applies.
4. Clears `review_verdict`, `review_body_sha256`, `approval_status`, `approved_body_sha256`, and `approved_at`.
5. Leaves `## Body` unchanged and sets `body_sha256` to the hash of that body.
6. Appends `reopened`. The previous `approved` event remains.

It does not humanize, rewrite, review, approve, or publish.

```text
reopen
  ↓
draft
  ↓
li-human or manual edit
  ↓
li-review
  ↓
li-approve
```

Approval cannot succeed again until `li-review` records a new `PASS` on the current body.

Withdrawn ideas and plans stay on disk. Do not delete them to hide repetition.
