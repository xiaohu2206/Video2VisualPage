from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .constants import STAGE_IDS, STAGES_BY_ID


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def sanitize_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    cleaned = cleaned.strip("._-")
    return cleaned or "video"


def normalize_path(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def stage_info(stage_id: str) -> dict[str, Any]:
    if stage_id not in STAGES_BY_ID:
        raise ValueError(f"Unknown stage id: {stage_id}")
    return STAGES_BY_ID[stage_id]


def stage_dir_name(stage_id: str) -> str:
    return str(stage_info(stage_id)["stage_dir"])


def stage_step_name(stage_id: str) -> str:
    return str(stage_info(stage_id)["step_name"])


def stage_legacy_dir_name(stage_id: str) -> str | None:
    return stage_info(stage_id).get("legacy_stage_dir")


def project_stage_dir(project_dir: str | Path, stage_id: str) -> Path:
    return Path(project_dir) / stage_dir_name(stage_id)


def legacy_project_stage_dir(project_dir: str | Path, stage_id: str) -> Path | None:
    legacy = stage_legacy_dir_name(stage_id)
    return Path(project_dir) / legacy if legacy else None


def find_stage_dir(project_dir: str | Path, stage_id: str) -> Path:
    preferred = project_stage_dir(project_dir, stage_id)
    if preferred.exists():
        return preferred
    legacy = legacy_project_stage_dir(project_dir, stage_id)
    if legacy and legacy.exists():
        return legacy
    return preferred


def stage_relative_path(stage_id: str, filename: str | Path) -> str:
    return f"{stage_dir_name(stage_id)}/{Path(filename).as_posix()}"


def stage_artifact_path(project_dir: str | Path, stage_id: str, filename: str | Path) -> Path:
    return project_stage_dir(project_dir, stage_id) / filename


def find_stage_artifact(project_dir: str | Path, stage_id: str, filename: str | Path) -> Path:
    preferred = project_stage_dir(project_dir, stage_id) / filename
    if preferred.exists():
        return preferred
    legacy_dir = legacy_project_stage_dir(project_dir, stage_id)
    if legacy_dir:
        legacy = legacy_dir / filename
        if legacy.exists():
            return legacy
    return preferred


def canonical_relative_path(relative_path: str | Path) -> str:
    parts = Path(relative_path).as_posix().split("/", 1)
    if len(parts) != 2:
        return Path(relative_path).as_posix()
    head, tail = parts
    for stage_id in STAGE_IDS:
        info = stage_info(stage_id)
        if head in {info["stage_dir"], info.get("legacy_stage_dir")}:
            return f"{info['stage_dir']}/{tail}"
    return Path(relative_path).as_posix()


def to_project_path(project_dir: str | Path, path: str | Path) -> str:
    project = Path(project_dir).resolve()
    target = Path(path).resolve()
    try:
        return target.relative_to(project).as_posix()
    except ValueError:
        return target.as_posix()


def resolve_artifact_path(project_dir: str | Path, value: str | Path | None) -> Path | None:
    if value is None:
        return None
    text = str(value)
    if not text:
        return None
    path = Path(text)
    if path.is_absolute():
        return path
    return Path(project_dir) / path


def find_project_dir(project: str | Path, output_root: str | Path | None = None) -> Path:
    candidate = Path(project).expanduser()
    if candidate.exists():
        return candidate.resolve()

    root = Path(output_root).expanduser() if output_root else repo_root() / "outputs"
    by_id = root / str(project)
    if by_id.exists():
        return by_id.resolve()
    raise FileNotFoundError(f"Project not found: {project}")
