from __future__ import annotations

from video2visualpage.stages.subtitle_align import align_subtitles


def test_align_subtitles_records_overlap_ratio() -> None:
    shots = [{"shot_id": "shot_001", "start_sec": 0.0, "end_sec": 2.0}]
    subtitles = [{"segment_id": "sub_0001", "start_sec": 1.0, "end_sec": 3.0, "text": "hello"}]

    result = align_subtitles(shots, subtitles)

    assert result[0]["subtitle_text"] == "hello"
    assert result[0]["subtitle_segments"][0]["overlap_sec"] == 1.0
    assert result[0]["subtitle_segments"][0]["overlap_ratio"] == 0.5
