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

global USERNAME, PASSWORD, api_key
warnings.simplefilter("ignore", DeprecationWarning)
app = Flask(__name__)
session_store = {}
MAX_PARALLEL_TOOL_CALLS = 10


def _log(label, message):
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{stamp}] [{label}] {message}", flush=True)


def _log_json(label, payload):
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    pretty = json.dumps(payload, indent=2, ensure_ascii=False, default=str)
    print(f"[{stamp}] [{label}] {pretty}", flush=True)

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
- prefer tools over free-text when an action/data retrieval is needed
- always use local timezone for interacting with calender
- interpret the requested event time in the local timezone first to resolve the correct calendar date and time, then convert that resolved local datetime into UTC
- apply extra reasoning scrutiny around meridians (AM/PM), especially 12:00 times
- treat "noon" as exactly 12:00 PM (12:00 local)
- treat "midnight" as exactly 12:00 AM (00:00 local) and resolve whether it means start-of-day vs next-day from context
- if a requested time could be interpreted as AM or PM, do not guess; ask a clarifying question before calling tools
- before calling tools, perform a final meridian sanity check so daytime requests (e.g. 2 PM) are not converted to overnight equivalents (e.g. 2 AM)
- If no duration is stated; *1 hour* is the default
- After any tool execution, always return a user-facing confirmation message (e.g. “Event added”, “Done”, or a brief status summary), even if no additional information is required
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
    event = f"""BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
SUMMARY:{title}
DTSTART:{start}
DTEND:{finish}
LOCATION:{location}
DESCRIPTION:{description}
RRULE:{rrule}
END:VEVENT
END:VCALENDAR"""
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



def ask_gpt54(user_input, system_prompt, results, previous_response_id=None, user_timezone=None):
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

    # Prepend time context to every user request before sending it to the model.
    formatted_request = (
        f"Current UTC time: {now_utc.strftime('%Y-%m-%d, %a %H:%M:%S  %z')}\n"
        f"Current Local time: {now_local.strftime('%Y-%m-%d, %a %H:%M:%S  %z')}\n"
        f"Request:{raw_prompt}"
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


def run_secretariat(prompt_text,image_data_url=None, previous_response_id=None, user_timezone=None, max_turns=12):
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
    user_timezone = session_data.get("timezone")

    if not prompt_text:
        return jsonify({"ok": False, "error": "Prompt is required."}), 400

    try:
        result = run_secretariat(
            prompt_text,
            image_data_url=image_data_url,
            previous_response_id=previous_response_id,
            user_timezone=user_timezone,
        )
        session_store[session_id] = {
            "previous_response_id": result.get("previous_response_id"),
            "timezone": user_timezone,
        }
        if result.get("state") == "DONE":
            session_store.pop(session_id, None)
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

    session_data = session_store.get(
        session_id,
        {"previous_response_id": None, "timezone": None},
    )
    if timezone_name:
        session_data["timezone"] = timezone_name
    session_store[session_id] = session_data

    return jsonify(
        {
            "ok": True,
            "session_id": session_id,
            "timezone": session_data.get("timezone"),
        }
    )


if __name__ == "__main__":
    secret = load_value_file('secrets.txt')
    USERNAME = secret['USERNAME']
    PASSWORD =  secret['PASSWORD']
    api_key = secret['api_key']
    app.run(host="127.0.0.1", port=8000, debug=False)

"""
Eating a sammy
"""
