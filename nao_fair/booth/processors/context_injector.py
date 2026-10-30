from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from pipecat.frames.frames import Frame, LLMContextFrame, LLMUpdateSettingsFrame
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection
from pipecat.services.openai.responses.llm import OpenAIResponsesLLMSettings

import pytz

from nao_fair.booth.processors.stop_phrase import contains_stop_phrase
from nao_fair.booth.session_log import session_log

if TYPE_CHECKING:
    from naoqi_control import NaoController

StopPhraseCallback = Callable[[], Awaitable[None]]

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PERSONALITY_PATH = _REPO_ROOT / "prompts" / "personality.md"
_EVENT_CONTEXT_PATH = _REPO_ROOT / "prompts" / "event_context.md"
_ANSWERING_HINTS_PATH = _REPO_ROOT / "prompts" / "answering_hints.md"
_LOCAL_CONTEXT_PATH = _REPO_ROOT / "prompts" / "local_context.md"
_TECHNICAL_CONTEXT_PATH = _REPO_ROOT / "prompts" / "technical_context.md"

_PERSONALITY_FALLBACK = (
    'Du bist ein NAO namens "{robot_name}" und antwortest kurz und freundlich.'
)
_ANSWERING_HINTS_FALLBACK = (
    "Antworte ohne Emojis; bei unverständlicher Eingabe bitte kurz um Wiederholung."
)
_LOCAL_CONTEXT_FALLBACK = (
    "Du befindest dich im Showroom Telematik an der TH Wildau."
)
_TECHNICAL_CONTEXT_FALLBACK = 'Du bist ein NAO-Roboter namens "{robot_name}".'

_WAVE_INSTRUCTION = (
    "Wenn jemand dich bittet zu winken oder zu grüßen, rufe das Werkzeug wave "
    "mit der korrekten Seite auf. Wenn jemand sich verabschiedet, rufe das "
    "Werkzeug farewell auf. Bei Zustimmung oder Ja rufe das Werkzeug nod auf. "
    "Bei Ablehnung oder Nein rufe das Werkzeug shake auf. Bestätige die Geste "
    "in einem kurzen Satz."
)


def _strip_html_comments(text: str) -> str:
    result: list[str] = []
    index = 0
    while index < len(text):
        start = text.find("<!--", index)
        if start == -1:
            result.append(text[index:])
            break
        result.append(text[index:start])
        end = text.find("-->", start + 4)
        if end == -1:
            break
        index = end + 3
    return "".join(result)


def _load_text_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    text = _strip_html_comments(path.read_text(encoding="utf-8"))
    if not text.strip():
        return None
    return text


def _load_substituted(path: Path, robot_name: str, fallback: str) -> str:
    raw = _load_text_file(path)
    if raw is None:
        return fallback.format(robot_name=robot_name)
    return raw.replace("{robot_name}", robot_name)


def _load_personality(robot_name: str) -> str:
    return _load_substituted(_PERSONALITY_PATH, robot_name, _PERSONALITY_FALLBACK)


def _load_answering_hints(robot_name: str) -> str:
    return _load_substituted(
        _ANSWERING_HINTS_PATH, robot_name, _ANSWERING_HINTS_FALLBACK
    )


def _load_local_context(robot_name: str) -> str:
    return _load_substituted(
        _LOCAL_CONTEXT_PATH, robot_name, _LOCAL_CONTEXT_FALLBACK
    )


def _load_technical_context(robot_name: str, *, mention_wave_tool: bool) -> str:
    technical = _load_substituted(
        _TECHNICAL_CONTEXT_PATH, robot_name, _TECHNICAL_CONTEXT_FALLBACK
    )
    if mention_wave_tool:
        technical = f"{technical.rstrip()}\n{_WAVE_INSTRUCTION}"
    return technical


def _load_event_context() -> str | None:
    return _load_text_file(_EVENT_CONTEXT_PATH)


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
                _load_personality(self.robot_name),
                _load_answering_hints(self.robot_name),
            ]

            event_context = _load_event_context()
            if event_context is not None:
                sections.append(event_context)

            sections.append(_load_local_context(self.robot_name))
            sections.append(
                _load_technical_context(
                    self.robot_name,
                    mention_wave_tool=self.mention_wave_tool,
                )
            )
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
