"""Automation store, schedule maths and natural-language schedule parsing."""
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

from . import providers as _providers
from .config import AUTO_DIR, RUNS_DIR, TZ, _read, _write, now_iso
from .providers import SEED_MODELS, _gemini_native_ok, cc_available, chat_providers, default_model, gemini_simple, split_model
from .runtime_claude import cc_quick

# automations ----------------------------------------------------------------


def list_automations() -> List[Dict]:
    items = [a for a in (_read(p) for p in AUTO_DIR.glob("*.json")) if a]
    items.sort(key=lambda a: a.get("next_run") or "9")
    return items


def get_automation(aid: str) -> Optional[Dict]:
    return _read(AUTO_DIR / f"{aid}.json")


def save_automation(a: Dict) -> Dict:
    a.setdefault("id", "a_" + uuid.uuid4().hex[:10])
    a.setdefault("enabled", True)
    a.setdefault("notify", "none")
    a.setdefault("created", now_iso())
    a["schedule_text"] = describe_schedule(a.get("schedule") or {})
    a["next_run"] = next_run(a["schedule"], datetime.now(TZ)).isoformat(timespec="minutes") if a["enabled"] and a.get("schedule") else None
    _write(AUTO_DIR / f"{a['id']}.json", a)
    return a


def delete_automation(aid: str) -> bool:
    p = AUTO_DIR / f"{aid}.json"
    if p.exists():
        p.unlink()
        shutil.rmtree(RUNS_DIR / aid, ignore_errors=True)
        return True
    return False


def list_runs(aid: str, limit: int = 30) -> List[Dict]:
    d = RUNS_DIR / aid
    if not d.exists():
        return []
    runs = [r for r in (_read(p) for p in d.glob("*.json")) if r]
    runs.sort(key=lambda r: r["started"], reverse=True)
    return runs[:limit]

# schedule --------------------------------------------------------------------
# {"type":"interval","minutes":480} | {"type":"times","times":["08:00"],"days":[0..6]} | {"type":"once","at":"2026-09-08T09:00"}


def next_run(s: Dict, after: datetime) -> Optional[datetime]:
    t = s.get("type")
    if t == "interval":
        mins = max(5, int(s.get("minutes", 60)))
        anchor = s.get("anchor")
        if anchor:
            a = datetime.fromisoformat(anchor).replace(tzinfo=TZ)
            if a > after:
                return a
            k = int((after - a).total_seconds() // (mins * 60)) + 1
            return a + timedelta(minutes=mins * k)
        return after + timedelta(minutes=mins)
    if t == "times":
        days = s.get("days") or list(range(7))
        times = sorted(s.get("times") or ["09:00"])
        for d in range(0, 8):
            day = (after + timedelta(days=d)).date()
            if day.weekday() not in days:
                continue
            for hm in times:
                h, m = [int(x) for x in hm.split(":")]
                cand = datetime(day.year, day.month, day.day, h, m, tzinfo=TZ)
                if cand > after:
                    return cand
        return None
    if t == "monthly":
        day = max(1, min(28, int(s.get("day", 1))))
        h, m = [int(x) for x in (s.get("time") or "09:00").split(":")]
        y, mo = after.year, after.month
        for _ in range(3):
            cand = datetime(y, mo, day, h, m, tzinfo=TZ)
            if cand > after:
                return cand
            mo += 1
            if mo > 12:
                mo, y = 1, y + 1
        return None
    if t == "once":
        at = datetime.fromisoformat(s["at"])
        at = at if at.tzinfo else at.replace(tzinfo=TZ)
        return at if at > after else None
    return None


DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def describe_schedule(s: Dict) -> str:
    t = s.get("type")
    if t == "interval":
        m = int(s.get("minutes", 60))
        if m % 1440 == 0:
            return f"Every {m // 1440} day(s)"
        if m % 60 == 0:
            return f"Every {m // 60} hour(s)" if m > 60 else "Hourly"
        return f"Every {m} minutes"
    if t == "times":
        days = s.get("days") or list(range(7))
        times = ", ".join(_ampm(x) for x in sorted(s.get("times") or []))
        if days == list(range(7)):
            return f"Daily at {times}"
        if days == [0, 1, 2, 3, 4]:
            return f"Every weekday at {times}"
        if len(days) == 1:
            return f"Every {DAYS[days[0]]} at {times}"
        return f"{', '.join(DAYS[d] for d in days)} at {times}"
    if t == "monthly":
        return f"Monthly on day {s.get('day', 1)} at {_ampm(s.get('time') or '09:00')}"
    if t == "once":
        return "Once at " + s.get("at", "?").replace("T", " ")
    return "No schedule"


def _ampm(hm: str) -> str:
    h, m = [int(x) for x in hm.split(":")]
    suf = "am" if h < 12 else "pm"
    h12 = h % 12 or 12
    return f"{h12}:{m:02d} {suf}"


async def parse_schedule_nl(text: str, model: Optional[str] = None) -> Dict:
    """Natural language -> schedule JSON (the same trick Zo uses)."""
    model = model or default_model()
    if model.startswith(("claude-code:", "gemini-cli:")):
        ps = chat_providers()
        model = f"{ps[0]}:{SEED_MODELS[ps[0]][0]}" if ps else model
    provider, m = split_model(model)
    prompt = (
        "Convert the schedule description into JSON. Output ONLY JSON, one of:\n"
        '{"type":"interval","minutes":N}\n'
        '{"type":"times","times":["HH:MM",...],"days":[0-6 Monday=0]}\n'
        '{"type":"monthly","day":1-28,"time":"HH:MM"}\n'
        '{"type":"once","at":"YYYY-MM-DDTHH:MM"}\n'
        f"Now is {datetime.now(TZ).strftime('%Y-%m-%d %H:%M %A')} in {TZ.key}. 24h times. "
        "'weekdays' means days [0,1,2,3,4]. If no days are given use all 7.\n\nDescription: " + text
    )
    if provider == "google" and _gemini_native_ok():
        raw = await gemini_simple(m, prompt, json_mode=True)
    elif not chat_providers() and cc_available():
        raw = await cc_quick(prompt)
    else:
        r = await _providers.client_for(provider).chat.completions.create(model=m, messages=[{"role": "user", "content": prompt}], temperature=0)
        raw = r.choices[0].message.content or ""
    match = re.search(r"\{.*\}", raw, re.S)
    s = json.loads(match.group(0)) if match else {}
    describe_schedule(s)  # validate shape
    return s
