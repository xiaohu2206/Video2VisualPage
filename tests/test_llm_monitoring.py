from __future__ import annotations

import json
from pathlib import Path

import video2visualpage.models.adapter as adapter_module
from video2visualpage.models import LocalModelAdapter
from video2visualpage.storage import read_json, read_jsonl


def _detail_path(project_dir: Path, row: dict) -> Path:
    return project_dir / Path(str(row["detail"]))


def test_llm_monitor_writes_structured_function_log(tmp_path) -> None:
    adapter = LocalModelAdapter(
        tmp_path,
        {
            "llm": {"provider": "local_heuristic", "use_env": False, "max_retries": 0},
            "llm_monitoring": {"enabled": True},
        },
        model_role="vision",
    )

    result = adapter.analyze_shot(
        {
            "shot_id": "shot_001",
            "time_range": {"start_sec": 0.0, "end_sec": 1.2},
            "frames": [],
            "subtitle_text": "这里展示模型监控日志。",
        }
    )

    assert result["shot_id"] == "shot_001"
    rows = read_jsonl(tmp_path / "logs" / "llm_monitor" / "index.jsonl")
    function_rows = [row for row in rows if row["record_type"] == "function_call"]
    assert function_rows
    assert function_rows[-1]["function"] == "shot_understanding"
    assert function_rows[-1]["status"] == "success"

    detail = read_json(_detail_path(tmp_path, function_rows[-1]))
    assert detail["classification"]["function"] == "shot_understanding"
    assert detail["input"]["payload"]["shot_id"] == "shot_001"
    assert detail["input"]["summary"]["identifiers"]["shot_id"] == "shot_001"
    assert detail["output"]["result"]["shot_id"] == "shot_001"
    assert detail["reliability"]["output_ok"] is True

    function_calls = read_jsonl(tmp_path / "logs" / "llm_monitor" / "by_function" / "shot_understanding" / "calls.jsonl")
    assert function_calls[-1]["call_id"] == function_rows[-1]["call_id"]
    health = read_json(tmp_path / "logs" / "llm_monitor" / "health.json")
    assert health["by_function"]["shot_understanding"]["success"] >= 1


def test_llm_monitor_records_provider_call_without_api_key(tmp_path, monkeypatch) -> None:
    captured_request_bodies = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            payload = {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "chunk_id": "chunk_001",
                                    "shot_range": ["shot_001", "shot_001"],
                                    "main_topics": ["监控"],
                                    "summary": "结构化监控日志。",
                                    "important_shots": ["shot_001"],
                                },
                                ensure_ascii=False,
                            )
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
            }
            return json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def fake_urlopen(request, timeout):
        captured_request_bodies.append(json.loads(request.data.decode("utf-8")))
        return FakeResponse()

    monkeypatch.setattr(adapter_module.urllib.request, "urlopen", fake_urlopen)
    adapter = LocalModelAdapter(
        tmp_path,
        {
            "llm": {
                "provider": "openai_compatible",
                "api_key": "secret-test-key",
                "model": "copy-test",
                "use_env": False,
                "max_retries": 0,
            },
            "llm_monitoring": {"enabled": True},
        },
        model_role="copywriting",
    )

    adapter.summarize_chunk(
        "chunk_001",
        [{"shot_id": "shot_001", "merged_summary": "需要监控模型调用。", "topic_tags": ["监控"]}],
    )

    rows = read_jsonl(tmp_path / "logs" / "llm_monitor" / "index.jsonl")
    raw_row = next(row for row in rows if row["record_type"] == "provider_call")
    function_row = next(row for row in rows if row["record_type"] == "function_call")
    raw_detail = read_json(_detail_path(tmp_path, raw_row))
    raw_detail_text = json.dumps(raw_detail, ensure_ascii=False)
    user_content = captured_request_bodies[0]["messages"][1]["content"]

    assert isinstance(user_content, str)
    assert "Input context:" in user_content
    assert "Chunk id: chunk_001" in user_content
    assert '"chunk_id"' not in user_content
    assert '"cards"' not in user_content
    assert raw_detail["parent_call_id"] == function_row["call_id"]
    assert raw_detail["request"]["payload_summary"]["identifiers"]["chunk_id"] == "chunk_001"
    assert raw_detail["response"]["usage"]["total_tokens"] == 20
    assert raw_detail["reliability"]["non_empty_output"] is True
    assert "secret-test-key" not in raw_detail_text
