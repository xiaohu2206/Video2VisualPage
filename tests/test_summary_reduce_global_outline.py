from __future__ import annotations

import video2visualpage.stages.summary_reduce as summary_reduce_module
from video2visualpage.models.adapter import LocalModelAdapter, _parse_global_outline_tags
from video2visualpage.stages.summary_reduce import _build_global_summary, _chunked
from video2visualpage.storage import atomic_write_json, read_json, read_jsonl, write_jsonl


def test_chunked_balances_shots_around_target_size() -> None:
    cards = [{"shot_id": f"shot_{index:03d}"} for index in range(1, 42)]

    chunks = _chunked(cards, 40)

    assert [len(chunk) for chunk in chunks] == [21, 20]
    assert chunks[0][0]["shot_id"] == "shot_001"
    assert chunks[0][-1]["shot_id"] == "shot_021"
    assert chunks[1][0]["shot_id"] == "shot_022"
    assert chunks[1][-1]["shot_id"] == "shot_041"


def test_chunked_distributes_all_shots_evenly_across_computed_chunks() -> None:
    cards = [{"shot_id": f"shot_{index:03d}"} for index in range(1, 82)]

    chunks = _chunked(cards, 40)

    assert [len(chunk) for chunk in chunks] == [27, 27, 27]


def test_parse_global_outline_tags_keeps_order_and_filters_invalid_chunks() -> None:
    raw = """
extra text should be ignored
<THEME>AI上下文管理</THEME>
<STYLE>structured_report</STYLE>
<SECTION chunks="chunk_001">一、问题背景与源码入口</SECTION>
<SECTION chunks="chunk_001,chunk_999">四层压缩与缓存保护</SECTION>
<SECTION chunks="chunk_002">四层压缩与缓存保护</SECTION>
"""

    result = _parse_global_outline_tags(raw, ["chunk_001", "chunk_002"])

    assert result["video_main_theme"] == "AI上下文管理"
    assert result["narrative_style"] == "structured_report"
    assert result["sections"] == [
        {"title": "问题背景与源码入口", "source_chunks": ["chunk_001"]},
        {"title": "四层压缩与缓存保护", "source_chunks": ["chunk_001"]},
    ]
    assert "invalid_section_chunks_removed:chunk_999" in result["warnings"]
    assert "duplicate_section_removed:四层压缩与缓存保护" in result["warnings"]


def test_model_adapter_global_outline_uses_tagged_text_output(tmp_path, monkeypatch) -> None:
    adapter = LocalModelAdapter(
        tmp_path,
        {
            "provider": "openai_compatible",
            "api_key": "test-key",
            "model": "copy-test",
            "use_env": False,
            "max_retries": 0,
        },
        model_role="copywriting",
    )

    def fake_chat_text(stage, system_prompt, payload, images=None):
        assert stage == "global_outline"
        assert "不要输出 JSON" in system_prompt
        assert images is None
        assert payload["chunks"] == [
            {
                "chunk_id": "chunk_001",
                "shot_range": "shot_001-shot_040",
                "main_topics": ["上下文丢失", "源码拆解"],
                "summary": "解释上下文丢失问题。",
            }
        ]
        return """
<THEME>AI上下文管理</THEME>
<SECTION chunks="chunk_001">问题背景与源码入口</SECTION>
"""

    monkeypatch.setattr(adapter, "_chat_text", fake_chat_text)

    result = adapter.summarize_global_outline(
        [
            {
                "chunk_id": "chunk_001",
                "shot_range": "shot_001-shot_040",
                "main_topics": ["上下文丢失", "源码拆解"],
                "summary": "解释上下文丢失问题。",
            }
        ]
    )

    assert result["video_main_theme"] == "AI上下文管理"
    assert result["sections"] == [{"title": "问题背景与源码入口", "source_chunks": ["chunk_001"]}]


def test_build_global_summary_uses_agent_sections_and_local_important_shots() -> None:
    cards = [{"shot_id": f"shot_{index:03d}"} for index in range(1, 10)]
    summaries = [
        {
            "chunk_id": "chunk_001",
            "main_topics": ["旧主题A", "旧主题B"],
            "important_shots": ["shot_001", "shot_003"],
        },
        {
            "chunk_id": "chunk_002",
            "main_topics": ["旧主题B", "旧主题C"],
            "important_shots": ["shot_003", "shot_008"],
        },
    ]
    outline = {
        "video_main_theme": "AI上下文管理",
        "narrative_style": "structured_report",
        "sections": [
            {"title": "问题背景与源码入口", "source_chunks": ["chunk_001"]},
            {"title": "缓存保护与清理策略", "source_chunks": ["chunk_002", "chunk_999"]},
        ],
        "warnings": ["invalid_section_chunks_removed:chunk_999"],
    }

    result = _build_global_summary(cards, summaries, 40, outline, [])

    assert result["video_main_theme"] == "AI上下文管理"
    assert result["main_sections"] == ["问题背景与源码入口", "缓存保护与清理策略"]
    assert result["suggested_chapter_count"] == 2
    assert result["important_shots"] == ["shot_001", "shot_003", "shot_008"]
    assert result["source_chunks"] == ["chunk_001", "chunk_002"]
    assert result["section_sources"] == [
        {"title": "问题背景与源码入口", "source_chunks": ["chunk_001"]},
        {"title": "缓存保护与清理策略", "source_chunks": ["chunk_002"]},
    ]
    assert result["warnings"] == ["invalid_section_chunks_removed:chunk_999"]


def test_build_global_summary_falls_back_to_topic_merge_when_agent_fails() -> None:
    cards = [{"shot_id": f"shot_{index:03d}"} for index in range(1, 12)]
    summaries = [
        {"chunk_id": "chunk_001", "main_topics": ["主题A", "主题B"], "important_shots": ["shot_001"]},
        {"chunk_id": "chunk_002", "main_topics": ["主题B", "主题C"], "important_shots": ["shot_002"]},
    ]

    result = _build_global_summary(cards, summaries, 40, None, ["global_outline_agent_failed:timeout"])

    assert result["video_main_theme"] == "主题A"
    assert result["main_sections"] == ["主题A", "主题B", "主题C"]
    assert result["important_shots"] == ["shot_001", "shot_002"]
    assert "global_outline_agent_failed:timeout" in result["warnings"]
    assert "global_outline_agent_fallback" in result["warnings"]


def test_run_summary_reduce_writes_agent_global_summary(tmp_path, monkeypatch) -> None:
    project_dir = tmp_path / "outputs" / "demo"
    (project_dir / "init").mkdir(parents=True)
    (project_dir / "shot_understanding").mkdir()
    atomic_write_json(project_dir / "init" / "config.json", {"llm": {"provider": "local_heuristic", "max_shots_per_chunk": 2}})
    write_jsonl(
        project_dir / "shot_understanding" / "shot_analysis.jsonl",
        [
            {"shot_id": "shot_001", "merged_summary": "a", "topic_tags": ["A"], "importance_score": 0.9},
            {"shot_id": "shot_002", "merged_summary": "b", "topic_tags": ["B"], "importance_score": 0.5},
            {"shot_id": "shot_003", "merged_summary": "c", "topic_tags": ["C"], "importance_score": 0.8},
        ],
    )
    calls: dict[str, object] = {}

    class FakeAdapter:
        def __init__(self, *args, **kwargs):
            pass

        def summarize_chunk(self, chunk_id, chunk):
            return {
                "chunk_id": chunk_id,
                "shot_range": [chunk[0]["shot_id"], chunk[-1]["shot_id"]],
                "main_topics": [f"旧主题-{chunk_id}"],
                "summary": f"{chunk_id} summary",
                "important_shots": [chunk[0]["shot_id"]],
            }

        def summarize_global_outline(self, summaries):
            calls["summaries"] = summaries
            return {
                "video_main_theme": "新的全局主题",
                "narrative_style": "structured_report",
                "sections": [
                    {"title": "归并后的第一部分", "source_chunks": ["chunk_001"]},
                    {"title": "归并后的第二部分", "source_chunks": ["chunk_002"]},
                ],
                "warnings": [],
            }

    monkeypatch.setattr(summary_reduce_module, "LocalModelAdapter", FakeAdapter)

    result = summary_reduce_module.run_summary_reduce(project_dir)

    assert result["chunk_count"] == 2
    assert [summary["chunk_id"] for summary in calls["summaries"]] == ["chunk_001", "chunk_002"]
    assert [summary["chunk_id"] for summary in read_jsonl(project_dir / "summary_reduce" / "chunk_summaries.jsonl")] == [
        "chunk_001",
        "chunk_002",
    ]
    global_summary = read_json(project_dir / "summary_reduce" / "global_summary.json")
    assert global_summary["video_main_theme"] == "新的全局主题"
    assert global_summary["main_sections"] == ["归并后的第一部分", "归并后的第二部分"]
    assert global_summary["suggested_chapter_count"] == 2
    assert global_summary["source_chunks"] == ["chunk_001", "chunk_002"]
