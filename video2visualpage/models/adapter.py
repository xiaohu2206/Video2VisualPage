from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from ..env import env_flag, env_value
from ..paths import repo_root, resolve_artifact_path
from ..progress import ProgressReporter
from ..state import now_iso
from ..storage import append_jsonl


def _compact_text(value: str, limit: int = 120) -> str:
    text = re.sub(r"\s+", " ", value).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."


def _keywords(text: str, limit: int = 6) -> list[str]:
    stopwords = {
        "the",
        "this",
        "that",
        "shot",
        "segment",
        "sampled",
        "keyframe",
        "keyframes",
        "seconds",
        "across",
        "has",
        "with",
        "video",
        "visual",
    }
    words = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z0-9_-]+", text)
    counts: dict[str, int] = {}
    for word in words:
        token = word.strip()
        if not token or token.lower() in stopwords:
            continue
        counts[token] = counts.get(token, 0) + 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], len(item[0]), item[0]))
    return [word for word, _ in ranked[:limit]]


def _int_value(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float_value(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


MODEL_ROLE_CONFIG_KEYS = {
    "vision": "vision_model",
    "copywriting": "copywriting_model",
}

MODEL_ROLE_ENV_PREFIXES = {
    "vision": ("VIDEO2VISUALPAGE_VISION_MODEL",),
    "copywriting": ("VIDEO2VISUALPAGE_COPYWRITING_MODEL", "VIDEO2VISUALPAGE_TEXT_MODEL"),
}

MODEL_ROLE_DEFAULTS: dict[str, dict[str, Any]] = {
    "vision": {
        "vision_enabled": True,
        "max_images_per_shot": 1,
        "temperature": 0.2,
    },
    "copywriting": {
        "vision_enabled": False,
        "max_images_per_shot": 0,
        "temperature": 0.35,
    },
}

ENV_VALUE_SUFFIXES = {
    "PROVIDER": "provider",
    "BASE_URL": "base_url",
    "API_KEY": "api_key",
    "MODEL": "model",
    "OUTPUT_LANGUAGE": "output_language",
    "MAX_RETRIES": "max_retries",
    "RETRY_DELAY_SEC": "retry_delay_sec",
    "RATE_LIMIT_PER_MINUTE": "rate_limit_per_minute",
    "TEMPERATURE": "temperature",
    "TIMEOUT": "timeout_sec",
    "MAX_IMAGES_PER_SHOT": "max_images_per_shot",
}

ENV_FLAG_SUFFIXES = {
    "JSON_MODE": "json_mode",
    "VISION_ENABLED": "vision_enabled",
}


def _normalize_model_role(model_role: str | None) -> str | None:
    if model_role is None:
        return None
    normalized = model_role.strip().lower().replace("-", "_")
    if normalized in ("text", "writer", "writing", "copy", "copywriting_model"):
        return "copywriting"
    if normalized in ("vision_model", "visual"):
        return "vision"
    if normalized in MODEL_ROLE_CONFIG_KEYS:
        return normalized
    raise ValueError(f"Unknown model role: {model_role}")


def _apply_non_empty(target: dict[str, Any], source: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(source, dict):
        return target
    for key, value in source.items():
        if value is None or value == "":
            continue
        target[key] = value
    return target


def _apply_prefixed_env(merged: dict[str, Any], prefix: str) -> None:
    for suffix, key in ENV_VALUE_SUFFIXES.items():
        value = env_value(f"{prefix}_{suffix}")
        if value not in (None, ""):
            merged[key] = value
    for suffix, key in ENV_FLAG_SUFFIXES.items():
        env_name = f"{prefix}_{suffix}"
        if env_value(env_name) not in (None, ""):
            merged[key] = env_flag(env_name, default=bool(merged.get(key, True)))


def _merge_env_config(
    config: dict[str, Any],
    *,
    model_role: str | None = None,
    apply_legacy_env: bool = True,
    apply_role_env: bool = True,
) -> dict[str, Any]:
    merged = dict(config)
    if not bool(merged.get("use_env", True)):
        return merged

    if apply_legacy_env:
        _apply_prefixed_env(merged, "VIDEO2VISUALPAGE_LLM")

        openai_key = env_value("OPENAI_API_KEY")
        if not merged.get("api_key") and openai_key:
            merged["api_key"] = openai_key
        openai_base_url = env_value("OPENAI_BASE_URL")
        if not merged.get("base_url") and openai_base_url:
            merged["base_url"] = openai_base_url
        openai_model = env_value("OPENAI_MODEL")
        if not merged.get("model") and openai_model:
            merged["model"] = openai_model

    if apply_role_env and model_role:
        for prefix in MODEL_ROLE_ENV_PREFIXES[model_role]:
            _apply_prefixed_env(merged, prefix)
    return merged


def effective_model_config(config: dict[str, Any], model_role: str | None = None) -> dict[str, Any]:
    role = _normalize_model_role(model_role)
    if any(key in config for key in ("llm", *MODEL_ROLE_CONFIG_KEYS.values())):
        common = _merge_env_config(config.get("llm", {}), apply_legacy_env=True, apply_role_env=False)
        if not role:
            return common
        role_config = dict(MODEL_ROLE_DEFAULTS.get(role, {}))
        _apply_non_empty(role_config, config.get(MODEL_ROLE_CONFIG_KEYS[role], {}))
        merged = dict(common)
        _apply_non_empty(merged, role_config)
        return _merge_env_config(merged, model_role=role, apply_legacy_env=False, apply_role_env=True)
    return _merge_env_config(config, model_role=role)


PROMPT_SIGNATURE_VERSION = "2026-06-24-ocr-subtitle-note-output"
PROMPT_SIGNATURE_FILES = (
    "shot_analysis_prompt.txt",
    "chapter_writer_prompt.txt",
    "outline_prompt.txt",
)


def _prompt_bundle_signature() -> str:
    hasher = hashlib.sha256()
    hasher.update(PROMPT_SIGNATURE_VERSION.encode("utf-8"))
    prompt_dir = repo_root() / "video2visualpage" / "prompts"
    for filename in PROMPT_SIGNATURE_FILES:
        path = prompt_dir / filename
        hasher.update(filename.encode("utf-8"))
        if path.exists():
            hasher.update(path.read_bytes())
    return hasher.hexdigest()


def model_signature(config: dict[str, Any], model_role: str | None = None) -> dict[str, Any]:
    role = _normalize_model_role(model_role)
    merged = effective_model_config(config, role)
    payload = {
        "model_role": role or "llm",
        "provider": str(merged.get("provider", "local_heuristic")).strip(),
        "base_url": str(merged.get("base_url") or "").strip().rstrip("/"),
        "model": str(merged.get("model") or "").strip(),
        "output_language": str(merged.get("output_language") or "zh-CN").strip(),
        "json_mode": bool(merged.get("json_mode", True)),
        "vision_enabled": bool(merged.get("vision_enabled", True)),
        "max_images_per_shot": _int_value(merged.get("max_images_per_shot"), 1),
        "temperature": _float_value(merged.get("temperature"), 0.2),
        "prompt_bundle": _prompt_bundle_signature(),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return {**payload, "signature": hashlib.sha256(encoded).hexdigest()}


def _read_prompt(filename: str, fallback: str) -> str:
    prompt_path = repo_root() / "video2visualpage" / "prompts" / filename
    if prompt_path.exists():
        return prompt_path.read_text(encoding="utf-8")
    return fallback


def _with_language_instruction(prompt: str, output_language: str, *, preserve_json_keys: bool = True) -> str:
    language = output_language or "zh-CN"
    suffix = f"输出语言要求：所有自然语言内容必须使用 {language}。"
    if preserve_json_keys:
        suffix += "JSON 字段名必须保持原样，不要翻译 ID、文件路径、枚举值或 JSON key。"
    else:
        suffix += "结构标签名、ID、文件路径和枚举值必须保持原样。"
    return f"{prompt.strip()}\n\n{suffix}"


def _parse_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        fence = re.search(r"```(?:json)?\s*(.*?)```", stripped, flags=re.DOTALL | re.IGNORECASE)
        if fence:
            return _parse_json_object(fence.group(1))
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start >= 0 and end > start:
            value = json.loads(stripped[start : end + 1])
        else:
            raise
    if not isinstance(value, dict):
        raise ValueError("Model response must be a JSON object")
    return value


def _parse_json_value(text: str) -> Any:
    stripped = text.strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        fence = re.search(r"```(?:json)?\s*(.*?)```", stripped, flags=re.DOTALL | re.IGNORECASE)
        if fence:
            return _parse_json_value(fence.group(1))
        start_candidates = [index for index in (stripped.find("{"), stripped.find("[")) if index >= 0]
        if start_candidates:
            start = min(start_candidates)
            end = max(stripped.rfind("}"), stripped.rfind("]"))
            if end > start:
                return json.loads(stripped[start : end + 1])
        raise


def _extract_tag(text: str, tag: str) -> str | None:
    pattern = rf"<\s*{re.escape(tag)}(?:\s+[^>]*)?\s*>(.*?)<\s*/\s*{re.escape(tag)}\s*>"
    match = re.search(pattern, text, flags=re.DOTALL | re.IGNORECASE)
    if not match:
        return None
    return match.group(1).strip()


def _tag_text(text: str, tag: str, default: str = "") -> str:
    value = _extract_tag(text, tag)
    if value is None:
        return default
    return value.strip()


def _tag_list(text: str, tag: str) -> list[str]:
    value = _extract_tag(text, tag)
    if value is None:
        return []
    stripped = value.strip()
    if stripped.lower() in {"", "null", "none", "n/a", "[]"} or stripped in {"无", "无。", "没有"}:
        return []

    items: list[str] = []
    for line in stripped.splitlines():
        item = re.sub(r"^\s*(?:[-*+]|\d+[.)]|[一二三四五六七八九十]+[、.])\s*", "", line).strip()
        if item:
            items.append(item)
    if len(items) <= 1:
        items = [item.strip() for item in re.split(r"[,\u3001，;；|]", stripped) if item.strip()]

    deduped: list[str] = []
    seen: set[str] = set()
    for item in items:
        cleaned = item.strip().strip("`")
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            deduped.append(cleaned)
    return deduped


def _tag_float(text: str, tag: str, default: float) -> float:
    value = _extract_tag(text, tag)
    if value is None:
        return default
    match = re.search(r"-?\d+(?:\.\d+)?", value)
    if not match:
        return default
    number = _float_value(match.group(0), default)
    return min(1.0, max(0.0, number))


def _tag_frame(text: str, tag: str, frames: list[str]) -> tuple[str | None, str | None]:
    value = _extract_tag(text, tag)
    if value is None:
        return (frames[0] if frames else None), None
    candidate = value.strip().strip("\"'`")
    if candidate.lower() in {"", "null", "none", "n/a"} or candidate in {"无", "无。", "没有"}:
        return (frames[0] if frames else None), None
    if candidate in frames:
        return candidate, None
    basename_matches = [frame for frame in frames if Path(frame).name == candidate or frame.endswith(candidate)]
    if basename_matches:
        return basename_matches[0], None
    if frames:
        return frames[0], f"模型返回的推荐帧不在候选关键帧中，已回退到 {frames[0]}。"
    return None, "模型返回了推荐帧，但该镜头没有可用关键帧。"


def _dedupe_texts(items: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = re.sub(r"\s+", " ", str(item)).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        deduped.append(text)
    return deduped


def _extract_ocr_texts(raw_text: str) -> list[str]:
    try:
        value = _parse_json_value(raw_text)
    except Exception:
        lines = [line.strip("- \t\r") for line in raw_text.splitlines()]
        return _dedupe_texts([line for line in lines if line and not line.startswith("```")])

    texts: list[str] = []

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            text = node.get("text")
            if isinstance(text, str) and text.strip():
                texts.append(text)
            for key in ("words_info", "ocr_result", "kv_result", "data", "items", "result"):
                if key in node:
                    visit(node[key])
        elif isinstance(node, list):
            for item in node:
                visit(item)

    visit(value)
    if not texts and isinstance(value, str):
        texts.append(value)
    return _dedupe_texts(texts)


def _parse_tagged_shot_analysis(text: str, package: dict[str, Any]) -> dict[str, Any]:
    missing = [tag for tag in ("visual_summary", "merged_summary") if _extract_tag(text, tag) is None]
    if missing:
        raise ValueError(f"shot_understanding tagged response missing required tags: {', '.join(missing)}")

    shot_id = str(package.get("shot_id") or "").strip()
    time_range = package.get("time_range") or {}
    frames = [str(frame) for frame in list(package.get("frames") or [])]
    subtitle = str(package.get("subtitle_text") or "").strip()
    warnings = [str(item) for item in list(package.get("warnings") or []) if str(item).strip()]
    parsed_warnings = _tag_list(text, "warnings")
    for warning in parsed_warnings:
        if warning not in warnings:
            warnings.append(warning)
    recommended_frame, frame_warning = _tag_frame(text, "recommended_display_frame", frames)
    if frame_warning and frame_warning not in warnings:
        warnings.append(frame_warning)

    merged_summary = _tag_text(text, "merged_summary")
    topic_tags = _tag_list(text, "topic_tags") or _keywords(merged_summary, limit=3) or ["内容提取"]
    narrative_role = _tag_text(text, "narrative_role", "unknown").strip().lower()
    allowed_roles = {"introduction", "explanation", "demo", "transition", "conclusion", "unknown"}
    if narrative_role not in allowed_roles:
        warnings.append(f"模型返回了未知 narrative_role={narrative_role}，已改为 unknown。")
        narrative_role = "unknown"

    return {
        "shot_id": shot_id,
        "start_sec": time_range.get("start_sec"),
        "end_sec": time_range.get("end_sec"),
        "visual_summary": _tag_text(text, "visual_summary"),
        "subtitle_summary": _tag_text(text, "subtitle_summary", _compact_text(subtitle, 160) if subtitle else "该镜头没有可用字幕。"),
        "merged_summary": merged_summary,
        "key_entities": _tag_list(text, "key_entities"),
        "actions": _tag_list(text, "actions"),
        "on_screen_text": _tag_list(text, "on_screen_text"),
        "topic_tags": topic_tags,
        "narrative_role": narrative_role,
        "importance_score": round(_tag_float(text, "importance_score", 0.5), 3),
        "recommended_display_frame": recommended_frame,
        "confidence": round(_tag_float(text, "confidence", 0.5), 3),
        "warnings": warnings,
        "model_output_format": "tagged_markdown_v1",
    }


def _require_keys(stage: str, payload: dict[str, Any], keys: list[str]) -> dict[str, Any]:
    missing = [key for key in keys if key not in payload]
    if missing:
        raise ValueError(f"{stage} model response missing required keys: {', '.join(missing)}")
    return payload


class LocalModelAdapter:
    """Model adapter with an explicit local heuristic mode and OpenAI-compatible API mode."""

    SUPPORTED_REMOTE_PROVIDERS = {"openai", "openai_compatible"}

    def __init__(
        self,
        project_dir: str | Path,
        config: dict[str, Any],
        *,
        model_role: str | None = None,
        progress: ProgressReporter | None = None,
    ):
        self.project_dir = Path(project_dir)
        self.model_role = _normalize_model_role(model_role) or "llm"
        role_for_config = None if self.model_role == "llm" else self.model_role
        self.config = effective_model_config(config, role_for_config)
        self.progress = progress
        self.provider = str(self.config.get("provider", "local_heuristic")).strip()
        self.max_retries = _int_value(self.config.get("max_retries"), 2)
        self.retry_delay_sec = _float_value(self.config.get("retry_delay_sec"), 0.2)
        self.rate_limit_per_minute = max(1, _int_value(self.config.get("rate_limit_per_minute"), 60))
        self.temperature = _float_value(self.config.get("temperature"), 0.2)
        self.timeout_sec = _float_value(self.config.get("timeout_sec"), 60.0)
        self.json_mode = bool(self.config.get("json_mode", True))
        self.vision_enabled = bool(self.config.get("vision_enabled", True))
        self.max_images_per_shot = max(0, _int_value(self.config.get("max_images_per_shot"), 1))
        self.output_language = str(self.config.get("output_language") or "zh-CN").strip()
        self.model = str(self.config.get("model") or "").strip()
        self.base_url = str(self.config.get("base_url") or "https://api.openai.com/v1").strip().rstrip("/")
        self.api_key = str(self.config.get("api_key") or "").strip()
        self._last_call = 0.0

        if self.provider == "local_heuristic":
            return
        if self.provider not in self.SUPPORTED_REMOTE_PROVIDERS:
            raise NotImplementedError(f"LLM provider is not implemented: {self.provider}")
        if not self.api_key:
            raise RuntimeError(f"Missing {self._model_label()} API key. Set {self._model_env_hint('API_KEY')} in .env.")
        if not self.model:
            raise RuntimeError(f"Missing {self._model_label()} model. Set {self._model_env_hint('MODEL')} in .env.")

    def _model_label(self) -> str:
        if self.model_role == "vision":
            return "vision model"
        if self.model_role == "copywriting":
            return "copywriting model"
        return "LLM"

    def _model_env_hint(self, suffix: str) -> str:
        if self.model_role == "llm":
            return f"VIDEO2VISUALPAGE_LLM_{suffix} or OPENAI_{suffix}"
        role_prefixes = " or ".join(f"{prefix}_{suffix}" for prefix in MODEL_ROLE_ENV_PREFIXES[self.model_role])
        return f"{role_prefixes} or VIDEO2VISUALPAGE_LLM_{suffix} or OPENAI_{suffix}"

    def _rate_limit(self) -> None:
        if self.provider == "local_heuristic":
            return
        min_gap = 60.0 / float(self.rate_limit_per_minute)
        elapsed = time.monotonic() - self._last_call
        if elapsed < min_gap:
            time.sleep(min_gap - elapsed)
        self._last_call = time.monotonic()

    def _with_retries(self, stage: str, fn: Callable[[], dict[str, Any]], payload: dict[str, Any]) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 2):
            self._rate_limit()
            started = time.perf_counter()
            try:
                if self.progress:
                    self.progress.model_attempt(
                        stage,
                        attempt,
                        self.provider,
                        self.model,
                        self._with_role_progress(self._payload_progress_summary(stage, payload)),
                    )
                result = fn()
                elapsed = time.perf_counter() - started
                if self.progress:
                    self.progress.model_success(stage, attempt, elapsed, self._result_progress_summary(result))
                self._log_call(stage, payload, result, attempt=attempt, elapsed=elapsed)
                return result
            except Exception as exc:  # noqa: BLE001 - adapter boundary logs all failures.
                elapsed = time.perf_counter() - started
                last_error = exc
                if self.progress:
                    self.progress.model_failure(stage, attempt, elapsed, exc)
                self._log_call(stage, payload, {"error": str(exc)}, attempt=attempt, elapsed=elapsed)
                if attempt <= self.max_retries:
                    time.sleep(self.retry_delay_sec)
        raise RuntimeError(f"{stage} failed after retries: {last_error}") from last_error

    def _with_role_progress(self, detail: str) -> str:
        if self.model_role == "llm":
            return detail
        return f"role={self.model_role}; {detail}"

    def _payload_progress_summary(self, stage: str, payload: dict[str, Any]) -> str:
        if stage == "shot_understanding":
            time_range = payload.get("time_range") or {}
            subtitle = _compact_text(str(payload.get("subtitle_text") or ""), 80) or "无字幕"
            frames = list(payload.get("frames") or [])
            return (
                f"shot_id={payload.get('shot_id')}; "
                f"time={time_range.get('start_sec')}-{time_range.get('end_sec')}s; "
                f"frames={len(frames)}; subtitle={subtitle}"
            )
        if stage == "summary_reduce":
            cards = list(payload.get("cards") or [])
            shot_ids = [str(card.get("shot_id")) for card in cards if card.get("shot_id")]
            shot_range = f"{shot_ids[0]}-{shot_ids[-1]}" if shot_ids else "empty"
            return f"chunk_id={payload.get('chunk_id')}; shots={len(cards)}; range={shot_range}"
        if stage == "chapter_write":
            chapter = payload.get("chapter") or {}
            cards = list(payload.get("cards") or [])
            title = _compact_text(str(chapter.get("title") or ""), 80)
            return f"chapter_id={chapter.get('chapter_id')}; title={title}; shots={len(cards)}"
        return f"input_keys={','.join(sorted(payload.keys()))}"

    def _result_progress_summary(self, result: dict[str, Any]) -> str:
        keys = ",".join(sorted(result.keys()))
        preview_source = result.get("merged_summary") or result.get("summary") or result.get("title") or result.get("body_markdown") or ""
        preview = _compact_text(str(preview_source), 80)
        return f"output_keys={keys}; preview={preview}" if preview else f"output_keys={keys}"

    def _log_call(
        self,
        stage: str,
        payload: dict[str, Any],
        result: dict[str, Any],
        *,
        attempt: int,
        elapsed: float,
    ) -> None:
        append_jsonl(
            self.project_dir / "logs" / "llm_calls.jsonl",
            {
                "time": now_iso(),
                "model_role": self.model_role,
                "provider": self.provider,
                "model": self.model if self.provider != "local_heuristic" else None,
                "stage": stage,
                "attempt": attempt,
                "elapsed_sec": round(elapsed, 3),
                "input_keys": sorted(payload.keys()),
                "output_keys": sorted(result.keys()),
            },
        )

    def _chat_completions_url(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return f"{self.base_url}/chat/completions"

    def _image_part(self, frame: str) -> dict[str, Any] | None:
        path = resolve_artifact_path(self.project_dir, frame)
        if path is None or not path.exists():
            return None
        mime_type = mimetypes.guess_type(path.name)[0] or "image/jpeg"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{encoded}"}}

    def _chat_raw(
        self,
        stage: str,
        system_prompt: str,
        payload: dict[str, Any],
        *,
        images: list[str] | None = None,
        response_instruction: str,
        json_response: bool,
        preserve_json_keys: bool,
    ) -> str:
        content: str | list[dict[str, Any]]
        text = (
            "输入 JSON:\n"
            + json.dumps(payload, ensure_ascii=False, indent=2)
            + "\n"
            + response_instruction
        )
        image_parts: list[dict[str, Any]] = []
        if self.vision_enabled and images:
            for frame in images[: self.max_images_per_shot]:
                part = self._image_part(frame)
                if part:
                    image_parts.append(part)
        if image_parts:
            content = [{"type": "text", "text": text}, *image_parts]
        else:
            content = text

        body: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": _with_language_instruction(
                        system_prompt,
                        self.output_language,
                        preserve_json_keys=preserve_json_keys,
                    ),
                },
                {"role": "user", "content": content},
            ],
            "temperature": self.temperature,
        }
        if json_response and self.json_mode:
            body["response_format"] = {"type": "json_object"}

        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self._chat_completions_url(),
            data=data,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_sec) as response:
                response_payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"LLM API request failed with HTTP {exc.code}: {body_text[:500]}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"LLM API request failed: {exc}") from exc

        choices = response_payload.get("choices") if isinstance(response_payload, dict) else None
        if not choices:
            raise RuntimeError("LLM API response does not contain choices")
        message = choices[0].get("message") or {}
        raw_content = message.get("content")
        if isinstance(raw_content, list):
            raw_content = "".join(str(item.get("text", "")) if isinstance(item, dict) else str(item) for item in raw_content)
        if isinstance(raw_content, dict):
            raw_content = json.dumps(raw_content, ensure_ascii=False)
        if not isinstance(raw_content, str) or not raw_content.strip():
            raise RuntimeError("LLM API response content is empty")
        return raw_content

    def _chat_json(self, stage: str, system_prompt: str, payload: dict[str, Any], images: list[str] | None = None) -> dict[str, Any]:
        raw_content = self._chat_raw(
            stage,
            system_prompt,
            payload,
            images=images,
            response_instruction=f"只返回严格 JSON。所有自然语言字段使用 {self.output_language}。",
            json_response=True,
            preserve_json_keys=True,
        )
        return _parse_json_object(raw_content)

    def _chat_text(self, stage: str, system_prompt: str, payload: dict[str, Any], images: list[str] | None = None) -> str:
        return self._chat_raw(
            stage,
            system_prompt,
            payload,
            images=images,
            response_instruction=f"按系统提示里的标签协议返回，不要返回 JSON。所有自然语言内容使用 {self.output_language}。",
            json_response=False,
            preserve_json_keys=False,
        )

    def _local_analyze_shot(self, package: dict[str, Any]) -> dict[str, Any]:
        shot_id = str(package.get("shot_id"))
        subtitle = str(package.get("subtitle_text") or "").strip()
        frames = list(package.get("frames") or [])
        time_range = package.get("time_range") or {}
        keys = _keywords(subtitle) if subtitle else []
        subtitle_summary = _compact_text(subtitle, 180) if subtitle else "该镜头没有可用字幕。"
        visual_summary = "本地启发式模式无法读取关键帧文字；请使用 vision model 进行 OCR。"
        if not frames:
            visual_summary = "未提供可用于 OCR 的关键帧。"
        merged = subtitle_summary if subtitle else "未提取到可用于笔记的文字内容。"
        warnings = [str(item) for item in list(package.get("warnings") or []) if str(item).strip()]
        warning = "local_heuristic_no_ocr: 本地启发式模式不会读取关键帧文字。"
        if warning not in warnings:
            warnings.append(warning)
        importance = min(0.9, max(0.15, 0.25 + min(len(subtitle), 180) / 260.0))
        return {
            "shot_id": shot_id,
            "start_sec": time_range.get("start_sec"),
            "end_sec": time_range.get("end_sec"),
            "visual_summary": visual_summary,
            "subtitle_summary": subtitle_summary,
            "merged_summary": merged,
            "key_entities": keys[:4],
            "actions": [],
            "on_screen_text": [],
            "topic_tags": keys[:3] or ["内容提取"],
            "narrative_role": "explanation" if subtitle else "unknown",
            "importance_score": round(importance, 3),
            "recommended_display_frame": frames[0] if frames else None,
            "confidence": 0.45 if subtitle else 0.2,
            "warnings": warnings,
        }

    def _is_ocr_model(self) -> bool:
        return "ocr" in self.model.lower()

    def _ocr_frame_texts(self, package: dict[str, Any], frame: str) -> list[str]:
        raw = self._chat_raw(
            "shot_understanding",
            "你是 OCR 引擎。只提取图片中可读文字，不要解释，不要补充图片外信息。",
            {
                "shot_id": package.get("shot_id"),
                "frame": frame,
                "task": "extract_visible_text",
            },
            images=[frame],
            response_instruction="只输出图片中的文字识别结果；看不清的文字不要猜。",
            json_response=False,
            preserve_json_keys=False,
        )
        return _extract_ocr_texts(raw)

    def _ocr_analyze_shot(self, package: dict[str, Any]) -> dict[str, Any]:
        shot_id = str(package.get("shot_id") or "")
        time_range = package.get("time_range") or {}
        frames = [str(frame) for frame in list(package.get("frames") or [])]
        subtitle = str(package.get("subtitle_text") or "").strip()
        warnings = [str(item) for item in list(package.get("warnings") or []) if str(item).strip()]
        frame_texts: list[tuple[str, list[str]]] = []
        for frame in frames[: self.max_images_per_shot]:
            try:
                frame_texts.append((frame, self._ocr_frame_texts(package, frame)))
            except Exception as exc:  # noqa: BLE001 - keep per-frame OCR failures visible in output.
                warnings.append(f"ocr_failed:{Path(frame).name}:{exc}")
                frame_texts.append((frame, []))

        on_screen_text = _dedupe_texts([text for _, texts in frame_texts for text in texts])
        subtitle_summary = _compact_text(subtitle, 240) if subtitle else "该镜头没有可用字幕。"
        if not subtitle:
            warnings.append("该镜头无字幕")
        if not on_screen_text:
            warnings.append("关键帧未识别出有效文字内容。")

        visual_summary = "\n".join(f"- {text}" for text in on_screen_text) if on_screen_text else "未提取到可读画面文字。"
        note_parts: list[str] = []
        note_parts.extend(f"- {text}" for text in on_screen_text)
        if subtitle:
            note_parts.append(f"- 字幕：{subtitle}")
        merged_summary = "\n".join(note_parts) if note_parts else "无可用知识点内容。"

        keywords = _keywords(" ".join([*on_screen_text, subtitle]), limit=8)
        best_frame = None
        if frame_texts:
            best_frame = max(frame_texts, key=lambda item: len(item[1]))[0]
        confidence = 0.85 if on_screen_text else (0.45 if subtitle else 0.0)
        importance = min(0.95, max(confidence, 0.25 + len(on_screen_text) * 0.08 + min(len(subtitle), 160) / 320.0))
        return {
            "shot_id": shot_id,
            "start_sec": time_range.get("start_sec"),
            "end_sec": time_range.get("end_sec"),
            "visual_summary": visual_summary,
            "subtitle_summary": subtitle_summary,
            "merged_summary": merged_summary,
            "key_entities": keywords[:6],
            "actions": ["文字提取"] if on_screen_text else [],
            "on_screen_text": on_screen_text,
            "topic_tags": keywords[:3] or (["文字提取"] if on_screen_text else ["内容提取"]),
            "narrative_role": "explanation" if on_screen_text or subtitle else "unknown",
            "importance_score": round(importance, 3),
            "recommended_display_frame": best_frame,
            "confidence": round(confidence, 3),
            "warnings": _dedupe_texts(warnings),
            "model_output_format": "ocr_text_v1",
        }

    def analyze_shot(self, package: dict[str, Any]) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            if self.provider == "local_heuristic":
                return self._local_analyze_shot(package)
            if self._is_ocr_model():
                return self._ocr_analyze_shot(package)
            prompt = _read_prompt(
                "shot_analysis_prompt.txt",
                "你是视频笔记内容提取 Agent。请只根据关键帧文字和字幕输出可用于博客笔记的标签协议内容。",
            )
            raw = self._chat_text("shot_understanding", prompt, package, images=list(package.get("frames") or []))
            result = _parse_tagged_shot_analysis(raw, package)
            return _require_keys(
                "shot_understanding",
                result,
                [
                    "shot_id",
                    "visual_summary",
                    "subtitle_summary",
                    "merged_summary",
                    "key_entities",
                    "topic_tags",
                    "recommended_display_frame",
                    "confidence",
                    "warnings",
                ],
            )

        return self._with_retries("shot_understanding", run, package)

    def _local_summarize_chunk(self, chunk_id: str, cards: list[dict[str, Any]]) -> dict[str, Any]:
        tags: list[str] = []
        summaries: list[str] = []
        important = sorted(cards, key=lambda item: float(item.get("importance_score") or 0.0), reverse=True)[:5]
        for card in cards:
            tags.extend(str(tag) for tag in card.get("topic_tags", []))
            summary = str(card.get("merged_summary") or "").strip()
            if summary:
                summaries.append(summary)
        main_topics = _keywords(" ".join(tags + summaries), limit=5) or ["视频笔记"]
        return {
            "chunk_id": chunk_id,
            "shot_range": [cards[0]["shot_id"], cards[-1]["shot_id"]] if cards else [],
            "main_topics": main_topics,
            "summary": _compact_text(" ".join(summaries), 280) if summaries else "该片段没有提取到可用于笔记的文字内容。",
            "important_shots": [item["shot_id"] for item in important],
        }

    def summarize_chunk(self, chunk_id: str, cards: list[dict[str, Any]]) -> dict[str, Any]:
        payload = {"chunk_id": chunk_id, "cards": cards}

        def run() -> dict[str, Any]:
            if self.provider == "local_heuristic":
                return self._local_summarize_chunk(chunk_id, cards)
            prompt = (
                "你是博客笔记摘要压缩 Agent。请把提供的镜头卡片压缩成用于博客目录规划的简洁中文摘要。"
                "只整合镜头卡片中的 OCR、字幕和知识点，不要补充画面人物、构图或场景分析。"
                "只返回严格 JSON，字段为 chunk_id、shot_range、main_topics、summary、important_shots。"
                "main_topics、summary 等自然语言字段必须使用简体中文。"
            )
            result = self._chat_json("summary_reduce", prompt, payload)
            return _require_keys("summary_reduce", result, ["chunk_id", "shot_range", "main_topics", "summary", "important_shots"])

        return self._with_retries("summary_reduce", run, payload)

    def _local_write_chapter(self, chapter: dict[str, Any], cards: list[dict[str, Any]], global_summary: dict[str, Any]) -> dict[str, Any]:
        summaries = [str(card.get("merged_summary") or "").strip() for card in cards if card.get("merged_summary")]
        key_points = [_compact_text(text, 80) for text in summaries[:5]]
        representative = next((card.get("recommended_display_frame") for card in cards if card.get("recommended_display_frame")), None)
        body_source = " ".join(summaries)
        if body_source:
            body = _compact_text(body_source, 900)
        else:
            body = "本章节没有提取到足够的画面文字或字幕内容。"
        return {
            "chapter_id": chapter["chapter_id"],
            "title": chapter["title"],
            "representative_frame": representative,
            "body_markdown": body,
            "key_points": key_points or [str(chapter.get("summary") or "关键视频片段")],
            "referenced_shots": list(chapter.get("shot_ids") or []),
            "global_theme": global_summary.get("video_main_theme"),
        }

    def write_chapter(self, chapter: dict[str, Any], cards: list[dict[str, Any]], global_summary: dict[str, Any]) -> dict[str, Any]:
        payload = {"chapter": chapter, "cards": cards, "global_summary": global_summary}

        def run() -> dict[str, Any]:
            if self.provider == "local_heuristic":
                return self._local_write_chapter(chapter, cards, global_summary)
            prompt = _read_prompt(
                "chapter_writer_prompt.txt",
                "你是博客笔记章节写作 Agent。请为一个博客笔记章节返回严格 JSON，所有自然语言字段使用简体中文。",
            )
            result = self._chat_json("chapter_write", prompt, payload)
            return _require_keys(
                "chapter_write",
                result,
                ["chapter_id", "title", "representative_frame", "body_markdown", "key_points", "referenced_shots"],
            )

        return self._with_retries("chapter_write", run, payload)
