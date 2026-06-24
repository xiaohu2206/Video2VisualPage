from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from ..config import default_config
from ..constants import PIPELINE_VERSION
from ..paths import find_stage_artifact, normalize_path, project_stage_dir, sanitize_name, stage_relative_path
from ..state import new_run_state, now_iso, write_step_manifest
from ..storage import atomic_write_json, read_json
from ..utils.eventlog import log_event
from ..utils.hashing import sha256_file


def _project_id(project_name: str) -> str:
    return sanitize_name(project_name)


def _reset_project_dir(root: Path, project_dir: Path) -> None:
    root_resolved = root.resolve()
    project_resolved = project_dir.resolve()
    if project_resolved.parent != root_resolved:
        raise RuntimeError(f"Refuse to overwrite project outside output root: {project_resolved}")
    if project_resolved == root_resolved:
        raise RuntimeError(f"Refuse to overwrite output root: {project_resolved}")
    if project_dir.is_dir():
        shutil.rmtree(project_dir)
    elif project_dir.exists():
        project_dir.unlink()


def _same_path(left: str | Path, right: str | Path) -> bool:
    return os.path.normcase(str(Path(left).expanduser().resolve())) == os.path.normcase(str(Path(right).expanduser().resolve()))


def find_reusable_project(
    video_path: str | Path,
    *,
    project_name: str | None = None,
    output_root: str | Path = "outputs",
) -> Path | None:
    video = normalize_path(video_path)
    root = normalize_path(output_root)
    if not root.exists():
        return None

    project_dir = root / _project_id(project_name or video.stem)
    project_json_path = find_stage_artifact(project_dir, "00_init", "project.json")
    if not project_json_path.exists():
        return None
    try:
        project = read_json(project_json_path)
    except Exception:  # noqa: BLE001 - ignore malformed old output folders.
        return None
    input_video = project.get("input_video")
    return project_dir if input_video and _same_path(input_video, video) else None


def create_project(video_path: str | Path, *, project_name: str | None = None, output_root: str | Path = "outputs") -> Path:
    video = normalize_path(video_path)
    if not video.exists():
        raise FileNotFoundError(f"Input video does not exist: {video}")

    root = normalize_path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    project_id = _project_id(project_name or video.stem)
    project_dir = root / project_id
    _reset_project_dir(root, project_dir)
    init_dir = project_stage_dir(project_dir, "00_init")
    logs_dir = project_dir / "logs"
    init_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    project_json: dict[str, Any] = {
        "project_id": project_id,
        "input_video": str(video),
        "output_dir": str(project_dir),
        "created_at": now_iso(),
        "pipeline_version": PIPELINE_VERSION,
        "source_hash": sha256_file(video),
    }
    state = new_run_state(project_id)
    for stage in state["stages"]:
        if stage["stage_id"] == "00_init":
            stage["status"] = "done"
            stage["started_at"] = project_json["created_at"]
            stage["finished_at"] = project_json["created_at"]
            stage["outputs"] = [
                stage_relative_path("00_init", "project.json"),
                stage_relative_path("00_init", "run_state.json"),
                stage_relative_path("00_init", "config.json"),
                stage_relative_path("00_init", "step_manifest.json"),
            ]

    atomic_write_json(init_dir / "project.json", project_json)
    atomic_write_json(init_dir / "config.json", default_config())
    atomic_write_json(init_dir / "run_state.json", state)
    write_step_manifest(
        project_dir,
        "00_init",
        status="done",
        outputs=state["stages"][0]["outputs"][:-1],
        result={"project_id": project_id, "input_video": str(video)},
    )
    log_event(project_dir, "project_created", project_id=project_id, input_video=str(video))
    return project_dir


def run_init_check(project_dir: str | Path) -> dict[str, Any]:
    project = read_json(find_stage_artifact(project_dir, "00_init", "project.json"))
    config = read_json(find_stage_artifact(project_dir, "00_init", "config.json"))
    video = Path(project["input_video"])
    if not video.exists():
        raise FileNotFoundError(f"Input video does not exist: {video}")
    return {
        "project_id": project["project_id"],
        "input_video": str(video),
        "config_keys": sorted(config.keys()),
    }
