from __future__ import annotations

from pathlib import Path
from typing import Any

from ..paths import find_stage_artifact, project_stage_dir, resolve_artifact_path, stage_relative_path
from ..storage import read_json, write_jsonl
from ..utils.eventlog import log_event


def build_shot_packages(project_dir: str | Path, shots: list[dict[str, Any]], aligned: list[dict[str, Any]]) -> list[dict[str, Any]]:
    project_path = Path(project_dir)
    aligned_by_id = {str(item.get("shot_id")): item for item in aligned}
    packages: list[dict[str, Any]] = []
    for index, shot in enumerate(shots):
        shot_id = str(shot.get("shot_id"))
        frames = [frame.get("path") for frame in shot.get("keyframes", []) if frame.get("path")]
        warnings = list(shot.get("warnings") or [])
        for frame in frames:
            resolved = resolve_artifact_path(project_path, frame)
            if not resolved or not resolved.exists():
                warnings.append(f"missing_frame:{frame}")
        subtitle = aligned_by_id.get(shot_id, {})
        packages.append(
            {
                "shot_id": shot_id,
                "time_range": {
                    "start_sec": shot.get("start_sec"),
                    "end_sec": shot.get("end_sec"),
                    "duration_sec": shot.get("duration_sec"),
                },
                "frames": frames,
                "subtitle_text": subtitle.get("subtitle_text", ""),
                "subtitle_segments": subtitle.get("subtitle_segments", []),
                "neighbor_context": {
                    "previous_shot_id": shots[index - 1].get("shot_id") if index > 0 else None,
                    "next_shot_id": shots[index + 1].get("shot_id") if index + 1 < len(shots) else None,
                },
                "warnings": sorted(set(warnings)),
            }
        )
    return packages


def run_shot_package(project_dir: str | Path) -> dict[str, Any]:
    project_path = Path(project_dir)
    stage_dir = project_stage_dir(project_path, "05_shot_package")
    stage_dir.mkdir(parents=True, exist_ok=True)
    normalized = read_json(find_stage_artifact(project_path, "02_shot_split", "normalized_shots.json"))
    aligned = read_json(find_stage_artifact(project_path, "04_subtitle_align", "shot_subtitles.json"))
    packages = build_shot_packages(project_path, list(normalized.get("shots") or []), list(aligned.get("items") or []))
    write_jsonl(stage_dir / "shot_packages.jsonl", packages)
    log_event(project_path, "shot_package_done", package_count=len(packages))
    return {"outputs": [stage_relative_path("05_shot_package", "shot_packages.jsonl")], "package_count": len(packages)}
