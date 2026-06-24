from __future__ import annotations

from video2visualpage.stages.qa import _repair_chapter_images, _repair_json_text
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
