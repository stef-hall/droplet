from __future__ import annotations

from datetime import datetime, timezone, timedelta, date
import argparse
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen
import uuid
import re
from threading import Lock
import sqlite3


_get_user_caldav_calendars_fn = None
_lists_dir = Path(__file__).resolve().parent / "lists"
_uid_alias_store: dict[int, dict[str, object]] = {}
_uid_alias_lock = Lock()
_memory_lock = Lock()
_memory_model = None
_memory_collection = None
_memory_db_path = Path(__file__).resolve().parent / "vector_db"
_memory_metadata_db_path = _memory_db_path / "metadata.db"
_memory_collection_name = "memories"


def _get_uid_alias(user_id: int, uid: str) -> str:
    uid = str(uid)
    with _uid_alias_lock:
        state = _uid_alias_store.setdefault(
            int(user_id),
            {"counter": 0, "uid_to_alias": {}, "alias_to_uid": {}},
        )
        uid_to_alias = state["uid_to_alias"]
        alias_to_uid = state["alias_to_uid"]
        existing = uid_to_alias.get(uid)
        if existing:
            return existing
        state["counter"] = int(state["counter"]) + 1
        alias = f"e{state['counter']}"
        uid_to_alias[uid] = alias
        alias_to_uid[alias] = uid
        return alias


def _resolve_uid_for_user(user_id: int, uid_or_alias: str) -> str:
    key = str(uid_or_alias or "").strip()
    if not key:
        return key
    with _uid_alias_lock:
        state = _uid_alias_store.get(int(user_id))
        if not state:
            return key
        alias_to_uid = state.get("alias_to_uid", {})
        return str(alias_to_uid.get(key, key))


def NormalizeEventUidAlias(user_id: int, uid_or_alias: str) -> str:
    resolved_uid = _resolve_uid_for_user(int(user_id), uid_or_alias)
    if not resolved_uid:
        return ""
    return _get_uid_alias(int(user_id), resolved_uid)

def offset_to_z(s):
    if s is None:
        return None, None
    if isinstance(s, date) and not isinstance(s, datetime):
        return s, None
    if isinstance(s, datetime):
        if s.tzinfo is None:
            return s.replace(tzinfo=timezone.utc), "+00:00"
        offset_td = s.utcoffset()
        if offset_td is None:
            return s.astimezone(timezone.utc), "+00:00"
        total_minutes = int(offset_td.total_seconds() // 60)
        sign = "+" if total_minutes >= 0 else "-"
        total_minutes = abs(total_minutes)
        hours = total_minutes // 60
        minutes = total_minutes % 60
        offset = f"{sign}{hours:02d}:{minutes:02d}"
        return s.astimezone(timezone.utc), offset

    if not isinstance(s, str):
        raise ValueError(f"Unsupported datetime value type: {type(s)!r}")

    text = s.strip()
    if re.fullmatch(r"\d{8}", text):
        return datetime.strptime(text, "%Y%m%d").date(), None

    # ICS UTC form: YYYYMMDDTHHMMSSZ
    if re.fullmatch(r"\d{8}T\d{6}Z", text):
        dt = datetime.strptime(text, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        return dt, "+00:00"

    # Compact local-with-offset form: YYYYMMDDTHHMMSS+HH:MM
    if re.fullmatch(r"\d{8}T\d{6}[+-]\d{2}:\d{2}", text):
        iso_text = (
            f"{text[:4]}-{text[4:6]}-{text[6:8]}T"
            f"{text[9:11]}:{text[11:13]}:{text[13:15]}{text[15:]}"
        )
        dt = datetime.fromisoformat(iso_text)
        return dt.astimezone(timezone.utc), text[15:]

    # Generic ISO handling (supports "YYYY-MM-DDTHH:MM:SS+HH:MM" and "...Z").
    iso_text = text[:-1] + "+00:00" if text.endswith("Z") else text
    dt = datetime.fromisoformat(iso_text)
    if dt.tzinfo is None:
        raise ValueError(f"Datetime value must include timezone offset: {s!r}")
    offset_td = dt.utcoffset()
    if offset_td is None:
        offset = "+00:00"
    else:
        total_minutes = int(offset_td.total_seconds() // 60)
        sign = "+" if total_minutes >= 0 else "-"
        total_minutes = abs(total_minutes)
        offset = f"{sign}{total_minutes // 60:02d}:{total_minutes % 60:02d}"
    return dt.astimezone(timezone.utc), offset


def z_to_offset(z, offset):
    if z is None:
        return None
    if isinstance(z, date) and not isinstance(z, datetime):
        return z.strftime("%Y%m%d")
    if isinstance(z, datetime):
        if z.tzinfo is None:
            dt = z.replace(tzinfo=timezone.utc)
        else:
            dt = z.astimezone(timezone.utc)
    else:
        if not isinstance(z, str):
            return str(z)
        text = z.strip()
        if re.fullmatch(r"\d{8}", text):
            return text
        if re.fullmatch(r"\d{8}T\d{6}Z", text):
            dt = datetime.strptime(text, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        elif re.fullmatch(r"\d{8}T\d{6}[+-]\d{2}:\d{2}", text):
            iso_text = (
                f"{text[:4]}-{text[4:6]}-{text[6:8]}T"
                f"{text[9:11]}:{text[11:13]}:{text[13:15]}{text[15:]}"
            )
            dt = datetime.fromisoformat(iso_text).astimezone(timezone.utc)
        else:
            iso_text = text[:-1] + "+00:00" if text.endswith("Z") else text
            try:
                parsed = datetime.fromisoformat(iso_text)
            except ValueError:
                return text
            if parsed.tzinfo is None:
                return text
            dt = parsed.astimezone(timezone.utc)

    if offset is None:
        return dt.strftime("%Y%m%dT%H%M%SZ")

    offset_tz = datetime.fromisoformat("2000-01-01T00:00:00" + offset).tzinfo
    local = dt.astimezone(offset_tz)

    return local.strftime("%Y%m%dT%H%M%S") + offset


def configure_tools(get_user_caldav_calendars, lists_dir: Path | None = None):
    global _get_user_caldav_calendars_fn, _lists_dir
    _get_user_caldav_calendars_fn = get_user_caldav_calendars
    if lists_dir is not None:
        _lists_dir = Path(lists_dir)


def _get_memory_collection():
    global _memory_model, _memory_collection
    if _memory_model is None or _memory_collection is None:
        import chromadb
        from sentence_transformers import SentenceTransformer

        _memory_db_path.mkdir(parents=True, exist_ok=True)
        _memory_model = SentenceTransformer("all-MiniLM-L6-v2")
        client = chromadb.PersistentClient(path=str(_memory_db_path))
        _memory_collection = client.get_or_create_collection(
            name=_memory_collection_name,
            metadata={"hnsw:space": "cosine"},
        )
    return _memory_model, _memory_collection


def _memory_metadata_connection():
    _memory_db_path.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_memory_metadata_db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS metadata (
            id TEXT PRIMARY KEY,
            json TEXT NOT NULL
        )
        """
    )
    columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(metadata)").fetchall()
    }
    if "user_id" not in columns:
        conn.execute("ALTER TABLE metadata ADD COLUMN user_id INTEGER")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_metadata_user_id ON metadata(user_id)")
    return conn


def _normalize_memory_list(value, field_name):
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be an array of strings.")
    return [str(item).strip() for item in value if str(item).strip()]


def _normalize_memory_score(value, field_name):
    score = float(value)
    if score < 0 or score > 1:
        raise ValueError(f"{field_name} must be between 0 and 1.")
    return score


_CANONICAL_MEMORY_TYPES = {
    "trigger": "Trigger",
    "reminder": "Reminder",
    "prefrence": "Prefrence",
    "entities": "Entities",
    "commitments": "Commitments",
}

_MEMORY_TYPE_ALIASES = {
    "triggers": "trigger",
    "reminders": "reminder",
    "preference": "prefrence",
    "preferences": "prefrence",
    "entity": "entities",
    "commitment": "commitments",
}


def _normalize_memory_type(value):
    normalized = str(value or "").strip().lower()
    normalized = _MEMORY_TYPE_ALIASES.get(normalized, normalized)
    if normalized not in _CANONICAL_MEMORY_TYPES:
        allowed = ", ".join(_CANONICAL_MEMORY_TYPES.values())
        raise ValueError(f"type must be one of: {allowed}.")
    return _CANONICAL_MEMORY_TYPES[normalized]


def AddMemory(
    user_id,
    memory_type,
    text,
    entities,
    tags,
    expires_at=None,
    source="assistant_inferred",
):
    text = str(text or "").strip()
    if not text:
        raise ValueError("text is required.")

    safe_user_id = int(user_id)
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    memory = {
        "id": f"mem_{uuid.uuid4().hex}",
        "user_id": safe_user_id,
        "type": _normalize_memory_type(memory_type),
        "text": text,
        "entities": _normalize_memory_list(entities, "entities"),
        "tags": _normalize_memory_list(tags, "tags"),
        "created_at": now,
        "updated_at": now,
        "expires_at": expires_at if expires_at not in ("", None) else None,
        "source": str(source or "assistant_inferred").strip() or "assistant_inferred",
    }

    with _memory_lock:
        model, collection = _get_memory_collection()
        with _memory_metadata_connection() as conn:
            conn.execute(
                """
                INSERT INTO metadata (id, user_id, json)
                VALUES (?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    user_id = excluded.user_id,
                    json = excluded.json
                """,
                (
                    memory["id"],
                    safe_user_id,
                    json.dumps(memory, ensure_ascii=False, sort_keys=True),
                ),
            )

        collection.upsert(
            ids=[memory["id"]],
            documents=[memory["text"]],
            embeddings=[model.encode(memory["text"]).tolist()],
            metadatas=[{"user_id": safe_user_id}],
        )

    return {"status": "stored", "memory": memory}


def _memory_metadata_search(item_id, user_id=None):
    with _memory_metadata_connection() as conn:
        if user_id is None:
            row = conn.execute(
                "SELECT json FROM metadata WHERE id = ?",
                (item_id,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT json FROM metadata WHERE id = ? AND user_id = ?",
                (item_id, int(user_id)),
            ).fetchone()

    if row is None:
        return None

    memory = json.loads(row[0])
    if user_id is not None and int(memory.get("user_id", -1)) != int(user_id):
        return None
    if isinstance(memory, dict):
        memory.pop("importance", None)
        memory.pop("confidence", None)
    return memory


def _memory_is_expired(memory):
    expires_at = memory.get("expires_at") if isinstance(memory, dict) else None
    if not expires_at:
        return False
    try:
        expires_dt = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
    except ValueError:
        return False
    if expires_dt.tzinfo is None:
        expires_dt = expires_dt.astimezone()
    return expires_dt <= datetime.now().astimezone()


def SearchMemories(user_id, query, top_k=5, memory_type=None, type=None):
    query = str(query or "").strip()
    if not query:
        return []

    safe_user_id = int(user_id)
    limit = max(1, min(int(top_k), 20))
    requested_type = type if type is not None else memory_type
    normalized_type = None
    if requested_type is not None and str(requested_type).strip():
        normalized_type = _normalize_memory_type(requested_type)

    with _memory_lock:
        model, collection = _get_memory_collection()
        results = collection.query(
            query_embeddings=[model.encode(query).tolist()],
            n_results=20 if normalized_type else limit,
            where={"user_id": safe_user_id},
        )

        output = []
        ids = (results.get("ids") or [[]])[0]
        distances = (results.get("distances") or [[]])[0]
        for idx, item_id in enumerate(ids):
            memory = _memory_metadata_search(item_id, user_id=safe_user_id)
            if memory is None:
                continue
            if _memory_is_expired(memory):
                continue
            if normalized_type and memory.get("type") != normalized_type:
                continue
            memory["score"] = distances[idx] if idx < len(distances) else None
            output.append(memory)
            if len(output) >= limit:
                break

    return output


def SearchMemory(user_id, query, top_k=5, memory_type=None, type=None):
    return SearchMemories(
        user_id=user_id,
        query=query,
        top_k=top_k,
        memory_type=memory_type,
        type=type,
    )


def DeleteMemory(user_id, memory_id):
    safe_user_id = int(user_id)
    memory_id = str(memory_id or "").strip()
    if not memory_id:
        raise ValueError("memory_id is required.")

    with _memory_lock:
        existing = _memory_metadata_search(memory_id, user_id=safe_user_id)
        if existing is None:
            return {"status": "not_found", "id": memory_id}

        _, collection = _get_memory_collection()
        collection.delete(ids=[memory_id])
        with _memory_metadata_connection() as conn:
            conn.execute(
                "DELETE FROM metadata WHERE id = ? AND user_id = ?",
                (memory_id, safe_user_id),
            )

    return {"status": "deleted", "id": memory_id}


def EditMemory(
    user_id,
    memory_id,
    memory_type=None,
    text=None,
    entities=None,
    tags=None,
    expires_at=None,
    source=None,
):
    safe_user_id = int(user_id)
    memory_id = str(memory_id or "").strip()
    if not memory_id:
        raise ValueError("memory_id is required.")

    with _memory_lock:
        memory = _memory_metadata_search(memory_id, user_id=safe_user_id)
        if memory is None:
            return {"status": "not_found", "id": memory_id}

        if memory_type is not None:
            memory["type"] = _normalize_memory_type(memory_type)
        if text is not None:
            new_text = str(text or "").strip()
            if not new_text:
                raise ValueError("text cannot be empty.")
            memory["text"] = new_text
        if entities is not None:
            memory["entities"] = _normalize_memory_list(entities, "entities")
        if tags is not None:
            memory["tags"] = _normalize_memory_list(tags, "tags")
        memory.pop("importance", None)
        memory.pop("confidence", None)
        if expires_at is not None:
            memory["expires_at"] = expires_at if expires_at != "" else None
        if source is not None:
            memory["source"] = str(source or "assistant_inferred").strip() or "assistant_inferred"

        memory["user_id"] = safe_user_id
        memory["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")

        model, collection = _get_memory_collection()
        with _memory_metadata_connection() as conn:
            conn.execute(
                """
                UPDATE metadata
                SET user_id = ?, json = ?
                WHERE id = ? AND user_id = ?
                """,
                (
                    safe_user_id,
                    json.dumps(memory, ensure_ascii=False, sort_keys=True),
                    memory_id,
                    safe_user_id,
                ),
            )

        collection.upsert(
            ids=[memory["id"]],
            documents=[memory["text"]],
            embeddings=[model.encode(memory["text"]).tolist()],
            metadatas=[{"user_id": safe_user_id}],
        )

    return {"status": "edited", "memory": memory}


def _get_user_caldav_calendars(user_id: int):
    if _get_user_caldav_calendars_fn is None:
        raise RuntimeError("Tools not configured: missing CalDAV calendar provider.")
    return _get_user_caldav_calendars_fn(user_id)


def _user_lists_dir(user_id):
    safe_user = str(int(user_id))
    return _lists_dir / safe_user


WEATHER_CODE_DESCRIPTIONS = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    56: "Light freezing drizzle",
    57: "Dense freezing drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    66: "Light freezing rain",
    67: "Heavy freezing rain",
    71: "Slight snow fall",
    73: "Moderate snow fall",
    75: "Heavy snow fall",
    77: "Snow grains",
    80: "Slight rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    85: "Slight snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail",
}


def _normalize_reminder_minutes(value):
    if value is None or value == "":
        return None
    minutes = int(value)
    if minutes < 0:
        raise ValueError("reminder_minutes_before must be >= 0")
    return minutes


_REMINDER_UNCHANGED = object()


def _extract_reminder_minutes(vevent):
    if not hasattr(vevent, "valarm"):
        return None
    alarms = vevent.valarm
    if not isinstance(alarms, list):
        alarms = [alarms]
    for alarm in alarms:
        trigger = getattr(alarm, "trigger", None)
        trigger_value = getattr(trigger, "value", None)
        if isinstance(trigger_value, timedelta):
            return int(abs(trigger_value.total_seconds()) // 60)
        trigger_text = str(trigger_value or "").strip().upper()
        match = re.fullmatch(r"-PT(\d+)M", trigger_text)
        if match:
            return int(match.group(1))
    return None


def AddEvent(user_id, title, times, location, description, rrule, reminder_minutes_before=None):
    if not isinstance(times, (list, tuple)) or len(times) != 2:
        raise ValueError("times must contain exactly two datetime strings: [start, finish].")
    start, finish = times
    start, offset = offset_to_z(start)
    finish, offset = offset_to_z(finish)
    start = start.strftime("%Y%m%dT%H%M%SZ")
    finish = finish.strftime("%Y%m%dT%H%M%SZ")
    uid = f"{uuid.uuid4()}"

    event_lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"SUMMARY:{title}",
        f"DTSTART:{start}",
        f"DTEND:{finish}",
    ]
    if location:
        event_lines.append(f"LOCATION:{location}")
    if description:
        event_lines.append(f"DESCRIPTION:{description}")
    if rrule:
        event_lines.append(f"RRULE:{rrule}")
    reminder_minutes = _normalize_reminder_minutes(reminder_minutes_before)
    if reminder_minutes is not None:
        event_lines.extend(
            [
                "BEGIN:VALARM",
                f"TRIGGER:-PT{reminder_minutes}M",
                "ACTION:DISPLAY",
                "DESCRIPTION:Reminder",
                "END:VALARM",
            ]
        )

    event_lines.extend(["END:VEVENT", "END:VCALENDAR"])
    event = "\n".join(event_lines)
    calendars = _get_user_caldav_calendars(int(user_id))
    calendar = calendars[0]
    calendar.add_event(event)
    uid_alias = _get_uid_alias(int(user_id), uid)
    return {
        "status": "Complete",
        "uid": uid_alias,
    }


def GetEvents(user_id, times):
    if not isinstance(times, (list, tuple)) or len(times) != 2:
        raise ValueError("times must contain exactly two datetime strings: [start, end].")
    start, end = times
    start, offset = offset_to_z(start)
    end, offset = offset_to_z(end)
    calendars = _get_user_caldav_calendars(int(user_id))
    columns = ["uid", "start", "end", "summary", "location", "description", "rrule", "reminder_minutes_before"]
    rows = []
    for cal in calendars:
        events = cal.date_search(start=start, end=end)
        for event in events:
            try:
                data = event.vobject_instance
                if not data or not hasattr(data, "vevent"):
                    continue

                vevent = data.vevent
                real_uid = str(vevent.uid.value)
                rows.append(
                    [
                        _get_uid_alias(int(user_id), real_uid),
                        str(z_to_offset(vevent.dtstart.value, offset)),
                        str(z_to_offset(vevent.dtend.value, offset)) if hasattr(vevent, "dtend") else None,
                        str(vevent.summary.value) if hasattr(vevent, "summary") else None,
                        str(vevent.location.value) if hasattr(vevent, "location") else None,
                        str(vevent.description.value) if hasattr(vevent, "description") else None,
                        str(vevent.rrule.value) if hasattr(vevent, "rrule") else None,
                        _extract_reminder_minutes(vevent),
                    ]
                )
            except Exception as e:
                print(
                    f"[GetEvents] Skipping malformed event in calendar "
                    f"'{cal.get_display_name()}': {e}",
                    flush=True,
                )
                continue
    return [columns, *rows]

def GetCalendarNames(user_id):
    calendars = _get_user_caldav_calendars(int(user_id))
    names = [str(cal.get_display_name() or "").strip() for cal in calendars]
    return {
        "status": "success",
        "calendar_names": [name for name in names if name],
    }


def DeleteEvent(user_id, uid):
    resolved_uid = _resolve_uid_for_user(int(user_id), uid)
    calendars = _get_user_caldav_calendars(int(user_id))
    for cal in calendars:
        for event in cal.events():
            data = event.vobject_instance
            if data and hasattr(data, "vevent"):
                if str(data.vevent.uid.value) == resolved_uid:
                    event.delete()
                    return {"status": "deleted"}
    return {"status": "not_found"}


def ReadCalendar(user_id, action, times=None):
    action = str(action or "").strip().lower()
    if action == "get_events":
        return GetEvents(user_id=user_id, times=times)
    if action == "get_calendar_names":
        return GetCalendarNames(user_id=user_id)
    raise ValueError(f"Unknown ReadCalendar action: {action}")


def ReadList(user_id, list_name):
    safe_name = str(list_name).strip()
    if not safe_name:
        return {"status": "failed", "error": "List name is required."}
    list_path = _user_lists_dir(user_id) / f"{safe_name}.txt"
    if not list_path.exists() or not list_path.is_file():
        return {"status": "not_found", "list_name": safe_name}
    with open(list_path, "r", encoding="utf-8") as f:
        content = f.read()
    return {"status": "success", "list_name": safe_name, "content": content}


def EditList(user_id, list_name, content):
    safe_name = str(list_name).strip()
    if not safe_name:
        return {"status": "failed", "error": "List name is required."}
    user_dir = _user_lists_dir(user_id)
    user_dir.mkdir(parents=True, exist_ok=True)
    list_path = user_dir / f"{safe_name}.txt"
    existed_before = list_path.exists()
    with open(list_path, "w", encoding="utf-8") as f:
        f.write("" if content is None else str(content))
    return {"status": "success", "list_name": safe_name, "created": not existed_before}


def DeleteList(user_id, list_name):
    safe_name = str(list_name).strip()
    if not safe_name:
        return {"status": "failed", "error": "List name is required."}
    list_path = _user_lists_dir(user_id) / f"{safe_name}.txt"
    if not list_path.exists() or not list_path.is_file():
        return {"status": "not_found", "list_name": safe_name}
    list_path.unlink()
    return {"status": "deleted", "list_name": safe_name}


def WriteList(user_id, action, list_name, content=None):
    action = str(action or "").strip().lower()
    if action == "edit":
        return EditList(user_id=user_id, list_name=list_name, content=content)
    if action == "delete":
        return DeleteList(user_id=user_id, list_name=list_name)
    raise ValueError(f"Unknown WriteList action: {action}")


def _to_utc_ics(value):
    if value is None:
        return ""
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt.strftime("%Y%m%dT%H%M%SZ")
    return str(value)


def _build_event_ics(uid, title, start, finish, location="", description="", rrule="", reminder_minutes_before=None):
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"SUMMARY:{title}",
        f"DTSTART:{start}",
        f"DTEND:{finish}",
    ]
    if location:
        lines.append(f"LOCATION:{location}")
    if description:
        lines.append(f"DESCRIPTION:{description}")
    if rrule:
        lines.append(f"RRULE:{rrule}")
    reminder_minutes = _normalize_reminder_minutes(reminder_minutes_before)
    if reminder_minutes is not None:
        lines.extend(
            [
                "BEGIN:VALARM",
                f"TRIGGER:-PT{reminder_minutes}M",
                "ACTION:DISPLAY",
                "DESCRIPTION:Reminder",
                "END:VALARM",
            ]
        )
    lines.extend(["END:VEVENT", "END:VCALENDAR"])
    return "\n".join(lines)


def EditEvent(user_id, uid, title=None, times=None, location=None, description=None, rrule=None, reminder_minutes_before=_REMINDER_UNCHANGED):
    resolved_uid = _resolve_uid_for_user(int(user_id), uid)
    start = finish = None
    if times is not None:
        if not isinstance(times, (list, tuple)) or len(times) != 2:
            raise ValueError("times must contain exactly two datetime strings: [start, finish].")
        start, finish = times
    if start is not None:
        start, _ = offset_to_z(start)
    if finish is not None:
        finish, _ = offset_to_z(finish)

    calendars = _get_user_caldav_calendars(int(user_id))
    for cal in calendars:
        for event in cal.events():
            data = event.vobject_instance
            if not data or not hasattr(data, "vevent"):
                continue
            vevent = data.vevent
            current_uid = str(vevent.uid.value) if hasattr(vevent, "uid") else ""
            if current_uid != resolved_uid:
                continue

            current_title = str(vevent.summary.value) if hasattr(vevent, "summary") else ""
            current_start = getattr(vevent.dtstart, "value", None) if hasattr(vevent, "dtstart") else None
            current_finish = getattr(vevent.dtend, "value", None) if hasattr(vevent, "dtend") else None
            current_location = str(vevent.location.value) if hasattr(vevent, "location") else ""
            current_description = str(vevent.description.value) if hasattr(vevent, "description") else ""
            current_rrule = str(vevent.rrule.value) if hasattr(vevent, "rrule") else ""
            current_reminder_minutes = _extract_reminder_minutes(vevent)

            new_title = title if title is not None else current_title
            new_start = start if start is not None else current_start
            new_finish = finish if finish is not None else current_finish
            new_location = location if location is not None else current_location
            new_description = description if description is not None else current_description
            new_rrule = rrule if rrule is not None else current_rrule
            new_reminder_minutes = current_reminder_minutes if reminder_minutes_before is _REMINDER_UNCHANGED else reminder_minutes_before

            if new_location is None:
                new_location = ""
            if new_description is None:
                new_description = ""
            if new_rrule is None:
                new_rrule = ""

            if not str(new_title).strip():
                return {"status": "failed", "error": "Edited event is missing required field: title."}
            if new_start is None:
                return {"status": "failed", "error": "Edited event is missing required field: start."}
            if new_finish is None:
                return {"status": "failed", "error": "Edited event is missing required field: finish."}

            start_ics = _to_utc_ics(new_start)
            finish_ics = _to_utc_ics(new_finish)
            replacement_ics = _build_event_ics(
                uid=resolved_uid,
                title=str(new_title),
                start=start_ics,
                finish=finish_ics,
                location=str(new_location),
                description=str(new_description),
                rrule=str(new_rrule),
                reminder_minutes_before=new_reminder_minutes,
            )

            try:
                event.data = replacement_ics
                event.save()
            except Exception as save_error:
                message = str(save_error)
                if "Forbidden" in message or "403" in message:
                    return {
                        "status": "failed",
                        "error": "Event is readable but not writable in this calendar (permission denied).",
                        "details": message,
                    }
                raise

            return {
                "status": "edited",
                "uid": _get_uid_alias(int(user_id), resolved_uid),
                "updated_fields": {
                    "title": title is not None,
                    "times": times is not None,
                    "location": location is not None,
                    "description": description is not None,
                    "rrule": rrule is not None,
                    "reminder_minutes_before": reminder_minutes_before is not _REMINDER_UNCHANGED,
                },
            }

    return {"status": "not_found"}


def WriteCalendar(
    user_id,
    action,
    uid=None,
    title=None,
    times=None,
    location=None,
    description=None,
    rrule=None,
    reminder_minutes_before=_REMINDER_UNCHANGED,
):
    action = str(action or "").strip().lower()
    if action == "add_event":
        return AddEvent(
            user_id=user_id,
            title=title,
            times=times,
            location="" if location is None else location,
            description="" if description is None else description,
            rrule="" if rrule is None else rrule,
            reminder_minutes_before=None if reminder_minutes_before is _REMINDER_UNCHANGED else reminder_minutes_before,
        )
    if action == "edit_event":
        return EditEvent(
            user_id=user_id,
            uid=uid,
            title=title,
            times=times,
            location=location,
            description=description,
            rrule=rrule,
            reminder_minutes_before=reminder_minutes_before,
        )
    if action == "delete_event":
        return DeleteEvent(user_id=user_id, uid=uid)
    raise ValueError(f"Unknown WriteCalendar action: {action}")


def GetWeather(latitude, longitude, times=None, field_names=None):
    def _parse_weather_dt(value):
        if value is None:
            return None
        if isinstance(value, datetime):
            if value.tzinfo is None:
                raise ValueError("Datetime values must include an explicit timezone offset.")
            return value
        text = str(value).strip()
        if not text:
            return None
        compact_match = re.fullmatch(r"(\d{8}T\d{6})([+-]\d{2}:\d{2})", text)
        if compact_match:
            dt_part, offset_part = compact_match.groups()
            return datetime.strptime(f"{dt_part}{offset_part}", "%Y%m%dT%H%M%S%z")
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            raise ValueError("Datetime values must include an explicit timezone offset.")
        return parsed

    start_time = end_time = None
    if times is not None:
        if not isinstance(times, (list, tuple)) or len(times) != 2:
            raise ValueError("times must contain exactly two datetime strings: [start, end].")
        start_time = _parse_weather_dt(times[0])
        end_time = _parse_weather_dt(times[1])

    field_names = field_names or {
        "temperature": "Tempc",
        "precipitation": "Precip",
        "wind_speed": "Wind_Speed",
        "conditions": "conditions",
    }

    params = {
        "latitude": latitude,
        "longitude": longitude,
        "current": ",".join(
            [
                "temperature_2m",
                "precipitation",
                "weather_code",
                "wind_speed_10m",
            ]
        ),
        "hourly": ",".join(
            [
                "temperature_2m",
                "precipitation",
                "weather_code",
                "wind_speed_10m",
            ]
        ),
        "timezone": "auto",
    }

    if start_time is not None and end_time is not None:
        params["start_hour"] = start_time.strftime("%Y-%m-%dT%H:00")
        params["end_hour"] = end_time.strftime("%Y-%m-%dT%H:00")

    url = f"https://api.open-meteo.com/v1/forecast?{urlencode(params)}"

    with urlopen(url, timeout=8) as response:
        payload = json.loads(response.read().decode("utf-8"))

    current = payload.get("current", {}) if isinstance(payload, dict) else {}
    weather_code = current.get("weather_code")

    response = {
        "status": "success",
        "latitude": payload.get("latitude", latitude),
        "longitude": payload.get("longitude", longitude),
        "timezone": payload.get("timezone"),
        "timezone_abbreviation": payload.get("timezone_abbreviation"),
        "current": {
            "time": current.get("time"),
            field_names["temperature"]: current.get("temperature_2m"),
            field_names["precipitation"]: current.get("precipitation"),
            field_names["wind_speed"]: current.get("wind_speed_10m"),
            field_names["conditions"]: WEATHER_CODE_DESCRIPTIONS.get(weather_code, "Unknown conditions"),
        },
    }

    if start_time is None or end_time is None:
        return response

    hourly = payload.get("hourly", {}) if isinstance(payload, dict) else {}
    weather_codes = hourly.get("weather_code", [])

    response["requested_range"] = {
        "start_time_utc": start_time.astimezone(timezone.utc).isoformat(),
        "end_time_utc": end_time.astimezone(timezone.utc).isoformat(),
    }

    response["forecast"] = {
        "time": hourly.get("time", []),
        field_names["temperature"]: hourly.get("temperature_2m", []),
        field_names["precipitation"]: hourly.get("precipitation", []),
        field_names["wind_speed"]: hourly.get("wind_speed_10m", []),
        field_names["conditions"]: [
            WEATHER_CODE_DESCRIPTIONS.get(code, "Unknown conditions")
            for code in weather_codes
        ],
    }

    return response


def ReadWeather(latitude, longitude, times=None, field_names=None):
    return GetWeather(
        latitude=latitude,
        longitude=longitude,
        times=times,
        field_names=field_names,
    )



if __name__ == "__main__":
    from server import LISTS_DIR, _get_user_caldav_calendars
    from server import compress_tool_output as compress
    configure_tools(_get_user_caldav_calendars, LISTS_DIR)

    response = SearchMemories(3, "whats my name?")
    print(response)
    quit()
    
    response = GetEvents(3,
    times = ["20260601T000000+12:00","20260608T000000+12:00"]
    )
    print(response)
    quit()
    
    
    x = GetWeather(
        latitude=-45.8742,
        longitude=170.5036,
        times=[
            "20260518T180000+12:00",
            "20260519T060000+12:00"
        ]
    )
    import server
    x = server.compress_getweather(x)
    

    print(x)
    quit()

    response = EditEvent(3,
        uid="f1c794d5-b32b-40ab-992f-d50568b06337",
        times=["20260512T180000+12:00", "20260512T190000+12:00"]
    )
    print(response)
    quit()
    
   
    response = AddEvent(3,
    title="Working AddEvent",
    times=["20260507T142556+12:00", "20260507T143056+12:00"],
    location="",
    description="",
    rrule=""
)
    print(response)
