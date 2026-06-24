from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


WIN_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def _bin_name(base: str) -> str:
    return f"{base}.exe" if os.name == "nt" else base


def _resolve_bin(base: str) -> str | None:
    name = _bin_name(base)

    env_path = os.environ.get("FFMPEG_PATH")
    if env_path:
        p = Path(env_path)
        if p.exists():
            if base == "ffprobe" and "ffprobe" not in p.name.lower():
                sibling = p.parent / name
                if sibling.exists():
                    return str(sibling)
            elif base == "ffmpeg":
                return str(p)

    env_dir = os.environ.get("FFMPEG_DIR") or os.environ.get("FFMPEG_HOME")
    if env_dir:
        p = Path(env_dir) / name
        if p.exists():
            return str(p)

    hit = shutil.which(base)
    if hit:
        return hit

    try:
        import imageio_ffmpeg

        ffmpeg_path = Path(imageio_ffmpeg.get_ffmpeg_exe())
        if base == "ffmpeg" and ffmpeg_path.exists():
            return str(ffmpeg_path)
        probe = ffmpeg_path.parent / name
        if base == "ffprobe" and probe.exists():
            return str(probe)
    except Exception:
        pass

    roots: list[Path] = []
    try:
        roots.append(Path(sys.executable).resolve().parent)
    except Exception:
        pass
    roots.extend([Path.cwd(), Path(__file__).resolve().parents[1]])

    for root in roots:
        for candidate in (
            root / name,
            root / "ffmpeg" / "bin" / name,
            root.parent / "ffmpeg" / "bin" / name,
        ):
            if candidate.exists():
                return str(candidate)
    return None


def resolve_ffmpeg_bin() -> str:
    hit = _resolve_bin("ffmpeg")
    if not hit:
        raise FileNotFoundError("ffmpeg not found. Install ffmpeg or set FFMPEG_PATH/FFMPEG_DIR.")
    return hit


def run_ffmpeg(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    kwargs = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }
    if os.name == "nt":
        kwargs["creationflags"] = WIN_NO_WINDOW
    proc = subprocess.run(args, check=False, **kwargs)
    if check and proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "ffmpeg failed").strip())
    return proc


def extract_audio_mp3(source_path: str | Path, audio_path: str | Path) -> Path:
    source = Path(source_path)
    if not source.exists():
        raise FileNotFoundError(f"Input media does not exist: {source}")

    out = Path(audio_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        resolve_ffmpeg_bin(),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-codec:a",
        "libmp3lame",
        "-q:a",
        "3",
        str(out),
    ]
    run_ffmpeg(cmd)
    return out
