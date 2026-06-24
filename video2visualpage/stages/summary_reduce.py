from __future__ import annotations

from pathlib import Path
from typing import Any

from ..models import LocalModelAdapter
from ..models.adapter import model_signature
from ..paths import find_stage_artifact, project_stage_dir, stage_relative_path
from ..progress import ProgressReporter
from ..storage import atomic_write_json, read_json, read_jsonl, write_jsonl
from ..utils.eventlog import log_event


def _chunked(items: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [items[index : index + size] for index in range(0, len(items), size)] or [[]]


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
        unique_topics = ["Opening", "Development", "Key moments", "Closing"]
    suggested = min(6, max(1, round(len(cards) / max(1, chunk_size / 2)))) if cards else 1
    global_summary = {
        "video_main_theme": unique_topics[0] if unique_topics else "Video visual report",
        "main_sections": unique_topics[:6] or ["Overview"],
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
