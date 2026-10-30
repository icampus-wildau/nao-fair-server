from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, get_type_hints

_REPO_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.toml"

_BOOL_TRUE = frozenset({"true", "1", "yes"})
_BOOL_FALSE = frozenset({"false", "0", "no"})


def _repo_config_path() -> Path:
    return _REPO_CONFIG_PATH


@dataclass
class AppConfig:
    nao_ip: str = "192.0.2.10"
    nao_ports: list[int] = field(default_factory=lambda: [9559, 9561])
    audio_bridge_port: int = 8765
    nao_bridge_token: str = ""
    output_volume: int = 80  # 0-100
    tts_speed: int = 90  # 50-400, 100 is NAO normal
    posture: str = "Sit"
    llm_model: str = "gpt-6-luna"
    listening_timeout_secs: float = 15.0
    context_reset_timeout_secs: float = 30.0
    thinking_timeout_secs: float = 20.0
    unmute_delay_secs: float = 0.3
    gladia_region: str = "eu-west"
    gladia_endpointing: float = 0.5
    gladia_max_utterance_secs: int = 10
    gladia_audio_enhancer: bool = True
    gladia_language: str = "de"

    def __post_init__(self) -> None:
        if isinstance(self.nao_ports, int):
            self.nao_ports = [self.nao_ports]
        try:
            self.nao_ports = [int(port) for port in self.nao_ports]
        except (TypeError, ValueError):
            raise ValueError("nao_ports") from None
        if not self.nao_ports:
            raise ValueError("nao_ports")
        if not 0 <= self.output_volume <= 100:
            raise ValueError("output_volume")
        if not 50 <= self.tts_speed <= 400:
            raise ValueError("tts_speed")

    @classmethod
    def load(cls, path: Path | None = None) -> AppConfig:
        data: dict[str, Any] = {}
        config_path = _resolve_config_path(path)
        if config_path is not None:
            with config_path.open("rb") as f:
                loaded = tomllib.load(f)
            for key in (f.name for f in fields(cls)):
                if key in loaded:
                    data[key] = loaded[key]
            if "nao_ports" not in data and "nao_port" in loaded:
                data["nao_ports"] = [loaded["nao_port"]]

        data = _apply_env_overrides(data)
        return cls(**_build_kwargs(data))


def _resolve_config_path(path: Path | None) -> Path | None:
    if path is not None:
        resolved = path.expanduser().resolve()
        if not resolved.is_file():
            raise FileNotFoundError(resolved)
        return resolved

    cwd_config = Path.cwd() / "config.toml"
    if cwd_config.is_file():
        return cwd_config.resolve()

    repo_config = _repo_config_path()
    if repo_config.is_file():
        return repo_config.resolve()

    return None


def _field_types() -> dict[str, type]:
    return get_type_hints(AppConfig)


def _apply_env_overrides(data: dict[str, Any]) -> dict[str, Any]:
    result = dict(data)
    types = _field_types()
    for field in fields(AppConfig):
        env_key = field.name.upper()
        raw = os.environ.get(env_key)
        if raw is None or raw == "":
            continue
        result[field.name] = _parse_env_value(field.name, types[field.name], raw)
    return result


def _parse_env_value(name: str, annotation: Any, raw: str) -> Any:
    origin = getattr(annotation, "__origin__", None)
    if origin is list:
        item_type = annotation.__args__[0]
        normalized = raw.replace(";", ",").replace(" ", ",")
        parts = [part for part in normalized.split(",") if part.strip()]
        if not parts:
            raise ValueError(name)
        return [_parse_env_value(name, item_type, part) for part in parts]
    if annotation is bool:
        return _parse_bool_env(name, raw)
    if annotation is int:
        try:
            return int(raw.strip(), 10)
        except ValueError:
            raise ValueError(name) from None
    if annotation is float:
        try:
            return float(raw.strip())
        except ValueError:
            raise ValueError(name) from None
    return raw


def _parse_bool_env(name: str, raw: str) -> bool:
    value = raw.strip().lower()
    if value in _BOOL_TRUE:
        return True
    if value in _BOOL_FALSE:
        return False
    raise ValueError(name)


def _build_kwargs(data: dict[str, Any]) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    for field in fields(AppConfig):
        if field.name in data:
            kwargs[field.name] = data[field.name]
    return kwargs
