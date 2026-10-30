from __future__ import annotations

from naoqi_control.leds import Leds


class Eyes:
    """Face-LED colors for this conversation. The control library only fades."""

    def __init__(self, leds: Leds):
        self._leds = leds

    def fade(self, color: str | int, duration: float = 0.15) -> None:
        self._leds.fade("FaceLeds", color, duration)

    def idle(self) -> None:
        self.fade(0xFFFFFF)

    def listening(self) -> None:
        self.fade(0x0066FF)

    def listening_to_user_speaking(self) -> None:
        self.fade(0x00E6FF)

    def thinking(self) -> None:
        self.fade(0xFFAA00)

    def speaking(self) -> None:
        self.fade(0x00FF44)

    def off(self) -> None:
        self.fade(0x000000)


def attach_eyes(nao) -> Eyes:
    """Hang conversation eye colors on a connected controller."""
    eyes = Eyes(nao.leds)
    nao.eyes = eyes
    return eyes
