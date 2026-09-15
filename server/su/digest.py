"""Condensed rules and manual for the small-task tier."""
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

from .config import RULES_FILE, WORKSPACE

DIGEST_FILE = WORKSPACE / "SU-digest.md"


def _digest_source() -> str:
    rules = RULES_FILE.read_text(encoding="utf-8") if RULES_FILE.exists() else ""
    manual = (WORKSPACE / "SU.md").read_text(encoding="utf-8") if (WORKSPACE / "SU.md").exists() else ""
    return (rules + "\n\n" + manual).strip()


def _digest_hash() -> str:
    return hashlib.sha1(_digest_source().encode("utf-8")).hexdigest()[:12]


def digest_text() -> str:
    """Condensed rules + manual (about 250 tokens), generated once by the build model; empty until generated or when the sources changed."""
    if not DIGEST_FILE.exists():
        return ""
    first, _, body = DIGEST_FILE.read_text(encoding="utf-8").partition("\n")
    return body.strip() if first.strip() == f"<!-- source:{_digest_hash()} -->" else ""
