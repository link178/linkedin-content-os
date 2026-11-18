---
name: li-approve
description: >-
  Records explicit human approval for one reviewed local post and moves that
  single artifact to content/approved/. Invoke only when the user explicitly
  runs li-approve for a specific POST id. Mentioning LinkedIn, a review PASS,
  or approval in earlier conversation is not a trigger and is not approval.
disable-model-invocation: true
---

# li-approve

Record human approval for one post. Follow [the content contract](../../../docs/content-contract.md). This skill does not publish, schedule, or call LinkedIn. Do not run `li-publish`.

Invoking `/li-approve POST-YYYYMMDD-NN` is the approval. Do not ask for another yes or no after that command. Do not infer approval from earlier messages, from `review_verdict: PASS`, or from the fact that a draft exists.

If the user does not name one `POST-YYYYMMDD-NN` id in this invocation, stop and ask for the id. Do not choose a post from the conversation.

## Preconditions

Run only:

```text
python scripts/content_engine.py approve POST-YYYYMMDD-NN
```

The command does not run li-review. Do not run li-review, `li-human`, or `stamp` from this skill to make the preconditions true.

Approval fails unless all of these are already true:

1. The artifact exists.
2. It is a post.
3. Semantic `review_verdict` is `PASS` and `review_body_sha256` equals the current body hash.
4. Editorial `editorial_verdict` is `PASS` and `editorial_body_sha256` equals the current body hash.
5. A current preview exists: `preview_body_sha256` equals the current body hash and `preview_generated_at` is set.
6. If a visual is attached, `preview_visual_sha256` equals the current `visual.svg` hash; if not, `preview_visual_sha256` is empty.
7. `python scripts/content_engine.py check` would pass.
8. The artifact is not already published.

Hash invariant for a new approval:

```text
current body hash
== body_sha256
== review_body_sha256
== editorial_body_sha256
== preview_body_sha256
== approved_body_sha256
```

Semantic `PASS` and editorial `PASS` are not human approval. The style score is not a gate.

## On failure

Stop. Show the command's errors. If the review is missing or stale, say that review must run first. Leave the file where it is. Do not edit `## Body`, do not clear fields by hand, and do not move the file.

## On success

The command sets `approval_status: approved`, `approved_body_sha256` to the current body hash, and `approved_at` to UTC `YYYY-MM-DDTHH:MM:SSZ`. It moves the only canonical copy from `content/drafts/` to `content/approved/` and appends one `approved` line to `content/history.jsonl`.

`body_sha256`, `review_body_sha256`, and `approved_body_sha256` are the same hash. The approved file is immutable. This skill does not edit it and does not reopen it. Reopening is a separate explicit `/li-reopen POST-YYYYMMDD-NN` command.

Show the post id, the approved path, and `approved_at`. This skill does not start publication. Publication is a separate explicit `/li-publish POST-YYYYMMDD-NN` command.

A second invocation for the same unchanged body reports that it is already approved. It does not change `approved_at` and it does not append another history line.

## What not to store

Do not write a user id, an email address, a LinkedIn cookie, or any other credential into the artifact or the history file.
