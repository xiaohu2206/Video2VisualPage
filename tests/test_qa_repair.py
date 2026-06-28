from __future__ import annotations

from video2visualpage.stages.qa import (
    _check_chapter_subsection_body,
    _check_chapter_write_warnings,
    _repair_chapter_images,
    _repair_json_text,
)
from video2visualpage.storage import atomic_write_json, read_json, write_jsonl


def test_repair_json_text_extracts_fenced_json_and_trailing_comma() -> None:
    assert _repair_json_text("```json\n{\"a\": 1,}\n```") == {"a": 1}


def test_repair_chapter_images_reselects_existing_frame(tmp_path) -> None:
    frame = tmp_path / "shot_split" / "keyframes" / "shot_001.jpg"
    frame.parent.mkdir(parents=True)
    frame.write_bytes(b"image")
    (tmp_path / "chapter_write").mkdir()
    (tmp_path / "shot_understanding").mkdir()
    (tmp_path / "shot_package").mkdir()

    atomic_write_json(
        tmp_path / "chapter_write" / "chapters_index.json",
        {"chapters": [{"chapter_id": "chapter_001", "path": "chapter_write/chapter_001.json"}]},
    )
    atomic_write_json(
        tmp_path / "chapter_write" / "chapter_001.json",
        {"chapter_id": "chapter_001", "representative_frame": "missing.jpg", "referenced_shots": ["shot_001"]},
    )
    write_jsonl(
        tmp_path / "shot_understanding" / "shot_analysis.jsonl",
        [{"shot_id": "shot_001", "recommended_display_frame": "shot_split/keyframes/shot_001.jpg"}],
    )
    write_jsonl(tmp_path / "shot_package" / "shot_packages.jsonl", [{"shot_id": "shot_001", "frames": []}])

    repairs = _repair_chapter_images(tmp_path)

    chapter = read_json(tmp_path / "chapter_write" / "chapter_001.json")
    assert repairs[0]["status"] == "fixed"
    assert chapter["representative_frame"] == "shot_split/keyframes/shot_001.jpg"


def test_check_chapter_subsection_body_requires_all_headings(tmp_path) -> None:
    (tmp_path / "outline_plan").mkdir()
    (tmp_path / "chapter_write").mkdir()
    atomic_write_json(
        tmp_path / "outline_plan" / "outline.json",
        {
            "title": "报告",
            "description": "",
            "chapters": [
                {
                    "chapter_id": "chapter_001",
                    "title": "章节",
                    "shot_ids": ["shot_001", "shot_002"],
                    "subsections": [
                        {"subsection_id": "chapter_001_sub_001", "title": "入口", "shot_ids": ["shot_001"]},
                        {"subsection_id": "chapter_001_sub_002", "title": "清理", "shot_ids": ["shot_002"]},
                    ],
                }
            ],
        },
    )
    atomic_write_json(
        tmp_path / "chapter_write" / "chapters_index.json",
        {"chapters": [{"chapter_id": "chapter_001", "path": "chapter_write/chapter_001.json"}]},
    )
    atomic_write_json(
        tmp_path / "chapter_write" / "chapter_001.json",
        {"chapter_id": "chapter_001", "body_markdown": "## 入口\n\n只有入口正文。", "warnings": []},
    )

    check = _check_chapter_subsection_body(tmp_path)[0]

    assert check["status"] == "failed"
    assert check["missing"] == [{"chapter_id": "chapter_001", "title": "清理"}]


def test_check_chapter_write_warnings_reports_oversized_subsection_as_warning(tmp_path) -> None:
    (tmp_path / "chapter_write").mkdir()
    atomic_write_json(
        tmp_path / "chapter_write" / "chapters_index.json",
        {"chapters": [{"chapter_id": "chapter_001", "path": "chapter_write/chapter_001.json"}]},
    )
    atomic_write_json(
        tmp_path / "chapter_write" / "chapter_001.json",
        {
            "chapter_id": "chapter_001",
            "body_markdown": "正文",
            "warnings": ["oversized_subsection:chapter_001_sub_003"],
        },
    )

    check = _check_chapter_write_warnings(tmp_path)[0]

    assert check["status"] == "warning"
    assert check["oversized_subsections"][0]["chapter_id"] == "chapter_001"
