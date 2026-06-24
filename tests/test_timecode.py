from __future__ import annotations

from video2visualpage.utils.timecode import overlap_seconds, parse_srt, seconds_to_srt_time


def test_overlap_seconds() -> None:
    assert overlap_seconds(0, 2, 1, 3) == 1
    assert overlap_seconds(0, 1, 1, 2) == 0


def test_srt_roundtrip_helpers() -> None:
    assert seconds_to_srt_time(65.25) == "00:01:05,250"
    segments = parse_srt("1\n00:00:01,000 --> 00:00:02,500\nhello\n\n")
    assert segments == [{"segment_id": "sub_0001", "start_sec": 1.0, "end_sec": 2.5, "text": "hello"}]
