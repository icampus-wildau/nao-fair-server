"""LLM tools for a small yes-nod and a small no-shake."""

from __future__ import annotations

from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.services.llm_service import FunctionCallParams

from naoqi_control import NaoController


def nod_tool(nao: NaoController) -> FunctionSchema:
    """Tool the model calls when it agrees or says yes."""

    async def nod(params: FunctionCallParams) -> None:
        nao.motion.nod()
        await params.result_callback({"ok": True, "gesture": "nod"})

    return FunctionSchema(
        name="nod",
        description=(
            "Kleines Nicken mit dem Kopf. Rufe dieses Werkzeug auf, wenn du "
            "einer Aussage zustimmst oder etwas bejahst, also bei Zustimmung "
            "und bei Ja. Nicht bei Ablehnung oder Nein; dafür nutze shake."
        ),
        properties={},
        required=[],
        handler=nod,
    )


def shake_tool(nao: NaoController) -> FunctionSchema:
    """Tool the model calls when it disagrees or says no."""

    async def shake(params: FunctionCallParams) -> None:
        nao.motion.shake()
        await params.result_callback({"ok": True, "gesture": "shake"})

    return FunctionSchema(
        name="shake",
        description=(
            "Kleines Kopfschütteln. Rufe dieses Werkzeug auf, wenn du etwas "
            "ablehnst oder verneinst, also bei Ablehnung und bei Nein. Nicht "
            "bei Zustimmung oder Ja; dafür nutze nod."
        ),
        properties={},
        required=[],
        handler=shake,
    )
