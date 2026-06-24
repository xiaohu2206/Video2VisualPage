from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from ..paths import find_stage_artifact, project_stage_dir, repo_root, stage_relative_path, to_project_path
from ..progress import ProgressReporter
from ..storage import atomic_write_json, read_json
from ..utils.eventlog import log_event


def _import_segmenter() -> Any:
    tool_path = repo_root() / "utils" / "opencv-shot-segmenter"
    if tool_path.exists():
        sys.path.insert(0, str(tool_path))
    from opencv_shot_segmenter import ShotSegmenterConfig, detect_shots  # type: ignore

    return ShotSegmenterConfig, detect_shots


def _fallback_single_shot(project_dir: Path, video_path: str, media_info: dict[str, Any]) -> dict[str, Any]:
    duration = float(media_info.get("duration_sec") or 0.0)
    end = max(duration, 0.001)
    return {
        "video_path": video_path,
        "duration": round(duration, 3),
        "fps": float(media_info.get("fps") or 0.0),
        "frame_count": int(media_info.get("frame_count") or 0),
        "width": int(media_info.get("width") or 0),
        "height": int(media_info.get("height") or 0),
        "shot_count": 1,
        "detection": {"backend": "fallback_single_shot", "device": "cpu", "warnings": ["shot_detection_failed"]},
        "shots": [
            {
                "shot_id": "shot_001",
                "start": 0.0,
                "end": round(end, 3),
                "duration": round(end, 3),
                "keyframes": [],
                "keyframe_times": [],
                "sample_frames": [],
                "start_frame": 0,
                "end_frame": int(media_info.get("frame_count") or 0),
            }
        ],
    }


def _normalize(project_dir: Path, raw: dict[str, Any]) -> dict[str, Any]:
    shots: list[dict[str, Any]] = []
    for index, item in enumerate(raw.get("shots") or [], start=1):
        shot_id = str(item.get("shot_id") or f"shot_{index:03d}")
        start = float(item.get("start") or item.get("start_sec") or 0.0)
        end = float(item.get("end") or item.get("end_sec") or start)
        duration = float(item.get("duration") or max(0.0, end - start))
        keyframes: list[dict[str, Any]] = []
        for frame_index, frame in enumerate(item.get("keyframe_times") or [], start=1):
            path = frame.get("path")
            if not path:
                continue
            keyframes.append(
                {
                    "frame_id": f"{shot_id}_{frame_index:02d}",
                    "position": float(frame.get("position") or 0.0),
                    "time_sec": float(frame.get("time") or 0.0),
                    "path": to_project_path(project_dir, path),
                }
            )
        for frame_index, path in enumerate(item.get("keyframes") or [], start=len(keyframes) + 1):
            if any(frame["path"] == to_project_path(project_dir, path) for frame in keyframes):
                continue
            keyframes.append(
                {
                    "frame_id": f"{shot_id}_{frame_index:02d}",
                    "position": 0.0,
                    "time_sec": round(start + duration * 0.5, 3),
                    "path": to_project_path(project_dir, path),
                }
            )
        shots.append(
            {
                "shot_id": shot_id,
                "start_sec": round(start, 3),
                "end_sec": round(end, 3),
                "duration_sec": round(duration, 3),
                "start_frame": int(item.get("start_frame") or 0),
                "end_frame": int(item.get("end_frame") or 0),
                "keyframes": keyframes,
                "warnings": [] if keyframes else ["no_keyframes"],
            }
        )
    return {
        "video_path": raw.get("video_path"),
        "shot_count": len(shots),
        "shots": shots,
        "source": raw.get("detection") or {},
    }


def run_shot_split(project_dir: str | Path) -> dict[str, Any]:
    project_path = Path(project_dir)
    project = read_json(find_stage_artifact(project_path, "00_init", "project.json"))
    config = read_json(find_stage_artifact(project_path, "00_init", "config.json"))
    media_info = read_json(find_stage_artifact(project_path, "01_media_probe", "media_info.json"))
    stage_dir = project_stage_dir(project_path, "02_shot_split")
    stage_dir.mkdir(parents=True, exist_ok=True)
    progress = ProgressReporter("02_shot_split")
    progress.start("准备拆分镜头", f"video={project['input_video']}")

    try:
        config_cls, detect_shots = _import_segmenter()
        scene_config = config.get("scene_detection", {})
        keyframe_positions = scene_config.get("keyframe_positions", [0.2, 0.8])
        raw = detect_shots(
            project["input_video"],
            output_dir=stage_dir,
            config=config_cls(
                threshold=float(scene_config.get("threshold", 0.5)),
                shot_prefix=str(scene_config.get("shot_prefix", "shot")),
                keyframe_positions=keyframe_positions,
                export_keyframes=True,
                min_gap_seconds=float(scene_config.get("min_gap_seconds", 0.35)),
                min_shot_seconds=float(scene_config.get("min_shot_seconds", 0.15)),
            ),
            progress_callback=lambda percent, message: progress.emit(percent, str(message)),
        )
    except Exception as exc:  # noqa: BLE001 - fallback is an explicit degradation path.
        progress.emit(100, "镜头拆分失败，使用单镜头降级", exc)
        raw = _fallback_single_shot(project_path, project["input_video"], media_info)
        raw["detection"]["error"] = str(exc)
        atomic_write_json(stage_dir / "shots.json", raw)

    normalized = _normalize(project_path, raw)
    atomic_write_json(stage_dir / "normalized_shots.json", normalized)
    log_event(project_path, "shot_split_done", shot_count=normalized["shot_count"])
    progress.done("镜头拆分完成", f"shots={normalized['shot_count']}")
    return {
        "outputs": [
            stage_relative_path("02_shot_split", "shots.json"),
            stage_relative_path("02_shot_split", "normalized_shots.json"),
        ],
        "shot_count": normalized["shot_count"],
    }
