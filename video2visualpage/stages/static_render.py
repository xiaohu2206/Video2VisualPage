from __future__ import annotations

import html
import re
import shutil
from pathlib import Path
from typing import Any

from ..paths import find_stage_artifact, project_stage_dir, resolve_artifact_path, stage_relative_path
from ..storage import atomic_write_json, atomic_write_text, read_json, read_jsonl
from ..utils.eventlog import log_error, log_event


STYLE = """
:root {
  color-scheme: light;
  --ink: #14191f;
  --ink-soft: #2d3741;
  --muted: #66727f;
  --faint: #8a96a3;
  --line: #d8e0e6;
  --line-strong: #b9c5ce;
  --panel: #f3f6f7;
  --paper: #ffffff;
  --paper-soft: #f8fafb;
  --accent: #0b766d;
  --accent-strong: #075c55;
  --accent-soft: #e5f3ef;
  --accent-line: #8bc4bc;
  --code-bg: #eef2f5;
  --shadow: 0 24px 70px rgb(29 45 57 / 10%);
  --radius: 8px;
  --radius-sm: 6px;
}
* { box-sizing: border-box; }
html {
  scroll-behavior: smooth;
}
body {
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif;
  line-height: 1.7;
  color: var(--ink);
  background:
    linear-gradient(180deg, #f2f5f6 0%, #eef3f1 46%, #f7f8f9 100%);
}
a {
  color: var(--accent-strong);
  text-decoration: none;
  text-underline-offset: 3px;
}
a:hover {
  text-decoration: underline;
}
a:focus-visible {
  outline: 3px solid rgb(11 118 109 / 28%);
  outline-offset: 3px;
  border-radius: var(--radius-sm);
}
.report-shell {
  max-width: 1280px;
  margin: 0 auto;
  padding: 40px 28px 72px;
}
.report-page {
  background: rgb(255 255 255 / 88%);
  border: 1px solid rgb(216 224 230 / 88%);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  overflow: hidden;
}
.report-hero {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(260px, 390px);
  gap: 44px;
  padding: 48px 46px 42px;
  background:
    linear-gradient(135deg, rgb(255 255 255 / 92%), rgb(240 246 247 / 88%));
  border-bottom: 1px solid var(--line);
}
.hero-copy {
  min-width: 0;
}
.eyebrow {
  color: var(--accent-strong);
  font-size: 13px;
  font-weight: 700;
  margin: 0 0 16px;
}
h1 {
  font-size: clamp(32px, 5vw, 58px);
  line-height: 1.04;
  margin: 0 0 18px;
  letter-spacing: 0;
}
h2 {
  font-size: 27px;
  margin: 0;
  letter-spacing: 0;
  line-height: 1.24;
}
h3 { font-size: 20px; margin: 30px 0 10px; letter-spacing: 0; line-height: 1.35; }
h4 { font-size: 18px; margin: 24px 0 8px; letter-spacing: 0; line-height: 1.4; }
p { margin: 0 0 16px; }
.summary {
  color: var(--ink-soft);
  font-size: 18px;
  line-height: 1.75;
  max-width: 76ch;
}
.report-stats {
  display: grid;
  align-content: start;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 0;
  margin: 0;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: rgb(255 255 255 / 72%);
}
.report-stats div {
  min-width: 0;
  padding: 18px 18px 16px;
  border-left: 1px solid var(--line);
}
.report-stats div:first-child {
  border-left: 0;
}
.report-stats dt {
  color: var(--muted);
  font-size: 12px;
  margin: 0 0 8px;
}
.report-stats dd {
  color: var(--ink);
  font-size: 28px;
  font-weight: 750;
  line-height: 1;
  margin: 0;
}
.toc {
  padding: 26px 46px 30px;
  border-bottom: 1px solid var(--line);
  background: var(--paper);
}
.toc-header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 18px;
  color: var(--muted);
  font-size: 13px;
  margin-bottom: 12px;
}
.toc-header strong {
  color: var(--ink);
  font-size: 15px;
}
.toc ol {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0 30px;
  list-style: none;
  margin: 0;
  padding: 0;
}
.toc li {
  min-width: 0;
  border-top: 1px solid var(--line);
}
.toc a {
  display: grid;
  grid-template-columns: 42px minmax(0, 1fr);
  gap: 12px;
  align-items: start;
  padding: 13px 0;
  color: var(--ink);
  text-decoration: none;
}
.toc a:hover {
  color: var(--accent-strong);
}
.toc-index {
  color: var(--accent-strong);
  font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
  font-size: 12px;
  line-height: 1.8;
}
.toc-title {
  overflow-wrap: anywhere;
}
.chapters {
  background: var(--paper);
}
.chapter-section {
  border-top: 1px solid var(--line);
  padding: 50px 46px 56px;
  scroll-margin-top: 20px;
}
.chapter-section:first-child {
  border-top: 0;
}
.chapter-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(320px, 420px);
  gap: 48px;
  align-items: start;
}
.chapter-copy {
  min-width: 0;
  max-width: 74ch;
}
.chapter-kicker {
  color: var(--accent-strong);
  font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
  font-size: 12px;
  margin-bottom: 12px;
}
.chapter-header {
  border-left: 4px solid var(--accent-line);
  padding-left: 18px;
  margin-bottom: 26px;
}
.chapter-body {
  color: var(--ink-soft);
  font-size: 16px;
}
.frame-board {
  min-width: 0;
  position: sticky;
  top: 24px;
}
.frame-board-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  margin: 0 0 12px;
  color: var(--muted);
  font-size: 13px;
}
.frame-board-title strong {
  color: var(--ink);
  font-size: 14px;
  letter-spacing: 0;
}
.frame-main,
.frame-thumb {
  margin: 0;
}
.frame-main {
  border: 1px solid var(--line);
  border-radius: var(--radius);
  overflow: hidden;
  background: var(--paper);
}
.frame-main img {
  display: block;
  width: 100%;
  aspect-ratio: 16 / 9;
  max-height: 286px;
  object-fit: contain;
  background: #e8edf1;
}
.frame-main figcaption,
.frame-thumb figcaption {
  color: var(--muted);
  font-size: 11px;
  line-height: 1.35;
  overflow: hidden;
  padding: 7px 9px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.frame-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  margin-top: 10px;
}
.frame-thumb {
  border: 1px solid var(--line);
  border-radius: var(--radius);
  overflow: hidden;
  background: var(--paper);
}
.frame-thumb img {
  display: block;
  width: 100%;
  aspect-ratio: 16 / 9;
  object-fit: contain;
  background: #e8edf1;
}
.key-points {
  border: 1px solid rgb(139 196 188 / 55%);
  border-left: 4px solid var(--accent);
  background: var(--accent-soft);
  border-radius: var(--radius);
  margin-top: 26px;
  padding: 16px 18px;
}
.key-points strong {
  color: var(--accent-ink);
}
.key-points ul {
  margin: 8px 0 0;
  padding-left: 20px;
}
.chapter-body h2 {
  font-size: 22px;
  margin-top: 26px;
}
.chapter-body h2:first-child,
.chapter-body h3:first-child,
.chapter-body h4:first-child {
  margin-top: 0;
}
.chapter-body ul,
.chapter-body ol {
  margin: 0 0 16px;
  padding-left: 24px;
}
.chapter-body li {
  margin: 4px 0;
}
.chapter-body strong {
  font-weight: 700;
}
.chapter-body code {
  background: var(--code-bg);
  border-radius: 5px;
  padding: 1px 5px;
  font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
  font-size: 0.92em;
}
.chapter-body pre {
  overflow-x: auto;
  background: #111827;
  color: #f9fafb;
  border-radius: 6px;
  padding: 14px 16px;
  margin: 0 0 16px;
}
.chapter-body pre code {
  background: transparent;
  color: inherit;
  padding: 0;
}
.chapter-body blockquote {
  border-left: 4px solid var(--accent-line);
  color: var(--muted);
  margin: 0 0 16px;
  padding: 2px 0 2px 16px;
}
.chapter-body hr {
  border: 0;
  border-top: 1px solid var(--line);
  margin: 28px 0;
}
.shot-refs {
  color: var(--muted);
  font-size: 13px;
  margin-top: 18px;
  overflow-wrap: anywhere;
}
@page {
  margin: 18mm;
}
@media print {
  body { background: #fff; }
  .report-shell { max-width: none; padding: 0; }
  .report-page { border: 0; box-shadow: none; border-radius: 0; }
  .report-hero,
  .toc,
  .chapter-section { padding-left: 0; padding-right: 0; }
  .frame-board { position: static; }
  .chapter-section { break-inside: avoid; }
  .frame-main img { max-height: 260px; }
}
@media (max-width: 900px) {
  .report-shell { padding: 24px 16px 52px; }
  .report-page { border-radius: var(--radius); }
  .report-hero {
    grid-template-columns: 1fr;
    gap: 28px;
    padding: 34px 24px;
  }
  .report-stats { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .toc { padding: 24px; }
  .toc ol { grid-template-columns: 1fr; }
  .chapter-section { padding: 38px 24px 44px; }
  h1 { font-size: 34px; }
  .chapter-layout { grid-template-columns: 1fr; gap: 24px; }
  .chapter-copy { max-width: none; }
  .frame-board { position: static; }
  .frame-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 560px) {
  .report-shell { padding: 0; }
  .report-page { border-left: 0; border-right: 0; border-radius: 0; box-shadow: none; }
  .report-hero { padding: 28px 18px 30px; }
  .summary { font-size: 16px; }
  .report-stats { grid-template-columns: 1fr; }
  .report-stats div {
    border-left: 0;
    border-top: 1px solid var(--line);
  }
  .report-stats div:first-child { border-top: 0; }
  .toc { padding: 22px 18px; }
  .toc a { grid-template-columns: 36px minmax(0, 1fr); }
  .chapter-section { padding: 34px 18px 42px; }
  .chapter-header { padding-left: 14px; }
  .frame-grid { grid-template-columns: 1fr; }
  .frame-main img { max-height: none; }
}
""".strip()


def _render_inline_markdown(text: str) -> str:
    code_tokens: dict[str, str] = {}

    def keep_code(match: re.Match[str]) -> str:
        token = f"@@CODE{len(code_tokens)}@@"
        code_tokens[token] = f"<code>{html.escape(match.group(1))}</code>"
        return token

    escaped = html.escape(re.sub(r"`([^`\n]+)`", keep_code, text))
    escaped = re.sub(
        r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)",
        lambda match: f'<a href="{html.escape(match.group(2), quote=True)}">{match.group(1)}</a>',
        escaped,
    )
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"__(.+?)__", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", escaped)
    escaped = re.sub(r"(?<!_)_([^_\n]+)_(?!_)", r"<em>\1</em>", escaped)
    for token, rendered in code_tokens.items():
        escaped = escaped.replace(token, rendered)
    return escaped


def _render_markdown(markdown: str) -> str:
    lines = markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    rendered: list[str] = []
    paragraph: list[str] = []
    list_items: list[str] = []
    list_tag: str | None = None
    code_lines: list[str] = []
    code_lang = ""
    in_code_block = False

    def flush_paragraph() -> None:
        if not paragraph:
            return
        text = " ".join(item.strip() for item in paragraph if item.strip())
        if text:
            rendered.append(f"<p>{_render_inline_markdown(text)}</p>")
        paragraph.clear()

    def flush_list() -> None:
        nonlocal list_tag
        if not list_items or not list_tag:
            return
        rendered.append(f"<{list_tag}>" + "".join(f"<li>{item}</li>" for item in list_items) + f"</{list_tag}>")
        list_items.clear()
        list_tag = None

    for line in lines:
        stripped = line.strip()
        if in_code_block:
            if stripped.startswith("```"):
                lang_attr = f' class="language-{html.escape(code_lang, quote=True)}"' if code_lang else ""
                rendered.append(f"<pre><code{lang_attr}>{html.escape(chr(10).join(code_lines))}</code></pre>")
                code_lines.clear()
                code_lang = ""
                in_code_block = False
            else:
                code_lines.append(line)
            continue

        if stripped.startswith("```"):
            flush_paragraph()
            flush_list()
            code_lang = stripped[3:].strip().split(" ", 1)[0]
            in_code_block = True
            continue

        if not stripped:
            flush_paragraph()
            flush_list()
            continue

        heading = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", stripped)
        if heading:
            flush_paragraph()
            flush_list()
            level = len(heading.group(1))
            rendered.append(f"<h{level}>{_render_inline_markdown(heading.group(2))}</h{level}>")
            continue

        if re.match(r"^[-*_]\s*[-*_]\s*[-*_]\s*$", stripped):
            flush_paragraph()
            flush_list()
            rendered.append("<hr />")
            continue

        quote = re.match(r"^>\s?(.*)$", stripped)
        if quote:
            flush_paragraph()
            flush_list()
            rendered.append(f"<blockquote><p>{_render_inline_markdown(quote.group(1))}</p></blockquote>")
            continue

        unordered = re.match(r"^\s*[-*+]\s+(.+)$", line)
        ordered = re.match(r"^\s*\d+[.)]\s+(.+)$", line)
        if unordered or ordered:
            flush_paragraph()
            current_tag = "ul" if unordered else "ol"
            if list_tag and list_tag != current_tag:
                flush_list()
            list_tag = current_tag
            item_text = (unordered or ordered).group(1)
            list_items.append(_render_inline_markdown(item_text))
            continue

        flush_list()
        paragraph.append(line)

    if in_code_block:
        lang_attr = f' class="language-{html.escape(code_lang, quote=True)}"' if code_lang else ""
        rendered.append(f"<pre><code{lang_attr}>{html.escape(chr(10).join(code_lines))}</code></pre>")
    flush_paragraph()
    flush_list()
    return "\n".join(rendered)


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


def _format_time(seconds: Any) -> str:
    try:
        total = max(0, int(float(seconds)))
    except (TypeError, ValueError):
        return ""
    minutes, secs = divmod(total, 60)
    return f"{minutes:02d}:{secs:02d}"


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _chapter_shot_ids(chapter: dict[str, Any], outline_chapter: dict[str, Any] | None) -> list[str]:
    shot_ids = [str(item) for item in (outline_chapter or {}).get("shot_ids", []) if item]
    shot_ids.extend(str(item) for item in chapter.get("referenced_shots", []) if item)
    return _dedupe(shot_ids)


def _shot_frame_candidates(analysis: dict[str, Any], package: dict[str, Any]) -> list[str]:
    candidates: list[str] = []
    recommended = analysis.get("recommended_display_frame")
    if recommended:
        candidates.append(str(recommended))
    candidates.extend(str(frame) for frame in package.get("frames", []) if frame)
    return _dedupe(candidates)


def _shot_start_seconds(analysis: dict[str, Any], package: dict[str, Any]) -> Any:
    if analysis.get("start_sec") is not None:
        return analysis.get("start_sec")
    time_range = package.get("time_range") if isinstance(package.get("time_range"), dict) else {}
    return time_range.get("start_sec")


def _chapter_images(
    project_dir: Path,
    output_dir: Path,
    chapter: dict[str, Any],
    outline_chapter: dict[str, Any] | None,
    analysis_by_id: dict[str, dict[str, Any]],
    packages_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, str]]:
    images: list[dict[str, str]] = []
    seen_sources: set[str] = set()
    for shot_id in _chapter_shot_ids(chapter, outline_chapter):
        analysis = analysis_by_id.get(shot_id, {})
        package = packages_by_id.get(shot_id, {})
        candidates = _shot_frame_candidates(analysis, package)
        frame_count = len(candidates)
        copied_for_shot = 0
        for frame in candidates:
            source = resolve_artifact_path(project_dir, frame)
            if not source or not source.exists():
                continue
            source_key = source.resolve().as_posix()
            if source_key in seen_sources:
                continue
            image_rel = _copy_image(project_dir, output_dir, frame)
            if not image_rel:
                continue
            seen_sources.add(source_key)
            copied_for_shot += 1
            time_label = _format_time(_shot_start_seconds(analysis, package))
            label_parts = [shot_id]
            if time_label:
                label_parts.append(time_label)
            if frame_count > 1:
                label_parts.append(f"{copied_for_shot}/{frame_count}")
            images.append(
                {
                    "src": image_rel,
                    "label": " · ".join(label_parts),
                    "alt": f"{chapter.get('title') or '章节'} {shot_id}",
                }
            )
    if not images:
        fallback = _copy_image(project_dir, output_dir, chapter.get("representative_frame"))
        if fallback:
            images.append(
                {
                    "src": fallback,
                    "label": str(chapter.get("chapter_id") or "representative_frame"),
                    "alt": str(chapter.get("title") or "章节图片"),
                }
            )
    return images


def _render_gallery(images: list[dict[str, str]], chapter_title: str) -> str:
    if not images:
        return ""
    primary = images[0]
    thumbs = images[1:]
    thumbs_html = "\n".join(
        f"""
<figure class="frame-thumb">
  <img src="{html.escape(item['src'])}" alt="{html.escape(item['alt'])}" loading="lazy" />
  <figcaption>{html.escape(item['label'])}</figcaption>
</figure>
""".strip()
        for item in thumbs
    )
    grid_html = f'<div class="frame-grid">{thumbs_html}</div>' if thumbs_html else ""
    return f"""
<aside class="frame-board" aria-label="{html.escape(chapter_title)} 关键帧">
  <div class="frame-board-title"><strong>关键帧</strong><span>{len(images)} 张视觉证据</span></div>
  <figure class="frame-main">
    <img src="{html.escape(primary['src'])}" alt="{html.escape(primary['alt'])}" loading="lazy" />
    <figcaption>{html.escape(primary['label'])}</figcaption>
  </figure>
  {grid_html}
</aside>
""".strip()


def _render_html(
    project_dir: Path,
    output_dir: Path,
    outline: dict[str, Any],
    chapters: list[dict[str, Any]],
    include_refs: bool,
    analysis_by_id: dict[str, dict[str, Any]] | None = None,
    packages_by_id: dict[str, dict[str, Any]] | None = None,
) -> str:
    analysis_by_id = analysis_by_id or {}
    packages_by_id = packages_by_id or {}
    outline_by_id = {str(chapter.get("chapter_id")): chapter for chapter in outline.get("chapters", [])}
    report_title = str(outline.get("title") or "视频博客笔记")
    report_description = str(outline.get("description") or "")
    nav_items = "\n".join(
        f"""
<li>
  <a href="#{html.escape(str(chapter.get('chapter_id') or f'chapter_{index:03d}'))}">
    <span class="toc-index">{index:02d}</span>
    <span class="toc-title">{html.escape(str(chapter.get("title") or "章节"))}</span>
  </a>
</li>
""".strip()
        for index, chapter in enumerate(chapters, start=1)
    )
    sections: list[str] = []
    image_count = 0
    for index, chapter in enumerate(chapters, start=1):
        chapter_id = str(chapter.get("chapter_id") or f"chapter_{index:03d}")
        chapter_title = str(chapter.get("title") or "章节")
        outline_chapter = outline_by_id.get(str(chapter.get("chapter_id")))
        images = _chapter_images(project_dir, output_dir, chapter, outline_chapter, analysis_by_id, packages_by_id)
        image_count += len(images)
        gallery_html = _render_gallery(images, chapter_title)
        points = "".join(f"<li>{html.escape(str(point))}</li>" for point in chapter.get("key_points", []))
        points_html = f'<div class="key-points"><strong>关键点</strong><ul>{points}</ul></div>' if points else ""
        refs = ", ".join(str(item) for item in chapter.get("referenced_shots", []))
        refs_html = f'<p class="shot-refs">引用镜头: {html.escape(refs)}</p>' if include_refs and refs else ""
        body_html = _render_markdown(str(chapter.get("body_markdown") or ""))
        sections.append(
            f"""
<section class="chapter-section" id="{html.escape(chapter_id)}" aria-labelledby="{html.escape(chapter_id)}-title">
  <div class="chapter-layout">
    <div class="chapter-copy">
      <div class="chapter-kicker">CHAPTER {index:02d}</div>
      <header class="chapter-header">
        <h2 id="{html.escape(chapter_id)}-title">{html.escape(chapter_title)}</h2>
      </header>
      <div class="chapter-body">{body_html}</div>
      {points_html}
      {refs_html}
    </div>
    {gallery_html}
  </div>
</section>
""".strip()
        )
    summary_html = f'<p class="summary">{html.escape(report_description)}</p>' if report_description else ""
    refs_status = "开启" if include_refs else "关闭"
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{html.escape(report_title)}</title>
  <link rel="stylesheet" href="assets/style.css" />
</head>
<body>
  <main class="report-shell">
    <article class="report-page">
      <header class="report-hero">
        <div class="hero-copy">
          <p class="eyebrow">视频图文报告</p>
          <h1>{html.escape(report_title)}</h1>
          {summary_html}
        </div>
        <dl class="report-stats" aria-label="报告概览">
          <div><dt>章节</dt><dd>{len(chapters)}</dd></div>
          <div><dt>关键帧</dt><dd>{image_count}</dd></div>
          <div><dt>引用</dt><dd>{refs_status}</dd></div>
        </dl>
      </header>
      <nav class="toc" aria-label="章节目录">
        <div class="toc-header"><strong>目录</strong><span>{len(chapters)} 章</span></div>
        <ol>{nav_items}</ol>
      </nav>
      <div class="chapters">{"".join(sections)}</div>
    </article>
  </main>
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
    shot_analysis = read_jsonl(find_stage_artifact(project_path, "06_shot_understanding", "shot_analysis.jsonl"))
    shot_packages = read_jsonl(find_stage_artifact(project_path, "05_shot_package", "shot_packages.jsonl"))
    analysis_by_id = {str(item.get("shot_id")): item for item in shot_analysis}
    packages_by_id = {str(item.get("shot_id")): item for item in shot_packages}
    include_refs = bool(config.get("render", {}).get("include_shot_references", True))
    html_text = _render_html(project_path, output_dir, outline, chapters, include_refs, analysis_by_id, packages_by_id)
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
