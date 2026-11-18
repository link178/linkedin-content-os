# Editorial contract

Editorial review answers one question:

> Is this actually good content worth publishing?

Semantic review (`review_verdict: PASS`) answers a different question:

> Is this post grounded, public-safe, and compliant?

These gates stay independent.

**Technical specificity is not equivalent to standalone reader value.** A post can pass semantic review and still fail editorial review when it opens inside project metrics without establishing why a reader should care.

## Verdicts

| Verdict | Meaning |
| --- | --- |
| `PASS` | Strong enough to publish after preview. |
| `REVISE` | Evidence is sufficient, but composition or reader value is weak. |
| `EVIDENCE_TOO_THIN` | Source evidence cannot support a stronger post without inventing detail. |

`standalone_value: LOW` must not receive `PASS` unless the post is an intentionally framed specialist deep dive with adequate opening context.

`audience_breadth` is an assessment of the body. It does not by itself change `editorial_verdict`. The headless cycle compares it with the topic's intended audience. A body assessed `SPECIALIST` is a strategic mismatch for `BROAD_TECH` and does not become a successful candidate for that audience by moving the metric below the opening.

When standalone value stays `LOW`, the reported weakest element and structural change name that opening failure, including when another dimension such as contrast also fails. The instruction is to open with why the point matters, then introduce scoped evidence. A bare or metric-first first line is that failure even when the sentence is longer than a short score.

## Dimensions

Evaluate thesis, concrete substance, tension or contrast, author-specific insight, reader value, hook quality, readability, evidence ceiling, **standalone value**, **audience breadth**, and **project context required**. Never recommend unsupported specificity. Do not treat general technical explanation as a universal measured outcome.

A concrete verb, a date, a contrast word, or a conclusion frame is not substance by itself. A personal anecdote that does not develop the thesis does not supply the missing substance. A closing paragraph that repeats an earlier paragraph is `REVISE`. The repair is a consequence the earlier paragraphs did not already state. It is not an unrelated anecdote.

When `REVISE` names contrast, the finding must identify missing opposition or an overclaimed causal identity (for example assigning a failure to the model or the dependency without scoped evidence). It must not repeat a generic outline when the body already states observation, contrast, decision, and lesson. A labeled contrast or an explicit “is not the same” opposition counts as contrast for scoring; a contrast marker alone still is not substance.

The headless cycle rejects a cited project, metric, career, or profile claim that does not develop the selected topic before any revision. That stop is not an editorial rewrite.

## Standalone and audience fields

| Field | Values |
| --- | --- |
| `standalone_value` | `HIGH`, `MEDIUM`, `LOW` |
| `audience_breadth` | `BROAD_TECH`, `AI_ENGINEERING`, `SPECIALIST` |
| `project_context_required` | `NONE`, `LIGHT`, `HIGH` |

Editorial questions (reported in `## Editorial review`):

1. Does the reader know why this matters within the opening section?
2. Does the post assume familiarity with a personal project?
3. Is the technical depth appropriate for the intended audience?
4. Could a more general framing preserve the evidence while increasing relevance?
5. Is the project used as evidence, or are readers asked to care about project internals by default?

## Metadata

```yaml
editorial_verdict:
editorial_body_sha256:
editorial_reviewed_at:
human_editorial_decision:  # optional APPROVE | REVISE | DROP
```

`human_editorial_decision: REVISE` or `DROP` blocks `approve` even when `editorial_verdict` is `PASS`.

Editorial review binds to the exact public body. A body edit invalidates editorial review even when semantic review would also need to rerun.

## Report section

Posts with editorial metadata include `## Editorial review` with concise findings and the standalone/audience fields. Do not dump long scoring diagnostics into `## Body`.

## Workflow

Run `/li-editorial POST-YYYYMMDD-NN` only after semantic `PASS` on the current body hash. Editorial review does not publish and does not call LinkedIn.

Strategy and topic selection precede ideas. See [`knowledge/content-strategy.md`](../knowledge/content-strategy.md).
