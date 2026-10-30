"""Operator secrets. Values stay on disk and are never returned."""

from __future__ import annotations

import json
import os
from pathlib import Path

SLOTS = ("openai", "gladia")
ENV_NAMES = {
    "openai": "OPENAI_API_KEY",
    "gladia": "GLADIA_API_KEY",
}


class SecretStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _read(self) -> dict[str, str]:
        if not self.path.is_file():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(raw, dict):
            return {}
        return {
            key: value
            for key, value in raw.items()
            if key in SLOTS and isinstance(value, str) and value.strip()
        }

    def _write(self, data: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(data), encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(self.path)
        os.chmod(self.path, 0o600)

    def status(self, slot: str) -> str:
        if slot not in SLOTS:
            raise KeyError(slot)
        stored = self._read().get(slot, "")
        if stored.strip():
            return "console"
        env = os.environ.get(ENV_NAMES[slot], "")
        if env.strip():
            return "environment"
        return "missing"

    def snapshot(self) -> dict:
        return {"slots": [{"slot": slot, "status": self.status(slot)} for slot in SLOTS]}

    def value(self, slot: str) -> str | None:
        """The secret the next client should use. Never log the return value."""
        if slot not in SLOTS:
            raise KeyError(slot)
        stored = self._read().get(slot, "")
        if stored.strip():
            return stored
        env = os.environ.get(ENV_NAMES[slot], "")
        if env.strip():
            return env
        return None

    def put(self, slot: str, value: str) -> str:
        if slot not in SLOTS:
            raise KeyError(slot)
        if not value or not value.strip():
            raise ValueError("blank")
        data = self._read()
        data[slot] = value
        self._write(data)
        return self.status(slot)

    def delete(self, slot: str) -> str:
        if slot not in SLOTS:
            raise KeyError(slot)
        data = self._read()
        data.pop(slot, None)
        if data:
            self._write(data)
        elif self.path.exists():
            self.path.unlink()
        return self.status(slot)
