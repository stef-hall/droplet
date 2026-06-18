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
    return "bruh"

def _retrieve_memory_context(user_id, query, top_k=5):
    if user_id is None:
        return ""

    try:
        # memories = SearchMemories(user_id=user_id, query=query, top_k=top_k)
        memories = [
            {
                "mem_ID": "mem_00a71b3348f84a24bec99ce4e673cdea",
                "type": "Entity",
                "search_text": "User's dog is named Tilly",
                "facts": {"entity": "dog", "name": "Tilly", "owner": "Stefan"},
                "created_at": "2026-06-18T13:21:15+12:00",
                "updated_at": "2026-06-18T13:21:15+12:00"
            },
            {
                "mem_ID": "mem_00ef3c0fe0ed41848908005521d518f8",
                "type": "Entity",
                "search_text": "User's name is Stefan",
                "facts": {"entity": "user", "name": "Stefan"},
                "created_at": "2026-06-18T13:21:15+12:00",
                "updated_at": "2026-06-18T13:21:15+12:00"
            }
        ]

        print(memories)
        print("\n")
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
        {"cols": columns, "Memories": rows, "Triggers": rows, "instruction": "Use only when relevant."},
        ensure_ascii=False,
        separators=(",", ":"),
        default=str
    )


x = _retrieve_memory_context(3, "hey bud")
print(x)
