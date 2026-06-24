from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


def _cv2_probe(video_path: Path) -> dict[str, Any]:
    import cv2  # type: ignore

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Unable to open video: {video_path}")
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        fps = fps if fps > 0 else 25.0
        duration = frame_count / fps if frame_count > 0 else 0.0
        return {
            "duration_sec": round(max(0.0, duration), 3),
            "fps": round(fps, 3),
            "frame_count": max(0, frame_count),
            "width": width,
            "height": height,
        }
    finally:
        cap.release()


def _find_ffprobe() -> str | None:
    explicit = os.environ.get("FFPROBE_PATH")
    if explicit and Path(explicit).exists():
        return explicit

    found = shutil.which("ffprobe")
    if found:
        return found

    for env_name in ("FFMPEG_DIR", "FFMPEG_HOME"):
        base = os.environ.get(env_name)
        if not base:
            continue
        candidate = Path(base) / "ffprobe.exe"
        if candidate.exists():
            return str(candidate)
        candidate = Path(base) / "bin" / "ffprobe.exe"
        if candidate.exists():
            return str(candidate)

    return None


def _ffprobe(video_path: Path) -> dict[str, Any]:
    ffprobe = _find_ffprobe()
    if not ffprobe:
        return {"audio_status": "unknown", "has_audio": False, "warnings": ["ffprobe_unavailable"]}

    command = [
        ffprobe,
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        str(video_path),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    if completed.returncode != 0:
        return {
            "audio_status": "unknown",
            "has_audio": False,
            "warnings": [f"ffprobe_failed:{completed.stderr.strip()[:200]}"],
        }

    payload = json.loads(completed.stdout or "{}")
    streams = payload.get("streams") if isinstance(payload, dict) else []
    video_stream = next((item for item in streams if item.get("codec_type") == "video"), {})
    audio_stream = next((item for item in streams if item.get("codec_type") == "audio"), None)
    fmt = payload.get("format") or {}
    return {
        "format": fmt.get("format_name") or video_path.suffix.lstrip("."),
        "video_codec": video_stream.get("codec_name"),
        "audio_codec": audio_stream.get("codec_name") if audio_stream else None,
        "audio_status": "present" if audio_stream else "absent",
        "has_audio": bool(audio_stream),
        "warnings": [],
    }


def probe_media(video_path: str | Path) -> dict[str, Any]:
    video = Path(video_path).expanduser().resolve()
    if not video.exists():
        raise FileNotFoundError(f"Video does not exist: {video}")

    warnings: list[str] = []
    try:
        cv2_info = _cv2_probe(video)
    except Exception as exc:  # noqa: BLE001 - fallback keeps pipeline state inspectable.
        cv2_info = {"duration_sec": 0.0, "fps": 0.0, "frame_count": 0, "width": 0, "height": 0}
        warnings.append(f"opencv_probe_failed:{exc}")

    stream_info = _ffprobe(video)
    warnings.extend(stream_info.pop("warnings", []))
    return {
        "video_path": str(video),
        **cv2_info,
        "format": stream_info.get("format") or video.suffix.lstrip(".").lower(),
        "video_codec": stream_info.get("video_codec"),
        "audio_codec": stream_info.get("audio_codec"),
        "has_audio": bool(stream_info.get("has_audio")),
        "audio_status": stream_info.get("audio_status", "unknown"),
        "warnings": warnings,
    }
