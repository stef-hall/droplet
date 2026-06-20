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
from tools import AddEvent, GetEvents, GetCalendarNames, DeleteEvent, ReadList, EditList, DeleteList, EditEvent, GetWeather, AddMemory, SearchMemories, EditMemory, DeleteMemory, _REMINDER_UNCHANGED, configure_tools

def _coerce_bool_flag(value, default=False):
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(int(value))
    lowered = str(value).strip().lower()
    if lowered in {"1", "true", "yes", "on", "enabled"}:
        return True
    if lowered in {"0", "false", "no", "off", "disabled", ""}:
        return False
    return bool(default)


RAGenable = 1

global api_key
warnings.simplefilter("ignore", DeprecationWarning)
app = Flask(__name__)
app.secret_key = os.environ.get("SECRETARIAT_APP_SECRET", "replace-me-in-production")
api_key = ""
session_store = {}
session_store_lock = Lock()
_trello_id_alias_lock = Lock()
_trello_id_alias_store: dict[int, dict[str, dict[str, object]]] = {}
_memory_id_alias_lock = Lock()
_memory_id_alias_store: dict[int, dict[str, object]] = {}
SESSION_TTL_SECONDS = 6 * 60 * 60
TRUSTED_DEVICE_COOKIE = "secretariat_trusted_device"
TRUSTED_DEVICE_DAYS = 60
MAX_PARALLEL_TOOL_CALLS = 10
LISTS_DIR = Path(__file__).resolve( ).parent / "lists"
DB_PATH = Path(__file__).resolve().parent / "secretariat.db"
DEFAULT_ASSISTANT_MODEL = "gpt-5.4"
ALLOWED_ASSISTANT_MODELS = {"gpt-5.4-mini", "gpt-5.4"}
COMMUNICATION_PROFILE_TYPE = "communication_profile"
DEFAULT_COMMUNICATION_PROFILE = {
    "type": COMMUNICATION_PROFILE_TYPE,
    "verbosity": "concise",
    "tone": "casual_direct",
    "format": "short paragraphs",
    "teaching_style": "analogies_then_examples",
    "challenge_level": "medium",
    "correction_style": "direct",
    "emotional_support": "practical",
    "decision_support": "recommendation",
}
COMMUNICATION_PROFILE_ALLOWED_VALUES = {
    "verbosity": {"concise", "balanced", "detailed"},
    "tone": {"casual_direct", "professional", "blunt", "warm", "energetic"},
    "format": {"short paragraphs", "bullet points", "tables", "examples"},
    "teaching_style": {"analogies", "examples", "definitions", "step_by_step_logic", "analogies_then_examples"},
    "challenge_level": {"agreeable", "medium", "confrontational"},
    "correction_style": {"gentle", "direct", "only_if_important"},
    "emotional_support": {"practical", "reassuring", "motivating", "leave_emotion_out"},
    "decision_support": {"pros_cons", "recommendation", "just_facts"},
}


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
        "readtrello": "Reading Trello...",
        "writetrello": "Updating Trello...",
        "readcalendar": "Reading Calendar...",
        "writecalendar": "Updating Calendar...",
        "writelist": "Updating List...",
        "readweather": "Getting Weather...",
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

        operation = str(payload.get("operation") or tool).strip()

        if operation == "AddEvent":
            counter["events_added"] = counter.get("events_added", 0) + 1
        elif operation == "DeleteEvent":
            deleted = True
            if isinstance(result, dict):
                deleted = str(result.get("status", "")).strip().lower() == "deleted"
            if deleted:
                counter["events_deleted"] = counter.get("events_deleted", 0) + 1
        elif operation == "EditEvent":
            edited = True
            if isinstance(result, dict):
                edited = str(result.get("status", "")).strip().lower() == "edited"
            if edited:
                counter["events_edited"] = counter.get("events_edited", 0) + 1
        elif operation == "EditList":
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
        elif operation == "DeleteList":
            list_deleted = True
            if isinstance(result, dict):
                list_deleted = str(result.get("status", "")).strip().lower() == "deleted"
            if list_deleted:
                counter["lists_deleted"] = counter.get("lists_deleted", 0) + 1
        elif operation == "AddMemory":
            counter["memories_added"] = counter.get("memories_added", 0) + 1
        elif operation == "EditMemory":
            memory_edited = True
            if isinstance(result, dict):
                memory_edited = str(result.get("status", "")).strip().lower() == "edited"
            if memory_edited:
                counter["memories_edited"] = counter.get("memories_edited", 0) + 1
        elif operation == "DeleteMemory":
            memory_deleted = True
            if isinstance(result, dict):
                memory_deleted = str(result.get("status", "")).strip().lower() == "deleted"
            if memory_deleted:
                counter["memories_deleted"] = counter.get("memories_deleted", 0) + 1


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
    if counter.get("memories_added", 0):
        lines.append(f"Memory added +{counter['memories_added']}")
    if counter.get("memories_edited", 0):
        lines.append(f"Memory edited {counter['memories_edited']}")
    if counter.get("memories_deleted", 0):
        lines.append(f"Memory deleted -{counter['memories_deleted']}")
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
                rag_enabled INTEGER,
                assistant_model TEXT,
                communication_profile TEXT,
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
        if "rag_enabled" not in existing_column_names:
            conn.execute("ALTER TABLE user_settings ADD COLUMN rag_enabled INTEGER")
        if "communication_profile" not in existing_column_names:
            conn.execute("ALTER TABLE user_settings ADD COLUMN communication_profile TEXT")
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


def _get_memory_alias(user_id: int, real_id: str) -> str:
    safe_id = str(real_id or "").strip()
    if not safe_id:
        return safe_id
    with _memory_id_alias_lock:
        user_state = _memory_id_alias_store.setdefault(
            int(user_id),
            {"counter": 0, "id_to_alias": {}, "alias_to_id": {}},
        )
        id_to_alias = user_state["id_to_alias"]
        alias_to_id = user_state["alias_to_id"]
        existing = id_to_alias.get(safe_id)
        if existing:
            return str(existing)
        user_state["counter"] = int(user_state["counter"]) + 1
        alias = f"m{user_state['counter']}"
        id_to_alias[safe_id] = alias
        alias_to_id[alias] = safe_id
        return alias


def _resolve_memory_id_for_user(user_id: int, id_or_alias: str) -> str:
    key = str(id_or_alias or "").strip()
    if not key:
        return key
    with _memory_id_alias_lock:
        user_state = _memory_id_alias_store.get(int(user_id), {})
        alias_to_id = user_state.get("alias_to_id", {})
        resolved = alias_to_id.get(key, key)
    return str(resolved)


def _alias_memory_rows_for_user(user_id: int, rows: list[dict]) -> list[dict]:
    aliased = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        clone = dict(row)
        real_id = str(clone.get("mem_ID", clone.get("id", ""))).strip()
        if real_id:
            alias_id = _get_memory_alias(int(user_id), real_id)
            clone["id"] = alias_id
            clone["mem_ID"] = alias_id
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
            SELECT user_id, caldav_url, caldav_username, caldav_password, caldav_calendar, trello_token, trello_boards, trello_board_ids, rag_enabled, assistant_model, communication_profile, updated_at
            FROM user_settings
            WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()


def _rag_enabled_for_user(user_id: int | None) -> bool:
    if user_id is None:
        return bool(RAGenable)
    row = _get_user_settings(int(user_id))
    if not row:
        return bool(RAGenable)
    return _coerce_bool_flag(row["rag_enabled"], default=RAGenable)


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


def _normalize_communication_profile(raw_profile) -> dict | None:
    if raw_profile is None:
        return None
    if isinstance(raw_profile, str):
        raw_profile = raw_profile.strip()
        if not raw_profile:
            return None
        try:
            raw_profile = json.loads(raw_profile)
        except Exception:
            return None
    if not isinstance(raw_profile, dict):
        return None

    profile = {"type": COMMUNICATION_PROFILE_TYPE}
    for key, allowed_values in COMMUNICATION_PROFILE_ALLOWED_VALUES.items():
        value = str(raw_profile.get(key, "")).strip()
        if value not in allowed_values:
            value = DEFAULT_COMMUNICATION_PROFILE[key]
        profile[key] = value
    return profile


def _format_communication_profile_for_prompt(profile: dict | None) -> str:
    profile = _normalize_communication_profile(profile)
    if not profile:
        return ""
    return (
        "Communication profile for this user:\n"
        f"- Verbosity: {profile['verbosity']}\n"
        f"- Tone: {profile['tone']}\n"
        f"- Format: {profile['format']}\n"
        f"- Teaching style: {profile['teaching_style']}\n"
        f"- Challenge level: {profile['challenge_level']}\n"
        f"- Correction style: {profile['correction_style']}\n"
        f"- Emotional support: {profile['emotional_support']}\n"
        f"- Decision support: {profile['decision_support']}\n"
        "Apply these preferences to every response unless the user's current request says otherwise.\n"
    )


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
You are a Personal, Proactive, and Powerful Ai Secretary for your user, your name is Secretariat.
You are an INTJ: analytical, strategic, independent, and future-focused. You think in systems, prefer long-term planning, value logic over impulse, and aim for efficient execution. You communicate directly, challenge weak reasoning, hold high standards, and focus on useful truth, competence, self-improvement, and mastery.


## Rules
- NEVER hallucinate tool requests or outputs
- You operate ONLY in the local timezone.
- Return a user-facing message when finished goal.
- Use City Centre Lat/Long as Co-ords.
- If a request is in objection with a memory, follow it anyway but mention it
- Ignore seconds and round to nearest minute unless seconds EXPLICTLY requested.
- Use FastReplies when apparant to you for possibile next steps to save user time in response.
- Don't mention system Alias/ID's from tool outputs.
"""
system_prompt = concise_prompt + """
## Display/Style
- Preserve current Tone, and Formality.
- You have access to Markdown formatting:
    - headers
    - **bold**, *italics* 
    - bullet lists
    - inline `code`, fenced ```code``` 
    - pipe tables | a | b |
- Display multipile events in a markdown time table 
- If someone calls you 'bud' you have to call them 'bud' back.
- Em Dashes ("—") are FORBIDDEN.
- Use Emojis and abbreviations for the keys while displaying values in pipe table for conveying large sets of data.

## Vague delete/remove/edit requests:
  - Check chat history before asking.
  - If unresolved, use the relevant Get tools.
  - If one match exists, act on it.
  - If multiple matches exist, ask which one.
  - If no match exists, say none was found and ask for detail.

## parallel tool calling
- When multiple retrieval or lookup steps are independent, prefer parallel tool calls to reduce wall-clock time.
- Do not parallelize steps that have prerequisite dependencies or where one result determines the next action.
- After parallel retrieval, pause to synthesize the results before making more calls.
- Prefer selective parallelism: parallelize independent evidence gathering, not speculative or redundant tool use.

## Missing context
- If required context is missing, do NOT guess.
- Prefer the appropriate lookup tool when the missing context is retrievable; ask a minimal clarifying question only when it is not.
- If you must proceed, label assumptions explicitly and choose a reversible action.

## If asked to Redo/Undo/Bring Back/Recreate/Restore
1. look back in your context
2. recreate the event exactly

## When Searching vague times: 
- this week → ...Sunday 23:59
- next week → ...Monday 00:00 - Sunday 23:59
- vague search → 14 days
- Confirm (with a reason) before searching >30 days

## FastReplies
- Never mention FastReplies
- Hidden text must be the user’s intended reply.
- Visible text must fit naturally in the assistant message.
- Any suggested actions, or solutions contained in a clarification questions MUST have FastReplies options.
- FastReplies must be embedded inside `message` only.
- Format each FastReply exactly as: [[send:visible assistant text|hidden user message]]
- e.g. "Did you mean [[send:...X|Yes, X]], or..."

## STRICT VALID RESPONSE FORMAT:
{
    "state": "RUNNING|WAITING|DONE",
    "message": "..."
}

# Memory 
## Rules
- One-time condition → Reminder
- Recurring condition → Trigger
- General reusable behaviour without a condition → Preference
- NEVER classify a one-time future instruction as a Preference.
- Edit an existing memory instead of creating a duplicate when possible. 
- NEVER Delete, Edit, or affect ANY part of a memory that's unrelated to the user's input.
- Do not display tool details when saving a memory unless the user asks.
- Respond naturally after saving, editing, or deleting a memory.
- Normalize relative or vague time expressions using the user’s timezone. eg "Beginning of the week" => "Monday 0900"

## Classify each memory by its primary intent:
### Reminder
- A one-time future notification or action.
- May activate at a specific time or when a condition occurs.

### Trigger
- A recurring conditional automation.
- Performs one or more actions whenever its condition occurs.

### Commitment
- Something the user intends, promises, or is expected to complete.
- Usually has a deadline, planned date, or future importance.

### Entity
- A persistent fact about a person, place, organisation, object, or named concept.
- Includes names, relationships, attributes, aliases, and nicknames.

### Preference
- A reusable preference about style, tone, behaviour, planning, or how the user likes things handled.
- Applies generally and is easily overridden by the current request.
""" 


tools = [
    {
        "type": "function",
        "name": "WriteCalendar",
        "description": "Add, edit, or delete calendar events. Actions: add_event, edit_event, delete_event.",
        "strict": False,
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["add_event", "edit_event", "delete_event"],
                    "description": "Write operation to perform."
                },
                "uid": {
                    "type": "string",
                    "description": "Event UID for edit_event or delete_event."
                },
                "title": {
                    "type": "string",
                    "description": "Event title for add_event, or updated title for edit_event."
                },
                "times": {
                    "type": "array",
                    "description": "Start date/time, then finish date/time in local timezone using format YYYYMMDDTHHMMSS+XX:XX.",
                    "items": {"type": "string"},
                    "minItems": 2,
                    "maxItems": 2
                },
                "location": {
                    "type": "string",
                    "description": "Event location."
                },
                "description": {
                    "type": "string",
                    "description": "Event description."
                },
                "rrule": {
                    "type": "string",
                    "description": "Optional recurrence rule in iCalendar RRULE format."
                },
                "reminder_minutes_before": {
                    "type": ["integer", "null"],
                    "description": "Reminder/alert lead time in minutes before event start, or null for no reminder."
                }
            },
            "required": ["action"],
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
                    "description": "List name without .txt extension."
                }
            },
            "required": ["list_name"],
            "additionalProperties": False
        }
    },
    {
        "type": "function",
        "name": "WriteList",
        "description": "Add, edit, or delete a saved local list. Use action edit to create or overwrite, or delete to remove.",
        "strict": False,
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["edit", "delete"],
                    "description": "Write operation to perform."
                },
                "list_name": {
                    "type": "string",
                    "description": "List name without .txt extension."
                },
                "content": {
                    "type": "string",
                    "description": "Full list content for action edit."
                }
            },
            "required": ["action", "list_name"],
            "additionalProperties": False
        }
    },
    {
        "type": "function",
        "name": "ReadWeather",
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
        "name": "ReadTrello",
        "description": "Read Trello data. Use action get_lists to list Trello lists, or get_cards to list cards in a Trello list.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["get_lists", "get_cards"],
                    "description": "Read operation to perform.",
                },
                "board_id": {
                    "type": "string",
                    "description": "Optional board ID for action get_lists.",
                },
                "list_id": {
                    "type": "string",
                    "description": "Required list ID for action get_cards.",
                },
            },
            "required": ["action"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "WriteTrello",
        "description": "Add, edit, or delete Trello resources. Actions: create_card, create_list, edit_card, delete_card, delete_list.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["create_card", "create_list", "edit_card", "delete_card", "delete_list"],
                    "description": "Write operation to perform.",
                },
                "board_id": {
                    "type": "string",
                    "description": "Board ID for create_list.",
                },
                "list_id": {
                    "type": "string",
                    "description": "List ID for create_card, edit_card move destination, or delete_list.",
                },
                "card_id": {
                    "type": "string",
                    "description": "Card ID for edit_card or delete_card.",
                },
                "name": {
                    "type": "string",
                    "description": "Card/list name for create actions, or new card title for edit_card.",
                },
                "description": {
                    "type": "string",
                    "description": "Card description for create_card or edit_card.",
                },
                "due": {
                    "type": "string",
                    "description": "Card due datetime in ISO-8601; empty string clears it for edit_card.",
                },
            },
            "required": ["action"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "ReadCalendar",
        "description": "Read calendar data. Use action get_events for events in a time range, or get_calendar_names for available calendar names.",
        "strict": False,
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["get_events", "get_calendar_names"],
                    "description": "Read operation to perform."
                },
                "times": {
                    "type": "array",
                    "description": "Required for get_events. Start date/time, then finish date/time in local timezone using format YYYYMMDDTHHMMSS+XX:XX.",
                    "items": {"type": "string"},
                    "minItems": 2,
                    "maxItems": 2
                }
            },
            "required": ["action"],
            "additionalProperties": False
        }
    },
    {
        "type": "function",
        "name": "SearchMemory",
        "description": "Search the user's stored memories when more memory context may help answer or act on the current request.",
        "strict": False,
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Semantic search query for matching memories."
                },
                "top_k": {
                    "type": "integer",
                    "description": "Maximum memories to return, from 1 to 20."
                },
                "type": {
                    "type": "string",
                    "enum": ["Trigger", "Reminder", "Commitment", "Preference", "Entity"],
                    "description": "Optional memory type filter."
                },
                "types": {
                    "type": "array",
                    "description": "Optional memory type range filter.",
                    "items": {
                        "type": "string",
                        "enum": ["Trigger", "Reminder", "Commitment", "Preference", "Entity"]
                    }
                }
            },
            "required": ["query", "top_k"],
            "additionalProperties": False
        }
    },
    {
        "type": "function",
        "name": "AddMemory",
        "description": "Store a durable memory for future conversations. Use when the user asks you to remember something or when a stable item worth retaining is identified.",
        "strict": False,
        "parameters": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["Trigger", "Reminder", "Commitment", "Preference", "Entity"],
                    "description": "Memory category."
                },
                "search_text": {
                    "type": "string",
                    "description": "Text used for semantic search and retrieval."
                },
                "facts": {
                    "type": "object",
                    "description": "Key/value facts tied to this memory.",
                    "additionalProperties": True
                }
            },
            "required": ["type", "search_text", "facts"],
            "additionalProperties": False
        }
    },
    {
        "type": "function",
        "name": "EditMemory",
        "description": "Edit an existing memory by mem_ID. Provide only the fields that should change.",
        "strict": False,
        "parameters": {
            "type": "object",
            "properties": {
                "memory_id": {
                    "type": "string",
                    "description": "Memory ID, such as mem_123."
                },
                "type": {
                    "type": "string",
                    "enum": ["Trigger", "Reminder", "Commitment", "Preference", "Entity"],
                    "description": "Updated memory category."
                },
                "search_text": {
                    "type": "string",
                    "description": "Updated text used for semantic search and retrieval."
                },
                "facts": {
                    "type": "object",
                    "description": "Updated key/value facts tied to this memory.",
                    "additionalProperties": True
                }
            },
            "required": ["memory_id"],
            "additionalProperties": False
        }
    },
    {
        "type": "function",
        "name": "DeleteMemory",
        "description": "Delete an existing memory by mem_ID.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "memory_id": {
                    "type": "string",
                    "description": "Memory ID to delete, such as mem_123."
                }
            },
            "required": ["memory_id"],
            "additionalProperties": False
        }
    },
]

_MEMORY_TOOL_NAMES = {"SearchMemory", "AddMemory", "EditMemory", "DeleteMemory"}


def _active_tools_for_request(rag_enabled: bool):
    if rag_enabled:
        return tools
    return [tool for tool in tools if str(tool.get("name", "")).strip() not in _MEMORY_TOOL_NAMES]


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


def _retag_condensed_tool(output, public_tool_name, operation):
    if isinstance(output, dict):
        output = dict(output)
        output["operation"] = operation
        output["tool"] = public_tool_name
    return output


def ToolUse(name, args, user_id=None, log_tool_deploy=True):
    if log_tool_deploy:
        _log_json("TOOL_DEPLOY", {"tool": name, "args": args})

    if name == "ReadTrello":
        action = str(args.get("action", "")).strip().lower()
        operation_map = {
            "get_lists": "GetTrelloLists",
            "get_cards": "GetTrelloCards",
        }
        operation = operation_map.get(action)
        if not operation:
            return {"status": "failed", "tool": "ReadTrello", "error": f"Unknown ReadTrello action: {action}", "args": args}
        output = ToolUse(operation, args, user_id=user_id)
        return _retag_condensed_tool(output, "ReadTrello", operation)

    if name == "WriteTrello":
        action = str(args.get("action", "")).strip().lower()
        operation_map = {
            "create_card": "CreateTrelloCard",
            "create_list": "CreateTrelloList",
            "edit_card": "EditTrelloCard",
            "delete_card": "DeleteTrelloCard",
            "delete_list": "DeleteTrelloList",
        }
        operation = operation_map.get(action)
        if not operation:
            return {"status": "failed", "tool": "WriteTrello", "error": f"Unknown WriteTrello action: {action}", "args": args}
        output = ToolUse(operation, args, user_id=user_id)
        return _retag_condensed_tool(output, "WriteTrello", operation)

    if name == "ReadCalendar":
        action = str(args.get("action", "")).strip().lower()
        operation_map = {
            "get_events": "GetEvents",
            "get_calendar_names": "GetCalendarNames",
        }
        operation = operation_map.get(action)
        if not operation:
            return {"status": "failed", "tool": "ReadCalendar", "error": f"Unknown ReadCalendar action: {action}", "args": args}
        output = ToolUse(operation, args, user_id=user_id)
        return _retag_condensed_tool(output, "ReadCalendar", operation)

    if name == "WriteCalendar":
        action = str(args.get("action", "")).strip().lower()
        operation_map = {
            "add_event": "AddEvent",
            "edit_event": "EditEvent",
            "delete_event": "DeleteEvent",
        }
        operation = operation_map.get(action)
        if not operation:
            return {"status": "failed", "tool": "WriteCalendar", "error": f"Unknown WriteCalendar action: {action}", "args": args}
        output = ToolUse(operation, args, user_id=user_id, log_tool_deploy=False)
        return _retag_condensed_tool(output, "WriteCalendar", operation)

    if name == "WriteList":
        action = str(args.get("action", "")).strip().lower()
        operation_map = {
            "edit": "EditList",
            "delete": "DeleteList",
        }
        operation = operation_map.get(action)
        if not operation:
            return {"status": "failed", "tool": "WriteList", "error": f"Unknown WriteList action: {action}", "args": args}
        output = ToolUse(operation, args, user_id=user_id)
        return _retag_condensed_tool(output, "WriteList", operation)

    if name == "ReadWeather":
        output = ToolUse("GetWeather", args, user_id=user_id)
        return _retag_condensed_tool(output, "ReadWeather", "GetWeather")

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

    # Stores a durable memory in vector search plus metadata DB
    if name == "AddMemory":
        try:
            output = AddMemory(
                user_id=user_id,
                memory_type=args.get("type"),
                search_text=args.get("search_text"),
                facts=args.get("facts"),
            )
            result_memory = output.get("memory", {}) if isinstance(output, dict) and isinstance(output.get("memory", {}), dict) else {}
            real_id = str(result_memory.get("mem_ID", result_memory.get("id", ""))).strip()
            alias_id = _get_memory_alias(int(user_id), real_id) if real_id else None
            if alias_id and isinstance(output, dict):
                output = dict(output)
                output["memory"] = dict(result_memory)
                output["memory"]["id"] = alias_id
                output["memory"]["mem_ID"] = alias_id
            return {
                "status": "success",
                "tool": "AddMemory",
                "memory": {
                    "id": alias_id,
                    "search_text": args.get("search_text"),
                },
                "result": output,
            }
        except Exception as e:
            return {
                "status": "failed",
                "tool": "AddMemory",
                "memory": {"search_text": args.get("search_text")},
                "error": str(e),
            }

    # Searches this user's durable memories
    if name == "SearchMemory":
        try:
            output = SearchMemories(
                user_id=user_id,
                query=args.get("query"),
                top_k=args.get("top_k", 5),
                memory_type=args.get("type") if "type" in args else None,
                memory_types=args.get("types") if "types" in args else None,
            )
            aliased_output = _alias_memory_rows_for_user(int(user_id), output)
            return {
                "status": "success",
                "tool": "SearchMemory",
                "query": args.get("query"),
                "type": args.get("type") if "type" in args else None,
                "types": args.get("types") if "types" in args else None,
                "result": aliased_output,
            }
        except Exception as e:
            return {
                "status": "failed",
                "tool": "SearchMemory",
                "query": args.get("query"),
                "type": args.get("type") if "type" in args else None,
                "types": args.get("types") if "types" in args else None,
                "error": str(e),
            }

    # Edits this user's durable memory by mem_ID
    if name == "EditMemory":
        memory_id_input = str(args.get("memory_id", "")).strip()
        memory_id = _resolve_memory_id_for_user(int(user_id), memory_id_input)
        try:
            output = EditMemory(
                user_id=user_id,
                memory_id=memory_id,
                memory_type=args.get("type") if "type" in args else None,
                search_text=args.get("search_text") if "search_text" in args else None,
                facts=args.get("facts") if "facts" in args else None,
            )
            status = output.get("status") if isinstance(output, dict) else None
            if status == "not_found":
                return {"status": "failed", "tool": "EditMemory", "memory": {"id": memory_id_input}, "error": "Memory not found", "result": output}
            result_memory = output.get("memory", {}) if isinstance(output, dict) and isinstance(output.get("memory", {}), dict) else {}
            real_id = str(result_memory.get("mem_ID", result_memory.get("id", memory_id))).strip()
            alias_id = _get_memory_alias(int(user_id), real_id) if real_id else memory_id_input
            if isinstance(output, dict):
                output = dict(output)
                if isinstance(result_memory, dict):
                    output["memory"] = dict(result_memory)
                    output["memory"]["id"] = alias_id
                    output["memory"]["mem_ID"] = alias_id
            return {"status": "success", "tool": "EditMemory", "memory": {"id": alias_id}, "result": output}
        except Exception as e:
            return {
                "status": "failed",
                "tool": "EditMemory",
                "memory": {"id": memory_id_input},
                "error": str(e),
            }

    # Deletes this user's durable memory by mem_ID
    if name == "DeleteMemory":
        memory_id_input = str(args.get("memory_id", "")).strip()
        memory_id = _resolve_memory_id_for_user(int(user_id), memory_id_input)
        try:
            output = DeleteMemory(user_id=user_id, memory_id=memory_id)
            status = output.get("status") if isinstance(output, dict) else None
            if status == "not_found":
                return {"status": "failed", "tool": "DeleteMemory", "memory": {"id": memory_id_input}, "error": "Memory not found", "result": output}
            real_id = str(output.get("id", memory_id)).strip() if isinstance(output, dict) else memory_id
            alias_id = _get_memory_alias(int(user_id), real_id) if real_id else memory_id_input
            if isinstance(output, dict):
                output = dict(output)
                output["id"] = alias_id
            return {"status": "success", "tool": "DeleteMemory", "memory": {"id": alias_id}, "result": output}
        except Exception as e:
            return {
                "status": "failed",
                "tool": "DeleteMemory",
                "memory": {"id": memory_id_input},
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

        # Keeps date + time so the LLM can tell which day the event is on.
        # Example: 20260613T170000+12:00 -> 0613T1700
        dt = datetime.strptime(v[:15], "%Y%m%dT%H%M%S")
        return dt.strftime("%m%dT%H%M")

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

def compress_addmemory(value):
    if not isinstance(value, dict):
        return value

    result = value.get("result", {}) if isinstance(value.get("result", {}), dict) else {}
    memory = result.get("memory", {}) if isinstance(result.get("memory", {}), dict) else value.get("memory", {})
    memory_id = (
        memory.get("id")
        or memory.get("mem_ID")
        or result.get("id")
        or value.get("memory", {}).get("id")
        or value.get("memory", {}).get("mem_ID")
    )

    cols = ["mem_ID", "status"]
    rows = [[memory_id, value.get("status")]]
    return {
        "tool": value.get("tool"),
        "cols": cols,
        "rows": rows,
    }

def compress_searchmemory(value):
    if isinstance(value, list):
        memories = value
        tool_name = "SearchMemory"
        query_value = None
        type_value = None
        types_value = None
    elif isinstance(value, dict):
        memories = value.get("result", []) if isinstance(value.get("result", []), list) else []
        tool_name = value.get("tool")
        query_value = value.get("query")
        type_value = value.get("type")
        types_value = value.get("types")
    else:
        return value
    cols = ["mem_ID", "type", "search_text", "facts", "score"]
    rows = []
    for memory in memories:
        if not isinstance(memory, dict):
            continue
        memory_id = memory.get("id") or memory.get("mem_ID")
        row_values = {
            "mem_ID": memory_id,
            "type": memory.get("type"),
            "search_text": memory.get("search_text"),
            "facts": memory.get("facts", {}),
            "score": float(str(memory.get("score"))[:7]),
        }
        rows.append([row_values[col] for col in cols])

    return {
        "tool": tool_name,
        "query": query_value,
        "type": type_value,
        "types": types_value if isinstance(types_value, list) else None,
        "cols": cols,
        "rows": rows,
    }


def compress_editmemory(value):
    if not isinstance(value, dict):
        return value

    result = value.get("result", {}) if isinstance(value.get("result", {}), dict) else {}
    memory = result.get("memory", {}) if isinstance(result.get("memory", {}), dict) else value.get("memory", {})
    memory_id = (
        memory.get("id")
        or memory.get("mem_ID")
        or result.get("id")
        or value.get("memory", {}).get("id")
        or value.get("memory", {}).get("mem_ID")
    )

    cols = ["mem_ID", "status"]
    rows = [[memory_id, value.get("status")]]
    return {
        "tool": value.get("tool"),
        "cols": cols,
        "rows": rows,
    }


def compress_deletememory(value):
    if not isinstance(value, dict):
        return value

    result = value.get("result", {}) if isinstance(value.get("result", {}), dict) else {}
    memory_id = (
        result.get("id")
        or result.get("mem_ID")
        or value.get("memory", {}).get("id")
        or value.get("memory", {}).get("mem_ID")
    )

    cols = ["mem_ID", "status"]
    rows = [[memory_id, value.get("status")]]
    return {
        "tool": value.get("tool"),
        "cols": cols,
        "rows": rows,
    }

def _compact_value(value):
    if isinstance(value, list):
        if all(isinstance(item, dict) for item in value):
            memory_like_keys = {"id", "mem_ID", "type", "search_text", "facts", "score"}
            if any(memory_like_keys.intersection(set(item.keys())) for item in value):
                return compress_searchmemory(value)
        return value

    if not isinstance(value, dict):
        return value

    operation = value.get("operation") or value.get("tool")

    if operation == 'GetEvents':
        x = compact_getevents(value)
        return x

    if operation == 'DeleteEvent':
        x = compact_deleteevent(value)
        return x
    
    if operation == 'EditEvent':
        x = compress_editevent(value)
        return x

    if operation == 'GetWeather':
        x = compress_getweather(value)
        return x
    
    if operation == 'EditList':
        x = compress_editlist(value)
        return x
    
    if operation == 'DeleteList':
        x = compress_deletelist(value)
        return x
    
    if operation == 'GetTrelloCards':
        x = compress_gettrellocards(value)
        return x

    if operation == 'AddMemory':
        x = compress_addmemory(value)
        return x

    if operation == 'SearchMemory':
        x = compress_searchmemory(value)
        return x

    if operation == 'EditMemory':
        x = compress_editmemory(value)
        return x

    if operation == 'DeleteMemory':
        x = compress_deletememory(value)
        return x

    print("Not Compressed: ", value)
    return value


def compress_tool_output(tool_output):
    if isinstance(tool_output, list):
        return [compress_tool_output(item) for item in tool_output]

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


def _compile_memories(user_id, query, cols, top_k, types):
    try:
        memories = SearchMemories(user_id=user_id, query=query, top_k=top_k, types=types)
    except Exception as e:
        _log("MEMORY_RAG", f"search failed for types ({types}): {e}")
        return ""
 
    rows = []
    for memory in memories:
        if not isinstance(memory, dict):
            continue

        values = {}
        for data in cols:
            if data == "mem_ID":
                memory_id = memory.get(data, {})
                values["mem_ID"] = _get_memory_alias(int(user_id), memory_id) if memory_id else ""
            else:
                values[data] = memory.get(data)

        rows.append([values[column] for column in cols])

    if not rows:
        return []
    else:
        return rows


def _retrieve_memory_context(user_id, query, top_k=5):
    if user_id is None:
        return ""

    cols = ["mem_ID", "type", "search_text", "facts"]
    relevantInfo = _compile_memories(user_id, query, cols, 8, ['Preference', 'Entity', 'Commitment'])
    Reminders = _compile_memories(user_id, query, cols, 5, ['Reminder'])
    Triggers = _compile_memories(user_id, query, cols, 5, ['Trigger'])

    return json.dumps(
        {"cols": cols, "Memories": relevantInfo, "Reminders": Reminders, "Triggers": Triggers},
        ensure_ascii=False,
        separators=(",\n", ":"),
        default=str
    )


def ask_gpt54(user_input, system_prompt, memory_context, communication_profile_context, results, previous_response_id=None, user_timezone=None, location_context=None, user_id=None, token_totals=None, active_tools=None):
    # Build a fresh OpenAI client for each request.
    client = OpenAI(api_key=api_key)
    request_tools = active_tools if active_tools is not None else tools
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
        "####\n"
        f"Local time: {now_local.strftime('%Y%m%dT%H%M%S%z, %a')}\n"
        f"Available lists: {lists_line}\n"
    )
    #formatted_request = "Request: test"

    user_content = [{"type": "input_text", "text": formatted_request}]
    contextual_inputs = []
    if memory_context:
        contextual_inputs.append(memory_context)
    if communication_profile_context:
        pass
        #contextual_inputs.append(communication_profile_context.strip()) Disabled this for now. Check with me before re instating this
    if contextual_inputs:
        user_content.append(
            {
                "type": "input_text",
                "text": (
                    "Memory context (lower priority than the user's direct request; use only when relevant):\n"
                    + "\n\n".join(contextual_inputs)
                ),
            }
        )
    if image_data_url:
        # Include an image input block when present.
        user_content.append({"type": "input_image", "image_url": image_data_url})

    # First turn: include system prompt and user content to initialize the response thread.
    if previous_response_id is None:
        print("[FULL SYSTEM PROMPT]")
        input_items = []
        input_items.append({"role": "user", "content": user_content})
        response = client.responses.create(
            model=selected_model,
            instructions=system_prompt,
            tools=request_tools,
            input=input_items,
            parallel_tool_calls=True,
        )
    else:
        # Follow-up turns: send function outputs when available, otherwise send the new user turn.
        if results:
            input_items = results
            instructions = concise_prompt
            #instructions = system_prompt
            print("[CONCISE PROMPT]")

        else:
            input_items = []
            input_items.append({"role": "user", "content": user_content})
            instructions = system_prompt
            print("[FULL SYSTEM PROMPT]")

        # Continue the same model conversation by passing previous_response_id.
        response = client.responses.create(
            previous_response_id=previous_response_id,
            model=selected_model,
            instructions=instructions,
            tools=request_tools,
            input=input_items,
            parallel_tool_calls=True,
        )
    return response


def run_secretariat(*args, status_callback=None, **kwargs):
    final = None

    for event in run_secretariat_core(*args, **kwargs):
        if event["type"] == "status" and status_callback:
            status_callback(event["label"])

        if event["type"] == "final":
            final = event

    return {
        "state": final["state"],
        "message": final["message"],
        "previous_response_id": final["previous_response_id"],
    }

def run_secretariat_core(
    prompt_text,image_data_url=None,previous_response_id=None,user_timezone=None,
    location_context=None,max_turns=12,user_id=None,token_totals=None,):
    results = []
    state = "RUNNING"
    assistant_message = ""
    current_response_id = previous_response_id
    action_counter = {}
    rag_enabled = _rag_enabled_for_user(user_id)
    active_tools = _active_tools_for_request(rag_enabled)
    memory_context = _retrieve_memory_context(user_id, prompt_text) if rag_enabled else ""
    communication_profile_context = ""
    if user_id is not None:
        row = _get_user_settings(int(user_id))
        if row:
            communication_profile_context = _format_communication_profile_for_prompt(row["communication_profile"])

    if not isinstance(token_totals, dict):
        token_totals = {
            "uncached": 0,
            "cached": 0,
            "rolling_uncached": 0,
            "rolling_cached": 0,
        }
    token_totals.setdefault("uncached", 0)
    token_totals.setdefault("cached", 0)
    token_totals.setdefault("rolling_uncached", 0)
    token_totals.setdefault("rolling_cached", 0)
    for turn_idx in range(max_turns):
        yield {"type": "status", "label": "Thinking..."}
        _log(f"TURN {turn_idx + 1}/{max_turns}", "")
        user_turn = {"prompt": prompt_text}
        if turn_idx == 0 and image_data_url:
            user_turn["image_data_url"] = image_data_url

        response = ask_gpt54(
            user_turn,
            system_prompt,
            memory_context,
            communication_profile_context,
            results,
            current_response_id,
            user_timezone=user_timezone,
            location_context=location_context,
            user_id=user_id,
            token_totals=token_totals,
            active_tools=active_tools,
        )
        current_response_id = response.id
        response_data = response.model_dump()
        _log("MODEL_OUTPUT", _extract_model_text(response_data.get("output", [])))
        results = []
        saw_function_call = False
        function_calls = []
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

        if not saw_function_call and assistant_message and state == "RUNNING":
            state = "DONE"
        if saw_function_call:
            _log("TOOL_BATCH", f"Executing {len(function_calls)} tool call(s)")
            yield {"type": "status", "label": _batch_status_label(function_calls),}
            tool_outputs = _execute_function_calls_parallel(function_calls,user_id=user_id)
            _accumulate_action_report(action_counter, tool_outputs)
            results.extend(compress_tool_output(tool_outputs))
            continue

        usage = response.usage
        input_tokens = usage.input_tokens
        cached_tokens = usage.input_tokens_details.cached_tokens
        uncached_tokens = input_tokens - cached_tokens
        token_totals["rolling_uncached"] = (token_totals.get("rolling_uncached", 0) + uncached_tokens)
        token_totals["rolling_cached"] = (token_totals.get("rolling_cached", 0) + cached_tokens)
        token_totals["uncached"] = (token_totals.get("uncached", 0) + uncached_tokens)
        token_totals["cached"] = (token_totals.get("cached", 0) + cached_tokens)
        print(f"[INPUT UN/C] {uncached_tokens} / {cached_tokens}")
        print(f"[TOTAL UN/C] {token_totals.get('uncached', 0)} / {token_totals.get('cached', 0)}")
        yield {
            "type": "token_usage",
            "input_uncached": uncached_tokens,
            "input_cached": cached_tokens,
            "rolling_uncached": token_totals.get("rolling_uncached", 0),
            "rolling_cached": token_totals.get("rolling_cached", 0),
            "total_uncached": token_totals.get("uncached", 0),
            "total_cached": token_totals.get("cached", 0),
        }
        if state in {"WAITING", "DONE"}:
            _log("TURN_END", f"state={state}")
            yield {
                "type": "final",
                "state": state,
                "message": (assistant_message or "") + _format_action_report(action_counter),
                "previous_response_id": current_response_id,
                "token_totals": token_totals,
            }
            print("")
            return

    yield {
        "type": "final",
        "state": state,
        "message": (assistant_message or "Request timed out.") + _format_action_report(action_counter),
        "previous_response_id": current_response_id,
        "token_totals": token_totals,
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
        "rag_enabled": _coerce_bool_flag(row["rag_enabled"], default=RAGenable) if row else bool(RAGenable),
        "assistant_model": _normalize_assistant_model(row["assistant_model"] if row else None),
        "has_password": bool(str(row["caldav_password"] or "")) if row else False,
    }
    return jsonify({"ok": True, "settings": settings_payload})


@app.get("/api/communication-profile")
def api_communication_profile_get():
    auth_error = _require_auth()
    if auth_error:
        return auth_error

    user_id = int(session["user_id"])
    row = _get_user_settings(user_id)
    profile = _normalize_communication_profile(row["communication_profile"] if row else None)
    return jsonify({"ok": True, "completed": bool(profile), "profile": profile})


@app.post("/api/communication-profile")
def api_communication_profile_save():
    auth_error = _require_auth()
    if auth_error:
        return auth_error

    payload = request.get_json(silent=True) or {}
    incoming_profile = payload.get("profile", payload)
    profile = _normalize_communication_profile(incoming_profile)
    if not profile:
        return jsonify({"ok": False, "error": "Communication profile is invalid."}), 400

    user_id = int(session["user_id"])
    existing = _get_user_settings(user_id)
    existing_profile = _normalize_communication_profile(existing["communication_profile"] if existing else None)
    if existing_profile:
        return jsonify({"ok": True, "completed": True, "profile": existing_profile})

    updated_at = _utc_now().isoformat()
    profile_json = json.dumps(profile, separators=(",", ":"))

    with _db_conn() as conn:
        if existing:
            conn.execute(
                """
                UPDATE user_settings
                SET communication_profile = ?, updated_at = ?
                WHERE user_id = ?
                """,
                (profile_json, updated_at, user_id),
            )
        else:
            conn.execute(
                """
                INSERT INTO user_settings (user_id, assistant_model, communication_profile, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, DEFAULT_ASSISTANT_MODEL, profile_json, updated_at),
            )
        conn.commit()

    return jsonify({"ok": True, "completed": True, "profile": profile})


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
    rag_enabled = _coerce_bool_flag(payload.get("rag_enabled"), default=RAGenable)
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
                SET caldav_url = ?, caldav_username = ?, caldav_password = ?, caldav_calendar = ?, trello_token = ?, trello_boards = ?, trello_board_ids = ?, rag_enabled = ?, assistant_model = ?, updated_at = ?
                WHERE user_id = ?
                """,
                (caldav_url, caldav_username, caldav_password, caldav_calendar, trello_token, trello_boards, trello_board_ids, int(rag_enabled), assistant_model, updated_at, user_id),
            )
        else:
            conn.execute(
                """
                INSERT INTO user_settings (user_id, caldav_url, caldav_username, caldav_password, caldav_calendar, trello_token, trello_boards, trello_board_ids, rag_enabled, assistant_model, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, caldav_url, caldav_username, caldav_password, caldav_calendar, trello_token, trello_boards, trello_board_ids, int(rag_enabled), assistant_model, updated_at),
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
            "rag_enabled": rag_enabled,
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
                    "rag_enabled": bool(RAGenable),
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
                "rag_enabled": _coerce_bool_flag(row["rag_enabled"], default=RAGenable),
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


@app.post("/api/secretariat/stream")  # snap
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
        token_totals = {
            "uncached": 0,
            "cached": 0,
            "rolling_uncached": 0,
            "rolling_cached": 0,
        }

    token_totals.setdefault("uncached", 0)
    token_totals.setdefault("cached", 0)
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
        final = None

        try:
            def emit(payload_obj):
                return json.dumps(payload_obj, ensure_ascii=False) + "\n"

            for event in run_secretariat_core(
                prompt_text,
                image_data_url=image_data_url,
                previous_response_id=previous_response_id,
                user_timezone=user_timezone,
                location_context=weather_location,
                user_id=user_id,
                token_totals=token_totals,
            ):
                if event.get("type") == "final":
                    final = event

                    yield emit({
                        "type": "final",
                        "ok": True,
                        "session_id": session_id,
                        "state": event.get("state"),
                        "message": event.get("message", ""),
                        "previous_response_id": event.get("previous_response_id"),
                    })
                else:
                    yield emit(event)

            if final is None:
                final = {
                    "previous_response_id": previous_response_id,
                    "token_totals": token_totals,
                }

            with session_store_lock:
                session_store[session_id] = {
                    "user_id": user_id,
                    "previous_response_id": final.get("previous_response_id"),
                    "timezone": user_timezone,
                    "weather_location": weather_location,
                    "token_totals": final.get("token_totals", token_totals),
                    "last_seen_ts": datetime.now(timezone.utc).timestamp(),
                }

        except Exception as e:
            _log_json(
                "API_SECRETARIAT_STREAM_ERROR",
                {
                    "error_type": type(e).__name__,
                    "error": str(e),
                    "traceback": traceback.format_exc(),
                },
            )

            yield json.dumps({
                "type": "final",
                "ok": False,
                "error": str(e),
            }, ensure_ascii=False) + "\n"

    return Response(
        stream_with_context(stream()),
        mimetype="application/x-ndjson",
    )

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
Do you Remeber?
"""
