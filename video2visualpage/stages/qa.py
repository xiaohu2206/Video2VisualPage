from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..paths import find_stage_artifact, project_stage_dir, resolve_artifact_path, stage_relative_path
from ..schemas.contracts import CONTRACTS, JSONL_CONTRACTS, validate_stage_contract
from ..storage import atomic_write_json, read_json, read_jsonl, write_jsonl
from ..utils.eventlog import log_event
from ..utils.validation import validate_json_file, validate_jsonl_file


def _check_json_files(project_path: Path) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for path in sorted(project_path.rglob("*.json")):
        ok, error = validate_json_file(path)
        checks.append({"name": "json_parse", "path": path.relative_to(project_path).as_posix(), "status": "passed" if ok else "failed", "error": error})
    for path in sorted(project_path.rglob("*.jsonl")):
        ok, error = validate_jsonl_file(path)
        checks.append({"name": "jsonl_parse", "path": path.relative_to(project_path).as_posix(), "status": "passed" if ok else "failed", "error": error})
    return checks


def _check_outline_refs(project_path: Path) -> list[dict[str, Any]]:
    outline_path = find_stage_artifact(project_path, "08_outline_plan", "outline.json")
    analysis_path = find_stage_artifact(project_path, "06_shot_understanding", "shot_analysis.jsonl")
    if not outline_path.exists() or not analysis_path.exists():
        return [{"name": "outline_shot_refs", "status": "skipped", "reason": "missing inputs"}]
    outline = read_json(outline_path)
    shot_ids = {str(item.get("shot_id")) for item in read_jsonl(analysis_path)}
    missing = []
    for chapter in outline.get("chapters", []):
        for shot_id in chapter.get("shot_ids", []):
            if str(shot_id) not in shot_ids:
                missing.append(str(shot_id))
        representative = str(chapter.get("representative_shot_id"))
        if representative not in shot_ids:
            missing.append(representative)
    return [
        {
            "name": "outline_shot_refs",
            "status": "passed" if not missing else "failed",
            "missing": sorted(set(missing)),
        }
    ]


def _check_chapter_images(project_path: Path) -> list[dict[str, Any]]:
    index_path = find_stage_artifact(project_path, "09_chapter_write", "chapters_index.json")
    if not index_path.exists():
        return [{"name": "chapter_images", "status": "skipped", "reason": "missing chapters_index.json"}]
    index = read_json(index_path)
    missing = []
    empty = []
    for item in index.get("chapters", []):
        chapter = read_json(project_path / item["path"])
        frame = chapter.get("representative_frame")
        resolved = resolve_artifact_path(project_path, frame)
        if not frame or not resolved or not resolved.exists():
            missing.append({"chapter_id": chapter.get("chapter_id"), "frame": frame})
        if not str(chapter.get("body_markdown") or "").strip():
            empty.append(chapter.get("chapter_id"))
    return [
        {"name": "chapter_images", "status": "passed" if not missing else "failed", "missing": missing},
        {"name": "chapter_body", "status": "passed" if not empty else "failed", "empty": empty},
    ]


def _check_final_outputs(project_path: Path) -> list[dict[str, Any]]:
    config = read_json(find_stage_artifact(project_path, "00_init", "config.json"))
    html_path = find_stage_artifact(project_path, "10_static_render", "index.html")
    checks = [
        {
            "name": "html_output",
            "status": "passed" if html_path.exists() else "failed",
            "path": stage_relative_path("10_static_render", "index.html"),
        }
    ]
    wants_pdf = bool(config.get("render", {}).get("output_pdf", False))
    pdf_status_path = find_stage_artifact(project_path, "10_static_render", "pdf_status.json")
    if wants_pdf or pdf_status_path.exists():
        pdf_path = find_stage_artifact(project_path, "10_static_render", "report.pdf")
        if pdf_path.exists():
            checks.append({"name": "pdf_output", "status": "passed", "path": stage_relative_path("10_static_render", "report.pdf")})
        else:
            checks.append(
                {
                    "name": "pdf_output",
                    "status": "warning",
                    "path": stage_relative_path("10_static_render", "report.pdf"),
                    "degraded_to_html": True,
                }
            )
    return checks


def _check_contracts(project_path: Path) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for relative_path in sorted([*CONTRACTS.keys(), *JSONL_CONTRACTS.keys()]):
        if relative_path == stage_relative_path("11_qa", "qa_report.json") and not (project_path / relative_path).exists():
            checks.append({"name": "schema_contract", "path": relative_path, "status": "skipped", "reason": "created_by_current_stage"})
            continue
        result = validate_stage_contract(project_path, relative_path)
        checks.append({"name": "schema_contract", **result})
    return checks


def _collect_errors(checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [check for check in checks if check.get("status") == "failed"]


def _repair_json_text(text: str) -> Any:
    cleaned = text.strip().lstrip("\ufeff")
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", cleaned, flags=re.DOTALL | re.IGNORECASE)
    if fence:
        cleaned = fence.group(1).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    starts = [idx for idx in (cleaned.find("{"), cleaned.find("[")) if idx >= 0]
    ends = [idx for idx in (cleaned.rfind("}"), cleaned.rfind("]")) if idx >= 0]
    if starts and ends:
        cleaned = cleaned[min(starts) : max(ends) + 1]
    cleaned = re.sub(r",\s*([}\]])", r"\1", cleaned)
    return json.loads(cleaned)


def _repair_json_artifacts(project_path: Path) -> list[dict[str, Any]]:
    repairs: list[dict[str, Any]] = []
    for path in sorted(project_path.rglob("*.json")):
        try:
            read_json(path)
            continue
        except Exception:
            pass
        try:
            payload = _repair_json_text(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 - failed repair is reported later by QA checks.
            repairs.append({"type": "json_repair", "path": path.relative_to(project_path).as_posix(), "status": "failed", "error": str(exc)})
            continue
        atomic_write_json(path, payload)
        repairs.append({"type": "json_repair", "path": path.relative_to(project_path).as_posix(), "status": "fixed"})

    for path in sorted(project_path.rglob("*.jsonl")):
        rows: list[Any] = []
        changed = False
        failed = False
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                try:
                    rows.append(_repair_json_text(line))
                    changed = True
                except Exception as exc:  # noqa: BLE001
                    repairs.append({"type": "jsonl_repair", "path": path.relative_to(project_path).as_posix(), "status": "failed", "error": str(exc)})
                    failed = True
                    break
        if changed and not failed:
            write_jsonl(path, rows)
            repairs.append({"type": "jsonl_repair", "path": path.relative_to(project_path).as_posix(), "status": "fixed"})
    return repairs


def _repair_chapter_images(project_path: Path) -> list[dict[str, Any]]:
    index_path = find_stage_artifact(project_path, "09_chapter_write", "chapters_index.json")
    analysis_path = find_stage_artifact(project_path, "06_shot_understanding", "shot_analysis.jsonl")
    package_path = find_stage_artifact(project_path, "05_shot_package", "shot_packages.jsonl")
    if not index_path.exists() or not analysis_path.exists():
        return []

    analysis_by_id = {str(item.get("shot_id")): item for item in read_jsonl(analysis_path)}
    packages_by_id = {str(item.get("shot_id")): item for item in read_jsonl(package_path)} if package_path.exists() else {}
    repairs: list[dict[str, Any]] = []
    index = read_json(index_path)
    for item in index.get("chapters", []):
        chapter_path = project_path / item["path"]
        chapter = read_json(chapter_path)
        current = resolve_artifact_path(project_path, chapter.get("representative_frame"))
        if current and current.exists():
            continue
        replacement = None
        for shot_id in chapter.get("referenced_shots", []):
            candidate = analysis_by_id.get(str(shot_id), {}).get("recommended_display_frame")
            resolved = resolve_artifact_path(project_path, candidate)
            if resolved and resolved.exists():
                replacement = candidate
                break
            for frame in packages_by_id.get(str(shot_id), {}).get("frames", []):
                resolved = resolve_artifact_path(project_path, frame)
                if resolved and resolved.exists():
                    replacement = frame
                    break
            if replacement:
                break
        if replacement:
            chapter["representative_frame"] = replacement
            atomic_write_json(chapter_path, chapter)
            repairs.append({"type": "representative_frame_reselect", "chapter_id": chapter.get("chapter_id"), "status": "fixed"})
        else:
            repairs.append({"type": "representative_frame_reselect", "chapter_id": chapter.get("chapter_id"), "status": "failed"})
    return repairs


def run_qa(project_dir: str | Path, *, autofix: bool | None = None) -> dict[str, Any]:
    project_path = Path(project_dir)
    stage_dir = project_stage_dir(project_path, "11_qa")
    stage_dir.mkdir(parents=True, exist_ok=True)
    try:
        config = read_json(find_stage_artifact(project_path, "00_init", "config.json"))
    except Exception:
        config = {"qa": {"autofix": True}}
    should_fix = bool(config.get("qa", {}).get("autofix", True)) if autofix is None else autofix
    repairs: list[dict[str, Any]] = []

    if should_fix:
        repairs.extend(_repair_json_artifacts(project_path))
        outline_exists = find_stage_artifact(project_path, "08_outline_plan", "outline.json").exists()
        chapters_exist = find_stage_artifact(project_path, "09_chapter_write", "chapters_index.json").exists()
        html_exists = find_stage_artifact(project_path, "10_static_render", "index.html").exists()
        if outline_exists and not chapters_exist:
            from .chapter_write import run_chapter_write

            run_chapter_write(project_path)
            chapters_exist = find_stage_artifact(project_path, "09_chapter_write", "chapters_index.json").exists()
            repairs.append({"type": "missing_chapters_rescue", "status": "fixed"})
        image_repairs = _repair_chapter_images(project_path)
        repairs.extend(image_repairs)
        should_rerender = bool(image_repairs) or not html_exists
        if chapters_exist and should_rerender:
            from .static_render import run_static_render

            run_static_render(project_path)
            repairs.append({"type": "html_rerender", "status": "fixed"})

    checks = []
    checks.extend(_check_json_files(project_path))
    checks.extend(_check_contracts(project_path))
    checks.extend(_check_outline_refs(project_path))
    checks.extend(_check_chapter_images(project_path))
    checks.extend(_check_final_outputs(project_path))
    errors = _collect_errors(checks)
    warnings = [check for check in checks if check.get("status") == "skipped"]
    warnings.extend(check for check in checks if check.get("status") == "warning")
    report = {
        "status": "passed" if not errors else "failed",
        "checks": checks,
        "warnings": warnings,
        "errors": errors,
        "repairs": repairs,
    }
    atomic_write_json(stage_dir / "qa_report.json", report)
    log_event(project_path, "qa_done", status=report["status"], error_count=len(errors), warning_count=len(warnings))
    return {"outputs": [stage_relative_path("11_qa", "qa_report.json")], "status": report["status"], "error_count": len(errors)}
