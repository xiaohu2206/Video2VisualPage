from __future__ import annotations

from pathlib import Path
from typing import Any

from ..models import LocalModelAdapter
from ..models.adapter import model_signature
from ..paths import find_stage_artifact, project_stage_dir, stage_relative_path, to_project_path
from ..progress import ProgressReporter
from ..storage import atomic_write_json, read_json, read_jsonl
from ..utils.eventlog import log_event


def run_chapter_write(project_dir: str | Path, *, chapter_id: str | None = None) -> dict[str, Any]:
    project_path = Path(project_dir)
    stage_dir = project_stage_dir(project_path, "09_chapter_write")
    stage_dir.mkdir(parents=True, exist_ok=True)
    config = read_json(find_stage_artifact(project_path, "00_init", "config.json"))
    outline = read_json(find_stage_artifact(project_path, "08_outline_plan", "outline.json"))
    cards = read_jsonl(find_stage_artifact(project_path, "06_shot_understanding", "shot_analysis.jsonl"))
    global_summary = read_json(find_stage_artifact(project_path, "07_summary_reduce", "global_summary.json"))
    cards_by_id = {str(card.get("shot_id")): card for card in cards}
    progress = ProgressReporter("09_chapter_write")
    planned_chapters = [
        chapter
        for chapter in outline.get("chapters", [])
        if not chapter_id or chapter.get("chapter_id") == chapter_id
    ]
    progress.start("准备生成章节", f"chapters={len(planned_chapters)}")
    adapter = LocalModelAdapter(project_path, config, model_role="copywriting", progress=progress)
    chapters = []
    total = len(planned_chapters)
    for index, chapter in enumerate(planned_chapters, start=1):
        current_chapter_id = str(chapter.get("chapter_id") or f"chapter_{index:03d}")
        chapter_cards = [cards_by_id[shot_id] for shot_id in chapter.get("shot_ids", []) if shot_id in cards_by_id]
        progress.item_start(
            index,
            total,
            "生成章节",
            current_chapter_id,
            f"title={chapter.get('title')}; shots={len(chapter_cards)}",
        )
        output = adapter.write_chapter(chapter, chapter_cards, global_summary)
        chapter_path = stage_dir / f"{output['chapter_id']}.json"
        atomic_write_json(chapter_path, output)
        chapters.append(
            {
                "chapter_id": output["chapter_id"],
                "title": output["title"],
                "path": to_project_path(project_path, chapter_path),
                "referenced_shots": output.get("referenced_shots", []),
            }
        )
        progress.item_done(index, total, "完成章节", current_chapter_id, f"title={output.get('title')}")
    existing_index = stage_dir / "chapters_index.json"
    if chapter_id and existing_index.exists():
        index_payload = read_json(existing_index)
        by_id = {item["chapter_id"]: item for item in index_payload.get("chapters", [])}
        for item in chapters:
            by_id[item["chapter_id"]] = item
        chapters = [by_id[key] for key in sorted(by_id)]
    atomic_write_json(stage_dir / "chapters_index.json", {"chapters": chapters})
    log_event(project_path, "chapter_write_done", chapter_count=len(chapters), chapter_id=chapter_id)
    progress.done("章节生成完成", f"chapters={len(chapters)}")
    outputs = [stage_relative_path("09_chapter_write", "chapters_index.json")] + [item["path"] for item in chapters]
    return {"outputs": outputs, "chapter_count": len(chapters), "model_signature": model_signature(config, "copywriting")}
