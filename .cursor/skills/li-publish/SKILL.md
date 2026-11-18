---
name: li-publish
description: >-
  Publishes exactly one already-approved text post to LinkedIn. Invoke only
  when the user explicitly runs /li-publish with one POST id. Mentioning
  LinkedIn, a review PASS, or an approval is not a trigger and is not
  permission to publish.
disable-model-invocation: true
---

# li-publish

Publish one approved text post. The invocation `/li-publish POST-YYYYMMDD-NN` is the publication authorization for that post and no other. Do not ask for another yes or no after that command.

No scheduler. This skill does not run on a timer, from a queue, from a background process, after approval, or after drafting. `li-review`, `li-approve`, `li-human`, `li-post`, `li-ideas`, `li-plan`, and `li-reopen` do not invoke it. Do not invoke it yourself unless the user just ran `/li-publish` with one post id.

If the user does not name one `POST-YYYYMMDD-NN` id in this invocation, stop and ask for the id. Do not choose a post from the conversation.

## Command

Run only:

```text
python scripts/linkedin_publish.py POST-YYYYMMDD-NN
```

The command resolves `LINKEDIN_ACCESS_TOKEN` from the process environment, then from the repository-root `.env`. If neither is set, run it in a terminal so the hidden `getpass` prompt can work. Do not pass the access token as an argument. Do not write the token, the OIDC subject, or the Person URN into the artifact, the history file, the command line, the chat, or any file other than the gitignored `.env`.

The `getpass` prompt is only the fallback. It is not a second publication confirmation.

## What the command does

It publishes only when the artifact is already a valid approved post: `content/approved/`, `review_verdict` `PASS`, `approval_status` `approved`, and the current body hash equal to `body_sha256`, `review_body_sha256`, and `approved_body_sha256`.

It does not review, approve, reopen, humanize, or repair the artifact. If the command refuses the post, stop. Leave the file where it is.

On the network it reads OpenID Connect userinfo, builds an in-memory author candidate `urn:li:person:{sub}`, prints a sanitized preview, and sends one `POST` to `/v2/ugcPosts`. The preview states `Transport: UGC_SELF_SERVICE` and `Endpoint: POST /v2/ugcPosts`. It does not print the Person URN. It does not call `/v2/me`. It does not call the Posts API after that request. A failure stops. Do not retry a create request.

`POSTS_API` (`POST /rest/posts`) remains implemented. It runs only when the command is invoked with `--transport POSTS_API`. That invocation also sends one create and does not fall back to `/v2/ugcPosts`. The default `/li-publish POST-...` command does not pass `--transport`.

If the command reports `publication outcome uncertain`, do not run it again to retry. Leave the approved artifact until the user reconciles it.

## On success

The command moves `content/approved/POST-....md` to `content/published/POST-....md`, records the LinkedIn post URN, and appends one `published` history line. Show the post id, the published path, and the LinkedIn post URN. Do not show the access token, the OIDC subject, or the Person URN.

The published file is immutable. Do not edit it.

## What not to store

Do not store an access token, an OIDC subject, a raw person id, or the Person URN used as the author.
