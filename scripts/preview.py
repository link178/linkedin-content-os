"""Local HTML preview for LinkedIn-style post review. Stdlib only."""

from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_HEADLINE = "Senior software engineer based in Spain"


def load_preview_config(root: Path) -> dict:
    path = root / "config" / "preview.json"
    if not path.is_file():
        return {"display_name": "", "headline": DEFAULT_HEADLINE, "image": ""}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"display_name": "", "headline": DEFAULT_HEADLINE, "image": ""}
    return {
        "display_name": str(data.get("display_name") or ""),
        "headline": str(data.get("headline") or DEFAULT_HEADLINE),
        "image": str(data.get("image") or ""),
    }


def body_to_html_paragraphs(body: str) -> str:
    parts = []
    for block in body.split("\n\n"):
        text = block.strip()
        if not text:
            continue
        parts.append(f"<p>{html.escape(text)}</p>")
    return "\n".join(parts)


def embed_svg(svg_path: Path) -> str:
    if not svg_path.is_file():
        return ""
    raw = svg_path.read_text(encoding="utf-8")
    if "<script" in raw.casefold() or "javascript:" in raw.casefold():
        raise ValueError("preview refuses SVG with script")
    return f'<div class="visual">{raw}</div>'


def render_html(
    body: str,
    display_name: str,
    headline: str,
    image_path: str,
    svg_path: Path | None,
) -> str:
    avatar = ""
    if image_path and Path(image_path).is_file():
        avatar = f'<img class="avatar" src="{html.escape(Path(image_path).as_uri())}" alt=""/>'
    else:
        avatar = '<div class="avatar placeholder" aria-hidden="true"></div>'
    name = html.escape(display_name or "Author")
    title = html.escape(headline)
    body_html = body_to_html_paragraphs(body)
    visual = embed_svg(svg_path) if svg_path else ""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Preview — {name}</title>
<style>
:root {{ color-scheme: light dark; }}
body {{ margin: 0; font-family: system-ui, Segoe UI, Roboto, sans-serif; background: #f3f2ef; color: #191919; }}
.banner {{ background: #fff8e6; border-bottom: 1px solid #e0e0e0; padding: 10px 16px; font-size: 14px; }}
.wrap {{ max-width: 552px; margin: 24px auto; padding: 0 16px; }}
.card {{ background: #fff; border: 1px solid #e0e0e0; border-radius: 8px; padding: 16px; box-shadow: 0 1px 3px rgba(0,0,0,.08); }}
.head {{ display: flex; gap: 12px; align-items: center; margin-bottom: 12px; }}
.avatar {{ width: 48px; height: 48px; border-radius: 50%; object-fit: cover; }}
.avatar.placeholder {{ background: linear-gradient(135deg,#cbd5e1,#94a3b8); }}
.meta .name {{ font-weight: 600; font-size: 14px; }}
.meta .headline {{ font-size: 12px; color: #666; line-height: 1.3; }}
.content p {{ margin: 0 0 12px; font-size: 14px; line-height: 1.45; white-space: pre-wrap; }}
.visual {{ margin-top: 12px; overflow: hidden; border-radius: 8px; }}
.visual svg {{ max-width: 100%; height: auto; display: block; }}
@media (max-width: 600px) {{ .wrap {{ margin: 12px auto; }} .card {{ padding: 12px; }} }}
</style>
</head>
<body>
<div class="banner">Approximate LinkedIn preview — LinkedIn UI may differ.</div>
<div class="wrap">
<article class="card">
<header class="head">
{avatar}
<div class="meta">
<div class="name">{name}</div>
<div class="headline">{title}</div>
</div>
</header>
<div class="content">
{body_html}
{visual}
</div>
</article>
</div>
</body>
</html>
"""


def write_preview(
    content_root: Path,
    repo_root: Path,
    post_id: str,
    body: str,
    body_sha256: str,
    visual_sha256: str,
    now: str | None = None,
) -> Path:
    config = load_preview_config(repo_root)
    svg_path = content_root / "visuals" / post_id / "visual.svg"
    svg = svg_path if svg_path.is_file() else None
    page = render_html(
        body,
        config["display_name"],
        config["headline"],
        config["image"],
        svg,
    )
    out_dir = content_root / "previews"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{post_id}.html"
    out_path.write_text(page, encoding="utf-8", newline="\n")
    for block in body.split("\n\n"):
        text = block.strip()
        if text and html.escape(text) not in page:
            raise ValueError("preview must contain exact public body text")
    return out_path


def generate_preview_for_post(
    content_root: Path,
    repo_root: Path,
    post_id: str,
    open_browser: bool = False,
) -> tuple[int, Path | None]:
    import sys

    import content_engine as engine

    matches = [p for p in engine.find_posts(content_root, post_id) if p.parent.name == "drafts"]
    if not matches:
        print(f"{post_id}: draft not found", file=sys.stderr)
        return 1, None
    path = matches[0]
    text = path.read_text(encoding="utf-8")
    fields, rest = engine.parse_frontmatter(text)
    sections = engine.section_map(rest)
    body = sections.get("Body", "")
    digest = engine.body_hash(body)
    if fields.get("review_verdict") != "PASS" or fields.get("review_body_sha256") != digest:
        print("semantic review PASS required on current body", file=sys.stderr)
        return 1, None
    if fields.get("editorial_verdict") != "PASS" or fields.get("editorial_body_sha256") != digest:
        print("editorial review PASS required on current body", file=sys.stderr)
        return 1, None
    import visual as visual_mod

    visual_hash, _ = visual_mod.read_visual_state(content_root, post_id)
    out_path = write_preview(content_root, repo_root, post_id, body, digest, visual_hash)
    engine.apply_preview_metadata(path, content_root, digest, visual_hash)
    if open_browser:
        import webbrowser

        webbrowser.open(out_path.resolve().as_uri())
    return 0, out_path


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    import content_engine as engine

    parser = argparse.ArgumentParser(description="Generate a local HTML preview for one draft post.")
    parser.add_argument("post_id", help="POST-YYYYMMDD-NN")
    parser.add_argument("--open", action="store_true", help="Open the preview in the default browser")
    args = parser.parse_args(argv)
    code, out_path = generate_preview_for_post(
        engine.CONTENT,
        engine.CONTENT.parent,
        args.post_id,
        open_browser=args.open,
    )
    if code == 0 and out_path is not None:
        print(out_path)
    return code


if __name__ == "__main__":
    import sys

    sys.exit(main())
