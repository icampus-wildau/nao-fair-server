"""Classify a tap, a double tap, or a long press before any action runs."""

from __future__ import annotations

LONG_SECS = 0.6
DOUBLE_SECS = 0.4
BOUNCE_SECS = 0.05


class TouchClassifier:
    def __init__(self) -> None:
        self._down: dict[str, float | None] = {}
        self._held_long: dict[str, bool] = {}
        self._consumed: dict[str, bool] = {}
        self._pending: dict[str, float] = {}
        self._release: dict[str, float] = {}
        self._bounce: dict[str, bool] = {}
        self._suppress: dict[str, float] = {}

    def down(self, sensor: str, now: float) -> str | None:
        if self._suppressed(sensor, now):
            self._bounce[sensor] = True
            return None
        pending = self._pending.get(sensor)
        if pending is not None:
            if now - self._release.get(sensor, now) < BOUNCE_SECS:
                self._bounce[sensor] = True
                return None
            if now < pending:
                self._clear_pending(sensor)
                self._begin(sensor, now)
                self._consumed[sensor] = True
                return "double_tap"
            self._clear_pending(sensor)
            self._begin(sensor, now)
            return "tap"
        self._begin(sensor, now)
        return None

    def up(self, sensor: str, now: float, wait_for_double: bool = True) -> str | None:
        if self._bounce.get(sensor):
            self._bounce[sensor] = False
            return None
        if self._down.get(sensor) is None:
            return None
        held_long = self._held_long.get(sensor, False)
        consumed = self._consumed.get(sensor, False)
        self._down[sensor] = None
        if held_long or consumed:
            return None
        if not wait_for_double:
            self._suppress[sensor] = now + BOUNCE_SECS
            return "tap"
        self._pending[sensor] = now + DOUBLE_SECS
        self._release[sensor] = now
        return None

    def flush(self, now: float, armed) -> list[tuple[str, str]]:
        found: list[tuple[str, str]] = []
        for sensor in list(self._pending):
            if armed(sensor):
                continue
            release = self._release.get(sensor, now)
            self._clear_pending(sensor)
            if now < release + BOUNCE_SECS:
                self._suppress[sensor] = release + BOUNCE_SECS
            found.append((sensor, "tap"))
        return found

    def tick(self, now: float) -> list[tuple[str, str]]:
        found: list[tuple[str, str]] = []
        for sensor, down in list(self._down.items()):
            if down is None or self._held_long.get(sensor) or self._consumed.get(sensor):
                continue
            if now - down >= LONG_SECS:
                self._held_long[sensor] = True
                found.append((sensor, "long_press"))
        for sensor, deadline in list(self._pending.items()):
            if now >= deadline:
                self._clear_pending(sensor)
                found.append((sensor, "tap"))
        return found

    def _begin(self, sensor: str, now: float) -> None:
        self._down[sensor] = now
        self._held_long[sensor] = False
        self._consumed[sensor] = False
        self._bounce[sensor] = False

    def _suppressed(self, sensor: str, now: float) -> bool:
        until = self._suppress.get(sensor)
        if until is None:
            return False
        if now < until:
            return True
        self._suppress.pop(sensor, None)
        return False

    def _clear_pending(self, sensor: str) -> None:
        self._pending.pop(sensor, None)
        self._release.pop(sensor, None)
