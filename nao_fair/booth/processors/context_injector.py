from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import TYPE_CHECKING

from pipecat.frames.frames import Frame, LLMContextFrame, LLMUpdateSettingsFrame
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection
from pipecat.services.openai.responses.llm import OpenAIResponsesLLMSettings

import pytz

from nao_fair.defaults import PERSONALITY, SYSTEM_PROMPT
from nao_fair.booth.processors.stop_phrase import contains_stop_phrase
from nao_fair.booth.session_log import session_log

if TYPE_CHECKING:
    from naoqi_control import NaoController

StopPhraseCallback = Callable[[], Awaitable[None]]

_WAVE_INSTRUCTION = (
    "Wenn jemand dich bittet zu winken oder zu grüßen, rufe das Werkzeug wave "
    "mit der korrekten Seite auf. Wenn jemand sich verabschiedet, rufe das "
    "Werkzeug farewell auf. Bei Zustimmung oder Ja rufe das Werkzeug nod auf. "
    "Bei Ablehnung oder Nein rufe das Werkzeug shake auf. Bestätige die Geste "
    "in einem kurzen Satz."
)


class NaoSystemInstruction:
    def __init__(
        self,
        robot_name: str,
        *,
        mention_wave_tool: bool = False,
        sections_provider: Callable[[], list[str]] | None = None,
    ):
        self.robot_name = robot_name
        self.mention_wave_tool = mention_wave_tool
        self._sections_provider = sections_provider

    @classmethod
    def from_nao_controller(
        cls,
        nao_controller: NaoController,
        *,
        mention_wave_tool: bool = False,
    ) -> NaoSystemInstruction:
        return cls(
            robot_name=nao_controller.robot_name or "NAO",
            mention_wave_tool=mention_wave_tool,
        )

    def build_system_instruction(self) -> str:
        tz = pytz.timezone("Europe/Berlin")
        local_time = datetime.now(tz)

        current_time = local_time.strftime("%H:%M")
        current_date = local_time.strftime("%Y-%m-%d")
        current_day = local_time.strftime("%A")
        current_month = local_time.strftime("%B")
        current_year = local_time.strftime("%Y")

        if self._sections_provider is not None:
            sections = list(self._sections_provider())
        else:
            sections = [
                PERSONALITY.replace("{robot_name}", self.robot_name),
                SYSTEM_PROMPT,
            ]
            if self.mention_wave_tool:
                sections.append(_WAVE_INSTRUCTION)
        sections.append(
            f"""## Zeitlicher Kontext:
- Uhrzeit: {current_time}
- Datum: {current_date}
- Tag: {current_day}
- Monat: {current_month}
- Jahr: {current_year}"""
        )

        return "\n\n".join(section.strip() for section in sections if section.strip())


class EnvironmentContextInjector(FrameProcessor):
    def __init__(
        self,
        robot_name: str,
        *,
        on_stop_phrase: StopPhraseCallback | None = None,
        mention_wave_tool: bool = False,
        sections_provider: Callable[[], list[str]] | None = None,
        llm_enabled: Callable[[], bool] | None = None,
    ):
        super().__init__()
        self.robot_name = robot_name
        self.nao_system_instruction = NaoSystemInstruction(
            robot_name,
            mention_wave_tool=mention_wave_tool,
            sections_provider=sections_provider,
        )
        self._on_stop_phrase = on_stop_phrase
        self._llm_enabled = llm_enabled

    @classmethod
    def from_nao_controller(
        cls,
        nao_controller: NaoController,
        *,
        on_stop_phrase: StopPhraseCallback | None = None,
        mention_wave_tool: bool = False,
    ) -> EnvironmentContextInjector:
        return cls(
            robot_name=nao_controller.robot_name or "NAO",
            on_stop_phrase=on_stop_phrase,
            mention_wave_tool=mention_wave_tool,
        )

    async def process_frame(
        self,
        frame: Frame,
        direction: FrameDirection,
    ):
        await super().process_frame(frame, direction)

        if (
            isinstance(frame, LLMContextFrame)
            and direction == FrameDirection.DOWNSTREAM
            and self._on_stop_phrase is not None
            and _latest_user_text_has_stop_phrase(frame, self.robot_name)
        ):
            session_log.event("Stop phrase")
            await self._on_stop_phrase()
            return

        if (
            isinstance(frame, LLMContextFrame)
            and direction == FrameDirection.DOWNSTREAM
            and self._llm_enabled is not None
            and not self._llm_enabled()
        ):
            return

        if (
            isinstance(frame, LLMContextFrame)
            and direction == FrameDirection.DOWNSTREAM
        ):
            await self.push_frame(
                LLMUpdateSettingsFrame(
                    delta=OpenAIResponsesLLMSettings(
                        system_instruction=self.nao_system_instruction.build_system_instruction()
                    )
                ),
                direction,
            )

        await self.push_frame(frame, direction)


def _latest_user_text_has_stop_phrase(
    frame: LLMContextFrame, robot_name: str
) -> bool:
    for message in reversed(frame.context.messages):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return contains_stop_phrase(content, robot_name=robot_name)
        return False
    return False
