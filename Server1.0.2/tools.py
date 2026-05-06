from __future__ import annotations

from datetime import datetime, timezone, timedelta
import argparse
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen
import re


_get_user_caldav_calendars_fn = None
_lists_dir = Path(__file__).resolve().parent / "lists"

def offset_to_z(dt_str: str):
    """
    '20260507T150000+12:00' -> ('20260507T030000Z', '+12:00')
    """
    m = re.search(r'([+-]\d{2}:\d{2})$', dt_str)
    if not m:
        raise ValueError("Datetime must end with offset like +12:00")
    offset = m.group(1)
    dt = datetime.fromisoformat(
        dt_str[:4] + "-" + dt_str[4:6] + "-" + dt_str[6:8] +
        "T" + dt_str[9:11] + ":" + dt_str[11:13] + ":" + dt_str[13:15] +
        offset
    )
    z = dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return z, offset


def z_to_offset(z_str: str, offset: str):
    """
    '20260507T030000Z', '+12:00' -> '20260507T150000+12:00'
    """
    dt = datetime.strptime(z_str, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    sign = 1 if offset[0] == "+" else -1
    hours = int(offset[1:3])
    minutes = int(offset[4:6])
    tz = timezone(sign * timedelta(hours=hours, minutes=minutes))
    return dt.astimezone(tz).strftime("%Y%m%dT%H%M%S") + offset


def configure_tools(get_user_caldav_calendars, lists_dir: Path | None = None):
    global _get_user_caldav_calendars_fn, _lists_dir
    _get_user_caldav_calendars_fn = get_user_caldav_calendars
    if lists_dir is not None:
        _lists_dir = Path(lists_dir)


def _get_user_caldav_calendars(user_id: int):
    if _get_user_caldav_calendars_fn is None:
        raise RuntimeError("Tools not configured: missing CalDAV calendar provider.")
    return _get_user_caldav_calendars_fn(user_id)


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
    start_dt, offset = offset_to_z(start)
    end_dt, offset = offset_to_z(end)

    if end_dt <= start_dt:
        raise ValueError("`end` must be after `start`.")

    calendars = _get_user_caldav_calendars(int(user_id))
    results = []
    for cal in calendars:
        try:
            events = cal.date_search(start=start_dt, end=end_dt)
        except Exception:
            # Some CalDAV providers are unreliable with date_search.
            # Fallback: scan calendar events and filter by DTSTART when possible.
            events = cal.events()
        for event in events:
            try:
                data = event.vobject_instance
            except Exception:
                continue
            if not data or not hasattr(data, "vevent"):
                continue

            vevent = data.vevent
            event_start = None
            if hasattr(vevent, "dtstart"):
                dt_value = vevent.dtstart.value
                if isinstance(dt_value, datetime):
                    event_start = dt_value if dt_value.tzinfo else dt_value.replace(tzinfo=timezone.utc)
                else:
                    try:
                        event_start = datetime.fromisoformat(str(dt_value))
                        if event_start.tzinfo is None:
                            event_start = event_start.replace(tzinfo=timezone.utc)
                    except Exception:
                        event_start = None

            if event_start is not None:
                event_start_utc = event_start.astimezone(timezone.utc)
                if not (start_dt <= event_start_utc <= end_dt):
                    continue

            results.append(
                {
                    "uid": str(vevent.uid.value),
                    "start": str(z_to_offset(vevent.dtstart.value, offset) ),
                    "end": str(z_to_offset(vevent.dtstart.value, offset)) if hasattr(vevent, "dtend") else None,
                    "summary": str(vevent.summary.value) if hasattr(vevent, "summary") else None,
                    "location": str(vevent.location.value) if hasattr(vevent, "location") else None,
                    "description": str(vevent.description.value) if hasattr(vevent, "description") else None,
                    "calendar": cal.get_display_name(),
                }
            )
    return results


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


def ReadList(list_name):
    safe_name = str(list_name).strip()
    if not safe_name:
        return {"status": "failed", "error": "List name is required."}
    list_path = _lists_dir / f"{safe_name}.txt"
    if not list_path.exists() or not list_path.is_file():
        return {"status": "not_found", "list_name": safe_name}
    with open(list_path, "r", encoding="utf-8") as f:
        content = f.read()
    return {"status": "success", "list_name": safe_name, "content": content}


def EditList(list_name, content):
    safe_name = str(list_name).strip()
    if not safe_name:
        return {"status": "failed", "error": "List name is required."}
    _lists_dir.mkdir(parents=True, exist_ok=True)
    list_path = _lists_dir / f"{safe_name}.txt"
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


def EditEvent(user_id, uid, title=None, start=None, finish=None, location=None, description=None, rrule=None):
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

    data = {
      "tool": "GetEvents",
      "args": {
        "start": "20260507T113236+12:00",
        "end": "20260509T113236+12:00"
      }
    }
    data = data['args']

    response = GetEvents(3, start=data['start'], end=data['end'])

    print(response)
