"""Remote MCP servers the agent can call."""
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

from .config import MCP_FILE, _read, _write, now_iso

# MCP --------------------------------------------------------------------------


def list_mcp() -> List[Dict]:
    return _read(MCP_FILE, [])


def save_mcp(servers: List[Dict]):
    _write(MCP_FILE, servers)


async def mcp_session(server: Dict):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    from mcp.client.sse import sse_client
    from mcp.shared._httpx_utils import create_mcp_http_client
    headers = dict(server.get("headers") or {})
    if server.get("token"):
        headers["Authorization"] = f"Bearer {server['token']}"
    if server.get("transport") == "sse":
        return sse_client(server["url"], headers=headers), ClientSession
    return streamable_http_client(server["url"], http_client=create_mcp_http_client(headers=headers)), ClientSession


async def mcp_reload(server: Dict) -> Dict:
    """Connect once, record the tool list on the server entry (mirrors Zo's last_reload_*)."""
    try:
        ctx, ClientSession = await mcp_session(server)
        async with ctx as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                tools = (await session.list_tools()).tools
                server["tools"] = [{"name": t.name, "description": (t.description or "")[:300],
                                    "schema": getattr(t, "input_schema", None) or getattr(t, "inputSchema", None) or {"type": "object", "properties": {}}} for t in tools]
        server["status"] = "ok"
        server["error"] = None
    except BaseException as e:  # ExceptionGroup from anyio task groups
        server["status"] = "error"
        server["error"] = _root_error(e)[:300]
    server["last_reload"] = now_iso()
    return server


def _root_error(e: BaseException) -> str:
    subs = getattr(e, "exceptions", None)
    if subs:
        return _root_error(subs[0])
    return f"{type(e).__name__}: {e}"


async def mcp_call(server: Dict, tool: str, args: Dict) -> str:
    ctx, ClientSession = await mcp_session(server)
    async with ctx as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            res = await session.call_tool(tool, args)
            parts = []
            for c in res.content:
                parts.append(getattr(c, "text", None) or json.dumps(getattr(c, "model_dump", lambda: str(c))(), default=str))
            return "\n".join(parts)[:20000]


def mcp_tool_specs(servers: List[Dict]) -> List[Dict]:
    specs = []
    for s in servers:
        if not s.get("enabled", True):
            continue
        for t in s.get("tools") or []:
            if t["name"] in (s.get("disabled_tools") or []):
                continue
            schema = t.get("schema") or {"type": "object", "properties": {}}
            specs.append({"type": "function", "function": {"name": f"mcp__{s['slug']}__{t['name']}",
                          "description": f"[{s['name']}] {t['description']}", "parameters": schema}})
    return specs
