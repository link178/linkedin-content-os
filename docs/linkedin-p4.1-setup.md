# P4.1 setup

This is the manual setup for the identity probe and the dry-run. It does not publish a post.

Generate the token in LinkedIn's own tools. Do not paste it into a file in this repository, into chat, into a command line, or into a commit.

## 1. Create or select a Developer App

Open the [LinkedIn Developer Portal](https://www.linkedin.com/developers/apps) and sign in. Create an app, or select the app you will use for this repository. The app needs a name and the rest of the registration fields LinkedIn asks for. You do not need to put the client id or client secret in this repository.

## 2. Add Sign in with LinkedIn using OpenID Connect

In the app, open the Products tab and add **Sign in with LinkedIn using OpenID Connect**. That product is what exposes the OIDC scopes.

## 3. Add Share on LinkedIn

On the same Products tab, add **Share on LinkedIn**. That product grants `w_member_social`, which a later phase would need to create a post for the authenticated member. P4.1 does not create a post.

## 4. Check the Auth tab

Open the Auth tab. Confirm the available scopes include:

```text
openid
profile
w_member_social
```

Leave `email` unselected when you generate a token. This probe does not read the member's email address.

The Auth tab also shows the client id and client secret. Leave both of them there. Do not copy the secret into this repository.

## 5. Generate a development token

Use LinkedIn's token tool, not a token pasted into source code.

Portal path, from [Developer Portal Tools](https://learn.microsoft.com/en-us/linkedin/shared/authentication/developer-portal-tools):

1. Open the LinkedIn Developer Portal token generator.
2. Select this app.
3. Select the OAuth flow and the scopes `openid`, `profile`, and `w_member_social`.
4. Approve the member consent screen.
5. On Token Details, use Copy token.

The token details page can show scopes and time to live. That page is outside this program. The script cannot see it, and it does not decode the token to recover those scopes.

Postman is the other documented option. The [authorization-code flow](https://learn.microsoft.com/en-us/linkedin/shared/authentication/authorization-code-flow) says a Postman test should use the redirect URL `https://oauth.pstmn.io/v1/callback` and Authorize using browser. Do not save the resulting token into a Postman collection that is committed here.

Native PKCE is not available just because the docs describe it. LinkedIn has to enable PKCE for the app. Do not switch this setup to PKCE.

## 6. Keep the token out of Git

Put the development token only in the process environment or in the gitignored repository-root `.env`:

```text
LINKEDIN_ACCESS_TOKEN=
```

`.env.example` shows that name with an empty value. Do not put a real token in:

- a source file
- `.env.example`
- Markdown
- `content/history.jsonl`
- a test
- a shell argument
- a git commit

The scripts read the variable. They do not write it, and they do not print it.

## 7. Run the identity command

From the repository root:

```text
python scripts/linkedin_access.py identity
```

## 8. How the token is chosen

The command checks, in order:

1. `LINKEDIN_ACCESS_TOKEN` already set in the process environment
2. `LINKEDIN_ACCESS_TOKEN` in the repository-root `.env`
3. a hidden `getpass` prompt

The prompt is:

```text
LinkedIn access token:
```

Paste the token only when the first two are empty. The terminal should not echo it. Press Enter. If you copied a curl command from the portal instead of the token itself, stop. That curl contains the token in an `Authorization` header and will land in shell history. Paste only the token value.

## 9. Read the identity result

A successful probe looks like this. The name and subject will be the authenticated member's, and the subject is not a Person URN.

```text
Authenticated LinkedIn member:
Name: ...
OIDC subject: ...
OIDC authentication:
OIDC_AUTHENTICATED
Author resolution:
PERSON_URN_UNRESOLVED
```

`PERSON_URN_UNRESOLVED` is the expected P4.1 result. The script must not print a value starting with `urn:li:person:`.

Other results:

| What you see | What it means |
| --- | --- |
| `No access token was provided.` | The prompt was empty, or the command had no interactive input. |
| `The access token is invalid, expired, or revoked.` | LinkedIn returned 401. Generate a new token. The old value is not printed. |
| `Insufficient scope or access.` | LinkedIn returned 403. Check that the token was minted with `openid` and `profile`. |
| `LinkedIn could not be reached. Identity is unverified.` | The network call failed. Nothing was inferred. |
| `UserInfo response was malformed. Identity is unresolved.` | The response had no usable subject. |

## 10. Dry-run an approved post

Approval has to already exist. This command will not approve a draft.

```text
python scripts/linkedin_access.py dry-run POST-YYYYMMDD-NN
```

Use the id of a file in `content/approved/`. The public fixture id used by the tests is `POST-20260927-01`. It is synthetic. The command uses the same token resolution as `identity`. It prompts only when the environment and `.env` are both empty. An empty or skipped prompt still checks the artifact locally and reports the member as not authenticated.

A structurally valid dry-run includes:

```text
Approved:
yes

Review:
PASS

Author resolution:
PERSON_URN_UNRESOLVED

Ready for live publication:
NO
Reason:
Person URN has not been verified.
```

`Ready for live publication: NO` is the success condition for P4.1. The commentary block is the public body, and it should match the approved text. The command does not create, edit, or delete a LinkedIn post.

`python scripts/linkedin_access.py status` prints the same boundary without asking for a token and without calling LinkedIn. It reports Profile API capability as unverified. It does not remember a previous live probe.

## 11. Probe the Profile API

This check is separate from identity. `GET https://api.linkedin.com/v2/me` is a diagnostic capability probe. Production identity is the OpenID Connect userinfo call from `identity`. Use the same development token. Do not paste the token, the OIDC subject, the person id, the Person URN, or the client secret into a file, chat, or a commit.

```text
python scripts/linkedin_access.py profile-probe
```

Two results answer the question. Neither one publishes.

HTTP 200 with a usable `id` prints `PROFILE_ID_VERIFIED` and `PERSON_URN_VERIFIED`. The id and the URN stay in that process. They are not printed.

HTTP 403 prints Profile API unavailable and `PERSON_URN_UNRESOLVED`. That means this application cannot read the documented Profile API. It does not mean the OIDC token is invalid.

HTTP 401 means the access token is invalid, expired, or revoked.

### Observed result

On 2026-10-05 the live probe returned this capability result. The access token, OIDC subject, person id, Person URN, and client secret are omitted.

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

P4.1b is closed on that result. Author resolution stays `PERSON_URN_UNRESOLVED`. Live publishing stays blocked. `status` does not remember this run.
