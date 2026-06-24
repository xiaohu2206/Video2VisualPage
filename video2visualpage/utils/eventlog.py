from __future__ import annotations

from pathlib import Path
from typing import Any

from ..state import now_iso
from ..storage import append_jsonl


def log_event(project_dir: str | Path, event: str, **payload: Any) -> None:
    append_jsonl(Path(project_dir) / "logs" / "events.jsonl", {"time": now_iso(), "event": event, **payload})


def log_error(project_dir: str | Path, stage: str, error: str, **payload: Any) -> None:
    append_jsonl(Path(project_dir) / "logs" / "errors.jsonl", {"time": now_iso(), "stage": stage, "error": error, **payload})
