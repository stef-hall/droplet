from __future__ import annotations

from datetime import datetime, timezone, timedelta, date
import argparse
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen
import re


_get_user_caldav_calendars_fn = None
_lists_dir = Path(__file__).resolve().parent / "lists"

def offset_to_z(s):
    if s == None:
        return None, None
    if isinstance(s, date) and not isinstance(s, datetime):
        return s, None
    if isinstance(s, str) and re.fullmatch(r"\d{8}", s):
        return datetime.strptime(s, "%Y%m%d").date(), None
    dt = datetime.fromisoformat(
        f"{s[:4]}-{s[4:6]}-{s[6:8]}T{s[9:11]}:{s[11:13]}:{s[13:15]}{s[15:]}"
    )
    offset = s[15:]
    return dt.astimezone(timezone.utc), offset


def z_to_offset(z, offset):
    if z == None:
        return None
    if isinstance(z, date) and not isinstance(z, datetime):
        return z.strftime("%Y%m%d")
    if isinstance(z, datetime):
        dt = z.astimezone(timezone.utc)
    else:
        if isinstance(z, str) and re.fullmatch(r"\d{8}", z):
            return z
        dt = datetime.strptime(z, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)

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


def AddEvent(user_id, title, start, finish, location, description, rrule):
    start, offset = offset_to_z(start)
    finish, offset = offset_to_z(finish)
    start = start.strftime("%Y%m%dT%H%M%SZ")
    finish = finish.strftime("%Y%m%dT%H%M%SZ")

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
    calendars = _get_user_caldav_calendars(int(user_id))
    calendar = calendars[0]
    calendar.add_event(event)
    return {"status": "Complete"}


def GetEvents(user_id, start, end):
    start, offset = offset_to_z(start)
    end, offset = offset_to_z(end)
    calendars = _get_user_caldav_calendars(int(user_id))
    columns = ["uid", "start", "end", "summary", "location", "description", "rrule", "calendar"]
    rows = []
    for cal in calendars:
        events = cal.date_search(start=start, end=end)
        for event in events:
            try:
                data = event.vobject_instance
                if not data or not hasattr(data, "vevent"):
                    continue

                vevent = data.vevent
                rows.append(
                    [
                        str(vevent.uid.value),
                        str(z_to_offset(vevent.dtstart.value, offset)),
                        str(z_to_offset(vevent.dtend.value, offset)) if hasattr(vevent, "dtend") else None,
                        str(vevent.summary.value) if hasattr(vevent, "summary") else None,
                        str(vevent.location.value) if hasattr(vevent, "location") else None,
                        str(vevent.description.value) if hasattr(vevent, "description") else None,
                        str(vevent.rrule.value) if hasattr(vevent, "rrule") else None,
                        cal.get_display_name(),
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


def DeleteEvent(user_id, uid):
    calendars = _get_user_caldav_calendars(int(user_id))
    for cal in calendars:
        for event in cal.events():
            data = event.vobject_instance
            if data and hasattr(data, "vevent"):
                if str(data.vevent.uid.value) == uid:
                    event.delete()
                    return {"status": "deleted"}
    return {"status": "not_found"}


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


def EditEvent(user_id, uid, title=None, start=None, finish=None, location=None, description=None, rrule=None):
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
            if current_uid != uid:
                continue

            current_title = str(vevent.summary.value) if hasattr(vevent, "summary") else ""
            current_start = getattr(vevent.dtstart, "value", None) if hasattr(vevent, "dtstart") else None
            current_finish = getattr(vevent.dtend, "value", None) if hasattr(vevent, "dtend") else None
            current_location = str(vevent.location.value) if hasattr(vevent, "location") else ""
            current_description = str(vevent.description.value) if hasattr(vevent, "description") else ""
            current_rrule = str(vevent.rrule.value) if hasattr(vevent, "rrule") else ""

            new_title = title if title is not None else current_title
            new_start = start if start is not None else current_start
            new_finish = finish if finish is not None else current_finish
            new_location = location if location is not None else current_location
            new_description = description if description is not None else current_description
            new_rrule = rrule if rrule is not None else current_rrule

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
                uid=uid,
                title=str(new_title),
                start=start_ics,
                finish=finish_ics,
                location=str(new_location),
                description=str(new_description),
                rrule=str(new_rrule),
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



if __name__ == "__main__":
    from server import LISTS_DIR, _get_user_caldav_calendars

    configure_tools(_get_user_caldav_calendars, LISTS_DIR)


    EditEvent(3,
        uid="f1c794d5-b32b-40ab-992f-d50568b06337",
        start="20260512T180000+12:00",
        finish="20260512T190000+12:00"
    )
    print(response)
    quit()
    
    response = GetEvents(3,
    start="20260507T000000+12:00",
    end="20260508T000000+12:00"
    )
    print(response)
    
   

    response = AddEvent(3,
    title="Working AddEvent",
    start="20260507T142556+12:00",
    finish="20260507T143056+12:00",
    location="",
    description="",
    rrule=""
)
    print(response)
