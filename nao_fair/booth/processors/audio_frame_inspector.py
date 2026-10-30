"""Live terminal stats for incoming audio frames."""

from __future__ import annotations

from dataclasses import dataclass

from pipecat.frames.frames import (
    Frame,
    InputAudioRawFrame,
    LLMContextFrame,
    OutputTransportMessageFrame,
    OutputTransportMessageUrgentFrame,
    TranscriptionFrame,
    UserSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor, FrameProcessorSetup
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

import struct

_PCM16_MAX = 32_768.0  # Normalization divisor for signed int16 samples.


def _unpack_pcm16(audio: bytes) -> tuple[int, ...]:
    sample_count = len(audio) // 2
    if sample_count == 0:
        return ()
    return struct.unpack(f"<{sample_count}h", audio)


def pcm16_rms(audio: bytes) -> float:
    """Return RMS level for PCM16 audio normalized to [0, 1]."""
    if not audio:
        return 0.0

    samples = _unpack_pcm16(audio)
    if not samples:
        return 0.0

    mean_square = sum(sample * sample for sample in samples) / len(samples)
    return min((mean_square**0.5) / _PCM16_MAX, 1.0)


def pcm16_peak(audio: bytes) -> float:
    """Return peak level for PCM16 audio normalized to [0, 1]."""
    if not audio:
        return 0.0

    samples = _unpack_pcm16(audio)
    if not samples:
        return 0.0

    return min(max(abs(sample) for sample in samples) / _PCM16_MAX, 1.0)


@dataclass
class AudioInspectorStats:
    """Snapshot of the latest audio frame metrics."""

    frame_count: int = 0
    total_bytes: int = 0
    sample_rate: int = 0
    channels: int = 0
    samples: int = 0
    rms: float = 0.0
    peak: float = 0.0
    vad_state: str | None = None


def format_level_bar(level: float, *, width: int = 40) -> str:
    """Render a simple ASCII level meter for a normalized value."""
    filled = min(max(int(level * width), 0), width)
    s = "#" * filled
    s += "." * (width - filled)
    s += f" {level:.2f}"

    return s


def render_audio_stats(stats: AudioInspectorStats) -> Panel:
    """Build the Rich panel shown in the live terminal display."""
    table = Table.grid(expand=True)
    table.add_column(justify="left")
    table.add_column(justify="right")

    table.add_row("frames received", str(stats.frame_count))
    table.add_row("total bytes", str(stats.total_bytes))
    table.add_row("sample rate", f"{stats.sample_rate} Hz")
    table.add_row("channels", str(stats.channels))
    table.add_row("samples (last frame)", str(stats.samples))
    table.add_row("rms", f"{stats.rms:.4f}")
    table.add_row("peak", f"{stats.peak:.4f}")
    table.add_row("rms meter", format_level_bar(stats.rms))
    table.add_row("peak meter", format_level_bar(stats.peak))
    table.add_row("vad state", stats.vad_state)

    return Panel(
        table,
        title="[bold cyan]Audio Frame Inspector[/bold cyan]",
        border_style="cyan",
    )


class AudioFrameInspector(FrameProcessor):
    """Compute RMS/peak and render stable live stats for incoming audio frames."""

    def __init__(self, name: str, *, console: Console | None = None) -> None:
        super().__init__(name=f"AudioFrameInspector {name}")
        self._console = console or Console()
        self._stats = AudioInspectorStats()
        self._live: Live | None = None
        self._last_frame_cls: type[Frame] | None = None

    @property
    def stats(self) -> AudioInspectorStats:
        return self._stats

    async def setup(self, setup: FrameProcessorSetup) -> None:
        await super().setup(setup)
        self._live = Live(
            Group(Text("Waiting for audio frames...", style="dim")),
            console=self._console,
            refresh_per_second=12,
            transient=False,
        )
        self._live.start()

    async def cleanup(self) -> None:
        if self._live is not None:
            self._live.stop()
            self._live = None
        await super().cleanup()

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        # if isinstance(frame, TranscriptionFrame):
        #     print("Got TranscriptionFrame:", frame.transcription)

        if isinstance(frame, InputAudioRawFrame):
            self._update_stats(frame)
            # self._render()
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, UserSpeakingFrame):
            if self._last_frame_cls != UserSpeakingFrame:
                print(f"[{self.name}] Got frame:", type(frame).__name__)
        else:
            print(f"[{self.name}] Got frame:", type(frame).__name__)

        self._last_frame_cls = type(frame)

        if isinstance(frame, LLMContextFrame):
            print(f"[{self.name}] LLMContextFrame: {frame.context.messages}")

        if isinstance(frame, OutputTransportMessageUrgentFrame) or isinstance(frame, OutputTransportMessageFrame):
            msg_type = frame.message.get("type")
            if msg_type == "vad":
                state = frame.message.get("state")
                if self._stats.vad_state != state:
                    self._stats.vad_state = state
                    # self._render()
        await self.push_frame(frame, direction)

    def _update_stats(self, frame: InputAudioRawFrame) -> None:
        self._stats.frame_count += 1
        self._stats.total_bytes += len(frame.audio)
        self._stats.sample_rate = frame.sample_rate
        self._stats.channels = frame.num_channels
        self._stats.samples = frame.num_frames
        self._stats.rms = pcm16_rms(frame.audio)
        self._stats.peak = pcm16_peak(frame.audio)

    def _render(self) -> None:
        if self._live is None:
            return
        self._live.update(render_audio_stats(self._stats))
