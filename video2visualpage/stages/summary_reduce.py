from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

from ..models import LocalModelAdapter
from ..models.adapter import model_signature
from ..paths import find_stage_artifact, project_stage_dir, stage_relative_path
from ..progress import ProgressReporter
from ..storage import atomic_write_json, read_json, read_jsonl, write_jsonl
from ..utils.eventlog import log_event


PLACEHOLDER_TOPICS = {"visual", "Visual timeline", "内容提取", "知识点整理", "重点回顾", "总结", "内容概览"}
DEFAULT_THEME = "视频博客笔记"
DEFAULT_SECTION = "内容概览"
DEFAULT_STYLE = "structured_report"
MAX_GLOBAL_SECTIONS = 6
MAX_IMPORTANT_SHOTS = 20


def _chunked(items: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    if not items:
        return [[]]
    target_size = max(1, size)
    chunk_count = max(1, math.ceil(len(items) / target_size))
    base_size, extra = divmod(len(items), chunk_count)
    chunks: list[list[dict[str, Any]]] = []
    start = 0
    for index in range(chunk_count):
        chunk_size = base_size + (1 if index < extra else 0)
        end = start + chunk_size
        chunks.append(items[start:end])
        start = end
    return chunks


def _meaningful_topics(topics: list[str]) -> list[str]:
    return [topic for topic in topics if topic and topic not in PLACEHOLDER_TOPICS]


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _dedupe_strings(items: list[Any]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = _clean_text(item)
        if not text or text in seen:
            continue
        seen.add(text)
        deduped.append(text)
    return deduped


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _source_chunk_ids(summaries: list[dict[str, Any]]) -> list[str]:
    return _dedupe_strings([summary.get("chunk_id") for summary in summaries])


def _collect_unique_topics(summaries: list[dict[str, Any]]) -> list[str]:
    topics: list[Any] = []
    for summary in summaries:
        topics.extend(_as_list(summary.get("main_topics")))
    unique_topics = _dedupe_strings(topics)
    if unique_topics == ["visual"] or unique_topics == ["Visual timeline"]:
        unique_topics = ["内容提取", "知识点整理", "重点回顾", "总结"]
    return unique_topics


def _collect_important_shots(summaries: list[dict[str, Any]]) -> list[str]:
    important_shots: list[Any] = []
    for summary in summaries:
        important_shots.extend(_as_list(summary.get("important_shots")))
    return _dedupe_strings(important_shots)[:MAX_IMPORTANT_SHOTS]


def _suggested_chapter_count(cards: list[dict[str, Any]], unique_topics: list[str], chunk_size: int) -> int:
    if not cards:
        return 1
    shot_count = len(cards)
    meaningful_topic_count = len(_meaningful_topics(unique_topics))
    chunk_based_count = (shot_count + max(1, chunk_size // 2) - 1) // max(1, chunk_size // 2)
    note_density_count = (shot_count + 7) // 8
    suggested = max(meaningful_topic_count, chunk_based_count, note_density_count, 1)
    return min(6, shot_count, suggested)


def _fallback_global_summary(
    cards: list[dict[str, Any]],
    summaries: list[dict[str, Any]],
    chunk_size: int,
    warnings: list[str],
) -> dict[str, Any]:
    unique_topics = _collect_unique_topics(summaries)
    meaningful_sections = _meaningful_topics(unique_topics)[:MAX_GLOBAL_SECTIONS]
    main_sections = meaningful_sections or unique_topics[:MAX_GLOBAL_SECTIONS] or [DEFAULT_SECTION]
    suggested = _suggested_chapter_count(cards, unique_topics, chunk_size)
    return {
        "video_main_theme": main_sections[0] if main_sections else DEFAULT_THEME,
        "main_sections": main_sections,
        "suggested_chapter_count": suggested,
        "narrative_style": DEFAULT_STYLE,
        "important_shots": _collect_important_shots(summaries),
        "source_chunks": _source_chunk_ids(summaries),
        "section_sources": [{"title": title, "source_chunks": []} for title in main_sections],
        "warnings": _dedupe_strings([*warnings, "global_outline_agent_fallback"]),
    }


def _build_global_summary(
    cards: list[dict[str, Any]],
    summaries: list[dict[str, Any]],
    chunk_size: int,
    outline: dict[str, Any] | None,
    warnings: list[str],
) -> dict[str, Any]:
    source_chunks = _source_chunk_ids(summaries)
    valid_chunks = set(source_chunks)
    outline_warnings = list(outline.get("warnings") or []) if isinstance(outline, dict) else []
    sections = list(outline.get("sections") or []) if isinstance(outline, dict) else []
    main_sections: list[str] = []
    section_sources: list[dict[str, Any]] = []

    for section in sections:
        if isinstance(section, dict):
            title = _clean_text(section.get("title"))
            raw_sources = _as_list(section.get("source_chunks"))
        else:
            title = _clean_text(section)
            raw_sources = []
        if not title or title in PLACEHOLDER_TOPICS or title in main_sections:
            continue
        filtered_sources = [chunk_id for chunk_id in _dedupe_strings(raw_sources) if chunk_id in valid_chunks]
        main_sections.append(title)
        section_sources.append({"title": title, "source_chunks": filtered_sources})
        if len(main_sections) >= MAX_GLOBAL_SECTIONS:
            break

    if not main_sections:
        return _fallback_global_summary(cards, summaries, chunk_size, [*warnings, *outline_warnings, "global_outline_agent_no_valid_sections"])

    theme = _clean_text(outline.get("video_main_theme")) if isinstance(outline, dict) else ""
    style = _clean_text(outline.get("narrative_style")) if isinstance(outline, dict) else ""
    shot_limit = max(1, len(cards))
    suggested = min(MAX_GLOBAL_SECTIONS, shot_limit, max(1, len(main_sections)))
    return {
        "video_main_theme": theme or main_sections[0] or DEFAULT_THEME,
        "main_sections": main_sections,
        "suggested_chapter_count": suggested,
        "narrative_style": style or DEFAULT_STYLE,
        "important_shots": _collect_important_shots(summaries),
        "source_chunks": source_chunks,
        "section_sources": section_sources,
        "warnings": _dedupe_strings([*warnings, *outline_warnings]),
    }


def run_summary_reduce(project_dir: str | Path) -> dict[str, Any]:
    project_path = Path(project_dir)
    stage_dir = project_stage_dir(project_path, "07_summary_reduce")
    stage_dir.mkdir(parents=True, exist_ok=True)
    config = read_json(find_stage_artifact(project_path, "00_init", "config.json"))
    cards = read_jsonl(find_stage_artifact(project_path, "06_shot_understanding", "shot_analysis.jsonl"))
    progress = ProgressReporter("07_summary_reduce")
    progress.start("准备压缩摘要", f"shots={len(cards)}")
    adapter = LocalModelAdapter(project_path, config, model_role="copywriting", progress=progress)
    chunk_size = max(1, int(config.get("llm", {}).get("max_shots_per_chunk", 40)))
    chunks = [chunk for chunk in _chunked(cards, chunk_size) if chunk]
    summaries = []
    total = len(chunks)
    for index, chunk in enumerate(chunks, start=1):
        chunk_id = f"chunk_{index:03d}"
        shot_ids = [str(card.get("shot_id")) for card in chunk if card.get("shot_id")]
        shot_range = f"{shot_ids[0]}-{shot_ids[-1]}" if shot_ids else "empty"
        progress.item_start(index, total, "压缩分块", chunk_id, f"shots={len(chunk)}; range={shot_range}")
        summaries.append(adapter.summarize_chunk(chunk_id, chunk))
        progress.item_done(index, total, "完成分块摘要", chunk_id, f"range={shot_range}")
    outline: dict[str, Any] | None = None
    warnings: list[str] = []
    try:
        outline = adapter.summarize_global_outline(summaries)
    except Exception as exc:  # noqa: BLE001 - global outline has a deterministic fallback.
        warnings.append(f"global_outline_agent_failed:{exc}")
    global_summary = _build_global_summary(cards, summaries, chunk_size, outline, warnings)
    write_jsonl(stage_dir / "chunk_summaries.jsonl", summaries)
    atomic_write_json(stage_dir / "global_summary.json", global_summary)
    log_event(project_path, "summary_reduce_done", chunk_count=len(summaries), chapter_count=global_summary["suggested_chapter_count"])
    progress.done("摘要压缩完成", f"chunks={len(summaries)}")
    return {
        "outputs": [
            stage_relative_path("07_summary_reduce", "chunk_summaries.jsonl"),
            stage_relative_path("07_summary_reduce", "global_summary.json"),
        ],
        "chunk_count": len(summaries),
        "model_signature": model_signature(config, "copywriting"),
    }
