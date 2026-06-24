from __future__ import annotations

from pathlib import Path
from typing import Any

from ..paths import find_stage_artifact, project_stage_dir, stage_relative_path
from ..storage import atomic_write_json, read_json
from ..utils.eventlog import log_event
from ..utils.timecode import overlap_seconds


def align_subtitles(shots: list[dict[str, Any]], segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for shot in shots:
        start = float(shot.get("start_sec", 0.0))
        end = float(shot.get("end_sec", start))
        aligned_segments: list[dict[str, Any]] = []
        for segment in segments:
            seg_start = float(segment.get("start_sec", 0.0))
            seg_end = float(segment.get("end_sec", seg_start))
            overlap = overlap_seconds(start, end, seg_start, seg_end)
            if overlap <= 0:
                continue
            seg_duration = max(0.001, seg_end - seg_start)
            aligned_segments.append(
                {
                    "segment_id": segment.get("segment_id"),
                    "overlap_sec": round(overlap, 3),
                    "overlap_ratio": round(overlap / seg_duration, 4),
                    "text": segment.get("text", ""),
                }
            )
        items.append(
            {
                "shot_id": shot.get("shot_id"),
                "start_sec": start,
                "end_sec": end,
                "subtitle_text": " ".join(str(item.get("text", "")).strip() for item in aligned_segments if item.get("text")).strip(),
                "subtitle_segments": aligned_segments,
            }
        )
    return items


def run_subtitle_align(project_dir: str | Path) -> dict[str, Any]:
    project_path = Path(project_dir)
    stage_dir = project_stage_dir(project_path, "04_subtitle_align")
    stage_dir.mkdir(parents=True, exist_ok=True)
    normalized = read_json(find_stage_artifact(project_path, "02_shot_split", "normalized_shots.json"))
    subtitles = read_json(find_stage_artifact(project_path, "03_subtitle_extract", "subtitles.json"))
    items = align_subtitles(list(normalized.get("shots") or []), list(subtitles.get("segments") or []))
    payload = {"items": items, "subtitle_source": subtitles.get("source"), "warnings": subtitles.get("warnings", [])}
    atomic_write_json(stage_dir / "shot_subtitles.json", payload)
    log_event(project_path, "subtitle_align_done", item_count=len(items))
    return {"outputs": [stage_relative_path("04_subtitle_align", "shot_subtitles.json")], "item_count": len(items)}
