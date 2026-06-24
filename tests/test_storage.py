from __future__ import annotations

from video2visualpage.storage import atomic_write_json, read_json, read_jsonl, write_jsonl


def test_json_and_jsonl_storage(tmp_path) -> None:
    json_path = tmp_path / "data.json"
    atomic_write_json(json_path, {"ok": True})
    assert read_json(json_path) == {"ok": True}

    jsonl_path = tmp_path / "rows.jsonl"
    write_jsonl(jsonl_path, [{"id": 1}, {"id": 2}])
    assert read_jsonl(jsonl_path) == [{"id": 1}, {"id": 2}]
