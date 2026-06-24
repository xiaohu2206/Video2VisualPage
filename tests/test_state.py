from __future__ import annotations

from pathlib import Path

from video2visualpage.stages import create_project
from video2visualpage.state import get_stage_record, load_run_state
from video2visualpage.storage import read_json


def test_create_project_initializes_state(tmp_path) -> None:
    video = tmp_path / "demo.mp4"
    video.write_bytes(b"fake-video")

    project_dir = create_project(video, project_name="demo", output_root=tmp_path / "outputs")
    project = read_json(Path(project_dir) / "init" / "project.json")
    state = load_run_state(project_dir)

    assert Path(project_dir).name == "demo"
    assert project["project_id"] == "demo"
    assert (Path(project_dir) / "init" / "step_manifest.json").exists()
    assert get_stage_record(state, "00_init")["status"] == "done"
    assert get_stage_record(state, "01_media_probe")["status"] == "pending"


def test_create_project_overwrites_existing_fixed_project_dir(tmp_path) -> None:
    video = tmp_path / "demo.mp4"
    video.write_bytes(b"fake-video")
    output_root = tmp_path / "outputs"

    project_dir = create_project(video, project_name="demo", output_root=output_root)
    stale = Path(project_dir) / "stale.txt"
    stale.write_text("old", encoding="utf-8")

    project_dir_again = create_project(video, project_name="demo", output_root=output_root)

    assert project_dir_again == project_dir
    assert not stale.exists()
    assert (Path(project_dir_again) / "init" / "project.json").exists()
    assert len(list(output_root.iterdir())) == 1
