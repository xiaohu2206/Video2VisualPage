from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import requests

from .asr_base import BaseASR, ProgressCallback


API_BASE_URL = "https://member.bilibili.com/x/bcut/rubick-interface"
API_REQ_UPLOAD = API_BASE_URL + "/resource/create"
API_COMMIT_UPLOAD = API_BASE_URL + "/resource/create/complete"
API_CREATE_TASK = API_BASE_URL + "/task"
API_QUERY_RESULT = API_BASE_URL + "/task/result"
BCUT_MODEL_ID = "8"


class BcutASRError(RuntimeError):
    pass


def _compact_json(value: Any, limit: int = 1000) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False)
    except Exception:
        text = str(value)
    return text[:limit] + "..." if len(text) > limit else text


def _decode_response(resp: requests.Response, action: str) -> dict[str, Any]:
    try:
        resp.raise_for_status()
    except Exception as exc:
        body = str(getattr(resp, "text", "") or "")[:1000]
        raise BcutASRError(f"Bcut ASR {action} HTTP failed: status={resp.status_code}; body={body}") from exc

    try:
        data = resp.json()
    except Exception as exc:
        body = str(getattr(resp, "text", "") or "")[:1000]
        raise BcutASRError(f"Bcut ASR {action} returned non-JSON response: status={resp.status_code}; body={body}") from exc

    if not isinstance(data, dict):
        raise BcutASRError(f"Bcut ASR {action} returned unexpected JSON: {_compact_json(data)}")
    return data


def _require_data(payload: dict[str, Any], action: str) -> Any:
    if "data" in payload and payload["data"] is not None:
        return payload["data"]
    code = payload.get("code")
    message = payload.get("message") or payload.get("msg")
    parts = ["missing data field"]
    if code is not None:
        parts.append(f"code={code}")
    if message:
        parts.append(f"message={message}")
    raise BcutASRError(f"Bcut ASR {action} failed: {', '.join(parts)}; response={_compact_json(payload)}")


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _bcut_requests_session() -> requests.Session:
    session = requests.Session()
    session.trust_env = _env_flag("BCUT_ASR_TRUST_ENV", default=False)
    return session


class BcutASR(BaseASR):
    """Bcut online speech recognition client."""

    headers = {
        "User-Agent": "Bilibili/1.0.0 (https://www.bilibili.com)",
        "Content-Type": "application/json",
    }

    def __init__(self, audio_path: str | bytes, *, use_cache: bool = False, cache_dir: str | None = None) -> None:
        super().__init__(audio_path, use_cache=use_cache, cache_dir=cache_dir)
        self.session = _bcut_requests_session()
        self.task_id: str | None = None
        self._etags: list[str] = []
        self._in_boss_key: str | None = None
        self._resource_id: str | None = None
        self._upload_id: str | None = None
        self._upload_urls: list[str] = []
        self._per_size: int | None = None
        self._clips: int | None = None
        self._download_url: str | None = None

    @staticmethod
    def test_connection(timeout: int = 6) -> dict[str, Any]:
        try:
            session = _bcut_requests_session()
            resp = session.get(API_BASE_URL, timeout=timeout)
            ok = int(resp.status_code) < 500
            return {"success": ok, "status_code": int(resp.status_code)}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def upload(self) -> None:
        if not self.file_binary:
            raise ValueError("No audio data loaded")

        payload = json.dumps(
            {
                "type": 2,
                "name": "audio.mp3",
                "size": len(self.file_binary),
                "ResourceFileType": "mp3",
                "model_id": BCUT_MODEL_ID,
            }
        )
        resp = self.session.post(API_REQ_UPLOAD, data=payload, headers=self.headers)
        resp_data = _require_data(_decode_response(resp, "request upload"), "request upload")

        self._in_boss_key = resp_data["in_boss_key"]
        self._resource_id = resp_data["resource_id"]
        self._upload_id = resp_data["upload_id"]
        self._upload_urls = resp_data["upload_urls"]
        self._per_size = resp_data["per_size"]
        self._clips = len(resp_data["upload_urls"])

        logging.info(
            "Upload requested, size=%sKB, clips=%s, part_size=%sKB",
            resp_data["size"] // 1024,
            self._clips,
            resp_data["per_size"] // 1024,
        )
        self._upload_part()
        self._commit_upload()

    def _upload_part(self) -> None:
        for clip in range(self._clips or 0):
            start_range = clip * (self._per_size or 0)
            end_range = (clip + 1) * (self._per_size or 0)
            resp = self.session.put(
                self._upload_urls[clip],
                data=self.file_binary[start_range:end_range],
                headers=self.headers,
            )
            try:
                resp.raise_for_status()
            except Exception as exc:
                body = str(getattr(resp, "text", "") or "")[:1000]
                raise BcutASRError(
                    f"Bcut ASR upload part failed: clip={clip}, status={resp.status_code}; body={body}"
                ) from exc
            etag = resp.headers.get("Etag")
            if etag:
                self._etags.append(etag)

    def _commit_upload(self) -> None:
        data = json.dumps(
            {
                "InBossKey": self._in_boss_key,
                "ResourceId": self._resource_id,
                "Etags": ",".join(self._etags),
                "UploadId": self._upload_id,
                "model_id": BCUT_MODEL_ID,
            }
        )
        resp = self.session.post(API_COMMIT_UPLOAD, data=data, headers=self.headers)
        resp_data = _require_data(_decode_response(resp, "commit upload"), "commit upload")
        if not isinstance(resp_data, dict) or not resp_data.get("download_url"):
            raise BcutASRError(f"Bcut ASR commit upload failed: missing download_url; response={_compact_json(resp_data)}")
        self._download_url = resp_data["download_url"]

    def create_task(self) -> str:
        resp = self.session.post(
            API_CREATE_TASK,
            json={"resource": self._download_url, "model_id": BCUT_MODEL_ID},
            headers=self.headers,
        )
        resp_data = _require_data(_decode_response(resp, "create task"), "create task")
        if not isinstance(resp_data, dict) or not resp_data.get("task_id"):
            raise BcutASRError(f"Bcut ASR create task failed: missing task_id; response={_compact_json(resp_data)}")
        self.task_id = resp_data["task_id"]
        return self.task_id

    def result(self, task_id: str | None = None) -> Any:
        resp = self.session.get(
            API_QUERY_RESULT,
            params={"model_id": BCUT_MODEL_ID, "task_id": task_id or self.task_id},
            headers=self.headers,
        )
        return _require_data(_decode_response(resp, "query result"), "query result")

    def _run(self, callback: ProgressCallback | None = None) -> dict:
        if callback:
            callback(20, "Uploading audio to Bcut ASR")
        self.upload()

        if callback:
            callback(40, "Creating Bcut ASR task")
        self.create_task()

        if callback:
            callback(55, "Polling Bcut ASR result")
        task_resp = None
        for _ in range(500):
            task_resp = self.result()
            if not isinstance(task_resp, dict):
                raise BcutASRError(f"Bcut ASR query result returned invalid data: {_compact_json(task_resp)}")
            if task_resp.get("state") == 4:
                break
            if task_resp.get("state") in {-1, 5, 6}:
                raise BcutASRError(f"Bcut ASR task failed: {_compact_json(task_resp)}")
            time.sleep(1)
        else:
            raise BcutASRError(f"Bcut ASR task timed out after polling; task_id={self.task_id}")

        result_text = task_resp.get("result") if isinstance(task_resp, dict) else None
        if not result_text:
            raise BcutASRError(f"Bcut ASR task completed without result: {_compact_json(task_resp)}")

        if callback:
            callback(95, "Parsing Bcut ASR result")
        try:
            return json.loads(result_text)
        except Exception as exc:
            raise BcutASRError(f"Bcut ASR result is not valid JSON: {_compact_json(result_text)}") from exc
