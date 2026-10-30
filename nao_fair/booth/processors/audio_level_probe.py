"""Feed microphone level into the session panel without printing frames."""

from __future__ import annotations

from pipecat.frames.frames import Frame, InputAudioRawFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from nao_fair.booth.processors.audio_frame_inspector import pcm16_rms
from nao_fair.booth.session_log import session_log


class AudioLevelProbe(FrameProcessor):
    """Update the panel RMS meter from incoming microphone audio."""

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, InputAudioRawFrame):
            session_log.set_rms(pcm16_rms(frame.audio))
        await self.push_frame(frame, direction)
