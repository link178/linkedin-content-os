---
name: li-cycle
description: >-
  Optional Cursor entry point for the headless content cycle. Runs the same
  Python orchestrator as the Review Inbox. Does not approve or publish.
disable-model-invocation: true
---

# li-cycle

Primary path: **Review Inbox** (`python scripts/inbox.py`) or CLI:

```text
python scripts/cycle.py run
python scripts/cycle.py discover
python scripts/cycle.py resume POST-YYYYMMDD-NN --human-note "..."
python scripts/cycle.py ready POST-YYYYMMDD-NN
python scripts/cycle.py status POST-YYYYMMDD-NN
```

This skill only forwards to those commands and prints JSON output. It does not reimplement validators, editorial scoring, visuals, or preview.

Requirements:

- Cursor CLI logged in (`agent status`)
- Proposed strategy under `content/strategy/`
- Local knowledge and proof registry unchanged

Outcomes include `READY_FOR_HUMAN` (derived), `NO_EDITORIAL_OPPORTUNITY`, `DISCOVERY_FAILURE`, diagnostics on failure, and explicit next human action. Inbox `APPROVE` sets `human_editorial_decision: APPROVE` only; lifecycle approval remains `/li-approve` or `content_engine.py approve`.
