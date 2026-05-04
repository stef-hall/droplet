from __future__ import annotations

import ast
import operator as op
from flask import Flask, jsonify, render_template, request

from datetime import datetime, timezone
from caldav import DAVClient
from openai import OpenAI
import vobject
import json
import warnings
import base64
import mimetypes
import uuid

warnings.simplefilter("ignore", DeprecationWarning)
app = Flask(__name__)
session_store = {}

global USERNAME, PASSWORD, api_key
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

secret = load_value_file('secrets.txt')
USERNAME = secret['USERNAME']
PASSWORD =  secret['PASSWORD']
api_key = secret['api_key']

def get_utc_calendar_string() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')

def get_local_time_string() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


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


def ToolUse(name, args):
    print('\nDeploying Tool: ', name, args)

    # Add Calender Event
    if name == 'AddEvent':
        title = args["title"]
        start = args["start"]
        finish = args["finish"]
        location = args.get("location", "")
        description = args.get("description", "")
        rrule = args.get("rrule", "")
        output = AddEvent(
            title=title,
            start=start,
            finish=finish,
            location=location,
            description=description,
            rrule=rrule
        )
        return output

    # Returns List of Events in Timeframe
    if name == 'GetEvents':
        start = args["start"]
        end = args["end"]
        output = GetEvents(
            start=start,
            end=end,
        )
        return output

    # Deletes Event for UID
    if name == 'DeleteEvent':
        uid = args['uid']
        output = DeleteEvent(
            uid=uid
        )
        return output



def ask_gpt54(user_input, system_prompt, results, previous_response_id=None):
    client = OpenAI(api_key=api_key)

    now_utc = datetime.now(timezone.utc)
    now_local = datetime.now().astimezone()
    image_data_url = None
    if isinstance(user_input, dict):
        image_data_url = user_input.get("image_data_url")
        raw_prompt = user_input.get("prompt", "")
    else:
        raw_prompt = user_input

    formatted_request = (
        f"Current UTC time: {now_utc.strftime('%Y-%m-%d, %a %H:%M:%S  %z')}\n"
        f"Current Local time: {now_local.strftime('%Y-%m-%d, %a %H:%M:%S  %z')}\n"
        f"Request:{raw_prompt}"
    )
    user_content = [{"type": "input_text", "text": formatted_request}]
    if image_data_url:
        user_content.append({"type": "input_image", "image_url": image_data_url})

    # First turn: provide system + user input.
    # Follow-up turns:
    # - if tools are pending, return tool outputs only
    # - otherwise send the next user turn while keeping conversation chain context
    if previous_response_id is None:
        input_items = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]
        response = client.responses.create(model="gpt-5.4", input=input_items, tools=tools)
    else:
        if results:
            input_items = results
        else:
            input_items = [{"role": "user", "content": user_content}]
        response = client.responses.create(
            model="gpt-5.4",
            input=input_items,
            tools=tools,
            previous_response_id=previous_response_id,
        )

    with open("mock.json", "w", encoding="utf-8") as f:
        json.dump(response.model_dump(), f, indent=2)
    return response


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
- If no duration is stated; *1 hour* is the default
- After any tool execution, always return a user-facing confirmation message (e.g. “Event added”, “Done”, or a brief status summary), even if no additional information is required
- Always return a state. RUNNING = Operating Tools/Thinking, WAITING = Waiting for User Input, DONE = ONLY when completley finished your task.

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
    }    
]
""" Possibile, but so expensive ~10k tokens..
{
    "type": "web_search"
}
"""


def run_secretariat(prompt_text, image_data_url=None, previous_response_id=None, max_turns=12):
    results = []
    state = "RUNNING"
    assistant_message = ""
    current_response_id = previous_response_id

    for _ in range(max_turns):
        user_turn = {"prompt": prompt_text, "image_data_url": image_data_url}
        response = ask_gpt54(user_turn, system_prompt, results, current_response_id)
        current_response_id = response.id
        response_data = response.model_dump()
        results = []
        saw_function_call = False

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
                name = content["name"]
                args = json.loads(content["arguments"])
                tool_output = ToolUse(name, args)
                results.append({
                    "type": "function_call_output",
                    "call_id": content["call_id"],
                    "output": json.dumps(tool_output),
                })

        if saw_function_call:
            continue

        if state in {"WAITING", "DONE"}:
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


@app.post("/api/secretariat")
def api_secretariat():
    payload = request.get_json(silent=True) or {}
    prompt_text = str(payload.get("prompt", "")).strip()
    image_data_url = payload.get("image_data_url")
    session_id = str(payload.get("session_id", "")).strip() or str(uuid.uuid4())
    previous_response_id = session_store.get(session_id)

    if not prompt_text:
        return jsonify({"ok": False, "error": "Prompt is required."}), 400

    try:
        result = run_secretariat(
            prompt_text,
            image_data_url=image_data_url,
            previous_response_id=previous_response_id,
        )
        session_store[session_id] = result.get("previous_response_id")
        if result.get("state") == "DONE":
            session_store.pop(session_id, None)
        return jsonify({"ok": True, "session_id": session_id, **result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=False)



"""
Grabbing a Fanta
"""
