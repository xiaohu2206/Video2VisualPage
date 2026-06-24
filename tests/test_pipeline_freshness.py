from __future__ import annotations

from video2visualpage.pipeline import run_stage_with_dependencies
from video2visualpage.stages import create_project
from video2visualpage.state import get_stage_record, load_run_state, set_stage_status, write_step_manifest
from video2visualpage.storage import atomic_write_json, read_json, write_jsonl


def test_model_stage_without_current_signature_is_rerun_for_downstream(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("VIDEO2VISUALPAGE_LLM_PROVIDER", "local_heuristic")
    monkeypatch.setenv("VIDEO2VISUALPAGE_VISION_MODEL_PROVIDER", "local_heuristic")
    monkeypatch.setenv("VIDEO2VISUALPAGE_COPYWRITING_MODEL_PROVIDER", "local_heuristic")
    video = tmp_path / "demo.mp4"
    video.write_bytes(b"fake-video")
    project_dir = create_project(video, project_name="demo", output_root=tmp_path / "outputs")

    (project_dir / "shot_package").mkdir()
    write_jsonl(
        project_dir / "shot_package" / "shot_packages.jsonl",
        [
            {
                "shot_id": "shot_001",
                "time_range": {"start_sec": 0.0, "end_sec": 1.0, "duration_sec": 1.0},
                "frames": [],
                "subtitle_text": "hello",
            }
        ],
    )
    write_step_manifest(project_dir, "05_shot_package", status="done", outputs=["shot_package/shot_packages.jsonl"])
    set_stage_status(project_dir, "05_shot_package", "done", outputs=["shot_package/shot_packages.jsonl", "shot_package/step_manifest.json"])

    (project_dir / "shot_understanding").mkdir()
    write_jsonl(project_dir / "shot_understanding" / "shot_analysis.jsonl", [{"shot_id": "shot_001", "merged_summary": "old"}])
    write_step_manifest(
        project_dir,
        "06_shot_understanding",
        status="done",
        outputs=["shot_understanding/shot_analysis.jsonl"],
        result={"outputs": ["shot_understanding/shot_analysis.jsonl"], "shot_count": 1},
    )
    set_stage_status(
        project_dir,
        "06_shot_understanding",
        "done",
        outputs=["shot_understanding/shot_analysis.jsonl", "shot_understanding/step_manifest.json"],
    )

    result = run_stage_with_dependencies(project_dir, "07_summary_reduce")

    assert result["stage_id"] == "07_summary_reduce"
    assert result["status"] == "done"
    shot_manifest = read_json(project_dir / "shot_understanding" / "step_manifest.json")
    assert shot_manifest["result"]["model_signature"]["provider"] == "local_heuristic"
    state = load_run_state(project_dir)
    assert get_stage_record(state, "06_shot_understanding")["status"] == "done"
