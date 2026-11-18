# scripts

The scripts use the Python standard library only.

`validate_proof_registry.py` checks `knowledge/proof-registry.yaml`: ids, sources, verification, public-safety, and that each statement still appears in its source file.

`content_engine.py` classifies those claims and checks local artifacts under `content/`. It stamps the post body hash and prints a content-review verdict of `PASS`, `NEEDS_EDIT`, or `BLOCKED`. `PASS` applies only to the current body hash. `approve` is a separate command: it records explicit human approval for one current `PASS` and moves that file to `content/approved/`. `reopen` is a separate command: it returns one valid approved post to `content/drafts/` without editing the body. Neither command publishes. `stamp` refuses an approved file. The style score is advisory and cannot produce a review verdict or an approval.

```text
python scripts/validate_proof_registry.py
python scripts/content_engine.py eligible
python scripts/content_engine.py review PATH
python scripts/content_engine.py editorial POST-YYYYMMDD-NN
python scripts/visual.py POST-YYYYMMDD-NN
python scripts/preview.py POST-YYYYMMDD-NN
python scripts/preview.py POST-YYYYMMDD-NN --open
python scripts/content_engine.py approve POST-YYYYMMDD-NN
python scripts/content_engine.py reopen POST-YYYYMMDD-NN
python scripts/content_engine.py check-all
python scripts/test_content_engine.py
python scripts/test_p5.py
python scripts/test_p51.py
python scripts/cycle.py run
python scripts/cycle.py discover [--refresh-research]
python scripts/cycle.py ready POST-YYYYMMDD-NN
python scripts/inbox.py
python scripts/scheduled_cycle.py
python scripts/schedule_windows.py install
python scripts/test_p6.py
python scripts/test_scheduler.py
python scripts/test_editorial_discovery.py
python scripts/test_p6_smoke.py
python scripts/test_linkedin_access.py
python scripts/linkedin_access.py status
python scripts/linkedin_access.py identity
python scripts/linkedin_access.py profile-probe
python scripts/linkedin_access.py dry-run POST-YYYYMMDD-NN
python scripts/linkedin_publish.py POST-YYYYMMDD-NN
python scripts/test_linkedin_publish.py
```

`linkedin_access.py` is the P4.1 probe. `identity`, `profile-probe`, and `dry-run` read `LINKEDIN_ACCESS_TOKEN` from the process environment, then from the repository-root `.env`, and otherwise prompt with `getpass`. `identity` reads OpenID Connect userinfo. `profile-probe` sends one diagnostic GET to `https://api.linkedin.com/v2/me` and reports whether the person id is available. Production identity stays the userinfo call. The recorded live result is HTTP 403, Profile API unavailable, author resolution `PERSON_URN_UNRESOLVED`. `dry-run` checks one approved post and prints a non-publishing preview. None of these commands publishes, and none stores the token, the OIDC subject, the person id, or a Person URN.

`linkedin_publish.py` is the P4.2 command. It publishes one approved text post when the user passes that post id. It uses the same token resolution, then sends one `UGC_SELF_SERVICE` create to `POST /v2/ugcPosts`. It does not retry, and it does not fall back to the Posts API. `--transport POSTS_API` is a separate explicit invocation. The boundary is [`docs/linkedin-integration.md`](../docs/linkedin-integration.md).

`approved_at` and history timestamps are UTC `YYYY-MM-DDTHH:MM:SSZ`.

The adapted style scripts live at `.cursor/skills/li-human/`. A low style score is not a failure. The unmodified upstream copies stay under `vendor/linkedin-agent-skill/skills/li-human/` and are not on `PATH`.
