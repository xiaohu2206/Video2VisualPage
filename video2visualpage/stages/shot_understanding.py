from __future__ import annotations

from pathlib import Path
from typing import Any

from ..models import LocalModelAdapter
from ..models.adapter import model_signature
from ..paths import find_stage_artifact, project_stage_dir, stage_relative_path
from ..progress import ProgressReporter
from ..storage import read_json, read_jsonl, write_jsonl
from ..utils.eventlog import log_error, log_event


def run_shot_understanding(project_dir: str | Path) -> dict[str, Any]:
    project_path = Path(project_dir)
    stage_dir = project_stage_dir(project_path, "06_shot_understanding")
    stage_dir.mkdir(parents=True, exist_ok=True)
    config = read_json(find_stage_artifact(project_path, "00_init", "config.json"))
    packages = read_jsonl(find_stage_artifact(project_path, "05_shot_package", "shot_packages.jsonl"))
    progress = ProgressReporter("06_shot_understanding")
    progress.start("准备分析镜头", f"shots={len(packages)}")
    adapter = LocalModelAdapter(project_path, config, model_role="vision", progress=progress)
    rows: list[dict[str, Any]] = []
    total = len(packages)
    for index, package in enumerate(packages, start=1):
        shot_id = str(package.get("shot_id") or f"shot_{index:03d}")
        progress.item_start(
            index,
            total,
            "分析镜头",
            shot_id,
            f"frames={len(package.get('frames') or [])}; subtitle={str(package.get('subtitle_text') or '')[:80]}",
        )
        try:
            rows.append(adapter.analyze_shot(package))
        except Exception as exc:  # noqa: BLE001 - add shot context before failing the stage.
            message = f"shot_understanding failed for {shot_id}: {exc}"
            log_error(project_path, "06_shot_understanding", message, shot_id=shot_id)
            raise RuntimeError(message) from exc
        progress.item_done(index, total, "完成镜头分析", shot_id)
    write_jsonl(stage_dir / "shot_analysis.jsonl", rows)
    log_event(project_path, "shot_understanding_done", shot_count=len(rows))
    progress.done("镜头理解完成", f"shots={len(rows)}")
    return {
        "outputs": [stage_relative_path("06_shot_understanding", "shot_analysis.jsonl")],
        "shot_count": len(rows),
        "model_signature": model_signature(config, "vision"),
    }
