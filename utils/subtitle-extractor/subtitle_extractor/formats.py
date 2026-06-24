from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any


def ms_to_srt_ts(ms: int | float) -> str:
    ms_int = max(0, int(round(float(ms))))
    total_seconds, milliseconds = divmod(ms_int, 1000)
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def utterances_to_srt(utterances: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    idx = 1
    for item in utterances:
        start = int(item.get("start_time", 0) or 0)
        end = int(item.get("end_time", 0) or 0)
        text = (item.get("text") or item.get("transcript") or "").strip()
        if not text or end <= start:
            continue
        lines.extend([str(idx), f"{ms_to_srt_ts(start)} --> {ms_to_srt_ts(end)}", text, ""])
        idx += 1
    return "\n".join(lines).strip() + ("\n" if lines else "")


def utterances_to_txt(utterances: list[dict[str, Any]]) -> str:
    return "\n".join(
        (item.get("text") or item.get("transcript") or "").strip()
        for item in utterances
        if (item.get("text") or item.get("transcript") or "").strip()
    ) + ("\n" if utterances else "")


def utterances_to_json(utterances: list[dict[str, Any]]) -> str:
    items: list[dict[str, Any]] = []
    for idx, item in enumerate(utterances, start=1):
        text = (item.get("text") or item.get("transcript") or "").strip()
        if not text:
            continue
        start_ms = int(item.get("start_time", 0) or 0)
        end_ms = int(item.get("end_time", 0) or 0)
        items.append(
            {
                "index": idx,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "start": round(start_ms / 1000.0, 3),
                "end": round(end_ms / 1000.0, 3),
                "text": text,
            }
        )
    return json.dumps(items, ensure_ascii=False, indent=2) + "\n"


def compress_srt(content: str) -> str:
    text = content.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    blocks = [block for block in text.split("\n\n") if block.strip()]
    out_lines: list[str] = []
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        timing_i = next((i for i, line in enumerate(lines[:3]) if "-->" in line), None)
        if timing_i is None:
            continue
        parts = lines[timing_i].split("-->")
        if len(parts) < 2:
            continue
        start = parts[0].strip()
        end = parts[1].strip()
        body = " ".join(lines[timing_i + 1 :])
        body = re.sub(r"\s+", " ", body).strip()
        body = re.sub(r"<[^>]+>", "", body)
        if body:
            out_lines.append(f"[{start}-{end}] {body}")
    return "\n".join(out_lines) + ("\n" if out_lines else "")


def serialize_utterances(
    asr_data: dict[str, Any],
    *,
    output_format: str = "srt",
    compressed_srt: bool = False,
) -> str:
    utterances = asr_data.get("utterances") if isinstance(asr_data, dict) else None
    if not isinstance(utterances, list):
        raise ValueError("ASR data does not contain an utterances list")

    if output_format == "srt":
        srt = utterances_to_srt(utterances)
        return compress_srt(srt) if compressed_srt else srt
    if output_format == "txt":
        return utterances_to_txt(utterances)
    if output_format == "json":
        return utterances_to_json(utterances)
    if output_format == "raw-json":
        return json.dumps(asr_data, ensure_ascii=False, indent=2) + "\n"
    raise ValueError(f"Unsupported output format: {output_format}")


def write_output(path: str | Path, content: str) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content, encoding="utf-8")
    return out
