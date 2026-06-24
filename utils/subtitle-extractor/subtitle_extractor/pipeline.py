from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from .asr_base import ProgressCallback
from .bcut_asr import BcutASR
from .ffmpeg import extract_audio_mp3
from .formats import serialize_utterances, write_output


@dataclass(frozen=True)
class SubtitleExtractionResult:
    input_path: Path
    audio_path: Path
    output_path: Path | None
    output_format: str
    utterances: list[dict]
    raw_data: dict


def _default_output_path(input_path: Path, output_format: str) -> Path:
    suffix = ".json" if output_format in {"json", "raw-json"} else f".{output_format}"
    return Path.cwd() / "outputs" / f"{input_path.stem}{suffix}"


def extract_subtitles(
    input_path: str | Path,
    *,
    output_path: str | Path | None = None,
    output_format: str = "srt",
    compressed_srt: bool = False,
    work_dir: str | Path | None = None,
    use_cache: bool = True,
    keep_audio: bool = False,
    progress_callback: ProgressCallback | None = None,
) -> SubtitleExtractionResult:
    source = Path(input_path).expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(f"Input file does not exist: {source}")

    output_format = output_format.strip().lower()
    if output_format not in {"srt", "txt", "json", "raw-json"}:
        raise ValueError("output_format must be one of: srt, txt, json, raw-json")

    out_path = Path(output_path).expanduser().resolve() if output_path else _default_output_path(source, output_format)
    base_work_dir = Path(work_dir).expanduser().resolve() if work_dir else Path.cwd() / "outputs" / "work"
    cache_dir = Path.cwd() / "outputs" / "asr_cache"
    base_work_dir.mkdir(parents=True, exist_ok=True)

    temp_ctx = tempfile.TemporaryDirectory(dir=str(base_work_dir), prefix="subtitle_") if not keep_audio else None
    actual_work_dir = Path(temp_ctx.name) if temp_ctx else base_work_dir
    audio_path = actual_work_dir / f"{source.stem}.mp3"

    try:
        if progress_callback:
            progress_callback(10, "Extracting audio")
        extract_audio_mp3(source, audio_path)

        if progress_callback:
            progress_callback(45, "Running Bcut ASR")
        asr = BcutASR(str(audio_path), use_cache=use_cache, cache_dir=str(cache_dir))
        data = asr.run(callback=progress_callback)
        utterances = data.get("utterances") if isinstance(data, dict) else None
        if not isinstance(utterances, list) or not utterances:
            raise RuntimeError("Bcut ASR did not return valid utterances")

        if progress_callback:
            progress_callback(97, "Writing subtitle output")
        content = serialize_utterances(data, output_format=output_format, compressed_srt=compressed_srt)
        write_output(out_path, content)

        if progress_callback:
            progress_callback(100, "Done")
        return SubtitleExtractionResult(
            input_path=source,
            audio_path=audio_path,
            output_path=out_path,
            output_format=output_format,
            utterances=utterances,
            raw_data=data,
        )
    finally:
        if temp_ctx:
            temp_ctx.cleanup()
