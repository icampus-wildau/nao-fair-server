import json

from loguru import logger

from pipecat.frames.frames import Frame, InputAudioRawFrame
from pipecat.serializers.base_serializer import FrameSerializer

from nao_websocket_bridge_protocol import CHANNELS, SAMPLE_RATE, validate_audio_format


NUM_CHANNELS = CHANNELS


class NaoAudioBridgeSerializer(FrameSerializer):
    def __init__(self):
        super().__init__()
        self._format_received = False
        self._bridge_dropped_chunks: int | None = None

    async def serialize(self, frame: Frame) -> str | bytes | None:
        # This WebSocket connection is input-only.
        return None

    async def deserialize(self, data: str | bytes) -> Frame | None:
        if isinstance(data, bytes):
            if not self._format_received:
                raise RuntimeError("Received audio before audio_format")

            if len(data) % 2:
                raise RuntimeError("PCM S16LE frame has an odd byte count")

            return InputAudioRawFrame(
                audio=data,
                sample_rate=SAMPLE_RATE,
                num_channels=NUM_CHANNELS,
            )

        message = json.loads(data)

        if message.get("type") == "audio_format":
            try:
                validate_audio_format(message)
            except ValueError as exc:
                raise RuntimeError(str(exc)) from exc
            self._format_received = True

            logger.info(
                f"NAO audio bridge: "
                f"{message['sample_rate']} Hz, "
                f"{message['channels']} channel, "
                f"{message['encoding']}"
            )
        elif message.get("type") == "status":
            self._note_bridge_status(message)
        else:
            logger.debug(f"NAO audio bridge control message: {message}")

        return None

    def _note_bridge_status(self, message: dict) -> None:
        dropped = message.get("dropped_chunks")
        if not isinstance(dropped, int):
            return
        previous = self._bridge_dropped_chunks
        self._bridge_dropped_chunks = dropped
        if previous is not None and dropped > previous:
            logger.warning(
                "NAO audio bridge dropped chunks: "
                f"{previous} -> {dropped}"
            )
