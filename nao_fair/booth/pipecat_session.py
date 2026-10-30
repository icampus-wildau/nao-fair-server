"""Pipecat pipeline session for one WebSocket connection."""

from __future__ import annotations
import asyncio
import os
import random
import time
from typing import TYPE_CHECKING

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import InputAudioRawFrame, LLMMessagesAppendFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair, LLMUserAggregatorParams
from pipecat.services.gladia.config import (
    CustomSpellingConfig,
    CustomVocabularyConfig,
    LanguageConfig,
    PreProcessingConfig,
    RealtimeProcessingConfig,
)
from pipecat.transcriptions.language import Language
from pipecat.transports.websocket.client import WebsocketClientParams, WebsocketClientTransport
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.base_input import BaseInputTransport
from pipecat.turns.user_start import VADUserTurnStartStrategy
from pipecat.turns.user_stop import TurnAnalyzerUserTurnStopStrategy
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.workers.runner import WorkerRunner
from nao_fair.booth.services.pausable_gladia import PausableGladiaSTTService
from pipecat.processors.aggregators.llm_text_processor import LLMTextProcessor


# from audio_pipeline.processors.audio_frame_inspector import AudioFrameInspector
# from audio_pipeline.processors.context_bridge import ContextBridgeProcessor
# from audio_pipeline.protocol import CHANNELS, SAMPLE_RATE
# from audio_pipeline.serializers.nao_serializer import NaoTransportSerializer
# from audio_pipeline.processors.status_updater_processor import EndPipelineStatusUpdater, PreLLMStatusUpdater
from pipecat.services.openai.responses.llm import OpenAIResponsesHttpLLMService

if TYPE_CHECKING:
    from naoqi_control import NaoController

from nao_fair.booth.config import AppConfig
from nao_fair.booth.nao.nao_nlp_manager import NaoNlpManager
from nao_fair.booth.processors.audio_level_probe import AudioLevelProbe
from nao_fair.booth.processors.context_injector import EnvironmentContextInjector
from nao_fair.booth.processors.stop_phrase import StopPhraseProcessor, contains_stop_phrase
from nao_fair.booth.session_log import session_log

from dotenv import load_dotenv
load_dotenv(override=False)


def install_single_flight_connect(session: object) -> None:
    """Let only one ``connect()`` dial, and redial a socket that is already dead.

    Pipecat pushes ``StartFrame`` before ``start()`` returns, so the input
    and output transports call ``connect()`` at the same time. Both see an
    empty socket and open one. The bridge keeps the later handshake and
    closes the earlier one. Pipecat also refuses to dial again while that
    closed socket object is still stored.
    """
    original = session.connect
    lock = asyncio.Lock()
    session._connect_lock = lock  # type: ignore[attr-defined]
    session._connect_original = original  # type: ignore[attr-defined]

    async def connect() -> None:
        async with lock:
            websocket = getattr(session, "_websocket", None)
            if websocket is not None and bool(session.is_connected):
                return
            if websocket is not None:
                session._websocket = None  # type: ignore[attr-defined]
            await original()

    session.connect = connect  # type: ignore[method-assign]


class BrowserAudioInput(BaseInputTransport):
    async def start(self, frame):
        await super().start(frame)
        await self.set_transport_ready(frame)


class PipecatSession:
    """Runs a Pipecat pipeline for one accepted WebSocket connection."""

    def __init__(
        self,
        nao_controller: NaoController | None,
        *,
        config: AppConfig | None = None,
        gladia_api_key: str | None = None,
        openai_api_key: str | None = None,
        vocabulary: list[str] | None = None,
        sections_provider=None,
        on_visitor_line=None,
        on_nao_line=None,
        on_state=None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        microphone_enabled: bool = True,
        stt_enabled: bool = True,
        llm_enabled: bool = True,
        announce: bool = True,
        bind_buttons: bool = True,
        external_context_reset: bool = False,
        browser_speech=None,
    ) -> None:
        self._config = config if config is not None else AppConfig.load()
        self._nao_controller = nao_controller
        self._gladia_api_key = gladia_api_key
        self._openai_api_key = openai_api_key
        self._vocabulary = vocabulary
        self._sections_provider = sections_provider
        self._on_visitor_line = on_visitor_line
        self._on_nao_line = on_nao_line
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._llm_enabled = llm_enabled
        self._announce = announce

        self._robot_name = (nao_controller.robot_name or "NAO") if nao_controller else "NAO"
        tools = []
        if nao_controller is not None:
            from nao_fair.booth.nao.head_tool import nod_tool, shake_tool
            from nao_fair.booth.nao.wave_tool import farewell_tool, wave_tool

            tools = [wave_tool(nao_controller), farewell_tool(nao_controller),
                     nod_tool(nao_controller), shake_tool(nao_controller)]
        self._context = LLMContext(tools=tools)
        self.ready = asyncio.Event()
        self.browser_input = None

        self._nao_nlp_manager = NaoNlpManager(
            self._nao_controller,
            context=self._context,
            context_reset_timeout_secs=self._config.context_reset_timeout_secs,
            listening_timeout_secs=self._config.listening_timeout_secs,
            thinking_timeout_secs=self._config.thinking_timeout_secs,
            unmute_delay_secs=self._config.unmute_delay_secs,
            bind_buttons=bind_buttons,
            external_context_reset=external_context_reset,
            microphone_enabled=microphone_enabled,
            stt_enabled=stt_enabled,
            llm_enabled=llm_enabled,
            on_state=on_state,
            speech=browser_speech,
        )

        self._raw_filter = self._nao_nlp_manager.raw_audio_filter_frame_processor
        self._speech_output = self._nao_nlp_manager.speech_output_frame_processor
        self._transport: WebsocketClientTransport | None = None

        self._worker: PipelineWorker | None = None
        self._runner: WorkerRunner | None = None
        self._stopped = False
        self._audio_reconnect_task: asyncio.Task | None = None
        self._audio_reconnect_delay = 0.5
        self._audio_connected_at: float | None = None
        self._llm_model = self._config.llm_model
        self._nao_bridge_url = None
        self._SAMPLE_RATE = 16000
        self._NUM_CHANNELS = 1
        if nao_controller is not None:
            from nao_websocket_bridge_protocol import CHANNELS, SAMPLE_RATE, websocket_url

            self._nao_bridge_url = websocket_url(
                nao_controller.ip, self._config.audio_bridge_port,
                getattr(self._config, "nao_bridge_token", ""),
            )
            self._SAMPLE_RATE = SAMPLE_RATE
            self._NUM_CHANNELS = CHANNELS

    async def run(self) -> None:
        """Build the pipeline and block until the worker finishes."""

        session_log.verbose(
            f"NAO bridge URL: {self._nao_bridge_url}"
        )
        session_log.verbose(f"Sample rate: {self._SAMPLE_RATE}")
        session_log.verbose(f"Channels: {self._NUM_CHANNELS}")
        session_log.verbose("Creating transport...")

        transport = None
        if self._nao_bridge_url is not None:
            from nao_fair.booth.serializer.nao_audio_bridge_serializer import NaoAudioBridgeSerializer

            transport = WebsocketClientTransport(
                uri=self._nao_bridge_url,
                params=WebsocketClientParams(
                    audio_in_enabled=True, audio_out_enabled=False,
                    audio_in_sample_rate=self._SAMPLE_RATE,
                    audio_in_channels=self._NUM_CHANNELS,
                    serializer=NaoAudioBridgeSerializer(),
                ),
            )
            install_single_flight_connect(transport._session)
            audio_input = transport.input()
        else:
            self.browser_input = BrowserAudioInput(TransportParams(
                audio_in_enabled=True, audio_in_sample_rate=16000, audio_in_channels=1,
            ))
            audio_input = self.browser_input

        gladia_language = self._config.gladia_language
        try:
            language = Language(gladia_language)
        except ValueError:
            language = Language.DE if gladia_language == "de" else Language.EN

        if self._vocabulary is None:
            vocabulary = [
                "NAO",
                "Telematik",
                "TH Wildau",
                "Wildau",
                "RoboticLab",
                "EDIH",
                "Stop",
                "Stopp",
            ]
        else:
            vocabulary = list(self._vocabulary)
        robot_name = self._robot_name
        if robot_name and robot_name not in vocabulary:
            vocabulary.append(robot_name)

        gladia_key = self._gladia_api_key or os.environ.get("GLADIA_API_KEY")
        if self._nao_nlp_manager.stt_enabled and not gladia_key:
            raise RuntimeError("gladia secret is missing")

        stt = PausableGladiaSTTService(
            api_key=gladia_key or "",
            region=self._config.gladia_region,
            settings=PausableGladiaSTTService.Settings(
                language_config=LanguageConfig(
                    languages=[language],
                    code_switching=False,
                ),
                endpointing=self._config.gladia_endpointing,
                maximum_duration_without_endpointing=(
                    self._config.gladia_max_utterance_secs
                ),
                pre_processing=PreProcessingConfig(
                    audio_enhancer=self._config.gladia_audio_enhancer,
                ),
                realtime_processing=RealtimeProcessingConfig(
                    custom_vocabulary=bool(vocabulary),
                    custom_vocabulary_config=(
                        CustomVocabularyConfig(
                            vocabulary=vocabulary,
                            default_intensity=0.6,
                        )
                        if vocabulary
                        else None
                    ),
                    custom_spelling=True,
                    custom_spelling_config=CustomSpellingConfig(
                        spelling_dictionary={"NAO": ["Nau", "Now"]},
                    ),
                ),
            ),
        )
        # Idle and speaking start muted. Dropping audio is not enough: the
        # live session stays closed until the front head button starts listening.
        self._raw_filter.attach_stt(stt)

        openai_key = self._openai_api_key or os.getenv("OPENAI_API_KEY")
        if self._llm_enabled and not openai_key:
            raise RuntimeError("openai secret is missing")
        llm_settings = OpenAIResponsesHttpLLMService.Settings(
            model=self._llm_model,
        )
        if self._temperature is not None:
            llm_settings.temperature = self._temperature
        if self._max_tokens is not None:
            llm_settings.max_completion_tokens = self._max_tokens
        llm = OpenAIResponsesHttpLLMService(
            api_key=openai_key or "llm-disabled",
            settings=llm_settings,
        )
        # Force lazy resource loading
        _ = llm._client.responses

        environment_context_injector = EnvironmentContextInjector(
            self._robot_name,
            on_stop_phrase=self._nao_nlp_manager.on_back_head_pressed,
            mention_wave_tool=False,
            sections_provider=self._sections_provider,
            llm_enabled=lambda: self._nao_nlp_manager.llm_enabled,
        )

        llm_text_processor = LLMTextProcessor()

        # For the internal VAD Analyzer we need a LLMContext and a LLMContextAggregator

        user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
            self._context,
            user_params=LLMUserAggregatorParams(
                vad_analyzer=SileroVADAnalyzer(
                    params=VADParams(
                        confidence=0.7,
                        start_secs=0.2,
                        stop_secs=0.2,
                        min_volume=0.6,
                    )
                ),
                user_turn_strategies=UserTurnStrategies(
                    start=[
                        VADUserTurnStartStrategy(),
                    ],
                    stop=[TurnAnalyzerUserTurnStopStrategy(
                        turn_analyzer=LocalSmartTurnAnalyzerV3()
                    )]
                ),
                # # No actual turn detection yet.
                # user_turn_strategies=ExternalUserTurnStrategies(),
            ),
        )

        self._user_aggregator = user_aggregator
        stop_phrase = StopPhraseProcessor(
            self._nao_nlp_manager,
            user_aggregator,
            on_final_transcript=self._on_visitor_line,
        )
        if self._on_nao_line is not None:
            self._speech_output.on_phrase = self._on_nao_line

        # Assistant aggregator sits after transport.output() so it records the
        # completed turn; ContextBridge then sends the full context upstream
        # as a WebSocket control message for the chat UI.
        stages: list = [
            self._nao_nlp_manager,
            audio_input,
        ]
        if session_log.panel and session_log.verbosity >= 1:
            stages.append(AudioLevelProbe())
        stages.extend(
            [
                self._raw_filter,
                stt,
                stop_phrase,
                user_aggregator,
                environment_context_injector,
                llm,
                llm_text_processor,
                self._speech_output,
                assistant_aggregator,
            ]
        )
        if transport is not None:
            stages.append(transport.output())
        pipeline = Pipeline(stages)
        worker = PipelineWorker(
            pipeline,
            params=PipelineParams(
                audio_in_sample_rate=self._SAMPLE_RATE,
            ),
            enable_rtvi=False,
            enable_turn_tracking=False,
            idle_timeout_secs=None,
        )
        runner = WorkerRunner(handle_sigint=False)

        @worker.event_handler("on_pipeline_started")
        async def on_pipeline_started(worker, frame):
            self.ready.set()

        if transport is not None:
            @transport.event_handler("on_disconnected")
            async def on_audio_disconnected(transport, *args):
                await self._handle_audio_disconnected()

        self._transport = transport
        self._worker = worker
        self._runner = runner

        if self._announce and self._nao_controller is not None:
            from naoqi_control import say_timeout_secs, speak_text

            await speak_text(
                self._nao_controller,
                "Los",
                timeout=say_timeout_secs("Los"),
            )

        await runner.add_workers(worker)
        try:
            await runner.run()
        finally:
            self._stopped = True
            await self._cancel_audio_reconnect()

    async def _handle_audio_disconnected(self) -> None:
        """Reconnect the bridge socket without dropping the conversation."""
        from nao_websocket_bridge_client.reconnect import (
            RECONNECT_BASE_SECS as _RECONNECT_BASE_SECS,
            RECONNECT_STABLE_SECS as _RECONNECT_STABLE_SECS,
            next_reconnect_delay as next_audio_reconnect_delay,
        )

        if self._stopped:
            return
        transport = self._transport
        if transport is not None and transport._session.is_connected:
            return
        task = self._audio_reconnect_task
        if task is not None and not task.done():
            return
        connected_at = getattr(self, "_audio_connected_at", None)
        held = (
            _RECONNECT_STABLE_SECS
            if connected_at is None
            else time.monotonic() - connected_at
        )
        previous = getattr(self, "_audio_reconnect_delay", _RECONNECT_BASE_SECS)
        self._audio_reconnect_delay = next_audio_reconnect_delay(previous, held)
        self._audio_connected_at = None
        session_log.event("Audio disconnected")
        self._audio_reconnect_task = asyncio.create_task(
            self._audio_reconnect_loop(),
            name="audio_bridge_reconnect",
        )

    async def _audio_reconnect_loop(self) -> None:
        from nao_websocket_bridge_client.reconnect import (
            RECONNECT_BASE_SECS as _RECONNECT_BASE_SECS,
            next_reconnect_delay as next_audio_reconnect_delay,
        )

        try:
            while not self._stopped:
                delay = self._audio_reconnect_delay + random.uniform(0.0, 0.25)
                await asyncio.sleep(delay)
                if self._stopped:
                    return
                try:
                    await self._reopen_audio_socket()
                    self._audio_connected_at = time.monotonic()
                    self._audio_reconnect_delay = _RECONNECT_BASE_SECS
                    session_log.event("Audio reconnected")
                    return
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    session_log.error(f"Audio reconnect failed: {exc}")
                    self._audio_reconnect_delay = next_audio_reconnect_delay(
                        self._audio_reconnect_delay,
                        0.0,
                    )
        finally:
            current = asyncio.current_task()
            if self._audio_reconnect_task is current:
                self._audio_reconnect_task = None

    async def _reopen_audio_socket(self) -> None:
        """Dial the bridge again on the transport session already in the pipeline."""
        transport = self._transport
        if transport is None:
            raise RuntimeError("audio transport is not running")
        session = transport._session
        lock = getattr(session, "_connect_lock", None)
        if lock is None:
            await self._dial_audio_socket(session)
            return
        async with lock:
            await self._dial_audio_socket(session)

    async def _dial_audio_socket(self, session: object) -> None:
        websocket = session._websocket
        session._websocket = None
        if websocket is not None:
            try:
                await websocket.close()
            except Exception:
                pass
        original = getattr(session, "_connect_original", None)
        if original is None:
            await session.connect()
        else:
            await original()
        if not session.is_connected:
            raise ConnectionError("audio bridge WebSocket did not connect")

    async def _cancel_audio_reconnect(self) -> None:
        task = self._audio_reconnect_task
        if task is None or task.done():
            self._audio_reconnect_task = None
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        self._audio_reconnect_task = None

    async def discard_context(self) -> None:
        """Drop the model conversation context. The transcript is not stored here."""
        self._context.set_messages([])

    async def start_conversation(self) -> None:
        from nao_fair.booth.nao.nao_nlp_manager import NAOState

        await self._nao_nlp_manager.transition_to_state(NAOState.LISTENING)

    async def send_message(self, text: str) -> None:
        """Submit a completed user utterance to the conversation pipeline."""
        from nao_fair.booth.nao.nao_nlp_manager import NAOState

        if not self.ready.is_set() or self._stopped:
            raise RuntimeError("Die Gesprächspipeline ist noch nicht bereit.")
        manager = self._nao_nlp_manager
        if not manager.llm_enabled:
            raise RuntimeError("Das Sprachmodell ist nicht aktiviert.")
        if manager.state not in (NAOState.IDLE, NAOState.LISTENING):
            raise RuntimeError("Bitte die laufende Antwort abwarten.")
        if manager.state == NAOState.IDLE:
            await manager.transition_to_state(NAOState.LISTENING)
        session_log.user(text)
        if self._on_visitor_line is not None:
            await self._on_visitor_line(text)
        if contains_stop_phrase(text, robot_name=self._robot_name):
            await manager.on_back_head_pressed()
            return
        await manager.transition_to_state(NAOState.THINKING)
        await self._user_aggregator.queue_frame(LLMMessagesAppendFrame(
            messages=[{"role": "user", "content": text}], run_llm=True,
        ))

    async def push_audio(self, audio: bytes) -> None:
        if self.browser_input is not None and self.ready.is_set() and not self._stopped:
            await self.browser_input.push_audio_frame(InputAudioRawFrame(
                audio=audio, sample_rate=self._SAMPLE_RATE, num_channels=self._NUM_CHANNELS,
            ))

    async def stop(self, reason: str = "Session stopped") -> None:
        """Cancel the worker when the host shuts down."""
        if self._stopped:
            return
        self._stopped = True
        await self._cancel_audio_reconnect()

        if self._worker is not None:
            await self._worker.cancel(reason=reason)

        if self._runner is not None:
            await self._runner.cancel()
