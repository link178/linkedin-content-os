---
name: li-preview
description: >-
  Generates a local HTML preview of the exact post body and optional visual
  for human approval. Invoke only when the user explicitly runs li-preview
  for a POST id.
disable-model-invocation: true
---

# li-preview

Build a local, view-only preview. This is not LinkedIn browser automation and makes no LinkedIn network calls.

## Preconditions

- semantic `PASS` on the current body
- editorial `PASS` on the current body

## Run

```text
python scripts/preview.py POST-YYYYMMDD-NN
python scripts/preview.py POST-YYYYMMDD-NN --open
```

Output: `content/previews/POST-ID.html` with the banner `Approximate LinkedIn preview — LinkedIn UI may differ.`

The preview contains the exact public body. Do not edit the post inside the preview. After preview, the user may run `/li-approve`.
