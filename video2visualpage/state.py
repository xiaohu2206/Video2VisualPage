from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .constants import PIPELINE_VERSION, STAGES
from .paths import find_stage_artifact, project_stage_dir, stage_relative_path, stage_step_name
from .storage import atomic_write_json, read_json


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def new_run_state(project_id: str) -> dict[str, Any]:
    stages = []
    for item in STAGES:
        stages.append(
            {
                "stage_id": item["stage_id"],
                "name": item["name"],
                "stage_dir": item["stage_dir"],
                "status": "pending",
                "outputs": [],
                "started_at": None,
                "finished_at": None,
                "error": None,
            }
        )
    return {
        "project_id": project_id,
        "pipeline_version": PIPELINE_VERSION,
        "stages": stages,
        "updated_at": now_iso(),
    }


def run_state_path(project_dir: str | Path) -> Path:
    return find_stage_artifact(project_dir, "00_init", "run_state.json")


def write_step_manifest(
    project_dir: str | Path,
    stage_id: str,
    *,
    status: str,
    outputs: list[str],
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> str:
    manifest_rel = stage_relative_path(stage_id, "step_manifest.json")
    payload = {
        "stage_id": stage_id,
        "step_name": stage_step_name(stage_id),
        "stage_dir": project_stage_dir(project_dir, stage_id).name,
        "status": status,
        "outputs": outputs,
        "json_outputs": [item for item in outputs if item.endswith((".json", ".jsonl"))],
        "error": error,
        "result": result or {},
        "updated_at": now_iso(),
    }
    atomic_write_json(project_stage_dir(project_dir, stage_id) / "step_manifest.json", payload)
    return manifest_rel


def load_run_state(project_dir: str | Path) -> dict[str, Any]:
    return read_json(run_state_path(project_dir))


def save_run_state(project_dir: str | Path, state: dict[str, Any]) -> None:
    state["updated_at"] = now_iso()
    atomic_write_json(run_state_path(project_dir), state)


def get_stage_record(state: dict[str, Any], stage_id: str) -> dict[str, Any]:
    for stage in state.get("stages", []):
        if stage.get("stage_id") == stage_id:
            return stage
    raise KeyError(f"Unknown stage in run state: {stage_id}")


def set_stage_status(
    project_dir: str | Path,
    stage_id: str,
    status: str,
    *,
    outputs: list[str] | None = None,
    error: str | None = None,
) -> None:
    state = load_run_state(project_dir)
    stage = get_stage_record(state, stage_id)
    stage["status"] = status
    if status == "running":
        stage["started_at"] = now_iso()
        stage["finished_at"] = None
        stage["error"] = None
    if status in {"done", "failed", "skipped"}:
        stage["finished_at"] = now_iso()
    if outputs is not None:
        stage["outputs"] = outputs
    if error is not None:
        stage["error"] = error
    elif status == "done":
        stage["error"] = None
    save_run_state(project_dir, state)
