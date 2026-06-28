from __future__ import annotations

from copy import deepcopy
from typing import Any


DEFAULT_CONFIG: dict[str, Any] = {
    "scene_detection": {
        "threshold": 0.5,
        "shot_prefix": "shot",
        "keyframe_positions": [0.2, 0.8],
        "min_gap_seconds": 0.35,
        "min_shot_seconds": 0.15,
        "fallback_mode": "single_shot",
    },
    "subtitle": {
        "prefer_sidecar": True,
        "asr_enabled": True,
        "online_asr_env": "VIDEO2VISUALPAGE_ENABLE_ONLINE_ASR",
        "language": "auto",
    },
    "llm": {
        "use_env": True,
        "provider": "local_heuristic",
        "base_url": None,
        "api_key": None,
        "model": None,
        "output_language": "zh-CN",
        "json_mode": True,
        "vision_enabled": True,
        "max_images_per_shot": 1,
        "temperature": 0.2,
        "timeout_sec": 60,
        "max_retries": 2,
        "retry_delay_sec": 0.2,
        "rate_limit_per_minute": 60,
        "max_shots_per_chunk": 40,
        "chapter_count": "auto",
    },
    "llm_monitoring": {
        "enabled": True,
        "capture_payloads": True,
        "capture_raw_response": True,
        "max_text_chars": 50000,
    },
    "chapter_subsections": {
        "enabled": True,
        "min_shots_for_model": 8,
        "min_need_score": 5,
        "max_subsections_per_chapter": 5,
        "min_shots_per_subsection": 2,
        "min_coverage_ratio": 0.6,
    },
    "chapter_write": {
        "max_shots_per_call": 20,
    },
    "vision_model": {
        "provider": "paddleocr",
        "base_url": None,
        "api_key": None,
        "model": "PP-OCRv6_medium",
        "vision_enabled": True,
        "max_images_per_shot": 2,
        "temperature": 0.2,
        "ocr_root": "utils/PaddleOCR-main",
        "ocr_det_model_dir": "utils/PaddleOCR-main/models/PP-OCRv6_medium_det",
        "ocr_rec_model_dir": "utils/PaddleOCR-main/models/PP-OCRv6_medium_rec",
        "ocr_device": "gpu",
        "ocr_engine": "paddle",
        "ocr_min_score": 0.6,
        "ocr_crop_min_score": 0.6,
        "ocr_allow_cpu_fallback": True,
        "ocr_crop_fallback": True,
        "ocr_concurrency": 1,
        "ocr_use_tensorrt": False,
        "ocr_precision": "fp32",
    },
    "copywriting_model": {
        "provider": None,
        "base_url": None,
        "api_key": None,
        "model": None,
        "vision_enabled": False,
        "max_images_per_shot": 0,
        "temperature": 0.35,
    },
    "render": {
        "output_html": True,
        "output_pdf": False,
        "theme": "clean_report",
        "include_timeline": True,
        "include_shot_references": True,
    },
    "qa": {
        "autofix": True,
    },
}


def default_config() -> dict[str, Any]:
    return deepcopy(DEFAULT_CONFIG)
