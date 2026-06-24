from __future__ import annotations

import argparse
from pathlib import Path

from .detector import ShotSegmenterConfig, detect_shots


def _progress(percent: float, message: str) -> None:
    print(f"[{percent:6.2f}%] {message}", flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Standalone OpenCV shot boundary detector.")
    parser.add_argument("--video", required=True, help="Input video path.")
    parser.add_argument("--output-dir", default="outputs", help="Directory for shots.json and exported assets.")
    parser.add_argument("--threshold", type=float, default=0.5, help="Detection sensitivity, usually 0.1-0.9.")
    parser.add_argument("--shot-prefix", default="shot", help="Shot id prefix, for example movie_shot or ref_shot.")
    parser.add_argument("--keyframe-positions", default="0.2,0.4,0.6,0.8", help="Comma separated positions inside each shot.")
    parser.add_argument("--sample-fps", type=float, default=0.0, help="Optional sample frame rate per shot.")
    parser.add_argument("--max-sample-frames-per-shot", type=int, default=0, help="Max optional sample frames per shot.")
    parser.add_argument("--min-gap-seconds", type=float, default=0.35, help="Minimum seconds between adjacent detected cuts.")
    parser.add_argument("--min-shot-seconds", type=float, default=0.15, help="Minimum final shot duration.")
    parser.add_argument("--resize-width", type=int, default=96, help="Analysis frame width.")
    parser.add_argument("--resize-height", type=int, default=54, help="Analysis frame height.")
    parser.add_argument("--no-keyframes", action="store_true", help="Do not export keyframe images.")
    parser.add_argument("--export-clips", action="store_true", help="Export silent shot clips with OpenCV VideoWriter.")
    parser.add_argument("--clip-codec", default="mp4v", help="FourCC codec for --export-clips.")
    parser.add_argument("--quiet", action="store_true", help="Hide progress output.")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    config = ShotSegmenterConfig(
        threshold=args.threshold,
        shot_prefix=args.shot_prefix,
        keyframe_positions=args.keyframe_positions,
        export_keyframes=not args.no_keyframes,
        sample_fps=args.sample_fps,
        max_sample_frames_per_shot=args.max_sample_frames_per_shot,
        min_gap_seconds=args.min_gap_seconds,
        min_shot_seconds=args.min_shot_seconds,
        resize_width=args.resize_width,
        resize_height=args.resize_height,
        export_clips=args.export_clips,
        clip_codec=args.clip_codec,
    )
    result = detect_shots(
        Path(args.video),
        output_dir=Path(args.output_dir),
        config=config,
        progress_callback=None if args.quiet else _progress,
    )
    print(Path(args.output_dir) / "shots.json")
    print(f"shots: {result['shot_count']}")


if __name__ == "__main__":
    main()
