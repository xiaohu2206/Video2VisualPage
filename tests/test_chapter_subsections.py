from __future__ import annotations

from video2visualpage.models.adapter import _parse_chapter_subsection_tags
from video2visualpage.stages.outline_plan import _decide_chapter_subsections, run_outline_plan
from video2visualpage.storage import atomic_write_json, read_json, read_jsonl, write_jsonl


def _card(index: int, tag: str, *, importance: float = 0.5) -> dict[str, object]:
    return {
        "shot_id": f"shot_{index:03d}",
        "start_sec": float(index * 10),
        "end_sec": float(index * 10 + 8),
        "merged_summary": f"{tag} 的说明内容",
        "topic_tags": [tag],
        "key_entities": [tag],
        "narrative_role": "explanation",
        "importance_score": importance,
        "recommended_display_frame": f"shot_split/keyframes/shot_{index:03d}.jpg",
    }


def test_short_chapter_does_not_trigger_subsections() -> None:
    chapter = {"chapter_id": "chapter_001", "title": "具体主题", "shot_ids": ["shot_001", "shot_002"]}
    cards = [_card(1, "缓存"), _card(2, "缓存")]

    decision = _decide_chapter_subsections(
        chapter,
        cards,
        {"llm": {"provider": "local_heuristic"}},
        total_shot_count=len(cards),
        chapter_count=1,
    )

    assert decision["mode"] == "keep"
    assert decision["reason_codes"] == ["few_shots"]


def test_dense_multi_topic_chapter_triggers_subsections() -> None:
    tags = ["问题背景", "问题背景", "源码入口", "源码入口", "缓存机制", "缓存机制", "线程隔离", "线程隔离"]
    cards = [_card(index, tag, importance=0.75 if index in {2, 6} else 0.5) for index, tag in enumerate(tags, start=1)]
    chapter = {"chapter_id": "chapter_001", "title": "内容概览", "shot_ids": [card["shot_id"] for card in cards]}

    decision = _decide_chapter_subsections(
        chapter,
        cards,
        {"llm": {"provider": "local_heuristic"}},
        total_shot_count=len(cards),
        chapter_count=1,
    )

    assert decision["mode"] == "split"
    assert decision["need_score"] >= 5
    assert "many_topics" in decision["reason_codes"]


def test_parse_chapter_subsection_tags_expands_ranges_and_keep() -> None:
    keep = _parse_chapter_subsection_tags("<KEEP/>", ["shot_001", "shot_002"])
    assert keep["mode"] == "keep"

    parsed = _parse_chapter_subsection_tags(
        """
<SUB shots="shot_001-shot_003">问题背景</SUB>
<SUB shots="shot_004,shot_005">解决方案</SUB>
""",
        ["shot_001", "shot_002", "shot_003", "shot_004", "shot_005"],
    )

    assert parsed["mode"] == "split"
    assert parsed["subsections"][0]["shot_ids"] == ["shot_001", "shot_002", "shot_003"]
    assert parsed["subsections"][1]["shot_ids"] == ["shot_004", "shot_005"]


def test_outline_plan_writes_subsections_for_dense_chapter(tmp_path) -> None:
    (tmp_path / "init").mkdir()
    (tmp_path / "shot_understanding").mkdir()
    (tmp_path / "summary_reduce").mkdir()
    atomic_write_json(
        tmp_path / "init" / "config.json",
        {
            "llm": {"provider": "local_heuristic", "chapter_count": 1, "use_env": False},
            "copywriting_model": {"provider": "local_heuristic"},
            "chapter_subsections": {"enabled": True, "min_shots_for_model": 8, "min_need_score": 5},
        },
    )
    tags = ["问题背景", "问题背景", "源码入口", "源码入口", "缓存机制", "缓存机制", "线程隔离", "线程隔离"]
    cards = [_card(index, tag, importance=0.8 if index in {2, 6} else 0.5) for index, tag in enumerate(tags, start=1)]
    write_jsonl(tmp_path / "shot_understanding" / "shot_analysis.jsonl", cards)
    atomic_write_json(
        tmp_path / "summary_reduce" / "global_summary.json",
        {
            "video_main_theme": "上下文管理",
            "main_sections": ["上下文管理"],
            "suggested_chapter_count": 1,
            "narrative_style": "structured_report",
            "important_shots": ["shot_002", "shot_006"],
        },
    )

    result = run_outline_plan(tmp_path)

    outline = read_json(tmp_path / "outline_plan" / "outline.json")
    decisions = read_jsonl(tmp_path / "outline_plan" / "subsection_decisions.jsonl")
    chapter = outline["chapters"][0]
    assert result["subsection_chapter_count"] == 1
    assert len(chapter["subsections"]) >= 2
    assert chapter["subsections"][0]["subsection_id"] == "chapter_001_sub_001"
    assert chapter["subsections"][0]["representative_shot_id"] in chapter["subsections"][0]["shot_ids"]
    assert decisions[0]["mode"] == "split"
