# LinkedIn Content OS

A local editorial workflow that turns evidence into LinkedIn drafts, then refuses to publish them until a person says so.

The Python core checks claims, hashes, and lifecycle state. A model may propose wording. It does not approve, and it does not publish. This repository is the public, synthetic demonstration of that system. It does not include a private career corpus.

## What makes this different from a basic post generator

A basic generator asks a model for a post and saves the text. This system separates jobs:

- Claims live in a proof registry. A sentence is eligible only when the registry says the claim is verified and public-safe, or it is a framed interpretation.
- A project result has to match one fact. Two neighboring facts are not combined into a cause.
- Semantic review, editorial review, and an independent critic are different gates. Passing one does not pass the next.
- Each gate is bound to a SHA-256 of the body. Editing the body makes the earlier pass stale.
- Automatic revision is capped. When the cap is hit, the candidate stops.
- A human decision (`APPROVE`, `REVISE`, or `DROP`) is separate from lifecycle approval, and both are separate from publication.
- The only publication command is an explicit publish invocation. The scheduler cannot call it.

The deterministic checks can reject known unsupported constructions. They do not guarantee that every false sentence will be caught. Live wording still depends on a model, and that model is untrusted input.

## Architecture

```text
strategy topics
    -> optional discovery
    -> topic selection
    -> model proposal (Cursor CLI, ask mode)
    -> semantic review (Python)
    -> editorial review (Python)
    -> critic (model proposal, Python schema)
    -> preview
    -> human decision
    -> lifecycle approval
    -> explicit publish
```

Python writes the artifacts, the hashes, and the state transitions. The model runtime is `scripts/cursor_runtime.py`. It runs in a temporary snapshot that does not include `.env`. Tests substitute `FakeRuntime` and never call LinkedIn.

Details and a diagram: [docs/engineering.md](docs/engineering.md).

| Path | Role |
| --- | --- |
| `scripts/` | Engine, cycle, inbox, discovery, LinkedIn commands, tests |
| `.cursor/skills/` | Explicit agent skills. Mentioning LinkedIn does not run them |
| `knowledge/` | Synthetic demo corpus and proof registry |
| `content/` | Synthetic strategy, ideas, drafts, and one fake published fixture |
| `research/trends/` | Short synthetic notes for source-token checks. Not article copies |
| `vendor/` | Unmodified MIT snapshot of linkedin-agent-skill, for reference |
| `docs/` | Contracts, LinkedIn integration, scheduler, license proposal |

## Evidence and validation

`knowledge/proof-registry.yaml` is restricted YAML. `scripts/validate_proof_registry.py` checks ids, sources, and that each statement appears in its source file.

`scripts/content_engine.py` classifies a claim as `fact`, `opinion`, or `blocked`.

- `verified` and `public_safe: true` can be cited as fact.
- `derived` and `public_safe: true` can be cited as opinion, and the sentence needs a conclusion frame.
- `unverified`, or `public_safe: false`, cannot be cited.

A metric citation must repeat the registry qualification as its scope and must keep the registry's numbers in the body. General technical explanation can stand without a personal claim id. It cannot launder a project result. The public demo shows both a supported metric sentence and a blocked cause.

## Human-in-the-loop lifecycle

These states are not interchangeable:

```text
semantic PASS
  != editorial PASS
  != critic PASS
  != READY_FOR_HUMAN
  != human_editorial_decision APPROVE
  != lifecycle approval
  != publication
```

`READY_FOR_HUMAN` means the gates that are required for that candidate are current. It is not approval. Approval moves one post into `content/approved/` and appends `content/history.jsonl`. Publication is a later command. A published file is not reopened for editing.

The local Review Inbox (`scripts/inbox.py`) can record APPROVE, REVISE, or DROP. It does not call the LinkedIn publish module. `scripts/cycle.py` does not import it.

## Offline demo

Requirements: Python 3.11 or newer. The demo uses the standard library only. It does not need a Cursor account, a network connection, or the private corpus.

From the repository root:

```text
python scripts/validate_proof_registry.py
python scripts/public_demo.py
python -m unittest discover -s scripts -p "test_*.py"
```

`public_demo.py` calls the real review and readiness functions. On success it prints:

- `supported_claim PASS` for the nine-question Hit Rate sentence cited as `LEGAL_RAG_007`
- `inflated_metric BLOCKED` for a "perfect retrieval" wording of that same claim
- `unsupported_cause BLOCKED` for "RRF caused the MRR 0.75 result" even when the use-fact and the metric-fact are both cited
- `blocked_draft_ready False` for `POST-20261113-02`
- `human_publication not invoked`

That is a deterministic contract demonstration. It does not generate a new post with a model.

The synthetic registry is labeled as a demo. Replace `knowledge/` with your own sources before using the system for real writing. Do not treat the demo claims as facts about the repository author.

## Live installation

Live generation needs all of the following. Missing any one of them means live generation does not run:

- Python 3.11+
- The standard library. There is no `requirements.txt` and no third-party Python package
- The Cursor CLI (`agent`) logged in on the machine. Override the executable with `CURSOR_AGENT` if it is not on the default path
- A network path to the model provider used by that CLI
- For LinkedIn identity or publish commands only: `LINKEDIN_ACCESS_TOKEN` in the environment or in a gitignored `.env`. Copy `.env.example` and fill it locally. Never commit `.env`
- Windows, macOS, or Linux for the engine and the tests. Windows Task Scheduler is optional and Windows-only

Expected live artifacts, when a cycle is actually run, are files under `content/`: idea, plan, draft, cycle record, and sometimes a preview. A live cycle that reaches `READY_FOR_HUMAN` is still waiting for a person. This public tree does not claim that an unattended model run has been verified here.

`scripts/test_p6_smoke.py` contains a live CLI smoke test. It does not run unless `LCOS_ALLOW_LIVE_SMOKE=1` and `agent status` reports a logged-in CLI. Continuous integration does not set that variable.

## Testing

The deterministic suite is `python -m unittest discover -s scripts -p "test_*.py"`.

It uses temporary directories and the synthetic registry. LinkedIn HTTP is mocked. The tests do not approve or publish a real post. Two cases skip in this tree: the opt-in live smoke test, and a legacy inbox rendering check whose private fixture is not included.

GitHub Actions runs the same commands on Ubuntu and Windows with Python 3.13. See `.github/workflows/deterministic.yml`. The workflow has `contents: read` and no repository secrets.

## Optional scheduled operation

Windows Task Scheduler can start `scripts/scheduled_cycle.py` on weekdays. The task prepares a candidate. It does not approve or publish. A missed 09:00 run is not caught up. `wake_to_run` and `start_when_available` are false.

The example `config/scheduler.json` uses timezone id `UTC`. Set `timezone_id` to the machine's Windows timezone before install. If preflight fails, install registers the task disabled.

An unattended calendar start has not been verified. One interactive test-mode run is covered by unit tests and is not the same thing as a 09:00 trigger after sleep. See [docs/scheduler.md](docs/scheduler.md).

## Limitations

- Deterministic review does not prove that a draft is true.
- Live quality depends on the Cursor CLI and the model behind it. Offline tests do not measure that.
- The public knowledge files are synthetic. They are not a portfolio biography.
- `human_revisions` history in older private candidates is not part of this tree.
- The inbox binds to `127.0.0.1` for local review. It is not a hosted service.
- There is no automatic retry of a LinkedIn write, and no fallback from one publish API to another.

## Security and publication boundaries

- Do not commit `.env`, tokens, cookies, or session files. `.gitignore` is a backstop for new files. It is not how this public history was cleaned. The public Git history was created as a new repository. See [CANDIDATE.md](CANDIDATE.md).
- Do not put private career notes, client names, or real draft archives into a fork that you publish.
- `scripts/linkedin_publish.py` performs one create when you run it for one post id. Nothing in the demo, the tests, or the scheduler calls it against LinkedIn.
- Preview HTML is local. It is not a LinkedIn session.

## License

Third-party MIT code and the adapted humanize helpers stay under their own notices. See [LICENSES.md](LICENSES.md) and `.cursor/skills/li-human/NOTICE.md`.

A project-wide license for the original code is not chosen yet. The candidate and the comparison are in [docs/licensing.md](docs/licensing.md). Do not treat this tree as licensed for redistribution until a `LICENSE` file is added.
