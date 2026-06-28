from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from video2visualpage.config import default_config
from video2visualpage.models import LocalModelAdapter
from video2visualpage.models.adapter import effective_model_config


def _apply_smoke_runtime_overrides() -> None:
    timeout = os.environ.get("VIDEO2VISUALPAGE_SMOKE_TIMEOUT_SEC", "90")
    for prefix in (
        "VIDEO2VISUALPAGE_LLM",
        "VIDEO2VISUALPAGE_COPYWRITING_MODEL",
        "VIDEO2VISUALPAGE_VISION_MODEL",
    ):
        os.environ[f"{prefix}_TIMEOUT"] = timeout
        os.environ[f"{prefix}_MAX_RETRIES"] = "0"
    os.environ["VIDEO2VISUALPAGE_VISION_MODEL_VISION_ENABLED"] = "1"
    os.environ["VIDEO2VISUALPAGE_VISION_MODEL_MAX_IMAGES_PER_SHOT"] = "1"


def _model_summary(config: dict, role: str) -> dict:
    effective = effective_model_config(config, role)
    return {
        "provider": effective.get("provider"),
        "base_url": effective.get("base_url"),
        "model": effective.get("model"),
        "api_key_present": bool(effective.get("api_key")),
    }


def _pick_smoke_image() -> Path:
    images = sorted((REPO_ROOT / "docs" / "images").glob("*.png"), key=lambda path: path.stat().st_size)
    if not images:
        raise FileNotFoundError("No PNG smoke image found under docs/images.")
    return images[0]


def main() -> int:
    _apply_smoke_runtime_overrides()

    config = default_config()
    config["llm_monitoring"] = {"enabled": False}

    summary = {
        "copywriting": _model_summary(config, "copywriting"),
        "vision": _model_summary(config, "vision"),
    }

    with tempfile.TemporaryDirectory(prefix="v2vp_model_smoke_") as temp_dir:
        project_dir = Path(temp_dir)
        frame_dir = project_dir / "frames"
        frame_dir.mkdir(parents=True, exist_ok=True)
        frame_path = frame_dir / "smoke_frame.png"
        shutil.copy2(_pick_smoke_image(), frame_path)

        try:
            print("Running copywriting model smoke test...", flush=True)
            copy_adapter = LocalModelAdapter(project_dir, config, model_role="copywriting")
            copy_result = copy_adapter._chat_raw(
                "manual_text_smoke",
                "You are a connectivity smoke test responder.",
                {"task": "reply with text_model_ok"},
                response_instruction="Reply with text_model_ok only.",
                json_response=False,
                preserve_json_keys=True,
            )
            summary["copywriting"].update(
                {
                    "ok": True,
                    "preview": str(copy_result).strip()[:200],
                }
            )
        except Exception as exc:  # noqa: BLE001 - manual smoke test should report provider errors.
            summary["copywriting"].update({"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:1000]}"})

        try:
            print("Running vision model smoke test...", flush=True)
            vision_adapter = LocalModelAdapter(project_dir, config, model_role="vision")
            vision_result = vision_adapter._chat_raw(
                "manual_vision_smoke",
                "You are a vision connectivity smoke test responder.",
                {"task": "describe the attached image in one short sentence"},
                images=["frames/smoke_frame.png"],
                response_instruction="Reply in one short sentence.",
                json_response=False,
                preserve_json_keys=True,
            )
            summary["vision"].update(
                {
                    "ok": True,
                    "image_attached": True,
                    "preview": str(vision_result).strip()[:240],
                }
            )
        except Exception as exc:  # noqa: BLE001 - manual smoke test should report provider errors.
            summary["vision"].update({"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:1000]}"})

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["copywriting"].get("ok") and summary["vision"].get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
