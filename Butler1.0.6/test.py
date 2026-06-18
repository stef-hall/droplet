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
import time
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
def _log(label, message):
    print(f"[{label}] {message}", flush=True)

def _get_memory_alias(user_id: int, real_id: str) -> str:
    return "ID"

def _retrieve_memory_context(user_id, query, top_k=5):
    if user_id is None:
        return ""

    try:
        #memories = SearchMemories(user_id=user_id, query=query, top_k=top_k)
        memories = [{'mem_ID': 'mem_289df82dd82a4098869eec986fefbfd5', 'type': 'Preference', 'search_text': 'User likes Tyler, the Creator and Fallout', 'facts': {'likes': "['Tyler, the Creator', 'Fallout']"}, 'created_at': '2026-06-18T18:08:44+12:00', 'updated_at': '2026-06-18T18:08:44+12:00', 'score': 0.8216750025749207}, {'mem_ID': 'mem_9167b040c82646feb3cce8ff00a09446', 'type': 'Entity', 'search_text': 'User likes Elvis before midday and The Rolling Stones after midday', 'facts': {'after_12:00': 'The Rolling Stones', 'before_12:00': 'Elvis', 'entity': 'music preference by time'}, 'created_at': '2026-06-18T18:08:44+12:00', 'updated_at': '2026-06-18T18:08:44+12:00', 'score': 0.8325091004371643}, {'mem_ID': 'mem_d0d8d2a20ec142839b54bddfb99e63a8', 'type': 'Entity', 'search_text': "User's dog is named Tilly", 'facts': {'entity': 'dog', 'name': 'Tilly', 'owner': 'user'}, 'created_at': '2026-06-18T18:07:11+12:00', 'updated_at': '2026-06-18T18:07:11+12:00', 'score': 0.8693448305130005}, {'mem_ID': 'mem_2d4c66d3b55d4762955a9dc951a159c6', 'type': 'Entity', 'search_text': "User's name is Stefan", 'facts': {'entity': 'user', 'name': 'Stefan'}, 'created_at': '2026-06-18T18:07:11+12:00', 'updated_at': '2026-06-18T18:07:11+12:00', 'score': 0.8776365518569946}, {'mem_ID': 'mem_e0206fdee46c4413bc5f139f7a4a7ce0', 'type': 'Trigger', 'search_text': 'At the beginning of every week, remind the user to plan runs', 'facts': {'action': 'remind user to plan runs', 'condition': 'beginning of every week', 'details': 'user goes on runs twice a day', 'recurrence': 'weekly'}, 'created_at': '2026-06-18T18:08:44+12:00', 'updated_at': '2026-06-18T18:08:44+12:00', 'score': 0.9183304309844971}]
    except Exception as e:
        _log("MEMORY_RAG", f"search failed: {e}")
        return ""

    columns = ["mem_ID", "type", "search_text", "facts"]
    rows = []

    for memory in memories:
        if not isinstance(memory, dict):
            continue

        values = {}
        for data in columns:
            if data == "mem_ID":
                memory_id = memory.get(data, {})
                values["mem_ID"] = _get_memory_alias(int(user_id), memory_id) if memory_id else ""
            else:
                values[data] = memory.get(data)

        rows.append([values[column] for column in columns])

    if not rows:
        return ""

    return json.dumps(
        {"cols": columns, "Memories": rows, "instruction": "Use only when relevant."},
        ensure_ascii=False,
        separators=(",", ":"),
        default=str
    )


x = _retrieve_memory_context(3, "hey bud")
print(x)
