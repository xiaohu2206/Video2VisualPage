from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from ..paths import find_stage_artifact, project_stage_dir, repo_root, stage_relative_path
from ..progress import ProgressReporter
from ..storage import atomic_write_json, atomic_write_text, read_json
from ..utils.eventlog import log_event
from ..utils.timecode import parse_srt, segments_to_srt


def _standard_subtitles(segments: list[dict[str, Any]], *, source: str, warnings: list[str] | None = None) -> dict[str, Any]:
    cleaned: list[dict[str, Any]] = []
    for index, segment in enumerate(segments, start=1):
        text = str(segment.get("text", "")).strip()
        start = float(segment.get("start_sec", segment.get("start", 0.0)) or 0.0)
        end = float(segment.get("end_sec", segment.get("end", start)) or start)
        if not text or end <= start:
            continue
        cleaned.append(
            {
                "segment_id": str(segment.get("segment_id") or f"sub_{index:04d}"),
                "start_sec": round(start, 3),
                "end_sec": round(end, 3),
                "text": text,
            }
        )
    return {"language": "auto", "source": source, "segments": cleaned, "warnings": warnings or []}


def _env_flag(name: str, *, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _load_sidecar(video_path: Path) -> dict[str, Any] | None:
    srt_path = video_path.with_suffix(".srt")
    if srt_path.exists():
        return _standard_subtitles(parse_srt(srt_path.read_text(encoding="utf-8")), source="sidecar_srt")

    json_path = video_path.with_suffix(".json")
    if json_path.exists():
        payload = read_json(json_path)
        if isinstance(payload, list):
            return _standard_subtitles(payload, source="sidecar_json")
        if isinstance(payload, dict):
            return _standard_subtitles(list(payload.get("segments") or []), source=payload.get("source", "sidecar_json"))
    return None


def _import_subtitle_tool() -> Any:
    tool_path = repo_root() / "utils" / "subtitle-extractor"
    if tool_path.exists():
        sys.path.insert(0, str(tool_path))
    from subtitle_extractor import extract_subtitles  # type: ignore

    return extract_subtitles


def _run_online_asr(video_path: Path, stage_dir: Path, progress: ProgressReporter) -> dict[str, Any]:
    extract_subtitles = _import_subtitle_tool()
    result = extract_subtitles(
        video_path,
        output_path=stage_dir / "asr_raw.json",
        output_format="raw-json",
        work_dir=stage_dir / "work",
        use_cache=True,
        keep_audio=False,
        progress_callback=lambda percent, message: progress.emit(percent, str(message)),
    )
    segments = []
    for index, item in enumerate(result.utterances, start=1):
        text = (item.get("text") or item.get("transcript") or "").strip()
        if not text:
            continue
        segments.append(
            {
                "segment_id": f"sub_{index:04d}",
                "start_sec": round(float(item.get("start_time", 0) or 0) / 1000.0, 3),
                "end_sec": round(float(item.get("end_time", 0) or 0) / 1000.0, 3),
                "text": text,
            }
        )
    return _standard_subtitles(segments, source="bcut_asr")


def run_subtitle_extract(project_dir: str | Path) -> dict[str, Any]:
    project_path = Path(project_dir)
    project = read_json(find_stage_artifact(project_path, "00_init", "project.json"))
    config = read_json(find_stage_artifact(project_path, "00_init", "config.json"))
    media_info = read_json(find_stage_artifact(project_path, "01_media_probe", "media_info.json"))
    stage_dir = project_stage_dir(project_path, "03_subtitle_extract")
    stage_dir.mkdir(parents=True, exist_ok=True)
    video_path = Path(project["input_video"])
    progress = ProgressReporter("03_subtitle_extract")
    progress.start("准备提取字幕", f"video={video_path}")

    subtitles = _load_sidecar(video_path) if config.get("subtitle", {}).get("prefer_sidecar", True) else None
    if subtitles is not None:
        progress.emit(40, "已加载旁挂字幕", f"source={subtitles.get('source')}; segments={len(subtitles.get('segments') or [])}")
    if subtitles is None:
        subtitle_config = config.get("subtitle", {})
        online_env = str(subtitle_config.get("online_asr_env", "VIDEO2VISUALPAGE_ENABLE_ONLINE_ASR"))
        can_try_asr = (
            bool(subtitle_config.get("asr_enabled", True))
            and media_info.get("audio_status") != "absent"
            and _env_flag(online_env, default=True)
        )
        if can_try_asr:
            try:
                progress.emit(5, "开始在线 ASR", f"env={online_env}")
                subtitles = _run_online_asr(video_path, stage_dir, progress)
            except Exception as exc:  # noqa: BLE001 - ASR is optional degradation.
                progress.emit(100, "在线 ASR 失败，写入空字幕", exc)
                subtitles = _standard_subtitles([], source="empty", warnings=[f"asr_failed:{exc}"])
        else:
            reason = "no_audio_detected" if media_info.get("audio_status") == "absent" else "online_asr_disabled"
            progress.emit(40, "跳过在线 ASR", reason)
            subtitles = _standard_subtitles([], source="empty", warnings=[reason])

    atomic_write_json(stage_dir / "subtitles.json", subtitles)
    atomic_write_text(stage_dir / "subtitles.srt", segments_to_srt(subtitles["segments"]))
    if not (stage_dir / "asr_raw.json").exists():
        atomic_write_json(stage_dir / "asr_raw.json", {"source": subtitles["source"], "segments": subtitles["segments"]})
    log_event(project_path, "subtitle_extract_done", segment_count=len(subtitles["segments"]), source=subtitles["source"])
    progress.done("字幕提取完成", f"source={subtitles['source']}; segments={len(subtitles['segments'])}")
    return {
        "outputs": [
            stage_relative_path("03_subtitle_extract", "subtitles.json"),
            stage_relative_path("03_subtitle_extract", "subtitles.srt"),
            stage_relative_path("03_subtitle_extract", "asr_raw.json"),
        ],
        "segment_count": len(subtitles["segments"]),
    }
