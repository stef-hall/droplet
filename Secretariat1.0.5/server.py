from __future__ import annotations

import ast
import operator as op
from flask import Flask, Response, jsonify, redirect, render_template, request, send_from_directory, session, stream_with_context # type: ignore
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
import sqlite3
import re
import os

from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from caldav import DAVClient # type: ignore
from openai import OpenAI # type: ignore
import vobject # type: ignore
import json
import warnings
import logging
import base64
import mimetypes
import uuid
import traceback
import secrets
import hashlib
from urllib.parse import urlencode, unquote
from urllib.request import Request, urlopen
from pathlib import Path
from werkzeug.security import check_password_hash, generate_password_hash # type: ignore
from tools import AddEvent, GetEvents, GetCalendarNames, DeleteEvent, ReadList, EditList, DeleteList, EditEvent, GetWeather, _REMINDER_UNCHANGED, configure_tools

global api_key
warnings.simplefilter("ignore", DeprecationWarning)
app = Flask(__name__)
app.secret_key = os.environ.get("SECRETARIAT_APP_SECRET", "replace-me-in-production")
api_key = ""
session_store = {}
session_store_lock = Lock()
_trello_id_alias_lock = Lock()
_trello_id_alias_store: dict[int, dict[str, dict[str, object]]] = {}
SESSION_TTL_SECONDS = 6 * 60 * 60
TRUSTED_DEVICE_COOKIE = "secretariat_trusted_device"
TRUSTED_DEVICE_DAYS = 60
MAX_PARALLEL_TOOL_CALLS = 10
LISTS_DIR = Path(__file__).resolve( ).parent / "lists"
DB_PATH = Path(__file__).resolve().parent / "secretariat.db"
DEFAULT_ASSISTANT_MODEL = "gpt-5.4"
ALLOWED_ASSISTANT_MODELS = {"gpt-5.4-mini", "gpt-5.4"}


class _SkipApiListsAccessLog(logging.Filter):
    def filter(self, record):
        msg = record.getMessage()
        if 'GET /api/lists HTTP/1.1" 200 -' in msg:
            return False
        if 'POST /api/secretariat/stream HTTP/1.1" 200 -' in msg:
            return False
        return True


_werkzeug_logger = logging.getLogger("werkzeug")
_werkzeug_logger.addFilter(_SkipApiListsAccessLog())
for _handler in _werkzeug_logger.handlers:
    _handler.addFilter(_SkipApiListsAccessLog())


def _log(label, message):
    print(f"[{label}] {message}", flush=True)


def _log_json(label, payload):
    pretty = json.dumps(payload, indent=2, ensure_ascii=False, default=str)
    print(f"[{label}] {pretty}", flush=True)


def _extract_model_text(output_items):
    parts = []
    for item in output_items or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "message":
            continue
        for block in item.get("content", []) or []:
            if not isinstance(block, dict):
                continue
            text = block.get("text")
            if text:
                text_str = str(text)
                try:
                    parsed = json.loads(text_str)
                    if isinstance(parsed, dict) and "message" in parsed:
                        parts.append(str(parsed.get("message", "")))
                    else:
                        parts.append(text_str)
                except Exception:
                    parts.append(text_str)
    return re.sub(r"\s+", " ", "\n".join(parts)).strip()


def _tool_name_to_status_label(tool_name: str) -> str:
    raw_name = str(tool_name or "").strip()
    normalized = raw_name.lower().replace(" ", "").replace("_", "")
    label_map = {
        "addevent": "Adding Event...",
        "getevents": "Getting Events...",
        "deleteevent": "Deleting...",
        "editevent": "Editing Event...",
        "readlist": "Reading List...",
        "editlist": "Updating List...",
        "deletelist": "Deleting List...",
        "gettrellolists": "Getting Lists...",
        "gettrellocards": "Getting Cards...",
        "createtrellocard": "Creating Card...",
        "createtrellolist": "Creating List...",
        "edittrellocard": "Editing Card...",
        "deletetrellolist": "Deleting List...",
        "deletetrellocard": "Deleting Card...",
        "getweather": "Getting Weather...",
        "getcalendarnames": "Getting Calendar Names...",
    }
    if normalized in label_map:
        return label_map[normalized]
    return f"Running {raw_name or 'Tool'}..."


def _batch_status_label(function_calls: list[dict]) -> str:
    names = [str(call.get("name", "")) for call in function_calls if call.get("name")]
    if not names:
        return "Running Tools..."
    if len(names) == 1:
        return _tool_name_to_status_label(names[0])
    unique_names = list(dict.fromkeys(names))
    if len(unique_names) == 1:
        return f"{_tool_name_to_status_label(unique_names[0]).rstrip('.')} x{len(names)}..."
    return "Running Multiple Steps..."


def _accumulate_action_report(counter: dict[str, int], tool_outputs: list[dict]):
    for item in tool_outputs:
        if not isinstance(item, dict):
            continue
        payload_raw = item.get("output")
        if not payload_raw:
            continue
        try:
            payload = json.loads(payload_raw)
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue

        if payload.get("status") != "success":
            continue

        tool = str(payload.get("tool", "")).strip()
        result = payload.get("result")

        if tool == "AddEvent":
            counter["events_added"] = counter.get("events_added", 0) + 1
        elif tool == "DeleteEvent":
            deleted = True
            if isinstance(result, dict):
                deleted = str(result.get("status", "")).strip().lower() == "deleted"
            if deleted:
                counter["events_deleted"] = counter.get("events_deleted", 0) + 1
        elif tool == "EditEvent":
            edited = True
            if isinstance(result, dict):
                edited = str(result.get("status", "")).strip().lower() == "edited"
            if edited:
                counter["events_edited"] = counter.get("events_edited", 0) + 1
        elif tool == "EditList":
            list_changed = True
            list_created = False
            if isinstance(result, dict):
                list_changed = str(result.get("status", "")).strip().lower() == "success"
                list_created = bool(result.get("created", False))
            if list_changed:
                if list_created:
                    counter["lists_added"] = counter.get("lists_added", 0) + 1
                else:
                    counter["lists_edited"] = counter.get("lists_edited", 0) + 1
        elif tool == "DeleteList":
            list_deleted = True
            if isinstance(result, dict):
                list_deleted = str(result.get("status", "")).strip().lower() == "deleted"
            if list_deleted:
                counter["lists_deleted"] = counter.get("lists_deleted", 0) + 1


def _format_action_report(counter: dict[str, int]) -> str:
    lines = []
    if counter.get("events_added", 0):
        lines.append(f"Events added +{counter['events_added']}")
    if counter.get("events_edited", 0):
        lines.append(f"Events edited {counter['events_edited']}")
    if counter.get("events_deleted", 0):
        lines.append(f"Deleted Events -{counter['events_deleted']}")
    if counter.get("lists_added", 0):
        lines.append(f"List added +{counter['lists_added']}")
    if counter.get("lists_edited", 0):
        lines.append(f"List edited {counter['lists_edited']}")
    if counter.get("lists_deleted", 0):
        lines.append(f"List deleted -{counter['lists_deleted']}")
    if not lines:
        return ""
    return "\n\n```summary\n" + "\n".join(lines) + "\n```"


def _prune_sessions(now_ts: float):
    stale_ids = [
        sid
        for sid, data in session_store.items()
        if now_ts - float(data.get("last_seen_ts", now_ts)) > SESSION_TTL_SECONDS
    ]
    for sid in stale_ids:
        session_store.pop(sid, None)


def _db_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db():
    with _db_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trusted_devices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                device_label TEXT,
                user_agent TEXT,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                last_used_at TEXT NOT NULL,
                last_used_ip TEXT,
                revoked_at TEXT,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id INTEGER PRIMARY KEY,
                caldav_url TEXT,
                caldav_username TEXT,
                caldav_password TEXT,
                caldav_calendar TEXT,
                trello_token TEXT,
                trello_boards TEXT,
                assistant_model TEXT,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
            """
        )
        columns = conn.execute("PRAGMA table_info(user_settings)").fetchall()
        existing_column_names = {str(row["name"]) for row in columns}
        if "assistant_model" not in existing_column_names:
            conn.execute("ALTER TABLE user_settings ADD COLUMN assistant_model TEXT")
        if "trello_token" not in existing_column_names:
            conn.execute("ALTER TABLE user_settings ADD COLUMN trello_token TEXT")
        if "trello_boards" not in existing_column_names:
            conn.execute("ALTER TABLE user_settings ADD COLUMN trello_boards TEXT")
        if "trello_board_ids" not in existing_column_names:
            conn.execute("ALTER TABLE user_settings ADD COLUMN trello_board_ids TEXT")
        conn.commit()


def _normalize_email(email: str) -> str:
    return str(email or "").strip().lower()


def _valid_email(email: str) -> bool:
    return bool(email)


def _normalize_caldav_url(caldav_url: str, caldav_username: str) -> str:
    raw_url = str(caldav_url or "").strip()
    if not raw_url:
        return raw_url

    lowered = raw_url.lower()
    is_google = (
        "googleusercontent.com/caldav/v2" in lowered
        or "google.com/calendar/dav/" in lowered
    )
    if not is_google:
        return raw_url

    username = str(caldav_username or "").strip()
    if not username:
        match = re.search(r"/calendar/dav/([^/]+)/events/?", raw_url, flags=re.IGNORECASE)
        if match:
            username = unquote(match.group(1)).strip()
    if not username:
        return raw_url

    return f"https://www.google.com/calendar/dav/{username}/events/"


def _parse_caldav_calendar_names(raw_value: str | None) -> list[str]:
    raw = str(raw_value or "").strip()
    if not raw:
        return []
    seen: set[str] = set()
    names: list[str] = []
    for part in raw.split(","):
        name = part.strip()
        if not name:
            continue
        lowered = name.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        names.append(name)
    return names


def _get_trello_alias(user_id: int, entity: str, real_id: str) -> str:
    safe_id = str(real_id or "").strip()
    if not safe_id:
        return safe_id
    kind = str(entity or "").strip().lower()
    if kind not in {"board", "list", "card"}:
        return safe_id
    prefix = {"board": "b", "list": "l", "card": "c"}[kind]
    with _trello_id_alias_lock:
        user_state = _trello_id_alias_store.setdefault(int(user_id), {})
        kind_state = user_state.setdefault(
            kind,
            {"counter": 0, "id_to_alias": {}, "alias_to_id": {}},
        )
        id_to_alias = kind_state["id_to_alias"]
        alias_to_id = kind_state["alias_to_id"]
        existing = id_to_alias.get(safe_id)
        if existing:
            return str(existing)
        kind_state["counter"] = int(kind_state["counter"]) + 1
        alias = f"{prefix}{kind_state['counter']}"
        id_to_alias[safe_id] = alias
        alias_to_id[alias] = safe_id
        return alias


def _resolve_trello_id_for_user(user_id: int, entity: str, id_or_alias: str) -> str:
    key = str(id_or_alias or "").strip()
    if not key:
        return key
    kind = str(entity or "").strip().lower()
    if kind not in {"board", "list", "card"}:
        return key
    with _trello_id_alias_lock:
        user_state = _trello_id_alias_store.get(int(user_id), {})
        kind_state = user_state.get(kind, {})
        alias_to_id = kind_state.get("alias_to_id", {})
        resolved = alias_to_id.get(key, key)
    return str(resolved)


def _alias_trello_list_rows_for_user(user_id: int, rows: list[dict]) -> list[dict]:
    aliased = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        board_real = str(row.get("board_id", "")).strip()
        list_real = str(row.get("list_id", "")).strip()
        clone = dict(row)
        if board_real:
            clone["board_id"] = _get_trello_alias(int(user_id), "board", board_real)
        if list_real:
            clone["list_id"] = _get_trello_alias(int(user_id), "list", list_real)
        aliased.append(clone)
    return aliased


def _alias_trello_card_rows_for_user(user_id: int, rows: list[dict]) -> list[dict]:
    aliased = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        card_real = str(row.get("card_id", "")).strip()
        list_real = str(row.get("list_id", "")).strip()
        clone = dict(row)
        if card_real:
            clone["card_id"] = _get_trello_alias(int(user_id), "card", card_real)
        if list_real:
            clone["list_id"] = _get_trello_alias(int(user_id), "list", list_real)
        aliased.append(clone)
    return aliased


def _require_auth():
    _restore_auth_from_trusted_device()
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"ok": False, "error": "Authentication required."}), 401
    return None


def _get_user_settings(user_id: int):
    with _db_conn() as conn:
        return conn.execute(
            """
            SELECT user_id, caldav_url, caldav_username, caldav_password, caldav_calendar, trello_token, trello_boards, trello_board_ids, assistant_model, updated_at
            FROM user_settings
            WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()


def _get_user_caldav_settings(user_id: int) -> dict[str, str]:
    row = _get_user_settings(user_id)
    if not row:
        raise ValueError("CalDAV settings are missing. Open Settings and add your CalDAV URL, username, and password.")

    caldav_username = str(row["caldav_username"] or "").strip()
    caldav_url = _normalize_caldav_url(str(row["caldav_url"] or "").strip(), caldav_username)
    caldav_password = str(row["caldav_password"] or "")
    caldav_calendar = str(row["caldav_calendar"] or "").strip()

    if not caldav_url or not caldav_username or not caldav_password:
        raise ValueError("CalDAV settings are incomplete. Open Settings and add your CalDAV URL, username, and password.")

    return {
        "url": caldav_url,
        "username": caldav_username,
        "password": caldav_password,
        "calendar": caldav_calendar,
    }


def _normalize_assistant_model(raw_model: str | None) -> str:
    model = str(raw_model or "").strip()
    if model in ALLOWED_ASSISTANT_MODELS:
        return model
    return DEFAULT_ASSISTANT_MODEL


def _get_user_caldav_calendars(user_id: int):
    settings = _get_user_caldav_settings(user_id)
    client = DAVClient(
        url=settings["url"],
        username=settings["username"],
        password=settings["password"],
    )
    principal = client.principal()
    calendars = principal.calendars()
    if not calendars:
        raise ValueError("No calendars are available for this CalDAV account.")

    preferred_calendars = _parse_caldav_calendar_names(settings["calendar"])
    if preferred_calendars:
        preferred_set = {name.strip().lower() for name in preferred_calendars}
        matching = [
            calendar
            for calendar in calendars
            if str(calendar.get_display_name() or "").strip().lower() in preferred_set
        ]
        if not matching:
            raise ValueError(f'Calendar "{settings["calendar"]}" was not found for this CalDAV account.')
        return matching

    return calendars


def _get_trello_boards_for_user(user_id: int) -> list[dict]:
    row = _get_user_settings(int(user_id))
    if not row:
        raise ValueError("Trello settings are missing.")
    trello_token = str(row["trello_token"] or "").strip()
    if not trello_token:
        raise ValueError("Trello token is missing.")

    query = urlencode({"fields": "name", "key": "ac891ffdcf2553ac640f08509636d1c6", "token": trello_token})
    url = f"https://api.trello.com/1/members/me/boards?{query}"
    with urlopen(url, timeout=12) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, list):
        raise ValueError("Unexpected Trello response.")

    boards = []
    for board in payload:
        if not isinstance(board, dict):
            continue
        board_id = str(board.get("id", "")).strip()
        board_name = str(board.get("name", "")).strip()
        if not board_id or not board_name:
            continue
        boards.append({"id": board_id, "name": board_name})
    return boards


def _get_trello_lists_for_user(user_id: int, board_id: str | None = None) -> list[dict]:
    row = _get_user_settings(int(user_id))
    if not row:
        raise ValueError("Trello settings are missing.")
    trello_token = str(row["trello_token"] or "").strip()
    if not trello_token:
        raise ValueError("Trello token is missing.")

    selected_board_ids = _parse_caldav_calendar_names(str(row["trello_board_ids"] or ""))
    boards = _get_trello_boards_for_user(int(user_id))
    board_lookup = {str(board["id"]): str(board["name"]) for board in boards}

    if board_id:
        target_ids = [str(board_id).strip()]
    elif selected_board_ids:
        target_ids = [bid for bid in selected_board_ids if bid in board_lookup]
    else:
        target_ids = list(board_lookup.keys())

    output = []
    for bid in target_ids:
        if not bid:
            continue
        query = urlencode({"fields": "name", "key": "ac891ffdcf2553ac640f08509636d1c6", "token": trello_token})
        url = f"https://api.trello.com/1/boards/{bid}/lists?{query}"
        with urlopen(url, timeout=12) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, list):
            continue
        for item in payload:
            if not isinstance(item, dict):
                continue
            list_name = str(item.get("name", "")).strip()
            list_id = str(item.get("id", "")).strip()
            if not list_name or not list_id:
                continue
            output.append(
                {
                    "board_id": bid,
                    "board_name": board_lookup.get(bid, ""),
                    "list_id": list_id,
                    "list_name": list_name,
                }
            )
    return output


def _get_trello_cards_for_user(user_id: int, list_id: str) -> list[dict]:
    row = _get_user_settings(int(user_id))
    if not row:
        raise ValueError("Trello settings are missing.")
    trello_token = str(row["trello_token"] or "").strip()
    if not trello_token:
        raise ValueError("Trello token is missing.")

    safe_list_id = str(list_id or "").strip()
    if not safe_list_id:
        raise ValueError("list_id is required.")

    query = urlencode(
        {
            "fields": "name,desc,due,url,idList",
            "key": "ac891ffdcf2553ac640f08509636d1c6",
            "token": trello_token,
        }
    )
    url = f"https://api.trello.com/1/lists/{safe_list_id}/cards?{query}"
    with urlopen(url, timeout=12) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, list):
        raise ValueError("Unexpected Trello response.")

    cards = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        card_id = str(item.get("id", "")).strip()
        card_name = str(item.get("name", "")).strip()
        if not card_id or not card_name:
            continue
        cards.append(
            {
                "card_id": card_id,
                "card_name": card_name,
                "description": str(item.get("desc", "") or ""),
                "due": item.get("due"),
                "url": str(item.get("url", "") or ""),
                "list_id": str(item.get("idList", "") or safe_list_id),
            }
        )
    return cards


def _edit_trello_card_for_user(
    user_id: int,
    card_id: str,
    name: str | None = None,
    description: str | None = None,
    due: str | None = None,
    list_id: str | None = None,
) -> dict:
    row = _get_user_settings(int(user_id))
    if not row:
        raise ValueError("Trello settings are missing.")
    trello_token = str(row["trello_token"] or "").strip()
    if not trello_token:
        raise ValueError("Trello token is missing.")

    safe_card_id = str(card_id or "").strip()
    if not safe_card_id:
        raise ValueError("card_id is required.")

    params: dict[str, str] = {
        "key": "ac891ffdcf2553ac640f08509636d1c6",
        "token": trello_token,
    }
    updated_fields: dict[str, bool] = {
        "name": False,
        "description": False,
        "due": False,
        "list_id": False,
    }

    if name is not None:
        params["name"] = str(name)
        updated_fields["name"] = True
    if description is not None:
        params["desc"] = str(description)
        updated_fields["description"] = True
    if due is not None:
        params["due"] = str(due)
        updated_fields["due"] = True
    if list_id is not None:
        params["idList"] = str(list_id)
        updated_fields["list_id"] = True

    if not any(updated_fields.values()):
        raise ValueError("At least one editable field is required.")

    body = urlencode(params).encode("utf-8")
    request = Request(
        url=f"https://api.trello.com/1/cards/{safe_card_id}",
        data=body,
        method="PUT",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urlopen(request, timeout=12) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Unexpected Trello response.")

    return {
        "status": "edited",
        "card_id": str(payload.get("id", safe_card_id)),
        "updated_fields": updated_fields,
    }


def _delete_trello_card_for_user(user_id: int, card_id: str) -> dict:
    row = _get_user_settings(int(user_id))
    if not row:
        raise ValueError("Trello settings are missing.")
    trello_token = str(row["trello_token"] or "").strip()
    if not trello_token:
        raise ValueError("Trello token is missing.")

    safe_card_id = str(card_id or "").strip()
    if not safe_card_id:
        raise ValueError("card_id is required.")

    params = urlencode({"key": "ac891ffdcf2553ac640f08509636d1c6", "token": trello_token})
    request = Request(
        url=f"https://api.trello.com/1/cards/{safe_card_id}?{params}",
        method="DELETE",
    )
    with urlopen(request, timeout=12):
        pass
    return {"status": "deleted", "card_id": safe_card_id}


def _delete_trello_list_for_user(user_id: int, list_id: str) -> dict:
    row = _get_user_settings(int(user_id))
    if not row:
        raise ValueError("Trello settings are missing.")
    trello_token = str(row["trello_token"] or "").strip()
    if not trello_token:
        raise ValueError("Trello token is missing.")

    safe_list_id = str(list_id or "").strip()
    if not safe_list_id:
        raise ValueError("list_id is required.")

    params = urlencode({"key": "ac891ffdcf2553ac640f08509636d1c6", "token": trello_token, "value": "true"})
    request = Request(
        url=f"https://api.trello.com/1/lists/{safe_list_id}/closed?{params}",
        method="PUT",
    )
    with urlopen(request, timeout=12):
        pass
    return {"status": "deleted", "list_id": safe_list_id}


def _create_trello_card_for_user(
    user_id: int,
    list_id: str,
    name: str,
    description: str | None = None,
    due: str | None = None,
) -> dict:
    row = _get_user_settings(int(user_id))
    if not row:
        raise ValueError("Trello settings are missing.")
    trello_token = str(row["trello_token"] or "").strip()
    if not trello_token:
        raise ValueError("Trello token is missing.")

    safe_list_id = str(list_id or "").strip()
    safe_name = str(name or "").strip()
    if not safe_list_id:
        raise ValueError("list_id is required.")
    if not safe_name:
        raise ValueError("name is required.")

    params: dict[str, str] = {
        "key": "ac891ffdcf2553ac640f08509636d1c6",
        "token": trello_token,
        "idList": safe_list_id,
        "name": safe_name,
    }
    if description is not None:
        params["desc"] = str(description)
    if due is not None:
        params["due"] = str(due)

    body = urlencode(params).encode("utf-8")
    request = Request(
        url="https://api.trello.com/1/cards",
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urlopen(request, timeout=12) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Unexpected Trello response.")

    return {
        "status": "created",
        "card_id": str(payload.get("id", "")),
        "card_name": str(payload.get("name", safe_name)),
        "list_id": str(payload.get("idList", safe_list_id)),
        "url": str(payload.get("url", "")),
    }


def _create_trello_list_for_user(user_id: int, board_id: str, name: str) -> dict:
    row = _get_user_settings(int(user_id))
    if not row:
        raise ValueError("Trello settings are missing.")
    trello_token = str(row["trello_token"] or "").strip()
    if not trello_token:
        raise ValueError("Trello token is missing.")

    safe_board_id = str(board_id or "").strip()
    safe_name = str(name or "").strip()
    if not safe_board_id:
        raise ValueError("board_id is required.")
    if not safe_name:
        raise ValueError("name is required.")

    params = {
        "key": "ac891ffdcf2553ac640f08509636d1c6",
        "token": trello_token,
        "idBoard": safe_board_id,
        "name": safe_name,
    }
    body = urlencode(params).encode("utf-8")
    request = Request(
        url="https://api.trello.com/1/lists",
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urlopen(request, timeout=12) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Unexpected Trello response.")

    return {
        "status": "created",
        "list_id": str(payload.get("id", "")),
        "list_name": str(payload.get("name", safe_name)),
        "board_id": str(payload.get("idBoard", safe_board_id)),
    }


def _utc_now():
    return datetime.now(timezone.utc)


def _device_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _client_ip() -> str | None:
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip() or None
    return request.remote_addr


def _restore_auth_from_trusted_device() -> bool:
    if session.get("user_id") and session.get("email"):
        return True

    token = request.cookies.get(TRUSTED_DEVICE_COOKIE, "")
    if not token:
        return False

    token_hash = _device_token_hash(token)
    now_iso = _utc_now().isoformat()
    with _db_conn() as conn:
        row = conn.execute(
            """
            SELECT td.id AS trusted_device_id, u.id AS user_id, u.email AS email
            FROM trusted_devices td
            JOIN users u ON u.id = td.user_id
            WHERE td.token_hash = ?
              AND td.revoked_at IS NULL
              AND td.expires_at > ?
            """,
            (token_hash, now_iso),
        ).fetchone()
        if not row:
            return False
        conn.execute(
            """
            UPDATE trusted_devices
            SET last_used_at = ?, last_used_ip = ?
            WHERE id = ?
            """,
            (now_iso, _client_ip(), int(row["trusted_device_id"])),
        )
        conn.commit()

    session["user_id"] = int(row["user_id"])
    session["email"] = str(row["email"])
    return True


def _set_trusted_device_cookie(response: Response, token: str, expires_at: datetime):
    response.set_cookie(
        TRUSTED_DEVICE_COOKIE,
        token,
        httponly=True,
        secure=not app.debug,
        samesite="Lax",
        expires=expires_at,
    )


def _clear_trusted_device_cookie(response: Response):
    response.set_cookie(
        TRUSTED_DEVICE_COOKIE,
        "",
        httponly=True,
        secure=not app.debug,
        samesite="Lax",
        expires=0,
    )


def _issue_trusted_device(user_id: int, device_label: str | None = None):
    token = secrets.token_urlsafe(48)
    token_hash = _device_token_hash(token)
    now = _utc_now()
    expires_at = now + timedelta(days=TRUSTED_DEVICE_DAYS)
    safe_user_agent = str(request.headers.get("User-Agent", ""))[:512]
    safe_label = (device_label or safe_user_agent or "Trusted device")[:120]

    with _db_conn() as conn:
        conn.execute(
            """
            INSERT INTO trusted_devices (
                user_id, token_hash, device_label, user_agent, created_at, expires_at, last_used_at, last_used_ip, revoked_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                user_id,
                token_hash,
                safe_label,
                safe_user_agent,
                now.isoformat(),
                expires_at.isoformat(),
                now.isoformat(),
                _client_ip(),
            ),
        )
        conn.commit()
    return token, expires_at


def _revoke_trusted_device_by_cookie():
    token = request.cookies.get(TRUSTED_DEVICE_COOKIE, "")
    if not token:
        return
    token_hash = _device_token_hash(token)
    now_iso = _utc_now().isoformat()
    with _db_conn() as conn:
        conn.execute(
            """
            UPDATE trusted_devices
            SET revoked_at = ?
            WHERE token_hash = ? AND revoked_at IS NULL
            """,
            (now_iso, token_hash),
        )
        conn.commit()


_init_db()


def get_available_lists(user_id: int | None = None):
    base_dir = LISTS_DIR
    if user_id is not None:
        base_dir = LISTS_DIR / str(int(user_id))
    if not base_dir.exists():
        return []
    return sorted(
        file_path.stem
        for file_path in base_dir.glob("*.txt")
        if file_path.is_file()
    )


def get_available_list_entries(user_id: int):
    names = get_available_lists(user_id=user_id)
    entries = []
    for name in names:
        result = ReadList(user_id=user_id, list_name=name)
        if not isinstance(result, dict):
            continue
        if str(result.get("status", "")).strip().lower() != "success":
            continue
        entries.append(
            {
                "list_name": str(result.get("list_name", name)),
                "content": str(result.get("content", "")),
            }
        )
    return entries

configure_tools(_get_user_caldav_calendars, LISTS_DIR)

concise_prompt = """
You are an assistant calender manager with access to tools.

Use a tool whenever it is required to complete the user’s request or when the tool provides the most accurate way to perform the task.

Rules:
- NEVER guess tool outputs
- ONLY use provided tools with EXACT schema
- You operate ONLY in the local timezone. For interacting, and reasoning with events
- prefer tools over free-text when an action/data retrieval is needed
- Don't use Em Dashes ("—")
- After any tool execution, return a user-facing message ONLY IF: the task is complete, or user input is required
- Consult your context first before calling GetEvents/Get...
- If the USER tells you to Restore/Undo/Bring Back/Recreate an event your FIRST STEP is to look back in your context for the requested events, then recreate the event
- If a requested time could be interpreted as AM or PM, do not guess; ask a clarifying question before calling tools
- Display multipile events in a markdown time table 
- If someone calls you 'bud' you have to call them 'bud' back
- Always return a state:
  - RUNNING = Operating Tools/Thinking
  - WAITING = Waiting for User Input
  - DONE = When totally finished your task
- The "message" field may contain markdown for formatting supported: 
  - headers
  - **bold**, *italics* 
  - bullet lists
  - inline `code`, fenced ```code``` 
  - pipe tables | a | b |)
"""
system_prompt = concise_prompt + """
Reminders:
- If multiple details are missing, ask for them all in one message.
- Use FastReplies for obvious next steps, clarifications, undo, confirmations, or suggested actions.
- If a duration cannot be reasonably defered, default to *1 hour*
- When a tool creates resources and returns IDs/UIDs, assume those returned IDs will be visible in conversation context after the batched tool results complete. Therefore, batch independent create calls together. Only serialize calls when the next call requires a value produced by a previous call.
- If given a City to GetWeather for; default to using the Co-Ordinates (Lat/Long) of that City's Center. 
- apply extra reasoning scrutiny around meridians (AM/PM), especially 12:00 times
- Don't Return technical ID's to the user, they are aliased and only usable backend
- "rn" = "Right Now"
- "tn" = "Tonight"

Tone:
- Keep responses concise. Prefer plain phrasing over long explanations
- Avoid filler phrases
- When asking for follow up details, be direct with the user. Ask simply for the information required don't explain why you need it.

For ambiguous delete/remove/edit requests:
- NEVER ask the user a follow up for more information, without FIRST consulting the chat history context window, and if no answer is found; The respective Get... Tools.
- After checking context:
  - If exactly one matching event/list/item exists, act on it.
  - If multiple matches exist, ask which one.
  - If no matches exist, say you couldn’t find it and ask for more detail.

FastReplies rules:
- FastReplies MUST use exactly: [[send: visible assistant text|hidden user message]]
- Visible text must fit naturally in the assistant message.
- Hidden text must be the user’s intended reply.
- Any suggested actions, or solutions contained in a clarification questions MUST have FastReplies options.
- Any “I can…”, “tell me…”, “if you meant…”, or “do you want…” suggestion needs a FastReply.
- e.g. "I couldn’t find a list called that. If you [[send: meant an event|Yes, I meant an event]], tell me which to remove."
- soft max of 3 FastReplies per message

-When multiple tool actions are needed, plan them as ordered steps:
  - Emit all independent actions that can run at the same time in the same assistant turn as multiple tool calls.
  - Emit dependent actions in later assistant turns only after prior tool outputs are available.
  - Treat delete-then-add flows as separate sequential turns.

STRICT VALID RESPONSE FORMAT:
{
    "state": "RUNNING|WAITING|DONE",
    "message": "..."
}

"""

tools = [
    {
        "type": "function",
        "name": "GetTrelloLists",
        "description": "Get Trello list names. Optionally provide board_id to scope to one board; otherwise returns lists from selected boards or all accessible boards.",
        "parameters": {
            "type": "object",
            "properties": {
                "board_id": {
                    "type": "string",
                    "description": "Optional Trello board ID (for example: 68a4ff7e11673166fa68cbfa).",
                }
            },
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "GetTrelloCards",
        "description": "Get all cards contained in a Trello list by list_id.",
        "parameters": {
            "type": "object",
            "properties": {
                "list_id": {
                    "type": "string",
                    "description": "Required Trello list ID (for example: 68a50bb5ad3235302b006a5c).",
                }
            },
            "required": ["list_id"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "EditTrelloCard",
        "description": "Edit a Trello card by card_id. Provide one or more fields to update.",
        "parameters": {
            "type": "object",
            "properties": {
                "card_id": {
                    "type": "string",
                    "description": "Required Trello card ID.",
                },
                "name": {
                    "type": "string",
                    "description": "Optional new card title.",
                },
                "description": {
                    "type": "string",
                    "description": "Optional new card description.",
                },
                "due": {
                    "type": "string",
                    "description": "Optional due datetime in ISO-8601 (or null-like empty string to clear).",
                },
                "list_id": {
                    "type": "string",
                    "description": "Optional destination Trello list ID (moves the card).",
                },
            },
            "required": ["card_id"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "DeleteTrelloCard",
        "description": "Delete a Trello card by card_id.",
        "parameters": {
            "type": "object",
            "properties": {
                "card_id": {
                    "type": "string",
                    "description": "Required Trello card ID.",
                }
            },
            "required": ["card_id"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "DeleteTrelloList",
        "description": "Delete (archive) a Trello list by list_id.",
        "parameters": {
            "type": "object",
            "properties": {
                "list_id": {
                    "type": "string",
                    "description": "Required Trello list ID.",
                }
            },
            "required": ["list_id"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "CreateTrelloCard",
        "description": "Create a new Trello card in a given list.",
        "parameters": {
            "type": "object",
            "properties": {
                "list_id": {
                    "type": "string",
                    "description": "Required Trello list ID where the card will be created.",
                },
                "name": {
                    "type": "string",
                    "description": "Required card title.",
                },
                "description": {
                    "type": "string",
                    "description": "Optional card description.",
                },
                "due": {
                    "type": "string",
                    "description": "Optional due datetime in ISO-8601.",
                },
            },
            "required": ["list_id", "name"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "CreateTrelloList",
        "description": "Create a new Trello list in a given board.",
        "parameters": {
            "type": "object",
            "properties": {
                "board_id": {
                    "type": "string",
                    "description": "Required Trello board ID where the list will be created.",
                },
                "name": {
                    "type": "string",
                    "description": "Required Trello list name.",
                },
            },
            "required": ["board_id", "name"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "AddEvent",
        "description": "Creates a calendar event in iCloud via CalDAV.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Event title"
                },
                "times": {
                    "type": "array",
                    "description": "Start date/time, then finish date/time. in LOCAL TIMEZONE using format YYYYMMDDTHHMMSS+XX:XX (e.g. 20260501T000000+12:00)",
                    "items": {"type": "string"},
                    "minItems": 2,
                    "maxItems": 2
                },
                "location": {
                    "type": "string",
                    "description": "Location the event takes place. Optional field. Omit if not provided."
                },
                "description": {
                    "type": "string",
                    "description": "Optional free-form description of the event. Not required for simple events (e.g. 'Run'). Can be used for dynamic or evolving context such as notes, instructions, or linked data (e.g. a bullet pointed shopping list for 'Supermarket' that may change over time). If not provided, omit or leave empty."
                },
                "rrule": {
                    "type": "string",
                    "description": "Optional recurrence rule (RRULE) for repeating events in iCalendar format (e.g. 'FREQ=WEEKLY;INTERVAL=1'). Defines how the event repeats over time (weekly, monthly, etc). If not provided, the event is treated as a single occurrence."
                },
                "reminder_minutes_before": {
                    "type": ["integer", "null"],
                    "description": "Optional reminder/alert lead time in minutes before event start (e.g. 10, 30, 60). Use null for no reminder."
                }
            },
            "required": ["title", "times", "location", "description", "rrule", "reminder_minutes_before"],
            "additionalProperties": False
        }
    },
    {
        "type": "function",
        "name": "GetEvents",
        "description": "Retrieve all calendar events within a given time range (inclusive of start, exclusive of end). Returns events with their UID, start time, end time, and summary.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "times": {
                    "type": "array",
                    "description": "Start date/time, then finish date/time. in LOCAL TIMEZONE using format YYYYMMDDTHHMMSS+XX:XX (e.g. 20260501T000000+12:00)",
                    "items": {"type": "string"},
                    "minItems": 2,
                    "maxItems": 2
                }
            },
            "required": ["times"],
            "additionalProperties": False
        }
    },
    {
        "type": "function",
        "name": "DeleteEvent",
        "description": "Delete a calendar event by its unique UID from the calendar.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "uid": {
                    "type": "string",
                    "description": "The unique identifier of the calendar event to delete."
                }
            },
            "required": ["uid"],
            "additionalProperties": False
        }
    },
    {
        "type": "function",
        "name": "ReadList",
        "description": "Read a saved list from the local lists folder by list name.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "list_name": {
                    "type": "string",
                    "description": "List name without .txt extension (e.g. Shopping List)"
                }
            },
            "required": ["list_name"],
            "additionalProperties": False
        }
    },
    {
        "type": "function",
        "name": "EditList",
        "description": "Create or overwrite a saved list in the local lists folder.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "list_name": {
                    "type": "string",
                    "description": "List name without .txt extension (e.g. Shopping List)"
                },
                "content": {
                    "type": "string",
                    "description": "Full text content that should be saved to the list file."
                }
            },
            "required": ["list_name", "content"],
            "additionalProperties": False
        }
    },
    {
        "type": "function",
        "name": "DeleteList",
        "description": "Delete a saved list from the local lists folder by list name.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "list_name": {
                    "type": "string",
                    "description": "List name without .txt extension (e.g. Shopping List)"
                }
            },
            "required": ["list_name"],
            "additionalProperties": False
        }
    },
    {
        "type": "function",
        "name": "EditEvent",
        "description": "Edit an existing calendar event by UID in one step. This performs an internal delete-and-recreate while preserving the event UID.",
        "strict": False,
        "parameters": {
            "type": "object",
            "properties": {
                "uid": {
                    "type": "string",
                    "description": "The unique identifier of the calendar event to edit."
                },
                "title": {
                    "type": "string",
                    "description": "Updated event title. Optional."
                },
                "times": {
                    "type": "array",
                    "description": "Updated start date/time, then finish date/time. in LOCAL TIMEZONE using format YYYYMMDDTHHMMSS+XX:XX (e.g. 20260501T000000+12:00). Optional.",
                    "items": {"type": "string"},
                    "minItems": 2,
                    "maxItems": 2
                },
                "location": {
                    "type": "string",
                    "description": "Updated event location. Optional."
                },
                "description": {
                    "type": "string",
                    "description": "Updated event description. Optional."
                },
                "rrule": {
                    "type": "string",
                    "description": "Updated recurrence rule (RRULE). Optional."
                },
                "reminder_minutes_before": {
                    "type": ["integer", "null"],
                    "description": "Updated reminder/alert lead time in minutes before event start. Optional. Use null to remove the reminder."
                }
            },
            "required": ["uid"],
            "additionalProperties": False
        }
    },
    {
        "type": "function",
        "name": "GetWeather",
        "description": "Fetch current weather or hourly forecasts by latitude/longitude.",
        "strict": False,
        "parameters": {
            "type": "object",
            "properties": {
                "latitude": {
                    "type": "number",
                    "description": "Latitude in decimal degrees."
                },
                "longitude": {
                    "type": "number",
                    "description": "Longitude in decimal degrees."
                },
                "times": {
                    "type": "array",
                    "description": "Optional start date/time, then finish date/time for the requested weather window, using the requested area's LOCAL TIMEZONE with format YYYYMMDDTHHMMSS+XX:XX (e.g. 20260501T000000+12:00).",
                    "items": {"type": "string"},
                    "minItems": 2,
                    "maxItems": 2
                }
            },
            "required": ["latitude", "longitude"],
            "additionalProperties": False
        }
    },
    {
        "type": "function",
        "name": "GetCalendarNames",
        "description": "Gets all available calendar names for the authenticated CalDAV account.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False
        }
    }
]


def load_value_file(path: str) -> dict[str, str]:
    data: dict[str, str] = {}
    with open(path, "r", encoding="utf-8") as f:
        for line_no, raw_line in enumerate(f, start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue  # skip blanks/comments

            if ":" not in line:
                raise ValueError(f"Invalid format on line {line_no}: {raw_line!r}")

            key, value = line.split(":", 1)  # split only first colon
            key = key.strip()
            value = value.strip()
            if not key:
                raise ValueError(f"Empty key on line {line_no}")

            data[key] = value
    return data


def _format_location_for_prompt(location_data):
    if not isinstance(location_data, dict):
        return "Location: unavailable"
    latitude = location_data.get("latitude")
    longitude = location_data.get("longitude")
    accuracy = location_data.get("accuracy_m")
    if latitude is None or longitude is None:
        return "Location: unavailable"
    if accuracy is None:
        return f"Known location: lat {latitude}, long {longitude}"
    return f"Known location: lat {latitude}, long {longitude} (accuracy ~{accuracy} m)"


def _coerce_float(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_iso_datetime(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    compact_match = re.fullmatch(r"(\d{8}T\d{6})([+-]\d{2}:\d{2})", text)
    if compact_match:
        dt_part, offset_part = compact_match.groups()
        try:
            return datetime.strptime(f"{dt_part}{offset_part}", "%Y%m%dT%H%M%S%z")
        except ValueError:
            return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def ToolUse(name, args, user_id=None):
    _log_json("TOOL_DEPLOY", {"tool": name, "args": args})

    # Add Calender Event
    if name == 'AddEvent':
        title = args.get("title")
        times = args.get("times") or []
        location = args.get("location", "")
        description = args.get("description", "")
        rrule = args.get("rrule", "")
        reminder_minutes_before = args.get("reminder_minutes_before")
        if not isinstance(times, list) or len(times) != 2:
            return {
                "status": "failed",
                "tool": "AddEvent",
                "event": {"title": title, "times": times},
                "error": "times must be an array with exactly two datetime strings: [start, finish].",
            }
        try:
            output = AddEvent(
                user_id=user_id,
                title=title,
                times=times,
                location=location,
                description=description,
                rrule=rrule,
                reminder_minutes_before=reminder_minutes_before,
            )
            if isinstance(output, dict):
                return {"status": "success", "tool": "AddEvent", "event": {"title": title, "times": times}, "result": output}
            return {"status": "success", "tool": "AddEvent", "event": {"title": title, "times": times}, "result": {"raw": str(output)}}
        except Exception as e:
            return {
                "status": "failed",
                "tool": "AddEvent",
                "event": {"title": title, "times": times},
                "error": str(e),
            }

    # Returns List of Events in Timeframe
    if name == 'GetEvents':
        times = args.get("times") or []
        if not isinstance(times, list) or len(times) != 2:
            return {
                "status": "failed",
                "tool": "GetEvents",
                "times": times,
                "error": "times must be an array with exactly two datetime strings: [start, end].",
            }
        start, end = times
        try:
            output = GetEvents(
                user_id=user_id,
                times=[start, end],
            )
            if isinstance(output, list):
                if output and isinstance(output[0], dict):
                    columns = ["uid", "start", "end", "summary", "location", "description", "rrule", "reminder_minutes_before"]
                    output = [columns] + [[event.get(column) for column in columns] for event in output]
                elif output and isinstance(output[0], list):
                    pass
                else:
                    output = [[], *output]
            return {"status": "success", "tool": "GetEvents", "times": [start, end], "result": output}
        except Exception as e:
            return {
                "status": "failed",
                "tool": "GetEvents",
                "times": [start, end],
                "error": str(e),
            }

    # Deletes Event for UID
    if name == 'DeleteEvent':
        uid = args.get('uid')
        try:
            output = DeleteEvent(
                user_id=user_id,
                uid=uid
            )
            if isinstance(output, dict):
                status = output.get("status", "success")
                if status == "not_found":
                    return {"status": "failed", "tool": "DeleteEvent", "event": {"uid": uid}, "error": "Event not found", "result": output}
                return {"status": "success", "tool": "DeleteEvent", "event": {"uid": uid}, "result": output}
            return {"status": "success", "tool": "DeleteEvent", "event": {"uid": uid}, "result": {"raw": str(output)}}
        except Exception as e:
            return {
                "status": "failed",
                "tool": "DeleteEvent",
                "event": {"uid": uid},
                "error": str(e),
            }

    # Reads saved list by list name
    if name == 'ReadList':
        list_name = args.get("list_name")
        try:
            output = ReadList(user_id=user_id, list_name=list_name)
            status = output.get("status") if isinstance(output, dict) else None
            if status == "not_found":
                return {"status": "failed", "tool": "ReadList", "list": {"list_name": list_name}, "error": "List not found", "result": output}
            if status == "failed":
                return {"status": "failed", "tool": "ReadList", "list": {"list_name": list_name}, "error": output.get("error", "Read failed"), "result": output}
            return {"status": "success", "tool": "ReadList", "list": {"list_name": list_name}, "result": output}
        except Exception as e:
            return {
                "status": "failed",
                "tool": "ReadList",
                "list": {"list_name": list_name},
                "error": str(e),
            }

    # Creates/overwrites saved list by list name
    if name == 'EditList':
        list_name = args.get("list_name")
        content = args.get("content")
        try:
            output = EditList(user_id=user_id, list_name=list_name, content=content)
            status = output.get("status") if isinstance(output, dict) else None
            if status == "failed":
                return {"status": "failed", "tool": "EditList", "list": {"list_name": list_name}, "error": output.get("error", "Edit failed"), "result": output}
            return {"status": "success", "tool": "EditList", "list": {"list_name": list_name}, "result": output}
        except Exception as e:
            return {
                "status": "failed",
                "tool": "EditList",
                "list": {"list_name": list_name},
                "error": str(e),
            }

    # Deletes saved list by list name
    if name == 'DeleteList':
        list_name = args.get("list_name")
        try:
            output = DeleteList(user_id=user_id, list_name=list_name)
            status = output.get("status") if isinstance(output, dict) else None
            if status == "not_found":
                return {"status": "failed", "tool": "DeleteList", "list": {"list_name": list_name}, "error": "List not found", "result": output}
            if status == "failed":
                return {"status": "failed", "tool": "DeleteList", "list": {"list_name": list_name}, "error": output.get("error", "Delete failed"), "result": output}
            return {"status": "success", "tool": "DeleteList", "list": {"list_name": list_name}, "result": output}
        except Exception as e:
            return {
                "status": "failed",
                "tool": "DeleteList",
                "list": {"list_name": list_name},
                "error": str(e),
            }

    # Edits Event by UID (delete + recreate in one tool call)
    if name == 'EditEvent':
        uid = args.get("uid")
        title = args.get("title")
        times = args.get("times")
        location = args.get("location")
        description = args.get("description")
        rrule = args.get("rrule")
        reminder_minutes_before = args["reminder_minutes_before"] if "reminder_minutes_before" in args else _REMINDER_UNCHANGED
        if times is not None and (not isinstance(times, list) or len(times) != 2):
            return {
                "status": "failed",
                "tool": "EditEvent",
                "event": {"uid": uid, "times": times},
                "error": "times must be an array with exactly two datetime strings: [start, finish].",
            }
        try:
            output = EditEvent(
                user_id=user_id,
                uid=uid,
                title=title,
                times=times,
                location=location,
                description=description,
                rrule=rrule,
                reminder_minutes_before=reminder_minutes_before,
            )
            status = output.get("status") if isinstance(output, dict) else None
            if status == "not_found":
                return {"status": "failed", "tool": "EditEvent", "event": {"uid": uid}, "error": "Event not found", "result": output}
            if status == "failed":
                return {"status": "failed", "tool": "EditEvent", "event": {"uid": uid}, "error": output.get("error", "Edit failed"), "result": output}
            return {"status": "success", "tool": "EditEvent", "event": {"uid": uid}, "result": output}
        except Exception as e:
            return {
                "status": "failed",
                "tool": "EditEvent",
                "event": {"uid": uid},
                "error": str(e),
            }

    # Returns current weather for coordinates, or hourly forecast for a requested range
    if name == "GetWeather":
        latitude = _coerce_float(args.get("latitude"))
        longitude = _coerce_float(args.get("longitude"))
        if latitude is None or longitude is None:
            return {
                "status": "failed",
                "tool": "GetWeather",
                "location": {"latitude": args.get("latitude"), "longitude": args.get("longitude")},
                "error": "Latitude and longitude must be numeric values.",
            }
        times_raw = args.get("times")
        if times_raw is not None and (not isinstance(times_raw, list) or len(times_raw) != 2):
            return {
                "status": "failed",
                "tool": "GetWeather",
                "location": {"latitude": latitude, "longitude": longitude},
                "error": "times must be an array with exactly two datetime strings: [start, end].",
            }
        start_time_raw = times_raw[0] if times_raw else None
        end_time_raw = times_raw[1] if times_raw else None
        start_time = _parse_iso_datetime(start_time_raw)
        end_time = _parse_iso_datetime(end_time_raw)
        if (start_time_raw is not None and start_time is None) or (end_time_raw is not None and end_time is None):
            return {
                "status": "failed",
                "tool": "GetWeather",
                "location": {"latitude": latitude, "longitude": longitude},
                "error": "times values must be valid ISO-8601 datetime values with explicit timezone offsets.",
            }
        if start_time and end_time and end_time <= start_time:
            return {
                "status": "failed",
                "tool": "GetWeather",
                "location": {"latitude": latitude, "longitude": longitude},
                "error": "times[1] must be after times[0].",
            }
        try:
            output = GetWeather(
                latitude=latitude,
                longitude=longitude,
                times=[start_time, end_time] if start_time and end_time else None,
            )
            return {
                "status": "success",
                "tool": "GetWeather",
                "location": {"latitude": latitude, "longitude": longitude},
                "times": [
                    start_time.isoformat() if start_time else None,
                    end_time.isoformat() if end_time else None,
                ],
                "result": output,
            }
        except Exception as e:
            return {
                "status": "failed",
                "tool": "GetWeather",
                "location": {"latitude": latitude, "longitude": longitude},
                "times": [
                    start_time.isoformat() if start_time else None,
                    end_time.isoformat() if end_time else None,
                ],
                "error": str(e),
            }

    # Returns all available calendar names
    if name == "GetCalendarNames":
        try:
            output = GetCalendarNames(user_id=user_id)
            return {
                "status": "success",
                "tool": "GetCalendarNames",
                "result": output,
            }
        except Exception as e:
            return {
                "status": "failed",
                "tool": "GetCalendarNames",
                "error": str(e),
            }

    # Returns Trello list names for one board, selected boards, or all accessible boards
    if name == "GetTrelloLists":
        board_id_input = str(args.get("board_id", "")).strip() or None
        board_id = _resolve_trello_id_for_user(int(user_id), "board", board_id_input) if board_id_input else None
        try:
            output = _get_trello_lists_for_user(int(user_id), board_id=board_id)
            aliased_output = _alias_trello_list_rows_for_user(int(user_id), output)
            return {
                "status": "success",
                "tool": "GetTrelloLists",
                "board_id": _get_trello_alias(int(user_id), "board", board_id) if board_id else None,
                "result": aliased_output,
            }
        except Exception as e:
            return {
                "status": "failed",
                "tool": "GetTrelloLists",
                "board_id": board_id_input,
                "error": str(e),
            }

    # Returns Trello cards for a given list_id
    if name == "GetTrelloCards":
        list_id_input = str(args.get("list_id", "")).strip()
        list_id = _resolve_trello_id_for_user(int(user_id), "list", list_id_input)
        try:
            output = _get_trello_cards_for_user(int(user_id), list_id=list_id)
            aliased_output = _alias_trello_card_rows_for_user(int(user_id), output)
            return {
                "status": "success",
                "tool": "GetTrelloCards",
                "list_id": _get_trello_alias(int(user_id), "list", list_id),
                "result": aliased_output,
            }
        except Exception as e:
            return {
                "status": "failed",
                "tool": "GetTrelloCards",
                "list_id": list_id_input,
                "error": str(e),
            }

    # Edits a Trello card by card_id
    if name == "EditTrelloCard":
        card_id_input = str(args.get("card_id", "")).strip()
        card_id = _resolve_trello_id_for_user(int(user_id), "card", card_id_input)

        def valid_string(value):
            if value is None:
                return None
            value = str(value).strip()
            return value if value else None

        kwargs = {
            "card_id": card_id,
        }

        name_value = valid_string(args.get("name"))
        description_value = args.get("description") if "description" in args else None
        due_value = valid_string(args.get("due"))
        list_id_value = valid_string(args.get("list_id"))

        if name_value is not None:
            kwargs["name"] = name_value

        if description_value is not None:
            kwargs["description"] = str(description_value)

        if due_value is not None:
            kwargs["due"] = due_value

        if list_id_value is not None:
            kwargs["list_id"] = _resolve_trello_id_for_user(int(user_id), "list", list_id_value)

        try:
            output = _edit_trello_card_for_user(
                int(user_id),
                **kwargs,
            )
            return {
                "status": "success",
                "tool": "EditTrelloCard",
                "card_id": _get_trello_alias(int(user_id), "card", str(output.get("card_id", card_id))),
                "result": {
                    **output,
                    "card_id": _get_trello_alias(int(user_id), "card", str(output.get("card_id", card_id))),
                },
            }
            
        except Exception as e:
            return {
                "status": "failed",
                "tool": "EditTrelloCard",
                "card_id": card_id_input,
                "error": str(e),
            }

    # Deletes a Trello card by card_id
    if name == "DeleteTrelloCard":
        card_id_input = str(args.get("card_id", "")).strip()
        card_id = _resolve_trello_id_for_user(int(user_id), "card", card_id_input)
        try:
            output = _delete_trello_card_for_user(int(user_id), card_id=card_id)
            return {
                "status": "success",
                "tool": "DeleteTrelloCard",
                "card_id": _get_trello_alias(int(user_id), "card", card_id),
                "result": {
                    **output,
                    "card_id": _get_trello_alias(int(user_id), "card", str(output.get("card_id", card_id))),
                },
            }
        except Exception as e:
            return {
                "status": "failed",
                "tool": "DeleteTrelloCard",
                "card_id": card_id_input,
                "error": str(e),
            }

    # Deletes (archives) a Trello list by list_id
    if name == "DeleteTrelloList":
        list_id_input = str(args.get("list_id", "")).strip()
        list_id = _resolve_trello_id_for_user(int(user_id), "list", list_id_input)
        try:
            output = _delete_trello_list_for_user(int(user_id), list_id=list_id)
            return {
                "status": "success",
                "tool": "DeleteTrelloList",
                "list_id": _get_trello_alias(int(user_id), "list", list_id),
                "result": {
                    **output,
                    "list_id": _get_trello_alias(int(user_id), "list", str(output.get("list_id", list_id))),
                },
            }
        except Exception as e:
            return {
                "status": "failed",
                "tool": "DeleteTrelloList",
                "list_id": list_id_input,
                "error": str(e),
            }

    # Creates a Trello card in a given list
    if name == "CreateTrelloCard":
        list_id_input = str(args.get("list_id", "")).strip()
        list_id = _resolve_trello_id_for_user(int(user_id), "list", list_id_input)
        name_value = str(args.get("name", "")).strip()
        description_value = args["description"] if "description" in args else None
        due_value = args["due"] if "due" in args else None
        try:
            output = _create_trello_card_for_user(
                int(user_id),
                list_id=list_id,
                name=name_value,
                description=description_value,
                due=due_value,
            )
            return {
                "status": "success",
                "tool": "CreateTrelloCard",
                "list_id": _get_trello_alias(int(user_id), "list", list_id),
                "result": {
                    **output,
                    "card_id": _get_trello_alias(int(user_id), "card", str(output.get("card_id", ""))),
                    "list_id": _get_trello_alias(int(user_id), "list", str(output.get("list_id", list_id))),
                },
            }
        except Exception as e:
            return {
                "status": "failed",
                "tool": "CreateTrelloCard",
                "list_id": list_id_input,
                "error": str(e),
            }

    # Creates a Trello list in a given board
    if name == "CreateTrelloList":
        board_id_input = str(args.get("board_id", "")).strip()
        board_id = _resolve_trello_id_for_user(int(user_id), "board", board_id_input)
        name_value = str(args.get("name", "")).strip()
        try:
            output = _create_trello_list_for_user(
                int(user_id),
                board_id=board_id,
                name=name_value,
            )
            return {
                "status": "success",
                "tool": "CreateTrelloList",
                "board_id": _get_trello_alias(int(user_id), "board", board_id),
                "result": {
                    **output,
                    "list_id": _get_trello_alias(int(user_id), "list", str(output.get("list_id", ""))),
                    "board_id": _get_trello_alias(int(user_id), "board", str(output.get("board_id", board_id))),
                },
            }
        except Exception as e:
            return {
                "status": "failed",
                "tool": "CreateTrelloList",
                "board_id": board_id_input,
                "error": str(e),
            }

    return {
        "status": "failed",
        "tool": name,
        "error": "Unknown tool name",
        "args": args,
    }


def _execute_function_calls_parallel(function_calls, user_id=None):
    if not function_calls:
        return []

    if len(function_calls) == 1:
        call = function_calls[0]
        result = ToolUse(call["name"], call["args"], user_id=user_id)
        return [{
            "type": "function_call_output",
            "call_id": call["call_id"],
            "output": json.dumps(result),
        }]

    outputs_by_call_id = {}
    batch_size = MAX_PARALLEL_TOOL_CALLS
    for i in range(0, len(function_calls), batch_size):
        batch = function_calls[i:i + batch_size]
        with ThreadPoolExecutor(max_workers=len(batch)) as executor:
            future_to_call = {
                executor.submit(ToolUse, call["name"], call["args"], user_id): call
                for call in batch
            }
            for future in as_completed(future_to_call):
                call = future_to_call[future]
                try:
                    outputs_by_call_id[call["call_id"]] = future.result()
                except Exception as e:
                    outputs_by_call_id[call["call_id"]] = {
                        "status": "failed",
                        "tool": call.get("name"),
                        "error": str(e),
                        "event": call.get("args", {}),
                    }

    ordered_outputs = []
    for call in function_calls:
        ordered_outputs.append({
            "type": "function_call_output",
            "call_id": call["call_id"],
            "output": json.dumps(outputs_by_call_id[call["call_id"]]),
        })
    return ordered_outputs


def _truncate_text(value, max_len=400):
    text = str(value)
    if len(text) <= max_len:
        return text
    return text[:max_len] + f"... [truncated {len(text) - max_len} chars]"


def _alias_token(value: str, prefix: str, state: dict) -> str:
    key = str(value or "")
    if not key:
        return key
    existing = state["by_value"].get(key)
    if existing:
        return existing
    state["counter"] += 1
    alias = f"{prefix}{state['counter']}"
    state["by_value"][key] = alias
    return alias


def _alias_model_output_ids(output_items, fc_state, call_state):
    aliased = []
    for item in output_items or []:
        if not isinstance(item, dict):
            aliased.append(item)
            continue
        clone = dict(item)
        raw_id = clone.get("id")
        if raw_id:
            clone["id"] = _alias_token(str(raw_id), "fc", fc_state)
        raw_call_id = clone.get("call_id")
        if raw_call_id:
            clone["call_id"] = _alias_token(str(raw_call_id), "c", call_state)
        aliased.append(clone)
    return aliased


def compact_getevents(output: dict) -> dict:
    cols_map = {
        "uid": "id",
        "start": "s",
        "end": "e",
        "summary": "title",
        "location": "loc",
        "description": "desc",
        "rrule": "rrule",
        "reminder_minutes_before": "rem",
    }

    raw_cols = output["result"][0]
    rows = output["result"][1:]

    compact_cols = [cols_map.get(c, c) for c in raw_cols]

    def compact_value(v):
        return "" if v is None else v

    def compact_time(v):
        if not v:
            return ""
        return datetime.strptime(v[:15], "%Y%m%dT%H%M%S").strftime("%H%M").lstrip("0") or "0"

    events = []
    for row in rows:
        event = []
        for col, value in zip(raw_cols, row):
            if col in {"start", "end"}:
                event.append(compact_time(value))
            else:
                event.append(compact_value(value))
        events.append(event)

    return {
        "times": output.get("times", ["", ""]),
        "cols": compact_cols,
        "events": events,
    }


def compact_deleteevent(output: dict) -> dict:
    return {
        "tool": "DeleteEvent",
        "deleted": output.get("event", {}).get("uid", "")
    }  


def compress_getweather(output: dict) -> dict:
    result = output.get("result", {}) or {}
    forecast = result.get("forecast", {}) or {}

    times = forecast.get("time", []) or []
    temps = forecast.get("Tempc", []) or []
    rain = forecast.get("Precip", []) or []
    wind = forecast.get("Wind_Speed", []) or []
    conds = forecast.get("conditions", []) or []

    def hhmm(t):
        if not t:
            return ""
        return t.split("T")[-1].replace(":", "")[:4]

    def compact_dt(t):
        if not t:
            return ""
        date_time, offset = t[:16], t[16:]
        date_time = date_time.replace("-", "").replace(":", "")
        offset = offset.replace(":", "")
        return date_time + offset

    def minmax(values):
        nums = [v for v in values if isinstance(v, (int, float))]
        if not nums:
            return None
        return {"min": min(nums), "max": max(nums)}

    def all_same_zero(values):
        nums = [v for v in values if isinstance(v, (int, float))]
        return bool(nums) and all(v == 0 for v in nums)

    def compress_conditions(times, conditions):
        if not times or not conditions:
            return []

        out = []
        start = hhmm(times[0])
        prev_time = hhmm(times[0])
        prev_cond = conditions[0]

        for t, cond in zip(times[1:], conditions[1:]):
            cur_time = hhmm(t)

            if cond != prev_cond:
                out.append([start if start == prev_time else f"{start}-{prev_time}", prev_cond])
                start = cur_time
                prev_cond = cond

            prev_time = cur_time

        out.append([start if start == prev_time else f"{start}-{prev_time}", prev_cond])
        return out

    current = result.get("current", {}) or {}

    compressed = {
        "weather": {
            "times": [
                compact_dt((output.get("times") or ["", ""])[0]),
                compact_dt((output.get("times") or ["", ""])[1]),
            ],
            "tz": result.get("timezone", ""),
            "now": [
                hhmm(current.get("time")),
                current.get("Tempc", ""),
                current.get("Precip", ""),
                current.get("Wind_Speed", ""),
                current.get("conditions", ""),
            ],
            "temp": minmax(temps),
            "wind": minmax(wind),
            "conds": compress_conditions(times, conds),
        }
    }

    if all_same_zero(rain):
        compressed["weather"]["rain"] = 0
    else:
        compressed["weather"]["rain"] = minmax(rain)

    compressed["weather"] = {
        k: v for k, v in compressed["weather"].items()
        if v not in ("", None, [], {})
    }

    return compressed


def compress_editevent(value):
    if not isinstance(value, dict):
        return value

    result = value.get("result", {})
    updated_fields = result.get("updated_fields", {})

    return {
        "tool": value.get("tool"),
        "uid": result.get("uid") or value.get("event", {}).get("uid"),
        "updated_fields": [k for k, v in updated_fields.items() if v],
    }

def compress_editlist(value):
    if not isinstance(value, dict):
        return value

    result = value.get("result", {})

    return {
        "tool": value.get("tool"),
        "list_name": result.get("list_name") or value.get("list", {}).get("list_name"),
        "created": result.get("created"),
    }

def compress_deletelist(value):
    if not isinstance(value, dict):
        return value

    result = value.get("result", {})

    return {
        "tool": value.get("tool"),
        "list_name": result.get("list_name") or value.get("list", {}).get("list_name"),
    }

def compress_gettrellocards(value):
    if not isinstance(value, dict):
        return value

    cards = value.get("result", []) or []
    cols = ["card_id", "card_name", "description", "due", "url"]
    rows = []
    for card in cards:
        if not isinstance(card, dict):
            continue
        rows.append([
            card.get("card_id", ""),
            card.get("card_name", ""),
            card.get("description", ""),
            card.get("due", ""),
            card.get("url", ""),
        ])

    return {
        "tool": value.get("tool"),
        "list_id": value.get("list_id", ""),
        "cols": cols,
        "rows": rows,
    }

def _compact_value(value):
    # Need to add Tool Specific Compression here #snap
    if value['tool'] == 'GetEvents':
        x = compact_getevents(value)
        return x

    if value['tool'] == 'DeleteEvent':
        x = compact_deleteevent(value)
        return x
    
    if value['tool'] == 'EditEvent':
        x = compress_editevent(value)
        return x

    if value['tool'] == 'GetWeather':
        x = compress_getweather(value)
        return x
    
    if value['tool'] == 'EditList':
        x = compress_editlist(value)
        return x
    
    if value['tool'] == 'DeleteList':
        x = compress_deletelist(value)
        return x
    
    if value['tool'] == 'GetTrelloCards':
        x = compress_gettrellocards(value)
        return x

    print("Not Compressed: ", value)
    return value


def compress_tool_output(tool_output):
    if not isinstance(tool_output, dict):
        return tool_output

    raw_output = tool_output.get("output")
    try:
        parsed_output = json.loads(raw_output) if isinstance(raw_output, str) else raw_output
    except Exception:
        parsed_output = {"status": "failed", "error": "Invalid tool output JSON"}

    compacted = _compact_value(parsed_output)

    return {
        "type": "function_call_output",
        "call_id": tool_output.get("call_id"),
        "output": json.dumps(compacted, ensure_ascii=False),
    }



def ask_gpt54(user_input, system_prompt, results, previous_response_id=None, user_timezone=None, location_context=None, user_id=None, token_totals=None):
    # Build a fresh OpenAI client for each request.
    client = OpenAI(api_key=api_key)
    selected_model = DEFAULT_ASSISTANT_MODEL
    if user_id is not None:
        row = _get_user_settings(int(user_id))
        if row:
            selected_model = _normalize_assistant_model(row["assistant_model"])

    # Generate both UTC and local timestamps so the model can reason about time-sensitive requests.
    now_utc = datetime.now(timezone.utc)
    now_local = datetime.now().astimezone()
    if user_timezone:
        try:
            # If a user timezone is provided, prefer that explicit timezone for local time context.
            now_local = now_utc.astimezone(ZoneInfo(user_timezone))
        except Exception:
            # Fall back to system-local timezone if timezone parsing fails.
            pass

    # Support both plain-text input and dict-based multimodal input (text + optional image).
    image_data_url = None
    if isinstance(user_input, dict):
        image_data_url = user_input.get("image_data_url")
        raw_prompt = user_input.get("prompt", "")
    else:
        raw_prompt = user_input

    available_lists = get_available_lists(user_id=user_id)
    lists_line = ", ".join(available_lists) if available_lists else "(none)"

    # Prepend time context to every user request before sending it to the model.
    # Removed:         f"Current UTC time: {now_utc.strftime('%Y-%m-%d, %a %H:%M:%S  %z')}\n"
    formatted_request = (
        f"Request: {raw_prompt}\n"
        "##############################\n"
        f"Current Local time: {now_local.strftime('%Y%m%dT%H%M%S%z')}\n"
        f"{_format_location_for_prompt(location_context)}\n"
        f"Available lists: {lists_line}\n"
    )
    #formatted_request = "Request: test"

    user_content = [{"type": "input_text", "text": formatted_request}]
    if image_data_url:
        # Include an image input block when present.
        user_content.append({"type": "input_image", "image_url": image_data_url})

    # First turn: include system prompt and user content to initialize the response thread.
    if previous_response_id is None:
        input_items = [
            {"role": "user", "content": user_content}
        ]
        response = client.responses.create(
            model=selected_model,
            instructions=system_prompt,
            tools=tools,
            input=input_items,
            parallel_tool_calls=True,
        )
    else:
        # Follow-up turns: send function outputs when available, otherwise send the new user turn.
        if results:
            input_items = results
            #instructions = concise_prompt #Disabled Just For Now
            instructions = system_prompt
            print("[CONCISE PROMPT]")

        else:
            input_items = [{"role": "user", "content": user_content}]
            instructions = system_prompt
            print("[FULL SYSTEM PROMPT]")

        # Continue the same model conversation by passing previous_response_id.
        response = client.responses.create(
            previous_response_id=previous_response_id,
            model=selected_model,
            instructions=instructions,
            tools=tools,
            input=input_items,
            parallel_tool_calls=True,
        )
    return response


def run_secretariat(prompt_text, image_data_url=None, previous_response_id=None, user_timezone=None, location_context=None, max_turns=12, status_callback=None, user_id=None, token_totals=None):
    results = []
    state = "RUNNING"
    assistant_message = ""
    current_response_id = previous_response_id
    if not isinstance(token_totals, dict):
        token_totals = {"uncached": 0, "cached": 0}
    action_counter = {}
    function_call_id_alias_state = {"counter": 0, "by_value": {}}
    call_id_alias_state = {"counter": 0, "by_value": {}}
    for turn_idx in range(max_turns):
        if status_callback:
            status_callback("Thinking...")
        _log(f"TURN {turn_idx + 1}/{max_turns}", "")
        user_turn = {"prompt": prompt_text}
        if turn_idx == 0 and image_data_url:
            user_turn["image_data_url"] = image_data_url
        response = ask_gpt54(
            user_turn,
            system_prompt,
            results,
            current_response_id,
            user_timezone=user_timezone,
            location_context=location_context,
            user_id=user_id,
            token_totals=token_totals,
        )
        current_response_id = response.id
        response_data = response.model_dump()
        results = []
        saw_function_call = False
        function_calls = []
        _log("MODEL_OUTPUT", _extract_model_text(response_data.get("output", [])))
        for content in response_data.get("output", []):
            if content.get("type") == "message" and content.get("content"):
                text_payload = content["content"][0].get("text", "")
                try:
                    parsed = json.loads(text_payload)
                    state = parsed.get("state", "RUNNING")
                    assistant_message = parsed.get("message", "")

                except Exception:
                    state = "RUNNING"
                    assistant_message = text_payload

            if content.get("type") == "function_call":
                saw_function_call = True
                function_calls.append({
                    "name": content["name"],
                    "args": json.loads(content["arguments"]),
                    "call_id": content["call_id"],
                })

        # If the model returned only a user-facing message and no tool calls,
        # treat that turn as complete instead of spinning another model turn.
        if not saw_function_call and assistant_message and state == "RUNNING":
            state = "DONE"

        if saw_function_call:
            _log("TOOL_BATCH", f"Executing {len(function_calls)} tool call(s)")
            if status_callback:
                status_callback(_batch_status_label(function_calls))
            tool_outputs = _execute_function_calls_parallel(function_calls, user_id=user_id)
            _accumulate_action_report(action_counter, tool_outputs)
            results.extend(compress_tool_output(tool_outputs))
            continue
        usage = response.usage
        input_tokens = usage.input_tokens
        cached_tokens = usage.input_tokens_details.cached_tokens
        uncached_tokens = input_tokens - cached_tokens
        if token_totals is not None:
            token_totals["rolling_uncached"] = token_totals.get("rolling_uncached", 0) + uncached_tokens
            token_totals["rolling_cached"] = token_totals.get("rolling_cached", 0) + cached_tokens
            token_totals["uncached"] = token_totals.get("uncached", 0) + token_totals["rolling_uncached"]
            token_totals["cached"] = token_totals.get("cached", 0) + token_totals["rolling_cached"]
        print(f"[INPUT UN/C] {uncached_tokens} / {cached_tokens}")
        print(f"[TOTAL UN/C] {token_totals.get('uncached', 0) if token_totals is not None else uncached_tokens} / {token_totals.get('cached', 0) if token_totals is not None else cached_tokens}")

        if state in {"WAITING", "DONE"}:
            _log("TURN_END", f"state={state}")
            if state == "DONE":
                print("")
            assistant_message = (assistant_message or "") + _format_action_report(action_counter)
            return {
                "state": state,
                "message": assistant_message,
                "previous_response_id": current_response_id,
            }

    return {
        "state": state,
        "message": (assistant_message or "Request timed out.") + _format_action_report(action_counter),
        "previous_response_id": current_response_id,
    }


@app.get("/")
def home():
    return render_template("Secretariat.html")


@app.get("/settings")
def settings_page():
    _restore_auth_from_trusted_device()
    if not session.get("user_id"):
        return redirect("/")
    return render_template("settings.html")


@app.get("/templates/styles.css")
def template_styles():
    return send_from_directory("templates", "styles.css")


@app.get("/api/auth/me")
def api_auth_me():
    _restore_auth_from_trusted_device()
    user_id = session.get("user_id")
    email = session.get("email")
    if not user_id or not email:
        return jsonify({"ok": True, "authenticated": False})
    return jsonify({"ok": True, "authenticated": True, "user": {"id": user_id, "email": email}})


@app.post("/api/auth/signup")
def api_auth_signup():
    payload = request.get_json(silent=True) or {}
    email = _normalize_email(payload.get("email", ""))
    password = str(payload.get("password", "")).strip()

    if not _valid_email(email):
        return jsonify({"ok": False, "error": "Username is required."}), 400
    if not password:
        return jsonify({"ok": False, "error": "Password is required."}), 400

    password_hash = generate_password_hash(password)
    created_at = datetime.now(timezone.utc).isoformat()
    try:
        with _db_conn() as conn:
            cursor = conn.execute(
                "INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, ?)",
                (email, password_hash, created_at),
            )
            conn.commit()
            user_id = int(cursor.lastrowid)
    except sqlite3.IntegrityError:
        return jsonify({"ok": False, "error": "An account with that username already exists."}), 409

    session["user_id"] = user_id
    session["email"] = email
    return jsonify({"ok": True, "user": {"id": user_id, "email": email}})


@app.post("/api/auth/signin")
def api_auth_signin():
    payload = request.get_json(silent=True) or {}
    email = _normalize_email(payload.get("email", ""))
    password = str(payload.get("password", ""))
    trust_device = bool(payload.get("trust_device", False))
    device_label = str(payload.get("device_label", "")).strip()

    with _db_conn() as conn:
        row = conn.execute(
            "SELECT id, email, password_hash FROM users WHERE email = ?",
            (email,),
        ).fetchone()

    if not row or not check_password_hash(str(row["password_hash"]), password):
        return jsonify({"ok": False, "error": "Invalid username or password."}), 401

    user_id = int(row["id"])
    user_email = str(row["email"])
    session["user_id"] = user_id
    session["email"] = user_email
    response = jsonify({"ok": True, "user": {"id": user_id, "email": user_email}})

    if trust_device:
        token, expires_at = _issue_trusted_device(user_id, device_label=device_label or None)
        _set_trusted_device_cookie(response, token, expires_at)

    return response


@app.post("/api/auth/signout")
def api_auth_signout():
    _revoke_trusted_device_by_cookie()
    session.clear()
    response = jsonify({"ok": True})
    _clear_trusted_device_cookie(response)
    return response


@app.get("/api/settings/caldav")
def api_settings_caldav_get():
    auth_error = _require_auth()
    if auth_error:
        return auth_error

    user_id = int(session["user_id"])
    row = _get_user_settings(user_id)
    settings_payload = {
        "caldav_url": str(row["caldav_url"] or "").strip() if row else "",
        "caldav_username": str(row["caldav_username"] or "").strip() if row else "",
        "caldav_calendar": str(row["caldav_calendar"] or "").strip() if row else "",
        "caldav_calendars": _parse_caldav_calendar_names(str(row["caldav_calendar"] or "")) if row else [],
        "assistant_model": _normalize_assistant_model(row["assistant_model"] if row else None),
        "has_password": bool(str(row["caldav_password"] or "")) if row else False,
    }
    return jsonify({"ok": True, "settings": settings_payload})


@app.get("/api/settings/caldav/calendars")
def api_settings_caldav_calendars_get():
    auth_error = _require_auth()
    if auth_error:
        return auth_error

    user_id = int(session["user_id"])
    settings = _get_user_caldav_settings(user_id)
    client = DAVClient(
        url=settings["url"],
        username=settings["username"],
        password=settings["password"],
    )
    principal = client.principal()
    calendars = principal.calendars()
    names = sorted(
        {
            str(calendar.get_display_name() or "").strip()
            for calendar in calendars
            if str(calendar.get_display_name() or "").strip()
        },
        key=lambda value: value.lower(),
    )
    selected = _parse_caldav_calendar_names(settings["calendar"])
    return jsonify({"ok": True, "calendars": names, "selected": selected})


@app.get("/api/lists")
def api_lists_get():
    auth_error = _require_auth()
    if auth_error:
        return auth_error

    user_id = int(session["user_id"])
    return jsonify({"ok": True, "lists": get_available_list_entries(user_id)})


@app.post("/api/lists/save")
def api_lists_save():
    auth_error = _require_auth()
    if auth_error:
        return auth_error

    payload = request.get_json(silent=True) or {}
    list_name = str(payload.get("list_name", "")).strip()
    content = str(payload.get("content", ""))

    if not list_name:
        return jsonify({"ok": False, "error": "List name is required."}), 400

    user_id = int(session["user_id"])
    result = EditList(user_id=user_id, list_name=list_name, content=content)
    if not isinstance(result, dict) or str(result.get("status", "")).strip().lower() != "success":
        return jsonify({"ok": False, "error": "Failed to save list."}), 500

    return jsonify({"ok": True, "list": {"list_name": list_name, "content": content}})


@app.post("/api/lists/delete")
def api_lists_delete():
    auth_error = _require_auth()
    if auth_error:
        return auth_error

    payload = request.get_json(silent=True) or {}
    list_name = str(payload.get("list_name", "")).strip()
    if not list_name:
        return jsonify({"ok": False, "error": "List name is required."}), 400

    user_id = int(session["user_id"])
    result = DeleteList(user_id=user_id, list_name=list_name)
    status = str(result.get("status", "")).strip().lower() if isinstance(result, dict) else ""
    if status not in {"success", "deleted"}:
        return jsonify({"ok": False, "error": "Failed to delete list."}), 500

    return jsonify({"ok": True, "list": {"list_name": list_name}})


@app.post("/api/settings/caldav")
def api_settings_caldav_save():
    auth_error = _require_auth()
    if auth_error:
        return auth_error

    payload = request.get_json(silent=True) or {}
    user_id = int(session["user_id"])
    caldav_username = str(payload.get("caldav_username", "")).strip()
    caldav_url = _normalize_caldav_url(str(payload.get("caldav_url", "")).strip(), caldav_username)
    caldav_calendars_payload = payload.get("caldav_calendars")
    if isinstance(caldav_calendars_payload, list):
        caldav_calendar = ", ".join(
            name
            for name in [str(item).strip() for item in caldav_calendars_payload]
            if name
        )
    else:
        caldav_calendar = str(payload.get("caldav_calendar", "")).strip()
    assistant_model = _normalize_assistant_model(payload.get("assistant_model"))
    trello_token = str(payload.get("trello_token", "")).strip()
    trello_boards_payload = payload.get("trello_boards")
    if isinstance(trello_boards_payload, list):
        trello_boards = ", ".join(
            name
            for name in [str(item).strip() for item in trello_boards_payload]
            if name
        )
    else:
        trello_boards = str(payload.get("trello_board", "")).strip()
    trello_board_ids_payload = payload.get("trello_board_ids")
    if isinstance(trello_board_ids_payload, list):
        trello_board_ids = ", ".join(
            board_id
            for board_id in [str(item).strip() for item in trello_board_ids_payload]
            if board_id
        )
    else:
        trello_board_ids = str(payload.get("trello_board_ids", "")).strip()
    if trello_token and trello_board_ids:
        try:
            board_ids_set = {value.lower() for value in _parse_caldav_calendar_names(trello_board_ids)}
            board_names = [
                str(board.get("name", "")).strip()
                for board in _get_trello_boards_for_user(user_id)
                if str(board.get("id", "")).strip().lower() in board_ids_set
            ]
            if board_names:
                trello_boards = ", ".join(board_names)
        except Exception:
            pass
    caldav_password_incoming = payload.get("caldav_password")
    caldav_password_incoming = "" if caldav_password_incoming is None else str(caldav_password_incoming)
    updated_at = _utc_now().isoformat()

    existing = _get_user_settings(user_id)
    caldav_password = caldav_password_incoming if caldav_password_incoming else str(existing["caldav_password"] or "") if existing else ""

    with _db_conn() as conn:
        if existing:
            conn.execute(
                """
                UPDATE user_settings
                SET caldav_url = ?, caldav_username = ?, caldav_password = ?, caldav_calendar = ?, trello_token = ?, trello_boards = ?, trello_board_ids = ?, assistant_model = ?, updated_at = ?
                WHERE user_id = ?
                """,
                (caldav_url, caldav_username, caldav_password, caldav_calendar, trello_token, trello_boards, trello_board_ids, assistant_model, updated_at, user_id),
            )
        else:
            conn.execute(
                """
                INSERT INTO user_settings (user_id, caldav_url, caldav_username, caldav_password, caldav_calendar, trello_token, trello_boards, trello_board_ids, assistant_model, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, caldav_url, caldav_username, caldav_password, caldav_calendar, trello_token, trello_boards, trello_board_ids, assistant_model, updated_at),
            )
        conn.commit()

    return jsonify({
        "ok": True,
        "settings": {
            "caldav_url": caldav_url,
            "caldav_username": caldav_username,
            "caldav_calendar": caldav_calendar,
            "caldav_calendars": _parse_caldav_calendar_names(caldav_calendar),
            "trello_token": trello_token,
            "trello_boards": _parse_caldav_calendar_names(trello_boards),
            "trello_board_ids": _parse_caldav_calendar_names(trello_board_ids),
            "assistant_model": assistant_model,
            "has_password": bool(caldav_password),
        },
    })


@app.get("/api/settings/caldav/full")
def api_settings_caldav_get_full():
    auth_error = _require_auth()
    if auth_error:
        return auth_error

    user_id = int(session["user_id"])
    row = _get_user_settings(user_id)
    if not row:
        return jsonify(
            {
                "ok": True,
                "settings": {
                    "caldav_url": "",
                    "caldav_username": "",
                    "caldav_calendar": "",
                    "caldav_calendars": [],
                    "trello_token": "",
                    "trello_board": "",
                    "trello_boards": [],
                    "trello_board_ids": [],
                    "assistant_model": DEFAULT_ASSISTANT_MODEL,
                    "has_password": False,
                },
            }
        )

    caldav_calendar = str(row["caldav_calendar"] or "")
    trello_boards = str(row["trello_boards"] or "")
    trello_board_ids = str(row["trello_board_ids"] or "")
    assistant_model = _normalize_assistant_model(row["assistant_model"])
    return jsonify(
        {
            "ok": True,
            "settings": {
                "caldav_url": str(row["caldav_url"] or ""),
                "caldav_username": str(row["caldav_username"] or ""),
                "caldav_calendar": caldav_calendar,
                "caldav_calendars": _parse_caldav_calendar_names(caldav_calendar),
                "trello_token": str(row["trello_token"] or ""),
                "trello_board": trello_boards,
                "trello_boards": _parse_caldav_calendar_names(trello_boards),
                "trello_board_ids": _parse_caldav_calendar_names(trello_board_ids),
                "assistant_model": assistant_model,
                "has_password": bool(row["caldav_password"]),
            },
        }
    )


@app.get("/api/settings/trello/boards")
def api_settings_trello_boards():
    auth_error = _require_auth()
    if auth_error:
        return auth_error

    user_id = int(session["user_id"])
    row = _get_user_settings(user_id)
    if not row:
        return jsonify({"ok": False, "error": "Save settings with a Trello token first."}), 400

    trello_token = str(row["trello_token"] or "").strip()
    if not trello_token:
        return jsonify({"ok": False, "error": "Save settings with a Trello token first."}), 400

    try:
        boards = _get_trello_boards_for_user(user_id)
    except Exception:
        return jsonify({"ok": False, "error": "Unable to connect to Trello with the saved token."}), 400

    selected_ids = _parse_caldav_calendar_names(str(row["trello_board_ids"] or ""))
    if not selected_ids:
        selected_names = {name.lower() for name in _parse_caldav_calendar_names(str(row["trello_boards"] or ""))}
        if selected_names:
            selected_ids = [
                str(board.get("id", "")).strip()
                for board in boards
                if str(board.get("name", "")).strip().lower() in selected_names
            ]
    boards_sorted = sorted(boards, key=lambda item: str(item.get("name", "")).lower())
    return jsonify({"ok": True, "boards": boards_sorted, "selected": selected_ids})


@app.post("/api/account/delete")
def api_account_delete():
    auth_error = _require_auth()
    if auth_error:
        return auth_error

    payload = request.get_json(silent=True) or {}
    confirmation_email = _normalize_email(payload.get("email", ""))
    current_email = _normalize_email(session.get("email", ""))
    user_id = int(session["user_id"])

    if not confirmation_email or confirmation_email != current_email:
        return jsonify({"ok": False, "error": "Enter your username exactly to delete this account."}), 400

    with _db_conn() as conn:
        conn.execute("DELETE FROM trusted_devices WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM user_settings WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()

    with session_store_lock:
        stale_session_ids = [
            sid
            for sid, data in session_store.items()
            if int(data.get("user_id", 0) or 0) == user_id
        ]
        for sid in stale_session_ids:
            session_store.pop(sid, None)

    session.clear()
    response = jsonify({"ok": True})
    _clear_trusted_device_cookie(response)
    return response


@app.get("/api/auth/devices")
def api_auth_devices():
    auth_error = _require_auth()
    if auth_error:
        return auth_error

    user_id = int(session["user_id"])
    now_iso = _utc_now().isoformat()
    with _db_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, device_label, user_agent, created_at, expires_at, last_used_at, last_used_ip
            FROM trusted_devices
            WHERE user_id = ?
              AND revoked_at IS NULL
              AND expires_at > ?
            ORDER BY last_used_at DESC
            """,
            (user_id, now_iso),
        ).fetchall()

    devices = [
        {
            "id": int(row["id"]),
            "label": str(row["device_label"] or ""),
            "user_agent": str(row["user_agent"] or ""),
            "created_at": str(row["created_at"]),
            "expires_at": str(row["expires_at"]),
            "last_used_at": str(row["last_used_at"]),
            "last_used_ip": str(row["last_used_ip"] or ""),
        }
        for row in rows
    ]
    return jsonify({"ok": True, "devices": devices})


@app.post("/api/auth/devices/revoke")
def api_auth_revoke_device():
    auth_error = _require_auth()
    if auth_error:
        return auth_error

    payload = request.get_json(silent=True) or {}
    device_id = int(payload.get("device_id", 0))
    if device_id <= 0:
        return jsonify({"ok": False, "error": "Valid device_id is required."}), 400

    user_id = int(session["user_id"])
    now_iso = _utc_now().isoformat()
    with _db_conn() as conn:
        cursor = conn.execute(
            """
            UPDATE trusted_devices
            SET revoked_at = ?
            WHERE id = ?
              AND user_id = ?
              AND revoked_at IS NULL
            """,
            (now_iso, device_id, user_id),
        )
        conn.commit()
        changed = int(cursor.rowcount or 0)

    if changed == 0:
        return jsonify({"ok": False, "error": "Trusted device not found."}), 404
    return jsonify({"ok": True})


@app.post("/api/secretariat")
def api_secretariat():
    auth_error = _require_auth()
    if auth_error:
        return auth_error
    _log("API_SECRETARIAT", "request_received")
    payload = request.get_json(silent=True) or {}
    prompt_text = str(payload.get("prompt", "")).strip()
    _log("USER_INPUT", _truncate_text(prompt_text, 1200))
    image_data_url = payload.get("image_data_url")
    session_id = str(payload.get("session_id", "")).strip() or str(uuid.uuid4())
    user_id = int(session["user_id"])
    now_ts = datetime.now(timezone.utc).timestamp()
    with session_store_lock:
        _prune_sessions(now_ts)
        session_data = session_store.get(session_id, {})
    if session_data.get("user_id") not in (None, user_id):
        session_data = {}
    previous_response_id = session_data.get("previous_response_id")
    token_totals = session_data.get("token_totals")
    if not isinstance(token_totals, dict):
        token_totals = {"uncached": 0, "cached": 0, "rolling_uncached": 0, "rolling_cached": 0}
    token_totals.setdefault("rolling_uncached", 0)
    token_totals.setdefault("rolling_cached", 0)
    payload_timezone = str(payload.get("timezone", "")).strip()
    payload_location = payload.get("location") if isinstance(payload.get("location"), dict) else {}
    payload_latitude = _coerce_float(payload_location.get("latitude"))
    payload_longitude = _coerce_float(payload_location.get("longitude"))
    payload_accuracy = _coerce_float(payload_location.get("accuracy_m"))

    user_timezone = payload_timezone or session_data.get("timezone")
    weather_location = session_data.get("weather_location")
    if payload_latitude is not None and payload_longitude is not None:
        weather_location = {
            "latitude": payload_latitude,
            "longitude": payload_longitude,
            "accuracy_m": payload_accuracy,
        }

    if not prompt_text:
        return jsonify({"ok": False, "error": "Prompt is required."}), 400

    try:
        result = run_secretariat(
            prompt_text,
            image_data_url=image_data_url,
            previous_response_id=previous_response_id,
            location_context=weather_location,
            user_id=user_id,
            token_totals=token_totals,
        )
        with session_store_lock:
            session_store[session_id] = {
                "user_id": user_id,
                "previous_response_id": result.get("previous_response_id"),
                "timezone": user_timezone,
                "weather_location": weather_location,
                "token_totals": token_totals,
                "last_seen_ts": now_ts,
            }
        _log_json("API_SECRETARIAT_RESULT", {"session_id": session_id, **result})
        return jsonify({"ok": True, "session_id": session_id, **result})
    except Exception as e:
        _log_json(
            "API_SECRETARIAT_ERROR",
            {
                "error_type": type(e).__name__,
                "error": str(e),
                "traceback": traceback.format_exc(),
            },
        )
        return jsonify({"ok": False, "error": str(e)}), 500


@app.post("/api/secretariat/stream")
def api_secretariat_stream():
    auth_error = _require_auth()
    if auth_error:
        return auth_error
    _log("API_SECRETARIAT_STREAM", "request_received")
    payload = request.get_json(silent=True) or {}
    prompt_text = str(payload.get("prompt", "")).strip()
    _log("USER_INPUT", _truncate_text(prompt_text, 1200))
    image_data_url = payload.get("image_data_url")
    session_id = str(payload.get("session_id", "")).strip() or str(uuid.uuid4())
    user_id = int(session["user_id"])
    now_ts = datetime.now(timezone.utc).timestamp()
    with session_store_lock:
        _prune_sessions(now_ts)
        session_data = session_store.get(session_id, {})
    if session_data.get("user_id") not in (None, user_id):
        session_data = {}
    previous_response_id = session_data.get("previous_response_id")
    token_totals = session_data.get("token_totals")
    if not isinstance(token_totals, dict):
        token_totals = {"uncached": 0, "cached": 0, "rolling_uncached": 0, "rolling_cached": 0}
    token_totals.setdefault("rolling_uncached", 0)
    token_totals.setdefault("rolling_cached", 0)
    payload_timezone = str(payload.get("timezone", "")).strip()
    payload_location = payload.get("location") if isinstance(payload.get("location"), dict) else {}
    payload_latitude = _coerce_float(payload_location.get("latitude"))
    payload_longitude = _coerce_float(payload_location.get("longitude"))
    payload_accuracy = _coerce_float(payload_location.get("accuracy_m"))

    user_timezone = payload_timezone or session_data.get("timezone")
    weather_location = session_data.get("weather_location")
    if payload_latitude is not None and payload_longitude is not None:
        weather_location = {
            "latitude": payload_latitude,
            "longitude": payload_longitude,
            "accuracy_m": payload_accuracy,
        }

    if not prompt_text:
        return jsonify({"ok": False, "error": "Prompt is required."}), 400

    def stream():
        try:
            def emit(payload_obj):
                return json.dumps(payload_obj, ensure_ascii=False) + "\n"

            results = []
            state = "RUNNING"
            assistant_message = ""
            current_response_id = previous_response_id
            max_turns = 12
            action_counter = {}
            function_call_id_alias_state = {"counter": 0, "by_value": {}}
            call_id_alias_state = {"counter": 0, "by_value": {}}

            for turn_idx in range(max_turns):
                _log(f"TURN {turn_idx + 1}/{max_turns}", "")
                yield emit({"type": "status", "label": "Thinking..."})

                user_turn = {"prompt": prompt_text}
                if turn_idx == 0 and image_data_url:
                    user_turn["image_data_url"] = image_data_url
                response = ask_gpt54(
                    user_turn,
                    system_prompt,
                    results,
                    current_response_id,
                    user_timezone=user_timezone,
                    location_context=weather_location,
                    user_id=user_id,
                    token_totals=token_totals,
                )
                current_response_id = response.id
                response_data = response.model_dump()
                results = []
                saw_function_call = False
                function_calls = []
                _log("MODEL_OUTPUT", _extract_model_text(response_data.get("output", [])))

                for content in response_data.get("output", []):
                    if content.get("type") == "message" and content.get("content"):
                        text_payload = content["content"][0].get("text", "")
                        try:
                            parsed = json.loads(text_payload)
                            state = parsed.get("state", "RUNNING")
                            assistant_message = parsed.get("message", "")
                        except Exception:
                            state = "RUNNING"
                            assistant_message = text_payload

                    if content.get("type") == "function_call":
                        saw_function_call = True
                        function_calls.append({
                            "name": content["name"],
                            "args": json.loads(content["arguments"]),
                            "call_id": content["call_id"],
                        })

                # If the model returned only a user-facing message and no tool calls,
                # treat that turn as complete instead of spinning another model turn.
                if not saw_function_call and assistant_message and state == "RUNNING":
                    state = "DONE"

                if saw_function_call:
                    _log("TOOL_BATCH", f"Executing {len(function_calls)} tool call(s)")
                    yield emit({"type": "status", "label": _batch_status_label(function_calls)})
                    tool_outputs = _execute_function_calls_parallel(function_calls, user_id=user_id)
                    _accumulate_action_report(action_counter, tool_outputs)
                    results.extend(compress_tool_output(output) for output in tool_outputs)
                    continue
                usage = response.usage
                input_tokens = usage.input_tokens
                cached_tokens = usage.input_tokens_details.cached_tokens
                uncached_tokens = input_tokens - cached_tokens
                if token_totals is not None:
                    token_totals["rolling_uncached"] = token_totals.get("rolling_uncached", 0) + uncached_tokens
                    token_totals["rolling_cached"] = token_totals.get("rolling_cached", 0) + cached_tokens
                    token_totals["uncached"] = token_totals.get("uncached", 0) + token_totals["rolling_uncached"]
                    token_totals["cached"] = token_totals.get("cached", 0) + token_totals["rolling_cached"]
                print(f"[INPUT UN/C] {uncached_tokens} / {cached_tokens}")
                print(f"[TOTAL UN/C] {token_totals.get('uncached', 0) if token_totals is not None else uncached_tokens} / {token_totals.get('cached', 0) if token_totals is not None else cached_tokens}")

                if state in {"WAITING", "DONE"}:
                    _log("TURN_END", f"state={state}")
                    if state == "DONE":
                        print("")
                    break

            result = {
                "state": state,
                "message": (assistant_message or "Request timed out.") + _format_action_report(action_counter),
                "previous_response_id": current_response_id,
            }
            with session_store_lock:
                session_store[session_id] = {
                    "user_id": user_id,
                    "previous_response_id": result.get("previous_response_id"),
                    "timezone": user_timezone,
                    "weather_location": weather_location,
                    "token_totals": token_totals,
                    "last_seen_ts": now_ts,
                }
            yield emit({"type": "final", "ok": True, "session_id": session_id, **result})
        except Exception as e:
            _log_json(
                "API_SECRETARIAT_STREAM_ERROR",
                {
                    "error_type": type(e).__name__,
                    "error": str(e),
                    "traceback": traceback.format_exc(),
                },
            )
            yield json.dumps({"type": "final", "ok": False, "error": str(e)}, ensure_ascii=False) + "\n"

    return Response(stream_with_context(stream()), mimetype="application/x-ndjson")


@app.post("/api/session/init")
def api_session_init():
    auth_error = _require_auth()
    if auth_error:
        return auth_error
    payload = request.get_json(silent=True) or {}
    session_id = str(payload.get("session_id", "")).strip() or str(uuid.uuid4())
    user_id = int(session["user_id"])
    now_ts = datetime.now(timezone.utc).timestamp()
    timezone_name = str(payload.get("timezone", "")).strip()
    location = payload.get("location") if isinstance(payload.get("location"), dict) else {}
    latitude = _coerce_float(location.get("latitude"))
    longitude = _coerce_float(location.get("longitude"))
    location_accuracy_m = _coerce_float(location.get("accuracy_m"))

    with session_store_lock:
        _prune_sessions(now_ts)
        session_data = session_store.get(
            session_id,
            {"user_id": user_id, "previous_response_id": None, "timezone": None, "weather_location": None, "token_totals": {"uncached": 0, "cached": 0}, "last_seen_ts": now_ts},
        )
        if session_data.get("user_id") not in (None, user_id):
            session_data = {"user_id": user_id, "previous_response_id": None, "timezone": None, "weather_location": None, "token_totals": {"uncached": 0, "cached": 0, "rolling_uncached": 0, "rolling_cached": 0}, "last_seen_ts": now_ts}
        if not isinstance(session_data.get("token_totals"), dict):
            session_data["token_totals"] = {"uncached": 0, "cached": 0, "rolling_uncached": 0, "rolling_cached": 0}
        session_data["token_totals"].setdefault("rolling_uncached", 0)
        session_data["token_totals"].setdefault("rolling_cached", 0)
        if timezone_name:
            session_data["timezone"] = timezone_name
        if latitude is not None and longitude is not None:
            session_data["weather_location"] = {
                "latitude": latitude,
                "longitude": longitude,
                "accuracy_m": location_accuracy_m,
            }
        session_data["last_seen_ts"] = now_ts
        session_data["user_id"] = user_id
        session_store[session_id] = session_data

    return jsonify(
        {
            "ok": True,
            "session_id": session_id,
            "timezone": session_data.get("timezone"),
            "weather_location": session_data.get("weather_location"),
        }
    )


if __name__ == "__main__":
    LISTS_DIR.mkdir(parents=True, exist_ok=True)
    _init_db()
    secret = load_value_file('secrets.txt')
    api_key = secret['api_key']
    app.run(host="0.0.0.0", port=8000, debug=False)

"""
Born In '0.5
R u faster.. maybeee
"""
