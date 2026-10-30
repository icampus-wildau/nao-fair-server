"""One long-running fair: connection, applied configuration, and the event picture."""

from __future__ import annotations

import asyncio
import json
import threading
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError

from nao_fair.catalog import PRESET_KINDS, SENSOR_IDS, SETUP_SLOTS
from nao_fair.compose import enabled_replacements, prompt_sections
from nao_fair.events import EventHub
from nao_fair.models import (
    ROBOT_FIELDS,
    Applied,
    Connection,
    InteractionsDraft,
    LlmDraft,
    RobotDraft,
    TriggerBinding,
)
from nao_fair.secrets import SecretStore
from nao_fair.store import Store
from nao_fair.test_driver import TestDriver


class ApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        self.status = status
        self.message = message


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validation_message(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors():
        loc = ".".join(str(item) for item in err["loc"])
        parts.append(f"{loc}: {err['msg']}" if loc else err["msg"])
    return "; ".join(parts)


class Runtime:
    def __init__(
        self,
        store: Store,
        secrets: SecretStore,
        *,
        driver: TestDriver | None = None,
        bridge_port: int = 8765,
        nao_ports: list[int] | None = None,
        bridge_token: str = "",
    ) -> None:
        applied, active = store.load_runtime()
        if applied is None:
            raise RuntimeError("applied configuration is missing")
        self.store = store
        self.started_at = _iso()
        self.secrets = secrets
        self.driver = driver
        self.bridge_port = bridge_port
        self.nao_ports = list(nao_ports or [9559, 9561])
        self.bridge_token = bridge_token
        self.hub = EventHub()
        self.lock = threading.Lock()
        self.connection = Connection(
            state="disconnected", address=None, name=None, failure=None
        )
        self.conversation_state = "idle"
        self.transcript: list[dict[str, str]] = []
        self.applied = applied
        self.active_setup_id = active
        raw_snapshot = store.meta_get("prompt_snapshot")
        raw_content = store.meta_get("applied_content")
        self.applied_content = (json.loads(raw_content) if raw_content is not None
                                else self._content_snapshot(applied))
        if raw_snapshot is None:
            self.prompt_snapshot = prompt_sections(store, applied, "{robot_name}")
        else:
            self.prompt_snapshot = json.loads(raw_snapshot)
        if raw_snapshot is None or raw_content is None:
            store.save_runtime(applied, active, self.prompt_snapshot, self.applied_content)
        self._generation = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._timer: asyncio.Task | None = None
        self._link: Any = None
        self.browser_test: Any = None
        self._operator_disconnect = False
        if driver is not None:
            driver.attach(self)

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self.hub.loop = loop

    async def shutdown(self) -> None:
        await self.stop_browser_test("Server wird beendet.")
        link = None
        with self.lock:
            self._generation += 1
            link = self._link
            self._link = None
            self._cancel_timer_locked()
        if link is not None:
            await link.close()
        self.store.close()

    def open_stream(self) -> tuple[asyncio.Queue, dict]:
        with self.lock:
            queue = self.hub.subscribe()
            picture = self.hub.make("picture", **self._picture_fields())
        return queue, picture

    def _picture_fields(self) -> dict:
        return {
            "connection": self.connection.model_dump(mode="json"),
            "conversation_state": self.conversation_state,
            "transcript": list(self.transcript),
            "applied": self.applied.model_dump(mode="json"),
            "active_setup_id": self.active_setup_id,
        }

    def status(self) -> dict:
        with self.lock:
            body = self.connection.model_dump(mode="json")
            body["conversation_state"] = self.conversation_state
            return body

    def applied_view(self) -> dict:
        with self.lock:
            return {
                "applied": self.applied.model_dump(mode="json"),
                "active_setup_id": self.active_setup_id,
            }

    async def connect(self, address: str) -> dict:
        address = address.strip()
        if not address or any(char.isspace() for char in address):
            raise ApiError(400, "address is required")
        with self.lock:
            if self.browser_test is not None:
                raise ApiError(409, "Zuerst den Browser-Mikrofontest beenden.")
            same = (
                self.connection.state == "connected"
                and self.connection.address == address
            )
            switching = self.connection.state == "connected" and not same
        if same:
            return self.connection.model_dump(mode="json")
        if switching:
            await self.disconnect()
        with self.lock:
            self._generation += 1
            generation = self._generation
            self.connection = Connection(
                state="disconnected", address=address, name=None, failure=None
            )
            self.conversation_state = "idle"
            self.hub.emit(
                "connection.changed",
                connection=self.connection.model_dump(mode="json"),
            )
        try:
            name, link = await self._open_link(address, generation)
        except _Aborted:
            with self.lock:
                return self.connection.model_dump(mode="json")
        except ConnectionError as exc:
            with self.lock:
                if generation != self._generation:
                    return self.connection.model_dump(mode="json")
                self.connection = Connection(
                    state="disconnected",
                    address=address,
                    name=None,
                    failure=str(exc),
                )
                self.conversation_state = "idle"
                self._clear_transcript_locked()
                self._cancel_timer_locked()
                self.hub.emit(
                    "connection.changed",
                    connection=self.connection.model_dump(mode="json"),
                )
                return self.connection.model_dump(mode="json")
        late = False
        with self.lock:
            if generation != self._generation:
                late = True
            else:
                self._link = link
                self.connection = Connection(
                    state="connected", address=address, name=name, failure=None
                )
                self.conversation_state = "idle"
                self.hub.emit(
                    "connection.changed",
                    connection=self.connection.model_dump(mode="json"),
                )
                result = self.connection.model_dump(mode="json")
        if late:
            if link is not None:
                await link.close()
            with self.lock:
                return self.connection.model_dump(mode="json")
        return result

    async def _open_link(self, address: str, generation: int) -> tuple[str, Any]:
        if self.driver is not None:
            name = await self.driver.open(address)
            if not self._current(generation):
                raise _Aborted()
            return name, None
        from nao_fair.robot.probe import bridge_accepts, tcp_accepts

        reachable = False
        for port in self.nao_ports:
            if await tcp_accepts(address, port):
                reachable = True
                break
            if not self._current(generation):
                raise _Aborted()
        if not reachable:
            raise ConnectionError(f"NAO unter {address} ist nicht erreichbar. IP-Adresse, Netzwerk und Stromversorgung prüfen.")
        if not self._current(generation):
            raise _Aborted()

        if not await bridge_accepts(address, self.bridge_port):
            raise ConnectionError(f"NAO unter {address} ist erreichbar, aber die Mikrofonbrücke auf Port {self.bridge_port} ist nicht erreichbar.")
        if not self._current(generation):
            raise _Aborted()
        from nao_fair.robot.live import LiveLink

        link = await LiveLink.open(
            runtime=self,
            address=address,
            ports=self.nao_ports,
            bridge_port=self.bridge_port,
            bridge_token=self.bridge_token,
            applied=self.applied,
            secrets=self.secrets,
        )
        if not self._current(generation):
            await link.close()
            raise _Aborted()
        return link.robot_name, link

    def _current(self, generation: int) -> bool:
        with self.lock:
            return generation == self._generation

    async def disconnect(self) -> dict:
        with self.lock:
            # Invalidate in-flight opens even while the public picture is disconnected.
            self._generation += 1
            if self.connection.state != "connected" and self._link is None:
                return self.connection.model_dump(mode="json")
            link = self._link
            self._link = None
            self._operator_disconnect = True
            address = self.connection.address
            previous = self.conversation_state
            self.connection = Connection(
                state="disconnected", address=address, name=None, failure=None
            )
            self.conversation_state = "idle"
            self._clear_transcript_locked()
            self._cancel_timer_locked()
            if previous != "idle":
                self.hub.emit("conversation.state_changed", state="idle")
            self.hub.emit(
                "connection.changed",
                connection=self.connection.model_dump(mode="json"),
            )
            result = self.connection.model_dump(mode="json")
        if link is not None:
            await link.close()
        with self.lock:
            self._operator_disconnect = False
        return result

    def driver_end_connection(self) -> None:
        self._run_on_loop(self.disconnect())

    def fail_disconnect(self, message: str) -> None:
        async def go() -> None:
            link = None
            with self.lock:
                if self._operator_disconnect or self.connection.state != "connected":
                    return
                self._generation += 1
                link = self._link
                self._link = None
                address = self.connection.address
                self.connection = Connection(
                    state="disconnected", address=address, name=None, failure=message
                )
                self.conversation_state = "idle"
                self._clear_transcript_locked()
                self._cancel_timer_locked()
                self.hub.emit("conversation.state_changed", state="idle")
                self.hub.emit(
                    "connection.changed",
                    connection=self.connection.model_dump(mode="json"),
                )
            if link is not None:
                await link.close()

        loop = self._loop
        if loop is None:
            return
        loop.create_task(go())

    def driver_set_state(self, state: str) -> None:
        if state not in ("idle", "listening", "thinking", "speaking"):
            raise ValueError("unknown conversation state")
        with self.lock:
            self._require_connected_locked()
            self._set_state_locked(state)

    def driver_append_transcript(self, speaker: str, text: str) -> None:
        if speaker not in ("visitor", "nao"):
            raise ValueError("unknown speaker")
        with self.lock:
            self._require_connected_locked()
            self._append_transcript_locked(speaker, text)

    def driver_press(self, sensor_id: str) -> None:
        if sensor_id not in SENSOR_IDS:
            raise ValueError("unknown sensor")
        with self.lock:
            self._require_connected_locked()
            self.hub.emit(
                "sensor.pressed", sensor_id=sensor_id, timestamp=_iso()
            )

    def note_visitor_line(self, text: str) -> None:
        with self.lock:
            if self.connection.state != "connected":
                return
            self._append_transcript_locked("visitor", text)

    def note_nao_line(self, text: str) -> None:
        with self.lock:
            if self.connection.state != "connected":
                return
            self._append_transcript_locked("nao", text)

    def note_state(self, state: str) -> None:
        with self.lock:
            if self.connection.state != "connected":
                return
            if self.conversation_state == state:
                self._arm_timer_locked()
                return
            self._set_state_locked(state)

    def note_sensor(self, sensor_id: str) -> None:
        binding: TriggerBinding | None = None
        with self.lock:
            if self.connection.state != "connected":
                return
            self.hub.emit("sensor.pressed", sensor_id=sensor_id, timestamp=_iso())
            binding = self.applied.triggers[sensor_id]
        if binding is not None and binding.enabled and binding.action is not None:
            self._schedule(self._run_binding(binding))

    def _require_connected_locked(self) -> None:
        if self.connection.state != "connected":
            raise RuntimeError("not connected")

    def _set_state_locked(self, state: str) -> None:
        self.conversation_state = state
        self._arm_timer_locked()
        self.hub.emit("conversation.state_changed", state=state)

    def _append_transcript_locked(self, speaker: str, text: str) -> None:
        from uuid import uuid4

        line = {
            "id": str(uuid4()),
            "speaker": speaker,
            "text": text,
            "timestamp": _iso(),
        }
        self.transcript.append(line)
        self._arm_timer_locked()
        self.hub.emit("transcript.message", **line)

    def _clear_transcript_locked(self) -> None:
        if not self.transcript:
            return
        self.transcript.clear()
        self.hub.emit("transcript.replaced", lines=[])

    def _arm_timer_locked(self) -> None:
        loop = self._loop
        timeout = float(self.applied.context_reset_timeout_secs)
        should = self.connection.state == "connected" and self.conversation_state == "idle"
        if loop is None or not loop.is_running():
            return

        def restart() -> None:
            if self._timer is not None and not self._timer.done():
                self._timer.cancel()
            self._timer = None
            if should:
                self._timer = loop.create_task(self._context_timeout(timeout))

        loop.call_soon_threadsafe(restart)

    def _cancel_timer_locked(self) -> None:
        loop = self._loop
        timer = self._timer
        self._timer = None
        if timer is None or loop is None or not loop.is_running():
            return
        loop.call_soon_threadsafe(timer.cancel)

    async def _context_timeout(self, timeout: float) -> None:
        try:
            await asyncio.sleep(timeout)
        except asyncio.CancelledError:
            return
        link = None
        with self.lock:
            if self.connection.state != "connected" or self.conversation_state != "idle":
                return
            if self._timer is not asyncio.current_task():
                return
            self._clear_transcript_locked()
            link = self._link
        if link is not None:
            await link.discard_context()

    async def update_audio(self, patch: dict) -> dict:
        # Merge only the direct controls; keep conversation, context and other settings.
        with self.lock:
            data = self.applied.model_dump()
            data.update(patch)
            data["revision"] += 1
            candidate = Applied.model_validate(data)
            self.applied = candidate
            self.store.save_runtime(candidate, self.active_setup_id, self.prompt_snapshot, self.applied_content)
            self.hub.emit("config.applied", applied=candidate.model_dump(mode="json"), active_setup_id=self.active_setup_id)
            link = self._link
            result = self.applied_view_locked()
        if link is not None:
            await link.on_audio_changed(candidate)
        return result

    async def apply(self, screen: str, draft: dict, expected_revision: int) -> dict:
        try:
            candidate = self._merge_draft(screen, draft)
            self._validate_references(candidate)
            snapshot = (prompt_sections(self.store, candidate, "{robot_name}")
                        if screen == "llm" else None)
            content = self._content_snapshot(candidate) if screen == "llm" else None
        except ValidationError as exc:
            raise ApiError(422, _validation_message(exc)) from exc
        except ValueError as exc:
            raise ApiError(422, str(exc)) from exc
        link = None
        with self.lock:
            if expected_revision != self.applied.revision:
                raise ApiError(409, "revision mismatch")
            candidate.revision = self.applied.revision + 1
            previous = self.conversation_state
            self.applied = candidate
            if screen != "interactions":
                self.conversation_state = "idle"
            if snapshot is not None:
                self.prompt_snapshot = snapshot
                self.applied_content = content
            self.store.save_runtime(candidate, self.active_setup_id, self.prompt_snapshot, self.applied_content)
            self._arm_timer_locked()
            if screen != "interactions" and previous != "idle":
                self.hub.emit("conversation.state_changed", state="idle")
            self.hub.emit(
                "config.applied",
                applied=candidate.model_dump(mode="json"),
                active_setup_id=self.active_setup_id,
            )
            link = self._link
            view = self.applied_view_locked()
        if link is not None and screen != "interactions":
            await link.on_applied(candidate, screen)
        if screen != "interactions":
            await self.stop_browser_test("Konfiguration übernommen. Den Mikrofontest erneut starten.")
        return view

    async def activate(self, setup_id: str) -> dict:
        setup = self.store.get_setup(setup_id)
        if setup is None:
            raise ApiError(404, "setup not found")
        try:
            candidate = self._merge_setup(setup)
            self._validate_references(candidate)
            snapshot = prompt_sections(self.store, candidate, "{robot_name}")
            content = self._content_snapshot(candidate)
        except ValidationError as exc:
            raise ApiError(422, _validation_message(exc)) from exc
        except ValueError as exc:
            raise ApiError(422, str(exc)) from exc
        link = None
        with self.lock:
            candidate.revision = self.applied.revision + 1
            previous = self.conversation_state
            self.applied = candidate
            self.active_setup_id = setup_id
            self.conversation_state = "idle"
            self.prompt_snapshot = snapshot
            self.applied_content = content
            self.store.save_runtime(candidate, setup_id, snapshot, content)
            self._arm_timer_locked()
            if previous != "idle":
                self.hub.emit("conversation.state_changed", state="idle")
            self.hub.emit(
                "config.applied",
                applied=candidate.model_dump(mode="json"),
                active_setup_id=setup_id,
            )
            link = self._link
            view = self.applied_view_locked()
        if link is not None:
            await link.on_applied(candidate, "setup")
        await self.stop_browser_test("Setup aktiviert. Den Mikrofontest erneut starten.")
        return view

    async def stop_browser_test(self, reason: str) -> None:
        with self.lock:
            test = self.browser_test
        if test is not None:
            await test.close(reason)

    def applied_view_locked(self) -> dict:
        return {
            "applied": self.applied.model_dump(mode="json"),
            "active_setup_id": self.active_setup_id,
        }

    def _merge_draft(self, screen: str, draft: dict) -> Applied:
        data = self.applied.model_dump()
        if screen == "robot":
            parsed = RobotDraft.model_validate(draft)
            data.update(parsed.model_dump())
        elif screen == "llm":
            parsed = LlmDraft.model_validate(draft)
            data.update(parsed.model_dump())
        elif screen == "interactions":
            parsed = InteractionsDraft.model_validate(draft)
            data["triggers"] = {
                key: value.model_dump() for key, value in parsed.triggers.items()
            }
        else:
            raise ValueError("unknown screen")
        return Applied.model_validate(data)

    def _merge_setup(self, setup: dict) -> Applied:
        data = self.applied.model_dump()
        preset_ids = setup["preset_ids"]
        if not isinstance(preset_ids, dict):
            raise ValueError("preset_ids must be an object")
        unknown = set(preset_ids) - set(SETUP_SLOTS)
        if unknown:
            raise ValueError("unknown setup slot")
        for slot, preset_id in preset_ids.items():
            preset = self.store.get_preset(str(preset_id))
            if preset is None:
                raise ValueError(f"preset not found: {preset_id}")
            if preset["kind"] != slot:
                raise ValueError(f"preset kind does not match slot {slot}")
            _apply_preset(data, slot, preset["payload"])
        return Applied.model_validate(data)

    def validate_preset(self, kind: str, payload: dict) -> None:
        try:
            data = self.applied.model_dump()
            _apply_preset(data, kind, payload)
            self._validate_references(Applied.model_validate(data))
        except ValidationError as exc:
            raise ApiError(422, _validation_message(exc)) from exc
        except ValueError as exc:
            raise ApiError(422, str(exc)) from exc

    def validate_setup(self, preset_ids: dict[str, str]) -> None:
        try:
            self._validate_references(self._merge_setup({"preset_ids": preset_ids}))
        except ValidationError as exc:
            raise ApiError(422, _validation_message(exc)) from exc
        except ValueError as exc:
            raise ApiError(422, str(exc)) from exc

    def _validate_references(self, applied: Applied) -> None:
        self._require_content(applied.system_prompt_id, "system_prompt")
        self._require_content(applied.local_context_id, "local_context")
        self._require_content(applied.personality_id, "personality")
        for knowledge_id in applied.knowledge_ids:
            self._require_content(knowledge_id, "knowledge")

    def _content_snapshot(self, applied: Applied) -> dict[str, str]:
        ids = [applied.system_prompt_id, applied.local_context_id, applied.personality_id, *applied.knowledge_ids]
        return {item_id: self.store.get_content(item_id)["markdown"] for item_id in ids}

    def content_view(self) -> dict:
        with self.lock:
            return {"items": dict(self.applied_content)}

    def _require_content(self, item_id: str, kind: str) -> None:
        item = self.store.get_content(item_id)
        if item is None or item["kind"] != kind:
            raise ValueError(f"missing {kind}")

    def require_robot(self) -> None:
        with self.lock:
            if self.connection.state != "connected":
                raise ApiError(409, "no robot connected")

    async def command(self, action: str, runner) -> dict:
        self.require_robot()
        try:
            await runner()
        except ApiError:
            raise
        except Exception as exc:
            with self.lock:
                self.hub.emit("action.failed", action=action, error=str(exc))
            raise ApiError(502, str(exc)) from exc
        with self.lock:
            self.hub.emit("action.completed", action=action)
        return {"action": action}

    async def start_conversation(self) -> dict:
        async def run() -> None:
            await self._start_conversation()

        return await self.command("start_conversation", run)

    async def send_message(self, text: str) -> dict:
        text = text.strip()
        if not text:
            raise ApiError(400, "Die Nachricht ist leer.")

        async def run() -> None:
            link = self._link_or_none()
            if link is None:
                raise ApiError(409, "Die Gesprächspipeline ist nicht verfügbar.")
            with self.lock:
                if self.conversation_state in ("thinking", "speaking"):
                    raise ApiError(409, "Bitte die laufende Antwort abwarten.")
            await link.send_message(text)

        return await self.command("send_message", run)

    async def stop_conversation(self) -> dict:
        async def run() -> None:
            await self._stop_conversation(clear=False)

        return await self.command("stop_conversation", run)

    async def stop_and_clear(self) -> dict:
        async def run() -> None:
            await self._stop_conversation(clear=True)

        return await self.command("stop_and_clear", run)

    async def speak(self, text: str) -> dict:
        async def run() -> None:
            link = self._link_or_none()
            if link is not None:
                await link.speak(text)

        return await self.command("speak", run)

    async def stop_speech(self) -> dict:
        async def run() -> None:
            link = self._link_or_none()
            if link is not None:
                await link.stop_speech()

        return await self.command("stop_speech", run)

    async def execute_motion(self, motion: str) -> dict:
        async def run() -> None:
            link = self._link_or_none()
            if link is not None:
                await link.execute_motion(motion)

        return await self.command(motion, run)

    async def stop_motion(self) -> dict:
        async def run() -> None:
            link = self._link_or_none()
            if link is not None:
                await link.stop_motion()

        return await self.command("stop", run)

    def _link_or_none(self):
        with self.lock:
            return self._link

    async def _run_binding(self, binding: TriggerBinding) -> None:
        action = binding.action
        try:
            if action == "speak":
                await self.speak(binding.phrase)
                return
            if action == "start_conversation":
                await self._start_conversation()
            elif action == "stop_conversation":
                await self._stop_conversation(clear=False)
            elif action == "stop_speech":
                link = self._link_or_none()
                if link is not None:
                    await link.stop_speech()
            elif action == "toggle_conversation":
                with self.lock:
                    idle = self.conversation_state == "idle"
                if idle:
                    await self._start_conversation()
                else:
                    await self._stop_conversation(clear=False)
            elif action == "stop_and_clear":
                await self._stop_conversation(clear=True)
            elif action == "motion" and binding.motion:
                link = self._link_or_none()
                if link is not None:
                    await link.execute_motion(binding.motion)
                action = binding.motion
            elif action == "stop_motion":
                link = self._link_or_none()
                if link is not None:
                    await link.stop_motion()
                action = "stop"
            else:
                return
        except ApiError:
            return
        except Exception as exc:
            with self.lock:
                self.hub.emit("action.failed", action=action or "action", error=str(exc))
            return
        if action not in ("speak",):
            with self.lock:
                self.hub.emit("action.completed", action=action or "action")

    async def _start_conversation(self) -> None:
        link = self._link_or_none()
        if link is not None:
            await link.start_conversation()
            return
        with self.lock:
            self._set_state_locked("listening")

    async def _stop_conversation(self, *, clear: bool) -> None:
        link = self._link_or_none()
        if link is not None:
            await link.stop_conversation(clear=clear)
        with self.lock:
            if clear:
                self._clear_transcript_locked()
            if link is None and self.conversation_state != "idle":
                self._set_state_locked("idle")
            else:
                self._arm_timer_locked()
        if clear and link is not None:
            await link.discard_context()

    def _schedule(self, coro) -> None:
        loop = self._loop
        if loop is None:
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            loop.create_task(coro)
            return
        asyncio.run_coroutine_threadsafe(coro, loop)

    def _run_on_loop(self, coro) -> None:
        loop = self._loop
        if loop is None:
            raise RuntimeError("server loop is not running")
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            loop.create_task(coro)
            return
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        future.result(timeout=10)

    def sections_for(self, applied: Applied, robot_name: str) -> list[str]:
        del applied
        name = robot_name.strip() or "NAO"
        with self.lock:
            return [section.replace("{robot_name}", name) for section in self.prompt_snapshot]

    def replacement_pairs(self, applied: Applied) -> list[tuple[str, str]]:
        return enabled_replacements(applied)


class _Aborted(Exception):
    pass


_LLM_PRESET_FIELDS = (
    "llm_model",
    "temperature",
    "max_tokens",
    "gladia_region",
    "gladia_endpointing",
    "gladia_max_utterance_secs",
    "gladia_audio_enhancer",
    "gladia_language",
)


def _apply_preset(data: dict, slot: str, payload: dict) -> None:
    if not isinstance(payload, dict):
        raise ValueError(f"{slot} payload must be an object")
    if slot == "system_prompt":
        data["system_prompt_id"] = _required_str(payload, "content_id")
    elif slot == "knowledge_bundle":
        ids = payload.get("content_ids")
        if not isinstance(ids, list) or not all(isinstance(item, str) for item in ids):
            raise ValueError("knowledge bundle content_ids must be a list of strings")
        data["knowledge_ids"] = list(ids)
    elif slot == "local_context":
        data["local_context_id"] = _required_str(payload, "content_id")
    elif slot == "personality":
        data["personality_id"] = _required_str(payload, "content_id")
    elif slot == "llm":
        for field in _LLM_PRESET_FIELDS:
            if field not in payload:
                raise ValueError(f"llm preset missing {field}")
            data[field] = payload[field]
    elif slot == "voice":
        for field in ROBOT_FIELDS:
            if field not in payload:
                raise ValueError(f"voice preset missing {field}")
            data[field] = payload[field]
    elif slot == "stt_vocabulary":
        if "terms" not in payload:
            raise ValueError("stt vocabulary preset missing terms")
        data["stt_vocabulary"] = payload["terms"]
    elif slot == "tts_replacements":
        if "replacements" not in payload:
            raise ValueError("tts replacements preset missing replacements")
        data["tts_replacements"] = payload["replacements"]
    elif slot == "interaction_mappings":
        if "triggers" not in payload:
            raise ValueError("interaction mappings preset missing triggers")
        data["triggers"] = payload["triggers"]
    elif slot not in PRESET_KINDS:
        raise ValueError(f"unknown preset slot {slot}")


def _required_str(payload: dict, key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"missing {key}")
    return value
