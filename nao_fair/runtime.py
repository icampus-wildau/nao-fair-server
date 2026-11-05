"""One long-running fair: connection, applied configuration, and the event picture."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError

from nao_fair.catalog import SENSOR_IDS
from nao_fair.compose import enabled_replacements, prompt_sections
from nao_fair.dialog import active_dialog, dialog_dict, line_ids, position_for, reconcile, step
from nao_fair.events import EventHub
from nao_fair.models import (
    Applied,
    Connection,
    InteractionsDraft,
    LlmDraft,
    RobotDraft,
    GestureBinding,
    ScenarioWrite,
)
from nao_fair.secrets import SecretStore
from nao_fair.store import Store
from nao_fair.test_driver import TestDriver
from nao_fair.touch import DOUBLE_SECS, LONG_SECS, TouchClassifier

logger = logging.getLogger(__name__)


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
        self.known_robots = store.load_known_robots()
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
        self._autostart_synced: bool | None = None
        self._bridge_ready: bool | None = None
        self._autostart_answering: bool | None = None
        self._touch = TouchClassifier()
        self._bumper_down: dict[str, float] = {}
        self._bumper_pair = False
        self._dialog_position: dict | None = None
        self._dialog_line_speaking = False
        self._dialog_epoch = 0
        self._pending_line_ids: list[str] | None = None
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
            "autostart_synced": self._autostart_synced,
            "known_robots": [dict(item) for item in self.known_robots],
            "bridge_ready": self._bridge_ready,
            "autostart_answering": self._autostart_answering,
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
                self._autostart_synced = True if link is None else bool(getattr(link, "autostart_synced", False))
                self.hub.emit(
                    "connection.changed",
                    connection=self.connection.model_dump(mode="json"),
                )
                self.known_robots = self.store.remember_known_robot(address, name)
                self.hub.emit("known_robots.changed", known_robots=[dict(item) for item in self.known_robots])
                self.hub.emit("autostart.changed", synced=self._autostart_synced)
                if link is None:
                    self._bridge_ready = True
                    self._autostart_answering = None
                else:
                    readiness = getattr(link, "bridge_readiness", None)
                    answering = getattr(link, "autostart_answering", None)
                    self._bridge_ready = readiness() if readiness else False
                    self._autostart_answering = bool(answering()) if answering else False
                self.hub.emit("bridge.changed", ready=self._bridge_ready)
                self.hub.emit("autostart.answering", answering=self._autostart_answering)
                result = self.connection.model_dump(mode="json")
                result["known_robots"] = [dict(item) for item in self.known_robots]
        if late:
            if link is not None:
                await link.close()
            with self.lock:
                return self.connection.model_dump(mode="json")
        await self._reconcile_connected_position()
        return result

    def forget_known_robot(self, address: str) -> list[dict[str, str]]:
        address = address.strip()
        if not address or any(char.isspace() for char in address):
            raise ApiError(400, "address is required")
        with self.lock:
            previous = list(self.known_robots)
            self.known_robots = self.store.forget_known_robot(address)
            if self.known_robots != previous:
                self.hub.emit("known_robots.changed", known_robots=[dict(item) for item in self.known_robots])
            return [dict(item) for item in self.known_robots]

    async def _open_link(self, address: str, generation: int) -> tuple[str, Any]:
        if self.driver is not None:
            name = await self.driver.open(address)
            if not self._current(generation):
                raise _Aborted()
            return name, None
        from nao_fair.robot.probe import tcp_accepts

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
            self._bumper_down.clear()
            self._bumper_pair = False
            self._touch = TouchClassifier()
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
            self._autostart_synced = None
            self._bridge_ready = None
            self._autostart_answering = None
            self.hub.emit(
                "connection.changed",
                connection=self.connection.model_dump(mode="json"),
            )
            self.hub.emit("autostart.changed", synced=None)
            self.hub.emit("bridge.changed", ready=None)
            self.hub.emit("autostart.answering", answering=None)
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
                self._bumper_down.clear()
                self._bumper_pair = False
                self._touch = TouchClassifier()
                address = self.connection.address
                self.connection = Connection(
                    state="disconnected", address=address, name=None, failure=message
                )
                self.conversation_state = "idle"
                self._clear_transcript_locked()
                self._cancel_timer_locked()
                self.hub.emit("conversation.state_changed", state="idle")
                self._autostart_synced = None
                self._bridge_ready = None
                self._autostart_answering = None
                self.hub.emit(
                    "connection.changed",
                    connection=self.connection.model_dump(mode="json"),
                )
                self.hub.emit("autostart.changed", synced=None)
                self.hub.emit("bridge.changed", ready=None)
                self.hub.emit("autostart.answering", answering=None)
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

    def adopt_robot_armed(self, armed: bool) -> None:
        with self.lock:
            if self.applied.autostart_enabled == armed:
                return
            updated = self.applied.model_copy(update={
                "autostart_enabled": armed, "revision": self.applied.revision + 1,
            })
            self.store.save_runtime(updated, self.active_setup_id, self.prompt_snapshot, self.applied_content)
            self.applied = updated
            self.hub.emit("config.applied", applied=updated.model_dump(mode="json"),
                          active_setup_id=self.active_setup_id)

    def note_sensor_down(self, sensor_id: str) -> None:
        now = time.monotonic()
        gesture = None
        with self.lock:
            if self.connection.state != "connected":
                return
            self.hub.emit("sensor.pressed", sensor_id=sensor_id, timestamp=_iso())
            if sensor_id in ("left_bumper", "right_bumper"):
                self._bumper_down.setdefault(sensor_id, now)
                if len(self._bumper_down) == 2 and now - min(self._bumper_down.values()) < LONG_SECS:
                    self._bumper_pair = True
                    self._touch.discard("left_bumper")
                    self._touch.discard("right_bumper")
                if self._bumper_pair:
                    return
            gesture = self._touch.down(sensor_id, now)
        if gesture:
            self._schedule(self._run_gesture(sensor_id, gesture))
        else:
            self._arm_touch(LONG_SECS)

    def note_sensor_up(self, sensor_id: str) -> None:
        now = time.monotonic()
        gesture = None
        with self.lock:
            if self.connection.state != "connected":
                return
            if sensor_id in ("left_bumper", "right_bumper"):
                self._bumper_down.pop(sensor_id, None)
                if self._bumper_pair:
                    self._touch.discard(sensor_id)
                    if not self._bumper_down:
                        self._bumper_pair = False
                    return
            gesture = self._touch.up(sensor_id, now, self._double_tap_armed_locked(sensor_id))
        if gesture:
            self._schedule(self._run_gesture(sensor_id, gesture))
        else:
            self._arm_touch(DOUBLE_SECS)

    def _arm_touch(self, delay: float) -> None:
        loop = self._loop
        if loop is None:
            return
        loop.call_later(delay, self._touch_due)

    def _touch_due(self) -> None:
        now = time.monotonic()
        with self.lock:
            if self.connection.state != "connected":
                return
            found = self._touch.tick(now)
        for sensor_id, gesture in found:
            self._schedule(self._run_gesture(sensor_id, gesture))

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
            await self._push_autostart(link, notice=False)
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
        previous_applied = None
        released: list[tuple[str, str]] = []
        with self.lock:
            if expected_revision != self.applied.revision:
                raise ApiError(409, "revision mismatch")
            previous_applied = self.applied
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
            if screen == "interactions":
                released = self._touch.flush(time.monotonic(), self._double_tap_armed_locked)
            link = self._link
            view = self.applied_view_locked()
        for sensor_id, gesture in released:
            self._schedule(self._run_gesture(sensor_id, gesture))
        if screen == "interactions" and previous_applied is not None:
            await self._reconcile_applied_position(previous_applied, candidate)
        if link is not None and screen != "interactions":
            await link.on_applied(candidate, screen)
        if link is not None:
            await self._push_autostart(link, notice=True)
        if screen != "interactions":
            await self.stop_browser_test("Konfiguration übernommen. Den Mikrofontest erneut starten.")
        return view

    async def _push_autostart(self, link: Any, *, notice: bool) -> None:
        if link is None or not hasattr(link, "sync_autostart"):
            return
        try:
            ok = await link.sync_autostart(self.applied)
        except Exception:
            logger.exception("Could not push autostart")
            ok = False
        with self.lock:
            self._autostart_synced = ok
            self.hub.emit("autostart.changed", synced=ok)
            if notice and not ok:
                self.hub.emit("autostart.kept")

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
            data["dialogs"] = [item.model_dump(mode="json") for item in parsed.dialogs]
            data["active_dialog_id"] = parsed.active_dialog_id
            data["autostart_enabled"] = parsed.autostart_enabled
        else:
            raise ValueError("unknown screen")
        return Applied.model_validate(data)

    def validate_scenario(self, body: ScenarioWrite) -> dict:
        from nao_fair.scenarios import combination

        try:
            document = combination(body.model_dump())
            data = self.applied.model_dump()
            data.update(document)
            self._validate_references(Applied.model_validate(data))
        except ValidationError as exc:
            raise ApiError(422, _validation_message(exc)) from exc
        except (ValueError, KeyError) as exc:
            raise ApiError(422, str(exc)) from exc
        return document

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
            if not await self._start_conversation():
                raise ApiError(409, "Die Mikrofonbrücke ist nicht bereit.")

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

    async def speak(
        self,
        text: str,
        *,
        action_key: str | None = None,
        delivery: str = "plain",
        motion: str | None = None,
    ) -> dict:
        async def run() -> None:
            link = self._link_or_none()
            if link is not None:
                await link.speak(text, action_key, delivery=delivery, motion=motion)

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

    def _double_tap_armed_locked(self, sensor_id: str) -> bool:
        binding = self.applied.triggers[sensor_id].double_tap
        return bool(binding.enabled and binding.action is not None)

    async def _run_gesture(self, sensor_id: str, gesture: str) -> None:
        with self.lock:
            binding = self.applied.triggers[sensor_id].gesture(gesture)
        if binding.enabled and binding.action is not None:
            await self._run_binding(binding, action_key=f"trigger:{sensor_id}:{gesture}")

    async def _run_binding(self, binding: GestureBinding, action_key: str | None = None) -> None:
        action = binding.action
        try:
            if action in ("dialog_next", "dialog_repeat", "dialog_previous"):
                await self._run_dialog(action, yield_next=binding.yield_next)
                return
            if action == "dialog_reset":
                await self._reset_dialog(binding.phrase)
                return
            if action == "speak":
                await self.speak(
                    binding.phrase,
                    action_key=action_key,
                    delivery=binding.delivery,
                    motion=binding.motion,
                )
                return
            if action == "start_conversation":
                if not await self._start_conversation():
                    return
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
                    if not await self._start_conversation():
                        return
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

    async def test_dialog(self, action: str, *, yield_next: bool = True, phrase: str = "") -> dict:
        self.require_robot()
        if action == "dialog_reset":
            await self._reset_dialog(phrase)
        else:
            await self._run_dialog(action, yield_next=yield_next)
        return {"action": action}

    async def _run_dialog(self, action: str, *, yield_next: bool = True) -> None:
        with self.lock:
            if action == "dialog_next" and yield_next and self._dialog_line_speaking:
                return
            dialog = active_dialog(
                [dialog_dict(item) for item in self.applied.dialogs],
                self.applied.active_dialog_id,
            )
        position = await self._read_position()
        line = step(dialog, position, action)
        if line is None or dialog is None:
            return
        with self.lock:
            if action == "dialog_next" and yield_next and self._dialog_line_speaking:
                return
            self._dialog_epoch += 1
            epoch = self._dialog_epoch
        text = str(line.get("text") or "")
        delivery = str(line.get("delivery") or "plain")
        motion = line.get("motion")
        link = self._link_or_none()
        spoke = False

        async def started() -> None:
            nonlocal spoke
            with self.lock:
                if epoch != self._dialog_epoch:
                    return
                self._dialog_line_speaking = True
            await self._write_position(position_for(str(dialog["id"]), str(line["id"]), position))
            spoke = True

        try:
            if link is None:
                await started()
            else:
                await link.speak_dialog_line(text, delivery, motion, started)
        except Exception as exc:
            if not spoke:
                with self.lock:
                    self.hub.emit("action.failed", action=action, error=str(exc))
                return
        finally:
            with self.lock:
                if epoch == self._dialog_epoch:
                    self._dialog_line_speaking = False
        if not spoke:
            return
        self.note_nao_line(text)
        with self.lock:
            self.hub.emit("action.completed", action=action)

    async def _reset_dialog(self, phrase: str) -> None:
        with self.lock:
            dialog = active_dialog(
                [dialog_dict(item) for item in self.applied.dialogs],
                self.applied.active_dialog_id,
            )
            if dialog is None:
                return
            self._dialog_epoch += 1
            self._dialog_line_speaking = False
        link = self._link_or_none()
        if link is not None and hasattr(link, "hush_for_reset"):
            await link.hush_for_reset()
        await self._write_position(None)
        text = phrase.strip()
        if text:
            if link is not None and hasattr(link, "speak_announcement"):
                await link.speak_announcement(text)
            self.note_nao_line(text)
        with self.lock:
            self.hub.emit("action.completed", action="dialog_reset")

    async def _read_position(self) -> dict | None:
        link = self._link_or_none()
        if link is not None and hasattr(link, "read_dialog_position"):
            try:
                found = await link.read_dialog_position()
            except Exception:
                logger.exception("Could not read the dialog position")
                return self._dialog_position
            self._dialog_position = found
            return found
        return self._dialog_position

    async def _write_position(self, position: dict | None) -> None:
        self._dialog_position = position
        link = self._link_or_none()
        if link is None or not hasattr(link, "write_dialog_position"):
            return
        try:
            await link.write_dialog_position(position)
        except Exception:
            logger.exception("Could not write the dialog position")

    async def _reconcile_applied_position(self, previous: Applied, candidate: Applied) -> None:
        old = active_dialog([dialog_dict(item) for item in previous.dialogs], previous.active_dialog_id)
        if self.connection.state != "connected":
            self._pending_line_ids = line_ids(old)
            return
        await self._write_reconciled(line_ids(old), candidate)

    async def _reconcile_connected_position(self) -> None:
        with self.lock:
            if self.connection.state != "connected":
                return
            candidate = self.applied
            old_ids = self._pending_line_ids
        await self._write_reconciled(old_ids, candidate)
        self._pending_line_ids = None

    async def _write_reconciled(self, old_ids: list[str] | None, candidate: Applied) -> None:
        position = await self._read_position()
        dialog = active_dialog([dialog_dict(item) for item in candidate.dialogs], candidate.active_dialog_id)
        updated = reconcile(old_ids, line_ids(dialog), position, candidate.active_dialog_id)
        if updated != position:
            await self._write_position(updated)

    def note_services(self, *, bridge_ready: bool | None = None, autostart_answering: bool | None = None, bridge: bool = False, answering: bool = False) -> None:
        """Publish a service change. Pass ``bridge`` or ``answering`` for the field that changed."""
        with self.lock:
            if self.connection.state != "connected":
                return
            bridge_event = None
            answering_event = None
            if bridge and bridge_ready != self._bridge_ready:
                self._bridge_ready = bridge_ready
                bridge_event = bridge_ready
            if answering and autostart_answering != self._autostart_answering:
                self._autostart_answering = autostart_answering
                answering_event = autostart_answering
        if bridge_event is not None:
            self.hub.emit("bridge.changed", ready=bridge_event)
        if answering_event is not None:
            self.hub.emit("autostart.answering", answering=answering_event)

    async def _start_conversation(self) -> bool:
        link = self._link_or_none()
        if link is not None:
            socket_open = getattr(link, "bridge_socket_open", None)
            if socket_open is not None and not socket_open():
                return False
            await link.start_conversation()
            return True
        with self.lock:
            self._set_state_locked("listening")
        return True

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

