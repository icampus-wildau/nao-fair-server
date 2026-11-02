"""How the active dialog moves. No robot and no clock."""

from __future__ import annotations

from typing import Any


def step(dialog: dict | None, position: dict | None, action: str) -> dict | None:
    """Return the line to start, or None when the press says nothing.

    The caller stores the returned line as the position only after speech starts.
    """
    if dialog is None or action not in ("dialog_next", "dialog_repeat", "dialog_previous"):
        return None
    lines = [line for line in dialog.get("lines") or [] if line.get("speaker", "nao") == "nao"]
    if not lines:
        return None
    index = _index(dialog, position)
    wrap = bool(dialog.get("wrap"))
    if action == "dialog_repeat":
        if index is None:
            return None
        return lines[index]
    if action == "dialog_next":
        if index is None:
            return lines[0]
        if index + 1 < len(lines):
            return lines[index + 1]
        if wrap:
            return lines[0]
        return None
    if index is None:
        return None
    if index == 0:
        if wrap:
            return lines[-1]
        return None
    return lines[index - 1]


def position_for(dialog_id: str, line_id: str, previous: dict | None) -> dict:
    seq = 1
    if previous and previous.get("seq"):
        seq = int(previous["seq"]) + 1
    return {"dialog_id": dialog_id, "line_id": line_id, "seq": seq}


def reconcile(
    old_line_ids: list[str] | None,
    new_line_ids: list[str],
    position: dict | None,
    active_id: str | None,
) -> dict | None:
    """Keep the spoken line across an Apply, or clear it."""
    if not position or not active_id or position.get("dialog_id") != active_id:
        return None
    line_id = position.get("line_id")
    if line_id in new_line_ids:
        return position
    if not old_line_ids or line_id not in old_line_ids:
        return None
    index = old_line_ids.index(line_id)
    for earlier in reversed(old_line_ids[:index]):
        if earlier in new_line_ids:
            return {"dialog_id": active_id, "line_id": earlier, "seq": position.get("seq") or 1}
    return None


def active_dialog(dialogs: list[dict], active_id: str | None) -> dict | None:
    if not active_id:
        return None
    for dialog in dialogs:
        if dialog.get("id") == active_id:
            return dialog
    return None


def _index(dialog: dict, position: dict | None) -> int | None:
    if not position or position.get("dialog_id") != dialog.get("id"):
        return None
    line_id = position.get("line_id")
    for index, line in enumerate(line for line in dialog.get("lines") or [] if line.get("speaker", "nao") == "nao"):
        if line.get("id") == line_id:
            return index
    return None


def line_ids(dialog: dict | None) -> list[str]:
    if not dialog:
        return []
    return [line.get("id") for line in dialog.get("lines") or [] if line.get("speaker", "nao") == "nao"]


def dialog_dict(dialog: Any) -> dict:
    if hasattr(dialog, "model_dump"):
        return dialog.model_dump(mode="json")
    return dialog
