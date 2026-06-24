from __future__ import annotations

from pathlib import Path
from typing import Any

from ..storage import read_json, read_jsonl


def validate_json_file(path: str | Path) -> tuple[bool, str | None]:
    try:
        read_json(path)
    except Exception as exc:  # noqa: BLE001 - QA should report parser details.
        return False, str(exc)
    return True, None


def validate_jsonl_file(path: str | Path) -> tuple[bool, str | None]:
    try:
        read_jsonl(path)
    except Exception as exc:  # noqa: BLE001 - QA should report parser details.
        return False, str(exc)
    return True, None


def require_keys(data: dict[str, Any], keys: list[str]) -> list[str]:
    return [key for key in keys if key not in data]
