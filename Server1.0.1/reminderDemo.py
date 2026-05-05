from caldav import DAVClient
import sys
sys.stdout.reconfigure(encoding="utf-8")

client = DAVClient(
    url="https://caldav.icloud.com",

)

principal = client.principal()

new_list = principal.make_calendar(
    name="Test Reminders List",
    supported_calendar_component_set=["VTODO"],
)

print("Created:", new_list.name)
print("URL:", new_list.url)