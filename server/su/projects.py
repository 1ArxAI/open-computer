"""Secrets grouped by project (names only; values live in .env)."""
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

from .config import DATA, _read, _write, env_file_keys

# ---------------------------------------------------------------- projects: secrets grouped by project (names only; values live in .env)

PROJECTS_FILE = DATA / "projects.json"
PROFILE = "profile"
BUILTIN_GROUPS = {"profile": "SU profile (owner's email, phone, mail, system)", "ai": "AI providers and model settings", "tools": "Tools and apps"}  # shown in their own settings tabs
HIDDEN_KEYS = {"SU_TOKEN", "SU_SESSION_SECRET", "PORT", "HOST"}  # never shown to the model


def projects_data() -> Dict:
    d = _read(PROJECTS_FILE, {}) or {}
    return {"projects": d.get("projects", []), "keys": d.get("keys", {})}


def project_of(key: str) -> Optional[str]:
    return projects_data()["keys"].get(key)


def save_project(pid: Optional[str], name: str, note: str) -> Dict:
    d = projects_data()
    pid = pid or re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or uuid.uuid4().hex[:6]
    p = next((x for x in d["projects"] if x["id"] == pid), None)
    if p:
        p.update(name=name, note=note)
    else:
        p = {"id": pid, "name": name, "note": note}; d["projects"].append(p)
    _write(PROJECTS_FILE, d)
    return p


def delete_project(pid: str) -> bool:
    d = projects_data()
    if not any(x["id"] == pid for x in d["projects"]):
        return False
    d["projects"] = [x for x in d["projects"] if x["id"] != pid]
    d["keys"] = {k: v for k, v in d["keys"].items() if v != pid}
    _write(PROJECTS_FILE, d)
    return True


def assign_key(key: str, pid: Optional[str]):
    d = projects_data()
    if pid:
        d["keys"][key] = pid
    else:
        d["keys"].pop(key, None)
    _write(PROJECTS_FILE, d)


SYSTEM_PROFILE_KEYS = {
    "SU_HOST", "SU_PORT", "SU_TOKEN", "SU_SESSION_SECRET", "SU_PUBLIC_URL",
    "SU_TZ", "SU_SESSION_IDLE", "SU_TRUST_PROXY", "SU_LOGIN_USER",
    "CLOUDFLARE_TUNNEL_TOKEN", "TUNNEL_TOKEN", "CF_TUNNEL_TOKEN", "CLOUDFLARE_TOKEN",
    "NOTIFY_EMAIL", "RESEND_API_KEY", "SMTP_HOST", "SMTP_PORT", "SMTP_USER",
    "SMTP_PASS", "EMAIL_FROM", "SU_MAIL_SEND_TO", "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID", "HOST", "PORT", "SU_FETCH_ALLOWED_HOSTS", "SU_AGENT_NAME", "SU_SERVICE_NAME", "SU_TUNNEL_CONTAINER",
    "SU_SEARCH_LOCATION", "SU_CONTEXT_BUDGET", "SU_QUICK_MODEL", "SU_BUILD_MODEL", "SU_AUTOMATION_MODEL"
}


def default_project_for_key(k: str) -> str:
    ku = k.upper()
    if ku in SYSTEM_PROFILE_KEYS:
        return "profile"
    if ku.startswith("SU_") and not any(ku.endswith(x) for x in ("_MODEL", "_KEY", "_API_KEY")):
        return "profile"
    if any(x in ku for x in ("_API_KEY", "_LLM_URL", "_KEY", "_MODEL")) and not any(x in ku for x in ("PIPEDREAM_", "TINYFISH_")):
        return "ai"
    if any(x in ku for x in ("PIPEDREAM_", "TINYFISH_", "BROWSERLESS_")):
        return "tools"
    return "profile"


def secrets_by_group() -> Dict[str, List[str]]:
    names = sorted(k for k in env_file_keys() if k.isupper() and k not in HIDDEN_KEYS)
    d = projects_data(); by: Dict[str, List[str]] = {}
    for k in names:
        grp = d["keys"].get(k) or d["keys"].get(k.upper())
        if not grp:
            grp = default_project_for_key(k)
        by.setdefault(grp or "", []).append(k)
    return by


def secrets_block(projects: Optional[List[str]] = None) -> str:
    """Secret names for the prompt, grouped by project so the owner can say "use the PI keys". Values are never included.
    With `projects` (from the planner) only Profile, Other and those groups list their key names; the rest show a count
    and the worker calls project_keys for them. None = everything (planner, automations, channels)."""
    by = secrets_by_group(); d = projects_data()
    want = None if projects is None else {PROFILE, *projects}
    def line(gid, label, note=""):
        keys = by.get(gid) or []
        if not keys:
            return None
        if want is None or gid in want:
            return f"- {label}" + (f", {note}" if note else "") + ": " + ", ".join(keys)
        return f"- {label}: {len(keys)} keys"
    lines = [line(gid, label) for gid, label in BUILTIN_GROUPS.items()]
    lines += [line(p["id"], f"{p['name']} ({p['id']})", p.get("note", "")) for p in d["projects"]]
    if by.get(""):
        lines.append("- Other: " + ", ".join(by[""]))
    tail = "" if want is None else "\nproject_keys(id) lists the names of any group shown as a count."
    return "Secrets available to scripts as environment variables (names only; a project name means its keys):\n" + ("\n".join(l for l in lines if l) or "(none)") + tail
