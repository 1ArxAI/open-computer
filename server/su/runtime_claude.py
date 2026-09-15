"""Claude Code CLI runtime."""
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
from .config import DATA, HOME, SKILLS_DIR, WORKSPACE, _write, env, now_iso
from .conversations import history_transcript, save_conversation
from .mcp import list_mcp
from .scopes import claude_disallow
from .pipedream import PD_MCP, pd_config, pd_connected_slugs, pd_headers
from .prompt import system_prompt

async def cc_mcp_config() -> Dict:
    """Same MCP servers and Pipedream apps the API runtime sees, as a Claude Code --mcp-config document."""
    servers: Dict[str, Any] = {}
    tok = env().get("SU_TOKEN", "")
    servers["su"] = {"type": "http", "url": f"http://127.0.0.1:{env().get('SU_PORT') or 8000}/mcp", "headers": ({"Authorization": f"Bearer {tok}"} if tok else {})}
    for srv in list_mcp():
        if not srv.get("enabled", True):
            continue
        entry: Dict[str, Any] = {"type": "sse" if srv.get("transport") == "sse" else "http", "url": srv["url"]}
        if srv.get("token"):
            entry["headers"] = {"Authorization": f"Bearer {srv['token']}"}
        servers[srv["slug"]] = entry
    if pd_config():
        try:
            for slug in await pd_connected_slugs():
                servers[f"app_{slug}"] = {"type": "http", "url": PD_MCP, "headers": await pd_headers(slug)}
        except Exception:
            pass
    return {"mcpServers": servers}


def _cc_ensure_skills_link():
    """Claude Code discovers skills in <cwd>/.claude/skills; point it at workspace/Skills."""
    link = WORKSPACE / ".claude" / "skills"
    try:
        link.parent.mkdir(exist_ok=True)
        if not link.exists():
            link.symlink_to(SKILLS_DIR, target_is_directory=True)
    except Exception:
        pass


async def run_claude_code(conv: Dict, user_input: str, model: str, on_event=None, extra_system: str = "", scope: str = "all", tier: str = "build", projects: Optional[List[str]] = None) -> str:
    async def emit(ev: Dict):
        conv.setdefault("events", []).append({**ev, "t": now_iso()})
        if on_event:
            r = on_event(ev)
            if asyncio.iscoroutine(r):
                await r

    _cc_ensure_skills_link()
    mcp_path = DATA / f"cc_mcp_{conv['id']}.json"
    _write(mcp_path, await cc_mcp_config())
    already = conv.pop("_user_appended", False)
    seen = conv.get("cc_seen", 0) if conv.get("cc_session") else 0
    unseen = history_transcript(conv, seen)
    prompt_input = (f"Earlier in this conversation (answered by other models):\n{unseen}\n\nOwner now says:\n{user_input}" if unseen else user_input)
    if not already:
        conv["messages"].append({"role": "user", "content": user_input}); save_conversation(conv)
    cc_note = ("SU tools are available as MCP tools named mcp__su__<tool>: generate_image, generate_video, send_email, send_telegram, web_search, "
               "web_fetch, create_automation, list_automations, update_automation, delete_automation, create_skill, search_app_catalog, connect_app, list_app_tools. "
               "Use mcp__su__generate_image for any image request and mcp__su__generate_video for any video request instead of drawing or scripting media.\n")
    if tier == "chat":
        cc_note = "Your tools are mcp__su__web_search and mcp__su__web_fetch.\n"
    cmd = [_providers.CLAUDE_BIN, "-p", prompt_input, "--output-format", "stream-json", "--verbose", "--model", model,
           "--dangerously-skip-permissions", "--append-system-prompt", system_prompt(cc_note + extra_system, tier=tier, projects=projects),
           "--mcp-config", str(mcp_path), "--strict-mcp-config"]
    if conv.get("cc_session"):
        cmd += ["--resume", conv["cc_session"]]
    disallow = claude_disallow(scope)
    if tier == "chat" or disallow == ["*"]:  # chat tier: no built-in tools (shell, files); the su MCP web tools stay
        cmd += ["--tools", ""]
    elif disallow:
        cmd += ["--disallowedTools", ",".join(disallow)]
    e = {**os.environ, **{k: v for k, v in env().items() if k.isupper()}, "HOME": str(HOME.parent), "TERM": "dumb",
         "PATH": str(Path.home() / ".local/bin") + ":" + os.environ.get("PATH", "")}
    proc = await asyncio.create_subprocess_exec(*cmd, cwd=str(WORKSPACE), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=e,
                                                limit=32 * 1024 * 1024)  # a stream-json line carries a whole tool result; asyncio's default 64 KiB raised ValueError
    final, last_text = "", ""
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
            if t == "assistant":
                for c in ev.get("message", {}).get("content", []):
                    if c.get("type") == "text" and c.get("text"):
                        last_text = c["text"]
                        await emit({"type": "text", "text": c["text"]})
                    elif c.get("type") == "tool_use":
                        await emit({"type": "tool_call", "name": c.get("name", "tool"), "args": c.get("input") or {}})
            elif t == "user":
                content = ev.get("message", {}).get("content")
                for c in content if isinstance(content, list) else []:
                    if c.get("type") == "tool_result":
                        out = c.get("content")
                        if isinstance(out, list):
                            out = "\n".join(x.get("text", "") for x in out if isinstance(x, dict))
                        await emit({"type": "tool_result", "name": "", "output": str(out or "")[:1500]})
            elif t == "result":
                conv["cc_session"] = ev.get("session_id") or conv.get("cc_session")
                final = ev.get("result") if isinstance(ev.get("result"), str) and ev.get("result") else last_text
                if ev.get("subtype", "").startswith("error") or ev.get("is_error"):
                    final = f"Claude Code error: {final or ev.get('subtype')}"
                    await emit({"type": "error", "text": final})
                else:
                    await emit({"type": "final", "text": final, "streamed": final == last_text})  # the last text event already showed it; the UI must not render it twice
        await proc.wait()
        if not final:
            err = (await proc.stderr.read()).decode("utf-8", "replace")[-800:]
            final = f"Claude Code exited {proc.returncode}: {err}"
            await emit({"type": "error", "text": final})
    except asyncio.TimeoutError:
        proc.kill()
        final = "Claude Code run timed out."
        await emit({"type": "error", "text": final})
    finally:
        try:
            mcp_path.unlink()
        except Exception:
            pass
    conv["messages"].append({"role": "assistant", "content": final})
    if not conv.get("title") or conv["title"] == "New chat":
        conv["title"] = user_input.strip().splitlines()[0][:60]
    save_conversation(conv)
    return final



# ---------------------------------------------------------------- fast no-tools pass (Claude Code haiku, ~3 s): first reply / planner / small helpers

async def cc_quick(prompt: str, system: str = "", model: str = "haiku", timeout: int = 60) -> str:
    empty = DATA / "cc_empty_mcp.json"
    if not empty.exists():
        _write(empty, {"mcpServers": {}})
    cmd = [_providers.CLAUDE_BIN, "-p", prompt, "--output-format", "json", "--model", model, "--tools", "", "--strict-mcp-config", "--mcp-config", str(empty)]
    if system:
        cmd += ["--append-system-prompt", system]
    e = {**os.environ, "HOME": str(HOME.parent), "TERM": "dumb", "PATH": str(Path.home() / ".local/bin") + ":" + os.environ.get("PATH", "")}
    proc = await asyncio.create_subprocess_exec(*cmd, cwd=str(WORKSPACE), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=e)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError("quick pass timed out")
    try:
        j = json.loads(out.decode("utf-8", "replace"))
        return (j.get("result") or "").strip()
    except Exception:
        raise RuntimeError(f"quick pass failed: {err.decode('utf-8', 'replace')[-300:]}")
