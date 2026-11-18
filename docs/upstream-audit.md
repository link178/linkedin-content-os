# Upstream audit

P0 record of [linkedin-agent-skill](https://github.com/Jakeschincariol/linkedin-agent-skill). The vendored tree is reference material. It is not the production content engine, and its growth advice is not a set of product invariants.

Labels used below:

| Label | Meaning |
| --- | --- |
| Upstream implementation fact | Behavior present in the inspected files. |
| Upstream author recommendation | Advice in prose or data, not enforced by code. |
| Our architectural decision | What this repository will do. |

## Repository snapshot

| | |
| --- | --- |
| Upstream | https://github.com/Jakeschincariol/linkedin-agent-skill |
| Commit | `add2c23882fe79180737d242ff80a5da205eda6a` |
| Branch | `main` (matches GitHub `main` on 2026-09-23) |
| Commit date | 2026-09-07 |
| Subject | The LinkedIn agent skill: 11 Claude skills, v1.0 |
| License | MIT |
| Copyright | Copyright (c) 2026 Jake Schincariol |
| Plugin version | `1.0.0` in `.claude-plugin/plugin.json` |
| Vendored | 2026-09-23, unmodified, at `vendor/linkedin-agent-skill/` |
| Excluded | `.git` only. No `__pycache__`, `.venv`, or `.DS_Store` was in the tree. |

### Inspected tree

```text
.claude-plugin/marketplace.json
.claude-plugin/plugin.json
.gitignore
LICENSE
README.md
skills/li-audit/SKILL.md
skills/li-carousel/SKILL.md
skills/li-comment/SKILL.md
skills/li-dm/SKILL.md
skills/li-human/SKILL.md
skills/li-human/detect.py
skills/li-human/humanize.py
skills/li-human/slop.json
skills/li-inbox/SKILL.md
skills/li-plan/SKILL.md
skills/li-post/SKILL.md
skills/li-post/hooks.json
skills/li-profile/SKILL.md
skills/li-profile/rubric.json
skills/li-reply/SKILL.md
skills/li-repurpose/SKILL.md
templates/voice.md
```

`LICENSE`, `README.md`, `.gitignore`, both plugin manifests, `templates/voice.md`, every `SKILL.md`, `hooks.json`, `rubric.json`, `detect.py`, and `humanize.py` were read in full. `slop.json` was inventoried by schema and entry counts, and its `invisible`, `typographic`, and `structures` sections were read in full. The lexical lists were sampled, not read line by line: 81 words, 32 phrases, 113 terms total, matching the README. Counts: 17 invisible classes, 11 typographic substitutions, 11 structures.

### Runtime

There is no `package.json`, `requirements.txt`, `pyproject.toml`, Dockerfile, or network client.

`humanize.py` and `detect.py` use the Python 3 standard library only (`argparse`, `json`, `os`, `re`, `statistics`, `sys`, `unicodedata`). They load `slop.json` from the script directory via `os.path.dirname(__file__)`. They do not upload text.

The pack is built for Claude Code: skill frontmatter, `~/.claude/skills/`, and `.claude-plugin/`. The README also says a single `SKILL.md` can be pasted into a chat. Our runtime is Cursor Agent Skills under `.cursor/skills/`.

## Dependency map

The skills share state by convention, not by a library:

```text
templates/voice.md  --copied by the user to-->  ~/.claude/linkedin/voice.md
li-post, li-plan                              read voice.md
li-post, li-comment                           append ~/.claude/linkedin/log.md
li-plan                                       writes ~/.claude/linkedin/plan.md
li-plan, li-audit, li-repurpose               read log.md and/or plan.md
li-post, li-plan, li-repurpose, li-audit      refer to hooks.json formula ids
li-post, li-comment, li-reply, li-profile,
li-dm, li-carousel, li-repurpose              require a /li-human pass
li-human SKILL.md                             shells out to humanize.py and detect.py
humanize.py, detect.py                        both require slop.json keys
                                              words, phrases, and (by script) more
li-audit                                      hands conclusions to li-plan
```

`li-audit` names `hooks.json` without a path. Resolution depends on the model, not on a file lookup.

There are no credentials, cookies, or OAuth files.

## Classification summary

| Component | Class |
| --- | --- |
| Never-fabricate rule | KEEP |
| Explicit human publish gate | KEEP |
| No browser automation or scraping | KEEP |
| Invisible-character deletion in `humanize.py` | KEEP |
| `li-post` | ADAPT |
| `li-plan` | ADAPT |
| `li-human` (skill, lexicon, scores) | ADAPT |
| `hooks.json` | ADAPT |
| `slop.json` | ADAPT |
| `humanize.py` typography and lexicon passes | ADAPT |
| `detect.py` | ADAPT |
| `templates/voice.md` | ADAPT |
| `li-audit` | DEFER |
| `li-profile` and `rubric.json` | DEFER |
| `li-carousel` | DEFER |
| `li-comment` | REFERENCE |
| `li-reply` | REFERENCE |
| `li-repurpose` | REFERENCE |
| `li-dm` | DROP |
| `li-inbox` | DROP |
| `.claude-plugin/` | DROP |
| `~/.claude/linkedin/` state files | DROP |

## Component inventory

### `li-post` — ADAPT

**Purpose.** Turn one idea into three hook options, one draft, a humanizer pass, and a copy-ready block.

**Useful concepts.** One idea per post. Ask for a missing fact instead of padding. Show hook alternatives. Never publish from the skill. On approval, remember what was drafted so a later audit can see it. Leave a visible placeholder when a number is missing (`{{your number}}` in the upstream text).

**Problematic assumptions.** Working length 900–1,300 characters; under 400 is "a thought"; over 2,000 must earn every line. Feed truncation at ~140 mobile characters and ~210 desktop characters (`hooks.json` note). Ranking claim that LinkedIn rewards dwell time, saves, and comments in that order. "No links in the post body" because outbound links are suppressed. Three hashtags maximum. A suggested post time taken from the weekly plan.

**Dependencies.** `~/.claude/linkedin/voice.md`, `hooks.json`, `/li-human`, and `~/.claude/linkedin/log.md`.

**Rationale.** The draft loop matches our pipeline up through human approval. The length, link, hashtag, and ranking rules stay hypotheses. State moves into `content/` and `knowledge/`, and missing numbers must resolve to the proof registry rather than a free-typed placeholder that can still ship.

### `li-plan` — ADAPT

**Purpose.** A weekly plan: what to post, when, and who to engage with.

**Useful concepts.** Plans come from something that actually happened. An angle is not a topic. Mix proof, opinion, teaching, story, and offer. Say so when posting-time tuning is ahead of the writing. The plan is not a scheduler.

**Problematic assumptions.** Four posts a week beat seven, and the fifth post is usually the weak one. Tuesday–Thursday 7:30–9:30am local for B2B, with Monday afternoon and Friday morning as second tier. Weekends are for personal stories or nothing. Engage 10 people split 5 reach / 3 peers / 2 buyers. Comment before a post has 20 comments. 20 minutes of commenting a day, before posting. Story and offer each once a fortnight.

**Dependencies.** `voice.md`, `log.md`, `li-post/hooks.json`, writes `plan.md`.

**Rationale.** Idea selection from real events is the part we want in `li-plan`. Cadence, clock times, and the engagement quota are author recommendations. Our planner will not encode them as defaults. Engagement lists are outside the core pipeline.

### `li-audit` — DEFER

**Purpose.** Post-mortem on posts the user has already published.

**Useful concepts.** The account's own posts outrank generic guides. The skill says this about itself: every rule in the pack is a prior. Rank by engagement rate and reach multiple, not raw impressions. Define comment ratio and save/send rate when the export has them. Look at hook, format, length, and theme before day and time. With a handful of posts, say the sample is too small. Pass conclusions back into planning.

**Problematic assumptions.** Save/send rate is "the strongest single predictor of future reach." First-hour replies do most of the distribution work (also stated in `li-reply`). The worked example calls an n=6 split "not close," while the instructions in the same file say six posts are not enough to see a pattern.

**Dependencies.** User-supplied CSV or screenshots, `log.md`, `hooks.json`, then `li-plan`. No importer script exists.

**Rationale.** This belongs in P5–P6, after we have imports and a publish history. The metric definitions are the part to adapt then. They do not become invariants now. Our skill of the same name must not be confused with pre-publish `li-review`.

### `li-human` — ADAPT

**Purpose.** Clean a draft and score it with five local checks before the user sees it.

**Useful concepts.** Run on the machine. Do not claim to be GPTZero, Originality, Copyleaks, Winston, or Turnitin. Strip invisible format characters. Flag sentence-shape problems instead of regex-rewriting them. Let the user delete lexicon entries they actually say. Show the text and the score together.

**Problematic assumptions.** PASS requires overall ≥ 70 and no check below 55. REVIEW is overall ≥ 50; otherwise FLAGGED. Overall = 60% mean + 40% weakest check. Em dashes should become commas. Curly quotes, ellipses, and bullets should be normalized. A fixed lexicon is "more human." Two cleanup rounds are normal; five means the draft should be thrown away.

**Dependencies.** `humanize.py`, `detect.py`, `slop.json`. No third-party packages. Other skills call this by name.

**Rationale.** Cleanup is part of P2 `li-human`, after voice exists, so the lexicon can be edited first. The numeric gate is an author threshold implemented in `detect.py`. It will not block approval. The invisible-character pass is the sub-part we KEEP as-is when we adapt the script.

#### `humanize.py` and `detect.py`

Implementation facts from the scripts:

- Three mutating passes: invisible/format characters, typography, lexicon. URLs and email-like tokens are masked first.
- Structural regexes are reported, not applied.
- `humanize.py` also flags sentence-length coefficient of variation below 0.35.
- `detect.py` maps burstiness CV from 0.22 (score 0) to 0.70 (score 100) and prints "want 0.55+".
- Specificity "human" anchor is 6 concrete markers per 100 words; "machine" anchor is 0.5.
- Slop density "machine" anchor is 4 hits per 100 words.
- Fingerprint "machine" anchor is 12 weighted marks per 1,000 characters.
- Voice score mixes contractions (weight 0.35), pronouns (0.35), and structural tells (0.30), then subtracts 12 for uniform bullets.
- `detect.py` exits 0 only on PASS. Any other verdict exits 1.
- Docstrings mention `pbpaste` and `python3`. Those are usage examples. The code path is stdin or a file path and is not macOS-only.
- Shebang is `#!/usr/bin/env python3`.

Our decision: vendor the scripts unchanged. Do not put them on `PATH`. Do not treat exit status 1 as a failed draft. Reconcile the 0.35 / 0.55 / 0.70 variation thresholds before any of them becomes a check we trust.

#### `slop.json` — ADAPT

Schema: `invisible`, `typographic`, `words`, `phrases`, `structures`.

The invisible list (17 classes, including a catch-all Cf deletion in code) is the KEEP-adjacent data. Typography substitutions and the 113-term lexicon are style policy. Several replacements delete phrases outright (`replace` is empty). The triad regex (`\b\w+, \w+, and \w+\.`) flags ordinary English. The hashtag-wall fix text says "Three hashtags. LinkedIn stopped rewarding the wall." That platform claim is an author recommendation stored as a rewrite hint.

Edit the lexicon against `knowledge/voice.md` in P2 before any production run. Do not treat a lexicon hit as a factual error.

### `hooks.json` — ADAPT

21 formulas, each with `id`, `name`, `template`, `example`, `best_for`, and `trap`. Five writing rules sit at the top of the file: one idea, line 2 pays off line 1, questions in line 1 must be specific, numbers beat adjectives, a hook that works under someone else's name is weak.

Those five rules are reusable drafting constraints. The `best_for` field mixes craft notes with performance claims that have no dataset in the repo:

| Id | Name | Performance claim in `best_for` |
| --- | --- | --- |
| 1 | Contrarian Take | Highest comment rate of any formula |
| 5 | The List Promise | Highest save rate; LinkedIn weights saves heavily |
| 9 | Story Cold Open | Highest dwell time |
| 10 | The Receipt | Pairing with an image roughly doubles reach |
| 12 | The Comparison | Very high share rate |
| 17 | Time Anchor | Strongest hook shape for a how-to |
| 20 | The Walk-Away | Very high engagement |
| 21 | The Direct Value | Highest conversion of the 21 |

Formula 14 adds a cadence rule in `trap`: pattern interrupts once a month.

Our decision: adapt templates and traps in P2 as a catalog of shapes. Quarantine superlatives. Do not store "highest comment rate" as data the planner can sort on.

### `templates/voice.md` — ADAPT

Sections: who I am, sound, positions, off limits, proof I can use. The file tells the user to copy it to `~/.claude/linkedin/voice.md`, or to paste three posts and have the model write the file.

Our decision: the section list informs P1 `knowledge/voice.md`, `positioning.md`, `guardrails.md`, and `proof-registry.yaml`. Proof entries must be supplied evidence, not an inferred voice card. We will not create the home-directory file.

### `li-profile` and `rubric.json` — DEFER

**Purpose.** Score a pasted profile out of 100 across 12 items, then rewrite in points-lost order. Does not log into LinkedIn.

**Useful concepts.** User pastes the text. Fix the highest-point gaps first. A rewrite cannot invent recommendations, a banner, or a posting history. Run copy through the humanizer. Re-score honestly.

**Problematic assumptions.** Point weights (headline 12, activity 10, and so on) are the author's rubric. "Most profiles land in the 30s and 40s" is a note in `rubric.json`, not a measurement. Headline formula `{what you do for whom} | {proof} | {how to start}`. About body under 1,400 characters. Experience older than ten years collapses to one line. Activity points require a post or comment in the last seven days. Photo guidance ("face fills roughly 60%") is aesthetic advice.

**Dependencies.** `rubric.json`, `/li-human`.

**Rationale.** Profile tooling is P7. The rubric stays reference material. It is not a LinkedIn scoring API.

### `li-carousel` — DEFER

**Purpose.** Slide copy for a document post, plus an optional HTML-to-PDF step after the user approves the copy.

**Useful concepts.** Use a carousel for a sequence, and a text post for a single claim. Approve copy before building a PDF. The user uploads the file.

**Problematic assumptions.** Document posts are the highest-dwell format because swipes are counted and scrolls are not. 8–12 slides; over 12, completion "falls off a cliff." Slide 1 is 80% of the result. Cover in six words. At most 25 words under a headline. PDF at 1080×1350, under 100MB, under 300 pages. Numbering slides raises completion. The PDF command is a comment, not a script.

**Dependencies.** `/li-human`. Optional Chrome headless print, not vendored.

**Rationale.** P7, if document posts are actually needed. Slide-count and dwell claims stay hypotheses.

### `li-comment` — REFERENCE

**Purpose.** Two comment options on a pasted post, typed by situation, then humanized. Batch mode logs who was commented on.

**Useful concepts.** Nine comment shapes (add a datum, missing case, disagree, extend, question, receipt, correction, reframe, one-liner). Refuse "Great post" openers. Do not guess the post from a URL. Do not auto-post.

**Problematic assumptions.** Commenting is the highest-leverage action on LinkedIn. A comment under a post with 400 reactions is seen by more people than most of the user's own posts. Length should be 2–4 sentences.

**Dependencies.** `/li-human`, `log.md`.

**Rationale.** Comment craft is not a core skill. The refusal to scrape is already an invariant. Keep the taxonomy as reading material.

### `li-reply` — REFERENCE

**Purpose.** Sort comments on the user's post into lead, substance, peer, support, and noise, then draft replies the user pastes.

**Useful concepts.** Answer in public. Match length. Concede the true part of a criticism. Ignore pitches. Nothing posts until the user says yes.

**Problematic assumptions.** The reply thread is where reach is decided, and the first hour does most of the work.

**Dependencies.** `/li-human`. Paste or screenshot only.

**Rationale.** Same as comments: useful craft notes, outside the evidence-to-analytics pipeline.

### `li-repurpose` — REFERENCE

**Purpose.** Pull claims, numbers, stories, mechanisms, mistakes, and quotable lines from a long asset, then assign each to a weekday and a hook id.

**Useful concepts.** Extract standalone items. Do not summarize. If the asset yields fewer than four items, say it is thin. Draft one post at a time. Do not write "as I mentioned in my video" unless that is true, and even then the post has to stand alone.

**Problematic assumptions.** One asset "contains four to six posts." A fixed week order: strongest claim, then story, then mechanism.

**Dependencies.** `hooks.json`, then `/li-post` and `/li-human`. Transcript must be pasted unless some other tool is already in the session.

**Rationale.** The extract-don't-summarize list can inform `li-ideas` if we later ingest long-form writing the user provides. The one-asset-to-a-week mill pattern is not the planner default. Not a planned skill.

### `li-dm` — DROP

**Purpose.** A 200-character invite note, a first message, and two follow-ups. The user sends them by hand.

**What we leave behind.** The 200-character invite limit is a product constraint of LinkedIn's note field, stated as author guidance to count characters. The rest of the cadence (+1 day, +4 days, +10 days), "two follow-ups is the number," "reply rate is 2%," and "never send more than 20 invites a day or LinkedIn throttles the account" are growth rules. Outreach is outside this content OS.

**Dependencies.** `/li-human`.

**Rationale.** Dropped from the product roadmap. The "do not automate outreach" warning is already covered by our publishing invariant. The file stays in the vendor snapshot.

### `li-inbox` — DROP

**Purpose.** Sort pasted messages into lead, recruiter, peer, ask, and spam, and draft replies.

**What we leave behind.** "Most inboxes are 80% noise." Sequence detection (no specifics, message within minutes, "quick question," calendar link, bump at four days) is a heuristic for recognizing automation. Inbox operations are outside the content pipeline.

**Dependencies.** `/li-human`. Explicitly refuses browser login.

**Rationale.** Dropped from the product roadmap. Vendor copy remains.

### `.claude-plugin/` — DROP

`plugin.json` and `marketplace.json` register a Claude Code plugin named `linkedin-agent`. They contain author and homepage metadata, not content logic.

Our decision: do not add a Cursor plugin manifest in P0. Leave the JSON in the vendor snapshot.

### Home-directory state — DROP

`voice.md`, `log.md`, and `plan.md` under `~/.claude/linkedin/` are the upstream integration bus. Our equivalents, when they exist, are files in `knowledge/` and `content/`.

## Explicit heuristic review

These items must not become system invariants without evidence from this account.

| Topic | Upstream implementation fact | Upstream author recommendation | Our architectural decision |
| --- | --- | --- | --- |
| Post length | Not computed in code | 900–1,300 characters is the working range | Voice and the draft decide length. Record length later if analytics support a range. |
| Truncation | Not computed in code | ~140 characters mobile, ~210 desktop, before "see more" | Treat as a UI hypothesis. Hooks still need to stand alone, because that is a writing constraint, not an algorithm claim. |
| Posting frequency | Not computed in code | Four posts a week; the fifth is usually weak | Planner has no numeric quota in P2. |
| Clock time | Not computed in code | Tue–Thu 7:30–9:30am for B2B | `li-plan` may leave time blank. `li-audit` checks time last, and only with enough posts. |
| Algorithm order | Not computed in code | Dwell, then saves, then comments | Hypothesis. Do not sort ideas by it. |
| Outbound links | Not computed in code | Body links are suppressed; put the link in the first comment | Hypothesis. A draft may include a link when the evidence needs one. |
| Hashtags | Regex flags 5+ hashtags; fix text says use three | "LinkedIn stopped rewarding the wall" | No hard hashtag cap until voice and analytics say otherwise. |
| Human score | `detect.py` PASS at ≥70 and minimum check ≥55; exit code 1 otherwise | Presented as the gate before the user sees a draft | Style signal only. Never an approval gate. |
| Burstiness targets | Flag CV < 0.35 in `humanize.py`; score anchors 0.22 and 0.70 in `detect.py` | "Want 0.55+" | Do not reconcile by picking a favorite constant. Revisit in P2 against real drafts. |
| Specificity | Score anchors 0.5 and 6 markers per 100 words | Numbers over adjectives in `li-post` and `hooks.json` | Real numbers must come from the proof registry. A density target is not required for a true sentence. |
| Hook rankings | Stored as `best_for` strings | "Highest comment rate," "highest save rate," "highest conversion," image reach "roughly doubles" | Catalog the shapes. Drop the rankings from any adapted data file. |
| Saves as predictor | Not computed | Strongest predictor of future reach | Compute save rate when an export has it. Do not assume the ranking. |
| Comment leverage | Not computed | Highest-leverage action; 400-reaction posts outperform one's own posts | Not an operating rule. |
| First-hour replies | Not computed | Where reach is decided | A P6 question, if reply timestamps exist. |
| Invite volume | Not computed | 20 invites/day, then throttle; 2% reply rate without a specific hook | Outreach stays out of the product. |
| Profile score | Points listed in `rubric.json`; nothing calls LinkedIn | 100-point rubric; first passes "land in the 30s and 40s" | P7 reference only. |
| Carousel performance | Not computed | Highest dwell; 8–12 slides; slide 1 is 80% | P7 hypothesis. |
| Em dash policy | `humanize.py` rewrites em dashes to commas | Em dashes are a machine tell | Voice file decides. Technical prose may keep them. |

## Coupling we will not carry forward

- Claude Code install paths and the plugin marketplace schema.
- A hidden home-directory bus shared by every skill.
- Cross-skill calls that assume `/li-human` has already defined "good."
- Formula ids as if they were measured tactics.
- `detect.py`'s process exit code as a quality gate.
- macOS paste examples and a `python3` shebang as the project interface. A future wrapper, if we need one, can call `python` explicitly.

## Reuse recommendation for P2

Adapt these assets. Do not copy the `SKILL.md` files into `.cursor/skills/` until the P2 design is written against real `knowledge/` files.

1. **`skills/li-post/SKILL.md` workflow.** Three hooks, one draft, cleanup, copy-ready block, explicit yes. Replace voice-file discovery with `knowledge/voice.md` and `knowledge/proof-registry.yaml`. Replace `{{your number}}` with an unresolved proof id that `li-review` must block.
2. **`skills/li-post/hooks.json` templates and traps.** Keep the 21 shapes as a catalog. Remove or clearly mark `best_for` performance superlatives in the adapted copy.
3. **`skills/li-plan/SKILL.md` angle selection.** Build slots from evidence and recent `content/` history. Omit the weekly quota, the clock-time table, and the 10-person engagement list from the default output.
4. **`skills/li-human/humanize.py`, `detect.py`, and `slop.json`.** After P1 voice exists, fork a project copy under a path P2 chooses. Keep invisible-character deletion. Make typography and lexicon follow voice. Report scores. Do not fail the skill when the score is under 70.
5. **`templates/voice.md` headings.** Already assigned to P1 knowledge files. P2 skills read those files and do not re-infer a persona from three posts.

Not P2 candidates:

- `li-audit` waits for P5 imports and P6.
- `li-profile`, `rubric.json`, and `li-carousel` wait for P7.
- `li-comment`, `li-reply`, and `li-repurpose` stay reference.
- `li-dm`, `li-inbox`, and `.claude-plugin/` stay dropped.

## What this audit did not do

No skill was installed. No script was rewritten. No personal career text was added. No LinkedIn account was contacted.
