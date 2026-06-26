from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from ..paths import resolve_artifact_path, sanitize_name, to_project_path
from ..state import now_iso
from ..storage import append_jsonl, atomic_write_json, read_jsonl


SCHEMA_VERSION = "llm_monitor.v1"
SENSITIVE_KEY_PARTS = ("api_key", "authorization", "access_token", "refresh_token", "secret", "password", "bearer")


def _stable_json(value: Any) -> str:
    return json.dumps(_jsonable(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, set):
        return sorted(_jsonable(item) for item in value)
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, bytes):
        return {"type": "bytes", "length": len(value), "sha256": hashlib.sha256(value).hexdigest()}
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _redact_and_trim(value: Any, *, max_text_chars: int) -> Any:
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            text_key = str(key)
            lower_key = text_key.lower()
            if any(part in lower_key for part in SENSITIVE_KEY_PARTS):
                cleaned[text_key] = "***redacted***"
                continue
            cleaned[text_key] = _redact_and_trim(item, max_text_chars=max_text_chars)
        return cleaned
    if isinstance(value, list):
        return [_redact_and_trim(item, max_text_chars=max_text_chars) for item in value]
    if isinstance(value, tuple):
        return [_redact_and_trim(item, max_text_chars=max_text_chars) for item in value]
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, str):
        if value.startswith("data:") and ";base64," in value[:128]:
            prefix = value.split(",", 1)[0]
            return {
                "redacted": True,
                "reason": "image_base64",
                "data_url_prefix": prefix,
                "chars": len(value),
                "sha256": _sha256_text(value),
            }
        if len(value) > max_text_chars:
            return (
                value[:max_text_chars].rstrip()
                + f"... [truncated chars={len(value)} sha256={_sha256_text(value)}]"
            )
        return value
    return _jsonable(value)


def _bytes_len(value: Any) -> int:
    return len(_stable_json(value).encode("utf-8"))


def _top_level_keys(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return []
    return sorted(str(key) for key in value.keys())


def _list_count_summary(payload: Any) -> dict[str, int]:
    if not isinstance(payload, dict):
        return {}
    summary: dict[str, int] = {}
    for key, value in payload.items():
        if isinstance(value, list):
            summary[str(key)] = len(value)
    return summary


def _field_preview(value: Any, *, limit: int = 120) -> str | None:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        text = json.dumps(_jsonable(value), ensure_ascii=False)
    else:
        text = str(value)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return None
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."


def _error_category(error: Any) -> str | None:
    if not error:
        return None
    text = str(error).lower()
    if "timeout" in text or "timed out" in text:
        return "timeout"
    if "http" in text:
        return "http"
    if "urlopen" in text or "connection" in text or "network" in text:
        return "network"
    if "json" in text or "parse" in text or "tag" in text:
        return "parse_or_contract"
    if "missing" in text or "required" in text:
        return "contract"
    if "empty" in text:
        return "empty_output"
    return "unknown"


def _empty_output_fields(result: Any) -> list[str]:
    if not isinstance(result, dict):
        return []
    empty: list[str] = []
    for key, value in result.items():
        if value in (None, "", [], {}):
            empty.append(str(key))
    return empty[:20]


def _warning_count(result: Any) -> int:
    if not isinstance(result, dict):
        return 0
    warnings = result.get("warnings")
    if isinstance(warnings, list):
        return len(warnings)
    if isinstance(warnings, str) and warnings.strip():
        return 1
    return 0


class LLMMonitor:
    """Structured, local-file monitor for model function calls and provider calls."""

    def __init__(self, project_dir: str | Path, config: dict[str, Any] | None = None):
        settings = config if isinstance(config, dict) else {}
        self.project_dir = Path(project_dir)
        self.enabled = bool(settings.get("enabled", True))
        self.capture_payloads = bool(settings.get("capture_payloads", True))
        self.capture_raw_response = bool(settings.get("capture_raw_response", True))
        try:
            self.max_text_chars = int(settings.get("max_text_chars") or 50000)
        except (TypeError, ValueError):
            self.max_text_chars = 50000
        self.root = self.project_dir / "logs" / "llm_monitor"

    def begin_function_call(
        self,
        *,
        stage: str,
        model_role: str,
        provider: str,
        model: str | None,
        payload: dict[str, Any],
        attempt: int,
        max_retries: int,
    ) -> dict[str, Any]:
        return {
            "call_id": self._new_call_id("fn"),
            "record_type": "function_call",
            "stage": stage,
            "started_at": now_iso(),
            "model_role": model_role,
            "provider": provider,
            "model": model,
            "attempt": attempt,
            "max_retries": max_retries,
            "payload": payload,
            "raw_call_ids": [],
        }

    def finish_function_call(
        self,
        call: dict[str, Any] | None,
        *,
        elapsed_sec: float,
        result: dict[str, Any] | None = None,
        error: BaseException | str | None = None,
    ) -> None:
        if not self.enabled or not call:
            return
        self._safe_write_function_call(call, elapsed_sec=elapsed_sec, result=result, error=error)

    def record_provider_call(
        self,
        *,
        stage: str,
        model_role: str,
        provider: str,
        model: str | None,
        base_url: str,
        request_url: str,
        request_body: dict[str, Any],
        payload: dict[str, Any],
        system_prompt: str,
        response_instruction: str,
        images: list[str] | None,
        attached_image_count: int,
        json_response: bool,
        elapsed_sec: float,
        parent_call: dict[str, Any] | None = None,
        response_payload: Any | None = None,
        raw_content: str | None = None,
        error: BaseException | str | None = None,
    ) -> str | None:
        if not self.enabled:
            return None
        try:
            call_id = self._new_call_id("raw")
            status = "failure" if error else "success"
            images_info = self._image_inputs(images or [], attached_image_count)
            sanitized_payload = self._capture(payload)
            sanitized_request = self._capture(request_body)
            sanitized_response = self._capture(response_payload) if self.capture_raw_response else None
            raw_text = self._capture(raw_content) if self.capture_raw_response and raw_content is not None else None
            error_text = str(error) if error else None
            detail = {
                "schema_version": SCHEMA_VERSION,
                "call_id": call_id,
                "parent_call_id": parent_call.get("call_id") if parent_call else None,
                "record_type": "provider_call",
                "time": now_iso(),
                "classification": {
                    "function": stage,
                    "model_role": model_role,
                    "provider": provider,
                },
                "model": {
                    "provider": provider,
                    "name": model,
                    "role": model_role,
                    "base_url": base_url,
                },
                "request": {
                    "url": request_url,
                    "json_response": json_response,
                    "response_instruction": self._capture(response_instruction),
                    "system_prompt": self._capture(system_prompt),
                    "payload": sanitized_payload if self.capture_payloads else {"captured": False},
                    "payload_summary": self._payload_summary(payload),
                    "images": images_info,
                    "body": sanitized_request,
                },
                "response": {
                    "status": status,
                    "raw_content": raw_text,
                    "raw_content_chars": len(raw_content or ""),
                    "raw_content_sha256": _sha256_text(raw_content or "") if raw_content is not None else None,
                    "provider_payload": sanitized_response,
                    "usage": self._usage_from_response(response_payload),
                    "finish_reason": self._finish_reason(response_payload),
                },
                "reliability": {
                    "status": status,
                    "http_ok": not error and response_payload is not None,
                    "non_empty_output": bool(raw_content and raw_content.strip()),
                    "json_requested": json_response,
                    "error_category": _error_category(error_text),
                },
                "stability": {
                    "elapsed_sec": round(elapsed_sec, 3),
                    "error": error_text,
                },
            }
            self._write_record(stage, detail)
            if parent_call is not None:
                parent_call.setdefault("raw_call_ids", []).append(call_id)
            return call_id
        except Exception:
            return None

    def _safe_write_function_call(
        self,
        call: dict[str, Any],
        *,
        elapsed_sec: float,
        result: dict[str, Any] | None,
        error: BaseException | str | None,
    ) -> None:
        try:
            status = "failure" if error else "success"
            payload = call.get("payload") or {}
            error_text = str(error) if error else None
            output = result or {}
            detail = {
                "schema_version": SCHEMA_VERSION,
                "call_id": call["call_id"],
                "record_type": "function_call",
                "time": now_iso(),
                "started_at": call.get("started_at"),
                "classification": {
                    "function": call.get("stage"),
                    "model_role": call.get("model_role"),
                    "provider": call.get("provider"),
                },
                "model": {
                    "provider": call.get("provider"),
                    "name": call.get("model"),
                    "role": call.get("model_role"),
                },
                "attempt": {
                    "number": call.get("attempt"),
                    "max_retries": call.get("max_retries"),
                    "raw_call_ids": call.get("raw_call_ids") or [],
                },
                "input": {
                    "payload": self._capture(payload) if self.capture_payloads else {"captured": False},
                    "summary": self._payload_summary(payload),
                },
                "output": {
                    "status": status,
                    "result": self._capture(output) if result is not None else None,
                    "summary": self._result_summary(output),
                },
                "reliability": {
                    "status": status,
                    "input_ok": bool(payload),
                    "output_ok": status == "success" and bool(output),
                    "output_empty_fields": _empty_output_fields(output),
                    "warning_count": _warning_count(output),
                    "error_category": _error_category(error_text),
                },
                "stability": {
                    "elapsed_sec": round(elapsed_sec, 3),
                    "attempt": call.get("attempt"),
                    "retry_count_before_success": max(0, int(call.get("attempt") or 1) - 1) if status == "success" else None,
                    "error": error_text,
                },
            }
            self._write_record(str(call.get("stage") or "unknown"), detail)
        except Exception:
            return

    def _write_record(self, stage: str, detail: dict[str, Any]) -> None:
        stage_dir = self._stage_dir(stage)
        details_dir = stage_dir / "details"
        detail_path = details_dir / f"{detail['call_id']}.json"
        detail_relpath = to_project_path(self.project_dir, detail_path)
        index_record = self._index_record(detail, detail_relpath)
        atomic_write_json(detail_path, detail)
        append_jsonl(stage_dir / "calls.jsonl", index_record)
        append_jsonl(self.root / "index.jsonl", index_record)
        self._write_health_snapshot()

    def _index_record(self, detail: dict[str, Any], detail_relpath: str) -> dict[str, Any]:
        reliability = detail.get("reliability") or {}
        stability = detail.get("stability") or {}
        classification = detail.get("classification") or {}
        model = detail.get("model") or {}
        input_summary = ((detail.get("input") or {}).get("summary") or (detail.get("request") or {}).get("payload_summary") or {})
        output_summary = ((detail.get("output") or {}).get("summary") or {})
        return {
            "schema_version": SCHEMA_VERSION,
            "time": detail.get("time"),
            "call_id": detail.get("call_id"),
            "parent_call_id": detail.get("parent_call_id"),
            "record_type": detail.get("record_type"),
            "function": classification.get("function"),
            "model_role": classification.get("model_role"),
            "provider": model.get("provider"),
            "model": model.get("name"),
            "status": reliability.get("status"),
            "error_category": reliability.get("error_category"),
            "elapsed_sec": stability.get("elapsed_sec"),
            "input_summary": input_summary,
            "output_summary": output_summary,
            "detail": detail_relpath,
        }

    def _write_health_snapshot(self) -> None:
        try:
            rows = read_jsonl(self.root / "index.jsonl")
            by_function: dict[str, dict[str, Any]] = {}
            total_elapsed = 0.0
            elapsed_count = 0
            for row in rows:
                function = str(row.get("function") or "unknown")
                slot = by_function.setdefault(
                    function,
                    {
                        "records": 0,
                        "function_calls": 0,
                        "provider_calls": 0,
                        "success": 0,
                        "failure": 0,
                        "elapsed_sec_total": 0.0,
                        "last_status": None,
                        "last_error_category": None,
                    },
                )
                slot["records"] += 1
                if row.get("record_type") == "function_call":
                    slot["function_calls"] += 1
                if row.get("record_type") == "provider_call":
                    slot["provider_calls"] += 1
                if row.get("status") == "success":
                    slot["success"] += 1
                if row.get("status") == "failure":
                    slot["failure"] += 1
                elapsed = row.get("elapsed_sec")
                if isinstance(elapsed, (int, float)):
                    slot["elapsed_sec_total"] += float(elapsed)
                    total_elapsed += float(elapsed)
                    elapsed_count += 1
                slot["last_status"] = row.get("status")
                slot["last_error_category"] = row.get("error_category")
            for slot in by_function.values():
                records = max(1, int(slot["records"]))
                slot["success_rate"] = round(float(slot["success"]) / records, 4)
                slot["elapsed_sec_avg"] = round(float(slot["elapsed_sec_total"]) / records, 3)
                del slot["elapsed_sec_total"]
            payload = {
                "schema_version": SCHEMA_VERSION,
                "updated_at": now_iso(),
                "records": len(rows),
                "success": sum(1 for row in rows if row.get("status") == "success"),
                "failure": sum(1 for row in rows if row.get("status") == "failure"),
                "elapsed_sec_avg": round(total_elapsed / elapsed_count, 3) if elapsed_count else 0.0,
                "by_function": by_function,
            }
            atomic_write_json(self.root / "health.json", payload)
        except Exception:
            return

    def _stage_dir(self, stage: str) -> Path:
        return self.root / "by_function" / sanitize_name(stage)

    def _capture(self, value: Any) -> Any:
        return _redact_and_trim(value, max_text_chars=self.max_text_chars)

    def _payload_summary(self, payload: Any) -> dict[str, Any]:
        summary: dict[str, Any] = {
            "top_level_keys": _top_level_keys(payload),
            "bytes": _bytes_len(payload),
            "sha256": _sha256_text(_stable_json(payload)),
            "list_counts": _list_count_summary(payload),
        }
        if isinstance(payload, dict):
            identifiers: dict[str, Any] = {}
            for key in ("shot_id", "chunk_id", "chapter_id", "task"):
                if key in payload:
                    identifiers[key] = payload.get(key)
            chapter = payload.get("chapter")
            if isinstance(chapter, dict):
                for key in ("chapter_id", "title", "shot_count"):
                    if key in chapter:
                        identifiers[f"chapter.{key}"] = chapter.get(key)
            if identifiers:
                summary["identifiers"] = identifiers
            preview_source = payload.get("subtitle_text") or payload.get("summary") or payload.get("text")
            preview = _field_preview(preview_source)
            if preview:
                summary["text_preview"] = preview
        return summary

    def _result_summary(self, result: Any) -> dict[str, Any]:
        summary: dict[str, Any] = {
            "top_level_keys": _top_level_keys(result),
            "bytes": _bytes_len(result),
            "sha256": _sha256_text(_stable_json(result)),
            "empty_fields": _empty_output_fields(result),
            "warning_count": _warning_count(result),
        }
        if isinstance(result, dict):
            preview_source = (
                result.get("merged_summary")
                or result.get("summary")
                or result.get("video_main_theme")
                or result.get("title")
                or result.get("body_markdown")
                or result.get("error")
            )
            preview = _field_preview(preview_source)
            if preview:
                summary["text_preview"] = preview
        return summary

    def _image_inputs(self, images: list[str], attached_image_count: int) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        for image in images:
            path = resolve_artifact_path(self.project_dir, image)
            exists = bool(path and path.exists())
            size = path.stat().st_size if path and exists else None
            items.append(
                {
                    "path": str(image),
                    "resolved": to_project_path(self.project_dir, path) if path else None,
                    "exists": exists,
                    "size_bytes": size,
                }
            )
        return {
            "requested_count": len(images),
            "attached_count": attached_image_count,
            "missing_count": sum(1 for item in items if not item["exists"]),
            "items": items,
        }

    def _usage_from_response(self, response_payload: Any) -> Any:
        if isinstance(response_payload, dict):
            return response_payload.get("usage")
        return None

    def _finish_reason(self, response_payload: Any) -> Any:
        if not isinstance(response_payload, dict):
            return None
        choices = response_payload.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                return first.get("finish_reason")
        return None

    def _new_call_id(self, prefix: str) -> str:
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S%f")
        return f"{prefix}_{stamp}_{uuid.uuid4().hex[:8]}"
