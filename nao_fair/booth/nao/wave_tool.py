"""LLM tool that waves the sitting NAO's hand when asked."""

from __future__ import annotations

from typing import Literal

from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.services.llm_service import FunctionCallParams

from naoqi_control import NaoController

_SIDES = frozenset({"left", "right", "both"})


def wave_tool(nao: NaoController) -> FunctionSchema:
    """Tool the model calls when the user asks the robot to wave."""

    async def wave(params: FunctionCallParams) -> None:
        raw = params.arguments.get("side", "left")
        side = str(raw).strip().lower()
        if side not in _SIDES:
            await params.result_callback(
                {
                    "ok": False,
                    "error": "side must be left, right, or both",
                }
            )
            return

        sides: list[Literal["left", "right"]]
        if side == "both":
            sides = ["left", "right"]
        elif side == "left":
            sides = ["left"]
        else:
            sides = ["right"]
        nao.motion.wave(sides)
        await params.result_callback({"ok": True, "waving": sides})

    return FunctionSchema(
        name="wave",
        description=(
            "Ausladendes Winken zum Grüßen. Rufe dieses Werkzeug auf, wenn der "
            "Nutzer dich bittet zu winken oder zu grüßen. Zum Verabschieden nutze "
            "farewell. Der Roboter sitzt; es bewegen sich nur die Arme. left ist "
            "die linke Hand des Roboters, right die rechte, both beide Hände. "
            "Nutze left, wenn keine Seite genannt wird."
        ),
        properties={
            "side": {
                "type": "string",
                "enum": ["left", "right", "both"],
                "description": (
                    "Welche Hand winken soll: left, right oder both."
                ),
            }
        },
        required=["side"],
        handler=wave,
    )


def farewell_tool(nao: NaoController) -> FunctionSchema:
    """Tool the model calls when the user is leaving."""

    async def farewell(params: FunctionCallParams) -> None:
        raw = params.arguments.get("side", "left")
        side = str(raw).strip().lower()
        if side not in _SIDES:
            await params.result_callback(
                {
                    "ok": False,
                    "error": "side must be left, right, or both",
                }
            )
            return

        sides: list[Literal["left", "right"]]
        if side == "both":
            sides = ["left", "right"]
        elif side == "left":
            sides = ["left"]
        else:
            sides = ["right"]
        nao.motion.farewell(sides)
        await params.result_callback({"ok": True, "farewell": sides})

    return FunctionSchema(
        name="farewell",
        description=(
            "Kurzes Verabschieden mit einer kleinen Armbewegung, ohne das "
            "ausladende Winken. Rufe dieses Werkzeug auf, wenn der Nutzer sich "
            "verabschiedet oder gehen möchte. Zum Grüßen oder wenn ausdrücklich "
            "gewinkt werden soll, nutze wave. left ist die linke Hand, right die "
            "rechte, both beide. Nutze left, wenn keine Seite genannt wird."
        ),
        properties={
            "side": {
                "type": "string",
                "enum": ["left", "right", "both"],
                "description": (
                    "Welche Hand sich verabschiedet: left, right oder both."
                ),
            }
        },
        required=["side"],
        handler=farewell,
    )
