---
name: li-trends
description: >-
  Researches and records sourced current developments for trend-aware content
  planning. Invoke only when the user explicitly runs li-trends. Does not create
  posts.
disable-model-invocation: true
---

# li-trends

Collect potentially relevant current developments for later strategy and ideas. Read [`knowledge/content-strategy.md`](../../../knowledge/content-strategy.md). Do not draft posts, publish, or approve.

## Scope

Prefer developments in:

- AI agents and coding agents
- context engineering
- MCP and tool integrations
- agent evaluation
- RAG and retrieval
- production AI engineering
- OpenAI, Anthropic, Cursor, GitHub Copilot, and major developer tooling

## Sources

When web or search tools are available:

1. Official product, documentation, and blog sources
2. Original repositories and releases
3. Credible technical reporting

Record for each item:

- URL
- source name
- publication date
- retrieval date (UTC)
- factual summary of what changed

If browsing is unavailable, stop and ask the user for external sources. Do not invent trends from model memory.

## Headless path

`python scripts/cycle.py discover [--refresh-research]` performs research without this skill. Python fetches the sources listed in `config/editorial-discovery.json` (no LinkedIn scraping, no browser automation). It writes a dated snapshot `research/trends/<date>-discovery-NN.{json,md}` and assesses candidates in one model call that does not browse. Edit the config to change sources or windows. See `docs/cycle-contract.md` for gates and outcomes.

Keep four kinds of statement separate, here and in the headless path:

- verified development: what a cited source states, with date
- discussion evidence: measured activity on a named platform; Hacker News points are not LinkedIn activity
- interpretation: the author's reading, labeled as such
- evergreen relevance: why it matters beyond this week

Do not call a subject trending on LinkedIn without evidence from an authorized source.

## Write

Store under `research/trends/YYYY-MM-DD.md`. One file per research session date.

Each item includes:

- development
- source
- date
- what changed
- why it may matter
- possible personal angle
- evidence needed from the author

Trend research does not automatically create a post. A trend enters the pipeline through `/li-strategy`, `/li-ideas`, or headless editorial selection when the selection rules in content strategy are met.
