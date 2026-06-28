from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import threading
from typing import Any

from ..models import LocalModelAdapter
from ..models.adapter import effective_model_config, model_signature
from ..paths import find_stage_artifact, project_stage_dir, stage_relative_path
from ..progress import ProgressReporter
from ..storage import read_json, read_jsonl, write_jsonl
from ..utils.eventlog import log_error, log_event


def _int_value(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _ocr_concurrency(config: dict[str, Any], total: int) -> int:
    if total <= 1:
        return 1
    vision_config = effective_model_config(config, "vision")
    workers = _int_value(vision_config.get("ocr_concurrency"), 1)
    return max(1, min(total, workers))


def _package_detail(package: dict[str, Any]) -> str:
    return f"frames={len(package.get('frames') or [])}; subtitle={str(package.get('subtitle_text') or '')[:80]}"


def run_shot_understanding(project_dir: str | Path) -> dict[str, Any]:
    project_path = Path(project_dir)
    stage_dir = project_stage_dir(project_path, "06_shot_understanding")
    stage_dir.mkdir(parents=True, exist_ok=True)
    config = read_json(find_stage_artifact(project_path, "00_init", "config.json"))
    packages = read_jsonl(find_stage_artifact(project_path, "05_shot_package", "shot_packages.jsonl"))
    progress = ProgressReporter("06_shot_understanding")
    progress.start("准备分析镜头", f"shots={len(packages)}")
    total = len(packages)
    workers = _ocr_concurrency(config, total)
    rows: list[dict[str, Any] | None] = [None] * total

    if workers <= 1:
        adapter = LocalModelAdapter(project_path, config, model_role="vision", progress=progress)
        for index, package in enumerate(packages, start=1):
            shot_id = str(package.get("shot_id") or f"shot_{index:03d}")
            progress.item_start(index, total, "分析镜头", shot_id, _package_detail(package))
            try:
                rows[index - 1] = adapter.analyze_shot(package)
            except Exception as exc:  # noqa: BLE001 - add shot context before failing the stage.
                message = f"shot_understanding failed for {shot_id}: {exc}"
                log_error(project_path, "06_shot_understanding", message, shot_id=shot_id)
                raise RuntimeError(message) from exc
            progress.item_done(index, total, "完成镜头分析", shot_id)
    else:
        progress.emit(None, "并发分析镜头", f"workers={workers}; shots={total}")
        thread_state = threading.local()

        def worker_analyze(package: dict[str, Any]) -> dict[str, Any]:
            adapter = getattr(thread_state, "adapter", None)
            if adapter is None:
                adapter = LocalModelAdapter(project_path, config, model_role="vision", progress=None)
                thread_state.adapter = adapter
            return adapter.analyze_shot(package)

        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="shot-ocr") as executor:
            futures = {}
            for index, package in enumerate(packages, start=1):
                shot_id = str(package.get("shot_id") or f"shot_{index:03d}")
                progress.emit(None, f"提交镜头分析 {index}/{total}: {shot_id}", _package_detail(package))
                futures[executor.submit(worker_analyze, package)] = (index, shot_id)

            completed = 0
            for future in as_completed(futures):
                index, shot_id = futures[future]
                try:
                    rows[index - 1] = future.result()
                except Exception as exc:  # noqa: BLE001 - add shot context before failing the stage.
                    message = f"shot_understanding failed for {shot_id}: {exc}"
                    log_error(project_path, "06_shot_understanding", message, shot_id=shot_id)
                    raise RuntimeError(message) from exc
                completed += 1
                progress.emit(
                    round(completed * 100 / total),
                    f"完成镜头分析 {completed}/{total}: {shot_id}",
                    f"source_index={index}",
                )

    completed_rows = [row for row in rows if row is not None]
    write_jsonl(stage_dir / "shot_analysis.jsonl", completed_rows)
    log_event(project_path, "shot_understanding_done", shot_count=len(completed_rows), ocr_concurrency=workers)
    progress.done("镜头理解完成", f"shots={len(completed_rows)}")
    return {
        "outputs": [stage_relative_path("06_shot_understanding", "shot_analysis.jsonl")],
        "shot_count": len(completed_rows),
        "model_signature": model_signature(config, "vision"),
    }
