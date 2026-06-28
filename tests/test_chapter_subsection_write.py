from __future__ import annotations

import pytest

from video2visualpage.models import LocalModelAdapter
from video2visualpage.models.adapter import _parse_chapter_subsection_write_tags


def _cards() -> list[dict[str, object]]:
    return [
        {
            "shot_id": "shot_001",
            "merged_summary": "第一部分说明上下文缓存。",
            "recommended_display_frame": "shot_split/keyframes/shot_001.jpg",
        },
        {
            "shot_id": "shot_002",
            "merged_summary": "第二部分说明规则清理。",
            "recommended_display_frame": "shot_split/keyframes/shot_002.jpg",
        },
    ]


def test_parse_chapter_subsection_write_tags_keeps_subsection_boundaries() -> None:
    subsections = [
        {"subsection_id": "chapter_001_sub_001", "title": "入口", "shot_ids": ["shot_001"]},
        {"subsection_id": "chapter_001_sub_002", "title": "清理", "shot_ids": ["shot_002"]},
    ]
    raw = """
<SUBSECTION id="chapter_001_sub_001">
<BODY_MARKDOWN>
缓存入口先收集上下文。
</BODY_MARKDOWN>
<KEY_POINTS>
- 收集上下文
</KEY_POINTS>
<REFERENCED_SHOTS>
- shot_001
- shot_999
</REFERENCED_SHOTS>
<REPRESENTATIVE_FRAME>
shot_split/keyframes/shot_001.jpg
</REPRESENTATIVE_FRAME>
</SUBSECTION>

<SUBSECTION id="chapter_001_sub_002">
<BODY_MARKDOWN>
规则清理会移除低价值内容。
</BODY_MARKDOWN>
<KEY_POINTS>
- 清理低价值内容
</KEY_POINTS>
<REFERENCED_SHOTS>
- shot_002
</REFERENCED_SHOTS>
<REPRESENTATIVE_FRAME>
shot_split/keyframes/shot_002.jpg
</REPRESENTATIVE_FRAME>
</SUBSECTION>
"""

    result = _parse_chapter_subsection_write_tags(raw, subsections, _cards(), {"video_main_theme": "上下文管理"})

    assert [item["subsection_id"] for item in result["subsections"]] == ["chapter_001_sub_001", "chapter_001_sub_002"]
    assert result["subsections"][0]["referenced_shots"] == ["shot_001"]
    assert result["subsections"][1]["referenced_shots"] == ["shot_002"]
    assert "chapter_001_sub_001:invalid_referenced_shots_removed:shot_999" in result["warnings"]


def test_parse_chapter_subsection_write_tags_requires_every_subsection() -> None:
    subsections = [
        {"subsection_id": "chapter_001_sub_001", "title": "入口", "shot_ids": ["shot_001"]},
        {"subsection_id": "chapter_001_sub_002", "title": "清理", "shot_ids": ["shot_002"]},
    ]
    with pytest.raises(ValueError, match="chapter_001_sub_002"):
        _parse_chapter_subsection_write_tags(
            """
<SUBSECTION id="chapter_001_sub_001">
<BODY_MARKDOWN>只有一个小节。</BODY_MARKDOWN>
</SUBSECTION>
""",
            subsections,
            _cards(),
            {},
        )


def test_write_chapter_subsections_uses_tagged_text_output(tmp_path, monkeypatch) -> None:
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
    chapter = {"chapter_id": "chapter_001", "title": "上下文缓存", "shot_ids": ["shot_001", "shot_002"]}
    subsections = [
        {"subsection_id": "chapter_001_sub_001", "title": "入口", "shot_ids": ["shot_001"]},
        {"subsection_id": "chapter_001_sub_002", "title": "清理", "shot_ids": ["shot_002"]},
    ]

    def fake_chat_text(stage, system_prompt, payload, images=None):
        assert stage == "chapter_write"
        assert "SUBSECTION" in system_prompt
        assert images is None
        assert [item["subsection_id"] for item in payload["subsections"]] == ["chapter_001_sub_001", "chapter_001_sub_002"]
        return """
<SUBSECTION id="chapter_001_sub_001">
<BODY_MARKDOWN>入口正文。</BODY_MARKDOWN>
<KEY_POINTS>- 入口</KEY_POINTS>
<REFERENCED_SHOTS>- shot_001</REFERENCED_SHOTS>
</SUBSECTION>
<SUBSECTION id="chapter_001_sub_002">
<BODY_MARKDOWN>清理正文。</BODY_MARKDOWN>
<KEY_POINTS>- 清理</KEY_POINTS>
<REFERENCED_SHOTS>- shot_002</REFERENCED_SHOTS>
</SUBSECTION>
"""

    monkeypatch.setattr(adapter, "_chat_text", fake_chat_text)

    result = adapter.write_chapter_subsections(chapter, subsections, _cards(), {"video_main_theme": "上下文管理"})

    assert [item["body_markdown"] for item in result["subsections"]] == ["入口正文。", "清理正文。"]

