"""Editorial discovery and adaptive topic selection. Synthetic fixtures only; no network, no LinkedIn."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from html import escape
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import content_engine as engine  # noqa: E402
import cursor_runtime as runtime_mod  # noqa: E402
import cycle as cycle_mod  # noqa: E402
import editorial_discovery as discovery  # noqa: E402
import inbox  # noqa: E402

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)

WIRE = "https://wire.example/feed"
TIMES = "https://times.example/feed"
VENDOR = "https://vendor.example/changelog/feed"

SOURCES = [
    {"id": "wire", "name": "Example Wire", "url": WIRE, "tier": "reporting", "categories": ["AI_EMPLOYMENT_SKILLS", "AI_SOCIETY"]},
    {"id": "times", "name": "Example Times", "url": TIMES, "tier": "reporting", "categories": ["TECH_MARKETS_BUSINESS"]},
    {"id": "vendor", "name": "Vendor Changelog", "url": VENDOR, "tier": "primary", "categories": ["SOFTWARE_ENGINEERING"]},
]

# Synthetic stories. None of these describe a real event.
AUDIT_WIRE = "https://wire.example/2026/11/12/regulator-orders-audits-of-ai-hiring-screeners"
AUDIT_TIMES = "https://times.example/business/regulator-ai-hiring-screener-audits"
AUDIT_TEXT = [
    "The regulator said employers with more than 250 staff must audit automated hiring screeners before using them on applicants.",
    "The audit rule takes effect in March 2027 and covers screening, ranking and interview scheduling tools used by large employers.",
    "Employers must keep the audit records for three years and show applicants which automated screeners were applied.",
]
VENDOR_URL = "https://vendor.example/changelog/2026-11-13-logs-json-flag"
VENDOR_TEXT = [
    "The vendor command line tool now accepts a json flag on the logs command, so pipelines can parse structured log output.",
]
JOBS_URL = "https://wire.example/2026/11/10/economists-split-on-coding-assistants-and-junior-hiring"
JOBS_TEXT = [
    "Economists interviewed for the report disagreed on whether coding assistants are reducing junior developer hiring at software companies.",
    "Two hiring surveys cited in the report show fewer junior developer postings this year, but neither survey isolates coding assistants as the cause.",
]
MARKET_URL = "https://times.example/markets/example-corp-cuts-cloud-forecast"
MARKET_TEXT = [
    "Example Corp shares fell nine percent after the company cut its cloud revenue forecast and said customers were delaying AI infrastructure spending.",
    "The company said enterprise customers are pausing large AI infrastructure purchases until pilot projects show returns.",
]
SURVEY_URL = "https://wire.example/2026/11/11/survey-ai-agents-replace-engineers"
SURVEY_TEXT = [
    "A survey of 400 engineering managers found that 22 percent plan to hire fewer junior engineers next year.",
    "The survey asked about hiring plans and did not measure whether coding agents replaced any existing engineers.",
]
SANDBOX_URL = "https://wire.example/2026/11/12/sandboxing-coding-agents-credentials"
SANDBOX_TEXT = [
    "Security researchers said local sandboxes for coding agents broker credentials through a proxy rather than removing them from command reach.",
]

EVERGREEN = {
    "topic": "Retries need idempotent handlers before tool calls repeat",
    "lane": "POSITIONING",
    "audience": "BROAD_TECH",
    "timeliness": "EVERGREEN",
    "standalone_value": "HIGH",
    "personal_evidence_strength": "LOW",
    "project_context_required": "NONE",
    "technical_depth": "ACCESSIBLE",
    "visual_potential": "NONE",
    "why_now": "Ambiguous outcomes after a call are a standing risk whenever the caller cannot tell completion from failure.",
    "why_this_author": "Backend API practice is background for idempotency thinking. No duplicate-call count is claimed.",
    "why_reader_cares": "A side effect that is unsafe to repeat turns every retry into a correctness question.",
}


def rss(items: list[tuple[str, str, datetime, str]]) -> bytes:
    entries = "".join(
        f"<item><title>{escape(title)}</title><link>{link}</link><pubDate>{format_datetime(when)}</pubDate>"
        f"<description>{escape(summary)}</description></item>"
        for title, link, when, summary in items
    )
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>feed</title>{entries}</channel></rss>'.encode()


def article(paragraphs: list[str]) -> bytes:
    body = "".join(f"<p>{escape(text)}</p>" for text in paragraphs)
    return f"<html><body><nav><p>Subscribe to our newsletter for weekly technology coverage and deals</p></nav>{body}<footer>c</footer></body></html>".encode()


def hn(stories: list[tuple[str, str, int]]) -> bytes:
    hits = [
        {"title": title, "url": url, "points": points, "num_comments": points // 3, "objectID": str(index), "created_at": "2026-11-12T10:00:00Z"}
        for index, (title, url, points) in enumerate(stories, start=1)
    ]
    return json.dumps({"hits": hits}).encode()


class FakeWeb:
    def __init__(self, pages: dict) -> None:
        self.pages = pages
        self.calls: list[str] = []

    def __call__(self, url: str, timeout: int) -> bytes:
        self.calls.append(url)
        key = "hn" if url.startswith("https://hn.algolia.com/") else url
        value = self.pages.get(key)
        if value is None:
            raise urllib.error.URLError(f"unreachable {url}")
        if isinstance(value, Exception):
            raise value
        return value


def make_settings(tmp: Path, web: FakeWeb, now: datetime = NOW, **config) -> discovery.DiscoverySettings:
    base = {
        "snapshot_max_age_hours": 24,
        "item_window_days": 10,
        "stale_after_days": 14,
        "max_items_per_source": 12,
        "max_candidates": 16,
        "max_candidates_per_category": 3,
        "max_sources_per_candidate": 3,
        "excerpt_chars": 1600,
        "request_timeout_sec": 5,
        "assessment_timeout_sec": 30,
        "discussion": {"min_points": 100, "major_points": 300},
        "categories": {"AI_EMPLOYMENT_SKILLS": "AI and jobs", "TECH_MARKETS_BUSINESS": "Markets", "SOFTWARE_ENGINEERING": "Software"},
        "sources": SOURCES,
    }
    base.update(config)
    return discovery.DiscoverySettings(
        config=base,
        research_dir=tmp / "research" / "trends",
        opportunity_dir=tmp / "research" / "opportunities",
        fetch=web,
        now=lambda: now,
    )


def web_for(stories: dict[str, list[tuple[str, str, datetime, list[str]]]], hn_stories=None) -> FakeWeb:
    pages: dict = {source["url"]: rss([]) for source in SOURCES}
    for feed, items in stories.items():
        pages[feed] = rss([(title, url, when, text[0]) for title, url, when, text in items])
        for _title, url, _when, text in items:
            pages[url] = article(text)
    pages["hn"] = hn(hn_stories or [])
    return FakeWeb(pages)


def opp(cid: str, **overrides) -> dict:
    entry = {
        "id": cid,
        "decision": "PURSUE",
        "category": "AI_EMPLOYMENT_SKILLS",
        "what_happened": "A development described by the cited sources.",
        "why_now": "It happened this week.",
        "who_is_affected": "Engineering leaders.",
        "audience": "BROAD_TECH",
        "specialist_only": False,
        "significance": "MODERATE",
        "practical_consequence": "Teams change a decision.",
        "decision_changed": "Which tools to adopt.",
        "source_contribution": "The source reports what was announced and who it covers.",
        "editorial_contribution": "Owners of an automated step need a criterion for when its output may be trusted unreviewed.",
        "contribution_type": "DECISION_CRITERION",
        "contribution_support": [0],
        "contribution_premise": 0,
        "contribution_overreach": [],
        "source_limitations": [],
        "reader_value": "MEDIUM",
        "reader_learns": "When to keep a human review step on an automated decision.",
        "holds_without_headline": True,
        "why_publish_now": "The development is this week.",
        "angle_strength": "ADEQUATE",
        "thesis": "The decision is about ownership of the process, not the tool.",
        "tension": "Speed against accountability.",
        "beyond_summary": "Connects the rule to everyday engineering ownership.",
        "grounding": "SOLID",
        "factual_statements": [],
        "interpretations": ["Ownership matters more than the tool."],
        "unsupported_implications": [],
        "unresolved_questions": [],
        "overlap": {"level": "NONE", "prior": "", "difference": ""},
        "composition_feasible": True,
        "requires_invented_experience": False,
        "relevant_claim_ids": [],
        "topic": {
            "topic": f"Topic for {cid}",
            "lane": "TREND_REACTION",
            "standalone_value": "HIGH",
            "personal_evidence_strength": "NONE",
            "project_context_required": "NONE",
            "technical_depth": "ACCESSIBLE",
            "visual_potential": "NONE",
            "why_this_author": "General engineering interpretation. No personal result is claimed.",
            "why_reader_cares": "Readers decide how much automated judgment to trust in a process they own.",
        },
        "reasons": f"Editorial judgment for {cid}.",
    }
    entry.update(overrides)
    return entry


def fact(text: str, url: str) -> dict:
    return {"text": text, "source_urls": [url]}


def ids_by_url(snapshot: dict) -> dict[str, str]:
    return {source["url"]: candidate["id"] for candidate in snapshot["candidates"] for source in candidate["sources"]}


def decide(settings, runtime, *, strategy=None, priors=None, used=None, recent=None, legacy=None):
    return discovery.decide(
        settings,
        runtime,
        strategy_topics=strategy or [],
        strategy_deferred=[],
        priors=priors or [],
        used_titles=used or set(),
        recent_lanes=recent or [],
        claims=engine.load_claims(),
        legacy_choice=legacy,
    )


def by_id(decision: dict) -> dict[str, dict]:
    return {entry["id"]: entry for entry in decision["candidates"]}


class ResearchRefreshTests(unittest.TestCase):
    def test_refresh_records_sources_dates_discussion_and_is_reused_while_fresh(self):
        old = NOW - timedelta(days=30)
        web = web_for(
            {
                WIRE: [
                    ("Regulator orders audits of AI hiring screeners at large employers", AUDIT_WIRE, NOW - timedelta(days=1), AUDIT_TEXT),
                    ("An old story about something else entirely", "https://wire.example/old", old, ["Old text that is long enough to be an excerpt paragraph."]),
                ],
                TIMES: [("Regulator orders AI hiring screener audits for large employers", AUDIT_TIMES, NOW - timedelta(days=2), AUDIT_TEXT)],
                VENDOR: [("Logs command gains a json flag", VENDOR_URL, NOW, VENDOR_TEXT)],
            },
            hn_stories=[("Regulator orders audits of AI hiring screeners", AUDIT_WIRE, 420)],
        )
        with tempfile.TemporaryDirectory() as tmp:
            settings = make_settings(Path(tmp), web)
            snapshot, mode = discovery.ensure_research(settings)
            self.assertEqual(mode, "refreshed")
            self.assertEqual(snapshot["status"], "OK")
            self.assertEqual(len(snapshot["candidates"]), 2)
            audit = next(c for c in snapshot["candidates"] if c["independent_sources"] == 2)
            self.assertEqual({s["url"] for s in audit["sources"]}, {AUDIT_WIRE, AUDIT_TIMES})
            self.assertEqual(audit["discussion"][0]["points"], 420)
            self.assertTrue(all(s["excerpt_origin"] == "article" for s in audit["sources"]))
            self.assertNotIn("Subscribe", audit["sources"][0]["excerpt"])
            self.assertFalse(any("old" in s["url"] for c in snapshot["candidates"] for s in c["sources"]))
            self.assertTrue(audit["latest_published"].startswith("2026-11-12"))
            md = (settings.research_dir / f"{snapshot['snapshot_id']}.md").read_text(encoding="utf-8")
            self.assertIn(AUDIT_WIRE, md)
            self.assertIn("not LinkedIn activity", md)
            calls = len(web.calls)
            again, mode = discovery.ensure_research(settings)
            self.assertEqual(mode, "reused")
            self.assertEqual(again["snapshot_id"], snapshot["snapshot_id"])
            self.assertEqual(len(web.calls), calls)
            settings.now = lambda: NOW + timedelta(hours=30)
            _later, mode = discovery.ensure_research(settings)
            self.assertEqual(mode, "refreshed")

    def test_coverage_of_one_development_is_one_candidate(self):
        day = NOW - timedelta(days=1)
        titles = [
            ("Dana Quill resigns as Examplecorp chief", "https://wire.example/a"),
            ("Dana Quill's exit leaves the board scrambling", "https://times.example/b"),
            ("Shares slide after Dana Quill departure", "https://vendor.example/c"),
            ("Examplecorp opens a new office in Lisbon", "https://wire.example/d"),
            ("Examplecorp Announces Quarterly Results", "https://times.example/e"),
        ] + [(f"Unrelated database release {index} improves backups", f"https://wire.example/f{index}") for index in range(5)]
        items = [{"title": title, "url": url, "published": day, "summary": ""} for title, url in titles]
        clusters = discovery.cluster_items(items)
        quill = [c for c in clusters if any("Quill" in item["title"] for item in c["items"])]
        self.assertEqual(len(quill), 1)
        self.assertEqual(len(quill[0]["items"]), 3)
        self.assertTrue(any(c["items"][0]["title"].endswith("Lisbon") and len(c["items"]) == 1 for c in clusters))

    def test_development_date_ignores_retrieval_dates(self):
        why = (
            "Local sandboxing became generally available 9 Oct 2026. GitHub docs retrieved 2026-11-12 describe defaults. "
            "See research/trends/2026-11-12-reconciliation.md."
        )
        self.assertEqual(discovery.development_date(why, NOW).date().isoformat(), "2026-10-09")
        self.assertEqual(
            discovery.development_date("Anthropic, 6 Nov 2026, retrieved 2026-11-12.", NOW).date().isoformat(), "2026-11-06"
        )
        self.assertIsNone(discovery.development_date("Retrieved 2026-11-12 from main.", NOW))


class SelectionScenarioTests(unittest.TestCase):
    def test_a_major_current_development_beats_a_weaker_evergreen_topic(self):
        web = web_for(
            {
                WIRE: [("Regulator orders audits of AI hiring screeners at large employers", AUDIT_WIRE, NOW - timedelta(days=1), AUDIT_TEXT)],
                TIMES: [("Regulator orders AI hiring screener audits for large employers", AUDIT_TIMES, NOW - timedelta(days=2), AUDIT_TEXT)],
            },
            hn_stories=[("Regulator orders audits of AI hiring screeners", AUDIT_WIRE, 420)],
        )
        with tempfile.TemporaryDirectory() as tmp:
            settings = make_settings(Path(tmp), web)
            snapshot, _ = discovery.ensure_research(settings)
            cid = ids_by_url(snapshot)[AUDIT_WIRE]
            runtime = cycle_mod.FakeRuntime(
                [
                    {
                        "opportunities": [
                            opp(
                                cid,
                                significance="MAJOR",
                                angle_strength="STRONG",
                                what_happened="A regulator ordered large employers to audit automated hiring screeners before use.",
                                thesis="An audit duty turns a hiring screener from a vendor feature into a system the employer must be able to explain.",
                                factual_statements=[
                                    fact(
                                        "Employers with more than 250 staff must audit automated hiring screeners before using them on applicants.",
                                        AUDIT_WIRE,
                                    ),
                                    fact("The audit rule takes effect in March 2027 and covers screening, ranking and interview scheduling tools.", AUDIT_TIMES),
                                ],
                                unresolved_questions=["How audits will be verified is not described."],
                                topic={**opp(cid)["topic"], "topic": "An audit duty makes hiring screeners an engineering ownership question"},
                            ),
                            opp("S01", significance="MODERATE", angle_strength="ADEQUATE", grounding="PARTIAL"),
                        ]
                    }
                ]
            )
            legacy = dict(EVERGREEN)
            decision = decide(settings, runtime, strategy=[EVERGREEN], legacy=legacy, recent=["TREND_REACTION", "TREND_REACTION"])
            self.assertEqual(decision["outcome"], "SELECTED")
            selected = decision["selected"]
            self.assertEqual(selected["id"], cid)
            self.assertEqual(selected["kind"], "TREND")
            self.assertTrue(any("angle_strength: STRONG vs ADEQUATE" in reason for reason in selected["why_selected"]))
            self.assertEqual(decision["legacy_lane_choice"]["title"], EVERGREEN["topic"])
            topic = selected["topic"]
            self.assertEqual(topic["timeliness"], "CURRENT")
            self.assertIn(AUDIT_WIRE, topic["why_now"])
            self.assertIn(f"research/trends/{snapshot['snapshot_id']}-{cid.casefold()}.md", topic["why_now"])
            self.assertEqual(cycle_mod.strategy_mod.trend_source_problem(topic, lambda _path: True), "")
            self.assertEqual(len(topic["grounded_facts"]), 2)
            note = (settings.research_dir / f"{snapshot['snapshot_id']}-{cid.casefold()}.md").read_text(encoding="utf-8")
            self.assertIn("- **url:** " + AUDIT_WIRE, note)
            self.assertIn("- **date:** 2026-11-12", note)
            self.assertIn("How audits will be verified is not described.", note)
            stored = json.loads((settings.opportunity_dir / f"{snapshot['snapshot_id']}.decision.json").read_text(encoding="utf-8"))
            self.assertEqual(stored["selected"]["id"], cid)

    def test_b_newer_niche_release_does_not_outrank_a_broad_topic(self):
        web = web_for({VENDOR: [("Logs command gains a json flag", VENDOR_URL, NOW, VENDOR_TEXT)]})
        with tempfile.TemporaryDirectory() as tmp:
            settings = make_settings(Path(tmp), web)
            snapshot, _ = discovery.ensure_research(settings)
            cid = ids_by_url(snapshot)[VENDOR_URL]
            vendor_fact = fact("The vendor command line tool now accepts a json flag on the logs command.", VENDOR_URL)
            runtime = cycle_mod.FakeRuntime(
                [
                    {
                        "opportunities": [
                            opp(cid, significance="MAJOR", specialist_only=True, audience="SPECIALIST", factual_statements=[vendor_fact]),
                            opp("S01", significance="MODERATE", angle_strength="STRONG", grounding="PARTIAL"),
                        ]
                    }
                ]
            )
            decision = decide(settings, runtime, strategy=[EVERGREEN])
            entries = by_id(decision)
            self.assertEqual(decision["selected"]["id"], "S01")
            self.assertEqual(entries[cid]["dims"]["significance"], "MODERATE")
            self.assertTrue(any(note.startswith("SIGNIFICANCE_CAPPED") for note in entries[cid]["notes"]))
            self.assertTrue(
                any(
                    "angle_strength: STRONG" in reason or "audience: BROAD_TECH vs SPECIALIST" in reason
                    for reason in decision["selected"]["why_selected"]
                )
            )
            minor = cycle_mod.FakeRuntime([{"opportunities": [opp(cid, significance="MINOR", factual_statements=[vendor_fact]), opp("S01")]}])
            (settings.opportunity_dir / f"{snapshot['snapshot_id']}.assessment.json").unlink()
            decision = decide(settings, minor, strategy=[EVERGREEN])
            self.assertIn("BELOW_BAR", by_id(decision)[cid]["codes"])
            self.assertEqual(decision["selected"]["id"], "S01")

    def test_c_no_compelling_trend_selects_evergreen_or_reports_no_opportunity(self):
        old = NOW - timedelta(days=40)
        web = web_for({WIRE: [("Old story", "https://wire.example/old", old, ["Old text that is long enough for a paragraph excerpt."])]})
        stale_current = {
            **EVERGREEN,
            "topic": "Sandbox defaults deserve a review",
            "lane": "TREND_REACTION",
            "timeliness": "CURRENT",
            "why_now": "Local sandboxing became generally available 9 Oct 2026. Docs retrieved 2026-11-12.",
        }
        with tempfile.TemporaryDirectory() as tmp:
            settings = make_settings(Path(tmp), web)
            runtime = cycle_mod.FakeRuntime([{"opportunities": [opp("S01", angle_strength="STRONG"), opp("S02", significance="MAJOR")]}])
            decision = decide(settings, runtime, strategy=[EVERGREEN, stale_current])
            self.assertEqual(decision["research"]["status"], "OK")
            self.assertEqual(decision["research"]["trend_candidates"], 0)
            self.assertEqual(decision["selected"]["id"], "S01")
            self.assertEqual(decision["selected"]["dims"]["timeliness"], "EVERGREEN")
            self.assertIn("STALE", by_id(decision)["S02"]["codes"])
            self.assertNotIn("research/trends/", json.dumps(decision["selected"]["topic"]))
        with tempfile.TemporaryDirectory() as tmp:
            settings = make_settings(Path(tmp), web)
            runtime = cycle_mod.FakeRuntime([{"opportunities": [opp("S01", significance="MINOR")]}])
            decision = decide(settings, runtime, strategy=[EVERGREEN])
            self.assertEqual(decision["outcome"], "NO_EDITORIAL_OPPORTUNITY")
            self.assertIsNone(decision["selected"])
            self.assertIn("BELOW_BAR", decision["no_opportunity_reason"])
            self.assertEqual(list(settings.research_dir.glob("*-c0*.md")), [])

    def test_d_repeated_thesis_is_detected_under_a_new_title(self):
        web = web_for({WIRE: [("Researchers say agent sandboxes broker credentials through a proxy", SANDBOX_URL, NOW - timedelta(days=1), SANDBOX_TEXT)]})
        priors = [
            {
                "topic": "A local sandbox brokers credentials; it does not remove them from command reach",
                "insight": "A sandbox that brokers credentials through a proxy still lets generated commands use them.",
                "status": "published",
            }
        ]
        sandbox_fact = fact("Local sandboxes for coding agents broker credentials through a proxy rather than removing them from command reach.", SANDBOX_URL)
        with tempfile.TemporaryDirectory() as tmp:
            settings = make_settings(Path(tmp), web)
            snapshot, _ = discovery.ensure_research(settings)
            cid = ids_by_url(snapshot)[SANDBOX_URL]
            recycled = opp(
                cid,
                factual_statements=[sandbox_fact],
                thesis="A sandbox brokers credentials through a proxy, so generated commands can still use credentials within reach.",
                topic={**opp(cid)["topic"], "topic": "Coding agent sandboxes still put credentials within command reach"},
            )
            decision = decide(settings, cycle_mod.FakeRuntime([{"opportunities": [recycled]}]), priors=priors)
            entry = by_id(decision)[cid]
            self.assertIn("REPETITIVE", entry["codes"])
            self.assertEqual(decision["outcome"], "NO_EDITORIAL_OPPORTUNITY")
            (settings.opportunity_dir / f"{snapshot['snapshot_id']}.assessment.json").unlink()
            declared = opp(
                cid,
                factual_statements=[sandbox_fact],
                thesis="Isolation is a product boundary decision.",
                overlap={"level": "SAME_THESIS", "prior": priors[0]["topic"], "difference": ""},
            )
            decision = decide(settings, cycle_mod.FakeRuntime([{"opportunities": [declared]}]), priors=priors)
            self.assertIn("REPETITIVE", by_id(decision)[cid]["codes"])

    def test_d_dropped_and_attempted_topics_stay_in_the_originality_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            content_root = Path(tmp)
            for index, (topic, outcome) in enumerate(
                [("Judge RAG on defined evaluations not vibes", "DROPPED"), ("Telemetry splits model and tool steps", "REVISION_EXHAUSTED")],
                start=1,
            ):
                cycle_mod.save_cycle(
                    content_root,
                    cycle_mod.CycleState(post_id=f"POST-2026100{index}-01", topic=topic, lane="POSITIONING", audience="BROAD_TECH", outcome=outcome),
                )
            for index in range(3, 9):
                cycle_mod.save_cycle(
                    content_root,
                    cycle_mod.CycleState(post_id=f"POST-2026110{index}-01", topic=f"Later topic {index}", lane="POSITIONING", audience="BROAD_TECH"),
                )
            priors = {item["topic"]: item["status"] for item in cycle_mod.originality_priors(content_root)}
            self.assertEqual(priors["Judge RAG on defined evaluations not vibes"], "dropped")
            self.assertEqual(priors["Telemetry splits model and tool steps"], "attempted")

    def test_e_unsupported_headline_implication_is_rejected_or_qualified(self):
        web = web_for({WIRE: [("AI coding agents will replace half of software engineers, survey suggests", SURVEY_URL, NOW - timedelta(days=2), SURVEY_TEXT)]})
        survey_fact = fact("A survey of 400 engineering managers found that 22 percent plan to hire fewer junior engineers next year.", SURVEY_URL)
        invented = fact("Half of software engineers have already been replaced by coding agents at surveyed firms.", SURVEY_URL)
        implication = "Coding agents will replace half of software engineers."
        with tempfile.TemporaryDirectory() as tmp:
            settings = make_settings(Path(tmp), web)
            snapshot, _ = discovery.ensure_research(settings)
            cid = ids_by_url(snapshot)[SURVEY_URL]
            overclaim = opp(
                cid,
                factual_statements=[survey_fact, invented],
                unsupported_implications=[implication],
                thesis="Coding agents will replace half of software engineers, so careers must change now.",
            )
            decision = decide(settings, cycle_mod.FakeRuntime([{"opportunities": [overclaim]}]))
            entry = by_id(decision)[cid]
            self.assertIn("UNSUPPORTED_THESIS", entry["codes"])
            self.assertTrue(any(note.startswith("UNVERIFIED_STATEMENTS_DROPPED") for note in entry["notes"]))
            (settings.opportunity_dir / f"{snapshot['snapshot_id']}.assessment.json").unlink()
            qualified = opp(
                cid,
                factual_statements=[survey_fact, invented],
                unsupported_implications=[implication],
                thesis="A hiring-plan survey is a signal about junior pipelines, not evidence that engineers were replaced.",
            )
            decision = decide(settings, cycle_mod.FakeRuntime([{"opportunities": [qualified]}]))
            selected = decision["selected"]
            self.assertEqual(selected["id"], cid)
            self.assertEqual(selected["dims"]["decision"], "QUALIFY")
            self.assertIn(implication, selected["topic"]["do_not_claim"])
            self.assertIn(invented["text"], selected["topic"]["do_not_claim"])
            self.assertEqual(selected["topic"]["grounded_facts"], [survey_fact["text"]])
            self.assertEqual(selected["dims"]["grounding"], "PARTIAL")
            prompt = cycle_mod.compose_prompt(selected["topic"])
            self.assertIn("Do not assert or imply them", prompt)
            self.assertIn(implication, prompt)
            (settings.opportunity_dir / f"{snapshot['snapshot_id']}.assessment.json").unlink()
            ungrounded = opp(cid, factual_statements=[invented])
            decision = decide(settings, cycle_mod.FakeRuntime([{"opportunities": [ungrounded]}]))
            self.assertIn("UNGROUNDED", by_id(decision)[cid]["codes"])

    def test_f_employment_debate_and_market_move_are_judged_on_the_same_dimensions(self):
        web = web_for(
            {
                WIRE: [("Economists split on whether coding assistants cut junior developer hiring", JOBS_URL, NOW - timedelta(days=3), JOBS_TEXT)],
                TIMES: [("Example Corp shares fall after it cuts cloud forecast on AI spending pause", MARKET_URL, NOW - timedelta(days=1), MARKET_TEXT)],
            },
            hn_stories=[("Example Corp cuts cloud forecast", MARKET_URL, 350), ("Economists split on coding assistants and junior hiring", JOBS_URL, 310)],
        )
        jobs_fact = fact("Two hiring surveys show fewer junior developer postings this year, but neither isolates coding assistants as the cause.", JOBS_URL)
        market_fact = fact("Example Corp shares fell nine percent after the company cut its cloud revenue forecast.", MARKET_URL)
        with tempfile.TemporaryDirectory() as tmp:
            settings = make_settings(Path(tmp), web)
            snapshot, _ = discovery.ensure_research(settings)
            ids = ids_by_url(snapshot)
            jobs, market = ids[JOBS_URL], ids[MARKET_URL]

            def run(jobs_dims: dict, market_dims: dict) -> dict:
                cache = settings.opportunity_dir / f"{snapshot['snapshot_id']}.assessment.json"
                cache.unlink(missing_ok=True)
                payload = {
                    "opportunities": [
                        opp(jobs, factual_statements=[jobs_fact], topic={**opp(jobs)["topic"], "topic": "Junior hiring data does not yet isolate coding assistants"}, **jobs_dims),
                        opp(market, category="TECH_MARKETS_BUSINESS", factual_statements=[market_fact], topic={**opp(market)["topic"], "topic": "A paused AI infrastructure budget is a demand signal"}, **market_dims),
                    ]
                }
                return decide(settings, cycle_mod.FakeRuntime([payload]))

            first = run({"significance": "MAJOR", "angle_strength": "STRONG"}, {"significance": "MAJOR", "angle_strength": "ADEQUATE"})
            self.assertEqual(first["selected"]["id"], jobs)
            self.assertTrue(any("angle_strength: STRONG vs ADEQUATE" in reason for reason in first["selected"]["why_selected"]))
            second = run({"significance": "MAJOR", "angle_strength": "ADEQUATE"}, {"significance": "MAJOR", "angle_strength": "STRONG"})
            self.assertEqual(second["selected"]["id"], market)
            for decision in (first, second):
                for entry in decision["candidates"]:
                    self.assertEqual(
                        set(entry["dims"]) - {"decision"},
                        set(discovery.RANK_DIMENSIONS) | {"anchored_support", "rank_development_kind"},
                    )
                    self.assertTrue(entry["sources"][0]["published"].startswith("2026-11-1"))
                    self.assertTrue(entry["discussion"])

    def test_lane_balance_only_breaks_a_full_editorial_tie(self):
        web = web_for({})
        evergreen_b = {**EVERGREEN, "topic": "Career paths need concrete practice", "lane": "PROJECT_SHOWCASE"}
        with tempfile.TemporaryDirectory() as tmp:
            settings = make_settings(Path(tmp), web)
            tie = cycle_mod.FakeRuntime([{"opportunities": [opp("S01"), opp("S02")]}])
            decision = decide(settings, tie, strategy=[EVERGREEN, evergreen_b], recent=["POSITIONING", "POSITIONING"])
            self.assertEqual(decision["selected"]["id"], "S02")
            self.assertIn("lane balance broke the tie", decision["selected"]["why_selected"][1])
        with tempfile.TemporaryDirectory() as tmp:
            settings = make_settings(Path(tmp), web)
            better = cycle_mod.FakeRuntime([{"opportunities": [opp("S01", angle_strength="STRONG"), opp("S02")]}])
            decision = decide(settings, better, strategy=[EVERGREEN, evergreen_b], recent=["POSITIONING", "POSITIONING"])
            self.assertEqual(decision["selected"]["id"], "S01")


# Synthetic stories for the contribution scenarios. None of these describe a real event.
RUIN_URL = "https://wire.example/2026/11/12/no-ai-agents-did-not-find-an-unknown-hillside-ruin"
RUIN_TITLE = "No, AI agents did not find an unknown hillside ruin"
RUIN_TEXT = [
    "An amateur set a swarm of AI agents on public lidar scans and announced a possibly unknown hillside ruin.",
    "The ruin had already been documented in a regional journal last year, and archaeologists said the agents' flag was not a discovery.",
    "The agents compared their flags against one public registry, and the journal record was not part of that registry.",
]
RUIN_FACT = "The ruin had already been documented in a regional journal last year, and archaeologists said the flag was not a discovery."
RUIN_REGISTRY_FACT = "The agents compared their flags against one public registry, and the journal record was not part of that registry."
PUSH_URL = "https://vendor.example/changelog/2026-11-12-ordered-reference-updates"
PUSH_TITLE = "How the platform orders only reference updates for concurrent agent pushes"
PUSH_TEXT = [
    "The platform team said objects from concurrent agent pushes are written in parallel while only reference updates are ordered.",
    "In internal tests the storage layer handled 6,000 pushes per second to one repository.",
]
PUSH_FACT = "Objects from concurrent agent pushes are written in parallel while only reference updates are ordered."
OUTAGE_WIRE = "https://wire.example/2026/11/12/office-suite-outage-60-countries"
OUTAGE_TIMES = "https://times.example/business/office-suite-outage-email-calendars"
OUTAGE_TEXT = [
    "An outage of a cloud office suite disrupted email and calendars for users in 60 countries on Thursday.",
    "The provider said a group of servers became unavailable and strained the remaining systems.",
]
OUTAGE_FACT = "An outage of a cloud office suite disrupted email and calendars for users in 60 countries on Thursday."


def contribution_web() -> FakeWeb:
    day = NOW - timedelta(days=1)
    return web_for(
        {
            WIRE: [(RUIN_TITLE, RUIN_URL, day, RUIN_TEXT), ("Office suite outage disrupts email in 60 countries", OUTAGE_WIRE, day, OUTAGE_TEXT)],
            TIMES: [("Office suite outage disrupts email and calendars in 60 countries", OUTAGE_TIMES, day, OUTAGE_TEXT)],
            VENDOR: [(PUSH_TITLE, PUSH_URL, day, PUSH_TEXT)],
        },
        hn_stories=[("My AI agents found an unknown ruin?", "https://amateur.example/ruin", 1033), ("Office suite outage", OUTAGE_WIRE, 900)],
    )


def ruin(cid: str, **overrides) -> dict:
    base = {
        "category": "AI_SOCIETY",
        "factual_statements": [fact(RUIN_FACT, RUIN_URL), fact(RUIN_REGISTRY_FACT, RUIN_URL)],
        "source_contribution": "An AI agent flag is not a discovery; amateurs with AI tools lack the expertise to judge what is new.",
        "topic": {**opp(cid)["topic"], "topic": "Agent flags are hypotheses until checked against prior work"},
    }
    base.update(overrides)
    return opp(cid, **base)


def push(cid: str, **overrides) -> dict:
    base = {
        "category": "SOFTWARE_ENGINEERING",
        "audience": "AI_ENGINEERING",
        "factual_statements": [fact(PUSH_FACT, PUSH_URL)],
        "source_contribution": "The platform parallelises object writes and orders only reference updates to support many agents.",
        "editorial_contribution": (
            "Teams adding coding agents should budget for contention on the branch they all update, "
            "because parallel object storage does not remove that ordered step."
        ),
        "contribution_type": "CONSEQUENCE",
        "angle_strength": "STRONG",
        "reader_value": "HIGH",
        "source_limitations": ["The 6,000 pushes per second figure is an internal test, not a customer guarantee."],
        "topic": {**opp(cid)["topic"], "topic": "More coding agents move contention to the shared branch"},
    }
    base.update(overrides)
    return opp(cid, **base)


def outage(cid: str, **overrides) -> dict:
    base = {
        "category": "TECH_COMPANY_CHANGES",
        "significance": "MAJOR",
        "factual_statements": [fact(OUTAGE_FACT, OUTAGE_WIRE)],
        "source_contribution": "A cloud office suite outage disrupted email and calendars in 60 countries after servers became unavailable.",
        "editorial_contribution": "Organisations should have a fallback for communication when a single provider fails.",
        "contribution_type": "CONSEQUENCE",
        "angle_strength": "ADEQUATE",
        "reader_value": "MEDIUM",
        "topic": {**opp(cid)["topic"], "topic": "One provider outage is a communication continuity question"},
    }
    base.update(overrides)
    return opp(cid, **base)


class ContributionScenarioTests(unittest.TestCase):
    """Newsworthiness, a good source summary, and an editorial contribution are judged separately."""

    def run_case(self, build, *, priors=None) -> tuple[dict, dict]:
        with tempfile.TemporaryDirectory() as tmp:
            settings = make_settings(Path(tmp), contribution_web())
            snapshot, _ = discovery.ensure_research(settings)
            ids = {"ruin": ids_by_url(snapshot)[RUIN_URL], "push": ids_by_url(snapshot)[PUSH_URL], "outage": ids_by_url(snapshot)[OUTAGE_WIRE]}
            strategy, payload = build(ids)
            decision = decide(
                settings,
                cycle_mod.FakeRuntime([{"opportunities": payload}]),
                strategy=strategy,
                priors=priors or [],
            )
            return decision, ids

    def test_a_source_summary_masquerading_as_insight_is_rejected(self):
        decision, ids = self.run_case(
            lambda ids: (
                [EVERGREEN],
                [
                    ruin(
                        ids["ruin"],
                        angle_strength="STRONG",
                        reader_value="HIGH",
                        contribution_type="RESTATES_SOURCE",
                        editorial_contribution="An AI agent flag is not a discovery without domain expertise.",
                        thesis="An AI agent marking a location is not a discovery.",
                    ),
                    opp("S01"),
                ],
            )
        )
        entry = by_id(decision)[ids["ruin"]]
        self.assertIn("NO_CONTRIBUTION", entry["codes"])
        self.assertEqual(entry["dims"]["angle_strength"], "WEAK")
        self.assertEqual(entry["status"], "REJECTED")
        self.assertEqual(decision["selected"]["id"], "S01")

    def test_a_reworded_source_conclusion_cannot_claim_a_strong_angle(self):
        decision, ids = self.run_case(
            lambda ids: (
                [EVERGREEN],
                [
                    ruin(
                        ids["ruin"],
                        angle_strength="STRONG",
                        contribution_type="IMPLICATION",
                        editorial_contribution="Amateurs with AI agent tools lack the expertise to judge a discovery.",
                    ),
                    opp("S01", angle_strength="STRONG"),
                ],
            )
        )
        entry = by_id(decision)[ids["ruin"]]
        self.assertEqual(entry["dims"]["angle_strength"], "ADEQUATE")
        self.assertTrue(any(note.startswith("CONTRIBUTION_CAPPED") for note in entry["notes"]))
        self.assertEqual(entry["status"], "ELIGIBLE")
        self.assertEqual(decision["selected"]["id"], "S01")

    def test_b_grounded_insight_that_extends_the_source_is_selected_with_its_limits(self):
        limitation = "Absence from one public registry is not evidence that a site is unknown."
        decision, ids = self.run_case(
            lambda ids: (
                [EVERGREEN],
                [
                    ruin(
                        ids["ruin"],
                        angle_strength="STRONG",
                        reader_value="HIGH",
                        contribution_type="DECISION_CRITERION",
                        contribution_support=[1],
                        contribution_premise=1,
                        editorial_contribution=(
                            "Before calling an agent result new, name the reference set it was checked against; "
                            "a miss in one registry only says the registry is incomplete."
                        ),
                        source_limitations=[limitation],
                        thesis="Absent from the registry is a statement about the registry, not about the world.",
                    ),
                    opp("S01"),
                ],
            )
        )
        selected = decision["selected"]
        self.assertEqual(selected["id"], ids["ruin"])
        self.assertEqual(selected["dims"]["angle_strength"], "STRONG")
        self.assertTrue(any("angle_strength: STRONG vs ADEQUATE" in reason for reason in selected["why_selected"]))
        topic = selected["topic"]
        self.assertIn(limitation, topic["source_limitations"])
        self.assertIn("not a discovery", topic["source_conclusion"])
        prompt = cycle_mod.compose_prompt(topic)
        self.assertIn("do not present it as the post's own insight", prompt)
        self.assertIn(limitation, prompt)

    def test_c_major_news_with_a_generic_contribution_does_not_qualify(self):
        decision, ids = self.run_case(
            lambda ids: (
                [],
                [
                    outage(ids["outage"], factual_statements=[fact(OUTAGE_FACT, OUTAGE_WIRE), fact(OUTAGE_FACT, OUTAGE_TIMES)], contribution_type="GENERIC", angle_strength="STRONG"),
                    push(ids["push"], significance="MODERATE"),
                ],
            )
        )
        entries = by_id(decision)
        self.assertEqual(entries[ids["outage"]]["dims"]["significance"], "MAJOR")
        self.assertIn("NO_CONTRIBUTION", entries[ids["outage"]]["codes"])
        self.assertEqual(decision["selected"]["id"], ids["push"])

    def test_d_moderate_engineering_news_with_a_useful_consequence_beats_major_news(self):
        decision, ids = self.run_case(
            lambda ids: ([], [outage(ids["outage"]), push(ids["push"], significance="MODERATE")])
        )
        entries = by_id(decision)
        self.assertEqual(entries[ids["outage"]]["status"], "ELIGIBLE")
        self.assertEqual(entries[ids["outage"]]["dims"]["audience"], "BROAD_TECH")
        selected = decision["selected"]
        self.assertEqual(selected["id"], ids["push"])
        self.assertEqual(selected["dims"]["audience"], "AI_ENGINEERING")
        self.assertTrue(any("angle_strength: STRONG vs ADEQUATE" in reason for reason in selected["why_selected"]))
        self.assertIn("internal test", " ".join(selected["topic"]["source_limitations"]))

    def test_e_evergreen_topic_outranks_recent_news_with_weak_angles(self):
        decision, ids = self.run_case(
            lambda ids: (
                [EVERGREEN],
                [
                    outage(ids["outage"]),
                    ruin(ids["ruin"], holds_without_headline=False, angle_strength="STRONG"),
                    push(ids["push"], reader_value="LOW", angle_strength="ADEQUATE"),
                    opp("S01", angle_strength="STRONG", reader_value="HIGH", significance="MODERATE"),
                ],
            )
        )
        entries = by_id(decision)
        self.assertIn("HEADLINE_DEPENDENT", entries[ids["ruin"]]["codes"])
        self.assertIn("BELOW_BAR", entries[ids["push"]]["codes"])
        self.assertEqual(entries[ids["outage"]]["status"], "ELIGIBLE")
        self.assertEqual(decision["selected"]["id"], "S01")
        self.assertEqual(decision["selected"]["dims"]["timeliness"], "EVERGREEN")
        self.assertTrue(any(f"won over {ids['outage']}" in reason for reason in decision["selected"]["why_selected"]))

    def test_f_unsupported_extrapolation_from_a_factual_article_is_rejected(self):
        invented = "Most amateur AI archaeology claims turn out to be false."
        decision, ids = self.run_case(
            lambda ids: (
                [],
                [
                    ruin(
                        ids["ruin"],
                        angle_strength="STRONG",
                        contribution_type="IMPLICATION",
                        editorial_contribution="Agent-led science will flood journals with false discoveries unless gatekeeping tightens.",
                        contribution_overreach=["Agent-led science is flooding journals with false discoveries"],
                    ),
                    push(
                        ids["push"],
                        factual_statements=[fact(invented, PUSH_URL), fact(PUSH_FACT, PUSH_URL)],
                        contribution_support=[0],
                        contribution_overreach=["The internal throughput figure is a customer guarantee"],
                    ),
                ],
            )
        )
        entries = by_id(decision)
        self.assertIn("UNSUPPORTED_CONTRIBUTION", entries[ids["ruin"]]["codes"])
        self.assertEqual(entries[ids["ruin"]]["status"], "REJECTED")
        push_entry = entries[ids["push"]]
        self.assertTrue(any(note.startswith("CONTRIBUTION_UNANCHORED") for note in push_entry["notes"]))
        self.assertEqual(push_entry["dims"]["angle_strength"], "ADEQUATE")
        self.assertNotIn("UNSUPPORTED_CONTRIBUTION", push_entry["codes"])
        self.assertEqual(push_entry["dims"]["decision"], "QUALIFY")
        self.assertEqual(decision["selected"]["id"], ids["push"])
        self.assertIn(invented, push_entry["topic"]["do_not_claim"])
        self.assertIn("The internal throughput figure is a customer guarantee", push_entry["topic"]["do_not_claim"])

    def test_g_no_sufficiently_strong_angle_is_no_opportunity(self):
        decision, ids = self.run_case(
            lambda ids: (
                [EVERGREEN],
                [
                    ruin(ids["ruin"], contribution_type="RESTATES_SOURCE"),
                    outage(ids["outage"], reader_value="LOW"),
                    push(ids["push"], contribution_type="GENERIC"),
                    opp("S01", contribution_type="GENERIC"),
                ],
            )
        )
        self.assertEqual(decision["outcome"], "NO_EDITORIAL_OPPORTUNITY")
        self.assertIsNone(decision["selected"])
        self.assertIn("NO_CONTRIBUTION", decision["no_opportunity_reason"])
        self.assertIn("BELOW_BAR", decision["no_opportunity_reason"])

    def test_assessment_without_the_contribution_contract_is_malformed(self):
        legacy = opp("S01", angle_strength="STRONG")
        for key in ("source_contribution", "editorial_contribution", "contribution_type", "reader_value", "reader_learns"):
            legacy.pop(key)
        parsed = discovery.parse_assessment({"opportunities": [legacy, opp("S02", decision="REJECT", editorial_contribution="")]}, {"S01", "S02"})
        self.assertIn("editorial_contribution missing", parsed["S01"]["problems"])
        self.assertIn("contribution_type missing or invalid", parsed["S01"]["problems"])
        self.assertEqual(parsed["S02"]["problems"], [])

    def test_prompt_separates_source_conclusion_from_contribution_and_discounts_popularity(self):
        settings = make_settings(Path(tempfile.gettempdir()), contribution_web())
        prompt = discovery.assessment_prompt({"candidates": []}, [], [], [], settings, "2026-11-13")
        for needle in (
            "source_contribution",
            "editorial_contribution",
            "contribution_premise",
            "RESTATES_SOURCE",
            "holds_without_headline",
            "why_publish_now",
        ):
            self.assertIn(needle, prompt)
        self.assertIn("Hacker News points, headline reach, and brand names are not evidence of angle strength", prompt)
        self.assertEqual(discovery.RANK_DIMENSIONS[0], "prior_attempt")
        self.assertEqual(discovery.RANK_DIMENSIONS[3], "development_kind")
        self.assertIn("contribution_scope", prompt)
        self.assertLess(discovery.RANK_DIMENSIONS.index("audience"), discovery.RANK_DIMENSIONS.index("significance"))


class AnchoringAndPriorTests(unittest.TestCase):
    PRIOR = [{"topic": "t", "insight": "", "status": "attempted", "source_urls": [discovery.normalize_url(RUIN_URL)], "post_id": "POST-PRIOR"}]

    def test_premise_unverified_caps_strong_to_adequate(self):
        decision, ids = ContributionScenarioTests().run_case(
            lambda ids: (
                [],
                [
                    ruin(
                        ids["ruin"],
                        angle_strength="STRONG",
                        contribution_support=[0],
                        contribution_premise=1,
                        factual_statements=[fact(RUIN_FACT, RUIN_URL)],
                    ),
                    push(ids["push"]),
                ],
            )
        )
        entry = by_id(decision)[ids["ruin"]]
        self.assertEqual(entry["dims"]["angle_strength"], "ADEQUATE")
        self.assertTrue(any(note.startswith("PREMISE_UNVERIFIED") for note in entry["notes"]))

    def test_grounding_count_does_not_beat_broader_audience(self):
        extra_fact = "In internal tests the storage layer handled 6,000 pushes per second to one repository."
        decision, ids = ContributionScenarioTests().run_case(
            lambda ids: (
                [],
                [
                    ruin(
                        ids["ruin"],
                        audience="BROAD_TECH",
                        angle_strength="STRONG",
                        reader_value="HIGH",
                        contribution_support=[0],
                        contribution_premise=0,
                        factual_statements=[fact(RUIN_FACT, RUIN_URL)],
                    ),
                    push(
                        ids["push"],
                        audience="AI_ENGINEERING",
                        angle_strength="STRONG",
                        reader_value="HIGH",
                        contribution_support=[0, 1],
                        contribution_premise=0,
                        factual_statements=[fact(PUSH_FACT, PUSH_URL), fact(extra_fact, PUSH_URL)],
                    ),
                ],
            )
        )
        selected = decision["selected"]
        self.assertEqual(selected["id"], ids["ruin"])
        self.assertGreater(by_id(decision)[ids["push"]]["dims"]["anchored_support"], selected["dims"]["anchored_support"])
        self.assertFalse(any(reason.split(" on ")[1].startswith("anchored_support") for reason in selected["why_selected"] if "won over" in reason))
        self.assertTrue(any("audience" in reason for reason in selected["why_selected"]))

    def test_prior_attempt_on_same_url_is_demoted_but_eligible(self):
        decision, ids = ContributionScenarioTests().run_case(
            lambda ids: (
                [],
                [
                    ruin(ids["ruin"], angle_strength="STRONG", reader_value="HIGH", contribution_premise=0),
                    push(ids["push"]),
                ],
            ),
            priors=self.PRIOR,
        )
        ruin_entry = by_id(decision)[ids["ruin"]]
        self.assertEqual(ruin_entry["status"], "ELIGIBLE")
        self.assertTrue(ruin_entry["dims"]["prior_attempt"])
        self.assertTrue(any(note.startswith("PRIOR_ATTEMPT") for note in ruin_entry["notes"]))
        self.assertEqual(decision["selected"]["id"], ids["push"])

    def test_prior_attempt_still_wins_when_it_is_the_only_eligible_candidate(self):
        decision, ids = ContributionScenarioTests().run_case(
            lambda ids: (
                [],
                [ruin(ids["ruin"], angle_strength="STRONG", reader_value="HIGH", contribution_premise=0)],
            ),
            priors=self.PRIOR,
        )
        ruin_entry = by_id(decision)[ids["ruin"]]
        self.assertTrue(ruin_entry["dims"]["prior_attempt"])
        self.assertEqual(decision["selected"]["id"], ids["ruin"])

    def test_assessment_key_changes_when_priors_change(self):
        snapshot = {"snapshot_id": "2026-11-13-discovery-99", "candidates": []}
        strategy = [{"id": "S01", "topic": {"topic": "x"}}]
        base = discovery.assessment_key(snapshot, strategy, [])
        with_prior = discovery.assessment_key(
            snapshot,
            strategy,
            [{"topic": "AI agents", "status": "attempted", "source_urls": ["theverge.com/foo"]}],
        )
        self.assertNotEqual(base, with_prior)

    def test_strategy_factual_statements_are_verified_against_why_now(self):
        anthropic_line = (
            "Claude Code loads only the first 200 lines or 25 KB of MEMORY.md as an injected index each session."
        )
        topic = {
            **EVERGREEN,
            "timeliness": "CURRENT",
            "why_now": (
                f"Anthropic documented agent memory injection bounds on 6 Nov 2026. {anthropic_line} "
                "https://www.anthropic.com/research/components-of-agent-memory"
            ),
        }
        bogus = "Every agent product enforces the same 25 KB bound by law."
        with tempfile.TemporaryDirectory() as tmp:
            settings = make_settings(Path(tmp), web_for({}))
            snapshot = {
                "snapshot_id": "2026-11-13-discovery-strategy",
                "status": "OK",
                "candidates": [],
                "sources": [],
                "discussion": {"status": "ok"},
            }
            settings.allow_refresh = False
            settings.force_refresh = False
            settings.research_dir.mkdir(parents=True, exist_ok=True)
            engine.write_atomic(
                settings.research_dir / f"{snapshot['snapshot_id']}.json",
                json.dumps(snapshot, indent=2) + "\n",
            )
            assessment = opp(
                "S01",
                factual_statements=[
                    fact(anthropic_line, "https://www.anthropic.com/research/components-of-agent-memory"),
                    fact(bogus, "https://www.anthropic.com/research/components-of-agent-memory"),
                ],
                contribution_support=[0, 1],
                contribution_premise=0,
            )
            decision = decide(settings, cycle_mod.FakeRuntime([{"opportunities": [assessment]}]), strategy=[topic])
            entry = decision["candidates"][0]
            self.assertEqual(entry["dims"]["anchored_support"], 1)
            self.assertTrue(any("UNVERIFIED_STATEMENTS_DROPPED" in note for note in entry["notes"]))

    def test_fresh_candidate_outranks_stronger_labels_on_a_prior_attempt(self):
        decision, ids = ContributionScenarioTests().run_case(
            lambda ids: (
                [],
                [
                    ruin(
                        ids["ruin"],
                        angle_strength="STRONG",
                        reader_value="HIGH",
                        contribution_premise=0,
                        factual_statements=[fact(RUIN_FACT, RUIN_URL), fact(RUIN_REGISTRY_FACT, RUIN_URL)],
                        contribution_support=[0, 1],
                    ),
                    push(ids["push"], angle_strength="ADEQUATE", reader_value="MEDIUM"),
                ],
            ),
            priors=self.PRIOR,
        )
        self.assertEqual(decision["selected"]["id"], ids["push"])
        self.assertFalse(by_id(decision)[ids["push"]]["dims"]["prior_attempt"])


def trend_candidate(cid: str, url: str, published: datetime, excerpt: str) -> dict:
    return {
        "id": cid,
        "title": f"Title {cid}",
        "categories": ["SOFTWARE_ENGINEERING"],
        "first_published": discovery.iso(published),
        "latest_published": discovery.iso(published),
        "independent_sources": 1,
        "sources": [
            {
                "source_id": "wire",
                "source_name": "Wire",
                "tier": "reporting",
                "title": f"Title {cid}",
                "url": url,
                "published": discovery.iso(published),
                "excerpt": excerpt,
                "excerpt_origin": "article",
                "fetch_error": "",
            }
        ],
        "discussion": [],
    }


def run_timeline_case(candidates, opportunities, *, now=NOW, priors=None):
    with tempfile.TemporaryDirectory() as tmp:
        settings = make_settings(Path(tmp), web_for({}), now=now)
        snapshot = {"snapshot_id": "2026-11-16-discovery-fixture", "status": "OK", "candidates": candidates}
        ids = {candidate["id"] for candidate in candidates}
        assessments = discovery.parse_assessment({"opportunities": opportunities}, ids)
        results = discovery.evaluate(
            settings,
            snapshot,
            [],
            assessments,
            priors=priors or [],
            used_titles=set(),
            claims=engine.load_claims(),
        )
        winner, comparisons = discovery.choose(results, [])
        return winner, {entry["id"]: entry for entry in results}, comparisons


class FreshnessAndScopeTests(unittest.TestCase):
    URL_A = "https://wire.example/story-a"
    URL_B = "https://wire.example/story-b"
    URL_C = "https://wire.example/story-c"
    URL_D = "https://wire.example/story-d"
    URL_E = "https://wire.example/story-e"

    def test_body_dates_without_year_resolve_against_publication(self):
        pub = NOW
        text = (
            "The vulnerability was publicly disclosed on 5 November and exploitation reports followed. "
            "CISA added the CVE to its catalog on 9 November."
        )
        timeline = discovery.trend_development_timeline(
            trend_candidate("C13", self.URL_A, pub, text),
            NOW,
            timedelta(days=14),
        )
        self.assertEqual(timeline["event_date"], "2026-11-05")
        self.assertEqual(timeline["latest_development"], "2026-11-09")
        self.assertEqual(timeline["development_kind"], "MATERIAL_UPDATE")

    def test_old_event_in_body_is_stale_despite_fresh_article(self):
        pub = NOW
        excerpt = "Analysts revisited the flaw disclosed on 5 October 2025 and repeated patch guidance."
        candidate = trend_candidate("C01", self.URL_A, pub, excerpt)
        assessment = opp(
            "C01",
            factual_statements=[fact("Analysts revisited the flaw disclosed on 5 October 2025.", self.URL_A)],
            contribution_support=[0],
            contribution_premise=0,
        )
        _winner, entries, _comparisons = run_timeline_case([candidate], [assessment])
        self.assertIn("STALE", entries["C01"]["codes"])

    def test_coverage_only_loses_to_new_event_at_equal_merit(self):
        pub = NOW
        recap = trend_candidate(
            "C01",
            self.URL_A,
            pub,
            "The company launched the feature on 10 November 2026 and teams are patching affected systems.",
        )
        fresh = trend_candidate(
            "C02",
            self.URL_B,
            pub,
            "The vendor launched a decisions API for multimodal routing with no dated follow-up in the excerpt.",
        )
        recap_assess = opp(
            "C01",
            audience="BROAD_TECH",
            angle_strength="STRONG",
            reader_value="HIGH",
            factual_statements=[fact("The company launched the feature on 10 November 2026.", self.URL_A)],
            contribution_support=[0],
            contribution_premise=0,
        )
        fresh_assess = opp(
            "C02",
            audience="AI_ENGINEERING",
            angle_strength="STRONG",
            reader_value="HIGH",
            factual_statements=[fact("The vendor launched a decisions API for multimodal routing.", self.URL_B)],
            contribution_support=[0],
            contribution_premise=0,
        )
        winner, entries, comparisons = run_timeline_case([recap, fresh], [recap_assess, fresh_assess])
        self.assertEqual(entries["C01"]["dims"]["development_kind"], "COVERAGE_ONLY")
        self.assertEqual(entries["C02"]["dims"]["development_kind"], "NEW_EVENT")
        self.assertEqual(winner["id"], "C02")
        self.assertTrue(any("development_kind" in reason for reason in comparisons))

    def test_material_update_ranks_with_new_event(self):
        pub = NOW
        update = trend_candidate(
            "C01",
            self.URL_C,
            pub,
            "The issue was disclosed on 5 November. CISA listed it on 9 November while exploitation continued.",
        )
        newer = trend_candidate("C02", self.URL_D, pub, "The vendor shipped a new API surface for agent planners.")
        assess_update = opp(
            "C01",
            angle_strength="STRONG",
            reader_value="HIGH",
            factual_statements=[
                fact("The issue was disclosed on 5 November.", self.URL_C),
                fact("CISA listed it on 9 November.", self.URL_C),
            ],
            contribution_support=[0, 1],
            contribution_premise=1,
        )
        assess_new = opp(
            "C02",
            angle_strength="ADEQUATE",
            reader_value="MEDIUM",
            factual_statements=[fact("The vendor shipped a new API surface for agent planners.", self.URL_D)],
            contribution_support=[0],
            contribution_premise=0,
        )
        winner, entries, _comparisons = run_timeline_case([update, newer], [assess_update, assess_new])
        self.assertEqual(entries["C01"]["dims"]["development_kind"], "MATERIAL_UPDATE")
        self.assertEqual(winner["id"], "C01")

    def test_specialist_guidance_relabels_broad_tech(self):
        pub = NOW
        candidate = trend_candidate("C01", self.URL_E, pub, "Apply OpenSSH 10.6p2 and restart sshd on Alpine hosts.")
        assessment = opp(
            "C01",
            audience="BROAD_TECH",
            contribution_scope="SPECIALIST_GUIDANCE",
            angle_strength="STRONG",
            reader_value="HIGH",
            factual_statements=[fact("Administrators should apply OpenSSH 10.6p2 and restart the service.", self.URL_E)],
            contribution_support=[0],
            contribution_premise=0,
        )
        _winner, entries, _comparisons = run_timeline_case([candidate], [assessment])
        self.assertEqual(entries["C01"]["dims"]["audience"], "AI_ENGINEERING")
        self.assertTrue(any(note.startswith("AUDIENCE_RELABELED") for note in entries["C01"]["notes"]))

    def test_general_decision_security_stays_broad_and_can_win(self):
        pub = NOW
        broad = trend_candidate(
            "C01",
            self.URL_A,
            pub,
            "musl-based OpenSSH without PAM can treat locked accounts as loginable under narrow conditions.",
        )
        narrow = trend_candidate("C02", self.URL_B, pub, "A vendor API exposes a new planner primitive.")
        broad_assess = opp(
            "C01",
            audience="BROAD_TECH",
            contribution_scope="GENERAL_DECISION",
            angle_strength="STRONG",
            reader_value="HIGH",
            factual_statements=[fact("musl-based OpenSSH without PAM can treat locked accounts as loginable.", self.URL_A)],
            contribution_support=[0],
            contribution_premise=0,
        )
        narrow_assess = opp(
            "C02",
            audience="AI_ENGINEERING",
            angle_strength="STRONG",
            reader_value="HIGH",
            factual_statements=[fact("A vendor API exposes a new planner primitive.", self.URL_B)],
            contribution_support=[0],
            contribution_premise=0,
        )
        winner, _entries, comparisons = run_timeline_case([broad, narrow], [broad_assess, narrow_assess])
        self.assertEqual(winner["id"], "C01")
        self.assertTrue(any("audience" in reason for reason in comparisons))


class FailureTests(unittest.TestCase):
    def test_g_research_failure_does_not_reuse_old_trends_as_fresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            good = web_for(
                {WIRE: [("Regulator orders audits of AI hiring screeners at large employers", AUDIT_WIRE, NOW - timedelta(days=3), AUDIT_TEXT)]}
            )
            earlier = make_settings(root, good, now=NOW - timedelta(days=2))
            old_snapshot, _ = discovery.ensure_research(earlier)
            self.assertEqual(len(old_snapshot["candidates"]), 1)
            settings = make_settings(root, FakeWeb({}))
            runtime = cycle_mod.FakeRuntime([{"opportunities": [opp("S01")]}])
            decision = decide(settings, runtime, strategy=[EVERGREEN])
            self.assertEqual(decision["research"]["status"], "FAILED")
            self.assertEqual(decision["research"]["mode"], "failed")
            self.assertEqual(decision["research"]["trend_candidates"], 0)
            self.assertEqual(decision["selected"]["kind"], "STRATEGY")
            self.assertEqual(len(decision["research"]["failed_sources"]), len(SOURCES))
            failed = json.loads((settings.research_dir / f"{decision['research']['snapshot_id']}.json").read_text(encoding="utf-8"))
            self.assertEqual(failed["status"], "FAILED")
            self.assertEqual(failed["candidates"], [])

    def test_g_discussion_outage_is_partial_research(self):
        web = web_for({WIRE: [("Regulator orders audits of AI hiring screeners", AUDIT_WIRE, NOW - timedelta(days=1), AUDIT_TEXT)]})
        web.pages["hn"] = TimeoutError("timed out")
        with tempfile.TemporaryDirectory() as tmp:
            snapshot, _ = discovery.ensure_research(make_settings(Path(tmp), web))
            self.assertEqual(snapshot["status"], "PARTIAL")
            self.assertEqual(snapshot["discussion"]["status"], "error")
            self.assertEqual(snapshot["candidates"][0]["discussion"], [])

    def test_g_malformed_or_failed_assessment_is_a_controlled_failure(self):
        web = web_for({WIRE: [("Regulator orders audits of AI hiring screeners", AUDIT_WIRE, NOW - timedelta(days=1), AUDIT_TEXT)]})
        with tempfile.TemporaryDirectory() as tmp:
            settings = make_settings(Path(tmp), web)
            with self.assertRaises(discovery.DiscoveryError) as raised:
                decide(settings, cycle_mod.FakeRuntime([{"verdict": "PASS"}]), strategy=[EVERGREEN])
            self.assertEqual(raised.exception.code, "DISCOVERY_FAILURE")
            with self.assertRaises(discovery.DiscoveryError):
                decide(settings, cycle_mod.FakeRuntime([{"opportunities": [{"id": "ZZ9"}]}]), strategy=[EVERGREEN])

            class Timeout:
                def ask(self, prompt, **kwargs):
                    raise runtime_mod.CursorRuntimeError("agent timed out after 30s")

            with self.assertRaises(discovery.DiscoveryError) as raised:
                decide(settings, Timeout(), strategy=[EVERGREEN])
            self.assertIn("timed out", str(raised.exception))
            bad_entry = cycle_mod.FakeRuntime([{"opportunities": [opp("C01", significance="HUGE"), opp("S01")]}])
            decision = decide(settings, bad_entry, strategy=[EVERGREEN])
            self.assertIn("MALFORMED_ASSESSMENT", by_id(decision)["C01"]["codes"])
            self.assertEqual(decision["selected"]["id"], "S01")

    def test_g_orchestrator_failure_leaves_no_candidate_and_no_lifecycle_change(self):
        previous = (engine.CONTENT, cycle_mod.ACTIVE_CONTENT_ROOT)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                content_root = root / "content"
                content_root.mkdir()
                engine.CONTENT = cycle_mod.ACTIVE_CONTENT_ROOT = content_root
                settings = make_settings(root, FakeWeb({}))

                class Timeout:
                    calls = 0

                    def ask(self, prompt, **kwargs):
                        Timeout.calls += 1
                        raise runtime_mod.CursorRuntimeError("agent timed out after 30s")

                write_strategy(content_root)
                orch = cycle_mod.Orchestrator(content_root, ROOT, Timeout(), discovery=settings)
                with self.assertRaises(cycle_mod.CycleError) as raised:
                    orch.run()
                self.assertEqual(raised.exception.code, "DISCOVERY_FAILURE")
                self.assertEqual(Timeout.calls, 1)
                no_opportunity = cycle_mod.Orchestrator(
                    content_root, ROOT, cycle_mod.FakeRuntime([{"opportunities": [opp("S01", significance="MINOR")]}]), discovery=settings
                )
                with self.assertRaises(cycle_mod.CycleError) as raised:
                    no_opportunity.run()
                self.assertEqual(raised.exception.code, "NO_EDITORIAL_OPPORTUNITY")
                for folder in ("ideas", "plans", "drafts", "approved", "published"):
                    self.assertEqual(list((content_root / folder).glob("*.md")) if (content_root / folder).is_dir() else [], [])
                self.assertEqual(list((content_root / "cycles").glob("POST-*")) if (content_root / "cycles").is_dir() else [], [])
                args = SimpleNamespace(
                    content_root=str(content_root), repo_root=str(ROOT), topic=None, post_id=None, human_note="",
                    max_revisions=2, fake_responses=None, selection="editorial", refresh_research=False,
                )
                buffer = io.StringIO()
                with patch.object(cycle_mod.discovery_mod.DiscoverySettings, "load", return_value=settings), patch.object(
                    cycle_mod.cursor_runtime_mod, "CursorRuntime", return_value=cycle_mod.FakeRuntime([{"opportunities": [opp("S01", significance="MINOR")]}])
                ), redirect_stdout(buffer):
                    code = cycle_mod.cmd_run(args)
                self.assertEqual(code, 1)
                self.assertEqual(json.loads(buffer.getvalue())["outcome"], "NO_EDITORIAL_OPPORTUNITY")
        finally:
            engine.CONTENT, cycle_mod.ACTIVE_CONTENT_ROOT = previous


def write_strategy(content_root: Path) -> None:
    folder = content_root / "strategy"
    folder.mkdir(parents=True, exist_ok=True)
    lines = ["---", "id: STRATEGY-20261113-01", "kind: strategy", "status: proposed", "created: 2026-11-13", "concentration_risk: false", "---", "", "## Topics", ""]
    for key, value in EVERGREEN.items():
        lines.append(("- topic: " if key == "topic" else f"  {key}: ") + value)
    lines += ["", "## Mix notes", "", "Fixture.", ""]
    (folder / "STRATEGY-20261113-01.md").write_text("\n".join(lines), encoding="utf-8")


AUDIT_BODY = (
    "Employers with more than 250 staff now have to audit automated hiring screeners before they use them on applicants.\n\n"
    "That changes who owns the screener. It is no longer a vendor feature a team switches on, "
    "but a production system someone must be able to explain.\n\n"
    "The rule covers screening, ranking and interview scheduling tools, so the audit trail has to show which tool made which decision.\n\n"
    "That ownership question is the part an engineering team can act on before the rule takes effect."
)


class PipelineIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.previous = (engine.CONTENT, cycle_mod.ACTIVE_CONTENT_ROOT)

    def tearDown(self):
        engine.CONTENT, cycle_mod.ACTIVE_CONTENT_ROOT = self.previous

    def test_selected_opportunity_flows_through_the_existing_gates_and_inbox(self):
        web = web_for(
            {
                WIRE: [("Regulator orders audits of AI hiring screeners at large employers", AUDIT_WIRE, NOW - timedelta(days=1), AUDIT_TEXT)],
                TIMES: [("Regulator orders AI hiring screener audits for large employers", AUDIT_TIMES, NOW - timedelta(days=2), AUDIT_TEXT)],
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            content_root = root / "content"
            for name in ("drafts", "ideas", "plans", "previews", "cycles"):
                (content_root / name).mkdir(parents=True)
            write_strategy(content_root)
            engine.CONTENT = cycle_mod.ACTIVE_CONTENT_ROOT = content_root
            settings = make_settings(root, web)
            snapshot, _ = discovery.ensure_research(settings)
            cid = ids_by_url(snapshot)[AUDIT_WIRE]
            title = "An audit duty makes hiring screeners an engineering ownership question"
            assessment = {
                "opportunities": [
                    opp(
                        cid,
                        significance="MAJOR",
                        angle_strength="STRONG",
                        what_happened="A regulator ordered large employers to audit automated hiring screeners before use.",
                        factual_statements=[
                            fact("Employers with more than 250 staff must audit automated hiring screeners before using them on applicants.", AUDIT_WIRE),
                            fact("The audit rule covers screening, ranking and interview scheduling tools used by large employers.", AUDIT_TIMES),
                        ],
                        topic={**opp(cid)["topic"], "topic": title},
                    ),
                    opp("S01", significance="MODERATE"),
                ]
            }
            compose = {
                "insight": "An audit duty turns a hiring screener into a system someone must own.",
                "hook": "callout",
                "hooks_considered": "callout names the ownership change.",
                "body": AUDIT_BODY,
                "claims_yaml": "- none",
                "lane": "TREND_REACTION",
                "audience": "BROAD_TECH",
            }
            semantic = {"verdict": "PASS", "findings": "General engineering interpretation of a sourced rule."}
            critic = {"verdict": "PASS", "blocking_issues": [], "non_blocking_issues": [], "revision_instructions": [], "reason": "ok"}
            runtime = cycle_mod.FakeRuntime([assessment, compose, semantic, critic])
            state = cycle_mod.Orchestrator(content_root, ROOT, runtime, max_revisions=2, discovery=settings).run()
            self.assertEqual(state.topic, title)
            self.assertEqual(state.lane, "TREND_REACTION")
            self.assertTrue(any(event.startswith(f"editorial selection {cid}") for event in state.events))
            ready, reasons = cycle_mod.readiness_report(content_root, state.post_id)
            self.assertTrue(ready, reasons)
            fields, _rest = engine.parse_frontmatter((content_root / "drafts" / f"{state.post_id}.md").read_text(encoding="utf-8"))
            self.assertEqual(fields.get("human_editorial_decision", ""), "")
            self.assertNotEqual(fields.get("status"), "approved")
            self.assertEqual(list((content_root / "approved").glob("*.md")) if (content_root / "approved").is_dir() else [], [])
            record = json.loads(cycle_mod.selection_path(content_root, state.post_id).read_text(encoding="utf-8"))
            self.assertEqual(record["selected"]["id"], cid)
            self.assertEqual(record["post_id"], state.post_id)
            idea = (content_root / "ideas" / f"{fields['idea']}.md").read_text(encoding="utf-8")
            self.assertIn(AUDIT_WIRE, idea)
            self.assertIn(f"research/trends/{snapshot['snapshot_id']}-{cid.casefold()}.md", idea)
            self.assertEqual(cycle_mod.select_topic(content_root, title)["topic"], title)
            inbox.InboxHandler.content_root = content_root
            page = inbox.render_candidate(content_root, state.post_id)
            self.assertIn("Editorial selection", page)
            self.assertIn(AUDIT_WIRE, page)
            self.assertIn("angle_strength: STRONG vs ADEQUATE", page)
            again = cycle_mod.editorial_selection(content_root, cycle_mod.FakeRuntime([assessment]), settings)
            self.assertIn("ALREADY_USED", by_id(again)[cid]["codes"])
            self.assertEqual(again["assessment"]["mode"], "model")


class ArticleExcerptTests(unittest.TestCase):
    def _html(self, paragraphs: list[str]) -> bytes:
        body = "".join(f"<p>{escape(p)}</p>" for p in paragraphs)
        return f"<html><body>{body}</body></html>".encode("utf-8")

    def test_late_limitation_appears_in_evidence_span(self):
        long_a = "Intro alpha. " * 120
        long_b = "More setup beta. " * 120
        limitation = (
            "Today the busiest repositories receive at most dozens of writes in a single second, "
            "typically in short bursts, and GitHub expects those bursts to become a sustained rate. "
            "The new architecture is available in a limited private preview now."
        )
        data = self._html([long_a, long_b, limitation])
        parser = discovery._ParagraphParser()
        parser.feed(data.decode("utf-8"))
        parser.close()
        head, _count = discovery._head_excerpt(parser.paragraphs, 1600)
        excerpt = discovery.article_excerpt(data, 1600, 1600)
        self.assertIn("limited private preview", excerpt)
        self.assertIn("dozens of writes", excerpt)
        self.assertNotIn("limited private preview", head)
        self.assertLessEqual(len(excerpt), 3204)

    def test_internal_test_distinction_supportable_not_customer_slo(self):
        excerpt = (
            "Through internal testing, we demonstrated 5000 agents and 7200 pushes per second. "
            "Today customer production sees dozens of writes per second in bursts only. "
            "The new architecture is in limited private preview."
        )
        self.assertTrue(
            discovery.statement_supported(
                "Through internal testing, we demonstrated 5000 agents and 7200 pushes per second.",
                excerpt,
            )
        )
        self.assertFalse(
            discovery.statement_supported(
                "Every GitHub customer now receives 7200 pushes per second in production.",
                excerpt,
            )
        )

    def test_compose_prompt_separates_source_conclusion_from_thesis(self):
        topic = {
            "editorial_thesis": "Ref updates are the scarce agreement surface.",
            "source_conclusion": "Immutable objects can be written in parallel; only refs need agreement.",
            "grounded_facts": ["fact one"],
            "source_limitations": ["internal test only"],
            "do_not_claim": [],
            "why_reader_cares": "Agents multiply writers.",
            "lane": "TREND_REACTION",
            "audience": "AI_ENGINEERING",
        }
        prompt = cycle_mod.compose_prompt(topic)
        self.assertIn("do not present it as the post's own insight", prompt)
        self.assertIn("additional understanding beyond the source conclusion", prompt)
        self.assertIn("not voiced as a new personal lesson", prompt)


if __name__ == "__main__":
    unittest.main()
