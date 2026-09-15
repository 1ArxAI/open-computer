"""Pipedream Connect apps."""
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

from .config import DATA, _read, _tool, _write, env
from .mcp import mcp_call, mcp_reload

# ---------------------------------------------------------------- Pipedream Connect (managed OAuth apps, same as Zo)

PD_API = "https://api.pipedream.com/v1"
PD_MCP = "https://remote.mcp.pipedream.net/v3"
PD_USER = "su"  # single owner; external_user_id on Pipedream
PD_FILE = DATA / "pipedream.json"
PD_FEATURED = ["gmail", "google_calendar", "google_drive", "google_sheets", "google_tasks", "notion", "linear", "airtable_oauth",
               "dropbox", "microsoft_outlook", "microsoft_outlook_calendar", "microsoft_onedrive", "spotify", "slack_v2", "github",
               "twitter", "telegram_bot_api", "discord", "stripe", "trello", "todoist", "hubspot"]
_pd_token: Dict[str, Any] = {}


def pd_config() -> Optional[Dict[str, str]]:
    e = env()
    if not (e.get("PIPEDREAM_CLIENT_ID") and e.get("PIPEDREAM_CLIENT_SECRET") and e.get("PIPEDREAM_PROJECT_ID")):
        return None
    return {"client_id": e["PIPEDREAM_CLIENT_ID"], "client_secret": e["PIPEDREAM_CLIENT_SECRET"],
            "project_id": e["PIPEDREAM_PROJECT_ID"], "environment": e.get("PIPEDREAM_ENVIRONMENT", "development")}


async def pd_token() -> str:
    cfg = pd_config()
    if not cfg:
        raise RuntimeError("Pipedream is not configured. Add PIPEDREAM_CLIENT_ID, PIPEDREAM_CLIENT_SECRET, PIPEDREAM_PROJECT_ID in Settings > Keys.")
    if _pd_token and time.time() < _pd_token["exp"]:
        return _pd_token["tok"]
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(PD_API + "/oauth/token", json={"grant_type": "client_credentials", "client_id": cfg["client_id"], "client_secret": cfg["client_secret"]})
    r.raise_for_status()
    j = r.json()
    _pd_token.update(tok=j["access_token"], exp=time.time() + int(j.get("expires_in", 3600)) - 300)
    return _pd_token["tok"]


async def pd_headers(app: Optional[str] = None) -> Dict[str, str]:
    cfg = pd_config()
    h = {"Authorization": f"Bearer {await pd_token()}", "x-pd-environment": cfg["environment"]}
    if app:
        h.update({"x-pd-project-id": cfg["project_id"], "x-pd-external-user-id": PD_USER, "x-pd-app-slug": app, "x-pd-tool-mode": "tools-only"})
    return h


def _pd_app(x: Dict) -> Dict:
    return {"slug": x.get("name_slug"), "name": x.get("name"), "desc": (x.get("description") or "")[:200],
            "auth_type": x.get("auth_type"), "icon": x.get("img_src"), "categories": x.get("categories") or []}


_pd_cache: Dict[str, Any] = {}


async def pd_apps(q: str = "", limit: int = 24) -> List[Dict]:
    key = f"q:{q.lower()}:{limit}"
    hit = _pd_cache.get(key)
    if hit and time.time() - hit["t"] < 3600:
        return hit["v"]
    if not q:  # featured catalog survives restarts: it changes about never, and refetching it cold cost 5 s per page open
        st = _pd_store(); cat = st.get("featured") or {}
        if cat.get("apps") and time.time() - cat.get("t", 0) < 86400:
            _pd_cache[key] = {"t": cat["t"], "v": cat["apps"]}
            return cat["apps"]
    h = await pd_headers()
    async with httpx.AsyncClient(timeout=30) as c:
        if q:
            r = await c.get(PD_API + "/apps", params={"q": q, "limit": limit}, headers=h)
            apps = [_pd_app(x) for x in r.json().get("data", [])]
        else:
            rs = await asyncio.gather(*(c.get(f"{PD_API}/apps/{slug}", headers=h) for slug in PD_FEATURED), return_exceptions=True)
            apps = [_pd_app(r.json()["data"]) for r in rs if not isinstance(r, Exception) and r.status_code == 200 and r.json().get("data")]
            if apps:
                st = _pd_store(); st["featured"] = {"t": time.time(), "apps": apps}; _write(PD_FILE, st)
    _pd_cache[key] = {"t": time.time(), "v": apps}
    return apps


_pd_accounts_cache: Dict[str, Any] = {}


async def pd_accounts(force: bool = False) -> List[Dict]:
    """Connected accounts; cached 60 s (about 0.5 s per call otherwise). force=True after a connect or disconnect."""
    cfg = pd_config()
    if not cfg:
        return []
    if not force and _pd_accounts_cache and time.time() - _pd_accounts_cache["t"] < 60:
        return _pd_accounts_cache["v"]
    h = await pd_headers()
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(f"{PD_API}/connect/{cfg['project_id']}/accounts", params={"external_user_id": PD_USER}, headers=h)
    out = []
    for a in r.json().get("data", []):
        app = a.get("app") or {}
        out.append({"id": a.get("id"), "name": a.get("name"), "healthy": a.get("healthy", True), "created": a.get("created_at"),
                    "slug": app.get("name_slug"), "app_name": app.get("name"), "icon": app.get("img_src")})
    _pd_accounts_cache.update(t=time.time(), v=out)
    return out


async def pd_resolve_slug(name: str) -> str:
    """Accept 'gmail', 'Gmail', 'mail', 'x'... and return a real Pipedream slug (prefers OAuth apps)."""
    name = name.strip().lower().replace(" ", "_")
    name = {"x": "twitter", "x_twitter": "twitter", "twitter_x": "twitter", "google_mail": "gmail", "gcal": "google_calendar",
            "calendar": "google_calendar", "drive": "google_drive", "sheets": "google_sheets", "slack": "slack_v2",
            "airtable": "airtable_oauth", "onedrive": "microsoft_onedrive", "outlook": "microsoft_outlook"}.get(name, name)
    h = await pd_headers()
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(f"{PD_API}/apps/{name}", headers=h)
        if r.status_code == 200 and r.json().get("data"):
            return r.json()["data"]["name_slug"]
        r = await c.get(PD_API + "/apps", params={"q": name, "limit": 10}, headers=h)
        data = r.json().get("data", [])
    if not data:
        raise ValueError(f"No app named {name!r} in the Pipedream catalog; try search_app_catalog.")
    exact = [x for x in data if x.get("name", "").lower() == name.replace("_", " ")]
    oauth = [x for x in (exact or data) if x.get("auth_type") == "oauth"]
    return (oauth or exact or data)[0]["name_slug"]


async def pd_connect_link(app: str) -> str:
    app = await pd_resolve_slug(app)
    cfg = pd_config()
    h = await pd_headers()
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{PD_API}/connect/{cfg['project_id']}/tokens", headers=h,
                         json={"external_user_id": PD_USER, "allowed_origins": [o for o in (env().get("SU_PUBLIC_URL", "").rstrip("/"), f"http://localhost:{env().get('SU_PORT') or 8000}") if o]})
    r.raise_for_status()
    url = r.json()["connect_link_url"] + "&app=" + app
    # Apps without a shared Pipedream OAuth client (X/Twitter) need your own client: PIPEDREAM_OAUTH_APP_IDS="twitter:oa_xxx,slack_v2:oa_yyy"
    for pair in env().get("PIPEDREAM_OAUTH_APP_IDS", "").split(","):
        if ":" in pair and pair.split(":", 1)[0].strip() == app:
            url += "&oauthAppId=" + pair.split(":", 1)[1].strip()
    return url


async def pd_delete_account(account_id: str) -> bool:
    cfg = pd_config()
    h = await pd_headers()
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.delete(f"{PD_API}/connect/{cfg['project_id']}/accounts/{account_id}", headers=h)
    return r.status_code in (200, 204)


def _pd_store() -> Dict:
    return _read(PD_FILE, {"tools": {}})


async def pd_app_tools(app: str, refresh: bool = False) -> List[Dict]:
    store = _pd_store()
    hit = store["tools"].get(app)
    if hit and not refresh and time.time() - hit["t"] < 86400:
        return hit["tools"]
    server = {"url": PD_MCP, "transport": "http", "headers": await pd_headers(app)}
    server = await mcp_reload(server)
    if server.get("status") != "ok":
        raise RuntimeError(server.get("error") or "MCP error")
    store["tools"][app] = {"t": time.time(), "tools": server["tools"]}
    _write(PD_FILE, store)
    return server["tools"]


async def pd_call(app: str, tool: str, args: Dict) -> str:
    server = {"url": PD_MCP, "transport": "http", "headers": await pd_headers(app)}
    return await mcp_call(server, tool, args)


_pd_slugs_cache: Dict[str, Any] = {}


async def pd_connected_slugs(force: bool = False) -> List[str]:
    if not force and _pd_slugs_cache and time.time() - _pd_slugs_cache["t"] < 120:
        return _pd_slugs_cache["v"]
    try:
        v = sorted({a["slug"] for a in await pd_accounts() if a.get("slug")})
    except Exception:
        v = _pd_slugs_cache.get("v", [])
    _pd_slugs_cache.update(t=time.time(), v=v)
    return v


async def pd_tool_specs() -> List[Dict]:
    """Zo style: one use_app tool + list_app_tools, not every app function (keeps the prompt small)."""
    slugs = await pd_connected_slugs()
    if not slugs:
        return []
    return [_tool("use_app", f"Call a tool of a connected app via Pipedream. Connected apps: {', '.join(slugs)}. Call list_app_tools(app) first to see tool names and their parameters.",
                  {"app": {"type": "string"}, "tool": {"type": "string"}, "args": {"type": "object", "description": "Tool parameters"}}, ["app", "tool"])]


def _app_tools():
  return [
    _tool("search_app_catalog", "Search the 3,000+ app catalog (Gmail, Calendar, Notion, X, Slack...) available through Pipedream Connect.", {"query": {"type": "string"}}),
    _tool("connect_app", "Get a one-click OAuth link so the owner can connect an app account. Return the link to the owner and ask them to open it; tools appear after they finish.", {"app_slug": {"type": "string"}}),
    _tool("list_app_tools", "List the tools available for a connected app (or any app slug) via Pipedream.", {"app_slug": {"type": "string"}}),
  ]
