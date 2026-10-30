"""SQLite store for content, presets, setups, and the applied configuration."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from nao_fair.models import Applied


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None

    def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS content_items (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                name TEXT NOT NULL,
                markdown TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS presets (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                payload TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS setups (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                preset_ids TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        conn.commit()
        self._conn = conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("store is closed")
        return self._conn

    def meta_get(self, key: str) -> str | None:
        with self._lock:
            row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return None if row is None else str(row["value"])

    def meta_set(self, key: str, value: str) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            self.conn.commit()

    def seed_complete(self) -> bool:
        return self.meta_get("seed_complete") == "1"

    def save_runtime(self, applied: Applied, active_setup_id: str | None, prompt_snapshot: list[str] | None = None, applied_content: dict[str, str] | None = None) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO meta(key, value) VALUES('applied', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (applied.model_dump_json(),),
            )
            self.conn.execute(
                "INSERT INTO meta(key, value) VALUES('active_setup_id', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (active_setup_id or "",),
            )
            if prompt_snapshot is not None:
                self.conn.execute(
                    "INSERT INTO meta(key, value) VALUES('prompt_snapshot', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (json.dumps(prompt_snapshot),),
                )
            if applied_content is not None:
                self.conn.execute(
                    "INSERT INTO meta(key, value) VALUES('applied_content', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (json.dumps(applied_content),),
                )
            self.conn.commit()

    def load_runtime(self) -> tuple[Applied | None, str | None]:
        raw = self.meta_get("applied")
        active = self.meta_get("active_setup_id") or None
        if not raw:
            return None, None
        return Applied.model_validate_json(raw), active

    def insert_seed(self, *, content: list[dict], presets: list[dict], setup: dict, applied: Applied, active_setup_id: str) -> None:
        with self._lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                row = self.conn.execute(
                    "SELECT value FROM meta WHERE key = 'seed_complete'"
                ).fetchone()
                if row is not None and row["value"] == "1":
                    self.conn.commit()
                    return
                for item in content:
                    self.conn.execute(
                        "INSERT INTO content_items(id, kind, name, markdown, updated_at) "
                        "VALUES(?, ?, ?, ?, ?)",
                        (item["id"], item["kind"], item["name"], item["markdown"], item["updated_at"]),
                    )
                for preset in presets:
                    self.conn.execute(
                        "INSERT INTO presets(id, kind, name, description, payload, updated_at) "
                        "VALUES(?, ?, ?, ?, ?, ?)",
                        (
                            preset["id"],
                            preset["kind"],
                            preset["name"],
                            preset["description"],
                            json.dumps(preset["payload"]),
                            preset["updated_at"],
                        ),
                    )
                self.conn.execute(
                    "INSERT INTO setups(id, name, description, preset_ids, updated_at) "
                    "VALUES(?, ?, ?, ?, ?)",
                    (
                        setup["id"],
                        setup["name"],
                        setup["description"],
                        json.dumps(setup["preset_ids"]),
                        setup["updated_at"],
                    ),
                )
                self.conn.execute(
                    "INSERT INTO meta(key, value) VALUES('applied', ?)",
                    (applied.model_dump_json(),),
                )
                self.conn.execute(
                    "INSERT INTO meta(key, value) VALUES('active_setup_id', ?)",
                    (active_setup_id,),
                )
                self.conn.execute(
                    "INSERT INTO meta(key, value) VALUES('seed_complete', '1')"
                )
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise

    def list_content(self, kind: str | None = None) -> list[dict]:
        with self._lock:
            if kind is None:
                rows = self.conn.execute(
                    "SELECT * FROM content_items ORDER BY name, id"
                ).fetchall()
            else:
                rows = self.conn.execute(
                    "SELECT * FROM content_items WHERE kind = ? ORDER BY name, id",
                    (kind,),
                ).fetchall()
        return [_content(row) for row in rows]

    def get_content(self, item_id: str) -> dict | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM content_items WHERE id = ?", (item_id,)
            ).fetchone()
        return None if row is None else _content(row)

    def count_content(self, kind: str) -> int:
        with self._lock:
            row = self.conn.execute(
                "SELECT COUNT(*) AS n FROM content_items WHERE kind = ?", (kind,)
            ).fetchone()
        return int(row["n"])

    def create_content(self, kind: str, name: str, markdown: str) -> dict:
        item = {
            "id": str(uuid4()),
            "kind": kind,
            "name": name,
            "markdown": markdown,
            "updated_at": now_iso(),
        }
        with self._lock:
            self.conn.execute(
                "INSERT INTO content_items(id, kind, name, markdown, updated_at) "
                "VALUES(:id, :kind, :name, :markdown, :updated_at)",
                item,
            )
            self.conn.commit()
        return item

    def update_content(self, item_id: str, name: str, markdown: str) -> dict | None:
        updated = now_iso()
        with self._lock:
            cursor = self.conn.execute(
                "UPDATE content_items SET name = ?, markdown = ?, updated_at = ? WHERE id = ?",
                (name, markdown, updated, item_id),
            )
            self.conn.commit()
            if cursor.rowcount == 0:
                return None
            row = self.conn.execute(
                "SELECT * FROM content_items WHERE id = ?", (item_id,)
            ).fetchone()
        return None if row is None else _content(row)

    def delete_content(self, item_id: str) -> bool:
        with self._lock:
            cursor = self.conn.execute("DELETE FROM content_items WHERE id = ?", (item_id,))
            self.conn.commit()
        return cursor.rowcount > 0

    def list_presets(self, kind: str | None = None) -> list[dict]:
        with self._lock:
            if kind is None:
                rows = self.conn.execute("SELECT * FROM presets ORDER BY name, id").fetchall()
            else:
                rows = self.conn.execute(
                    "SELECT * FROM presets WHERE kind = ? ORDER BY name, id", (kind,)
                ).fetchall()
        return [_preset(row) for row in rows]

    def get_preset(self, preset_id: str) -> dict | None:
        with self._lock:
            row = self.conn.execute("SELECT * FROM presets WHERE id = ?", (preset_id,)).fetchone()
        return None if row is None else _preset(row)

    def create_preset(self, kind: str, name: str, description: str, payload: dict) -> dict:
        item = {
            "id": str(uuid4()),
            "kind": kind,
            "name": name,
            "description": description,
            "payload": payload,
            "updated_at": now_iso(),
        }
        with self._lock:
            self.conn.execute(
                "INSERT INTO presets(id, kind, name, description, payload, updated_at) "
                "VALUES(?, ?, ?, ?, ?, ?)",
                (item["id"], kind, name, description, json.dumps(payload), item["updated_at"]),
            )
            self.conn.commit()
        return {key: item[key] for key in ("id", "kind", "name", "description", "payload")}

    def update_preset(self, preset_id: str, name: str, description: str, payload: dict) -> dict | None:
        updated = now_iso()
        with self._lock:
            cursor = self.conn.execute(
                "UPDATE presets SET name = ?, description = ?, payload = ?, updated_at = ? WHERE id = ?",
                (name, description, json.dumps(payload), updated, preset_id),
            )
            self.conn.commit()
            if cursor.rowcount == 0:
                return None
            row = self.conn.execute("SELECT * FROM presets WHERE id = ?", (preset_id,)).fetchone()
        return None if row is None else _preset(row)

    def delete_preset(self, preset_id: str) -> bool:
        with self._lock:
            cursor = self.conn.execute("DELETE FROM presets WHERE id = ?", (preset_id,))
            self.conn.commit()
        return cursor.rowcount > 0

    def list_setups(self) -> list[dict]:
        with self._lock:
            rows = self.conn.execute("SELECT * FROM setups ORDER BY name, id").fetchall()
        return [_setup(row) for row in rows]

    def get_setup(self, setup_id: str) -> dict | None:
        with self._lock:
            row = self.conn.execute("SELECT * FROM setups WHERE id = ?", (setup_id,)).fetchone()
        return None if row is None else _setup(row)

    def create_setup(self, name: str, description: str, preset_ids: dict[str, str]) -> dict:
        item_id = str(uuid4())
        updated = now_iso()
        with self._lock:
            self.conn.execute(
                "INSERT INTO setups(id, name, description, preset_ids, updated_at) "
                "VALUES(?, ?, ?, ?, ?)",
                (item_id, name, description, json.dumps(preset_ids), updated),
            )
            self.conn.commit()
        return {"id": item_id, "name": name, "description": description, "preset_ids": preset_ids}

    def update_setup(self, setup_id: str, name: str, description: str, preset_ids: dict[str, str]) -> dict | None:
        updated = now_iso()
        with self._lock:
            cursor = self.conn.execute(
                "UPDATE setups SET name = ?, description = ?, preset_ids = ?, updated_at = ? WHERE id = ?",
                (name, description, json.dumps(preset_ids), updated, setup_id),
            )
            self.conn.commit()
            if cursor.rowcount == 0:
                return None
        return {"id": setup_id, "name": name, "description": description, "preset_ids": preset_ids}

    def delete_setup(self, setup_id: str) -> bool:
        with self._lock:
            cursor = self.conn.execute("DELETE FROM setups WHERE id = ?", (setup_id,))
            self.conn.commit()
        return cursor.rowcount > 0


    def remove_with_replacement(self, category: str, item_id: str, replacement_id: str | None) -> bool:
        from nao_fair.operator_state import content_ids, CONTENT_SINGLE_KEYS, CONTENT_LIST_KEYS
        table = 'content_items' if category == 'content' else 'presets'
        with self._lock, self.conn:
            original = self.conn.execute(f'SELECT * FROM {table} WHERE id = ?', (item_id,)).fetchone()
            if original is None:
                return False
            replacement = self.conn.execute(f'SELECT * FROM {table} WHERE id = ?', (replacement_id,)).fetchone() if replacement_id else None
            if replacement_id and (replacement is None or replacement_id == item_id or replacement['kind'] != original['kind']):
                raise ValueError('Der Ersatz muss ein anderes Element derselben Art sein.')
            if category == 'content':
                if original['kind'] == 'system_prompt' and self.conn.execute("SELECT count(*) FROM content_items WHERE kind = 'system_prompt'").fetchone()[0] <= 1:
                    raise ValueError('Der letzte System-Prompt kann nicht gelöscht werden.')
                rows = self.conn.execute('SELECT id, payload FROM presets').fetchall()
                updates = [(row['id'], json.loads(row['payload'])) for row in rows if item_id in content_ids(json.loads(row['payload']))]
                if updates and replacement is None:
                    raise ValueError('Der Baustein wird von Presets verwendet. Bitte einen Ersatz wählen.')
                for preset_id, payload in updates:
                    for key in CONTENT_SINGLE_KEYS:
                        if payload.get(key) == item_id: payload[key] = replacement_id
                    for key in CONTENT_LIST_KEYS:
                        if key in payload: payload[key] = list(dict.fromkeys(replacement_id if value == item_id else value for value in payload[key]))
                    self.conn.execute('UPDATE presets SET payload = ?, updated_at = ? WHERE id = ?', (json.dumps(payload), now_iso(), preset_id))
            else:
                rows = self.conn.execute('SELECT id, preset_ids FROM setups').fetchall()
                updates = [(row['id'], json.loads(row['preset_ids'])) for row in rows if item_id in json.loads(row['preset_ids']).values()]
                if updates and replacement is None:
                    raise ValueError('Das Preset wird von Setups verwendet. Bitte einen Ersatz wählen.')
                for setup_id, slots in updates:
                    slots = {key: replacement_id if value == item_id else value for key, value in slots.items()}
                    self.conn.execute('UPDATE setups SET preset_ids = ?, updated_at = ? WHERE id = ?', (json.dumps(slots), now_iso(), setup_id))
            self.conn.execute(f'DELETE FROM {table} WHERE id = ?', (item_id,))
        return True


def _content(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "kind": row["kind"],
        "name": row["name"],
        "markdown": row["markdown"],
        "updated_at": row["updated_at"],
    }


def _preset(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "kind": row["kind"],
        "name": row["name"],
        "description": row["description"],
        "payload": json.loads(row["payload"]),
    }


def _setup(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "preset_ids": json.loads(row["preset_ids"]),
    }
