from __future__ import annotations

from math import ceil
from pathlib import Path
from typing import Any

from ..paths import find_stage_artifact, project_stage_dir, stage_relative_path
from ..storage import atomic_write_json, read_json, read_jsonl
from ..utils.eventlog import log_event


PLACEHOLDER_SECTIONS = {"visual", "Visual timeline", "内容提取", "知识点整理", "重点回顾", "总结", "内容概览"}


def _meaningful_sections(global_summary: dict[str, Any]) -> list[str]:
    sections = [str(section) for section in global_summary.get("main_sections") or [] if section]
    return [section for section in sections if section not in PLACEHOLDER_SECTIONS]


def _desired_chapter_count(config: dict[str, Any], global_summary: dict[str, Any], shot_count: int) -> int:
    raw = config.get("llm", {}).get("chapter_count", "auto")
    if raw != "auto":
        try:
            return min(8, max(1, int(raw)))
        except (TypeError, ValueError):
            pass
    try:
        suggested = int(global_summary.get("suggested_chapter_count") or 1)
    except (TypeError, ValueError):
        suggested = 1
    section_count = len(_meaningful_sections(global_summary))
    auto_count = max(suggested, section_count, 1)
    return min(8, max(1, min(auto_count, shot_count or 1)))


def run_outline_plan(project_dir: str | Path) -> dict[str, Any]:
    project_path = Path(project_dir)
    stage_dir = project_stage_dir(project_path, "08_outline_plan")
    stage_dir.mkdir(parents=True, exist_ok=True)
    config = read_json(find_stage_artifact(project_path, "00_init", "config.json"))
    cards = read_jsonl(find_stage_artifact(project_path, "06_shot_understanding", "shot_analysis.jsonl"))
    global_summary = read_json(find_stage_artifact(project_path, "07_summary_reduce", "global_summary.json"))
    count = _desired_chapter_count(config, global_summary, len(cards))
    per_chapter = max(1, ceil(len(cards) / count)) if cards else 1
    sections = list(global_summary.get("main_sections") or [])
    chapters: list[dict[str, Any]] = []
    for index in range(count):
        group = cards[index * per_chapter : (index + 1) * per_chapter]
        if not group:
            continue
        representative = max(group, key=lambda item: float(item.get("importance_score") or 0.0))
        title_seed = sections[index] if index < len(sections) else f"Part {index + 1}"
        chapters.append(
            {
                "chapter_id": f"chapter_{index + 1:03d}",
                "title": str(title_seed),
                "summary": str(representative.get("merged_summary") or title_seed),
                "shot_ids": [str(item["shot_id"]) for item in group],
                "representative_shot_id": str(representative["shot_id"]),
            }
        )
    outline = {
        "title": "视频博客笔记",
        "description": "由视频帧文字、字幕和知识点提取生成的静态笔记。",
        "chapters": chapters,
    }
    atomic_write_json(stage_dir / "outline.json", outline)
    log_event(project_path, "outline_plan_done", chapter_count=len(chapters))
    return {"outputs": [stage_relative_path("08_outline_plan", "outline.json")], "chapter_count": len(chapters)}
