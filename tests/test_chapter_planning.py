from __future__ import annotations

from video2visualpage.models.adapter import _parse_outline_planner_tags
from video2visualpage.stages.outline_plan import _desired_chapter_count, run_outline_plan
from video2visualpage.stages.summary_reduce import _suggested_chapter_count
from video2visualpage.storage import atomic_write_json, read_json, read_jsonl, write_jsonl


def _card(index: int, tag: str = "topic") -> dict[str, object]:
    return {
        "shot_id": f"shot_{index:03d}",
        "start_sec": float(index * 10),
        "end_sec": float(index * 10 + 8),
        "merged_summary": f"{tag} summary {index}",
        "topic_tags": [tag],
        "key_entities": [tag],
        "narrative_role": "explanation",
        "importance_score": 0.9 if index in {1, 4} else 0.5,
    }


def _write_outline_project(tmp_path, cards, global_summary, chunk_summaries, config=None):
    (tmp_path / "init").mkdir()
    (tmp_path / "shot_understanding").mkdir()
    (tmp_path / "summary_reduce").mkdir()
    atomic_write_json(
        tmp_path / "init" / "config.json",
        config
        or {
            "llm": {"provider": "local_heuristic", "chapter_count": "auto", "use_env": False},
            "copywriting_model": {"provider": "local_heuristic"},
            "chapter_subsections": {"enabled": False},
        },
    )
    write_jsonl(tmp_path / "shot_understanding" / "shot_analysis.jsonl", cards)
    write_jsonl(tmp_path / "summary_reduce" / "chunk_summaries.jsonl", chunk_summaries)
    atomic_write_json(tmp_path / "summary_reduce" / "global_summary.json", global_summary)


def test_summary_reduce_prefers_meaningful_topics_for_auto_chapter_count() -> None:
    cards = [{"shot_id": f"shot_{index:03d}"} for index in range(1, 24)]
    topics = [
        "AI编程上下文失忆问题",
        "Claude Code 源码拆解",
        "四层记忆压缩机制",
        "会话记忆结构化提取",
    ]

    assert _suggested_chapter_count(cards, topics, chunk_size=40) == 4


def test_outline_plan_uses_sections_when_suggested_count_is_too_low() -> None:
    config = {"llm": {"chapter_count": "auto"}}
    global_summary = {
        "suggested_chapter_count": 1,
        "main_sections": [
            "AI编程上下文失忆问题",
            "Claude Code 源码拆解",
            "四层记忆压缩机制",
            "会话记忆结构化提取",
        ],
    }

    assert _desired_chapter_count(config, global_summary, shot_count=23) == 4


def test_outline_plan_manual_chapter_count_still_wins() -> None:
    config = {"llm": {"chapter_count": 1}}
    global_summary = {
        "suggested_chapter_count": 4,
        "main_sections": ["问题", "原因", "方案", "总结"],
    }

    assert _desired_chapter_count(config, global_summary, shot_count=23) == 1


def test_outline_plan_uses_section_sources_for_chapter_boundaries(tmp_path) -> None:
    cards = [_card(index, "intro" if index <= 3 else "layer") for index in range(1, 7)]
    _write_outline_project(
        tmp_path,
        cards,
        {
            "video_main_theme": "Demo",
            "main_sections": ["Context overview", "Layer one"],
            "suggested_chapter_count": 2,
            "section_sources": [
                {"title": "Context overview", "source_chunks": ["chunk_001"]},
                {"title": "Layer one", "source_chunks": ["chunk_002", "chunk_003"]},
            ],
        },
        [
            {"chunk_id": "chunk_001", "shot_range": ["shot_001", "shot_003"], "summary": "intro"},
            {"chunk_id": "chunk_002", "shot_range": ["shot_004", "shot_005"], "summary": "layer a"},
            {"chunk_id": "chunk_003", "shot_range": ["shot_006", "shot_006"], "summary": "layer b"},
        ],
    )

    result = run_outline_plan(tmp_path)

    outline = read_json(tmp_path / "outline_plan" / "outline.json")
    decisions = read_jsonl(tmp_path / "outline_plan" / "chapter_boundary_decisions.jsonl")
    assert result["primary_strategy"] == "section_sources"
    assert outline["chapters"][0]["shot_ids"] == ["shot_001", "shot_002", "shot_003"]
    assert outline["chapters"][1]["shot_ids"] == ["shot_004", "shot_005", "shot_006"]
    assert decisions[0]["strategy"] == "section_sources"
    assert decisions[0]["source_chunks"] == ["chunk_001"]


def test_outline_plan_maps_time_range_chunks_to_shots(tmp_path) -> None:
    cards = [_card(index, "intro" if index <= 2 else "demo") for index in range(1, 5)]
    _write_outline_project(
        tmp_path,
        cards,
        {
            "video_main_theme": "Demo",
            "main_sections": ["Intro", "Demo"],
            "suggested_chapter_count": 2,
            "section_sources": [
                {"title": "Intro", "source_chunks": ["chunk_001"]},
                {"title": "Demo", "source_chunks": ["chunk_002"]},
            ],
        },
        [
            {"chunk_id": "chunk_001", "shot_range": "10.0 - 28.0", "summary": "intro"},
            {"chunk_id": "chunk_002", "shot_range": "30.0 - 48.0", "summary": "demo"},
        ],
    )

    run_outline_plan(tmp_path)

    outline = read_json(tmp_path / "outline_plan" / "outline.json")
    assert outline["chapters"][0]["shot_ids"] == ["shot_001", "shot_002"]
    assert outline["chapters"][1]["shot_ids"] == ["shot_003", "shot_004"]


def test_outline_planner_tags_parse_chapters_and_summaries() -> None:
    raw = """
<TITLE>Demo outline</TITLE>
<DESCRIPTION>Demo description</DESCRIPTION>
<CHAPTER shots="shot_001-shot_003">Context</CHAPTER>
<SUMMARY>Context summary</SUMMARY>
<CHAPTER shots="shot_004,shot_005">Details</CHAPTER>
<SUMMARY>Details summary</SUMMARY>
"""

    result = _parse_outline_planner_tags(raw, [f"shot_{index:03d}" for index in range(1, 6)], max_chapters=4)

    assert result["title"] == "Demo outline"
    assert result["chapters"][0] == {
        "title": "Context",
        "summary": "Context summary",
        "shot_ids": ["shot_001", "shot_002", "shot_003"],
    }
    assert result["chapters"][1]["shot_ids"] == ["shot_004", "shot_005"]


def test_outline_plan_writes_structure_risk_file(tmp_path) -> None:
    cards = [_card(index, "topic") for index in range(1, 5)]
    _write_outline_project(
        tmp_path,
        cards,
        {
            "video_main_theme": "Demo",
            "main_sections": ["Only"],
            "suggested_chapter_count": 1,
            "section_sources": [{"title": "Only", "source_chunks": ["chunk_001"]}],
        },
        [{"chunk_id": "chunk_001", "shot_range": ["shot_001", "shot_004"], "summary": "all"}],
    )

    run_outline_plan(tmp_path)

    structure_qa = read_json(tmp_path / "outline_plan" / "outline_structure_qa.json")
    assert structure_qa["risk_level"] == "low"
    assert structure_qa["agent_called"] is False
    assert structure_qa["chapter_metrics"][0]["shot_count"] == 4
