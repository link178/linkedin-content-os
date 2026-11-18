"""Local deterministic SVG visuals for posts. Stdlib only."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

VISUAL_STRATEGIES = frozenset(
    {"NONE", "SINGLE_CARD", "FLOW", "COMPARISON", "METRIC", "ARCHITECTURE", "ILLUSTRATIVE_RANK"}
)

VISUAL_INFORMATION_GAIN = frozenset({"ADDITIVE", "DECORATIVE", "NONE"})

CANVAS_W = 1080
CANVAS_H = 1350

METRIC_PAIR = re.compile(
    r"(Hit Rate|MRR|precision|recall|latency|throughput)[^\n]{0,80}(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)

COMPARISON_PAIR = re.compile(
    r"(more|extra|heavier|additional)[^\n]{0,40}(?:orchestration|scaffolding|infra|layer)[^\n]{0,120}(?:no improvement|friction|did not|instead|kept|lightweight)",
    re.IGNORECASE | re.DOTALL,
)

FLOW_MARKERS = re.compile(
    r"\b(?:tried|tested|observed|kept|removed|lesson)\b",
    re.IGNORECASE,
)

HIT_MRR_CONTRAST = re.compile(
    r"\bhit rate\b.*\bmrr\b|\bmrr\b.*\bhit rate\b",
    re.IGNORECASE | re.DOTALL,
)


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bytes_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def is_bare_metric_body(body: str) -> bool:
    first_line = body.strip().split("\n")[0].strip() if body.strip() else ""
    return bool(re.match(r"^\s*Hit Rate\s+[\d.]+\s*,\s*MRR\s+[\d.]+\.?\s*$", first_line, re.I))


def choose_strategy(body: str) -> str:
    if is_bare_metric_body(body):
        return "METRIC"
    if HIT_MRR_CONTRAST.search(body) and re.search(r"\brank\b", body, re.I):
        return "ILLUSTRATIVE_RANK"
    metrics = METRIC_PAIR.findall(body)
    if len(metrics) >= 2:
        return "METRIC"
    if COMPARISON_PAIR.search(body):
        return "COMPARISON"
    lower = body.casefold()
    if "trade-off" in lower or "tradeoff" in lower or ("options" in lower and "decision" in lower):
        return "ARCHITECTURE"
    if len(FLOW_MARKERS.findall(body)) >= 3 and len(body) < 1200:
        return "FLOW"
    if has_concrete_card(body):
        return "SINGLE_CARD"
    return "NONE"


def classify_visual_information_gain(body: str, strategy: str) -> str:
    if strategy == "NONE":
        return "NONE"
    if strategy == "ILLUSTRATIVE_RANK":
        return "ADDITIVE"
    if strategy in {"COMPARISON", "FLOW", "ARCHITECTURE"}:
        return "ADDITIVE"
    if strategy == "METRIC":
        if is_bare_metric_body(body):
            return "DECORATIVE"
        if HIT_MRR_CONTRAST.search(body):
            return "DECORATIVE"
        return "DECORATIVE"
    if strategy == "SINGLE_CARD":
        return "DECORATIVE"
    return "NONE"


def has_concrete_card(body: str) -> bool:
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    return len(lines) >= 4 and any(len(line) <= 80 for line in lines[:6])


def extract_short_lines(body: str, limit: int = 4, max_len: int = 100) -> list[str]:
    sentences = re.split(r"(?<=[.!?])\s+", body.strip())
    picked: list[str] = []
    for sentence in sentences:
        text = sentence.strip()
        if not text or len(text) > max_len:
            continue
        picked.append(text)
        if len(picked) >= limit:
            break
    return picked


def escape_svg(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def assert_substrings(body: str, labels: list[str]) -> None:
    for label in labels:
        if label and label not in body:
            raise ValueError(f"visual text must be substring of body: {label!r}")


def render_illustrative_rank_svg() -> bytes:
    """General teaching diagram. Not per-query results from any project evaluation.

    Bars compare per-query reciprocal rank (RR). MRR is named only as the mean
    of those ranks, never as a per-query score.
    """
    font = "Segoe UI, system-ui, sans-serif"
    relevant = "#1d9bf0"
    irrelevant = "#5c6770"
    rest = "#2a3038"
    ink = "#e7e9ea"
    muted = "#8b98a5"

    def text(x: int, y: int, content: str, size: int, fill: str, weight: str = "400") -> str:
        return (
            f'<text x="{x}" y="{y}" fill="{fill}" font-family="{font}" '
            f'font-size="{size}" font-weight="{weight}">{escape_svg(content)}</text>'
        )

    def rank_row(x: int, y: int, rank: int, kind: str) -> list[str]:
        if kind == "relevant":
            width = max(36, int(340 / rank))
            color = relevant
            label = "relevant"
            label_fill = ink
        elif kind == "irrelevant":
            width = 72
            color = irrelevant
            label = "irrelevant"
            label_fill = muted
        else:
            width = 36
            color = rest
            label = "…"
            label_fill = muted
        return [
            text(x, y + 22, str(rank), 22, muted, "600"),
            f'<rect x="{x + 36}" y="{y}" width="{width}" height="28" rx="4" fill="{color}"/>',
            text(x + 52 + width, y + 22, label, 22, label_fill),
        ]

    columns = (
        (72, "Query A", ("relevant", "rest", "rest", "rest", "rest"), "Hit@5 = yes · RR = 1"),
        (580, "Query B", ("irrelevant", "irrelevant", "relevant", "rest", "rest"), "Hit@5 = yes · RR = 1/3"),
    )
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{CANVAS_W}" height="{CANVAS_H}" viewBox="0 0 {CANVAS_W} {CANVAS_H}">',
        '<rect width="100%" height="100%" fill="#0f1419"/>',
        text(72, 96, "Hit@5 vs reciprocal rank (illustrative)", 36, ink, "600"),
        text(72, 148, "Not measured per-query results", 22, muted),
    ]
    for origin, title, kinds, score in columns:
        parts.append(text(origin, 230, title, 28, ink, "600"))
        row_y = 270
        for rank, kind in enumerate(kinds, start=1):
            parts.extend(rank_row(origin, row_y, rank, kind))
            row_y += 118
        parts.append(text(origin, 900, score, 24, ink, "600"))
    parts.extend(
        [
            '<rect x="72" y="980" width="936" height="280" rx="16" fill="#1a1f26"/>',
            text(104, 1055, "Both are hits inside the top 5.", 28, ink, "600"),
            text(104, 1125, "Per-query reciprocal rank differs: 1 versus 1/3.", 26, ink),
            text(104, 1190, "MRR is the mean of those reciprocal ranks.", 26, muted),
            text(104, 1250, "It is not a per-query score.", 26, muted),
            "</svg>",
        ]
    )
    return "\n".join(parts).encode("utf-8")


def render_svg(strategy: str, body: str) -> bytes | None:
    if strategy == "NONE":
        return None
    if strategy == "ILLUSTRATIVE_RANK":
        return render_illustrative_rank_svg()
    lines = extract_short_lines(body, 4)
    assert_substrings(body, lines)
    footer = "Engineering note"
    if not lines:
        lines = [sentence.strip() for sentence in re.split(r"(?<=[.!?])\s+", body.strip()) if sentence.strip()][:4]
    title = lines[0] if lines else body[:80]
    if title not in body:
        title = body[: min(80, len(body))]
    assert_substrings(body, [title] + lines[1:])
    y = 120
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{CANVAS_W}" height="{CANVAS_H}" viewBox="0 0 {CANVAS_W} {CANVAS_H}">',
        f'<rect width="100%" height="100%" fill="#0f1419"/>',
        f'<text x="80" y="{y}" fill="#e7e9ea" font-family="Segoe UI, system-ui, sans-serif" font-size="42" font-weight="600">{escape_svg(title[:70])}</text>',
    ]
    y += 80
    parts.append(
        f'<text x="80" y="{y}" fill="#8b98a5" font-family="Segoe UI, system-ui, sans-serif" font-size="24">{escape_svg(strategy.replace("_", " "))}</text>'
    )
    y += 100
    for line in lines[1:]:
        parts.append(
            f'<text x="80" y="{y}" fill="#e7e9ea" font-family="Segoe UI, system-ui, sans-serif" font-size="28">{escape_svg(line)}</text>'
        )
        y += 72
    parts.append(
        f'<text x="80" y="{CANVAS_H - 80}" fill="#8b98a5" font-family="Segoe UI, system-ui, sans-serif" font-size="22">{footer}</text>'
    )
    parts.append("</svg>")
    return "\n".join(parts).encode("utf-8")


def write_visual_bundle(
    content_root: Path,
    post_id: str,
    body_sha256: str,
    body: str,
    strategy: str | None = None,
    now: str | None = None,
    force_strategy: bool = False,
) -> dict:
    chosen = strategy or choose_strategy(body)
    if chosen not in VISUAL_STRATEGIES:
        raise ValueError(f"unknown strategy {chosen}")
    proposed = chosen
    gain = classify_visual_information_gain(body, proposed)
    render_strategy = proposed
    if not force_strategy and gain != "ADDITIVE":
        render_strategy = "NONE"
    folder = content_root / "visuals" / post_id
    folder.mkdir(parents=True, exist_ok=True)
    timestamp = now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    svg_bytes = render_svg(render_strategy, body) if render_strategy != "NONE" else None
    visual_hash = ""
    svg_path = folder / "visual.svg"
    if svg_bytes is not None:
        svg_path.write_bytes(svg_bytes)
        visual_hash = bytes_sha256(svg_bytes)
    elif svg_path.exists() and not force_strategy:
        svg_path.unlink()
    stored_strategy = render_strategy if render_strategy != "NONE" else "NONE"
    stored_gain = gain if render_strategy != "NONE" else ("DECORATIVE" if gain == "DECORATIVE" else "NONE")
    yaml_text = (
        f"post_id: {post_id}\n"
        f"strategy: {stored_strategy}\n"
        f"visual_information_gain: {stored_gain}\n"
        f"body_sha256: {body_sha256}\n"
        f"visual_sha256: {visual_hash}\n"
        f"created_at: {timestamp}\n"
    )
    (folder / "visual.yaml").write_text(yaml_text, encoding="utf-8", newline="\n")
    return {
        "strategy": stored_strategy,
        "visual_information_gain": stored_gain,
        "visual_sha256": visual_hash,
        "folder": folder,
    }


def read_visual_state(content_root: Path, post_id: str) -> tuple[str, str]:
    """Return (visual_sha256, strategy) or ('', 'NONE') if absent."""
    yaml_path = content_root / "visuals" / post_id / "visual.yaml"
    svg_path = content_root / "visuals" / post_id / "visual.svg"
    if not yaml_path.is_file():
        return "", "NONE"
    text = yaml_path.read_text(encoding="utf-8")
    strategy = "NONE"
    visual_hash = ""
    for line in text.splitlines():
        if line.startswith("strategy:"):
            strategy = line.split(":", 1)[1].strip()
        if line.startswith("visual_sha256:"):
            visual_hash = line.split(":", 1)[1].strip()
    if svg_path.is_file():
        on_disk = bytes_sha256(svg_path.read_bytes())
        if visual_hash and visual_hash != on_disk:
            return on_disk, strategy
        if not visual_hash:
            return on_disk, strategy
    return visual_hash, strategy


def generate_visual_for_post(
    content_root: Path,
    post_id: str,
    strategy: str | None = None,
) -> tuple[int, dict | None]:
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
    artifact_id = fields.get("id", post_id)
    force = strategy is not None
    result = write_visual_bundle(
        content_root,
        artifact_id,
        digest,
        body,
        strategy=strategy,
        force_strategy=force,
    )
    return 0, result


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    import content_engine as engine

    parser = argparse.ArgumentParser(description="Create or refresh a local SVG visual for one draft post.")
    parser.add_argument("post_id", help="POST-YYYYMMDD-NN")
    parser.add_argument("--strategy", choices=sorted(VISUAL_STRATEGIES), help="Force a visual strategy")
    args = parser.parse_args(argv)
    code, result = generate_visual_for_post(engine.CONTENT, args.post_id, strategy=args.strategy)
    if code == 0 and result is not None:
        print(result["strategy"])
        print(result.get("visual_information_gain", "NONE"))
        if result["visual_sha256"]:
            print(result["visual_sha256"])
    return code


if __name__ == "__main__":
    import sys

    sys.exit(main())
