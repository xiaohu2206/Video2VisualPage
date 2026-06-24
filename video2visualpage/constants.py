from __future__ import annotations

PIPELINE_VERSION = "0.1.0"

STAGES = [
    {
        "stage_id": "00_init",
        "step_name": "init",
        "name": "Project initialization",
        "stage_dir": "init",
        "legacy_stage_dir": "00_init",
        "outputs": ["init/project.json", "init/run_state.json", "init/config.json"],
    },
    {
        "stage_id": "01_media_probe",
        "step_name": "media_info",
        "name": "Media info",
        "stage_dir": "media_info",
        "legacy_stage_dir": "01_media_probe",
        "outputs": ["media_info/media_info.json"],
    },
    {
        "stage_id": "02_shot_split",
        "step_name": "shot_split",
        "name": "Shot split",
        "stage_dir": "shot_split",
        "legacy_stage_dir": "02_shot_split",
        "outputs": ["shot_split/shots.json", "shot_split/normalized_shots.json"],
    },
    {
        "stage_id": "03_subtitle_extract",
        "step_name": "subtitle_extract",
        "name": "Subtitle extract",
        "stage_dir": "subtitle_extract",
        "legacy_stage_dir": "03_subtitle_extract",
        "outputs": ["subtitle_extract/subtitles.json", "subtitle_extract/subtitles.srt"],
    },
    {
        "stage_id": "04_subtitle_align",
        "step_name": "subtitle_align",
        "name": "Subtitle align",
        "stage_dir": "subtitle_align",
        "legacy_stage_dir": "04_subtitle_align",
        "outputs": ["subtitle_align/shot_subtitles.json"],
    },
    {
        "stage_id": "05_shot_package",
        "step_name": "shot_package",
        "name": "Shot package",
        "stage_dir": "shot_package",
        "legacy_stage_dir": "05_shot_package",
        "outputs": ["shot_package/shot_packages.jsonl"],
    },
    {
        "stage_id": "06_shot_understanding",
        "step_name": "shot_understanding",
        "name": "Shot understanding",
        "stage_dir": "shot_understanding",
        "legacy_stage_dir": "06_shot_understanding",
        "outputs": ["shot_understanding/shot_analysis.jsonl"],
    },
    {
        "stage_id": "07_summary_reduce",
        "step_name": "summary_reduce",
        "name": "Summary reduce",
        "stage_dir": "summary_reduce",
        "legacy_stage_dir": "07_summary_reduce",
        "outputs": ["summary_reduce/chunk_summaries.jsonl", "summary_reduce/global_summary.json"],
    },
    {
        "stage_id": "08_outline_plan",
        "step_name": "outline_plan",
        "name": "Outline plan",
        "stage_dir": "outline_plan",
        "legacy_stage_dir": "08_outline_plan",
        "outputs": ["outline_plan/outline.json"],
    },
    {
        "stage_id": "09_chapter_write",
        "step_name": "chapter_write",
        "name": "Chapter write",
        "stage_dir": "chapter_write",
        "legacy_stage_dir": "09_chapter_write",
        "outputs": ["chapter_write/chapters_index.json"],
    },
    {
        "stage_id": "10_static_render",
        "step_name": "static_render",
        "name": "Static render",
        "stage_dir": "static_render",
        "legacy_stage_dir": "10_static_render",
        "outputs": ["static_render/index.html"],
    },
    {
        "stage_id": "11_qa",
        "step_name": "qa",
        "name": "QA",
        "stage_dir": "qa",
        "legacy_stage_dir": "11_qa",
        "outputs": ["qa/qa_report.json"],
    },
]

STAGE_IDS = [stage["stage_id"] for stage in STAGES]

STAGE_DEPENDENCIES = {
    "00_init": [],
    "01_media_probe": [],
    "02_shot_split": ["01_media_probe"],
    "03_subtitle_extract": ["01_media_probe"],
    "04_subtitle_align": ["02_shot_split", "03_subtitle_extract"],
    "05_shot_package": ["02_shot_split", "04_subtitle_align"],
    "06_shot_understanding": ["05_shot_package"],
    "07_summary_reduce": ["06_shot_understanding"],
    "08_outline_plan": ["06_shot_understanding", "07_summary_reduce"],
    "09_chapter_write": ["06_shot_understanding", "07_summary_reduce", "08_outline_plan"],
    "10_static_render": ["09_chapter_write"],
    "11_qa": ["10_static_render"],
}

STAGES_BY_ID = {stage["stage_id"]: stage for stage in STAGES}
STAGE_IDS_BY_STEP_NAME = {stage["step_name"]: stage["stage_id"] for stage in STAGES}
