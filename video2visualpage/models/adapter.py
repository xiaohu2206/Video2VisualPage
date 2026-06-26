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
from ..monitoring import LLMMonitor
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


PROMPT_SIGNATURE_VERSION = "2026-06-26-outline-planner-agent"
PROMPT_SIGNATURE_FILES = (
    "shot_analysis_prompt.txt",
    "global_outline_prompt.txt",
    "outline_planner_prompt.txt",
    "chapter_subsection_prompt.txt",
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


def _extract_tag_blocks(text: str, tag: str) -> list[tuple[dict[str, str], str]]:
    pattern = rf"<\s*{re.escape(tag)}(?P<attrs>[^>]*)>(?P<body>.*?)<\s*/\s*{re.escape(tag)}\s*>"
    blocks: list[tuple[dict[str, str], str]] = []
    for match in re.finditer(pattern, text, flags=re.DOTALL | re.IGNORECASE):
        attrs: dict[str, str] = {}
        raw_attrs = match.group("attrs") or ""
        for attr in re.finditer(r"([A-Za-z_][\w:-]*)\s*=\s*([\"'])(.*?)\2", raw_attrs, flags=re.DOTALL):
            attrs[attr.group(1).lower()] = attr.group(3).strip()
        blocks.append((attrs, match.group("body").strip()))
    return blocks


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


def _clean_outline_title(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value)).strip()
    text = re.sub(r"^\s*(?:[-*+]|\d+[.)、:]|第\s*\d+\s*(?:部分|章|节)[：:、]?|[一二三四五六七八九十]+[、.：:])\s*", "", text)
    return text.strip().strip("`#*- ")


def _split_chunk_ids(value: str) -> list[str]:
    return _dedupe_texts([item for item in re.split(r"[,，、;\s]+", value) if item.strip()])


def _parse_global_outline_tags(text: str, valid_chunk_ids: list[str]) -> dict[str, Any]:
    valid_chunks = set(valid_chunk_ids)
    warnings: list[str] = []
    theme = _clean_outline_title(_tag_text(text, "THEME"))
    if not theme:
        raise ValueError("global_outline tagged response missing required THEME tag")

    style = _tag_text(text, "STYLE", "structured_report").strip() or "structured_report"
    sections: list[dict[str, Any]] = []
    seen_titles: set[str] = set()
    invalid_chunk_refs: list[str] = []

    for attrs, body in _extract_tag_blocks(text, "SECTION"):
        title = _clean_outline_title(body)
        if not title:
            continue
        if title in seen_titles:
            warnings.append(f"duplicate_section_removed:{title}")
            continue
        raw_chunks = attrs.get("chunks") or attrs.get("source_chunks") or ""
        source_chunks: list[str] = []
        for chunk_id in _split_chunk_ids(raw_chunks):
            if chunk_id in valid_chunks:
                source_chunks.append(chunk_id)
            else:
                invalid_chunk_refs.append(chunk_id)
        seen_titles.add(title)
        sections.append({"title": title, "source_chunks": _dedupe_texts(source_chunks)})

    if invalid_chunk_refs:
        warnings.append(f"invalid_section_chunks_removed:{','.join(_dedupe_texts(invalid_chunk_refs))}")
    if not sections:
        raise ValueError("global_outline tagged response missing valid SECTION tags")
    if len(sections) > 6:
        warnings.append(f"too_many_sections_truncated:{len(sections)}")
        sections = sections[:6]

    return {
        "video_main_theme": theme,
        "narrative_style": style,
        "sections": sections,
        "warnings": warnings,
    }


def _has_keep_tag(text: str) -> bool:
    return bool(re.search(r"<\s*KEEP\s*/\s*>", text, flags=re.IGNORECASE))


def _split_shot_refs(value: str) -> list[str]:
    refs: list[str] = []
    for token in re.split(r"[,，、;；\s]+", value):
        cleaned = token.strip().strip("\"'`")
        if cleaned:
            refs.append(cleaned)
    return refs


def _expand_shot_refs(raw_refs: str, valid_shot_ids: list[str]) -> tuple[list[str], list[str]]:
    shot_index = {shot_id: index for index, shot_id in enumerate(valid_shot_ids)}
    expanded: list[str] = []
    invalid: list[str] = []
    for token in _split_shot_refs(raw_refs):
        if token in shot_index:
            expanded.append(token)
            continue
        if "-" in token:
            start, end = [part.strip() for part in token.split("-", 1)]
            if start in shot_index and end in shot_index:
                start_index = shot_index[start]
                end_index = shot_index[end]
                low = min(start_index, end_index)
                high = max(start_index, end_index)
                expanded.extend(valid_shot_ids[low : high + 1])
                continue
        invalid.append(token)
    return _dedupe_texts(expanded), _dedupe_texts(invalid)


def _parse_chapter_subsection_tags(text: str, valid_shot_ids: list[str], *, max_subsections: int = 5) -> dict[str, Any]:
    warnings: list[str] = []
    subsections: list[dict[str, Any]] = []
    invalid_refs: list[str] = []
    seen_titles: set[str] = set()
    max_count = max(1, max_subsections)

    for attrs, body in _extract_tag_blocks(text, "SUB"):
        title = _compact_text(_clean_outline_title(body), 24)
        if not title:
            continue
        if title in seen_titles:
            warnings.append(f"duplicate_subsection_removed:{title}")
            continue
        shot_refs = attrs.get("shots") or attrs.get("shot_ids") or ""
        shot_ids, invalid = _expand_shot_refs(shot_refs, valid_shot_ids)
        invalid_refs.extend(invalid)
        if not shot_ids:
            warnings.append(f"empty_subsection_removed:{title}")
            continue
        seen_titles.add(title)
        subsections.append({"title": title, "shot_ids": shot_ids})
        if len(subsections) >= max_count:
            break

    if invalid_refs:
        warnings.append(f"invalid_subsection_shots_removed:{','.join(_dedupe_texts(invalid_refs))}")
    if len(subsections) >= max_count and len(_extract_tag_blocks(text, "SUB")) > max_count:
        warnings.append(f"too_many_subsections_truncated:{len(_extract_tag_blocks(text, 'SUB'))}")
    if subsections:
        return {"mode": "split", "subsections": subsections, "warnings": warnings}
    if _has_keep_tag(text):
        return {"mode": "keep", "subsections": [], "warnings": warnings}
    raise ValueError("chapter_subsections tagged response missing valid KEEP or SUB tags")


def _next_tag_body_after(text: str, offset: int, tag: str, stop_tag: str | None = None) -> str:
    stop_index = len(text)
    if stop_tag:
        stop_match = re.search(rf"<\s*{re.escape(stop_tag)}(?:\s+[^>]*)?>", text[offset:], flags=re.IGNORECASE)
        if stop_match:
            stop_index = offset + stop_match.start()
    snippet = text[offset:stop_index]
    return _tag_text(snippet, tag)


def _parse_outline_planner_tags(text: str, valid_shot_ids: list[str], *, max_chapters: int = 8) -> dict[str, Any]:
    warnings: list[str] = []
    chapters: list[dict[str, Any]] = []
    invalid_refs: list[str] = []
    seen_titles: set[str] = set()
    max_count = max(1, max_chapters)
    chapter_pattern = re.compile(
        r"<\s*CHAPTER(?P<attrs>[^>]*)>(?P<body>.*?)<\s*/\s*CHAPTER\s*>",
        flags=re.DOTALL | re.IGNORECASE,
    )

    for match in chapter_pattern.finditer(text):
        raw_attrs = match.group("attrs") or ""
        attrs: dict[str, str] = {}
        for attr in re.finditer(r"([A-Za-z_][\w:-]*)\s*=\s*([\"'])(.*?)\2", raw_attrs, flags=re.DOTALL):
            attrs[attr.group(1).lower()] = attr.group(3).strip()

        title = _compact_text(_clean_outline_title(match.group("body")), 28)
        if not title:
            continue
        if title in seen_titles:
            warnings.append(f"duplicate_outline_chapter_removed:{title}")
            continue
        shot_refs = attrs.get("shots") or attrs.get("shot_ids") or ""
        shot_ids, invalid = _expand_shot_refs(shot_refs, valid_shot_ids)
        invalid_refs.extend(invalid)
        if not shot_ids:
            warnings.append(f"empty_outline_chapter_removed:{title}")
            continue
        summary = _compact_text(_next_tag_body_after(text, match.end(), "SUMMARY", stop_tag="CHAPTER"), 80)
        seen_titles.add(title)
        chapters.append({"title": title, "summary": summary or title, "shot_ids": shot_ids})
        if len(chapters) >= max_count:
            break

    if invalid_refs:
        warnings.append(f"invalid_outline_shots_removed:{','.join(_dedupe_texts(invalid_refs))}")
    total_chapter_tags = len(list(chapter_pattern.finditer(text)))
    if len(chapters) >= max_count and total_chapter_tags > max_count:
        warnings.append(f"too_many_outline_chapters_truncated:{total_chapter_tags}")
    if not chapters:
        raise ValueError("outline_planner tagged response missing valid CHAPTER tags")

    return {
        "title": _compact_text(_clean_outline_title(_tag_text(text, "TITLE")), 32),
        "description": _compact_text(_tag_text(text, "DESCRIPTION"), 160),
        "chapters": chapters,
        "warnings": warnings,
    }


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
        monitoring_config = config.get("llm_monitoring", {}) if isinstance(config, dict) else {}
        self.monitor = LLMMonitor(self.project_dir, monitoring_config)
        self._active_llm_function_call: dict[str, Any] | None = None
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
            call = self.monitor.begin_function_call(
                stage=stage,
                model_role=self.model_role,
                provider=self.provider,
                model=self.model or None,
                payload=payload,
                attempt=attempt,
                max_retries=self.max_retries,
            )
            previous_call = self._active_llm_function_call
            self._active_llm_function_call = call
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
                self.monitor.finish_function_call(call, elapsed_sec=elapsed, result=result)
                self._log_call(stage, payload, result, attempt=attempt, elapsed=elapsed)
                return result
            except Exception as exc:  # noqa: BLE001 - adapter boundary logs all failures.
                elapsed = time.perf_counter() - started
                last_error = exc
                if self.progress:
                    self.progress.model_failure(stage, attempt, elapsed, exc)
                self.monitor.finish_function_call(call, elapsed_sec=elapsed, result={"error": str(exc)}, error=exc)
                self._log_call(stage, payload, {"error": str(exc)}, attempt=attempt, elapsed=elapsed)
                if attempt <= self.max_retries:
                    time.sleep(self.retry_delay_sec)
            finally:
                self._active_llm_function_call = previous_call
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
        if stage == "global_outline":
            chunks = list(payload.get("chunks") or [])
            chunk_ids = [str(chunk.get("chunk_id")) for chunk in chunks if isinstance(chunk, dict) and chunk.get("chunk_id")]
            preview = ",".join(chunk_ids[:4])
            suffix = "..." if len(chunk_ids) > 4 else ""
            return f"chunks={len(chunks)}; ids={preview}{suffix}"
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
        request_url = self._chat_completions_url()
        request = urllib.request.Request(
            request_url,
            data=data,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        raw_started = time.perf_counter()
        response_payload: Any | None = None
        raw_content: Any | None = None

        def record_provider_call(*, error: BaseException | str | None = None) -> None:
            self.monitor.record_provider_call(
                stage=stage,
                model_role=self.model_role,
                provider=self.provider,
                model=self.model or None,
                base_url=self.base_url,
                request_url=request_url,
                request_body=body,
                payload=payload,
                system_prompt=system_prompt,
                response_instruction=response_instruction,
                images=images,
                attached_image_count=len(image_parts),
                json_response=json_response,
                elapsed_sec=time.perf_counter() - raw_started,
                parent_call=self._active_llm_function_call,
                response_payload=response_payload,
                raw_content=raw_content if isinstance(raw_content, str) else None,
                error=error,
            )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout_sec) as response:
                response_payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace")
            response_payload = {"http_status": exc.code, "body": body_text}
            raw_content = body_text
            error_message = f"LLM API request failed with HTTP {exc.code}: {body_text[:500]}"
            record_provider_call(error=error_message)
            raise RuntimeError(error_message) from exc
        except urllib.error.URLError as exc:
            error_message = f"LLM API request failed: {exc}"
            record_provider_call(error=error_message)
            raise RuntimeError(error_message) from exc

        try:
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
        except RuntimeError as exc:
            record_provider_call(error=exc)
            raise

        record_provider_call()
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

    def _global_outline_payload(self, summaries: list[dict[str, Any]]) -> dict[str, Any]:
        chunks: list[dict[str, Any]] = []
        for summary in summaries:
            raw_topics = summary.get("main_topics") or []
            if isinstance(raw_topics, str):
                raw_topics = [raw_topics]
            topics = [str(topic) for topic in list(raw_topics) if str(topic).strip()]
            chunks.append(
                {
                    "chunk_id": str(summary.get("chunk_id") or ""),
                    "shot_range": summary.get("shot_range") or "",
                    "main_topics": topics[:5],
                    "summary": _compact_text(str(summary.get("summary") or ""), 240),
                }
            )
        return {"chunks": chunks}

    def _local_summarize_global_outline(self, summaries: list[dict[str, Any]]) -> dict[str, Any]:
        topics: list[str] = []
        sections: list[dict[str, Any]] = []
        seen_titles: set[str] = set()
        for summary in summaries:
            chunk_id = str(summary.get("chunk_id") or "").strip()
            raw_topics = summary.get("main_topics") or []
            if isinstance(raw_topics, str):
                raw_topics = [raw_topics]
            chunk_topics = [str(topic).strip() for topic in list(raw_topics) if str(topic).strip()]
            topics.extend(chunk_topics)
            title = _clean_outline_title(chunk_topics[0] if chunk_topics else str(summary.get("summary") or ""))
            if not title or title in seen_titles:
                continue
            seen_titles.add(title)
            sections.append({"title": _compact_text(title, 28), "source_chunks": [chunk_id] if chunk_id else []})
            if len(sections) >= 6:
                break
        unique_topics = _dedupe_texts(topics)
        if not sections:
            sections = [{"title": "内容概览", "source_chunks": []}]
        return {
            "video_main_theme": _compact_text(unique_topics[0], 24) if unique_topics else "视频博客笔记",
            "narrative_style": "structured_report",
            "sections": sections,
            "warnings": [],
        }

    def summarize_global_outline(self, summaries: list[dict[str, Any]]) -> dict[str, Any]:
        payload = self._global_outline_payload(summaries)
        valid_chunk_ids = [str(chunk.get("chunk_id")) for chunk in payload["chunks"] if chunk.get("chunk_id")]

        def run() -> dict[str, Any]:
            if self.provider == "local_heuristic":
                return self._local_summarize_global_outline(summaries)
            prompt = _read_prompt(
                "global_outline_prompt.txt",
                (
                    "你是 Global Outline Agent。根据按时间顺序排列的 chunk 摘要生成视频级大纲。"
                    "合并重复主题，按视频时间顺序组织 SECTION。不要输出 JSON。"
                    "只输出 <THEME>、<STYLE>、<SECTION chunks=\"chunk_001\"> 标签。"
                    "THEME 不超过 24 个中文字符，SECTION 标题不超过 28 个中文字符，SECTION 最多 6 个。"
                ),
            )
            raw = self._chat_text("global_outline", prompt, payload)
            return _parse_global_outline_tags(raw, valid_chunk_ids)

        return self._with_retries("global_outline", run, payload)

    def _outline_planner_payload(
        self,
        global_summary: dict[str, Any],
        chunk_summaries: list[dict[str, Any]],
        shot_briefs: list[dict[str, Any]],
        *,
        max_chapters: int,
    ) -> dict[str, Any]:
        chunks: list[dict[str, Any]] = []
        for summary in chunk_summaries:
            raw_topics = summary.get("main_topics") or []
            if isinstance(raw_topics, str):
                raw_topics = [raw_topics]
            chunks.append(
                {
                    "chunk_id": str(summary.get("chunk_id") or ""),
                    "shot_range": summary.get("shot_range") or "",
                    "main_topics": [str(topic) for topic in list(raw_topics) if str(topic).strip()][:5],
                    "summary": _compact_text(str(summary.get("summary") or ""), 260),
                }
            )
        return {
            "global_summary": {
                "video_main_theme": str(global_summary.get("video_main_theme") or ""),
                "main_sections": list(global_summary.get("main_sections") or []),
                "section_sources": list(global_summary.get("section_sources") or []),
                "important_shots": list(global_summary.get("important_shots") or []),
            },
            "chunk_summaries": chunks,
            "shot_briefs": shot_briefs,
            "max_chapters": max_chapters,
        }

    def _local_plan_outline(
        self,
        global_summary: dict[str, Any],
        chunk_summaries: list[dict[str, Any]],
        shot_briefs: list[dict[str, Any]],
        *,
        max_chapters: int,
    ) -> dict[str, Any]:
        shot_ids = [str(shot.get("shot_id")) for shot in shot_briefs if shot.get("shot_id")]
        if not shot_ids:
            raise ValueError("outline_planner requires at least one shot brief")

        chapters: list[dict[str, Any]] = []
        seen_ranges: set[tuple[str, str]] = set()
        for summary in chunk_summaries[:max(1, max_chapters)]:
            chunk_id = str(summary.get("chunk_id") or "").strip()
            raw_topics = summary.get("main_topics") or []
            if isinstance(raw_topics, str):
                raw_topics = [raw_topics]
            title_source = next((str(topic).strip() for topic in raw_topics if str(topic).strip()), "")
            title = _compact_text(_clean_outline_title(title_source or str(summary.get("summary") or "")), 28)
            range_value = summary.get("shot_range")
            range_ids: list[str] = []
            if isinstance(range_value, (list, tuple)):
                range_ids = [str(item) for item in range_value if str(item) in shot_ids]
            elif isinstance(range_value, str):
                range_ids, _ = _expand_shot_refs(range_value, shot_ids)
            if not range_ids and chunk_id:
                range_ids = [shot_id for shot_id in shot_ids if shot_id.startswith(chunk_id)]
            if not range_ids:
                continue
            key = (range_ids[0], range_ids[-1])
            if key in seen_ranges:
                continue
            seen_ranges.add(key)
            chapters.append(
                {
                    "title": title or f"Part {len(chapters) + 1}",
                    "summary": _compact_text(str(summary.get("summary") or title), 80),
                    "shot_ids": range_ids,
                }
            )

        if not chapters:
            count = min(max(1, max_chapters), max(1, (len(shot_ids) + 7) // 8))
            per_group = max(1, (len(shot_ids) + count - 1) // count)
            sections = [str(section) for section in list(global_summary.get("main_sections") or []) if str(section).strip()]
            for index in range(count):
                group = shot_ids[index * per_group : (index + 1) * per_group]
                if not group:
                    continue
                title = sections[index] if index < len(sections) else f"Part {index + 1}"
                chapters.append({"title": title, "summary": title, "shot_ids": group})

        return {
            "title": str(global_summary.get("video_main_theme") or ""),
            "description": "",
            "chapters": chapters[:max(1, max_chapters)],
            "warnings": ["local_outline_planner"],
        }

    def plan_outline(
        self,
        global_summary: dict[str, Any],
        chunk_summaries: list[dict[str, Any]],
        shot_briefs: list[dict[str, Any]],
        *,
        max_chapters: int,
    ) -> dict[str, Any]:
        payload = self._outline_planner_payload(global_summary, chunk_summaries, shot_briefs, max_chapters=max_chapters)
        valid_shot_ids = [str(shot.get("shot_id")) for shot in shot_briefs if shot.get("shot_id")]

        def run() -> dict[str, Any]:
            if self.provider == "local_heuristic":
                return self._local_plan_outline(global_summary, chunk_summaries, shot_briefs, max_chapters=max_chapters)
            prompt = _read_prompt(
                "outline_planner_prompt.txt",
                (
                    "You are an Outline Planner Agent. Plan first-level report chapters from the full video outline context. "
                    "Only output TITLE, DESCRIPTION, CHAPTER and SUMMARY tags. Do not output JSON or Markdown."
                ),
            )
            raw = self._chat_text("outline_planner", prompt, payload)
            return _parse_outline_planner_tags(raw, valid_shot_ids, max_chapters=max_chapters)

        return self._with_retries("outline_planner", run, payload)

    def _chapter_subsection_payload(
        self,
        chapter: dict[str, Any],
        cards: list[dict[str, Any]],
        *,
        target_subsections: int,
        max_subsections: int,
    ) -> dict[str, Any]:
        shots: list[dict[str, Any]] = []
        for card in cards:
            raw_tags = card.get("topic_tags") or []
            if isinstance(raw_tags, str):
                raw_tags = [raw_tags]
            raw_entities = card.get("key_entities") or []
            if isinstance(raw_entities, str):
                raw_entities = [raw_entities]
            shots.append(
                {
                    "shot_id": str(card.get("shot_id") or ""),
                    "start_sec": card.get("start_sec"),
                    "end_sec": card.get("end_sec"),
                    "importance_score": card.get("importance_score"),
                    "topic_tags": [str(tag) for tag in list(raw_tags) if str(tag).strip()][:5],
                    "key_entities": [str(entity) for entity in list(raw_entities) if str(entity).strip()][:5],
                    "text": _compact_text(str(card.get("merged_summary") or ""), 90),
                }
            )
        return {
            "chapter": {
                "chapter_id": str(chapter.get("chapter_id") or ""),
                "title": str(chapter.get("title") or ""),
                "summary": _compact_text(str(chapter.get("summary") or ""), 180),
                "shot_count": len(shots),
                "target_subsections": target_subsections,
                "max_subsections": max_subsections,
            },
            "shots": shots,
        }

    def _local_plan_chapter_subsections(
        self,
        chapter: dict[str, Any],
        cards: list[dict[str, Any]],
        *,
        target_subsections: int,
        max_subsections: int,
    ) -> dict[str, Any]:
        count = min(max_subsections, max(2, target_subsections), max(1, len(cards) // 2))
        if len(cards) < 2 or count < 2:
            return {"mode": "keep", "subsections": [], "warnings": ["local_subsection_keep"]}
        per_group = max(1, (len(cards) + count - 1) // count)
        subsections: list[dict[str, Any]] = []
        for index in range(count):
            group = cards[index * per_group : (index + 1) * per_group]
            if not group:
                continue
            tokens: list[str] = []
            for card in group:
                raw_tags = card.get("topic_tags") or []
                if isinstance(raw_tags, str):
                    raw_tags = [raw_tags]
                tokens.extend(str(tag) for tag in list(raw_tags) if str(tag).strip())
            if tokens:
                title = _compact_text(_clean_outline_title(_dedupe_texts(tokens)[0]), 24)
            else:
                summary = " ".join(str(card.get("merged_summary") or "") for card in group)
                title = _compact_text((_keywords(summary, limit=1) or [f"小节 {index + 1}"])[0], 24)
            subsections.append({"title": title or f"小节 {index + 1}", "shot_ids": [str(card.get("shot_id")) for card in group]})
        if len(subsections) < 2:
            return {"mode": "keep", "subsections": [], "warnings": ["local_subsection_single_group"]}
        return {"mode": "split", "subsections": subsections, "warnings": []}

    def plan_chapter_subsections(
        self,
        chapter: dict[str, Any],
        cards: list[dict[str, Any]],
        *,
        target_subsections: int,
        max_subsections: int,
    ) -> dict[str, Any]:
        payload = self._chapter_subsection_payload(
            chapter,
            cards,
            target_subsections=target_subsections,
            max_subsections=max_subsections,
        )
        valid_shot_ids = [str(card.get("shot_id")) for card in cards if card.get("shot_id")]

        def run() -> dict[str, Any]:
            if self.provider == "local_heuristic":
                return self._local_plan_chapter_subsections(
                    chapter,
                    cards,
                    target_subsections=target_subsections,
                    max_subsections=max_subsections,
                )
            prompt = _read_prompt(
                "chapter_subsection_prompt.txt",
                (
                    "你是 Chapter Subsection Agent。判断当前一级章节是否需要二级小节。"
                    "如果已经足够细，只输出 <KEEP/>。如果需要拆分，只输出 "
                    "<SUB shots=\"shot_001-shot_006\">小节标题</SUB> 标签。不要输出 JSON、Markdown 或解释。"
                ),
            )
            raw = self._chat_text("chapter_subsections", prompt, payload)
            return _parse_chapter_subsection_tags(raw, valid_shot_ids, max_subsections=max_subsections)

        return self._with_retries("chapter_subsections", run, payload)

    def _local_write_chapter(self, chapter: dict[str, Any], cards: list[dict[str, Any]], global_summary: dict[str, Any]) -> dict[str, Any]:
        summaries = [str(card.get("merged_summary") or "").strip() for card in cards if card.get("merged_summary")]
        key_points = [_compact_text(text, 80) for text in summaries[:5]]
        representative = next((card.get("recommended_display_frame") for card in cards if card.get("recommended_display_frame")), None)
        cards_by_id = {str(card.get("shot_id")): card for card in cards}
        subsections = list(chapter.get("subsections") or [])
        if subsections:
            parts: list[str] = []
            for subsection in subsections:
                subsection_cards = [cards_by_id[shot_id] for shot_id in subsection.get("shot_ids", []) if shot_id in cards_by_id]
                subsection_summaries = [str(card.get("merged_summary") or "").strip() for card in subsection_cards if card.get("merged_summary")]
                section_body = _compact_text(" ".join(subsection_summaries), 360) if subsection_summaries else "本小节没有提取到足够的文字内容。"
                parts.append(f"## {subsection.get('title')}\n\n{section_body}")
            body = "\n\n".join(parts)
        else:
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
