# LinkedIn integration

P4.1 probes access and identity. It does not publish. P4.2 publishes one already-approved text post only when the user runs `/li-publish POST-YYYYMMDD-NN`.

Checked against LinkedIn's public docs on 2026-09-28. The product decision in this file wins if a later doc and this repository disagree about what this codebase will do.

## Current integration model

Publication is an explicit user action on one already-approved post. The repository does not post by itself.

There is no scheduler, no cron, no background posting, no publish-on-approval, and no pipeline that generates a draft and publishes it. There is no browser automation and no scraping.

`li-approve` records approval. Approval does not call LinkedIn.

## Products and scopes

The Developer App should enable two self-service products:

| Product | Scope | Why it is requested |
| --- | --- | --- |
| Sign in with LinkedIn using OpenID Connect | `openid` | Authenticate the member and call the userinfo endpoint. |
| Sign in with LinkedIn using OpenID Connect | `profile` | Read the lite profile fields returned by userinfo. |
| Share on LinkedIn | `w_member_social` | Create a post on behalf of the authenticated member. |

Do not request `email`. P4.1 does not need the member's email address.

These are configured scopes. They are the scopes a person should select when generating a development token. They are not a claim about what a given opaque access token can do.

Official references:

- [Sign In with LinkedIn using OpenID Connect](https://learn.microsoft.com/en-us/linkedin/consumer/integrations/self-serve/sign-in-with-linkedin-v2)
- [Share on LinkedIn](https://learn.microsoft.com/en-us/linkedin/consumer/integrations/self-serve/share-on-linkedin)
- [Getting Access to LinkedIn APIs](https://learn.microsoft.com/en-us/linkedin/shared/authentication/getting-access)

`r_liteprofile` and `r_emailaddress` are not the implementation. The OIDC product replaces that older Sign In model.

## Authentication

P4.1 does not run an OAuth flow and does not store a client secret, access token, refresh token, or authorization code.

The person generates a development token outside this repository. Commands resolve it in this order: the process environment variable `LINKEDIN_ACCESS_TOKEN`, then `LINKEDIN_ACCESS_TOKEN` in the repository-root `.env`, then a hidden prompt (`getpass`). The token stays in process memory. The script does not accept it as a command argument, does not write `.env`, and does not print the token.

Two official ways to obtain that token:

- LinkedIn Developer Portal token generator, described in [Developer Portal Tools](https://learn.microsoft.com/en-us/linkedin/shared/authentication/developer-portal-tools).
- The documented 3-legged flow, including the Postman callback `https://oauth.pstmn.io/v1/callback` noted in [Authorization Code Flow](https://learn.microsoft.com/en-us/linkedin/shared/authentication/authorization-code-flow).

The OAuth token response can include a `scope` field, and the portal token page shows scopes and time to live. This program never receives that token response. It only receives the access token string, and it treats that string as opaque. It does not decode the token as a JWT and it does not call the token introspection API, because introspection needs the client secret.

A successful userinfo call proves that this token could read userinfo. It does not prove `w_member_social`. If userinfo returns 403, the script reports insufficient scope or access and names the configured scopes. It still does not invent the token's effective scope list.

## PKCE is not assumed

LinkedIn documents PKCE for native clients in [Authenticating with OAuth 2.0 for Native Clients](https://learn.microsoft.com/en-us/linkedin/shared/authentication/authorization-code-flow-native). That page says PKCE must be enabled for the application by LinkedIn after the app exists. This repository does not assume that enablement, does not implement PKCE, and does not fall back from the authorization-code flow to PKCE.

## OIDC identity

Discovery document:

`https://www.linkedin.com/oauth/.well-known/openid-configuration`

The identity command reads `userinfo_endpoint` from that document and calls it only when it is exactly:

`https://api.linkedin.com/v2/userinfo`

The documented userinfo fields this probe understands are `sub`, `name`, `given_name`, `family_name`, `picture`, and `locale`. Email is ignored when present. The command prints the name and the OIDC subject. It does not write them to disk.

The discovery document lists `subject_types_supported` as `pairwise`. `sub` is a subject identifier for this client, not a LinkedIn person id.

## Person URN boundary

The Posts API requires an author. For a member, LinkedIn documents that author as:

`urn:li:person:{id}`

The `{id}` in that URN is the person id from the member profile, not the OIDC `sub`.

What was checked:

- The OIDC userinfo schema calls `sub` a subject identifier. The discovery metadata says subject identifiers are pairwise.
- Share on LinkedIn still says to see the OIDC docs for the Person URN. Those OIDC docs do not define a Person URN and do not say to copy `sub` into `urn:li:person:{sub}`.
- The Posts API says to take `id` from the Retrieve Current Member's Profile API and build `urn:li:person:{id}`. That profile call is `GET https://api.linkedin.com/v2/me`. Its documented permissions are `r_liteprofile`, `r_basicprofile`, or `r_compliance`. Those are not the scopes this integration requests. `GET https://api.linkedin.com/v2/me` is a diagnostic capability probe. Production identity is the OpenID Connect userinfo call. The `identity` command calls userinfo. `profile-probe` is the only command that calls `/v2/me`.

Resulting states:

| State | P4.1 identity command |
| --- | --- |
| `OIDC_AUTHENTICATED` | Reached when userinfo returns a subject. |
| `PERSON_URN_UNRESOLVED` | The only author state the identity command can report. |
| `PERSON_URN_VERIFIED` | Not reachable from userinfo. OIDC `sub` is not a person id. |

`OIDC_AUTHENTICATED` together with `PERSON_URN_UNRESOLVED` is a passing P4.1 outcome. `PERSON_URN_VERIFIED` is reachable only from `profile-probe` when `/v2/me` returns a usable `id`. The live probe on 2026-10-05 returned HTTP 403, so the Profile API is unavailable and the identity command keeps author resolution at `PERSON_URN_UNRESOLVED`. That command does not publish. P4.2 uses a separate, unverified OIDC candidate, documented below. The recorded Profile API result is in the P4.1b section below.

The dry-run author field is the placeholder `UNRESOLVED`. It is not a URN.

## Two create transports, no fallback

Share on LinkedIn, the self-service product enabled for this app, documents shares as:

`POST https://api.linkedin.com/v2/ugcPosts`

The [Posts API](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api?view=li-lms-2026-09) says the Posts API replaces `ugcPosts` and documents:

`POST https://api.linkedin.com/rest/posts`

The first live capability validation uses exactly `UGC_SELF_SERVICE`, because that is the endpoint the enabled Share on LinkedIn product documents. `POSTS_API` stays implemented. An invocation uses it only when `--transport POSTS_API` is passed. A failure on either transport stops. The other transport is not called.

P4.1 does not call either endpoint. Its dry-run still previews the Posts API text-post shape below and does not send it.

The Posts API text-post shape represented by the dry-run is:

- `author`: Person URN, unresolved in P4.1
- `commentary`: the approved public body
- `visibility`: `PUBLIC`
- `distribution.feedDistribution`: `MAIN_FEED`
- `distribution.targetEntities`: empty
- `distribution.thirdPartyDistributionChannels`: empty
- `lifecycleState`: `PUBLISHED`
- `isReshareDisabledByAuthor`: false
- headers: `Content-Type: application/json`, `X-Restli-Protocol-Version: 2.0.0`, and `Linkedin-Version`

`payload structure understood` means those fields match the current Posts API text-post sample.

`live API version verified` means a real request has succeeded with a version header. That has not happened.

## API version

The Posts API sample leaves the version header as a `YYYYMM` placeholder. It does not name the current month.

The [versioning page](https://learn.microsoft.com/en-us/linkedin/marketing/versioning), read on 2026-09-28 and again on 2026-10-05, lists the latest version as September 2026, header `202609`. The same page says Marketing version `202510` sunsets on 15 October 2026. Versions move every month. The re-check on 2026-10-05 did not show a newer header, so the constant stays `202609`.

`api_version_status` is `documented_not_live_verified`.

`202609` is the header named by that page on those dates. It is not a guess, and it is not proof that LinkedIn has accepted the header. The constant lives only as `DOCUMENTED_LINKEDIN_VERSION` in `scripts/linkedin_access.py`. `scripts/linkedin_publish.py` imports it. A live create has not confirmed it yet.

## P4.1

| Boundary | Status |
| --- | --- |
| OAuth/token access bootstrap | implemented |
| OIDC identity | implemented |
| Person URN resolution | unresolved (`PERSON_URN_UNRESOLVED`) |
| Profile API capability | unavailable; live HTTP 403 on 2026-10-05 |
| Dry-run | implemented |
| Live publishing | explicit `/li-publish` in P4.2; not called by the P4.1 commands |

Commands:

```text
python scripts/linkedin_access.py status
python scripts/linkedin_access.py identity
python scripts/linkedin_access.py profile-probe
python scripts/linkedin_access.py dry-run POST-YYYYMMDD-NN
```

The dry-run reads only `content/approved/`. It requires `review_verdict` `PASS`, `approval_status` `approved`, and equal `body_sha256`, `review_body_sha256`, and `approved_body_sha256` values for the current body. It runs the existing artifact check. Drafts, stale approvals, and reopened posts are refused. The command does not review, approve, reopen, or edit the artifact.

Setup steps: [linkedin-p4.1-setup.md](linkedin-p4.1-setup.md).

## P4.2 — explicit single-post publication

`/li-publish POST-YYYYMMDD-NN` is the publication authorization for that one approved post. The skill is [`.cursor/skills/li-publish/SKILL.md`](../.cursor/skills/li-publish/SKILL.md). The command is:

```text
python scripts/linkedin_publish.py POST-YYYYMMDD-NN
```

No scheduler. No cron. No queue. No background publishing. No publish-at time. No publish-after-approval. No automatic retry. `li-review`, `li-approve`, and the drafting skills do not call it. A second yes/no prompt is not required, because naming the post id is the authorization.

Before the create request, the command requires an approved post whose current body hash equals `body_sha256`, `review_body_sha256`, and `approved_body_sha256`, with `review_verdict` `PASS` and `approval_status` `approved`. It refuses drafts, reopened posts, stale review, stale approval, externally edited approved files, and posts that are already published. It does not review, approve, reopen, humanize, or repair the file.

Author resolution for this command:

| State | When |
| --- | --- |
| `OIDC_AUTHENTICATED` | Userinfo succeeded and returned a subject. |
| `PERSON_URN_CANDIDATE_FROM_OIDC` | The in-memory author is `urn:li:person:{sub}`. LinkedIn has not accepted it yet. |
| `PERSON_URN_LIVE_VERIFIED` | Runtime only, after the selected create returns HTTP 201. Not written to the artifact. |

The identity command still reports `PERSON_URN_UNRESOLVED`. It does not build this candidate. P4.1b showed that `/v2/me` returns HTTP 403 for these scopes, so publication does not call `/v2/me`. The candidate is not `PERSON_URN_VERIFIED`. The raw subject and the Person URN are not stored. The created content id, from the `x-restli-id` response header, may be stored as `linkedin_post_urn`.

The default create is one `POST https://api.linkedin.com/v2/ugcPosts` (`UGC_SELF_SERVICE`) for a text-only public share. The body is the Share on LinkedIn text sample: `author`, `lifecycleState` `PUBLISHED`, `specificContent` / `com.linkedin.ugc.ShareContent` / `shareCommentary.text` / `shareMediaCategory` `NONE`, and `visibility` / `com.linkedin.ugc.MemberNetworkVisibility` `PUBLIC`. The request sends `X-Restli-Protocol-Version: 2.0.0` and does not send `Linkedin-Version`. The sanitized preview states `Transport: UGC_SELF_SERVICE` and `Endpoint: POST /v2/ugcPosts`. It does not print the Person URN.

`POSTS_API` remains the `POST https://api.linkedin.com/rest/posts` implementation. It is not called unless that transport is selected. `UGC_SELF_SERVICE` failure does not call the Posts API. `POSTS_API` failure does not call `ugcPosts`. One invocation sends at most one mutating request. Documented create success is HTTP 201. HTTP 409 is not retried. A timeout or other network failure after the request may have been sent is `publication outcome uncertain`. The approved file stays where it is unless success can be proven. HTTP 403 on `UGC_SELF_SERVICE` reports Share on LinkedIn capability denied and leaves the artifact approved. HTTP 400 leaves the author candidate unverified.

On HTTP 201 with a `urn:li:share:` or `urn:li:ugcPost:` id, the command sets `publication_status`, `published_body_sha256`, `published_at`, and `linkedin_post_urn`, moves the file to `content/published/`, and appends a `published` history line. The published body hash must equal the review and approval hashes. The published file is immutable.

## Compliance

The [LinkedIn API Terms of Use](https://www.linkedin.com/legal/l/api-terms-of-use), last revised 13 December 2022, section 3.1 item 26, says the developer agrees not to:

> Use the Content or the APIs to automate posting on the LinkedIn Services.

This repository does not automate posting. P4.1 makes no post, comment, like, reshare, or media upload. P4.2 sends one create request only after the user names one approved post id. It does not schedule that request. The terms also restrict scraping and other non-official collection of LinkedIn content. This project does not scrape.

## P4.1b — Profile API capability probe

`profile-probe` asks one question: can this app's token read the authenticated member's Profile API `id` from `GET https://api.linkedin.com/v2/me`?

`GET https://api.linkedin.com/v2/me` is a diagnostic capability probe. Production identity is the OpenID Connect userinfo call from `identity`.

The command resolves the access token the same way (`LINKEDIN_ACCESS_TOKEN`, then repository-root `.env`, then `getpass`), sends one GET, and does not store the token, the person id, or a Person URN. It does not publish.

The request is the documented URL with no query. The Profile API documents Rest.li projections such as `projection=(id)`, and the same page says requesting a non-default field set needs partner permissions. This self-service probe does not send a projection, so a 403 means the Profile API itself is unavailable. The command reads `id` and drops every other field. It does not print the id or the Person URN.

Checked on 2026-10-05:

- [Profile API](https://learn.microsoft.com/en-us/linkedin/shared/integrations/people/profile-api)
- [Posts API](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api?view=li-lms-2026-09) author `urn:li:person:{id}`

| Result | Meaning |
| --- | --- |
| HTTP 200 and a non-empty string `id` | `PROFILE_ID_VERIFIED` and `PERSON_URN_VERIFIED`. The in-memory URN is built only from that `id`. |
| HTTP 200 without a usable `id` | `PERSON_URN_UNRESOLVED`. |
| HTTP 403 | Profile API unavailable. This is not an authentication failure. OIDC may already have succeeded. |
| HTTP 401 | The access token is invalid, expired, or revoked. The token is not printed. |

`status` does not call LinkedIn. It reports OIDC as unverified, Profile API capability as unverified, the Person URN as unresolved, and publishing as disabled. It does not remember a previous live probe.

Run the probe with the same development token used for `identity`. Do not paste the token, the OIDC subject, the person id, the Person URN, or the client secret into the repository, chat, or a commit.

### Recorded live result

Observed on 2026-10-05 by running `python scripts/linkedin_access.py profile-probe`. This repository stores the capability result only.

```text
HTTP:
403

Profile API:
unavailable

Configured scopes:
openid profile w_member_social

Person URN:
unresolved

Author resolution:
PERSON_URN_UNRESOLVED
```

The current application cannot read the documented Profile API with those configured scopes. P4.1b is closed on that result. The identity command keeps author resolution at `PERSON_URN_UNRESOLVED`. This record omits the access token, the OIDC subject, the person id, the Person URN, and the client secret.

## Network boundary

P4.1 and P4.1b may send only these GET requests:

- the OIDC discovery URL
- `https://api.linkedin.com/v2/userinfo`
- `https://api.linkedin.com/v2/me` from `profile-probe` only

`linkedin_access.py` still refuses `POST`, `PUT`, `PATCH`, and `DELETE` before a socket is opened. Any other GET URL is refused, including a query string.

P4.2 may send those same identity GETs and then one `POST`. The default URL is `https://api.linkedin.com/v2/ugcPosts`. `https://api.linkedin.com/rest/posts` is sent only when `--transport POSTS_API` is explicit. It does not send both. Tests mock HTTP and do not contact LinkedIn.

## Secrets

Do not commit an access token, client secret, authorization code, or refresh token. `.gitignore` ignores `.env` and `.env.*`, and keeps `.env.example`. Local development may set `LINKEDIN_ACCESS_TOKEN` in the process environment or in the gitignored repository-root `.env`. The scripts read that file and do not write it. `.env.example` has an empty value. Do not put a real token in `.env.example`, docs, history, logs, tests, or command-line arguments.
