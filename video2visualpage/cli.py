from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .paths import find_project_dir, repo_root
from .pipeline import (
    dependency_closure,
    normalize_stage_id,
    parse_stage_range,
    resume_pipeline,
    run_pipeline,
    run_stage,
    run_stage_with_dependencies,
)
from .progress import ProgressReporter
from .stages import create_project, run_chapter_write, run_qa, run_static_render
from .state import get_stage_record, load_run_state, set_stage_status, write_step_manifest

DEFAULT_PROJECT_ID = "demo"


def _print(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _default_output_root() -> Path:
    return repo_root() / "outputs"


def _add_existing_project_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--project",
        nargs="?",
        const=DEFAULT_PROJECT_ID,
        default=None,
        help=f"Project directory or id under outputs. Defaults to {DEFAULT_PROJECT_ID}.",
    )


def _add_project_or_video_args(parser: argparse.ArgumentParser) -> None:
    _add_existing_project_arg(parser)
    parser.add_argument("--video", default=None, help="Input video path. Reuses a matching project or creates one.")
    parser.add_argument("--project-name", default=None, help="Project id prefix when --video is used.")
    parser.add_argument("--output-root", default=str(_default_output_root()), help="Output root directory.")
    parser.add_argument("--new-project", action="store_true", help="Deprecated; --video always overwrites the fixed project folder.")


def _existing_project_from_args(args: argparse.Namespace) -> Path:
    return find_project_dir(getattr(args, "project", None) or DEFAULT_PROJECT_ID, args.output_root)


def _project_from_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> Path:
    project = getattr(args, "project", None)
    video = getattr(args, "video", None)
    if project and video:
        parser.error("Use either --project or --video, not both.")
    if video:
        return create_project(video, project_name=getattr(args, "project_name", None), output_root=args.output_root)
    return _existing_project_from_args(args)


def _record_direct_stage(project_dir: Path, stage_id: str, result: dict[str, Any]) -> dict[str, Any]:
    outputs = list(result.get("outputs") or [])
    status = "failed" if result.get("status") == "failed" else "done"
    manifest = write_step_manifest(project_dir, stage_id, status=status, outputs=outputs, result=result)
    outputs = list(dict.fromkeys([*outputs, manifest]))
    set_stage_status(project_dir, stage_id, status, outputs=outputs)
    result["outputs"] = outputs
    return result


def _ensure_dependencies(project_dir: Path, stage_id: str) -> None:
    state = load_run_state(project_dir)
    for dependency in dependency_closure(stage_id):
        if get_stage_record(state, dependency).get("status") != "done":
            run_stage(project_dir, dependency)
            state = load_run_state(project_dir)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Video2VisualPage local JSON/JSONL pipeline.")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Create a project workspace.")
    init.add_argument("--video", required=True, help="Input video path.")
    init.add_argument("--project-name", default=None, help="Project id prefix.")
    init.add_argument("--output-root", default=str(_default_output_root()), help="Output root directory.")

    run = sub.add_parser("run", help="Create a project and run pipeline stages.")
    run.add_argument("video", help="Input video path.")
    run.add_argument("--project-name", default=None, help="Project id prefix.")
    run.add_argument("--output-root", default=str(_default_output_root()), help="Output root directory.")
    run.add_argument("--from-stage", default="media_info", help="First stage to run after init.")
    run.add_argument("--to-stage", default=None, help="Last stage to run.")
    run.add_argument("--steps", "--step-range", dest="step_range", default=None, help="Numeric stage range, for example 1-5 or 6-11. Overrides --from-stage/--to-stage.")
    run.add_argument("--force", action="store_true", help="Rerun completed stages.")

    resume = sub.add_parser("resume", help="Resume from the first incomplete stage.")
    resume.add_argument("project", nargs="?", default=None, help=f"Project directory or id under outputs. Defaults to {DEFAULT_PROJECT_ID}.")
    resume.add_argument("--output-root", default=str(_default_output_root()), help="Output root directory.")
    resume.add_argument("--to-stage", default=None, help="Last stage to run.")
    resume.add_argument("--steps", "--step-range", dest="step_range", default=None, help="Numeric stage range end, for example 1-5 or 6-11. For resume, the range end is used as --to-stage.")
    resume.add_argument("--force", action="store_true", help="Rerun selected stages even if done.")

    stage = sub.add_parser("stage", help="Run one stage.")
    _add_project_or_video_args(stage)
    stage.add_argument("--stage", required=True, help="Stage id or alias.")
    stage.add_argument("--force", action="store_true", help="Rerun if already done.")
    stage.add_argument("--no-deps", action="store_true", help="Do not auto-run missing dependency steps.")

    rerun = sub.add_parser("rerun", help="Rerun a stage range.")
    _add_existing_project_arg(rerun)
    rerun.add_argument("--from", dest="from_stage", default=None, help="First stage to rerun.")
    rerun.add_argument("--to", dest="to_stage", default=None, help="Last stage to rerun.")
    rerun.add_argument("--steps", "--step-range", dest="step_range", default=None, help="Numeric stage range, for example 1-5 or 6-11. Overrides --from/--to.")
    rerun.add_argument("--output-root", default=str(_default_output_root()), help="Output root directory.")

    render = sub.add_parser("render", help="Render HTML and optionally PDF.")
    _add_project_or_video_args(render)
    render.add_argument("--format", default="html", help="Comma separated format list: html,pdf.")

    qa = sub.add_parser("qa", help="Run QA checks.")
    _add_project_or_video_args(qa)
    qa.add_argument("--fix", action="store_true", help="Try supported automatic repairs first.")
    qa.add_argument("--no-fix", action="store_true", help="Disable automatic repairs.")

    write_chapter = sub.add_parser("write-chapter", help="Regenerate one chapter.")
    _add_existing_project_arg(write_chapter)
    write_chapter.add_argument("--chapter-id", required=True, help="Chapter id, for example chapter_001.")
    write_chapter.add_argument("--output-root", default=str(_default_output_root()), help="Output root directory.")

    for alias, stage_id in {
        "media-info": "01_media_probe",
        "media_info": "01_media_probe",
        "media-probe": "01_media_probe",
        "media_probe": "01_media_probe",
        "shot-split": "02_shot_split",
        "shot_split": "02_shot_split",
        "subtitle-extract": "03_subtitle_extract",
        "subtitle_extract": "03_subtitle_extract",
        "subtitle-align": "04_subtitle_align",
        "subtitle_align": "04_subtitle_align",
        "build-shot-packages": "05_shot_package",
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
        "static-render": "10_static_render",
        "static_render": "10_static_render",
    }.items():
        command = sub.add_parser(alias, help=f"Run {stage_id}.")
        command.set_defaults(stage_alias=stage_id)
        _add_project_or_video_args(command)
        command.add_argument("--force", action="store_true", help="Rerun if already done.")
        command.add_argument("--no-deps", action="store_true", help="Do not auto-run missing dependency steps.")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "init":
        progress = ProgressReporter("00_init")
        progress.start("创建项目", f"video={args.video}")
        project_dir = create_project(args.video, project_name=args.project_name, output_root=args.output_root)
        progress.done("项目创建完成", project_dir)
        _print({"project_dir": str(project_dir), "status": "created"})
        return 0

    if args.command == "run":
        progress = ProgressReporter("00_init")
        progress.start("创建项目", f"video={args.video}")
        project_dir = create_project(args.video, project_name=args.project_name, output_root=args.output_root)
        progress.done("项目创建完成", project_dir)
        from_stage, to_stage = (parse_stage_range(args.step_range) if args.step_range else (args.from_stage, args.to_stage))
        results = run_pipeline(project_dir, from_stage=from_stage, to_stage=to_stage, force=args.force)
        _print({"project_dir": str(project_dir), "results": results})
        return 0

    if args.command == "resume":
        project_dir = _existing_project_from_args(args)
        to_stage = parse_stage_range(args.step_range)[1] if args.step_range else args.to_stage
        results = resume_pipeline(project_dir, to_stage=to_stage, force=args.force)
        _print({"project_dir": str(project_dir), "results": results})
        return 0

    if args.command == "stage":
        project_dir = _project_from_args(args, parser)
        runner = run_stage if args.no_deps else run_stage_with_dependencies
        result = runner(project_dir, normalize_stage_id(args.stage), force=args.force)
        _print({"project_dir": str(project_dir), "result": result})
        return 0

    if args.command == "rerun":
        project_dir = _existing_project_from_args(args)
        if not args.step_range and not args.from_stage:
            parser.error("rerun requires --from or --steps.")
        from_stage, to_stage = (parse_stage_range(args.step_range) if args.step_range else (args.from_stage, args.to_stage))
        results = run_pipeline(project_dir, from_stage=from_stage, to_stage=to_stage, force=True)
        _print({"project_dir": str(project_dir), "results": results})
        return 0

    if args.command == "render":
        project_dir = _project_from_args(args, parser)
        _ensure_dependencies(project_dir, "10_static_render")
        progress = ProgressReporter("10_static_render")
        progress.start("开始渲染", f"format={args.format}")
        result = run_static_render(project_dir, output_format=args.format)
        result = _record_direct_stage(project_dir, "10_static_render", result)
        progress.done("渲染完成", f"outputs={len(result.get('outputs') or [])}")
        _print({"project_dir": str(project_dir), "result": result})
        return 0

    if args.command == "qa":
        project_dir = _project_from_args(args, parser)
        _ensure_dependencies(project_dir, "11_qa")
        autofix = False if args.no_fix else (True if args.fix else None)
        progress = ProgressReporter("11_qa")
        progress.start("开始 QA", f"autofix={autofix}")
        result = run_qa(project_dir, autofix=autofix)
        result = _record_direct_stage(project_dir, "11_qa", result)
        progress.done("QA 完成", f"status={result.get('status')}; errors={result.get('error_count')}")
        _print({"project_dir": str(project_dir), "result": result})
        return 0

    if args.command == "write-chapter":
        project_dir = _existing_project_from_args(args)
        _ensure_dependencies(project_dir, "09_chapter_write")
        result = run_chapter_write(project_dir, chapter_id=args.chapter_id)
        result = _record_direct_stage(project_dir, "09_chapter_write", result)
        _print({"project_dir": str(project_dir), "result": result})
        return 0

    if hasattr(args, "stage_alias"):
        project_dir = _project_from_args(args, parser)
        runner = run_stage if args.no_deps else run_stage_with_dependencies
        result = runner(project_dir, args.stage_alias, force=args.force)
        _print({"project_dir": str(project_dir), "result": result})
        return 0

    raise AssertionError(f"Unhandled command: {args.command}")
