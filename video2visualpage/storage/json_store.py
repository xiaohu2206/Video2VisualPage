from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable


def _replace_with_temp(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def atomic_write_json(path: str | Path, data: Any) -> Path:
    out = Path(path)
    encoded = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False).encode("utf-8")
    _replace_with_temp(out, encoded + b"\n")
    return out


def atomic_write_text(path: str | Path, text: str) -> Path:
    out = Path(path)
    _replace_with_temp(out, text.encode("utf-8"))
    return out


def read_json(path: str | Path) -> Any:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def append_jsonl(path: str | Path, item: Any) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(item, ensure_ascii=False, sort_keys=False)
    with out.open("a", encoding="utf-8") as handle:
        handle.write(line)
        handle.write("\n")
    return out


def write_jsonl(path: str | Path, items: Iterable[Any]) -> Path:
    lines = [json.dumps(item, ensure_ascii=False, sort_keys=False) for item in items]
    return atomic_write_text(path, "\n".join(lines) + ("\n" if lines else ""))


def read_jsonl(path: str | Path) -> list[Any]:
    rows: list[Any] = []
    source = Path(path)
    if not source.exists():
        return rows
    with source.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                rows.append(json.loads(text))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at {source}:{line_number}: {exc}") from exc
    return rows
