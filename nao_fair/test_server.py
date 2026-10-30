"""Test-only process for the operator console.

``python -m nao_fair`` / ``main()`` / ``create_app()`` do not import this
module and grow no route, flag, or environment variable for the driver.
"""

from __future__ import annotations

import asyncio

import uvicorn
from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.responses import JSONResponse

from nao_fair.app import create_app
from nao_fair.runtime import ApiError, Runtime
from nao_fair.test_driver import TestDriver

_router = APIRouter()


def create_test_app() -> FastAPI:
    app = create_app(driver=TestDriver(name="NAO-Messe"), serve_frontend=False)
    app.include_router(_router)
    return app


def _listener_count(runtime: Runtime) -> int:
    with runtime.hub._lock:
        return len(runtime.hub._subscribers)


async def _wait_for_listener(runtime: Runtime, timeout: float = 10.0) -> None:
    """Let an open console subscribe before events are published.

    A press is not part of the picture, so it is lost if nobody is listening
    yet. ``open_stream`` snapshots that picture under the runtime lock; taking
    the same lock afterwards keeps the snapshot ahead of these events.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while _listener_count(runtime) == 0 and loop.time() < deadline:
        await asyncio.sleep(0.02)

    def barrier() -> None:
        with runtime.lock:
            return None

    await asyncio.to_thread(barrier)


async def _apply(runtime: Runtime, driver: TestDriver, events: list) -> None:
    for event in events:
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        if kind == "picture":
            await _apply_picture(runtime, driver, event)
        elif kind == "conversation.state_changed":
            driver.set_conversation_state(str(event.get("state") or ""))
        elif kind == "transcript.message":
            driver.append_transcript(str(event.get("speaker") or ""), str(event.get("text") or ""))
        elif kind == "sensor.pressed":
            driver.press(str(event.get("sensor_id") or ""))


async def _apply_picture(runtime: Runtime, driver: TestDriver, event: dict) -> None:
    connection = event.get("connection")
    if not isinstance(connection, dict):
        return
    state = connection.get("state")
    if state == "connected":
        address = connection.get("address")
        result = await runtime.connect("" if address is None else str(address))
        if result.get("state") != "connected":
            failure = result.get("failure") or "not connected"
            raise ApiError(409, str(failure))
        driver.set_conversation_state(str(event.get("conversation_state") or "idle"))
        transcript = event.get("transcript") or []
        if isinstance(transcript, list):
            for line in transcript:
                if not isinstance(line, dict):
                    continue
                speaker = line.get("speaker")
                text = line.get("text")
                if speaker in ("visitor", "nao") and isinstance(text, str):
                    driver.append_transcript(speaker, text)
    elif state == "disconnected":
        await asyncio.to_thread(driver.end_connection)


@_router.get("/api/test/driver")
def get_test_driver() -> dict:
    return {"ok": True}


@_router.post("/api/test/driver")
async def post_test_driver(request: Request) -> Response:
    try:
        payload = await request.json()
    except Exception as exc:
        raise ApiError(400, "invalid JSON") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
        raise ApiError(400, "events must be a list")
    runtime: Runtime = request.app.state.runtime
    driver = runtime.driver
    if not isinstance(driver, TestDriver):
        raise ApiError(500, "test driver is not attached")
    await _wait_for_listener(runtime)
    try:
        await _apply(runtime, driver, payload["events"])
    except ApiError:
        raise
    except (ValueError, RuntimeError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return Response(status_code=204)


def main() -> None:
    uvicorn.run(create_test_app(), host="127.0.0.1", port=8000, log_level="info")


if __name__ == "__main__":
    main()
