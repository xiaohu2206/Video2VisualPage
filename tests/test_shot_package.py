from __future__ import annotations

from video2visualpage.stages.shot_package import build_shot_packages


def test_shot_package_marks_missing_frames(tmp_path) -> None:
    shots = [
        {
            "shot_id": "shot_001",
            "start_sec": 0.0,
            "end_sec": 1.0,
            "duration_sec": 1.0,
            "keyframes": [{"path": "missing.jpg"}],
        }
    ]
    aligned = [{"shot_id": "shot_001", "subtitle_text": "hello", "subtitle_segments": []}]

    packages = build_shot_packages(tmp_path, shots, aligned)

    assert packages[0]["subtitle_text"] == "hello"
    assert "missing_frame:missing.jpg" in packages[0]["warnings"]
