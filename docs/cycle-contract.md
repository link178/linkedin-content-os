# Cycle contract (P6)

Headless preparation uses `scripts/cycle.py` with the Cursor CLI (`agent -p --mode ask --output-format json --trust`) as the LLM runtime. Python owns artifact writes, hashes, and lifecycle. The CLI workspace is a sanitized snapshot without `.env` or tokens.

## Commands

```text
python scripts/cycle.py run [--topic TITLE] [--selection editorial|strategy] [--refresh-research] [--content-root PATH]
python scripts/cycle.py discover [--refresh-research]
python scripts/cycle.py resume POST-YYYYMMDD-NN [--human-note TEXT]
python scripts/cycle.py ready POST-YYYYMMDD-NN
python scripts/cycle.py status POST-YYYYMMDD-NN
python scripts/inbox.py
```

`run` writes one JSON object to stdout and exits 0 only when `outcome` is `READY_FOR_HUMAN`. Every other outcome exits 1, including a partial cycle that created a draft. The success object is indented, so callers parse the document rather than a single line. The command does not approve or publish.

## Editorial selection

`run` without `--topic` uses editorial selection by default; `--selection strategy` keeps the previous lane-gap choice. `discover` runs the same selection without creating a post and exits non-zero unless a topic is selected.

1. **Research.** `scripts/editorial_discovery.py` fetches the feeds in `config/editorial-discovery.json` with Python, plus one Hacker News search as discussion evidence. A snapshot younger than `snapshot_max_age_hours` is reused; `--refresh-research` forces a new one. Output: `research/trends/<date>-discovery-NN.{json,md}`. Status is `OK`, `PARTIAL` (some sources or discussion failed), or `FAILED`. A failed refresh never falls back to an older snapshot. Hacker News points are not LinkedIn activity and are never reported as such.
2. **Assessment.** One ask-mode call receives the bounded trend candidates with article excerpts, the eligible strategy topics, recent theses (topic, status, and cited URLs), and eligible claims. It separates verified facts, discussion evidence, interpretation, and evergreen relevance, and judges significance, audience, angle strength, reader value, grounding, timeliness, and feasibility as labels, not scores. For every non-rejected candidate it states, separately, what the source already concludes (`source_contribution`) and what the post would add (`editorial_contribution`, typed as consequence, trade-off, decision criterion, comparison, synthesis, or implication; or `RESTATES_SOURCE` / `GENERIC`), which verified statements support it, which single statement is the key premise (`contribution_premise`), what it would need that the sources do not establish, the source limitations a post must keep visible, what the reader learns, whether the topic holds without its headline, and why now. Discussion volume and headline reach are not evidence of angle or value. It does not browse. The result is cached per snapshot, strategy set, and prior digest in `research/opportunities/<snapshot>.assessment.json`.
3. **Gates.** Python rejects with explicit codes: `STALE` (article publication older than `stale_after_days`, event or latest body-dated development outside the window, or `CURRENT` strategy topics past `stale_after_days`), `UNDATED`, `UNGROUNDED` (no stated fact is supported by an article excerpt or, for strategy topics, by `why_now` source text; unsupported statements are dropped), `UNSUPPORTED_THESIS`, `SIGNIFICANCE_CAPPED` (`MAJOR` needs two outlets or major discussion), `INVENTS_EXPERIENCE`, `NOT_FEASIBLE`, `BELOW_BAR` (minor significance, weak angle, or low reader value), `NO_CONTRIBUTION` (restates the source or is generic), `UNSUPPORTED_CONTRIBUTION` (the contribution or thesis asserts an outcome the assessment lists as not established; listed items otherwise force `QUALIFY` and join `do_not_claim`), `HEADLINE_DEPENDENT`, `REPETITIVE` and `ALREADY_USED` (against every attempted, dropped, approved, or published thesis), `AUDIENCE_CONFLICT`, `INVALID_TOPIC`, `MODEL_REJECTED`. A trend whose sources overlap a prior attempt is not rejected; it is demoted in ranking with a `PRIOR_ATTEMPT` note.
4. **Choice.** Eligible candidates are ordered by fresh source (not a prior attempt on the same URLs), angle strength, reader value, event freshness (`NEW_EVENT` / `MATERIAL_UPDATE` ahead of `COVERAGE_ONLY`; `STALE` is a gate), audience breadth, significance, grounding, then timeliness. Trend freshness uses calendar dates in source excerpts (including month-day phrases without a year, resolved against the article publication date), not the feed timestamp alone. Verified statement count in `contribution_support` remains on the decision record for transparency but does not break ties. Lane balance only breaks a full tie. Assessment may set `contribution_scope`: `SPECIALIST_GUIDANCE` labeled `BROAD_TECH` is relabeled to `AI_ENGINEERING` or `SPECIALIST`. A `STRONG` angle is capped to `ADEQUATE` when the contribution cites no verified statement (`CONTRIBUTION_UNANCHORED`), the key premise is not verified (`PREMISE_UNVERIFIED`), or the contribution mostly reuses the source headline and conclusion (`CONTRIBUTION_CAPPED`). The decision, every candidate with its codes, and the lane-gap choice it replaced are written to `research/opportunities/<snapshot>.decision.json`; the run copies it to `content/cycles/<post>.selection.json`, and the Inbox shows it.
5. **Outcomes.** No eligible candidate: `NO_EDITORIAL_OPPORTUNITY` with the rejection summary; nothing is composed. Research or assessment failure: `DISCOVERY_FAILURE`. A selected trend writes its source note under `research/trends/`; composition receives the thesis, the grounded facts, the source's own conclusion (to attribute, not to present as the post's insight), the source limitations to keep visible, and a `do_not_claim` list.

## Readiness

`READY_FOR_HUMAN` is derived, not a post `status`. It requires semantic, editorial, and critic `PASS` on the current body hash, current preview, passing `content_engine.py check`, and `human_editorial_decision` empty or `APPROVE`. It is not `approved` and not published.

## Critic

Frontmatter: `critic_verdict`, `critic_body_sha256`, `critic_reviewed_at`. Section: `## Critic review`. Verdicts: `PASS`, `REVISE`, `DROP`. When critic fields exist, `approve` requires critic `PASS`.

## Inbox

`python scripts/inbox.py` serves `http://127.0.0.1:8765/`.

- `GET /` is the dashboard: Generate Candidate, generation status, and recent candidates with lane, audience, and readiness. It does not open a historical candidate.
- `GET /candidate/POST-YYYYMMDD-NN` shows the body, verdicts, blocking issues, derived readiness, the existing HTML preview, human actions, and a collapsible **Revision history**. Human-requested `REVISE` actions are listed separately from automatic semantic, editorial, and critic revisions. After a consumed human revision, the verdicts distinguish **Previous human decision: REVISE** from **Current version: Awaiting human decision**; derived readiness is unchanged. History comes from `content/cycles/POST-*.yaml` (`human_revisions`, when present, or legacy `events` strings such as `revision N: human` and `human revision applied:`). Prior body text is not shown unless it was stored. Applied feedback copied from a legacy event is labeled truncated only when that stored text is 240 characters, which is the event cap. Shorter legacy notes are shown in full, and missing timestamps, hashes, and review verdicts stay blank. A semantic `BLOCKED` lists the deterministic unsupported-claim reasons. Editorial, critic, and preview lines are marked skipped after that terminal block, not as separate content failures.
- **Generate Candidate** starts `cycle.py run` without blocking the HTTP request. Status is `running`, `success`, or `error`.
- **APPROVE** is offered only when readiness is true, and the server checks readiness again. It sets `human_editorial_decision: APPROVE` only; run `python scripts/content_engine.py approve POST-...` separately.
- **REVISE** stores feedback and starts `cycle.py resume`. Human `REVISE` stays pending until a changed body is revalidated; an unchanged resume does not clear it.
- **DROP** sets `human_editorial_decision: DROP` and records the note on the cycle log.

## Revision cap

Default `max_revision_cycles = 2` counts automatic body revisions only, not reviewer runs.

Before semantic review, the cycle compares the body with the selected topic. A `POSITIONING` topic, or an accessible `BROAD_TECH` topic, that opens on a project metric is misaligned. So is `standalone_value: LOW` when the topic expects `HIGH` or `MEDIUM`, and `project_context_required: HIGH` when the topic expects `NONE` or `LIGHT`. A body assessed `SPECIALIST` does not satisfy an intended `BROAD_TECH` audience. That mismatch is terminal: the cycle records `STRATEGY_MISALIGNED` and does not ask for a rewrite that hides the metric lesson under a general opening. The other mismatches are revision instructions. If the cap is already spent, the outcome is `STRATEGY_MISALIGNED` and the error names the mismatch and the opening.

Topic selection reads the latest proposed strategy. It defers a `POSITIONING` / `BROAD_TECH` topic whose author evidence matches a retrieval-metric claim, and it defers a topic that repeats a recent metric result or decision when another eligible topic remains. The similarity window is the last four consumed topics for lane mix. Published and approved ideas stay in the similarity set after they leave that window. A `CAREER_ENGINEERING` topic with no project or metric claim that develops the thesis is deferred. Profile labels are not that claim.

Composition receives personal claims that develop the selected topic, not the whole registry. General technical analysis may be the post. An eligible claim is not a relevant claim. After composition, a blank or unreadable `## Claims` section stops the cycle before semantic review; use `- none` when no personal claim is cited. Semantic review receives the idea's `why_now` and cited trend note for external attribution checks; the script verdict stays authoritative. After composition, a cited project, metric, career, or profile claim that does not develop the topic stops the cycle as `PLAN_MISMATCH`. That stop does not spend a revision and does not ask for a different anecdote. Semantic validation is unchanged for project outcomes, derived framing, and blocked claims. An uncited engineering analysis may pass when it does not state a personal fact, copy a registry statement, or state a project outcome. A recorded semantic `BLOCKED` draft with a current hash remains auditable through `check` and `check-all` without becoming `READY_FOR_HUMAN`. An explicit topic name may repeat a recent subject. It may not force the deferred broad label. Lane choice among the topics that remain follows the soft mix as a preference, not a quota. `TREND_REACTION` topics marked `CURRENT` or `TIME_SENSITIVE` need a source in `why_now`. Editorial `REVISE` revisions receive the computed weakest element and structural change. A semantic `NEEDS_EDIT` for a missing conclusion frame names the required phrases (`My conclusion`, `I treat this as`, `The lesson I take`); a reviewer paraphrase does not replace that repair. A frame on one derived sentence does not frame a different derived sentence. When the script already passes, a model `NEEDS_EDIT` or `BLOCKED` overrides it only with a semantic concern that names the sentence, the claim id when there is one, what exceeds the evidence, whether wording can repair it, and the minimal correction. Contrast, hook, readability, and other presentation objections do not. A non-recoverable semantic concern stops without a wording revision. A recoverable concern is the revision instruction. The same concern after that repair is on the named sentence is a repeated reviewer request, and it does not spend another body revision. A framed paraphrase does not count while the named sentence is still bare. An unchanged body does not count as a revision. The exhaustion error names the unresolved sentence. A revision may reorder the opening. It may not invent evidence or change the topic lane or audience. The two-revision cap is unchanged.

## Global cycle lock

`run`, `resume`, and `discover` acquire `content/cycles/cycle.lock` before any pipeline work. A second process gets stdout `{"outcome":"SKIPPED_ALREADY_RUNNING","error":"..."}` and exit 1. Per-post `POST-*.lock` files still apply while a draft is running.

Scheduled runs use [`scripts/scheduled_cycle.py`](../scripts/scheduled_cycle.py) and the same lock. See [`docs/scheduler.md`](scheduler.md).

## Environment

`CONTENT_ROOT` overrides `content/` for subprocess checks. `CURSOR_AGENT` overrides the path to `agent.cmd`. Tests use `--fake-responses` instead of the CLI.
