from __future__ import annotations

from video2visualpage.stages.outline_plan import _desired_chapter_count
from video2visualpage.stages.summary_reduce import _suggested_chapter_count


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
