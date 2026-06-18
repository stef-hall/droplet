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
        #memories = SearchMemories(user_id=user_id, query=query, top_k=top_k)
        memories = [{'confidence': 0.98, 'created_at': '2026-06-16T01:52:51+12:00', 'entities': ['user', 'Tilly'], 'expires_at': None, 'id': 'mem_a4a69aa6afa84423a7ae1f6743a71359', 'importance': 0.72, 'source': 'user_explicit', 'tags': ['pet', 'dog'], 'text': "Tilly is the user's dog.", 'type': 'Entities', 'updated_at': '2026-06-16T01:52:51+12:00', 'user_id': 3, 'score': 0.8762269616127014}, {'created': '2026-06-15T20:08:15+12:00', 'facts': ["The user's name is Stefan."], 'id': 'mem_c68d6a2725434b11a694edf755818613', 'search_text': "User's name is Stefan", 'type': 'Entities', 'updated': '2026-06-15T20:12:22+12:00', 'user_id': 3, 'score': 0.8776365518569946}, {'confidence': 1.0, 'created_at': '2026-06-16T01:59:01+12:00', 'entities': ['user', 'assistant'], 'expires_at': None, 'id': 'mem_2cd6b8c6d96a4db6a2aba4723632aa6d', 'importance': 0.78, 'source': 'user_explicit', 'tags': ['next-conversation', 'reminder', 'phrase'], 'text': 'When the user next talks to the assistant, say: "Surprise Im right Here!"', 'type': 'Trigger', 'updated_at': '2026-06-18T12:13:05+12:00', 'user_id': 3, 'score': 0.9360418915748596}]
        print(memories)
        print('\n')
    except Exception as e:
        _log("MEMORY_RAG", f"search failed: {e}")
        return ""

    if not memories:
        return ""

    rows = []

    for memory in memories:
        if not isinstance(memory, dict):
            continue

        text = str(memory.get("text", "")).strip()
        if not text:
            continue

        memory_id = str(memory.get("id", "")).strip()
        alias_id = _get_memory_alias(int(user_id), memory_id) if memory_id else ""

        rows.append([
            alias_id,
            memory.get("type", ""),
            text
        ])

    if not rows:
        return ""

    memory_context = {
        "memories": rows,
        "instruction": "Use only when relevant."
    }

    return json.dumps(memory_context, ensure_ascii=False, separators=(",", ":"), default=str)

x = _retrieve_memory_context(3, "hey bud")


print(x)