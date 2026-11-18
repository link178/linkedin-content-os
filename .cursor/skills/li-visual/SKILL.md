---
name: li-visual
description: >-
  Decides whether a local SVG visual helps one editorially strong post and
  creates it when information gain is additive. Invoke only when the user
  explicitly runs li-visual for a POST id.
disable-model-invocation: true
---

# li-visual

Optional visual for one draft post. Read [docs/editorial-contract.md](../../../docs/editorial-contract.md). Do not publish. Do not call LinkedIn or external image APIs.

## Preconditions

- semantic `review_verdict: PASS` on the current body
- `editorial_verdict: PASS` on the current body

Do not create visuals for weak posts to hide weak writing.

## Information gain

Classify `visual_information_gain`:

- `ADDITIVE` — the visual teaches something text alone does not (ranking diagram, architecture, flow, decision comparison)
- `DECORATIVE` — repeats headline or metrics
- `NONE` — no visual needed

Automatic generation writes `visual.svg` only when `visual_information_gain` is `ADDITIVE`. Decorative cards are skipped unless the user passes `--strategy` to force a branded card.

Illustrative teaching diagrams must be labeled as general examples, not as per-query results from a project evaluation.

## Run

```text
python scripts/visual.py POST-YYYYMMDD-NN
python scripts/visual.py POST-YYYYMMDD-NN --strategy COMPARISON
```

`visual_strategy: NONE` is valid. Assets live under `content/visuals/POST-ID/` as `visual.yaml` and optional `visual.svg` (1080×1350).

Show the chosen strategy and information gain. Regenerate preview after visual changes.
