"""Conversation store, transcripts and history folding."""
import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import signal
import smtplib
import subprocess
import tarfile
import time
import uuid
import io
from datetime import datetime, timedelta
from email.mime.text import MIMEText
import email.utils
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
from urllib.parse import urlparse, urljoin
from zoneinfo import ZoneInfo
import ipaddress
import socket
import httpx
import yaml
from openai import AsyncOpenAI

from .config import CONV_DIR, _read, _write, now_iso

# conversations ---------------------------------------------------------------


def load_conversation(cid: str) -> Optional[Dict]:
    return _read(CONV_DIR / f"{cid}.json")


def save_conversation(c: Dict):
    c["updated"] = now_iso()
    for m in c.get("messages", []):
        m.setdefault("ts", c["updated"])  # one place: every stored message gets a timestamp for the UI
    _write(CONV_DIR / f"{c['id']}.json", c)


def new_conversation(title: str, source: str = "chat") -> Dict:
    c = {"id": "c_" + uuid.uuid4().hex[:12], "title": title[:60], "source": source,
         "created": now_iso(), "updated": now_iso(), "messages": [], "events": []}
    save_conversation(c)
    return c


def list_conversations(source_prefix: str = "chat", limit: int = 50) -> List[Dict]:
    items = []
    for p in CONV_DIR.glob("*.json"):
        c = _read(p)
        if c and c.get("source", "chat").startswith(source_prefix):
            items.append({"id": c["id"], "title": c["title"], "updated": c["updated"], "source": c["source"]})
    items.sort(key=lambda x: x["updated"], reverse=True)
    return items[:limit]


def history_transcript(conv: Dict, start: int = 0, limit_chars: int = 12000) -> str:
    """Plain-text transcript of conv["messages"][start:] (user/assistant text; tool use summarised) for runtimes that keep their own state."""
    lines = []
    for m in conv.get("messages", [])[start:]:
        role, content = m.get("role"), m.get("content") or ""
        if role == "user":
            lines.append("Owner: " + content.strip())
        elif role == "assistant":
            if m.get("tool_calls"):
                names = ", ".join(tc.get("function", {}).get("name", "tool") for tc in m["tool_calls"])
                lines.append(f"SU (used tools: {names})" + (": " + content.strip() if content.strip() else ""))
            elif content.strip():
                lines.append("SU: " + content.strip())
    text = "\n".join(lines)
    return text[-limit_chars:] if len(text) > limit_chars else text


def model_history(conv: Dict) -> List[Dict]:
    """Messages to send: the summary of folded turns (if any) then the recent turns, without UI-only keys."""
    start = conv.get("folded_upto", 0)
    hist = [{k: v for k, v in m.items() if k not in ("ts", "plan")} for m in conv["messages"][start:]]  # UI-only keys never reach the provider
    if start and conv.get("summary"):
        hist = [{"role": "user", "content": "Summary of the earlier part of this conversation:\n" + conv["summary"]}, {"role": "assistant", "content": "Noted."}] + hist
    return hist
