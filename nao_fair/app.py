"""HTTP and WebSocket routes for the operator console."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from pydantic import ValidationError

from nao_fair.catalog import PRESET_KINDS, SETUP_SLOTS, capabilities
from nao_fair.runtime import ApiError, Runtime, _validation_message
from nao_fair.secrets import SLOTS, SecretStore
from nao_fair.seed import seed_if_needed
from nao_fair.settings import Settings
from nao_fair.store import Store
from nao_fair.test_driver import TestDriver
from nao_fair.models import (
    AudioUpdate,
    ApplyRequest,
    ConfirmDelete,
    ConnectRequest,
    ContentUpdate,
    ContentWrite,
    MotionRequest,
    PresetUpdate,
    PresetWrite,
    SecretWrite,
    SetupWrite,
    SpeakRequest,
)


def create_app(
    *,
    driver: TestDriver | None = None,
    serve_frontend: bool = True,
    db_path=None,
    secrets_path=None,
    config_path=None,
    prompts_dir=None,
    bridge_port: int | None = None,
    nao_ports: list[int] | None = None,
    bridge_token: str | None = None,
) -> FastAPI:
    """Build the fair server. Production calls this with no driver."""
    settings = Settings.from_env()
    store = Store(db_path or settings.db_path)
    store.open()
    seed_if_needed(
        store,
        config_path=config_path or settings.config_path,
        prompts_dir=prompts_dir or settings.prompts_dir,
    )
    runtime = Runtime(
        store,
        SecretStore(secrets_path or settings.secrets_path),
        driver=driver,
        bridge_port=bridge_port if bridge_port is not None else settings.bridge_port,
        nao_ports=nao_ports if nao_ports is not None else list(settings.nao_ports),
        bridge_token=settings.bridge_token if bridge_token is None else bridge_token,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        import asyncio

        runtime.attach_loop(asyncio.get_running_loop())
        yield
        await runtime.shutdown()

    app = FastAPI(title="NAO Fair", lifespan=lifespan)
    app.state.runtime = runtime
    app.include_router(router)
    frontend_dir = settings.frontend_dir.resolve()
    frontend_index = (frontend_dir / "index.html").resolve()
    if serve_frontend and frontend_index.is_relative_to(frontend_dir) and frontend_index.is_file():
        @app.api_route("/{path:path}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
        async def frontend(path: str, request: Request):
            if path == "api" or path.startswith("api/") or request.method not in ("GET", "HEAD"):
                raise HTTPException(status_code=404)
            target = (frontend_dir / path).resolve()
            if not target.is_relative_to(frontend_dir):
                raise HTTPException(status_code=404)
            if target.is_file():
                return FileResponse(target)
            if path.startswith("assets/") or target.suffix:
                raise HTTPException(status_code=404)
            return FileResponse(frontend_index)
    app.add_exception_handler(ApiError, _api_error)
    return app


def _api_error(_request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse({"error": exc.message}, status_code=exc.status)


router = APIRouter(prefix="/api")


@router.websocket("/browser-audio")
async def browser_microphone(websocket: WebSocket) -> None:
    from nao_fair.browser_audio import browser_audio

    await browser_audio(websocket)


def _runtime(request: Request) -> Runtime:
    return request.app.state.runtime


async def _body(request: Request, model):
    try:
        payload = await request.json()
    except Exception as exc:
        raise ApiError(400, "invalid JSON") from exc
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise ApiError(422, _validation_message(exc)) from exc


@router.websocket("/events")
async def events(websocket: WebSocket) -> None:
    runtime: Runtime = websocket.app.state.runtime
    await websocket.accept()
    queue, picture = runtime.open_stream()
    try:
        await websocket.send_json(picture)
        while True:
            event_task = asyncio.create_task(queue.get())
            receive_task = asyncio.create_task(websocket.receive())
            try:
                done, _ = await asyncio.wait(
                    (event_task, receive_task), return_when=asyncio.FIRST_COMPLETED
                )
                if receive_task in done and receive_task.result()["type"] == "websocket.disconnect":
                    break
                if event_task in done:
                    await websocket.send_json(event_task.result())
            finally:
                for task in (event_task, receive_task):
                    if not task.done():
                        task.cancel()
                await asyncio.gather(event_task, receive_task, return_exceptions=True)
    except WebSocketDisconnect:
        return
    finally:
        runtime.hub.unsubscribe(queue)


@router.post("/robot/connect")
async def connect_robot(request: Request) -> dict:
    body = await _body(request, ConnectRequest)
    return await _runtime(request).connect(body.address)


@router.post("/robot/disconnect")
async def disconnect_robot(request: Request) -> dict:
    return await _runtime(request).disconnect()


@router.get("/robot/status")
def robot_status(request: Request) -> dict:
    return _runtime(request).status()


@router.patch("/robot/audio")
async def update_audio(request: Request) -> dict:
    body = await _body(request, AudioUpdate)
    return await _runtime(request).update_audio(body.model_dump(exclude_none=True))


@router.get("/robot/capabilities")
def robot_capabilities() -> dict:
    return capabilities()


@router.get("/config/applied")
def get_applied(request: Request) -> dict:
    return _runtime(request).applied_view()


@router.get("/config/applied-content")
def get_applied_content(request: Request) -> dict:
    return _runtime(request).content_view()


@router.post("/config/apply")
async def apply_config(request: Request) -> dict:
    body = await _body(request, ApplyRequest)
    return await _runtime(request).apply(body.screen, body.draft, body.expected_revision)


@router.post("/conversation/start")
async def conversation_start(request: Request) -> dict:
    return await _runtime(request).start_conversation()


@router.post("/conversation/message")
async def conversation_message(request: Request) -> dict:
    body = await _body(request, SpeakRequest)
    return await _runtime(request).send_message(body.text)


@router.post("/conversation/stop")
async def conversation_stop(request: Request) -> dict:
    return await _runtime(request).stop_conversation()


@router.post("/conversation/stop-and-clear")
async def conversation_stop_and_clear(request: Request) -> dict:
    return await _runtime(request).stop_and_clear()


@router.post("/speech/speak")
async def speech_speak(request: Request) -> dict:
    body = await _body(request, SpeakRequest)
    return await _runtime(request).speak(body.text)


@router.post("/speech/stop")
async def speech_stop(request: Request) -> dict:
    return await _runtime(request).stop_speech()


@router.post("/motions/execute")
async def motions_execute(request: Request) -> dict:
    body = await _body(request, MotionRequest)
    return await _runtime(request).execute_motion(body.motion)


@router.post("/motions/stop")
async def motions_stop(request: Request) -> dict:
    return await _runtime(request).stop_motion()


@router.get("/content")
def list_content(request: Request, kind: str | None = None) -> list:
    return _runtime(request).store.list_content(kind)


@router.post("/content")
async def create_content(request: Request) -> dict:
    body = await _body(request, ContentWrite)
    return _runtime(request).store.create_content(body.kind, body.name, body.markdown)


@router.get("/content/{item_id}")
def get_content(item_id: str, request: Request) -> dict:
    item = _runtime(request).store.get_content(item_id)
    if item is None:
        raise ApiError(404, "content not found")
    return item


@router.put("/content/{item_id}")
async def update_content(item_id: str, request: Request) -> dict:
    body = await _body(request, ContentUpdate)
    item = _runtime(request).store.update_content(item_id, body.name, body.markdown)
    if item is None:
        raise ApiError(404, "content not found")
    return item


@router.get("/content/{item_id}/references")
def content_references(item_id: str, request: Request) -> dict:
    from nao_fair.operator_state import references
    return references(_runtime(request), 'content', item_id)


@router.delete("/content/{item_id}")
def delete_content(item_id: str, request: Request, replacement_id: str | None = None) -> dict:
    return _delete_library(request, 'content', item_id, replacement_id)


def _delete_library(request: Request, category: str, item_id: str, replacement_id: str | None) -> dict:
    from nao_fair.operator_state import references
    runtime = _runtime(request)
    with runtime.lock:
        if references(runtime, category, item_id)['applied']:
            raise ApiError(409, 'Der Baustein ist aktuell übernommen. Zuerst einen anderen Baustein übernehmen.')
        try:
            deleted = runtime.store.remove_with_replacement(category, item_id, replacement_id)
        except ValueError as exc:
            raise ApiError(409, str(exc)) from exc
    if not deleted: raise ApiError(404, 'Element wurde nicht gefunden. Bitte die Liste neu laden.')
    return {'deleted': item_id}


@router.get("/presets")
def list_presets(request: Request, kind: str | None = None) -> list:
    return _runtime(request).store.list_presets(kind)


@router.post("/presets")
async def create_preset(request: Request) -> dict:
    body = await _body(request, PresetWrite)
    if body.kind not in PRESET_KINDS:
        raise ApiError(422, "unknown preset kind")
    _runtime(request).validate_preset(body.kind, body.payload)
    return _runtime(request).store.create_preset(
        body.kind, body.name, body.description, body.payload
    )


@router.get("/presets/{preset_id}")
def get_preset(preset_id: str, request: Request) -> dict:
    item = _runtime(request).store.get_preset(preset_id)
    if item is None:
        raise ApiError(404, "preset not found")
    return item


@router.put("/presets/{preset_id}")
async def update_preset(preset_id: str, request: Request) -> dict:
    body = await _body(request, PresetUpdate)
    existing = _runtime(request).store.get_preset(preset_id)
    if existing is None:
        raise ApiError(404, "preset not found")
    _runtime(request).validate_preset(existing["kind"], body.payload)
    item = _runtime(request).store.update_preset(
        preset_id, body.name, body.description, body.payload
    )
    if item is None:
        raise ApiError(404, "preset not found")
    return item


@router.get("/presets/{preset_id}/references")
def preset_references(preset_id: str, request: Request) -> dict:
    from nao_fair.operator_state import references
    return references(_runtime(request), 'preset', preset_id)


@router.delete("/presets/{preset_id}")
def delete_preset(preset_id: str, request: Request, replacement_id: str | None = None) -> dict:
    return _delete_library(request, 'preset', preset_id, replacement_id)


@router.get("/setups")
def list_setups(request: Request) -> list:
    return _runtime(request).store.list_setups()


@router.post("/setups")
async def create_setup(request: Request) -> dict:
    body = await _body(request, SetupWrite)
    _check_slots(body.preset_ids)
    _runtime(request).validate_setup(body.preset_ids)
    return _runtime(request).store.create_setup(body.name, body.description, body.preset_ids)


@router.get("/setups/{setup_id}")
def get_setup(setup_id: str, request: Request) -> dict:
    item = _runtime(request).store.get_setup(setup_id)
    if item is None:
        raise ApiError(404, "setup not found")
    return item


@router.put("/setups/{setup_id}")
async def update_setup(setup_id: str, request: Request) -> dict:
    body = await _body(request, SetupWrite)
    _check_slots(body.preset_ids)
    if _runtime(request).store.get_setup(setup_id) is None:
        raise ApiError(404, "setup not found")
    _runtime(request).validate_setup(body.preset_ids)
    item = _runtime(request).store.update_setup(
        setup_id, body.name, body.description, body.preset_ids
    )
    if item is None:
        raise ApiError(404, "setup not found")
    return item


@router.delete("/setups/{setup_id}")
async def delete_setup(setup_id: str, request: Request) -> JSONResponse:
    body = await _body(request, ConfirmDelete)
    if not body.confirm:
        raise ApiError(400, "confirmation required")
    if not _runtime(request).store.delete_setup(setup_id):
        raise ApiError(404, "setup not found")
    return JSONResponse({"deleted": setup_id})


@router.post("/setups/{setup_id}/activate")
async def activate_setup(setup_id: str, request: Request) -> dict:
    return await _runtime(request).activate(setup_id)


@router.get("/secrets")
def get_secrets(request: Request) -> dict:
    return _runtime(request).secrets.snapshot()


@router.put("/secrets/{slot}")
async def put_secret(slot: str, request: Request) -> dict:
    if slot not in SLOTS:
        raise ApiError(404, "unknown secret slot")
    body = await _body(request, SecretWrite)
    try:
        status = _runtime(request).secrets.put(slot, body.value)
    except ValueError as exc:
        raise ApiError(400, "secret value is empty") from exc
    return {"slot": slot, "status": status}


@router.delete("/secrets/{slot}")
def delete_secret(slot: str, request: Request) -> dict:
    if slot not in SLOTS:
        raise ApiError(404, "unknown secret slot")
    status = _runtime(request).secrets.delete(slot)
    return {"slot": slot, "status": status}


def _check_slots(preset_ids: dict[str, str]) -> None:
    unknown = set(preset_ids) - set(SETUP_SLOTS)
    if unknown:
        raise ApiError(422, "unknown setup slot")


@router.get('/system')
async def system_status(request: Request, probe: bool = False) -> dict:
    from nao_fair.operator_state import system_report
    return await system_report(_runtime(request), probe)


@router.get('/setups/{setup_id}/preview')
def preview_setup(setup_id: str, request: Request) -> dict:
    from nao_fair.operator_state import setup_preview
    return setup_preview(_runtime(request), setup_id)
