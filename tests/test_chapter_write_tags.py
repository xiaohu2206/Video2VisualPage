from __future__ import annotations

import pytest

from video2visualpage.models import LocalModelAdapter
from video2visualpage.models.adapter import _parse_chapter_write_tags


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


def test_parse_chapter_write_tags_preserves_contract_and_validates_refs() -> None:
    chapter = {
        "chapter_id": "chapter_001",
        "title": "上下文缓存",
        "summary": "说明缓存和规则清理。",
        "shot_ids": ["shot_001", "shot_002"],
    }
    raw = """
<CHAPTER_ID>
wrong_id
</CHAPTER_ID>

<TITLE>
上下文缓存机制
</TITLE>

<REPRESENTATIVE_FRAME>
shot_002.jpg
</REPRESENTATIVE_FRAME>

<BODY_MARKDOWN>
## 缓存入口

上下文缓存需要先做规则清理，再进入后续压缩流程。
</BODY_MARKDOWN>

<KEY_POINTS>
- 先清理规则
- 再保护缓存
</KEY_POINTS>

<REFERENCED_SHOTS>
- shot_001-shot_002
- shot_999
</REFERENCED_SHOTS>
"""

    result = _parse_chapter_write_tags(raw, chapter, _cards(), {"video_main_theme": "上下文管理"})

    assert result["chapter_id"] == "chapter_001"
    assert result["title"] == "上下文缓存机制"
    assert result["representative_frame"] == "shot_split/keyframes/shot_002.jpg"
    assert "规则清理" in result["body_markdown"]
    assert result["key_points"] == ["先清理规则", "再保护缓存"]
    assert result["referenced_shots"] == ["shot_001", "shot_002"]
    assert result["global_theme"] == "上下文管理"
    assert result["model_output_format"] == "tagged_chapter_v1"
    assert "chapter_id_mismatch_ignored:wrong_id" in result["warnings"]
    assert "invalid_referenced_shots_removed:shot_999" in result["warnings"]


def test_write_chapter_uses_tagged_text_output(tmp_path, monkeypatch) -> None:
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
    chapter = {
        "chapter_id": "chapter_001",
        "title": "上下文缓存",
        "shot_ids": ["shot_001", "shot_002"],
    }

    def fail_chat_json(*args, **kwargs):
        raise AssertionError("chapter_write should use tagged text output, not JSON mode")

    def fake_chat_text(stage, system_prompt, payload, images=None):
        assert stage == "chapter_write"
        assert "不要输出 JSON" in system_prompt
        assert "<BODY_MARKDOWN>" in system_prompt
        assert images is None
        assert payload["chapter"]["chapter_id"] == "chapter_001"
        return """
<CHAPTER_ID>chapter_001</CHAPTER_ID>
<TITLE>上下文缓存</TITLE>
<REPRESENTATIVE_FRAME>shot_split/keyframes/shot_001.jpg</REPRESENTATIVE_FRAME>
<BODY_MARKDOWN>
上下文缓存的关键是先清理规则，再减少重复信息。
</BODY_MARKDOWN>
<KEY_POINTS>
- 清理规则
- 减少重复信息
</KEY_POINTS>
<REFERENCED_SHOTS>
- shot_001
- shot_002
</REFERENCED_SHOTS>
"""

    monkeypatch.setattr(adapter, "_chat_json", fail_chat_json)
    monkeypatch.setattr(adapter, "_chat_text", fake_chat_text)

    result = adapter.write_chapter(chapter, _cards(), {"video_main_theme": "上下文管理"})

    assert result["chapter_id"] == "chapter_001"
    assert result["body_markdown"].startswith("上下文缓存")
    assert result["key_points"] == ["清理规则", "减少重复信息"]
    assert result["referenced_shots"] == ["shot_001", "shot_002"]


def test_parse_chapter_write_tags_requires_body_markdown() -> None:
    with pytest.raises(ValueError, match="BODY_MARKDOWN"):
        _parse_chapter_write_tags(
            "<TITLE>缺少正文</TITLE>",
            {"chapter_id": "chapter_001", "title": "缺少正文", "shot_ids": ["shot_001"]},
            _cards(),
            {},
        )
