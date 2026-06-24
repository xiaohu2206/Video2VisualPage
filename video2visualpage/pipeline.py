from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .constants import STAGE_DEPENDENCIES, STAGE_IDS, STAGE_IDS_BY_STEP_NAME
from .models.adapter import model_signature
from .paths import find_stage_artifact
from .progress import ProgressReporter
from .stages import (
    run_chapter_write,
    run_init_check,
    run_media_probe,
    run_outline_plan,
    run_qa,
    run_shot_package,
    run_shot_split,
    run_shot_understanding,
    run_static_render,
    run_subtitle_align,
    run_subtitle_extract,
    run_summary_reduce,
)
from .state import get_stage_record, load_run_state, set_stage_status, write_step_manifest
from .storage import read_json
from .utils.eventlog import log_error, log_event

StageRunner = Callable[[str | Path], dict[str, Any]]

STAGE_RUNNERS: dict[str, StageRunner] = {
    "00_init": run_init_check,
    "01_media_probe": run_media_probe,
    "02_shot_split": run_shot_split,
    "03_subtitle_extract": run_subtitle_extract,
    "04_subtitle_align": run_subtitle_align,
    "05_shot_package": run_shot_package,
    "06_shot_understanding": run_shot_understanding,
    "07_summary_reduce": run_summary_reduce,
    "08_outline_plan": run_outline_plan,
    "09_chapter_write": run_chapter_write,
    "10_static_render": run_static_render,
    "11_qa": run_qa,
}

MODEL_SIGNATURE_STAGES = {"06_shot_understanding", "07_summary_reduce", "09_chapter_write"}
MODEL_ROLE_BY_STAGE = {
    "06_shot_understanding": "vision",
    "07_summary_reduce": "copywriting",
    "09_chapter_write": "copywriting",
}


def normalize_stage_id(value: str) -> str:
    value = value.strip()
    if value in STAGE_IDS:
        return value
    if value.isdigit():
        prefix = f"{int(value):02d}"
        matches = [stage_id for stage_id in STAGE_IDS if stage_id.startswith(prefix)]
        if len(matches) == 1:
            return matches[0]
    normalized_value = value.replace("-", "_")
    if normalized_value in STAGE_IDS_BY_STEP_NAME:
        return STAGE_IDS_BY_STEP_NAME[normalized_value]
    aliases = {
        "init": "00_init",
        "media-probe": "01_media_probe",
        "media_probe": "01_media_probe",
        "media-info": "01_media_probe",
        "media_info": "01_media_probe",
        "shot-split": "02_shot_split",
        "shot_split": "02_shot_split",
        "subtitle-extract": "03_subtitle_extract",
        "subtitle_extract": "03_subtitle_extract",
        "subtitle-align": "04_subtitle_align",
        "subtitle_align": "04_subtitle_align",
        "shot-package": "05_shot_package",
        "shot_package": "05_shot_package",
        "analyze-shots": "06_shot_understanding",
        "shot-understanding": "06_shot_understanding",
        "shot_understanding": "06_shot_understanding",
        "reduce-summary": "07_summary_reduce",
        "summary-reduce": "07_summary_reduce",
        "summary_reduce": "07_summary_reduce",
        "plan-outline": "08_outline_plan",
        "outline-plan": "08_outline_plan",
        "outline_plan": "08_outline_plan",
        "write-chapters": "09_chapter_write",
        "chapter-write": "09_chapter_write",
        "chapter_write": "09_chapter_write",
        "render": "10_static_render",
        "static-render": "10_static_render",
        "static_render": "10_static_render",
        "qa": "11_qa",
    }
    if value in aliases:
        return aliases[value]
    if normalized_value in aliases:
        return aliases[normalized_value]
    if value[:2].isdigit():
        matches = [stage_id for stage_id in STAGE_IDS if stage_id.startswith(value[:2])]
        if len(matches) == 1:
            return matches[0]
    raise ValueError(f"Unknown stage: {value}")


def parse_stage_range(value: str) -> tuple[str, str]:
    text = value.strip()
    for separator in ("-", ":", ".."):
        if separator in text:
            start, end = [part.strip() for part in text.split(separator, 1)]
            break
    else:
        start = text
        end = text
    if not start or not end:
        raise ValueError(f"Invalid stage range: {value}")
    return normalize_stage_id(start), normalize_stage_id(end)


def stage_slice(from_stage: str | None = None, to_stage: str | None = None) -> list[str]:
    start = normalize_stage_id(from_stage) if from_stage else STAGE_IDS[0]
    end = normalize_stage_id(to_stage) if to_stage else STAGE_IDS[-1]
    start_index = STAGE_IDS.index(start)
    end_index = STAGE_IDS.index(end)
    if start_index > end_index:
        raise ValueError(f"from_stage must not be after to_stage: {from_stage} > {to_stage}")
    return STAGE_IDS[start_index : end_index + 1]


def first_incomplete_stage(project_dir: str | Path) -> str | None:
    state = load_run_state(project_dir)
    for stage_id in STAGE_IDS:
        stage = get_stage_record(state, stage_id)
        if stage.get("status") != "done":
            return stage_id
    return None


def _stage_manifest(project_dir: str | Path, stage_id: str) -> dict[str, Any] | None:
    manifest_path = find_stage_artifact(project_dir, stage_id, "step_manifest.json")
    if not manifest_path.exists():
        return None
    try:
        return read_json(manifest_path)
    except Exception:  # noqa: BLE001 - malformed manifests must not be treated as current.
        return None


def _manifest_mtime(project_dir: str | Path, stage_id: str) -> float | None:
    manifest_path = find_stage_artifact(project_dir, stage_id, "step_manifest.json")
    if not manifest_path.exists():
        return None
    return manifest_path.stat().st_mtime


def _model_signature_current(project_dir: str | Path, stage_id: str, manifest: dict[str, Any]) -> bool:
    if stage_id not in MODEL_SIGNATURE_STAGES:
        return True
    previous = (manifest.get("result") or {}).get("model_signature") or manifest.get("model_signature")
    if not isinstance(previous, dict) or not previous.get("signature"):
        return False
    try:
        config = read_json(find_stage_artifact(project_dir, "00_init", "config.json"))
    except Exception:  # noqa: BLE001 - let the real stage fail with the detailed config error.
        return False
    current = model_signature(config, MODEL_ROLE_BY_STAGE.get(stage_id))
    return previous.get("signature") == current.get("signature")


def _dependencies_current(project_dir: str | Path, stage_id: str, manifest_mtime: float) -> bool:
    for dependency in STAGE_DEPENDENCIES.get(stage_id, []):
        dependency_mtime = _manifest_mtime(project_dir, dependency)
        if dependency_mtime is None or dependency_mtime > manifest_mtime:
            return False
    return True


def _stage_current(project_dir: str | Path, stage_id: str) -> bool:
    state = load_run_state(project_dir)
    stage_id = normalize_stage_id(stage_id)
    if get_stage_record(state, stage_id).get("status") != "done":
        return False
    manifest = _stage_manifest(project_dir, stage_id)
    if manifest is None or manifest.get("status") != "done":
        return False
    manifest_mtime = _manifest_mtime(project_dir, stage_id)
    if manifest_mtime is None:
        return False
    return _model_signature_current(project_dir, stage_id, manifest) and _dependencies_current(project_dir, stage_id, manifest_mtime)


def dependency_closure(stage_id: str) -> list[str]:
    stage_id = normalize_stage_id(stage_id)
    ordered: list[str] = []
    seen: set[str] = set()

    def visit(current: str) -> None:
        for dependency in STAGE_DEPENDENCIES.get(current, []):
            if dependency in seen:
                continue
            visit(dependency)
            seen.add(dependency)
            ordered.append(dependency)

    visit(stage_id)
    return ordered


def run_stage(project_dir: str | Path, stage_id: str, *, force: bool = False) -> dict[str, Any]:
    project_path = Path(project_dir)
    stage_id = normalize_stage_id(stage_id)
    progress = ProgressReporter(stage_id)
    state = load_run_state(project_path)
    record = get_stage_record(state, stage_id)
    if record.get("status") == "done" and not force and _stage_current(project_path, stage_id):
        log_event(project_path, "stage_skipped", stage_id=stage_id, reason="already_done")
        progress.done("阶段已是最新，跳过", f"outputs={len(record.get('outputs', []))}")
        return {"stage_id": stage_id, "status": "skipped", "outputs": record.get("outputs", [])}

    runner = STAGE_RUNNERS[stage_id]
    progress.start("开始执行阶段")
    set_stage_status(project_path, stage_id, "running")
    try:
        result = runner(project_path)
    except Exception as exc:
        message = str(exc)
        progress.emit(100, "阶段失败", message)
        set_stage_status(project_path, stage_id, "failed", error=message)
        try:
            write_step_manifest(project_path, stage_id, status="failed", outputs=[], error=message)
        except Exception:  # noqa: BLE001 - preserve the original stage failure.
            pass
        log_error(project_path, stage_id, message)
        raise
    outputs = list(result.get("outputs") or record.get("outputs") or [])
    final_status = "failed" if result.get("status") == "failed" else "done"
    manifest_output = write_step_manifest(project_path, stage_id, status=final_status, outputs=outputs, result=result)
    outputs = list(dict.fromkeys([*outputs, manifest_output]))
    set_stage_status(project_path, stage_id, final_status, outputs=outputs)
    log_event(project_path, "stage_done", stage_id=stage_id, status=final_status, outputs=outputs)
    progress.done("阶段完成", f"outputs={len(outputs)}")
    return {
        "stage_id": stage_id,
        "status": final_status,
        **{key: value for key, value in result.items() if key != "status"},
        "outputs": outputs,
    }


def run_stage_with_dependencies(project_dir: str | Path, stage_id: str, *, force: bool = False) -> dict[str, Any]:
    stage_id = normalize_stage_id(stage_id)
    for dependency in dependency_closure(stage_id):
        if not _stage_current(project_dir, dependency):
            run_stage(project_dir, dependency, force=False)
    return run_stage(project_dir, stage_id, force=force)


def run_pipeline(
    project_dir: str | Path,
    *,
    from_stage: str | None = None,
    to_stage: str | None = None,
    force: bool = False,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for stage_id in stage_slice(from_stage, to_stage):
        results.append(run_stage(project_dir, stage_id, force=force))
    return results


def resume_pipeline(project_dir: str | Path, *, to_stage: str | None = None, force: bool = False) -> list[dict[str, Any]]:
    start = first_incomplete_stage(project_dir)
    if start is None:
        return []
    return run_pipeline(project_dir, from_stage=start, to_stage=to_stage, force=force)
