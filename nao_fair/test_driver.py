"""Test-only stand-in for a connected robot.

Production ``main()`` never constructs this. A press reports the sensor and
does not run the bound action.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nao_fair.runtime import Runtime


class TestDriver:
    __test__ = False

    def __init__(self, name: str = "NAO", refuse: str | None = None) -> None:
        self.name = name
        self.refuse = refuse
        self._runtime: Runtime | None = None

    def attach(self, runtime: Runtime) -> None:
        self._runtime = runtime

    async def open(self, address: str) -> str:
        del address
        if self.refuse:
            raise ConnectionError(self.refuse)
        return self.name

    def _runtime_or_raise(self) -> Runtime:
        if self._runtime is None:
            raise RuntimeError("driver is not attached")
        return self._runtime

    def set_conversation_state(self, state: str) -> None:
        self._runtime_or_raise().driver_set_state(state)

    def append_transcript(self, speaker: str, text: str) -> None:
        self._runtime_or_raise().driver_append_transcript(speaker, text)

    def press(self, sensor_id: str) -> None:
        self._runtime_or_raise().driver_press(sensor_id)

    def end_connection(self) -> None:
        self._runtime_or_raise().driver_end_connection()
