"""Durable facts kept across chats."""
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

from .config import DATA, _read, _write

# ---------------------------------------------------------------- memory: a few durable facts per chat, kept as JSON, injected into every prompt

MEMORY_FILE = DATA / "memory.json"
MEMORY_MAX = 120
_mem_lock = asyncio.Lock()


def memory_facts() -> List[Dict]:
    return (_read(MEMORY_FILE, {}) or {}).get("facts", [])


def memory_text() -> str:
    facts = memory_facts()[-MEMORY_MAX:]
    return ("Memory (facts learned in earlier chats):\n" + "\n".join(f"- {f['text']}" for f in facts)) if facts else ""


def forget(fid: str) -> bool:
    facts = memory_facts(); keep = [f for f in facts if f["id"] != fid]
    if len(keep) != len(facts):
        _write(MEMORY_FILE, {"facts": keep})
    return len(keep) != len(facts)
