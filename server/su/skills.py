"""Skill folders, catalog and install."""
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

from .config import SKILLS_DIR, SKILLS_MANIFEST
from .security import is_safe_url

# skills -----------------------------------------------------------------------


def _frontmatter(text: str) -> Dict:
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
    if not m:
        return {"body": text}
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except Exception:
        meta = {}
    meta["body"] = m.group(2)
    return meta


_skills_cache: Dict[str, Any] = {}


def list_skills() -> List[Dict]:
    try:
        sig = tuple(sorted((d.name, (d / "SKILL.md").stat().st_mtime) for d in SKILLS_DIR.iterdir() if (d / "SKILL.md").exists()))
    except Exception:
        sig = ()
    if _skills_cache.get("sig") == sig:
        return _skills_cache["v"]
    out = []
    for d in sorted(SKILLS_DIR.iterdir()) if SKILLS_DIR.exists() else []:
        f = d / "SKILL.md"
        if d.is_dir() and f.exists():
            meta = _frontmatter(f.read_text(encoding="utf-8", errors="replace"))
            out.append({"name": meta.get("name") or d.name, "dir": d.name, "description": str(meta.get("description", ""))[:400],
                        "path": str(f), "metadata": meta.get("metadata") or {},
                        "scripts": sorted(p.name for p in (d / "scripts").glob("*")) if (d / "scripts").exists() else []})
    _skills_cache.update(sig=sig, v=out)
    return out


def read_skill(name: str) -> str:
    for s in list_skills():
        if name in (s["name"], s["dir"]):
            body = Path(s["path"]).read_text(encoding="utf-8", errors="replace")
            files = "\n".join(str(p.relative_to(SKILLS_DIR / s["dir"])) for p in (SKILLS_DIR / s["dir"]).rglob("*") if p.is_file())
            return f"# Skill folder: {SKILLS_DIR / s['dir']}\n# Files:\n{files}\n\n{body}"
    return f"No skill named {name}. Installed: " + ", ".join(s["name"] for s in list_skills())


def create_skill(name: str, description: str, body: str, scripts: Optional[Dict[str, str]] = None) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")
    d = SKILLS_DIR / slug
    d.mkdir(parents=True, exist_ok=True)
    fm = f"---\nname: {slug}\ndescription: {json.dumps(description)}\nmetadata:\n  author: su\n---\n\n"
    (d / "SKILL.md").write_text(fm + body.strip() + "\n", encoding="utf-8")
    for fn, content in (scripts or {}).items():
        p = d / "scripts" / Path(fn).name
        p.parent.mkdir(exist_ok=True)
        p.write_text(content, encoding="utf-8")
        p.chmod(0o755)
    return str(d)


_catalog_cache: Dict[str, Any] = {}


async def skills_catalog() -> Dict:
    if _catalog_cache and time.time() - _catalog_cache["t"] < 3600:
        return _catalog_cache["data"]
    safe, err = is_safe_url(SKILLS_MANIFEST)
    if not safe:
        raise ValueError(f"Unsafe skills manifest URL: {err}")
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as c:
        data = (await c.get(SKILLS_MANIFEST)).json()
    _catalog_cache.update(t=time.time(), data=data)
    return data


async def install_skill(slug: str) -> str:
    cat = await skills_catalog()
    entry = next((s for s in cat.get("skills", []) if s.get("slug") == slug or s.get("name") == slug), None)
    if not entry:
        raise ValueError(f"Skill '{slug}' not in catalog")
    tar_url = cat.get("tarball_url", "")
    if not tar_url:
        raise ValueError("Skills catalog has no tarball_url configured")
    safe, err = is_safe_url(tar_url)
    if not safe:
        raise ValueError(f"Unsafe skill tarball URL '{tar_url}': {err}")
    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as c:
        resp = await c.get(tar_url)
        if resp.status_code != 200:
            raise RuntimeError(f"Failed to download skill archive (HTTP {resp.status_code})")
        blob = resp.content

    skill_folder_name = Path(entry["path"]).name or entry.get("slug") or entry.get("name")
    dest = SKILLS_DIR / skill_folder_name
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True, exist_ok=True)

    entry_path = entry.get("path", "").strip("/")
    installed_count = 0

    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tf:
        members = tf.getmembers()
        # Find prefix for this skill within tarball members
        prefix = None
        for mem in members:
            if f"/{entry_path}/" in mem.name or mem.name.endswith(f"/{entry_path}") or mem.name.startswith(f"{entry_path}/"):
                parts = mem.name.split(entry_path, 1)
                prefix = parts[0] + entry_path + "/"
                break
        if not prefix:
            archive_root = cat.get("archive_root", "skills-main").strip("/")
            prefix = f"{archive_root}/{entry_path}/" if archive_root else f"{entry_path}/"

        for mem in members:
            if mem.name.startswith(prefix) and mem.isfile():
                rel = mem.name[len(prefix):]
                if not rel or ".." in rel:
                    continue
                out = dest / rel
                out.parent.mkdir(parents=True, exist_ok=True)
                extracted = tf.extractfile(mem)
                if extracted:
                    out.write_bytes(extracted.read())
                    if rel.endswith(".py") or rel.endswith(".sh") or "scripts" in rel:
                        out.chmod(0o755)
                    installed_count += 1

    if installed_count == 0:
        raise RuntimeError(f"No files found in archive for skill '{slug}' under path '{entry_path}'")

    _skills_cache.clear()
    return str(dest)
