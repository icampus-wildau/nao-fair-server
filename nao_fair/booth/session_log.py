"""Conversation log for the host process.

Default output is the transcript and failures. ``-v`` adds diagnostics, and
``--panel`` draws the same events in a live view. Library loggers stay at
warning until verbosity is raised.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from collections import deque
from typing import TextIO

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from nao_fair.booth.processors.audio_frame_inspector import format_level_bar

_TRANSCRIPT_LIMIT = 8
_NOTE_LIMIT = 8
_HANDLER_FLAG = "_nao_session_log"


def configure_library_logging(
    verbosity: int,
    *,
    console: Console | None = None,
) -> None:
    """Point stdlib logging and Loguru at warning, info, or debug."""
    if verbosity >= 2:
        level_no = logging.DEBUG
        level_name = "DEBUG"
    elif verbosity >= 1:
        level_no = logging.INFO
        level_name = "INFO"
    else:
        level_no = logging.WARNING
        level_name = "WARNING"

    root = logging.getLogger()
    root.setLevel(level_no)
    for handler in list(root.handlers):
        if getattr(handler, _HANDLER_FLAG, False):
            root.removeHandler(handler)

    if console is None:
        handler: logging.Handler = logging.StreamHandler(sys.stderr)
    else:
        handler = _ConsoleLogHandler(console)
    setattr(handler, _HANDLER_FLAG, True)
    handler.setLevel(level_no)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)

    try:
        from loguru import logger
    except ImportError:
        return

    logger.remove()
    if console is None:
        logger.add(sys.stderr, level=level_name, format="{level} {message}")
        return

    def _sink(message: object) -> None:
        console.print(str(message), end="")

    logger.add(_sink, level=level_name, format="{level} {message}")


class _ConsoleLogHandler(logging.Handler):
    def __init__(self, console: Console) -> None:
        super().__init__()
        self._console = console

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._console.print(self.format(record))
        except Exception:
            self.handleError(record)


class SessionLog:
    """Lines the operator should see, plus the optional live panel."""

    def __init__(self) -> None:
        self._enabled = False
        self.verbosity = 0
        self._panel = False
        self._stream: TextIO = sys.stderr
        self._console: Console | None = None
        self._live: Live | None = None
        self._lock = threading.Lock()
        self._rms_refreshed_at = 0.0

        self.robot: str | None = None
        self.address: str | None = None
        self.model: str | None = None
        self.robot_state = "—"
        self.microphone = "muted"
        self.gladia = "closed"
        self.reconnect: str | None = None
        self.last_user: str | None = None
        self.last_nao: str | None = None
        self.interim_text: str | None = None
        self.rms: float | None = None
        self._turns: deque[tuple[str, str]] = deque(maxlen=_TRANSCRIPT_LIMIT)
        self._events: deque[str] = deque(maxlen=_NOTE_LIMIT)
        self._notes: deque[str] = deque(maxlen=_NOTE_LIMIT)

    @property
    def panel(self) -> bool:
        return self._panel

    def configure(
        self,
        verbosity: int = 0,
        *,
        panel: bool = False,
        stream: TextIO | None = None,
        libraries: bool = True,
    ) -> None:
        """Enable output. A log stays silent until this is called."""
        self._enabled = False
        self.shutdown()
        self.verbosity = max(verbosity, 0)
        self._panel = panel
        self._stream = stream or sys.stderr
        self.robot = None
        self.address = None
        self.model = None
        self.robot_state = "—"
        self.microphone = "muted"
        self.gladia = "closed"
        self.reconnect = None
        self.last_user = None
        self.last_nao = None
        self.interim_text = None
        self.rms = None
        self._turns.clear()
        self._events.clear()
        self._notes.clear()
        self._rms_refreshed_at = 0.0

        console: Console | None = None
        if panel:
            console = Console(file=self._stream, force_terminal=True)
            self._console = console
            self._live = Live(
                self.render(),
                console=console,
                refresh_per_second=8,
                transient=False,
            )
            self._live.start()
        if libraries:
            configure_library_logging(self.verbosity, console=console)
        self._enabled = True

    def shutdown(self) -> None:
        live = self._live
        self._live = None
        self._panel = False
        if live is not None:
            live.stop()

    def startup(self, *, robot: str | None, address: str, model: str) -> None:
        if not self._enabled:
            return
        name = robot.strip() if isinstance(robot, str) and robot.strip() else "NAO"
        with self._lock:
            self.robot = name
            self.address = address
            self.model = model
            self.reconnect = None
            self._publish(f"{name} @ {address} · {model}")

    def state(self, name: str, *, microphone: str | None = None) -> None:
        if not self._enabled:
            return
        with self._lock:
            self.robot_state = name
            if microphone is not None:
                self.microphone = microphone
            self._publish(f"State: {name}")

    def user(self, text: str) -> None:
        cleaned = text.strip()
        if not cleaned or not self._enabled:
            return
        with self._lock:
            self.last_user = cleaned
            self.interim_text = None
            self._turns.append(("user", cleaned))
            self._publish(f"User: {cleaned}")

    def interim(self, text: str) -> None:
        cleaned = text.strip()
        if not cleaned or not self._enabled or self.verbosity < 1:
            return
        with self._lock:
            self.interim_text = cleaned
            self._publish(f"Interim: {cleaned}")

    def nao(self, text: str) -> None:
        cleaned = text.strip()
        if not cleaned or not self._enabled:
            return
        with self._lock:
            self.last_nao = cleaned
            self._turns.append(("nao", cleaned))
            self._publish(f"NAO: {cleaned}")

    def event(self, message: str) -> None:
        if not self._enabled:
            return
        with self._lock:
            self._events.append(message)
            self._publish(message)

    def error(self, message: str) -> None:
        if not self._enabled:
            return
        line = f"Error: {message}"
        with self._lock:
            self._events.append(line)
            self._publish(line)

    def verbose(self, message: str) -> None:
        if not self._enabled or self.verbosity < 1:
            return
        with self._lock:
            self._notes.append(message)
            self._publish(message)

    def set_gladia(self, status: str) -> None:
        if not self._enabled:
            return
        with self._lock:
            self.gladia = status
            self._refresh_panel()

    def set_reconnect(self, label: str | None) -> None:
        if not self._enabled:
            return
        with self._lock:
            self.reconnect = label
            self._refresh_panel()

    def set_rms(self, level: float) -> None:
        if not self._enabled or self.verbosity < 1:
            return
        with self._lock:
            self.rms = level
            now = time.monotonic()
            if self._panel and now - self._rms_refreshed_at >= 0.12:
                self._rms_refreshed_at = now
                self._refresh_panel()

    def render(self) -> Panel:
        """Build the live panel. Safe to call without starting ``Live``."""
        table = Table.grid(expand=True, padding=(0, 1))
        table.add_column(style="bold")
        table.add_column()

        robot = self.robot or "—"
        address = self.address or "—"
        table.add_row("Robot", f"{robot} @ {address}")
        table.add_row("Model", self.model or "—")
        table.add_row("State", self.robot_state)
        table.add_row("Microphone", self.microphone)
        table.add_row("Gladia", self.gladia)
        if self.reconnect:
            table.add_row("Reconnect", self.reconnect)
        table.add_row("User", self.last_user or "—")
        table.add_row("NAO", self.last_nao or "—")
        if self.verbosity >= 1:
            table.add_row("Interim", self.interim_text or "—")
            if self.rms is not None:
                table.add_row("RMS", format_level_bar(self.rms, width=24))

        parts: list[object] = [table]
        if self._turns:
            transcript = Text()
            for role, text in self._turns:
                label = "User" if role == "user" else "NAO"
                transcript.append(f"{label}: {text}\n")
            parts.append(Panel(transcript, title="Transcript", border_style="dim"))
        if self._events:
            parts.append(Text("\n".join(self._events)))
        if self.verbosity >= 1 and self._notes:
            parts.append(Text("\n".join(self._notes), style="dim"))

        return Panel(
            Group(*parts),
            title="NAO session",
            border_style="cyan",
        )

    def _publish(self, message: str) -> None:
        """Print a line, or refresh the panel. Caller holds ``_lock``."""
        if self._panel and self._live is not None:
            self._live.update(self.render())
            return
        print(message, file=self._stream, flush=True)

    def _refresh_panel(self) -> None:
        if self._panel and self._live is not None:
            self._live.update(self.render())


session_log = SessionLog()
