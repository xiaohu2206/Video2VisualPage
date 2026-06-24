from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from video2visualpage.pipeline import run_pipeline
from video2visualpage.stages import create_project
from video2visualpage.storage import read_json


def _make_video(path: Path) -> None:
    width, height = 160, 90
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10.0, (width, height))
    assert writer.isOpened()
    try:
        for index in range(20):
            frame = np.zeros((height, width, 3), dtype=np.uint8)
            frame[:, :, 0] = min(255, index * 10)
            frame[:, :, 1] = 60
            frame[:, :, 2] = 180
            writer.write(frame)
    finally:
        writer.release()


def test_full_pipeline_generates_html_and_passes_qa(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("VIDEO2VISUALPAGE_ENABLE_ONLINE_ASR", "0")
    monkeypatch.setenv("VIDEO2VISUALPAGE_LLM_PROVIDER", "local_heuristic")
    monkeypatch.setenv("VIDEO2VISUALPAGE_VISION_MODEL_PROVIDER", "local_heuristic")
    monkeypatch.setenv("VIDEO2VISUALPAGE_COPYWRITING_MODEL_PROVIDER", "local_heuristic")
    video = tmp_path / "tiny.mp4"
    _make_video(video)

    project_dir = create_project(video, project_name="tiny", output_root=tmp_path / "outputs")
    run_pipeline(project_dir, from_stage="media_info", to_stage="qa")

    assert (project_dir / "static_render" / "index.html").exists()
    assert (project_dir / "static_render" / "render_result.json").exists()
    assert (project_dir / "shot_split" / "step_manifest.json").exists()
    report = read_json(project_dir / "qa" / "qa_report.json")
    assert report["status"] == "passed"
    html = (project_dir / "static_render" / "index.html").read_text(encoding="utf-8")
    assert "assets/images/" in html
