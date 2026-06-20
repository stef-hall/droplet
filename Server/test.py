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

def _compile_memories(user_id, query, cols, top_k, types):
    try:
        memories = SearchMemories(user_id=user_id, query=query, top_k=top_k, types=types)
    except Exception as e:
        _log("MEMORY_RAG", f"search failed for types ({types}): {e}")
        return ""
 
    rows = []
    for memory in memories:
        if not isinstance(memory, dict):
            continue

        values = {}
        for data in cols:
            if data == "mem_ID":
                memory_id = memory.get(data, {})
                values["mem_ID"] = _get_memory_alias(int(user_id), memory_id) if memory_id else ""
            else:
                values[data] = memory.get(data)

        rows.append([values[column] for column in cols])

    if not rows:
        return []
    else:
        return rows


def _retrieve_memory_context(user_id, query, top_k=5):
    if user_id is None:
        return ""

    cols = ["mem_ID", "type", "search_text", "facts"]
    relevantInfo = _compile_memories(user_id, query, cols, 8, ['Preference', 'Entity', 'Commitment'])
    Reminders = _compile_memories(user_id, query, cols, 5, ['Reminder'])
    Triggers = _compile_memories(user_id, query, cols, 5, ['Trigger'])

    return json.dumps(
        {"cols": cols, "Memories": relevantInfo, "Reminders": Reminders, "Triggers": Triggers},
        ensure_ascii=False,
        separators=(",", ":"),
        default=str
    )


x = _retrieve_memory_context(3, "hey bud")
print(x)
