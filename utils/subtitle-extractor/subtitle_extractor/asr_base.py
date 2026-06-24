from __future__ import annotations

import json
import logging
import zlib
from pathlib import Path
from typing import Callable


ProgressCallback = Callable[[int, str], None]


class BaseASR:
    """Small ASR base class with binary loading, CRC32 and JSON file cache."""

    def __init__(
        self,
        audio_path: str | bytes | Path,
        *,
        use_cache: bool = False,
        cache_dir: str | Path | None = None,
    ) -> None:
        self.use_cache = bool(use_cache)
        self.audio_path = audio_path
        self.file_binary: bytes = b""
        self.crc32_hex = ""
        self.cache_dir = Path(cache_dir) if cache_dir else Path.cwd() / "outputs" / "asr_cache"

        if isinstance(audio_path, bytes):
            self.file_binary = audio_path
        else:
            p = Path(audio_path)
            if not p.exists():
                raise FileNotFoundError(f"Audio file does not exist: {p}")
            self.file_binary = p.read_bytes()

        self.crc32_hex = format(zlib.crc32(self.file_binary) & 0xFFFFFFFF, "08x")

    def _cache_path(self) -> Path:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        return self.cache_dir / f"{self._get_key()}.json"

    def _get_key(self) -> str:
        return f"{self.__class__.__name__}-{self.crc32_hex}"

    def _run(self, callback: ProgressCallback | None = None) -> dict:
        raise NotImplementedError

    def run(self, callback: ProgressCallback | None = None) -> dict:
        cache_path = self._cache_path()
        if self.use_cache and cache_path.exists():
            try:
                data = json.loads(cache_path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and isinstance(data.get("utterances"), list):
                    logging.info("ASR cache hit: %s", cache_path)
                    return data
            except Exception as exc:
                logging.warning("Failed to read ASR cache, rerunning: %s", exc)

        data = self._run(callback=callback)
        if not isinstance(data, dict):
            raise ValueError("ASR returned invalid data, expected dict")

        try:
            cache_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            logging.warning("Failed to write ASR cache: %s", exc)

        return data
