"""URL and file path guards."""
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

from .config import HOME, WORKSPACE, env

DANGEROUS_PORTS = {
    21, 22, 23, 25, 53, 69, 110, 111, 135, 137, 138, 139, 143, 389, 445, 465, 587,
    993, 995, 1080, 1433, 1521, 2049, 2375, 2376, 3306, 3389, 5432, 5900, 6379,
    11211, 27017, 28017
}


def is_safe_url(url: str) -> Tuple[bool, str]:
    """Validate that a URL uses http(s) and does not resolve to private, loopback, or cloud metadata addresses (SSRF defense)."""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False, f"Unsupported scheme '{parsed.scheme}': only http and https are allowed."
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if port in DANGEROUS_PORTS:
            return False, f"Access to port {port} is blocked for security reasons."
        host = parsed.hostname
        if not host:
            return False, "Missing hostname in URL."
        host_clean = host.lower().strip("[]")
        if host_clean in {h.strip().lower() for h in env().get("SU_FETCH_ALLOWED_HOSTS", "").split(",") if h.strip()}:
            return True, ""  # explicitly allowed by the owner in Settings
        if host_clean in ("localhost", "local", "metadata", "instance-data") or host_clean.endswith((".localhost", ".local", ".internal")):
            return False, f"Access to host '{host}' is blocked for security."
        try:
            addrinfo = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
        except Exception as ex:
            return False, f"Could not resolve host '{host}': {ex}"
        for entry in addrinfo:
            ip_str = entry[4][0]
            ip = ipaddress.ip_address(ip_str)
            if ip.is_loopback:
                return False, f"Access to loopback address ({ip_str}) is forbidden."
            if ip.is_private:
                return False, f"Access to private network range ({ip_str}) is forbidden."
            if ip.is_link_local:
                return False, f"Access to link-local/cloud metadata IP ({ip_str}) is forbidden."
            if ip.is_reserved or ip.is_multicast or ip.is_unspecified:
                return False, f"Access to reserved IP ({ip_str}) is forbidden."
            if str(ip) in ("169.254.169.254", "fd00:ec2::254"):
                return False, "Access to cloud metadata service is forbidden."
        return True, ""
    except Exception as ex:
        return False, f"Invalid URL: {ex}"


def is_safe_file_path(raw_path: Union[str, Path], allow_write: bool = False) -> Tuple[bool, str, Path]:
    """Validate that a path accessed by agent file tools is safe and does not touch credentials, server source code, or system secrets."""
    try:
        raw_str = str(raw_path or "").strip()
        if not raw_str:
            return False, "Empty path provided.", WORKSPACE
        p = Path(raw_str).expanduser()
        if not p.is_absolute():
            p = WORKSPACE / p
        target = p.resolve()

        # 1. Block .env files
        if target.name == ".env" or target.name.startswith(".env.") or (HOME / ".env").resolve() == target:
            return False, "Access to environment secret files (.env) is forbidden.", target

        # 2. Block server code directory tampering or inspection
        server_dir = (HOME / "server").resolve()
        if target == server_dir or server_dir in target.parents:
            if allow_write:
                return False, "Writing or editing gateway server code is forbidden for security.", target
            if target.name in ("main.py", "agent.py", "channels.json") or (target.name.endswith(".py") and server_dir in target.parents):
                return False, "Access to server code is forbidden.", target

        # 3. Block SSH and GPG keys
        home_dir = Path.home().resolve()
        ssh_dir = (home_dir / ".ssh").resolve()
        gnupg_dir = (home_dir / ".gnupg").resolve()
        if target == ssh_dir or ssh_dir in target.parents:
            return False, "Access to SSH keys and credentials is forbidden.", target
        if target == gnupg_dir or gnupg_dir in target.parents:
            return False, "Access to GPG credentials is forbidden.", target

        # 4. Block user shell persistence files on write
        if allow_write and target in (home_dir / ".bashrc", home_dir / ".profile", home_dir / ".bash_profile", home_dir / ".zshrc"):
            return False, f"Modifying shell startup files ({target.name}) is forbidden.", target

        # 5. Block sensitive system files
        target_str = str(target)
        if target_str.startswith(("/etc/shadow", "/etc/sudoers", "/etc/security", "/root")):
            return False, f"Access to system sensitive path '{target}' is forbidden.", target

        return True, "", target
    except Exception as ex:
        return False, f"Invalid path '{raw_path}': {ex}", Path(str(raw_path))
