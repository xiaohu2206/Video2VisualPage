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
    "vision_model": {
        "provider": None,
        "base_url": None,
        "api_key": None,
        "model": None,
        "vision_enabled": True,
        "max_images_per_shot": 1,
        "temperature": 0.2,
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
