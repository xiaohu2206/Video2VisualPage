from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

import cv2
import numpy as np

ProgressCallback = Callable[[float, str], None]

DEFAULT_KEYFRAME_POSITIONS = (0.2, 0.4, 0.6, 0.8)


@dataclass(frozen=True)
class ShotSegmenterConfig:
    threshold: float = 0.5
    shot_prefix: str = "shot"
    keyframe_positions: str | Iterable[float] | None = DEFAULT_KEYFRAME_POSITIONS
    export_keyframes: bool = True
    sample_fps: float = 0.0
    max_sample_frames_per_shot: int = 0
    min_gap_seconds: float = 0.35
    min_shot_seconds: float = 0.15
    resize_width: int = 96
    resize_height: int = 54
    export_clips: bool = False
    clip_codec: str = "mp4v"


def _shot_id(prefix: str, idx: int) -> str:
    width = 6 if prefix.endswith("movie_shot") or prefix == "movie_shot" else 3
    return f"{prefix}_{idx:0{width}d}"


def _safe_float(value: Any, default: float) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def video_info(video_path: str | Path) -> dict[str, Any]:
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Unable to open video: {video_path}")
    try:
        fps = _safe_float(cap.get(cv2.CAP_PROP_FPS), 25.0)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        duration = frame_count / fps if fps > 0 and frame_count > 0 else 0.0
        return {
            "fps": fps if fps > 0 else 25.0,
            "frame_count": max(0, frame_count),
            "duration": max(0.0, duration),
            "width": width,
            "height": height,
        }
    finally:
        cap.release()


def parse_positions(value: str | Iterable[float] | None) -> list[float]:
    if value is None:
        return list(DEFAULT_KEYFRAME_POSITIONS)
    if isinstance(value, str):
        raw = [part.strip() for part in value.split(",")]
        positions = [float(part) for part in raw if part]
    else:
        positions = [float(part) for part in value]
    cleaned = sorted({min(0.95, max(0.05, item)) for item in positions})
    return cleaned or list(DEFAULT_KEYFRAME_POSITIONS)


def _normalize_scenes(
    scenes: Iterable[tuple[int, int]],
    frame_count: int,
    min_frames: int,
) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    last_frame = max(0, frame_count - 1)
    for start, end in scenes:
        start_i = max(0, int(start))
        end_i = min(last_frame, int(end))
        if end_i < start_i:
            continue
        if out and start_i <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], end_i))
        elif end_i - start_i + 1 >= min_frames or not out:
            out.append((start_i, end_i))
        elif out:
            out[-1] = (out[-1][0], end_i)
    return out or [(0, last_frame)]


def _detect_boundaries(
    video_path: str | Path,
    config: ShotSegmenterConfig,
    progress_callback: ProgressCallback | None = None,
) -> tuple[list[tuple[int, int]], int, dict[str, Any]]:
    if progress_callback:
        progress_callback(1.0, "OpenCV shot detection started")

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Unable to open video: {video_path}")

    fps = _safe_float(cap.get(cv2.CAP_PROP_FPS), 25.0)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    resize_to = (max(16, int(config.resize_width)), max(9, int(config.resize_height)))

    scores: list[float] = []
    frames: list[int] = []
    prev_gray = None
    prev_hist = None
    prev_edge = None
    idx = 0

    try:
        while True:
            ok, frame = cap.read()
            if not ok or frame is None:
                break

            small = cv2.resize(frame, resize_to, interpolation=cv2.INTER_AREA)
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
            hist = cv2.calcHist([hsv], [0, 1], None, [32, 32], [0, 180, 0, 256])
            cv2.normalize(hist, hist)
            edge = cv2.Canny(gray, 80, 160)

            if prev_gray is not None and prev_hist is not None and prev_edge is not None:
                gray_score = float(np.mean(cv2.absdiff(prev_gray, gray))) / 255.0
                hist_score = float(cv2.compareHist(prev_hist, hist, cv2.HISTCMP_BHATTACHARYYA))
                edge_score = float(np.mean(cv2.absdiff(prev_edge, edge))) / 255.0
                scores.append(gray_score * 0.50 + hist_score * 0.40 + edge_score * 0.10)
                frames.append(idx)

            prev_gray = gray
            prev_hist = hist
            prev_edge = edge
            idx += 1

            if progress_callback and total_frames > 0 and (idx == 1 or idx % 300 == 0):
                progress_callback(min(82.0, (idx / total_frames) * 82.0), "Scanning frames")
    finally:
        cap.release()

    frame_count = max(1, idx)
    if not scores:
        return [(0, frame_count - 1)], frame_count, {
            "backend": "opencv_frame_diff",
            "device": "cpu",
            "cut_count": 0,
        }

    arr = np.array(scores, dtype=np.float32)
    threshold = min(0.99, max(0.01, float(config.threshold)))
    median = float(np.median(arr))
    mad = float(np.median(np.abs(arr - median)))
    percentile_rank = max(90.0, min(99.7, 99.3 - (1.0 - threshold) * 5.0))
    percentile = float(np.percentile(arr, percentile_rank))
    adaptive_threshold = max(
        0.045,
        median + max(2.2, threshold * 5.0) * max(mad, 0.002),
        percentile,
    )

    cuts = [0]
    min_gap = max(1, int(round(fps * max(0.0, config.min_gap_seconds))))
    for i, score in enumerate(scores):
        left = scores[i - 1] if i > 0 else -1.0
        right = scores[i + 1] if i + 1 < len(scores) else -1.0
        frame_idx = frames[i]
        if score >= adaptive_threshold and score >= left and score >= right and frame_idx - cuts[-1] >= min_gap:
            cuts.append(frame_idx)

    bounds: list[tuple[int, int]] = []
    for i, start in enumerate(cuts):
        end = cuts[i + 1] - 1 if i + 1 < len(cuts) else frame_count - 1
        bounds.append((start, end))

    backend_info = {
        "backend": "opencv_frame_diff",
        "device": "cpu",
        "cut_count": max(0, len(cuts) - 1),
        "min_gap_frames": min_gap,
        "adaptive_threshold": round(adaptive_threshold, 5),
        "score_median": round(median, 5),
        "score_p99": round(float(np.percentile(arr, 99.0)), 5),
    }
    return bounds, frame_count, backend_info


def _write_frame_image(out_path: str | Path, frame: Any) -> Path:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    ext = out.suffix or ".jpg"
    ok, encoded = cv2.imencode(ext, frame)
    if not ok:
        raise RuntimeError(f"Failed to encode frame: {out}")
    out.write_bytes(encoded.tobytes())
    return out


def extract_frames(
    video_path: str | Path,
    requests: Iterable[tuple[float, str | Path]],
    *,
    fps: float | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
) -> list[Path]:
    items = [(max(0.0, float(time_sec)), Path(out_path)) for time_sec, out_path in requests]
    if not items:
        return []

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Unable to open video: {video_path}")

    actual_fps = max(1e-6, float(fps or cap.get(cv2.CAP_PROP_FPS) or 25.0))
    indexed = sorted(
        ((int(round(time_sec * actual_fps)), order, out_path) for order, (time_sec, out_path) in enumerate(items)),
        key=lambda item: (item[0], item[1]),
    )
    results: list[Path | None] = [None] * len(items)
    max_sequential_gap = max(1, int(round(actual_fps * 2.0)))
    current_frame = int(cap.get(cv2.CAP_PROP_POS_FRAMES) or 0)
    cached_frame_index = -1
    cached_frame = None

    try:
        for processed, (target_frame, order, out_path) in enumerate(indexed, start=1):
            frame = cached_frame if target_frame == cached_frame_index else None
            if frame is None:
                if target_frame < current_frame or target_frame - current_frame > max_sequential_gap:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
                    current_frame = int(cap.get(cv2.CAP_PROP_POS_FRAMES) or target_frame)

                ok = False
                while current_frame <= target_frame:
                    ok, candidate = cap.read()
                    if not ok or candidate is None:
                        break
                    frame = candidate
                    cached_frame_index = current_frame
                    cached_frame = candidate
                    current_frame += 1

                if not ok or frame is None:
                    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
                    if frame_count > 0:
                        cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, frame_count - 1))
                        ok, frame = cap.read()
                    if not ok or frame is None:
                        raise RuntimeError(f"Failed to extract frame: {video_path} @ frame {target_frame}")

            _write_frame_image(out_path, frame)
            results[order] = out_path
            if progress_callback:
                progress_callback(processed, len(indexed))
    finally:
        cap.release()

    return [path for path in results if path is not None]


def _time_at_position(start: float, end: float, position: float) -> float:
    duration = max(1e-6, end - start)
    return start + duration * min(0.95, max(0.05, float(position)))


def _sample_times(start: float, end: float, sample_fps: float, max_frames: int) -> list[float]:
    if sample_fps <= 0 or max_frames <= 0:
        return []
    duration = max(0.0, end - start)
    if duration <= 0:
        return []
    count = min(max_frames, max(1, int(round(duration * sample_fps))))
    if count == 1:
        return [(start + end) / 2.0]
    margin = min(duration * 0.08, 0.25)
    usable_start = start + margin
    usable_end = max(usable_start, end - margin)
    step = (usable_end - usable_start) / float(count - 1)
    return [usable_start + step * i for i in range(count)]


def _build_shot_rows(
    scenes: list[tuple[int, int]],
    *,
    fps: float,
    duration: float,
    keyframe_dir: Path,
    sample_dir: Path,
    config: ShotSegmenterConfig,
) -> tuple[list[dict[str, Any]], list[tuple[float, Path]]]:
    positions = parse_positions(config.keyframe_positions)
    sample_fps = max(0.0, float(config.sample_fps))
    max_sample_frames = max(0, int(config.max_sample_frames_per_shot))
    frame_requests: list[tuple[float, Path]] = []
    shots: list[dict[str, Any]] = []

    for idx, (start_f, end_f) in enumerate(scenes, start=1):
        start = max(0.0, start_f / fps)
        end = min(duration or ((end_f + 1) / fps), (end_f + 1) / fps)
        if end <= start:
            end = start + (1.0 / fps)

        sid = _shot_id(config.shot_prefix, idx)
        keyframes: list[str] = []
        keyframe_times: list[dict[str, Any]] = []
        if config.export_keyframes:
            for keyframe_index, position in enumerate(positions):
                time_sec = _time_at_position(start, end, position)
                role = f"fifth_{keyframe_index + 1}"
                key_path = keyframe_dir / f"{sid}_{role}_{int(round(position * 100)):02d}.jpg"
                frame_requests.append((time_sec, key_path))
                keyframes.append(str(key_path))
                keyframe_times.append({
                    "path": str(key_path),
                    "time": round(time_sec, 3),
                    "frame": int(round(time_sec * fps)),
                    "role": role,
                    "position": round(position, 4),
                })

        sample_frames: list[dict[str, Any]] = []
        if config.export_keyframes and sample_fps > 0 and max_sample_frames > 0:
            for sample_idx, time_sec in enumerate(_sample_times(start, end, sample_fps, max_sample_frames), start=1):
                sample_path = sample_dir / f"{sid}_sample_{sample_idx:03d}.jpg"
                frame_requests.append((time_sec, sample_path))
                sample_frames.append({
                    "path": str(sample_path),
                    "time": round(time_sec, 3),
                    "frame": int(round(time_sec * fps)),
                })

        shots.append({
            "shot_id": sid,
            "start": round(start, 3),
            "end": round(end, 3),
            "duration": round(end - start, 3),
            "keyframes": keyframes,
            "keyframe_times": keyframe_times,
            "sample_frames": sample_frames,
            "start_frame": int(start_f),
            "end_frame": int(end_f),
        })
    return shots, frame_requests


def export_shot_clips(
    video_path: str | Path,
    shots: list[dict[str, Any]],
    out_dir: str | Path,
    *,
    codec: str = "mp4v",
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> list[str]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Unable to open video: {video_path}")

    fps = max(1e-6, float(cap.get(cv2.CAP_PROP_FPS) or 25.0))
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    if width <= 0 or height <= 0:
        cap.release()
        raise RuntimeError(f"Unable to read video dimensions: {video_path}")

    fourcc = cv2.VideoWriter_fourcc(*codec[:4].ljust(4))
    paths: list[str] = []
    try:
        for idx, shot in enumerate(shots, start=1):
            sid = str(shot.get("shot_id") or f"shot_{idx:03d}")
            clip_path = out / f"{sid}.mp4"
            start_frame = int(shot["start_frame"])
            end_frame = int(shot["end_frame"])
            writer = cv2.VideoWriter(str(clip_path), fourcc, fps, (width, height))
            if not writer.isOpened():
                raise RuntimeError(f"Unable to create clip writer: {clip_path}")
            try:
                cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
                current = start_frame
                while current <= end_frame:
                    ok, frame = cap.read()
                    if not ok or frame is None:
                        break
                    writer.write(frame)
                    current += 1
            finally:
                writer.release()
            paths.append(str(clip_path))
            shot["clip_path"] = str(clip_path)
            if progress_callback:
                progress_callback(idx, len(shots), str(clip_path))
    finally:
        cap.release()
    return paths


def detect_shots(
    video_path: str | Path,
    *,
    output_dir: str | Path,
    threshold: float = 0.5,
    shot_prefix: str = "shot",
    keyframe_positions: str | Iterable[float] | None = DEFAULT_KEYFRAME_POSITIONS,
    export_keyframes: bool = True,
    sample_fps: float = 0.0,
    max_sample_frames_per_shot: int = 0,
    export_clips: bool = False,
    progress_callback: ProgressCallback | None = None,
    config: ShotSegmenterConfig | None = None,
) -> dict[str, Any]:
    video = Path(video_path)
    if not video.exists():
        raise FileNotFoundError(f"Video does not exist: {video}")

    cfg = config or ShotSegmenterConfig(
        threshold=threshold,
        shot_prefix=shot_prefix,
        keyframe_positions=keyframe_positions,
        export_keyframes=export_keyframes,
        sample_fps=sample_fps,
        max_sample_frames_per_shot=max_sample_frames_per_shot,
        export_clips=export_clips,
    )

    started = time.perf_counter()
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    info = video_info(video)
    fps = max(1e-6, float(info["fps"]))

    scenes, frame_count, backend_info = _detect_boundaries(video, cfg, progress_callback)
    min_frames = max(2, int(round(fps * max(0.0, cfg.min_shot_seconds))))
    scenes = _normalize_scenes(scenes, frame_count, min_frames)

    keyframe_dir = out_dir / "keyframes"
    sample_dir = keyframe_dir / "samples"
    if cfg.export_keyframes:
        keyframe_dir.mkdir(parents=True, exist_ok=True)
    if cfg.export_keyframes and cfg.sample_fps > 0 and cfg.max_sample_frames_per_shot > 0:
        sample_dir.mkdir(parents=True, exist_ok=True)

    if progress_callback:
        progress_callback(84.0, f"Exporting metadata for {len(scenes)} shots")

    shots, frame_requests = _build_shot_rows(
        scenes,
        fps=fps,
        duration=float(info["duration"]),
        keyframe_dir=keyframe_dir,
        sample_dir=sample_dir,
        config=cfg,
    )

    keyframe_started = time.perf_counter()
    if cfg.export_keyframes:
        def on_frame_progress(processed: int, total: int) -> None:
            if progress_callback and (processed == 1 or processed == total or processed % 30 == 0):
                progress_callback(84.0 + (processed / max(1, total)) * 12.0, f"Exported frames {processed}/{total}")

        extract_frames(video, frame_requests, fps=fps, progress_callback=on_frame_progress)
    keyframe_seconds = time.perf_counter() - keyframe_started

    clip_paths: list[str] = []
    if cfg.export_clips:
        clip_paths = export_shot_clips(
            video,
            shots,
            out_dir / "shot_clips",
            codec=cfg.clip_codec,
            progress_callback=lambda i, total, path: progress_callback(96.0 + i / max(1, total) * 4.0, f"Exported clip {i}/{total}") if progress_callback else None,
        )

    timings = {
        "detection_seconds": round(time.perf_counter() - started - keyframe_seconds, 3),
        "keyframe_seconds": round(keyframe_seconds, 3),
        "total_seconds": round(time.perf_counter() - started, 3),
    }
    backend_info["timings"] = timings

    result = {
        "video_path": str(video),
        "duration": round(float(info["duration"]), 3),
        "fps": round(fps, 3),
        "frame_count": int(frame_count),
        "width": int(info["width"]),
        "height": int(info["height"]),
        "shot_count": len(shots),
        "detection": backend_info,
        "shots": shots,
    }
    if clip_paths:
        result["clip_paths"] = clip_paths

    json_path = out_dir / "shots.json"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if progress_callback:
        progress_callback(100.0, f"Wrote {json_path}")
    return result
