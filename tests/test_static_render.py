from __future__ import annotations

from video2visualpage.stages.static_render import _render_gallery, _render_html, _render_markdown


def test_render_markdown_headings_and_emphasis() -> None:
    markdown = """## 现象：AI 突然失忆

在跟 AI 聊代码、做项目的过程中，AI 就突然“失忆”了。

## 本质原因：脑容量限制

这种上下文失忆现象，**并不是因为 LLM（大语言模型）太笨**，而是受限于它的 **“脑容量”**。
"""

    rendered = _render_markdown(markdown)

    assert "<h2>现象：AI 突然失忆</h2>" in rendered
    assert "<h2>本质原因：脑容量限制</h2>" in rendered
    assert "<strong>并不是因为 LLM（大语言模型）太笨</strong>" in rendered
    assert "## 现象" not in rendered
    assert "**并不是" not in rendered


def test_render_markdown_lists_code_and_escapes_html() -> None:
    markdown = """- 使用 `context`
- 保留 **关键事实**

<script>alert("x")</script>
"""

    rendered = _render_markdown(markdown)

    assert "<ul><li>使用 <code>context</code></li><li>保留 <strong>关键事实</strong></li></ul>" in rendered
    assert "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;" in rendered
    assert "<script>" not in rendered


def test_render_html_uses_all_chapter_keyframes(tmp_path) -> None:
    project_dir = tmp_path / "project"
    output_dir = project_dir / "static_render"
    frame_dir = project_dir / "shot_split" / "keyframes"
    frame_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)
    for name in ["shot_001_a.jpg", "shot_001_b.jpg", "shot_002_a.jpg"]:
        (frame_dir / name).write_bytes(b"fake-image")

    outline = {
        "title": "视频博客笔记",
        "description": "",
        "chapters": [
            {
                "chapter_id": "chapter_001",
                "title": "第一章",
                "shot_ids": ["shot_001", "shot_002"],
            }
        ],
    }
    chapters = [
        {
            "chapter_id": "chapter_001",
            "title": "第一章",
            "body_markdown": "## 小节",
            "key_points": ["重点"],
            "referenced_shots": ["shot_001"],
        }
    ]
    analysis_by_id = {
        "shot_001": {
            "shot_id": "shot_001",
            "start_sec": 0,
            "recommended_display_frame": "shot_split/keyframes/shot_001_b.jpg",
        },
        "shot_002": {
            "shot_id": "shot_002",
            "start_sec": 8,
            "recommended_display_frame": "shot_split/keyframes/shot_002_a.jpg",
        },
    }
    packages_by_id = {
        "shot_001": {
            "shot_id": "shot_001",
            "frames": ["shot_split/keyframes/shot_001_a.jpg", "shot_split/keyframes/shot_001_b.jpg"],
        },
        "shot_002": {
            "shot_id": "shot_002",
            "frames": ["shot_split/keyframes/shot_002_a.jpg"],
        },
    }

    rendered = _render_html(project_dir, output_dir, outline, chapters, True, analysis_by_id, packages_by_id)

    assert "视觉证据" in rendered
    assert "3 张" in rendered
    assert "assets/images/shot_001_a.jpg" in rendered
    assert "assets/images/shot_001_b.jpg" in rendered
    assert "assets/images/shot_002_a.jpg" in rendered
    assert rendered.count("<img ") == 3


def test_render_gallery_collapses_extra_keyframes() -> None:
    images = [
        {
            "src": f"assets/images/shot_{index:03d}.jpg",
            "label": f"shot_{index:03d}",
            "alt": f"shot {index:03d}",
        }
        for index in range(1, 11)
    ]

    rendered = _render_gallery(images, "章节")

    assert "视觉证据" in rendered
    assert "10 张" in rendered
    assert "frame-count" in rendered
    assert "frame-more" in rendered
    assert "frame-drawer" in rendered
    assert "还有 3 张关键帧" in rendered
    assert "assets/images/shot_010.jpg" in rendered
    assert rendered.count("<img ") == 10
