from __future__ import annotations

import re


def overlap_seconds(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    return max(0.0, min(float(a_end), float(b_end)) - max(float(a_start), float(b_start)))


def seconds_to_srt_time(value: float) -> str:
    total_ms = max(0, int(round(float(value) * 1000.0)))
    total_seconds, ms = divmod(total_ms, 1000)
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{ms:03d}"


def parse_srt_time(value: str) -> float:
    match = re.match(r"^\s*(\d+):(\d{2}):(\d{2})[,.](\d{1,3})\s*$", value)
    if not match:
        raise ValueError(f"Invalid SRT timestamp: {value}")
    hours, minutes, seconds, millis = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(millis.ljust(3, "0")) / 1000.0


def parse_srt(content: str) -> list[dict[str, object]]:
    blocks = [block for block in content.replace("\r\n", "\n").replace("\r", "\n").split("\n\n") if block.strip()]
    segments: list[dict[str, object]] = []
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        timing_index = next((idx for idx, line in enumerate(lines) if "-->" in line), None)
        if timing_index is None:
            continue
        start_raw, end_raw = [part.strip().split()[0] for part in lines[timing_index].split("-->", 1)]
        text = " ".join(lines[timing_index + 1 :]).strip()
        if not text:
            continue
        start = parse_srt_time(start_raw)
        end = parse_srt_time(end_raw)
        if end <= start:
            continue
        segments.append(
            {
                "segment_id": f"sub_{len(segments) + 1:04d}",
                "start_sec": round(start, 3),
                "end_sec": round(end, 3),
                "text": text,
            }
        )
    return segments


def segments_to_srt(segments: list[dict[str, object]]) -> str:
    lines: list[str] = []
    for index, segment in enumerate(segments, start=1):
        start = seconds_to_srt_time(float(segment.get("start_sec", 0.0)))
        end = seconds_to_srt_time(float(segment.get("end_sec", 0.0)))
        text = str(segment.get("text", "")).strip()
        if not text:
            continue
        lines.extend([str(index), f"{start} --> {end}", text, ""])
    return "\n".join(lines)
