import asyncio
import threading
from types import SimpleNamespace

from nao_fair.robot.live import LiveLink


def test_the_same_speak_action_does_not_queue_and_a_different_one_does():
    asyncio.run(_same_action_does_not_stack())


def test_stopping_speech_drops_a_queued_different_action():
    asyncio.run(_stop_drops_the_queued_action())


async def _same_action_does_not_stack() -> None:
    link, spoken, release = _link()

    async def speak_queued(text: str, epoch: int, delivery: str = "plain", motion: str | None = None) -> None:
        del epoch, delivery, motion
        spoken.append(text)
        if text == "Hallo":
            await release.wait()

    link._speak_queued = speak_queued
    first = asyncio.create_task(link.speak("Hallo", "trigger:front_head:tap"))
    await _until(lambda: spoken == ["Hallo"])
    repeated = asyncio.create_task(link.speak("Hallo wieder", "trigger:front_head:tap"))
    await asyncio.wait_for(repeated, timeout=0.2)
    other = asyncio.create_task(link.speak("Tschüss", "trigger:rear_head:tap"))
    await asyncio.sleep(0.05)
    assert spoken == ["Hallo"]
    release.set()
    await first
    await other
    assert spoken == ["Hallo", "Tschüss"]


async def _stop_drops_the_queued_action() -> None:
    link, spoken, release = _link()

    async def stop_async() -> None:
        release.set()

    link.nao = SimpleNamespace(tts=SimpleNamespace(stop_async=stop_async))

    async def speak_queued(text: str, epoch: int, delivery: str = "plain", motion: str | None = None) -> None:
        del epoch, delivery, motion
        spoken.append(text)
        await release.wait()

    link._speak_queued = speak_queued
    first = asyncio.create_task(link.speak("Hallo", "trigger:front_head:tap"))
    await _until(lambda: spoken == ["Hallo"])
    queued = asyncio.create_task(link.speak("Tschüss", "trigger:rear_head:tap"))
    await asyncio.sleep(0.05)
    await link.stop_speech()
    await first
    await queued
    assert spoken == ["Hallo"]


def _link() -> tuple[LiveLink, list[str], asyncio.Event]:
    runtime = SimpleNamespace(
        lock=threading.Lock(),
        conversation_state="listening",
        note_state=lambda state: None,
        note_nao_line=lambda text: None,
    )
    return LiveLink(runtime, SimpleNamespace(), "NAO"), [], asyncio.Event()


async def _until(ready) -> None:
    for _ in range(50):
        if ready():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("speech did not start")
