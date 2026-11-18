---
name: li-reopen
description: >-
  Returns one approved local post to content/drafts/ so it can be edited.
  Invoke only when the user explicitly runs li-reopen for a specific POST id.
  Mentioning LinkedIn, a review PASS, or approval is not a trigger and does
  not reopen the post.
disable-model-invocation: true
---

# li-reopen

Reopen one approved post for editing. Follow [the content contract](../../../docs/content-contract.md). This skill does not humanize, rewrite, review, approve, or publish.

Invoking `/li-reopen POST-YYYYMMDD-NN` is the authorization to reopen that artifact. Do not infer it from a request to edit, from `li-human`, or from `stamp`.

If the user does not name one `POST-YYYYMMDD-NN` id in this invocation, stop and ask for the id. Do not choose a post from the conversation.

## Preconditions

Run only:

```text
python scripts/content_engine.py reopen POST-YYYYMMDD-NN
```

The command finds the post in `content/approved/` and verifies it is a valid approved artifact. Do not edit `## Body` first. Do not run `li-human`, `li-review`, `li-approve`, or `stamp` from this skill.

## On failure

Stop. Show the command's errors. Leave the file where it is. Do not edit the body, do not clear fields by hand, and do not move the file.

## On success

The command keeps the same post id, moves the only copy to `content/drafts/`, and leaves `## Body` unchanged. `body_sha256` is the hash of that same body.

It clears `approval_status`, `approved_body_sha256`, `approved_at`, `review_verdict`, and `review_body_sha256`. Status becomes `styled`. It appends one `reopened` line to `content/history.jsonl`. The earlier `approved` line stays.

Show the post id and the drafts path. Approval cannot succeed again until `li-review` records a new `PASS` on the current body and the author runs `li-approve`.

The next explicit step, if the author wants it, is `li-human` or a manual edit, then `li-review`. Do not run `li-publish`.

## What not to store

Do not write a user id, an email address, a LinkedIn cookie, or any other credential into the artifact or the history file.
