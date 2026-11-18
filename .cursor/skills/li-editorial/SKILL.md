---
name: li-editorial
description: >-
  Records editorial PASS, REVISE, or EVIDENCE_TOO_THIN for one reviewed post.
  Invoke only when the user explicitly runs li-editorial for a POST id.
disable-model-invocation: true
---

# li-editorial

Record editorial quality for one draft post. Read [docs/editorial-contract.md](../../../docs/editorial-contract.md). Do not rewrite `## Body`. Do not publish. Do not approve.

## Preconditions

The post must be in `content/drafts/` with:

- `status: reviewed`
- `review_verdict: PASS`
- `review_body_sha256` equal to the current body hash

If semantic review is missing or stale, stop and ask for `/li-review` first.

## Run

```text
python scripts/content_engine.py editorial POST-YYYYMMDD-NN
```

The command writes `editorial_verdict`, `editorial_body_sha256`, `editorial_reviewed_at`, and `## Editorial review` including:

- `standalone_value`
- `audience_breadth`
- `project_context_required`

Editorial `PASS` does not require human approval. The author may set optional `human_editorial_decision: REVISE` or `DROP` when editorial PASS still misses strategic fit.

Show the verdict, standalone value, and the weakest finding. Do not chain `/li-visual` or `/li-preview` automatically.
