from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from opencv_shot_segmenter import detect_shots


def _write_synthetic_video(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fps = 12.0
    size = (96, 54)
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, size)
    assert writer.isOpened()
    try:
        colors = [
            (20, 20, 220),
            (20, 200, 20),
            (220, 30, 30),
        ]
        for color in colors:
            frame = np.full((size[1], size[0], 3), color, dtype=np.uint8)
            for _ in range(14):
                writer.write(frame)
    finally:
        writer.release()


def test_detect_shots_exports_json_and_keyframes(tmp_path: Path) -> None:
    video = tmp_path / "synthetic.mp4"
    out_dir = tmp_path / "out"
    _write_synthetic_video(video)

    result = detect_shots(
        video,
        output_dir=out_dir,
        threshold=0.5,
        shot_prefix="movie_shot",
        keyframe_positions="0.12,0.5,0.88",
    )

    assert result["shot_count"] == 3
    assert (out_dir / "shots.json").exists()
    assert result["shots"][0]["shot_id"] == "movie_shot_000001"
    assert len(result["shots"][0]["keyframes"]) == 3
    for frame_path in result["shots"][0]["keyframes"]:
        assert Path(frame_path).exists()


def test_detect_shots_can_skip_keyframes(tmp_path: Path) -> None:
    video = tmp_path / "synthetic.mp4"
    out_dir = tmp_path / "out"
    _write_synthetic_video(video)

    result = detect_shots(video, output_dir=out_dir, export_keyframes=False)

    assert result["shot_count"] >= 1
    assert not (out_dir / "keyframes").exists()
