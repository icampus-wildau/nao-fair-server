"""Process settings. The server listens on localhost unless a setting says otherwise."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 8000
    db_path: Path = _REPO_ROOT / "data" / "nao-fair.sqlite"
    secrets_path: Path = _REPO_ROOT / "data" / "operator-secrets.json"
    config_path: Path = _REPO_ROOT / "config.toml"
    prompts_dir: Path = _REPO_ROOT / "prompts"
    bridge_port: int = 8765
    nao_ports: tuple[int, ...] = (9559, 9561)
    bridge_token: str = ""
    frontend_dir: Path = _REPO_ROOT.parent / "nao-fair-frontend" / "dist"

    @classmethod
    def from_env(cls) -> Settings:
        ports = os.environ.get("NAO_PORTS", "9559,9561")
        parsed = tuple(int(part) for part in ports.replace(";", ",").split(",") if part.strip())
        return cls(
            host=os.environ.get("NAO_FAIR_HOST", "127.0.0.1"),
            port=int(os.environ.get("NAO_FAIR_PORT", "8000")),
            db_path=Path(os.environ.get("NAO_FAIR_DB", str(_REPO_ROOT / "data" / "nao-fair.sqlite"))),
            secrets_path=Path(
                os.environ.get(
                    "NAO_FAIR_SECRETS",
                    str(_REPO_ROOT / "data" / "operator-secrets.json"),
                )
            ),
            config_path=Path(os.environ.get("NAO_FAIR_CONFIG", str(_REPO_ROOT / "config.toml"))),
            prompts_dir=Path(os.environ.get("NAO_FAIR_PROMPTS", str(_REPO_ROOT / "prompts"))),
            bridge_port=int(os.environ.get("NAO_FAIR_BRIDGE_PORT", "8765")),
            nao_ports=parsed or (9559, 9561),
            bridge_token=os.environ.get("NAO_BRIDGE_TOKEN", ""),
            frontend_dir=Path(os.environ.get(
                "NAO_FAIR_FRONTEND_DIR", str(_REPO_ROOT.parent / "nao-fair-frontend" / "dist")
            )),
        )
