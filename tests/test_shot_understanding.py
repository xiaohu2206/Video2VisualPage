from __future__ import annotations

import sys
import time
import types

import pytest
from PIL import Image

from video2visualpage.models import LocalModelAdapter
import video2visualpage.models.adapter as model_adapter
from video2visualpage.models.adapter import effective_model_config, model_signature
from video2visualpage.pipeline import run_stage
from video2visualpage.stages import create_project
from video2visualpage.stages import shot_understanding
from video2visualpage.state import get_stage_record, load_run_state
from video2visualpage.storage import atomic_write_json, read_jsonl, write_jsonl


def _make_project_with_package(tmp_path):
    (tmp_path / "init").mkdir()
    atomic_write_json(tmp_path / "init" / "config.json", {"llm": {"provider": "local_heuristic", "use_env": False}})
    (tmp_path / "shot_package").mkdir()
    write_jsonl(
        tmp_path / "shot_package" / "shot_packages.jsonl",
        [
            {
                "shot_id": "shot_001",
                "time_range": {"start_sec": 0.0, "end_sec": 1.0, "duration_sec": 1.0},
                "frames": [],
                "subtitle_text": "hello",
            }
        ],
    )
    return tmp_path


def test_shot_understanding_raises_on_model_failure(tmp_path, monkeypatch) -> None:
    project_dir = _make_project_with_package(tmp_path)

    class FailingAdapter:
        def __init__(self, *args, **kwargs):
            pass

        def analyze_shot(self, package):
            raise RuntimeError("model_timeout")

    monkeypatch.setattr(shot_understanding, "LocalModelAdapter", FailingAdapter)

    with pytest.raises(RuntimeError, match="shot_understanding failed for shot_001: model_timeout"):
        shot_understanding.run_shot_understanding(project_dir)

    assert not (project_dir / "shot_understanding" / "shot_analysis.jsonl").exists()
    errors = read_jsonl(project_dir / "logs" / "errors.jsonl")
    assert errors[-1]["shot_id"] == "shot_001"
    assert "model_timeout" in errors[-1]["error"]


def test_shot_understanding_reports_model_progress_to_stderr(tmp_path, capsys) -> None:
    project_dir = _make_project_with_package(tmp_path)

    result = shot_understanding.run_shot_understanding(project_dir)

    captured = capsys.readouterr()
    assert result["shot_count"] == 1
    assert captured.out == ""
    assert "[progress] 06_shot_understanding" in captured.err
    assert "分析镜头 1/1: shot_001" in captured.err
    assert "运行本地分析 attempt 1: shot_001" in captured.err
    assert "100%" in captured.err


def test_shot_understanding_can_run_ocr_concurrently_preserving_order(tmp_path, monkeypatch) -> None:
    (tmp_path / "init").mkdir()
    atomic_write_json(
        tmp_path / "init" / "config.json",
        {
            "llm": {"provider": "local_heuristic", "use_env": False},
            "vision_model": {"ocr_concurrency": 2},
        },
    )
    (tmp_path / "shot_package").mkdir()
    write_jsonl(
        tmp_path / "shot_package" / "shot_packages.jsonl",
        [
            {"shot_id": "shot_001", "frames": [], "subtitle_text": "slow"},
            {"shot_id": "shot_002", "frames": [], "subtitle_text": "fast"},
            {"shot_id": "shot_003", "frames": [], "subtitle_text": "last"},
        ],
    )

    class FakeAdapter:
        def __init__(self, *args, **kwargs):
            pass

        def analyze_shot(self, package):
            if package["shot_id"] == "shot_001":
                time.sleep(0.03)
            return {"shot_id": package["shot_id"], "model_output_format": "fake"}

    monkeypatch.setattr(shot_understanding, "LocalModelAdapter", FakeAdapter)

    result = shot_understanding.run_shot_understanding(tmp_path)

    rows = read_jsonl(tmp_path / "shot_understanding" / "shot_analysis.jsonl")
    assert result["shot_count"] == 3
    assert [row["shot_id"] for row in rows] == ["shot_001", "shot_002", "shot_003"]


def test_model_adapter_accepts_tagged_markdown_shot_output(tmp_path, monkeypatch) -> None:
    adapter = LocalModelAdapter(
        tmp_path,
        {
            "provider": "openai_compatible",
            "api_key": "test-key",
            "model": "vision-test",
            "use_env": False,
            "max_retries": 0,
        },
        model_role="vision",
    )
    package = {
        "shot_id": "shot_001",
        "time_range": {"start_sec": 1.2, "end_sec": 3.4, "duration_sec": 2.2},
        "frames": ["shot_package/keyframes/shot_001_a.jpg", "shot_package/keyframes/shot_001_b.jpg"],
        "subtitle_text": "这里展示产品的核心能力。",
    }

    def fake_chat_text(stage, system_prompt, payload, images=None):
        assert stage == "shot_understanding"
        assert "不要返回 JSON" in system_prompt
        assert "只关注“画面中的文字/OCR 内容”和“字幕文本”" in system_prompt
        assert "<visual_summary>" in system_prompt
        assert payload["shot_id"] == "shot_001"
        assert images == package["frames"]
        return """
<visual_summary>
## 目录 CONTENTS
- [L1] 微压缩 (Microcompact)
  - 零成本规则清理，保护服务端缓存
- [L2] 会话记忆 (Session Memory)
  - 事实结构化提取，精细边界锁定
</visual_summary>

<subtitle_summary>
这里展示产品的核心能力。
</subtitle_summary>

<merged_summary>
## 目录 CONTENTS
- [L1] 微压缩 (Microcompact)：零成本规则清理，保护服务端缓存
- [L2] 会话记忆 (Session Memory)：事实结构化提取，精细边界锁定

产品核心能力围绕规则清理和会话记忆展开。
</merged_summary>

<key_entities>
- 微压缩
- 会话记忆
- Microcompact
- Session Memory
</key_entities>

<actions>
- 分层
- 定义
</actions>

<on_screen_text>
- 目录 CONTENTS
- [L1] 微压缩 (Microcompact)
- 零成本规则清理，保护服务端缓存
- [L2] 会话记忆 (Session Memory)
- 事实结构化提取，精细边界锁定
</on_screen_text>

<topic_tags>
- 产品能力
- 知识目录
</topic_tags>

<narrative_role>
explanation
</narrative_role>

<importance_score>
0.82
</importance_score>

<recommended_display_frame>
shot_001_b.jpg
</recommended_display_frame>

<confidence>
0.91
</confidence>

<warnings>
</warnings>
"""

    monkeypatch.setattr(adapter, "_chat_text", fake_chat_text)

    result = adapter.analyze_shot(package)

    assert result["shot_id"] == "shot_001"
    assert result["start_sec"] == 1.2
    assert "## 目录 CONTENTS" in result["visual_summary"]
    assert "[L1] 微压缩" in result["merged_summary"]
    assert result["key_entities"] == ["微压缩", "会话记忆", "Microcompact", "Session Memory"]
    assert result["actions"] == ["分层", "定义"]
    assert result["topic_tags"] == ["产品能力", "知识目录"]
    assert result["recommended_display_frame"] == "shot_package/keyframes/shot_001_b.jpg"
    assert result["importance_score"] == 0.82
    assert result["confidence"] == 0.91
    assert result["model_output_format"] == "tagged_markdown_v1"


def test_model_adapter_supports_ocr_model_output(tmp_path, monkeypatch) -> None:
    adapter = LocalModelAdapter(
        tmp_path,
        {
            "provider": "openai_compatible",
            "api_key": "test-key",
            "model": "qwen3.5-ocr",
            "use_env": False,
            "max_retries": 0,
            "max_images_per_shot": 2,
        },
        model_role="vision",
    )
    package = {
        "shot_id": "shot_004",
        "time_range": {"start_sec": 10.0, "end_sec": 12.0, "duration_sec": 2.0},
        "frames": ["shot_split/keyframes/shot_004_a.jpg", "shot_split/keyframes/shot_004_b.jpg"],
        "subtitle_text": "也就是上下文被撑爆了",
        "warnings": [],
    }
    calls: list[list[str] | None] = []

    def fake_chat_raw(stage, system_prompt, payload, *, images=None, response_instruction, json_response, preserve_json_keys):
        assert stage == "shot_understanding"
        assert "OCR" in system_prompt
        calls.append(images)
        if images == ["shot_split/keyframes/shot_004_a.jpg"]:
            return """
```json
[
  {"text": "LLM"},
  {"text": "不是模型太笨"},
  {"text": "而是脑容量"}
]
```
"""
        return """
```json
[
  {"text": "不是模型太笨"},
  {"text": "上下文"}
]
```
"""

    monkeypatch.setattr(adapter, "_chat_raw", fake_chat_raw)

    result = adapter.analyze_shot(package)

    assert calls == [["shot_split/keyframes/shot_004_a.jpg"], ["shot_split/keyframes/shot_004_b.jpg"]]
    assert result["model_output_format"] == "ocr_text_v1"
    assert result["on_screen_text"] == ["LLM", "不是模型太笨", "而是脑容量", "上下文"]
    assert "也就是上下文被撑爆了" in result["merged_summary"]
    assert result["recommended_display_frame"] == "shot_split/keyframes/shot_004_a.jpg"
    assert result["confidence"] == 0.85


def test_model_adapter_uses_local_paddleocr_provider(tmp_path, monkeypatch) -> None:
    ocr_root = tmp_path / "PaddleOCR-main"
    (ocr_root / "models" / "PP-OCRv6_medium_det").mkdir(parents=True)
    (ocr_root / "models" / "PP-OCRv6_medium_rec").mkdir(parents=True)
    frame = tmp_path / "shot_split" / "keyframes" / "shot_006_a.jpg"
    frame.parent.mkdir(parents=True)
    frame.write_bytes(b"fake-jpeg")
    init_calls: list[dict] = []
    predict_calls: list[str] = []

    class FakePaddleOCR:
        def __init__(self, **kwargs):
            init_calls.append(kwargs)

        def predict(self, image_path, **kwargs):
            predict_calls.append(image_path)
            assert kwargs["text_rec_score_thresh"] == 0.5
            return [{"res": {"rec_texts": ["知识内容", "低分噪声", "AI Agent"], "rec_scores": [0.98, 0.2, 0.87]}}]

    monkeypatch.setitem(sys.modules, "paddleocr", types.SimpleNamespace(PaddleOCR=FakePaddleOCR))
    adapter = LocalModelAdapter(
        tmp_path,
        {
            "provider": "paddleocr",
            "model": "PP-OCRv6_medium",
            "use_env": False,
            "max_retries": 0,
            "max_images_per_shot": 1,
            "ocr_root": str(ocr_root),
            "ocr_device": "gpu",
            "ocr_min_score": 0.5,
        },
        model_role="vision",
    )

    result = adapter.analyze_shot(
        {
            "shot_id": "shot_006",
            "time_range": {"start_sec": 4.0, "end_sec": 6.0, "duration_sec": 2.0},
            "frames": ["shot_split/keyframes/shot_006_a.jpg"],
            "subtitle_text": "这里讲的是知识内容。",
        }
    )

    assert init_calls[0]["device"] == "gpu"
    assert init_calls[0]["engine"] == "paddle"
    assert init_calls[0]["text_detection_model_dir"] == str(ocr_root / "models" / "PP-OCRv6_medium_det")
    assert init_calls[0]["text_recognition_model_dir"] == str(ocr_root / "models" / "PP-OCRv6_medium_rec")
    assert predict_calls == [str(frame)]
    assert result["model_output_format"] == "ocr_text_v1"
    assert result["on_screen_text"] == ["知识内容", "AI Agent"]
    assert result["recommended_display_frame"] == "shot_split/keyframes/shot_006_a.jpg"
    assert "这里讲的是知识内容。" in result["merged_summary"]


def test_paddleocr_provider_falls_back_to_crop_recognition_when_pipeline_reads_whole_frame(tmp_path, monkeypatch) -> None:
    ocr_root = tmp_path / "PaddleOCR-main"
    (ocr_root / "models" / "PP-OCRv6_medium_det").mkdir(parents=True)
    (ocr_root / "models" / "PP-OCRv6_medium_rec").mkdir(parents=True)
    frame = tmp_path / "shot_split" / "keyframes" / "shot_007_a.jpg"
    frame.parent.mkdir(parents=True)
    Image.new("RGB", (100, 50), "white").save(frame)
    recognizer_devices: list[str] = []

    class FakePaddleOCR:
        def __init__(self, **kwargs):
            pass

        def predict(self, image_path, **kwargs):
            return [
                {
                    "res": {
                        "rec_texts": ["乱码整图"],
                        "rec_scores": [0.99],
                        "rec_boxes": [[0, 0, 100, 50]],
                    }
                }
            ]

    class FakeTextRecognition:
        def __init__(self, **kwargs):
            self.device = kwargs["device"]
            self.calls = 0
            recognizer_devices.append(self.device)

        def predict(self, images):
            self.calls += 1
            if self.device.startswith("gpu"):
                return [{"res": {"rec_text": "非", "rec_score": 0.99}}]
            if self.calls == 1:
                return [{"res": {"rec_text": "测试OCR", "rec_score": 0.99}}]
            image_list = images if isinstance(images, list) else [images]
            return [{"res": {"rec_text": "知识内容", "rec_score": 0.98}} for _ in image_list]

    monkeypatch.setitem(
        sys.modules,
        "paddleocr",
        types.SimpleNamespace(PaddleOCR=FakePaddleOCR, TextRecognition=FakeTextRecognition),
    )
    monkeypatch.setattr(model_adapter, "_candidate_text_crop_boxes", lambda image_path: [(0, 0, 100, 50)])

    adapter = LocalModelAdapter(
        tmp_path,
        {
            "provider": "paddleocr",
            "model": "PP-OCRv6_medium",
            "use_env": False,
            "max_retries": 0,
            "max_images_per_shot": 1,
            "ocr_root": str(ocr_root),
            "ocr_device": "gpu",
            "ocr_min_score": 0.6,
        },
        model_role="vision",
    )

    result = adapter.analyze_shot(
        {
            "shot_id": "shot_007",
            "time_range": {"start_sec": 0.0, "end_sec": 1.0, "duration_sec": 1.0},
            "frames": ["shot_split/keyframes/shot_007_a.jpg"],
            "subtitle_text": "",
        }
    )

    assert recognizer_devices == ["gpu", "cpu"]
    assert result["on_screen_text"] == ["知识内容"]
    assert any("ocr_device_fallback:gpu->cpu" in warning for warning in result["warnings"])
    assert any("ocr_crop_fallback:shot_007_a.jpg" in warning for warning in result["warnings"])


def test_pipeline_marks_shot_understanding_failed_on_model_failure(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("VIDEO2VISUALPAGE_LLM_PROVIDER", "local_heuristic")
    monkeypatch.setenv("VIDEO2VISUALPAGE_VISION_MODEL_PROVIDER", "local_heuristic")
    video = tmp_path / "demo.mp4"
    video.write_bytes(b"fake-video")
    project_dir = create_project(video, project_name="demo", output_root=tmp_path / "outputs")
    (project_dir / "shot_package").mkdir()
    write_jsonl(
        project_dir / "shot_package" / "shot_packages.jsonl",
        [
            {
                "shot_id": "shot_001",
                "time_range": {"start_sec": 0.0, "end_sec": 1.0, "duration_sec": 1.0},
                "frames": [],
                "subtitle_text": "hello",
            }
        ],
    )

    class FailingAdapter:
        def __init__(self, *args, **kwargs):
            pass

        def analyze_shot(self, package):
            raise RuntimeError("model_timeout")

    monkeypatch.setattr(shot_understanding, "LocalModelAdapter", FailingAdapter)

    with pytest.raises(RuntimeError, match="model_timeout"):
        run_stage(project_dir, "06_shot_understanding", force=True)

    state = load_run_state(project_dir)
    record = get_stage_record(state, "06_shot_understanding")
    assert record["status"] == "failed"
    assert "model_timeout" in record["error"]
    assert not (project_dir / "shot_understanding" / "shot_analysis.jsonl").exists()


def test_model_adapter_rejects_unimplemented_provider(tmp_path) -> None:
    with pytest.raises(NotImplementedError, match="LLM provider is not implemented: unknown_vendor"):
        LocalModelAdapter(tmp_path, {"provider": "unknown_vendor", "use_env": False})


def test_openai_compatible_requires_model_config(tmp_path) -> None:
    with pytest.raises(RuntimeError, match="Missing LLM API key"):
        LocalModelAdapter(tmp_path, {"provider": "openai_compatible", "model": "demo-model", "use_env": False})


def test_role_specific_model_env_overrides_shared_config(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("VIDEO2VISUALPAGE_VISION_MODEL_PROVIDER", "openai_compatible")
    monkeypatch.setenv("VIDEO2VISUALPAGE_VISION_MODEL_BASE_URL", "https://vision.example/v1")
    monkeypatch.setenv("VIDEO2VISUALPAGE_VISION_MODEL_MODEL", "vision-demo")
    monkeypatch.setenv("VIDEO2VISUALPAGE_VISION_MODEL_API_KEY", "vision-key")
    monkeypatch.setenv("VIDEO2VISUALPAGE_VISION_MODEL_VISION_ENABLED", "1")
    monkeypatch.setenv("VIDEO2VISUALPAGE_VISION_MODEL_OCR_DEVICE", "gpu:0")
    monkeypatch.setenv("VIDEO2VISUALPAGE_COPYWRITING_MODEL_PROVIDER", "openai_compatible")
    monkeypatch.setenv("VIDEO2VISUALPAGE_COPYWRITING_MODEL_BASE_URL", "https://copy.example/v1")
    monkeypatch.setenv("VIDEO2VISUALPAGE_COPYWRITING_MODEL_MODEL", "copy-demo")
    monkeypatch.setenv("VIDEO2VISUALPAGE_COPYWRITING_MODEL_API_KEY", "copy-key")
    monkeypatch.setenv("VIDEO2VISUALPAGE_COPYWRITING_MODEL_VISION_ENABLED", "0")

    config = {
        "llm": {"provider": "local_heuristic", "use_env": True},
        "vision_model": {},
        "copywriting_model": {},
    }

    vision_config = effective_model_config(config, "vision")
    copy_config = effective_model_config(config, "copywriting")
    assert vision_config["model"] == "vision-demo"
    assert vision_config["base_url"] == "https://vision.example/v1"
    assert vision_config["vision_enabled"] is True
    assert vision_config["ocr_device"] == "gpu:0"
    assert copy_config["model"] == "copy-demo"
    assert copy_config["base_url"] == "https://copy.example/v1"
    assert copy_config["vision_enabled"] is False

    vision_adapter = LocalModelAdapter(tmp_path, config, model_role="vision")
    copy_adapter = LocalModelAdapter(tmp_path, config, model_role="copywriting")
    assert vision_adapter.model == "vision-demo"
    assert copy_adapter.model == "copy-demo"


def test_model_signature_is_role_specific(monkeypatch) -> None:
    monkeypatch.setenv("VIDEO2VISUALPAGE_VISION_MODEL_MODEL", "vision-demo")
    monkeypatch.setenv("VIDEO2VISUALPAGE_COPYWRITING_MODEL_MODEL", "copy-demo")

    config = {
        "llm": {"provider": "local_heuristic", "use_env": True},
        "vision_model": {},
        "copywriting_model": {},
    }

    vision_signature = model_signature(config, "vision")
    copy_signature = model_signature(config, "copywriting")
    assert vision_signature["model_role"] == "vision"
    assert copy_signature["model_role"] == "copywriting"
    assert vision_signature["signature"] != copy_signature["signature"]
