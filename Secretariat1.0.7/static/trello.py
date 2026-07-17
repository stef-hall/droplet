import requests
import sys
sys.stdout.reconfigure(encoding="utf-8")

# https://trello.com/1/authorize?expiration=never&name=MyApp&scope=read,write&response_type=token&key=ac891ffdcf2553ac640f08509636d1c6
# https://trello.com/1/authorize?expiration=never&name=MyApp&scope=read,write&response_type=token&key=YOUR_API_KEY



BASE = "https://api.trello.com/1"
BOARD_ID = "68a4ff7e11673166fa68cbfa"

auth = {
    "key": API_KEY,
    "token": TOKEN
}

r = requests.get(
    f"{BASE}/boards/{BOARD_ID}/lists",
    params={
        **auth,
        "cards": "open",
        "card_fields": "name,desc,due"
    }
)

r.raise_for_status()
lists = r.json()

for trello_list in lists:
    print(f"\n=== {trello_list['name']} ===")

    cards = trello_list.get("cards", [])

    if not cards:
        print("No cards")

    for card in cards:
        print(f"- {card['name']}")
        print(f"  Desc: {card.get('desc', '')}")
        print(f"  Due: {card.get('due')}")
