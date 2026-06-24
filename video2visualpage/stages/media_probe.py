from __future__ import annotations

from pathlib import Path
from typing import Any

from ..paths import find_stage_artifact, project_stage_dir, stage_relative_path
from ..storage import atomic_write_json, read_json
from ..tools.media_probe import probe_media
from ..utils.eventlog import log_event


def run_media_probe(project_dir: str | Path) -> dict[str, Any]:
    project_path = Path(project_dir)
    project = read_json(find_stage_artifact(project_path, "00_init", "project.json"))
    stage_dir = project_stage_dir(project_path, "01_media_probe")
    stage_dir.mkdir(parents=True, exist_ok=True)
    info = probe_media(project["input_video"])
    atomic_write_json(stage_dir / "media_info.json", info)
    log_event(project_path, "media_probe_done", duration_sec=info.get("duration_sec"), has_audio=info.get("has_audio"))
    return {"outputs": [stage_relative_path("01_media_probe", "media_info.json")], "media_info": info}
