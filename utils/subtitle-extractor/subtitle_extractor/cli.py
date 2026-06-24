from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .bcut_asr import BcutASR
from .pipeline import extract_subtitles


def _progress(percent: int, message: str) -> None:
    print(f"[{percent:>3}%] {message}", flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract subtitles from a video/audio file with Bcut ASR.")
    parser.add_argument("input", nargs="?", help="Input video or audio path")
    parser.add_argument("-o", "--output", help="Output subtitle path. Defaults to outputs/<input-name>.<format>")
    parser.add_argument(
        "--format",
        choices=["srt", "txt", "json", "raw-json"],
        default="srt",
        help="Output format, default: srt",
    )
    parser.add_argument("--compressed-srt", action="store_true", help="Write compact lines like [start-end] text")
    parser.add_argument("--work-dir", default=None, help="Directory for temporary audio and working files")
    parser.add_argument("--no-cache", action="store_true", help="Disable ASR JSON cache")
    parser.add_argument("--keep-audio", action="store_true", help="Keep converted MP3 in the work directory")
    parser.add_argument("--test-connection", action="store_true", help="Only test Bcut ASR connectivity")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose logs")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s: %(message)s",
    )

    if args.test_connection:
        result = BcutASR.test_connection()
        print(result)
        return 0 if result.get("success") else 1

    if not args.input:
        print("ERROR: input path is required unless --test-connection is used", file=sys.stderr)
        return 2

    try:
        result = extract_subtitles(
            args.input,
            output_path=Path(args.output) if args.output else None,
            output_format=args.format,
            compressed_srt=args.compressed_srt,
            work_dir=args.work_dir,
            use_cache=not args.no_cache,
            keep_audio=args.keep_audio,
            progress_callback=_progress,
        )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Output: {result.output_path}")
    print(f"Segments: {len(result.utterances)}")
    if args.keep_audio:
        print(f"Audio: {result.audio_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
