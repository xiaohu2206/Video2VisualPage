from __future__ import annotations

from video2visualpage.stages.chapter_write import _batch_subsection_units, run_chapter_write
from video2visualpage.storage import atomic_write_json, read_json, read_jsonl, write_jsonl


def _unit(name: str, count: int) -> dict[str, object]:
    return {"subsection_id": name, "title": name, "shot_ids": [f"{name}_shot_{index}" for index in range(count)]}


def _card(index: int) -> dict[str, object]:
    return {
        "shot_id": f"shot_{index:03d}",
        "merged_summary": f"镜头 {index} 的说明内容",
        "importance_score": 0.5,
        "warnings": [],
        "recommended_display_frame": f"shot_split/keyframes/shot_{index:03d}.jpg",
    }


def test_batch_subsection_units_limits_only_total_shots() -> None:
    units = [_unit("sub_001", 1), _unit("sub_002", 1), _unit("sub_003", 1), _unit("sub_004", 1), _unit("sub_005", 1)]

    batches = _batch_subsection_units(units, max_shots_per_call=10)

    assert [[unit["subsection_id"] for unit in batch] for batch in batches] == [
        ["sub_001", "sub_002", "sub_003", "sub_004", "sub_005"]
    ]


def test_batch_subsection_units_keeps_oversized_subsection_whole() -> None:
    units = [_unit("sub_001", 2), _unit("sub_002", 5), _unit("sub_003", 2)]

    batches = _batch_subsection_units(units, max_shots_per_call=4)

    assert [[unit["subsection_id"] for unit in batch] for batch in batches] == [["sub_001"], ["sub_002"], ["sub_003"]]


def test_run_chapter_write_batches_subsections_and_assembles_chapter(tmp_path) -> None:
    (tmp_path / "init").mkdir()
    (tmp_path / "outline_plan").mkdir()
    (tmp_path / "shot_understanding").mkdir()
    (tmp_path / "summary_reduce").mkdir()
    atomic_write_json(
        tmp_path / "init" / "config.json",
        {
            "llm": {"provider": "local_heuristic", "use_env": False},
            "copywriting_model": {"provider": "local_heuristic"},
            "chapter_write": {"max_shots_per_call": 4},
        },
    )
    cards = [_card(index) for index in range(1, 10)]
    write_jsonl(tmp_path / "shot_understanding" / "shot_analysis.jsonl", cards)
    atomic_write_json(tmp_path / "summary_reduce" / "global_summary.json", {"video_main_theme": "上下文管理"})
    atomic_write_json(
        tmp_path / "outline_plan" / "outline.json",
        {
            "title": "报告",
            "description": "",
            "chapters": [
                {
                    "chapter_id": "chapter_001",
                    "title": "章节",
                    "summary": "章节摘要",
                    "shot_ids": [f"shot_{index:03d}" for index in range(1, 10)],
                    "representative_shot_id": "shot_001",
                    "subsections": [
                        {
                            "subsection_id": "chapter_001_sub_001",
                            "title": "入口",
                            "shot_ids": ["shot_001", "shot_002"],
                            "representative_shot_id": "shot_001",
                        },
                        {
                            "subsection_id": "chapter_001_sub_002",
                            "title": "清理",
                            "shot_ids": ["shot_003", "shot_004"],
                            "representative_shot_id": "shot_003",
                        },
                        {
                            "subsection_id": "chapter_001_sub_003",
                            "title": "超大小节",
                            "shot_ids": ["shot_005", "shot_006", "shot_007", "shot_008", "shot_009"],
                            "representative_shot_id": "shot_005",
                        },
                    ],
                }
            ],
            "warnings": [],
        },
    )

    result = run_chapter_write(tmp_path)

    chapter = read_json(tmp_path / "chapter_write" / "chapter_001.json")
    batches = read_jsonl(tmp_path / "chapter_write" / "subsection_write_batches.jsonl")
    assert result["subsection_batch_count"] == 2
    assert chapter["write_batch_count"] == 2
    assert "## 入口" in chapter["body_markdown"]
    assert chapter["body_markdown"].index("## 入口") < chapter["body_markdown"].index("## 清理")
    assert chapter["body_markdown"].index("## 清理") < chapter["body_markdown"].index("## 超大小节")
    assert chapter["referenced_shots"] == [f"shot_{index:03d}" for index in range(1, 10)]
    assert "oversized_subsection:chapter_001_sub_003" in chapter["warnings"]
    assert [batch["subsection_ids"] for batch in batches] == [
        ["chapter_001_sub_001", "chapter_001_sub_002"],
        ["chapter_001_sub_003"],
    ]
    assert batches[1]["oversized_subsection_ids"] == ["chapter_001_sub_003"]
