from __future__ import annotations

import ast
import operator as op
from flask import Flask, jsonify, render_template, request, send_from_directory # type: ignore
from concurrent.futures import ThreadPoolExecutor, as_completed

from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from caldav import DAVClient # type: ignore
from openai import OpenAI # type: ignore
import vobject # type: ignore
import json
import warnings
import base64
import mimetypes
import uuid
import traceback
from urllib.parse import urlencode
from urllib.request import urlopen
from pathlib import Path

global USERNAME, PASSWORD, api_key
warnings.simplefilter("ignore", DeprecationWarning)
app = Flask(__name__)
session_store = {}
MAX_PARALLEL_TOOL_CALLS = 10
LISTS_DIR = Path(__file__).resolve().parent / "lists"


def _log(label, message):
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{stamp}] [{label}] {message}", flush=True)


def _log_json(label, payload):
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    pretty = json.dumps(payload, indent=2, ensure_ascii=False, default=str)
    print(f"[{stamp}] [{label}] {pretty}", flush=True)


def get_available_lists():
    if not LISTS_DIR.exists():
        return []
    return sorted(
        file_path.stem
        for file_path in LISTS_DIR.glob("*.txt")
        if file_path.is_file()
    )


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

system_prompt = """
You are an assistant calender manager with access to tools.

Use a tool whenever it is required to complete the user’s request or when the tool provides the most accurate way to perform the task.

Before calling a tool:
- ensure all required parameters are present
- if any required information is missing, ask the user for it

Rules:
- never guess tool outputs
- only use tools that are provided
- follow tool schemas exactly (no extra fields)
- Keep responses concise and natural. Prefer short plain phrasing over long explanations.
- For clarifying questions, ask only what is necessary in one short sentence whenever possible.
- Avoid filler phrases. When mentioning defaults, do it briefly (example: "What time do you want? I'll default to 1 hour long.")
- When asking for follow up details, be direct with the user. Don't ask "I can help with that I just need the detail duration..." Instead ask "How Long?". Also if multiple details are missing ask for all of them at once.
- prefer tools over free-text when an action/data retrieval is needed
- Take the initative, but offer quick responses to cater or undo your actions if uncertain. 
- always use local timezone for interacting with calender
- interpret the requested event time in the local timezone first to resolve the correct calendar date and time, then convert that resolved local datetime into UTC
- weather context may be provided with each request; use it to improve scheduling suggestions (especially for outdoor activities)
- apply extra reasoning scrutiny around meridians (AM/PM), especially 12:00 times
- treat "noon" as exactly 12:00 PM (12:00 local)
- treat "midnight" as exactly 12:00 AM (00:00 local) and resolve whether it means start-of-day vs next-day from context
- if a requested time could be interpreted as AM or PM, do not guess; ask a clarifying question before calling tools
- before calling tools, perform a final meridian sanity check so daytime requests (e.g. 2 PM) are not converted to overnight equivalents (e.g. 2 AM)
- If no duration is stated; *1 hour* is the default
- After any tool execution, always return a user-facing confirmation message (e.g. “Event added”, “Done”, or a brief status summary), even if no additional information is required
- The "message" field may contain markdown for formatting (e.g. **bold**, *italics*, bullet lists, and `code`)
- For one-tap user replies, use this exact markdown line format: [[send: your suggested user message]]
- Use quick responses in the format: [[send: visible assistant text|hidden user message]] inline text, as obvious follow up's if your not completley comfortable taking action. (e.g. [[send: Want me to add a Run after that too? | Yes, add a Run afterwards]])
- For the quick responses, the text before "|" is what the assistant shows inline, and the text after "|" is the exact user message sends on click. Use these to make sentence to read naturally from the assistant's perspective.
- Always return a state. RUNNING = Operating Tools/Thinking, WAITING = Waiting for User Input, DONE = ONLY when completley finished your task.

- When multiple tool actions are needed, plan them as ordered steps:
  - Emit all independent actions that can run at the same time in the same assistant turn as multiple tool calls.
  - Emit dependent actions in later assistant turns only after prior tool outputs are available.
  - Treat delete-then-add flows as separate sequential turns.

STRICT VALID RESPONSE FORMAT:
{
    "state": "RUNNING|WAITING|DONE",
    "message": "...",
}

"""

tools = [
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
                "start": {
                    "type": "string",
                    "description": "Event start time in UTC time iCalendar format (e.g. 20260502T150000Z)"
                },
                "finish": {
                    "type": "string",
                    "description": "Event end time in UTC time iCalendar format (e.g. 20260502T160000Z)"
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
                }
            },
            "required": ["title", "start", "finish", "location", "description", "rrule"],
            "additionalProperties": False
        }
    },
    {
        "type": "function",
        "name": "GetEvents",
        "description": "Retrieve all calendar events within a given UTC time range (inclusive of start, exclusive of end). Returns events with their UID, start time, end time, and summary.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "start": {
                    "type": "string",
                    "description": "Start of the time range in UTC using format YYYYMMDDTHHMMSSZ (e.g. 20260501T000000Z)"
                },
                "end": {
                    "type": "string",
                    "description": "End of the time range in UTC using format YYYYMMDDTHHMMSSZ (e.g. 20260507T000000Z)"
                }
            },
            "required": ["start", "end"],
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
                "start": {
                    "type": "string",
                    "description": "Updated event start time in UTC time iCalendar format (e.g. 20260502T150000Z). Optional."
                },
                "finish": {
                    "type": "string",
                    "description": "Updated event end time in UTC time iCalendar format (e.g. 20260502T160000Z). Optional."
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
                }
            },
            "required": ["uid"],
            "additionalProperties": False
        }
    },
    {
        "type": "function",
        "name": "GetWeather",
        "description": "Fetch current weather, or hourly forecast data within a requested time range, for a specific latitude and longitude.",
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
                "start_time": {
                    "type": "string",
                    "description": "Optional start of requested weather window in ISO-8601 format (e.g. 2026-05-06T09:00:00+12:00)."
                },
                "end_time": {
                    "type": "string",
                    "description": "Optional end of requested weather window in ISO-8601 format (e.g. 2026-05-06T18:00:00+12:00). Must be after start_time."
                }
            },
            "required": ["latitude", "longitude"],
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


def AddEvent(title, start, finish, location, description, rrule):
    event_lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "BEGIN:VEVENT",
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
    event_lines.extend(["END:VEVENT", "END:VCALENDAR"])
    event = "\n".join(event_lines)
    client = DAVClient(
        url="https://caldav.icloud.com",
        username=USERNAME,
        password=PASSWORD
    )
    principal = client.principal()
    calendars = principal.calendars()
    calendar = calendars[0]  # first iCloud calendar
    calendar.add_event(event)
    return {'status' : 'Complete'}


def GetEvents(start, end):
    def parse_utc_z(ts):
        return datetime.strptime(ts, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)

    start = parse_utc_z(start)
    end = parse_utc_z(end)
    client = DAVClient(
        url="https://caldav.icloud.com",
        username=USERNAME,
        password=PASSWORD
    )
    principal = client.principal()
    calendars = principal.calendars()
    results = []
    for cal in calendars:
        events = cal.date_search(start=start, end=end)
        for event in events:
            data = event.vobject_instance
            if not data or not hasattr(data, "vevent"):
                continue

            vevent = data.vevent
            results.append({
                "uid": str(vevent.uid.value),
                "start": str(vevent.dtstart.value),
                "end": str(vevent.dtend.value) if hasattr(vevent, "dtend") else None,
                "summary": str(vevent.summary.value) if hasattr(vevent, "summary") else None,
                "location": str(vevent.location.value) if hasattr(vevent, "location") else None,
                "description": str(vevent.description.value) if hasattr(vevent, "description") else None,
                "calendar": cal.get_display_name()
            })
    return results


def DeleteEvent(uid):
    client = DAVClient(
        url="https://caldav.icloud.com",
        username=USERNAME,
        password=PASSWORD
    )
    principal = client.principal()
    calendars = principal.calendars()
    for cal in calendars:
        for event in cal.events():
            data = event.vobject_instance
            if data and hasattr(data, "vevent"):
                if str(data.vevent.uid.value) == uid:
                    event.delete()
                    return {"status": "deleted"}
    return {"status": "not_found"}


def ReadList(list_name):
    safe_name = str(list_name).strip()
    if not safe_name:
        return {"status": "failed", "error": "List name is required."}
    list_path = LISTS_DIR / f"{safe_name}.txt"
    if not list_path.exists() or not list_path.is_file():
        return {"status": "not_found", "list_name": safe_name}
    with open(list_path, "r", encoding="utf-8") as f:
        content = f.read()
    return {"status": "success", "list_name": safe_name, "content": content}


def EditList(list_name, content):
    safe_name = str(list_name).strip()
    if not safe_name:
        return {"status": "failed", "error": "List name is required."}
    LISTS_DIR.mkdir(parents=True, exist_ok=True)
    list_path = LISTS_DIR / f"{safe_name}.txt"
    with open(list_path, "w", encoding="utf-8") as f:
        f.write("" if content is None else str(content))
    return {"status": "success", "list_name": safe_name}


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


def _build_event_ics(uid, title, start, finish, location="", description="", rrule=""):
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
    lines.extend(["END:VEVENT", "END:VCALENDAR"])
    return "\n".join(lines)


def EditEvent(uid, title=None, start=None, finish=None, location=None, description=None, rrule=None):
    client = DAVClient(
        url="https://caldav.icloud.com",
        username=USERNAME,
        password=PASSWORD
    )
    principal = client.principal()
    calendars = principal.calendars()
    for cal in calendars:
        for event in cal.events():
            data = event.vobject_instance
            if not data or not hasattr(data, "vevent"):
                continue
            vevent = data.vevent
            current_uid = str(vevent.uid.value) if hasattr(vevent, "uid") else ""
            if current_uid != uid:
                continue

            current_title = str(vevent.summary.value) if hasattr(vevent, "summary") else ""
            current_start = _to_utc_ics(vevent.dtstart.value) if hasattr(vevent, "dtstart") else ""
            current_finish = _to_utc_ics(vevent.dtend.value) if hasattr(vevent, "dtend") else ""
            current_location = str(vevent.location.value) if hasattr(vevent, "location") else ""
            current_description = str(vevent.description.value) if hasattr(vevent, "description") else ""
            current_rrule = str(vevent.rrule.value) if hasattr(vevent, "rrule") else ""

            new_title = title if title is not None else current_title
            new_start = start if start is not None else current_start
            new_finish = finish if finish is not None else current_finish
            new_location = location if location is not None else current_location
            new_description = description if description is not None else current_description
            new_rrule = rrule if rrule is not None else current_rrule

            if not new_title or not new_start or not new_finish:
                return {"status": "failed", "error": "Edited event is missing required fields (title/start/finish)."}

            new_event = _build_event_ics(
                uid=uid,
                title=new_title,
                start=new_start,
                finish=new_finish,
                location=new_location,
                description=new_description,
                rrule=new_rrule,
            )
            event.delete()
            cal.add_event(new_event)
            return {
                "status": "edited",
                "uid": uid,
                "updated_fields": {
                    "title": title is not None,
                    "start": start is not None,
                    "finish": finish is not None,
                    "location": location is not None,
                    "description": description is not None,
                    "rrule": rrule is not None,
                },
            }

    return {"status": "not_found"}


def GetWeather(latitude, longitude, start_time=None, end_time=None, field_names=None):
    field_names = field_names or {
        "temperature": "Tempc",
        "precipitation": "Precip",
        "wind_speed": "Wind_Speed",
        "conditions": "conditions",
    }

    params = {
        "latitude": latitude,
        "longitude": longitude,
        "current": ",".join([
            "temperature_2m",
            "precipitation",
            "weather_code",
            "wind_speed_10m",
        ]),
        "hourly": ",".join([
            "temperature_2m",
            "precipitation",
            "weather_code",
            "wind_speed_10m",
        ]),
        "timezone": "auto",
    }

    if start_time is not None and end_time is not None:
        params["start_hour"] = start_time.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:00")
        params["end_hour"] = end_time.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:00")

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
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def ToolUse(name, args):
    _log_json("TOOL_DEPLOY", {"tool": name, "args": args})

    # Add Calender Event
    if name == 'AddEvent':
        title = args.get("title")
        start = args.get("start")
        finish = args.get("finish")
        location = args.get("location", "")
        description = args.get("description", "")
        rrule = args.get("rrule", "")
        try:
            output = AddEvent(
                title=title,
                start=start,
                finish=finish,
                location=location,
                description=description,
                rrule=rrule
            )
            if isinstance(output, dict):
                return {"status": "success", "tool": "AddEvent", "event": {"title": title, "start": start, "finish": finish}, "result": output}
            return {"status": "success", "tool": "AddEvent", "event": {"title": title, "start": start, "finish": finish}, "result": {"raw": str(output)}}
        except Exception as e:
            return {
                "status": "failed",
                "tool": "AddEvent",
                "event": {"title": title, "start": start, "finish": finish},
                "error": str(e),
            }

    # Returns List of Events in Timeframe
    if name == 'GetEvents':
        start = args.get("start")
        end = args.get("end")
        try:
            output = GetEvents(
                start=start,
                end=end,
            )
            return {"status": "success", "tool": "GetEvents", "range": {"start": start, "end": end}, "result": output}
        except Exception as e:
            return {
                "status": "failed",
                "tool": "GetEvents",
                "range": {"start": start, "end": end},
                "error": str(e),
            }

    # Deletes Event for UID
    if name == 'DeleteEvent':
        uid = args.get('uid')
        try:
            output = DeleteEvent(
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
            output = ReadList(list_name=list_name)
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
            output = EditList(list_name=list_name, content=content)
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

    # Edits Event by UID (delete + recreate in one tool call)
    if name == 'EditEvent':
        uid = args.get("uid")
        title = args.get("title")
        start = args.get("start")
        finish = args.get("finish")
        location = args.get("location")
        description = args.get("description")
        rrule = args.get("rrule")
        try:
            output = EditEvent(
                uid=uid,
                title=title,
                start=start,
                finish=finish,
                location=location,
                description=description,
                rrule=rrule,
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
        start_time_raw = args.get("start_time")
        end_time_raw = args.get("end_time")
        start_time = _parse_iso_datetime(start_time_raw)
        end_time = _parse_iso_datetime(end_time_raw)
        if (start_time_raw is None) != (end_time_raw is None):
            return {
                "status": "failed",
                "tool": "GetWeather",
                "location": {"latitude": latitude, "longitude": longitude},
                "error": "start_time and end_time must be provided together.",
            }
        if (start_time_raw is not None and start_time is None) or (end_time_raw is not None and end_time is None):
            return {
                "status": "failed",
                "tool": "GetWeather",
                "location": {"latitude": latitude, "longitude": longitude},
                "error": "start_time and end_time must be valid ISO-8601 datetime values.",
            }
        if start_time and end_time and end_time <= start_time:
            return {
                "status": "failed",
                "tool": "GetWeather",
                "location": {"latitude": latitude, "longitude": longitude},
                "error": "end_time must be after start_time.",
            }
        try:
            output = GetWeather(
                latitude=latitude,
                longitude=longitude,
                start_time=start_time,
                end_time=end_time,
            )
            return {
                "status": "success",
                "tool": "GetWeather",
                "location": {"latitude": latitude, "longitude": longitude},
                "range": {
                    "start_time": start_time.isoformat() if start_time else None,
                    "end_time": end_time.isoformat() if end_time else None,
                },
                "result": output,
            }
        except Exception as e:
            return {
                "status": "failed",
                "tool": "GetWeather",
                "location": {"latitude": latitude, "longitude": longitude},
                "range": {
                    "start_time": start_time.isoformat() if start_time else None,
                    "end_time": end_time.isoformat() if end_time else None,
                },
                "error": str(e),
            }

    return {
        "status": "failed",
        "tool": name,
        "error": "Unknown tool name",
        "args": args,
    }


def _execute_function_calls_parallel(function_calls):
    if not function_calls:
        return []

    if len(function_calls) == 1:
        call = function_calls[0]
        result = ToolUse(call["name"], call["args"])
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
                executor.submit(ToolUse, call["name"], call["args"]): call
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



def ask_gpt54(user_input, system_prompt, results, previous_response_id=None, user_timezone=None, location_context=None):
    # Build a fresh OpenAI client for each request.
    client = OpenAI(api_key=api_key)

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

    available_lists = get_available_lists()
    lists_line = ", ".join(available_lists) if available_lists else "(none)"

    # Prepend time context to every user request before sending it to the model.
    formatted_request = (
        f"Current UTC time: {now_utc.strftime('%Y-%m-%d, %a %H:%M:%S  %z')}\n"
        f"Current Local time: {now_local.strftime('%Y-%m-%d, %a %H:%M:%S  %z')}\n"
        f"{_format_location_for_prompt(location_context)}\n"
        f"Available lists: {lists_line}\n"
        f"##############################\n"
        f"Request: {raw_prompt}"
    )
    user_content = [{"type": "input_text", "text": formatted_request}]
    if image_data_url:
        # Include an image input block when present.
        user_content.append({"type": "input_image", "image_url": image_data_url})

    # First turn: include system prompt and user content to initialize the response thread.
    if previous_response_id is None:
        input_items = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]
        response = client.responses.create(
            model="gpt-5.4",
            input=input_items,
            tools=tools,
            parallel_tool_calls=True,
        )


    else:
        # Follow-up turns: send function outputs when available, otherwise send the new user turn.
        if results:
            input_items = results

        else:
            input_items = [{"role": "user", "content": user_content}]

        # Continue the same model conversation by passing previous_response_id.
        response = client.responses.create(
            model="gpt-5.4",
            input=input_items,
            tools=tools,
            previous_response_id=previous_response_id,
            parallel_tool_calls=True,
        )

    return response


def run_secretariat(prompt_text, image_data_url=None, previous_response_id=None, user_timezone=None, location_context=None, max_turns=12):
    results = []
    state = "RUNNING"
    assistant_message = ""
    current_response_id = previous_response_id
    for turn_idx in range(max_turns):
        _log("TURN_START", f"{turn_idx + 1}/{max_turns}")
        user_turn = {"prompt": prompt_text, "image_data_url": image_data_url}
        response = ask_gpt54(
            user_turn,
            system_prompt,
            results,
            current_response_id,
            user_timezone=user_timezone,
            location_context=location_context,
        )
        current_response_id = response.id
        response_data = response.model_dump()
        results = []
        saw_function_call = False
        function_calls = []
        _log_json("MODEL_OUTPUT", response_data.get("output", []))
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

        if saw_function_call:
            _log("TOOL_BATCH", f"Executing {len(function_calls)} tool call(s)")
            results.extend(_execute_function_calls_parallel(function_calls))
            continue

        if state in {"WAITING", "DONE"}:
            _log("TURN_END", f"state={state}")
            return {
                "state": state,
                "message": assistant_message,
                "previous_response_id": current_response_id,
            }

    return {
        "state": state,
        "message": assistant_message or "Request timed out.",
        "previous_response_id": current_response_id,
    }


@app.get("/")
def home():
    return render_template("index.html")

@app.get("/templates/styles.css")
def template_styles():
    return send_from_directory("templates", "styles.css")


@app.post("/api/secretariat")
def api_secretariat():
    _log("API_SECRETARIAT", "request_received")
    payload = request.get_json(silent=True) or {}
    prompt_text = str(payload.get("prompt", "")).strip()
    image_data_url = payload.get("image_data_url")
    session_id = str(payload.get("session_id", "")).strip() or str(uuid.uuid4())
    session_data = session_store.get(session_id, {})
    previous_response_id = session_data.get("previous_response_id")
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
            user_timezone=user_timezone,
            location_context=weather_location,
        )
        session_store[session_id] = {
            "previous_response_id": result.get("previous_response_id"),
            "timezone": user_timezone,
            "weather_location": weather_location,
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


@app.post("/api/session/init")
def api_session_init():
    payload = request.get_json(silent=True) or {}
    session_id = str(payload.get("session_id", "")).strip() or str(uuid.uuid4())
    timezone_name = str(payload.get("timezone", "")).strip()
    location = payload.get("location") if isinstance(payload.get("location"), dict) else {}
    latitude = _coerce_float(location.get("latitude"))
    longitude = _coerce_float(location.get("longitude"))
    location_accuracy_m = _coerce_float(location.get("accuracy_m"))

    session_data = session_store.get(
        session_id,
        {"previous_response_id": None, "timezone": None, "weather_location": None},
    )
    if timezone_name:
        session_data["timezone"] = timezone_name
    if latitude is not None and longitude is not None:
        session_data["weather_location"] = {
            "latitude": latitude,
            "longitude": longitude,
            "accuracy_m": location_accuracy_m,
        }
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
    secret = load_value_file('secrets.txt')
    USERNAME = secret['USERNAME']
    PASSWORD =  secret['PASSWORD']
    api_key = secret['api_key']
    app.run(host="127.0.0.1", port=8000, debug=False)

"""
Eating a sammy
"""

