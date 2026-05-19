from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import caldav

EMAIL = "stefanhall05@gmail.com"
APP_PASSWORD = "tmbv tnss oamm yurg"

URL = "https://www.google.com/calendar/dav/stefanhall05@gmail.com/events/"

TZ = ZoneInfo("Pacific/Auckland")

client = caldav.DAVClient(
    url=URL,
    username=EMAIL,
    password=APP_PASSWORD
)

calendar = client.calendar(url=URL)

start = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
end = start + timedelta(days=1)

events = calendar.date_search(start=start, end=end)

for event in events:
    vevent = event.vobject_instance.vevent
    print(vevent.summary.value if hasattr(vevent, "summary") else "(No title)")
    print(vevent.dtstart.value)
    print("-" * 40)