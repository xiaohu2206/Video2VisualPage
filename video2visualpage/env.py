from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from .paths import repo_root


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


@lru_cache(maxsize=8)
def load_dotenv_values(path: str | None = None) -> dict[str, str]:
    env_path = Path(path) if path else repo_root() / ".env"
    if not env_path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        values[key] = _strip_quotes(value)
    return values


def env_value(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is not None:
        return value
    return load_dotenv_values().get(name, default)


def env_flag(name: str, *, default: bool) -> bool:
    value = env_value(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}
