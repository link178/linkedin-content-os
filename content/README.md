# content

Local strategy batches, ideas, plans, drafts, approved posts, and posts published by an explicit `/li-publish`.

| Path | What it holds |
| --- | --- |
| `strategy/` | `STRATEGY-YYYYMMDD-NN.md` files from `/li-strategy` |
| `ideas/` | `IDEA-YYYYMMDD-NN.md` files from `li-ideas` |
| `plans/` | `PLAN-YYYYMMDD-NN.md` files from `li-plan` |
| `drafts/` | `POST-YYYYMMDD-NN.md` files from `li-post`, then `li-human` and `li-review` |
| `approved/` | The same post id after an explicit `li-approve`. One canonical file, not a second copy. It stays here until an explicit `li-reopen` or `/li-publish`. |
| `published/` | The same post id after an explicit `/li-publish`. One canonical file. It is immutable. |
| `visuals/` | Optional `POST-ID/visual.yaml` and `visual.svg` from `/li-visual`. |
| `previews/` | Local `POST-ID.html` previews from `/li-preview`. |
| `history.jsonl` | Append-only approval, reopen, and publication audit. Created on the first approval. The post file remains the source of truth. |

Pipeline: **STRATEGY → IDEA → plan → post → semantic review → editorial review → optional visual → preview → approval.**

The shape, the claim rules, the body hash, and the approval rules are in [`docs/content-contract.md`](../docs/content-contract.md). Editorial standalone value rules are in [`docs/editorial-contract.md`](../docs/editorial-contract.md).

A review `PASS` leaves the post in `drafts/`. `python scripts/linkedin_access.py dry-run POST-...` can read an approved file. That command does not publish. `python scripts/linkedin_publish.py POST-...` is the publication command.
