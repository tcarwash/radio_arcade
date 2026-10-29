"""Shared app preferences without importing GUI packages."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any

from .store import default_data_dir


@dataclass
class Settings:
    callsign: str = ""
    host: str = "127.0.0.1"
    transport: str = "broadcast"
    kiss_port: int = 8100
    command_port: int = 8300
    group: str = "RAGAMES"
    color_scheme: str = "midnight"
    sound_enabled: bool = False

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Settings":
        path = Path(path) if path is not None else default_data_dir() / "settings.json"
        if not path.is_file():
            return cls()
        try:
            data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
            allowed = {field for field in cls.__dataclass_fields__}
            return cls(**{key: value for key, value in data.items() if key in allowed})
        except (OSError, ValueError, TypeError):
            return cls()

    def save(self, path: str | Path | None = None) -> None:
        path = Path(path) if path is not None else default_data_dir() / "settings.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(json.dumps(asdict(self), indent=2) + "\n", encoding="utf-8")
        temp.replace(path)
