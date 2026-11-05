from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AppConfig:
    nao_ip: str = "192.0.2.10"
    nao_ports: list[int] = field(default_factory=lambda: [9559, 9561])
    audio_bridge_port: int = 8765
    nao_bridge_token: str = ""
    output_volume: int = 50  # 0-100
    tts_speed: int = 80  # 50-400, 100 is NAO normal
    posture: str = "Sit"
    llm_model: str = "gpt-6-luna"
    listening_timeout_secs: float = 3.5
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
