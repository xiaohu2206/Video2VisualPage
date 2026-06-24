from __future__ import annotations

import os
import re
import sys
from typing import Any


FALSE_VALUES = {"0", "false", "no", "off"}


def progress_enabled() -> bool:
    value = os.environ.get("VIDEO2VISUALPAGE_PROGRESS")
    if value is None:
        return True
    return value.strip().lower() not in FALSE_VALUES


def _compact(value: Any, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", str(value)).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."


class ProgressReporter:
    def __init__(self, stage_id: str):
        self.stage_id = stage_id
        self._current_index: int | None = None
        self._current_total: int | None = None
        self._current_label: str | None = None
        self._current_detail: str | None = None

    def emit(self, percent: float | int | None, message: str, detail: Any | None = None) -> None:
        if not progress_enabled():
            return
        if percent is None:
            percent_text = " --%"
        else:
            bounded = max(0, min(100, int(round(float(percent)))))
            percent_text = f"{bounded:3d}%"
        line = f"[progress] {self.stage_id} {percent_text} - {message}"
        if detail not in (None, ""):
            line += f" | {_compact(detail)}"
        print(line, file=sys.stderr, flush=True)

    def start(self, message: str, detail: Any | None = None) -> None:
        self.emit(0, message, detail)

    def done(self, message: str, detail: Any | None = None) -> None:
        self.emit(100, message, detail)

    def item_start(self, index: int, total: int, message: str, label: str, detail: Any | None = None) -> None:
        self._current_index = index
        self._current_total = total
        self._current_label = label
        self._current_detail = _compact(detail) if detail not in (None, "") else None
        self.emit(self._item_percent(completed=False), f"{message} {index}/{total}: {label}", detail)

    def item_done(self, index: int, total: int, message: str, label: str, detail: Any | None = None) -> None:
        self.emit(self._percent(index, total), f"{message} {index}/{total}: {label}", detail)

    def model_attempt(self, logical_stage: str, attempt: int, provider: str, model: str | None, detail: Any | None = None) -> None:
        model_text = model or "local_heuristic"
        label = self._current_label or logical_stage
        extra = detail if detail not in (None, "") else self._current_detail
        action = "运行本地分析" if provider == "local_heuristic" else "请求模型"
        self.emit(
            self._item_percent(completed=False),
            f"{action} attempt {attempt}: {label}",
            f"stage={logical_stage}; provider={provider}; model={model_text}; {extra or ''}".strip(),
        )

    def model_success(self, logical_stage: str, attempt: int, elapsed_sec: float, detail: Any | None = None) -> None:
        label = self._current_label or logical_stage
        extra = detail if detail not in (None, "") else self._current_detail
        self.emit(
            self._item_percent(completed=False),
            f"模型返回 attempt {attempt}: {label}",
            f"stage={logical_stage}; elapsed={elapsed_sec:.2f}s; {extra or ''}".strip(),
        )

    def model_failure(self, logical_stage: str, attempt: int, elapsed_sec: float, error: Exception) -> None:
        label = self._current_label or logical_stage
        self.emit(
            self._item_percent(completed=False),
            f"模型失败 attempt {attempt}: {label}",
            f"stage={logical_stage}; elapsed={elapsed_sec:.2f}s; error={error}",
        )

    def _item_percent(self, *, completed: bool) -> int:
        if self._current_index is None or self._current_total is None:
            return 0
        done_count = self._current_index if completed else self._current_index - 1
        return self._percent(done_count, self._current_total)

    @staticmethod
    def _percent(done_count: int, total: int) -> int:
        if total <= 0:
            return 100
        return max(0, min(100, round(done_count * 100 / total)))
