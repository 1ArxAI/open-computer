"""Paths, environment, timezone, agent name and the tiny json store helpers."""
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

log = logging.getLogger("su")

HOME =Path(os.environ.get("SU_HOME", str(Path(__file__).resolve().parent.parent)))  # the install folder
WORKSPACE = HOME / "workspace"
SKILLS_DIR = WORKSPACE / "Skills"
DATA = HOME / "data" / "su"
CONV_DIR = DATA / "conversations"
AUTO_DIR = DATA / "automations"
TASK_DIR = DATA / "tasks"
RUNS_DIR = DATA / "runs"
MCP_FILE = DATA / "mcp.json"
PROVIDERS_FILE = DATA / "providers.json"
RULES_FILE = WORKSPACE / "RULES.md"
MAX_STEPS = 40
SKILLS_MANIFEST = "https://raw.githubusercontent.com/zocomputer/skills/main/manifest.json"

for d in (DATA, CONV_DIR, AUTO_DIR, RUNS_DIR, SKILLS_DIR, TASK_DIR):
    d.mkdir(parents=True, exist_ok=True)


def _read(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write(p: Path, obj):
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)

KEY_ALIASES = {
    "XAI_API_KEY": ["GROK_API_KEY", "X_AI_API_KEY"],
    "ANTHROPIC_API_KEY": ["CLAUDE_API_KEY"],
    "GEMINI_API_KEY": ["GOOGLE_API_KEY", "PALM_API_KEY"],
    "OPENAI_API_KEY": ["OPEN_AI_KEY", "OPENAI_KEY"],
    "LOCAL_LLM_URL": ["OLLAMA_URL", "OLLAMA_BASE_URL", "VLLM_URL"],
    "OPENROUTER_API_KEY": ["OPENROUTER_KEY"],
    "FIREWORKS_API_KEY": ["FIREWORKS_KEY"],
    "DEEPINFRA_API_KEY": ["DEEPINFRA_KEY"],
    "TOGETHER_API_KEY": ["TOGETHER_KEY"],
    "MISTRAL_API_KEY": ["MISTRAL_KEY"],
    "GROQ_API_KEY": ["GROQ_KEY"],
    "DEEPSEEK_API_KEY": ["DEEPSEEK_KEY"],
}


_ENV_FILE_KEYS_AT_START: Optional[set] = None


def env() -> Dict[str, str]:
    """os.environ (systemd loads .env at service start) overlaid with the live .env; keys deleted from .env since start vanish."""
    global _ENV_FILE_KEYS_AT_START
    e = dict(os.environ)
    p = HOME / ".env"
    current_keys = set()
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k = line.split("=", 1)[0].strip()
                current_keys.add(k); current_keys.add(k.upper())
    if _ENV_FILE_KEYS_AT_START is None:
        _ENV_FILE_KEYS_AT_START = set(current_keys)
    for k in _ENV_FILE_KEYS_AT_START - current_keys:
        e.pop(k, None)
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                e[k] = v
                e.setdefault(k.upper(), v)  # secrets are case-insensitive: gemini_api_key also serves GEMINI_API_KEY
    return e


TZ = ZoneInfo(env().get("SU_TZ") or "UTC")
AGENT_NAME = env().get("SU_AGENT_NAME") or "SU"  # what the assistant calls itself in prompts and the UI


def env_file_keys() -> List[str]:
    """Key names in .env. (systemd loads .env into os.environ, so "not in os.environ" is no way to tell secrets from system vars.)"""
    p = HOME / ".env"
    if not p.exists():
        return []
    return [l.split("=", 1)[0].strip() for l in p.read_text(encoding="utf-8").splitlines() if l.strip() and not l.strip().startswith("#") and "=" in l]

# ---------------------------------------------------------------- json stores


def now_iso() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


# ---------------------------------------------------------------- subprocess helpers


def _ws_path(path: str) -> Path:
    p = Path(path).expanduser()
    return p if p.is_absolute() else WORKSPACE / p


# ---------------------------------------------------------------- tools


def _tool(name, desc, props, required=None):
    return {"type": "function", "function": {"name": name, "description": desc,
            "parameters": {"type": "object", "properties": props, "required": required or list(props)}}}
