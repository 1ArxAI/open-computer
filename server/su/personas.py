"""Saved agents (personas) and tool scopes."""
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

from .config import DATA, _read, _write, now_iso

# agents (Zo personas): name + instructions + default model + tool scope ----------

AGENTS_DIR = DATA / "agents"
AGENTS_DIR.mkdir(parents=True, exist_ok=True)
SCOPES = {"all": None,
          "workspace": {"run_command", "send_email", "send_telegram", "create_task", "control_task"},  # files, web, apps; no shell, no comms
          "read": {"run_command", "write_file", "send_email", "send_telegram", "create_skill", "create_automation", "update_automation", "delete_automation", "create_agent", "create_task", "control_task"},
          "chat": "*"}  # chat = no tools at all
CC_SCOPE_DISALLOW = {"workspace": ["Bash"], "read": ["Bash", "Write", "Edit", "NotebookEdit"], "chat": ["*"]}


def list_agents() -> List[Dict]:
    items = [a for a in (_read(p) for p in AGENTS_DIR.glob("*.json")) if a]
    items.sort(key=lambda a: a.get("name", "").lower())
    return items


def get_agent(aid: Optional[str]) -> Optional[Dict]:
    if not aid:
        return None
    a = _read(AGENTS_DIR / f"{aid}.json")
    if a:
        return a
    return next((x for x in list_agents() if x.get("handle") == aid or x.get("name") == aid), None)


def save_agent(a: Dict) -> Dict:
    a.setdefault("id", "ag_" + uuid.uuid4().hex[:8])
    a["handle"] = re.sub(r"[^a-z0-9]+", "-", (a.get("handle") or a.get("name", "agent")).lower()).strip("-")
    a.setdefault("scope", "all")
    a.setdefault("model", None)
    a.setdefault("created", now_iso())
    _write(AGENTS_DIR / f"{a['id']}.json", a)
    return a


def delete_agent(aid: str) -> bool:
    p = AGENTS_DIR / f"{aid}.json"
    if p.exists():
        p.unlink()
        return True
    return False


def scope_filter(tools: List[Dict], scope: str) -> List[Dict]:
    block = SCOPES.get(scope or "all")
    if block is None:
        return tools
    if block == "*":
        return []
    out = []
    for t in tools:
        n = t["function"]["name"]
        if n in block:
            continue
        if scope == "read" and (n.startswith("app__") or n.startswith("mcp__")) and not any(k in n for k in ("get", "list", "find", "search", "read", "retrieve")):
            continue
        out.append(t)
    return out


def agent_system_extra(a: Optional[Dict]) -> str:
    if not a:
        return ""
    return f"You are acting as the agent '{a['name']}'. Follow these instructions above all else:\n{a.get('prompt', '')}\n"
