"""Robot program versions and the SSH install started from the system tab."""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field

from nao_fair.runtime import ApiError

logger = logging.getLogger(__name__)

CATALOG = (
    {
        "id": "bridge",
        "label": "Mikrofonbrücke",
        "repo": "icampus-wildau/nao-websocket-bridge",
        "marker": "audio_bridge.py",
    },
    {
        "id": "autostart",
        "label": "Autostart-Prozess",
        "repo": "icampus-wildau/nao-fair-autostart",
        "marker": "autostart_service.py",
    },
)
_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
_RELEASE_TTL = 60.0


class InstallRequest(BaseModel):
    password: str = Field(min_length=1)
    confirm: bool = False


class HostKeyRequest(BaseModel):
    accept: bool


class PasswordRequest(BaseModel):
    password: str = Field(min_length=1)


def release_tuple(tag: str | None) -> tuple[int, int, int] | None:
    if not tag:
        return None
    match = _TAG.fullmatch(tag.strip())
    if match is None:
        return None
    return tuple(int(part) for part in match.groups())


def install_action(reported: str | None, latest: str | None, running: bool | None) -> str | None:
    """Which install button a program row offers."""
    latest_version = release_tuple(latest)
    if latest_version is None:
        return None
    reported_version = release_tuple(reported)
    if reported_version is None or running is not True:
        return "install"
    if reported_version == latest_version:
        return "reinstall"
    if reported_version < latest_version:
        return "update"
    return None


def autostart_overview(*, connected: bool, reachable: bool | None, answering: bool | None) -> str:
    if connected:
        return "running" if answering else "stopped"
    if reachable is False:
        return "blocked"
    return "unknown"


def choose_key_line(scan: str) -> str | None:
    lines = [line.strip() for line in scan.splitlines() if line.strip() and not line.startswith("#") and not line.startswith("|")]
    for prefer in ("ssh-ed25519", "ecdsa-sha2-nistp256", "ssh-rsa"):
        for line in lines:
            if f" {prefer} " in f" {line} ":
                return line
    return lines[0] if lines else None


def key_material(line: str) -> str:
    parts = line.split()
    if len(parts) < 3:
        return line.strip()
    return " ".join(parts[1:])


def fingerprint_of(line: str) -> str:
    completed = subprocess.run(
        ["ssh-keygen", "-lf", "-"],
        input=line + "\n",
        text=True,
        capture_output=True,
        check=False,
    )
    parts = completed.stdout.split()
    if len(parts) >= 2:
        return parts[1]
    raise RuntimeError("Der SSH-Hostschlüssel konnte nicht gelesen werden.")


def read_known(path: Path, host: str) -> str | None:
    if not path.is_file():
        return None
    prefix = (host + " ", host + "\t")
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(prefix):
            return line
    return None


def write_known(path: Path, host: str, line: str) -> None:
    prefix = (host + " ", host + "\t")
    kept = []
    if path.is_file():
        kept = [item for item in path.read_text(encoding="utf-8").splitlines() if item and not item.startswith(prefix)]
    kept.append(line)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(kept) + "\n", encoding="utf-8")


def scan_host_key(host: str) -> str:
    completed = subprocess.run(
        ["ssh-keyscan", "-T", "5", host],
        text=True,
        capture_output=True,
        check=False,
    )
    line = choose_key_line(completed.stdout)
    if line is None:
        raise RuntimeError("Der SSH-Hostschlüssel konnte nicht gelesen werden.")
    return line


def prepare_tree(repo: str, tarball_url: str, marker: str, work: Path) -> Path:
    archive_path = work / "release.tar.gz"
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        response = client.get(tarball_url, headers={"User-Agent": "nao-fair"})
        response.raise_for_status()
        archive_path.write_bytes(response.content)
    extracted = work / "src"
    extracted.mkdir()
    with tarfile.open(archive_path) as archive:
        archive.extractall(extracted, filter="data")
    for path in extracted.rglob(marker):
        if path.is_file():
            return path.parent
    raise RuntimeError(f"Die Veröffentlichung von {repo} enthält {marker} nicht.")


def run_install(root: Path, host: str, password: str, version: str, known_hosts: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["NAO_IP"] = host
    env["NAO_PASSWORD"] = password
    env["NAO_PROGRAM_VERSION"] = version
    env["NAO_KNOWN_HOSTS"] = str(known_hosts)
    script = root / "packaging" / "install.sh"
    return subprocess.run(
        ["sh", str(script), "--ip", host],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _rejected_password(output: str) -> bool:
    text = output.lower()
    return "permission denied" in text or "authentication failed" in text


def _public_error(output: str, password: str) -> str:
    redacted = output.replace(password, "••••") if password else output
    lines = [line.strip() for line in redacted.splitlines() if line.strip()]
    if not lines:
        return "Die Installation ist fehlgeschlagen."
    return "\n".join(lines[-8:])


class _Job:
    def __init__(self, program_id: str, password: str) -> None:
        self.program_id = program_id
        self.password = password
        self.state = "downloading"
        self.error = ""
        self.fingerprint = ""
        self.previous_fingerprint = ""
        self.changed = False
        self.decision = threading.Event()
        self.accepted = False
        self.password_ready = threading.Event()
        self.attempts = 0

    def view(self) -> dict[str, Any]:
        return {
            "program": self.program_id,
            "state": self.state,
            "error": self.error,
            "fingerprint": self.fingerprint,
            "previous_fingerprint": self.previous_fingerprint or None,
            "changed": self.changed,
        }


class ProgramService:
    def __init__(self, runtime: Any, known_hosts: Path) -> None:
        self.runtime = runtime
        self.known_hosts = known_hosts
        self._lock = threading.Lock()
        self._job: _Job | None = None
        self._releases: dict[str, dict[str, str]] = {}
        self._releases_error: str | None = None
        self._releases_at = 0.0

    async def snapshot(self) -> dict[str, Any]:
        await self._refresh_releases()
        address = self.runtime.connection.address
        bridge_running = await self._bridge_running(address)
        bridge_version = _bridge_version(self.runtime) if bridge_running else None
        autostart_running, autostart_version = await _autostart_observation(self.runtime)
        observed = {
            "bridge": (bridge_running, bridge_version),
            "autostart": (autostart_running, autostart_version),
        }
        programs = []
        for program in CATALOG:
            running, reported = observed[program["id"]]
            latest = self._releases.get(program["id"], {}).get("tag")
            programs.append({
                "id": program["id"],
                "label": program["label"],
                "running": running,
                "reported_version": reported,
                "latest_version": latest,
                "action": install_action(reported, latest, running) if address else None,
            })
        with self._lock:
            job = None if self._job is None else self._job.view()
        return {
            "address": address,
            "releases_error": self._releases_error,
            "install": job,
            "programs": programs,
        }

    async def _bridge_running(self, address: str | None) -> bool | None:
        if not address:
            return None
        from nao_fair.robot.probe import bridge_accepts

        return await bridge_accepts(address, self.runtime.bridge_port)

    async def _refresh_releases(self) -> None:
        if self._releases and time.monotonic() - self._releases_at < _RELEASE_TTL:
            return
        try:
            found = await _fetch_releases()
        except Exception:
            logger.exception("Could not load robot program releases")
            self._releases = {}
            self._releases_error = "Die Veröffentlichungen konnten nicht geladen werden."
            self._releases_at = 0.0
            return
        self._releases = found
        self._releases_error = None if len(found) == len(CATALOG) else "Einige Veröffentlichungen konnten nicht geladen werden."
        self._releases_at = time.monotonic()

    async def begin(self, program_id: str, password: str) -> dict[str, Any]:
        _program(program_id)
        address = self.runtime.connection.address
        if not address:
            raise ApiError(400, "Keine Roboteradresse gespeichert.")
        picture = await self.snapshot()
        selected = next(item for item in picture["programs"] if item["id"] == program_id)
        if selected["action"] is None:
            raise ApiError(400, "Für dieses Programm gibt es keine Installation.")
        with self._lock:
            if self._job is not None and self._job.state not in ("done", "failed"):
                raise ApiError(409, "Es läuft bereits eine Installation.")
            job = _Job(program_id, password)
            self._job = job
        program = _program(program_id)
        thread = threading.Thread(target=self._run, args=(job, program, address), name=f"install-{program_id}", daemon=True)
        thread.start()
        return job.view()

    def decide_hostkey(self, accept: bool) -> dict[str, Any]:
        with self._lock:
            job = self._job
            if job is None or job.state != "hostkey":
                raise ApiError(409, "Es wird kein Hostschlüssel bestätigt.")
            job.accepted = accept
            job.decision.set()
            return job.view()

    def retry_password(self, password: str) -> dict[str, Any]:
        with self._lock:
            job = self._job
            if job is None or job.state != "password":
                raise ApiError(409, "Es wird kein Passwort erwartet.")
            job.password = password
            job.error = ""
            job.state = "installing"
            job.password_ready.set()
            return job.view()

    def _run(self, job: _Job, program: dict[str, str], address: str) -> None:
        work = Path(tempfile.mkdtemp(prefix="nao-fair-install-"))
        try:
            info = self._releases[program["id"]]
            root = prepare_tree(program["repo"], info["tarball"], program["marker"], work)
            self._accept_hostkey(job, address)
            while True:
                job.state = "installing"
                completed = run_install(root, address, job.password, info["tag"], self.known_hosts)
                output = f"{completed.stdout}\n{completed.stderr}"
                if completed.returncode == 0:
                    job.state = "done"
                    job.error = ""
                    return
                if _rejected_password(output) and job.attempts < 2:
                    job.attempts += 1
                    with self._lock:
                        job.password_ready.clear()
                        job.password = ""
                        job.error = "Das Passwort wurde abgelehnt."
                        job.state = "password"
                    if not job.password_ready.wait(timeout=300):
                        job.state = "failed"
                        job.error = "Das Passwort wurde nicht erneut eingegeben."
                        return
                    continue
                job.state = "failed"
                job.error = _public_error(output, job.password)
                return
        except Exception as exc:
            logger.exception("Robot program install failed")
            job.state = "failed"
            message = str(exc).strip() or "Die Installation ist fehlgeschlagen."
            job.error = message.replace(job.password, "••••") if job.password else message
        finally:
            job.password = ""
            shutil.rmtree(work, ignore_errors=True)

    def _accept_hostkey(self, job: _Job, address: str) -> None:
        scanned = scan_host_key(address)
        stored = read_known(self.known_hosts, address)
        if stored is not None and key_material(stored) == key_material(scanned):
            return
        job.changed = stored is not None
        job.fingerprint = fingerprint_of(scanned)
        job.previous_fingerprint = fingerprint_of(stored) if stored else ""
        with self._lock:
            job.decision.clear()
            job.state = "hostkey"
        if not job.decision.wait(timeout=300):
            raise RuntimeError("Der Hostschlüssel wurde nicht bestätigt.")
        if not job.accepted:
            raise RuntimeError("Der Hostschlüssel wurde abgelehnt.")
        write_known(self.known_hosts, address, scanned)


def _program(program_id: str) -> dict[str, str]:
    for program in CATALOG:
        if program["id"] == program_id:
            return program
    raise ApiError(404, "Unbekanntes Roboterprogramm.")


async def _fetch_releases() -> dict[str, dict[str, str]]:
    found: dict[str, dict[str, str]] = {}
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        for program in CATALOG:
            try:
                response = await client.get(
                    f"https://api.github.com/repos/{program['repo']}/releases/latest",
                    headers={"Accept": "application/vnd.github+json", "User-Agent": "nao-fair"},
                )
                response.raise_for_status()
                data = response.json()
                if data.get("draft") or data.get("prerelease") or not data.get("tag_name") or not data.get("tarball_url"):
                    raise RuntimeError("Die neueste Veröffentlichung kann nicht installiert werden.")
                found[program["id"]] = {"tag": data["tag_name"], "tarball": data["tarball_url"]}
            except Exception:
                logger.exception("Could not load release for %s", program["id"])
    return found


def _bridge_version(runtime: Any) -> str | None:
    link = getattr(runtime, "_link", None)
    session = getattr(link, "session", None)
    transport = getattr(session, "_transport", None)
    client = getattr(transport, "_session", None)
    if not bool(getattr(client, "is_connected", False)):
        return None
    version = getattr(session, "bridge_version", None)
    if isinstance(version, str) and version.strip():
        return version.strip()
    return None


async def _autostart_observation(runtime: Any) -> tuple[bool | None, str | None]:
    if runtime.status()["state"] != "connected":
        return None, None
    link = getattr(runtime, "_link", None)
    version = None
    nao = getattr(link, "nao", None)
    if nao is not None:
        from nao_fair.robot.autostart import read_reported_version

        version = await read_reported_version(nao)
    last_ack = getattr(link, "last_autostart_ack", None)
    if last_ack is not None and time.monotonic() - last_ack < 6.0:
        return True, version
    return False, None


async def autostart_check(runtime: Any, *, connected: bool, reachable: bool | None) -> dict[str, str]:
    answering, _version = await _autostart_observation(runtime)
    state = autostart_overview(connected=connected, reachable=reachable, answering=answering)
    detail = {
        "running": "Der Autostart-Prozess antwortet.",
        "stopped": "Der Autostart-Prozess antwortet nicht.",
        "blocked": "NAO nicht erreichbar.",
        "unknown": "Nicht geprüft.",
    }[state]
    return {"state": state, "detail": detail, "action": "programs"}
