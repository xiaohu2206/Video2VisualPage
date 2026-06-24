from __future__ import annotations

from pathlib import Path
from typing import Any

from ..models import LocalModelAdapter
from ..models.adapter import model_signature
from ..paths import find_stage_artifact, project_stage_dir, stage_relative_path
from ..progress import ProgressReporter
from ..storage import atomic_write_json, read_json, read_jsonl, write_jsonl
from ..utils.eventlog import log_event


PLACEHOLDER_TOPICS = {"visual", "Visual timeline", "内容提取", "知识点整理", "重点回顾", "总结", "内容概览"}


def _chunked(items: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [items[index : index + size] for index in range(0, len(items), size)] or [[]]


def _meaningful_topics(topics: list[str]) -> list[str]:
    return [topic for topic in topics if topic and topic not in PLACEHOLDER_TOPICS]


def _suggested_chapter_count(cards: list[dict[str, Any]], unique_topics: list[str], chunk_size: int) -> int:
    if not cards:
        return 1
    shot_count = len(cards)
    meaningful_topic_count = len(_meaningful_topics(unique_topics))
    chunk_based_count = (shot_count + max(1, chunk_size // 2) - 1) // max(1, chunk_size // 2)
    note_density_count = (shot_count + 7) // 8
    suggested = max(meaningful_topic_count, chunk_based_count, note_density_count, 1)
    return min(6, shot_count, suggested)


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
    topics: list[str] = []
    important_shots: list[str] = []
    for summary in summaries:
        topics.extend(summary.get("main_topics", []))
        important_shots.extend(summary.get("important_shots", []))
    unique_topics = list(dict.fromkeys(str(topic) for topic in topics if topic))
    if unique_topics == ["visual"] or unique_topics == ["Visual timeline"]:
        unique_topics = ["内容提取", "知识点整理", "重点回顾", "总结"]
    suggested = _suggested_chapter_count(cards, unique_topics, chunk_size)
    global_summary = {
        "video_main_theme": unique_topics[0] if unique_topics else "视频博客笔记",
        "main_sections": unique_topics[:6] or ["内容概览"],
        "suggested_chapter_count": suggested,
        "narrative_style": "structured_report",
        "important_shots": list(dict.fromkeys(important_shots))[:20],
    }
    write_jsonl(stage_dir / "chunk_summaries.jsonl", summaries)
    atomic_write_json(stage_dir / "global_summary.json", global_summary)
    log_event(project_path, "summary_reduce_done", chunk_count=len(summaries), chapter_count=suggested)
    progress.done("摘要压缩完成", f"chunks={len(summaries)}")
    return {
        "outputs": [
            stage_relative_path("07_summary_reduce", "chunk_summaries.jsonl"),
            stage_relative_path("07_summary_reduce", "global_summary.json"),
        ],
        "chunk_count": len(summaries),
        "model_signature": model_signature(config, "copywriting"),
    }
