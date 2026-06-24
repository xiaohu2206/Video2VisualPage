from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from video2visualpage import cli
from video2visualpage.cli import main
from video2visualpage.pipeline import parse_stage_range, stage_slice


def _make_video(path: Path) -> None:
    width, height = 128, 72
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10.0, (width, height))
    assert writer.isOpened()
    try:
        for index in range(12):
            frame = np.zeros((height, width, 3), dtype=np.uint8)
            frame[:, :, 0] = min(255, index * 18)
            frame[:, :, 1] = 90
            frame[:, :, 2] = 120
            writer.write(frame)
    finally:
        writer.release()


def test_step_command_can_start_from_video_and_write_own_folder(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("VIDEO2VISUALPAGE_ENABLE_ONLINE_ASR", "0")
    video = tmp_path / "tiny.mp4"
    _make_video(video)
    output_root = tmp_path / "outputs"

    status = main(
        [
            "media-info",
            "--video",
            str(video),
            "--project-name",
            "solo",
            "--output-root",
            str(output_root),
        ]
    )

    assert status == 0

    status = main(
        [
            "shot-split",
            "--video",
            str(video),
            "--project-name",
            "solo",
            "--output-root",
            str(output_root),
        ]
    )

    assert status == 0
    projects = list(output_root.iterdir())
    assert len(projects) == 1
    project_dir = projects[0]
    assert (project_dir / "media_info" / "media_info.json").exists()
    assert (project_dir / "shot_split" / "normalized_shots.json").exists()
    assert (project_dir / "shot_split" / "step_manifest.json").exists()


def test_step_command_new_project_keeps_fixed_output_folder(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("VIDEO2VISUALPAGE_ENABLE_ONLINE_ASR", "0")
    video = tmp_path / "tiny.mp4"
    _make_video(video)
    output_root = tmp_path / "outputs"

    assert main(["media-info", "--video", str(video), "--project-name", "solo", "--output-root", str(output_root)]) == 0
    assert (
        main(
            [
                "media-info",
                "--video",
                str(video),
                "--project-name",
                "solo",
                "--output-root",
                str(output_root),
                "--new-project",
            ]
        )
        == 0
    )

    projects = list(output_root.iterdir())
    assert len(projects) == 1
    assert projects[0].name == "solo"


def test_numeric_stage_range_maps_to_padded_stage_ids() -> None:
    assert parse_stage_range("1-5") == ("01_media_probe", "05_shot_package")
    assert [stage["stage_id"] if isinstance(stage, dict) else stage for stage in stage_slice("1", "5")] == [
        "01_media_probe",
        "02_shot_split",
        "03_subtitle_extract",
        "04_subtitle_align",
        "05_shot_package",
    ]


def test_step_command_defaults_to_demo_project(tmp_path, monkeypatch) -> None:
    output_root = tmp_path / "outputs"
    project_dir = output_root / "demo"
    calls: dict[str, object] = {}

    def fake_find_project_dir(project, output_root_arg=None):
        calls["find_project_dir"] = {"project": project, "output_root": output_root_arg}
        return project_dir

    def fake_run_stage_with_dependencies(resolved_project_dir, stage_id, *, force=False):
        calls["run_stage"] = {"project_dir": resolved_project_dir, "stage_id": stage_id, "force": force}
        return {"stage_id": stage_id, "status": "done", "outputs": []}

    monkeypatch.setattr(cli, "find_project_dir", fake_find_project_dir)
    monkeypatch.setattr(cli, "run_stage_with_dependencies", fake_run_stage_with_dependencies)

    assert cli.main(["outline-plan", "--output-root", str(output_root)]) == 0

    assert calls["find_project_dir"] == {"project": "demo", "output_root": str(output_root)}
    assert calls["run_stage"] == {
        "project_dir": project_dir,
        "stage_id": "08_outline_plan",
        "force": False,
    }


def test_project_flag_without_value_defaults_to_demo_project(tmp_path, monkeypatch) -> None:
    output_root = tmp_path / "outputs"
    project_dir = output_root / "demo"
    calls: dict[str, object] = {}

    def fake_find_project_dir(project, output_root_arg=None):
        calls["find_project_dir"] = {"project": project, "output_root": output_root_arg}
        return project_dir

    def fake_run_stage(resolved_project_dir, stage_id, *, force=False):
        calls["run_stage"] = {"project_dir": resolved_project_dir, "stage_id": stage_id, "force": force}
        return {"stage_id": stage_id, "status": "done", "outputs": []}

    monkeypatch.setattr(cli, "find_project_dir", fake_find_project_dir)
    monkeypatch.setattr(cli, "run_stage", fake_run_stage)

    assert cli.main(["summary-reduce", "--project", "--output-root", str(output_root), "--no-deps"]) == 0

    assert calls["find_project_dir"] == {"project": "demo", "output_root": str(output_root)}
    assert calls["run_stage"] == {
        "project_dir": project_dir,
        "stage_id": "07_summary_reduce",
        "force": False,
    }


def test_rerun_command_defaults_to_demo_project(tmp_path, monkeypatch) -> None:
    output_root = tmp_path / "outputs"
    project_dir = output_root / "demo"
    calls: dict[str, object] = {}

    def fake_find_project_dir(project, output_root_arg=None):
        calls["find_project_dir"] = {"project": project, "output_root": output_root_arg}
        return project_dir

    def fake_run_pipeline(resolved_project_dir, *, from_stage=None, to_stage=None, force=False):
        calls["run_pipeline"] = {
            "project_dir": resolved_project_dir,
            "from_stage": from_stage,
            "to_stage": to_stage,
            "force": force,
        }
        return [{"stage_id": from_stage, "status": "done"}]

    monkeypatch.setattr(cli, "find_project_dir", fake_find_project_dir)
    monkeypatch.setattr(cli, "run_pipeline", fake_run_pipeline)

    assert cli.main(["rerun", "--steps", "6-8", "--output-root", str(output_root)]) == 0

    assert calls["find_project_dir"] == {"project": "demo", "output_root": str(output_root)}
    assert calls["run_pipeline"] == {
        "project_dir": project_dir,
        "from_stage": "06_shot_understanding",
        "to_stage": "08_outline_plan",
        "force": True,
    }


def test_run_command_accepts_numeric_step_range(tmp_path, monkeypatch) -> None:
    video = tmp_path / "tiny.mp4"
    video.write_bytes(b"fake-video")
    project_dir = tmp_path / "outputs" / "demo"
    calls: dict[str, object] = {}

    def fake_create_project(video_path, *, project_name=None, output_root=None):
        calls["create_project"] = {
            "video": str(video_path),
            "project_name": project_name,
            "output_root": output_root,
        }
        return project_dir

    def fake_run_pipeline(created_project_dir, *, from_stage=None, to_stage=None, force=False):
        calls["run_pipeline"] = {
            "project_dir": created_project_dir,
            "from_stage": from_stage,
            "to_stage": to_stage,
            "force": force,
        }
        return [{"stage_id": from_stage, "status": "done"}]

    monkeypatch.setattr(cli, "create_project", fake_create_project)
    monkeypatch.setattr(cli, "run_pipeline", fake_run_pipeline)

    status = cli.main(
        [
            "run",
            str(video),
            "--project-name",
            "demo",
            "--output-root",
            str(tmp_path / "outputs"),
            "--to-stage",
            "qa",
            "--steps",
            "1-5",
        ]
    )

    assert status == 0
    assert calls["run_pipeline"] == {
        "project_dir": project_dir,
        "from_stage": "01_media_probe",
        "to_stage": "05_shot_package",
        "force": False,
    }
