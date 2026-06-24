from __future__ import annotations

import html
import shutil
from pathlib import Path
from typing import Any

from ..paths import find_stage_artifact, project_stage_dir, resolve_artifact_path, stage_relative_path
from ..storage import atomic_write_json, atomic_write_text, read_json
from ..utils.eventlog import log_error, log_event


STYLE = """
:root {
  color-scheme: light;
  --ink: #1f2933;
  --muted: #667085;
  --line: #d8dee8;
  --panel: #f7f9fc;
  --accent: #0f766e;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif;
  line-height: 1.68;
  color: var(--ink);
  background: #ffffff;
}
article {
  max-width: 920px;
  margin: 0 auto;
  padding: 48px 24px 72px;
}
h1 { font-size: 36px; margin: 0 0 12px; letter-spacing: 0; }
h2 { font-size: 24px; margin: 40px 0 12px; letter-spacing: 0; }
p { margin: 0 0 16px; }
nav {
  border-top: 1px solid var(--line);
  border-bottom: 1px solid var(--line);
  padding: 18px 0;
  margin: 28px 0 36px;
}
nav ol { margin: 0; padding-left: 22px; }
a { color: var(--accent); text-decoration: none; }
.summary { color: var(--muted); font-size: 17px; }
.chapter-image {
  display: block;
  width: 100%;
  max-height: 520px;
  object-fit: contain;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 6px;
  margin: 16px 0 20px;
}
.key-points {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 14px 18px;
}
.shot-refs {
  color: var(--muted);
  font-size: 13px;
  overflow-wrap: anywhere;
}
@media print {
  article { max-width: none; padding: 24px; }
  .chapter-image { max-height: 360px; }
}
""".strip()


def _paragraphs(markdown: str) -> str:
    blocks = [item.strip() for item in markdown.split("\n\n") if item.strip()]
    return "\n".join(f"<p>{html.escape(block)}</p>" for block in blocks)


def _copy_image(project_dir: Path, output_dir: Path, frame: str | None) -> str | None:
    source = resolve_artifact_path(project_dir, frame)
    if not source or not source.exists():
        return None
    images_dir = output_dir / "assets" / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    target = images_dir / source.name
    if source.resolve() != target.resolve():
        shutil.copy2(source, target)
    return f"assets/images/{target.name}"


def _render_html(project_dir: Path, output_dir: Path, outline: dict[str, Any], chapters: list[dict[str, Any]], include_refs: bool) -> str:
    nav_items = "\n".join(
        f'<li><a href="#{html.escape(chapter["chapter_id"])}">{html.escape(chapter["title"])}</a></li>'
        for chapter in chapters
    )
    sections: list[str] = []
    for chapter in chapters:
        image_rel = _copy_image(project_dir, output_dir, chapter.get("representative_frame"))
        image_html = f'<img class="chapter-image" src="{html.escape(image_rel)}" alt="{html.escape(chapter["title"])}" />' if image_rel else ""
        points = "".join(f"<li>{html.escape(str(point))}</li>" for point in chapter.get("key_points", []))
        refs = ", ".join(str(item) for item in chapter.get("referenced_shots", []))
        refs_html = f'<p class="shot-refs">Referenced shots: {html.escape(refs)}</p>' if include_refs and refs else ""
        sections.append(
            f"""
<section id="{html.escape(chapter['chapter_id'])}">
  <h2>{html.escape(chapter['title'])}</h2>
  {image_html}
  {_paragraphs(str(chapter.get('body_markdown') or ''))}
  <div class="key-points"><strong>Key points</strong><ul>{points}</ul></div>
  {refs_html}
</section>
""".strip()
        )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{html.escape(outline.get("title", "Video Visual Report"))}</title>
  <link rel="stylesheet" href="assets/style.css" />
</head>
<body>
  <article>
    <h1>{html.escape(outline.get("title", "Video Visual Report"))}</h1>
    <p class="summary">{html.escape(outline.get("description", ""))}</p>
    <nav><ol>{nav_items}</ol></nav>
    {"".join(sections)}
  </article>
</body>
</html>
"""


def _try_pdf(project_path: Path, html_path: Path, pdf_path: Path) -> tuple[bool, str | None]:
    try:
        from weasyprint import HTML  # type: ignore

        HTML(filename=str(html_path)).write_pdf(str(pdf_path))
        return True, None
    except Exception as exc:  # noqa: BLE001 - PDF is optional.
        message = f"pdf_render_failed:{exc}"
        log_error(project_path, "10_static_render", message, html=str(html_path))
        return False, message


def run_static_render(project_dir: str | Path, *, output_format: str | None = None) -> dict[str, Any]:
    project_path = Path(project_dir)
    output_dir = project_stage_dir(project_path, "10_static_render")
    assets_dir = output_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    config = read_json(find_stage_artifact(project_path, "00_init", "config.json"))
    outline = read_json(find_stage_artifact(project_path, "08_outline_plan", "outline.json"))
    chapter_index = read_json(find_stage_artifact(project_path, "09_chapter_write", "chapters_index.json"))
    chapters = [read_json(project_path / item["path"]) for item in chapter_index.get("chapters", [])]
    include_refs = bool(config.get("render", {}).get("include_shot_references", True))
    html_text = _render_html(project_path, output_dir, outline, chapters, include_refs)
    atomic_write_text(assets_dir / "style.css", STYLE + "\n")
    atomic_write_text(output_dir / "index.html", html_text)
    outputs = [
        stage_relative_path("10_static_render", "index.html"),
        stage_relative_path("10_static_render", "assets/style.css"),
    ]

    formats = {part.strip().lower() for part in (output_format or "").split(",") if part.strip()}
    wants_pdf = "pdf" in formats or (not formats and bool(config.get("render", {}).get("output_pdf", False)))
    if wants_pdf:
        ok, error = _try_pdf(project_path, output_dir / "index.html", output_dir / "report.pdf")
        if ok:
            outputs.append(stage_relative_path("10_static_render", "report.pdf"))
            atomic_write_json(
                output_dir / "pdf_status.json",
                {"status": "passed", "path": stage_relative_path("10_static_render", "report.pdf")},
            )
            outputs.append(stage_relative_path("10_static_render", "pdf_status.json"))
        else:
            atomic_write_json(output_dir / "pdf_status.json", {"status": "failed", "error": error, "degraded_to_html": True})
            outputs.append(stage_relative_path("10_static_render", "pdf_status.json"))
    render_result = {
        "status": "passed",
        "html": stage_relative_path("10_static_render", "index.html"),
        "outputs": outputs,
        "chapter_count": len(chapters),
    }
    atomic_write_json(output_dir / "render_result.json", render_result)
    outputs.append(stage_relative_path("10_static_render", "render_result.json"))
    log_event(project_path, "static_render_done", outputs=outputs)
    return {"outputs": outputs, "html": str(output_dir / "index.html")}
