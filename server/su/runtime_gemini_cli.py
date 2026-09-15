"""Gemini CLI runtime."""
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
from .config import HOME, SKILLS_DIR, WORKSPACE, _write, env, now_iso
from .conversations import history_transcript, save_conversation
from .prompt import system_prompt
from .providers import gemini_cli_logged_in
from .scopes import expand
from .runtime_claude import cc_mcp_config

async def _gemini_write_settings():
    """Project-level .gemini/settings.json in the workspace: SU tools + custom MCP + Pipedream apps."""
    cfg = await cc_mcp_config()
    servers = {}
    for name, e in cfg["mcpServers"].items():
        entry = {"httpUrl" if e.get("type") == "http" else "url": e["url"]}
        if e.get("headers"):
            entry["headers"] = e["headers"]
        servers[name] = entry
    d = WORKSPACE / ".gemini"
    d.mkdir(exist_ok=True)
    _write(d / "settings.json", {"mcpServers": servers})
    link = d / "skills"
    try:
        if not link.exists():
            link.symlink_to(SKILLS_DIR, target_is_directory=True)
    except Exception:
        pass


async def run_gemini_cli(conv: Dict, user_input: str, model: str, on_event=None, extra_system: str = "", scope: str = "all") -> str:
    async def emit(ev: Dict):
        conv.setdefault("events", []).append({**ev, "t": now_iso()})
        if on_event:
            r = on_event(ev)
            if asyncio.iscoroutine(r):
                await r

    await _gemini_write_settings()
    earlier = history_transcript(conv)
    conv["messages"].append({"role": "user", "content": user_input}); save_conversation(conv)
    note = ("SU tools are available as MCP tools from server 'su': generate_image, generate_video, send_email, send_telegram, web_search, web_fetch, "
            "create_automation, list_automations, update_automation, delete_automation, create_skill, search_app_catalog, connect_app, list_app_tools. "
            "Use generate_image / generate_video for any image or video request instead of scripting media.\n")
    prompt = system_prompt(note + extra_system) + (f"\n\n---\nEarlier in this conversation:\n{earlier}" if earlier else "") + "\n\n---\nOwner request:\n" + user_input
    cmd = [_providers.GEMINI_BIN, "-p", prompt, "-o", "stream-json", "-m", model, "--skip-trust", "--approval-mode", "yolo" if "shell" in expand(scope) else "plan"]
    e = {**os.environ, **{k: v for k, v in env().items() if k.isupper()}, "HOME": str(HOME.parent), "TERM": "dumb",
         "PATH": str(Path.home() / ".local/bin") + ":" + os.environ.get("PATH", ""), "GEMINI_CLI_TRUST_WORKSPACE": "true"}
    if gemini_cli_logged_in() and env().get("SU_GEMINI_CLI_AUTH", "apikey") == "google":
        # Google retired the free "Code Assist for individuals" tier for Gemini CLI in Sep 2026 (UNSUPPORTED_CLIENT); opt in only if it returns
        e.pop("GEMINI_API_KEY", None)
        e["GOOGLE_GENAI_USE_GCA"] = "true"
    proc = await asyncio.create_subprocess_exec(*cmd, cwd=str(WORKSPACE), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=e)
    final, last_text, buf = "", "", ""
    await emit({"type": "status", "text": f"Gemini CLI ({model})"})
    try:
        while True:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=1800)
            if not line:
                break
            try:
                ev = json.loads(line)
            except Exception:
                continue
            t = ev.get("type")
            if t == "message" and ev.get("role") == "assistant":
                content = ev.get("content") or ""
                buf = (buf + content) if ev.get("delta") else content
            elif t == "tool_use":
                if buf.strip():
                    await emit({"type": "text", "text": buf}); last_text, buf = buf, ""
                await emit({"type": "tool_call", "name": ev.get("tool_name") or "tool", "args": ev.get("parameters") or {}})
            elif t == "tool_result":
                await emit({"type": "tool_result", "name": "", "output": str(ev.get("output") or ev.get("error") or "")[:1500]})
            elif t == "result":
                final = buf.strip() or last_text
                if ev.get("status") not in (None, "success"):
                    final = f"Gemini CLI error: {ev.get('error') or ev.get('status')}\n{final}"
                    await emit({"type": "error", "text": final})
                else:
                    await emit({"type": "final", "text": final})
        await proc.wait()
        if not final:
            err = (await proc.stderr.read()).decode("utf-8", "replace")[-800:]
            final = buf.strip() or last_text or f"Gemini CLI exited {proc.returncode}: {err}"
            await emit({"type": "final" if (buf.strip() or last_text) else "error", "text": final})
    except asyncio.TimeoutError:
        proc.kill()
        final = "Gemini CLI run timed out."
        await emit({"type": "error", "text": final})
    conv["messages"].append({"role": "assistant", "content": final})
    conv["cc_seen"] = len(conv["messages"])
    if not conv.get("title") or conv["title"] == "New chat":
        conv["title"] = user_input.strip().splitlines()[0][:60]
    save_conversation(conv)
    return final
