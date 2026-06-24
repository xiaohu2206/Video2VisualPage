from __future__ import annotations

from pathlib import Path
from typing import Any

from ..paths import canonical_relative_path
from ..storage import read_json, read_jsonl


CONTRACTS: dict[str, dict[str, list[str]]] = {
    "init/project.json": {"keys": ["project_id", "input_video", "output_dir", "created_at", "pipeline_version"]},
    "init/run_state.json": {"keys": ["project_id", "pipeline_version", "stages", "updated_at"]},
    "init/config.json": {"keys": ["scene_detection", "subtitle", "llm", "render", "qa"]},
    "media_info/media_info.json": {"keys": ["video_path", "duration_sec", "fps", "frame_count", "width", "height", "has_audio"]},
    "shot_split/normalized_shots.json": {"keys": ["video_path", "shot_count", "shots"]},
    "subtitle_extract/subtitles.json": {"keys": ["language", "source", "segments", "warnings"]},
    "subtitle_align/shot_subtitles.json": {"keys": ["items"]},
    "summary_reduce/global_summary.json": {"keys": ["video_main_theme", "main_sections", "suggested_chapter_count"]},
    "outline_plan/outline.json": {"keys": ["title", "description", "chapters"]},
    "chapter_write/chapters_index.json": {"keys": ["chapters"]},
    "qa/qa_report.json": {"keys": ["status", "checks", "warnings", "errors"]},
}

JSONL_CONTRACTS: dict[str, list[str]] = {
    "shot_package/shot_packages.jsonl": ["shot_id", "time_range", "frames", "subtitle_text", "neighbor_context"],
    "shot_understanding/shot_analysis.jsonl": ["shot_id", "merged_summary", "importance_score", "warnings"],
    "summary_reduce/chunk_summaries.jsonl": ["chunk_id", "shot_range", "main_topics", "summary"],
}


def _missing_keys(data: dict[str, Any], keys: list[str]) -> list[str]:
    return [key for key in keys if key not in data]


def validate_stage_contract(project_dir: str | Path, relative_path: str) -> dict[str, Any]:
    project_path = Path(project_dir)
    relative_path = canonical_relative_path(relative_path)
    target = project_path / relative_path
    if not target.exists():
        return {"path": relative_path, "status": "failed", "missing_file": True, "missing_keys": []}

    if relative_path in CONTRACTS:
        payload = read_json(target)
        missing = _missing_keys(payload, CONTRACTS[relative_path]["keys"]) if isinstance(payload, dict) else ["<root_object>"]
        return {"path": relative_path, "status": "passed" if not missing else "failed", "missing_keys": missing}

    if relative_path in JSONL_CONTRACTS:
        rows = read_jsonl(target)
        missing: list[str] = []
        for index, row in enumerate(rows, start=1):
            if not isinstance(row, dict):
                missing.append(f"line_{index}:<root_object>")
                continue
            for key in _missing_keys(row, JSONL_CONTRACTS[relative_path]):
                missing.append(f"line_{index}:{key}")
        return {"path": relative_path, "status": "passed" if not missing else "failed", "missing_keys": missing}

    return {"path": relative_path, "status": "skipped", "missing_keys": []}
