"""Tool specs, execute_tool, tool tiers, and the SU-as-MCP tool list."""
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

from .automations import delete_automation, get_automation, list_automations, save_automation
from .config import WORKSPACE, _tool
from .mcp import list_mcp, mcp_call
from .media import generate_image, generate_video
from .personas import get_agent, list_agents, save_agent
from .pipedream import _app_tools, pd_app_tools, pd_apps, pd_call, pd_config, pd_connect_link, pd_resolve_slug
from .projects import BUILTIN_GROUPS, projects_data, secrets_by_group
from .security import is_safe_file_path
from .skills import create_skill, read_skill
from .tasks import _proc_env, delete_task, get_task, list_tasks, save_task, start_task, stop_task, task_logs
from .web import _web_fetch, _web_search, send_email, send_telegram

BUILTIN_TOOLS = [
    _tool("run_command", "Run a bash command on this box. Returns output. 120s timeout.",
          {"command": {"type": "string"}, "cwd": {"type": "string", "description": "Working directory, default workspace"}}, ["command"]),
    _tool("read_file", "Read a text file.", {"path": {"type": "string"}}),
    _tool("write_file", "Write a text file (creates parent folders).", {"path": {"type": "string"}, "content": {"type": "string"}}),
    _tool("list_dir", "List a directory.", {"path": {"type": "string"}}),
    _tool("edit_file", "Replace an exact text span in a file (old must occur exactly once). Use for small precise edits instead of rewriting whole files.",
          {"path": {"type": "string"}, "old": {"type": "string"}, "new": {"type": "string"}}),
    _tool("grep", "Search file contents recursively (regex). Returns file:line:text, max 200 lines.", {"pattern": {"type": "string"}, "path": {"type": "string", "description": "Folder to search, default workspace"}}, ["pattern"]),
    _tool("glob", "Find files by glob pattern, e.g. Projects/**/*.json", {"pattern": {"type": "string"}}),
    _tool("web_fetch", "Fetch a URL and return readable text (HTML stripped).", {"url": {"type": "string"}}),
    _tool("web_search", "Search the web. Returns titles, URLs, snippets, dates. For news or current events set topic=news and time_range=day or week.",
          {"query": {"type": "string"}, "time_range": {"type": "string", "enum": ["anytime", "day", "week", "month", "year"], "description": "Recency window, default anytime"},
           "topic": {"type": "string", "enum": ["general", "news"], "description": "Search index, default general"}}, ["query"]),
    _tool("send_email", "Send an email to the owner (or a recipient). Uses SMTP_* or RESEND_API_KEY secrets.",
          {"subject": {"type": "string"}, "body": {"type": "string"}, "to": {"type": "string", "description": "Optional; defaults to NOTIFY_EMAIL"}}, ["subject", "body"]),
    _tool("send_telegram", "Send a Telegram message to the owner (TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID).", {"text": {"type": "string"}}),
    _tool("generate_image", "Generate an image with the configured image model and save it as a file. Use this whenever the owner asks for an image, logo, illustration, thumbnail or picture; do not draw with code.",
          {"prompt": {"type": "string"}, "path": {"type": "string", "description": "Optional output path (.png), default Projects/media/"},
           "reference": {"type": "string", "description": "Optional path of an image to edit or use as reference"}}, ["prompt"]),
    _tool("generate_video", "Generate a short video clip with the configured video model and save it as a file. Use this whenever the owner asks for a video; do not assemble videos with ffmpeg unless they explicitly ask for that.",
          {"prompt": {"type": "string"}, "path": {"type": "string", "description": "Optional output path (.mp4)"},
           "image": {"type": "string", "description": "Optional starting image path"}, "seconds": {"type": "integer", "description": "Clip length, default 5"}}, ["prompt"]),
    _tool("read_skill", "Read the full SKILL.md and file list of an installed skill. Call before using a skill.", {"name": {"type": "string"}}),
    _tool("create_skill", "Create or overwrite a skill folder Skills/<name>/SKILL.md (+ optional scripts).",
          {"name": {"type": "string"}, "description": {"type": "string"}, "body": {"type": "string", "description": "Markdown instructions"},
           "scripts": {"type": "object", "description": "filename -> file content", "additionalProperties": {"type": "string"}}}, ["name", "description", "body"]),
    _tool("create_automation", "Create a scheduled automation. schedule is JSON: {type:interval,minutes} | {type:times,times:['08:00'],days:[0-6 Mon=0]} | {type:monthly,day:1-28,time:'09:00'} | {type:once,at:'YYYY-MM-DDTHH:MM'}",
          {"name": {"type": "string"}, "prompt": {"type": "string", "description": "Full instructions the agent follows on each run"},
           "schedule": {"type": "object"}, "notify": {"type": "string", "enum": ["none", "email", "telegram"]},
           "model": {"type": "string", "description": "provider:model, optional"},
           "agent": {"type": "string", "description": "id or handle of a saved agent to run as, optional"}}, ["name", "prompt", "schedule"]),
    _tool("list_automations", "List automations with schedule and status.", {}),
    _tool("list_agents", "List saved agents (personas) that chats and automations can run as.", {}),
    _tool("create_agent", "Create or update a reusable agent (persona): identity + instructions, optional default model, tool scope.",
          {"name": {"type": "string"}, "prompt": {"type": "string", "description": "Who the agent is and how it must work"},
           "model": {"type": "string", "description": "provider:model, optional"},
           "scope": {"type": "string", "enum": ["all", "workspace", "read", "chat"], "description": "all=every tool; workspace=no shell/comms; read=read-only; chat=no tools"}}, ["name", "prompt"]),
    _tool("update_automation", "Update fields of an automation by id.",
          {"id": {"type": "string"}, "fields": {"type": "object", "description": "Any of name,prompt,schedule,notify,model,enabled"}}),
    _tool("delete_automation", "Delete an automation by id.", {"id": {"type": "string"}}),
    _tool("create_task", "Register a 24/7 background task: a shell command the gateway keeps running (scrapers, watchers, pollers). Restarted automatically if it exits. Stdout/stderr go to its log. Put the script in Projects/<name>/ first.",
          {"name": {"type": "string"}, "command": {"type": "string", "description": "Shell command, e.g. python3 Projects/scraper/run.py"},
           "cwd": {"type": "string", "description": "Working directory, default workspace"},
           "start": {"type": "boolean", "description": "Start now (default true)"}}, ["name", "command"]),
    _tool("project_keys", "List the secret KEY_NAMES of one project or group (profile, ai, tools, or a project id from the prompt). Values are never returned.", {"project": {"type": "string"}}),
    _tool("list_tasks", "List 24/7 background tasks with running state, pid, restarts, last exit.", {}),
    _tool("control_task", "Start, stop or delete a background task by id.", {"id": {"type": "string"}, "action": {"type": "string", "enum": ["start", "stop", "delete"]}}),
    _tool("task_logs", "Last lines of a background task's log.", {"id": {"type": "string"}, "lines": {"type": "integer", "description": "default 100"}}, ["id"]),
]


async def execute_tool(name: str, args: Dict, mcp_servers: List[Dict]) -> str:
    try:
        if name == "run_command":
            cwd = args.get("cwd") or str(WORKSPACE)
            proc = await asyncio.create_subprocess_shell(
                args["command"], cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                env=_proc_env())
            try:
                out, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
            except asyncio.TimeoutError:
                proc.kill()
                return "Timed out after 120s"
            text = out.decode("utf-8", "replace")
            return (text[-20000:] if text else "") + f"\n[exit {proc.returncode}]"
        if name == "read_file":
            safe, err, p = is_safe_file_path(args["path"], allow_write=False)
            if not safe:
                return f"<security_error>Blocked read_file: {err}</security_error>"
            if not p.exists() or p.is_dir():
                return f"File not found: {args['path']}"
            content = p.read_text(encoding="utf-8", errors="replace")[:60000]
            return f'<untrusted_file_content path="{p.name}">\n{content}\n</untrusted_file_content>'
        if name == "write_file":
            safe, err, p = is_safe_file_path(args["path"], allow_write=True)
            if not safe:
                return f"<security_error>Blocked write_file: {err}</security_error>"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(args["content"], encoding="utf-8")
            return f"Wrote {len(args['content'])} chars to {p}"
        if name == "list_dir":
            raw = args.get("path") or WORKSPACE
            safe, err, p = is_safe_file_path(raw, allow_write=False)
            if not safe:
                return f"<security_error>Blocked list_dir: {err}</security_error>"
            if not p.exists() or not p.is_dir():
                return f"Directory not found: {raw}"
            return "\n".join(f"{'d' if x.is_dir() else 'f'} {x.name}" for x in sorted(p.iterdir()))[:20000]
        if name == "edit_file":
            safe, err, p = is_safe_file_path(args["path"], allow_write=True)
            if not safe:
                return f"<security_error>Blocked edit_file: {err}</security_error>"
            if not p.exists() or p.is_dir():
                return f"File not found: {args['path']}"
            text = p.read_text(encoding="utf-8")
            n = text.count(args["old"])
            if n != 1:
                return f"Edit refused: `old` occurs {n} times (must be exactly once)."
            p.write_text(text.replace(args["old"], args["new"], 1), encoding="utf-8")
            return f"Edited {p}"
        if name == "grep":
            raw = args.get("path") or WORKSPACE
            safe, err, root = is_safe_file_path(raw, allow_write=False)
            if not safe:
                return f"<security_error>Blocked grep: {err}</security_error>"
            proc = await asyncio.create_subprocess_exec("grep", "-rnIE", "--exclude-dir=.git", "--exclude-dir=node_modules", "--exclude-dir=.ssh", "--exclude=.env*",
                                                        "--", args["pattern"], str(root), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
            return "\n".join(out.decode("utf-8", "replace").splitlines()[:200]) or "No matches."
        if name == "glob":
            hits = sorted(str(p.relative_to(WORKSPACE)) for p in WORKSPACE.glob(args["pattern"]) if p.is_file())[:300]
            return "\n".join(hits) or "No files."
        if name == "web_fetch":
            return await _web_fetch(args["url"])
        if name == "web_search":
            return await _web_search(args["query"], args.get("time_range") or "", args.get("topic") or "")
        if name == "send_email":
            return await asyncio.to_thread(send_email, args["subject"], args["body"], args.get("to"))
        if name == "send_telegram":
            return await asyncio.to_thread(send_telegram, args["text"])
        if name == "generate_image":
            return await generate_image(args["prompt"], args.get("path"), args.get("reference"))
        if name == "generate_video":
            return await generate_video(args["prompt"], args.get("path"), args.get("image"), int(args.get("seconds") or 5))
        if name == "read_skill":
            return read_skill(args["name"])
        if name == "create_skill":
            return "Created skill at " + create_skill(args["name"], args["description"], args["body"], args.get("scripts"))
        if name == "create_automation":
            a = save_automation({"name": args["name"], "prompt": args["prompt"], "schedule": args["schedule"],
                                 "notify": args.get("notify", "none"), "model": args.get("model") or None, "agent": args.get("agent") or None})
            return json.dumps({"id": a["id"], "schedule_text": a["schedule_text"], "next_run": a["next_run"]})
        if name == "list_automations":
            return json.dumps([{k: a.get(k) for k in ("id", "name", "schedule_text", "notify", "enabled", "next_run", "last_status")} for a in list_automations()], indent=1)
        if name == "update_automation":
            a = get_automation(args["id"])
            if not a:
                return "Not found"
            a.update({k: v for k, v in args["fields"].items() if k in ("name", "prompt", "schedule", "notify", "model", "enabled", "agent")})
            a = save_automation(a)
            return json.dumps({"id": a["id"], "schedule_text": a["schedule_text"], "next_run": a["next_run"]})
        if name == "list_agents":
            return json.dumps([{k: a.get(k) for k in ("id", "name", "handle", "scope", "model")} for a in list_agents()], indent=1) or "[]"
        if name == "create_agent":
            existing = get_agent(args["name"])
            a = save_agent({**(existing or {}), "name": args["name"], "prompt": args["prompt"], "model": args.get("model") or (existing or {}).get("model"), "scope": args.get("scope") or (existing or {}).get("scope", "all")})
            return json.dumps({"id": a["id"], "handle": a["handle"], "scope": a["scope"]})
        if name == "delete_automation":
            return "Deleted" if delete_automation(args["id"]) else "Not found"
        if name == "create_task":
            t = save_task({"name": args["name"], "command": args["command"], "cwd": args.get("cwd") or None})
            if args.get("start", True):
                t = start_task(t["id"])
            return json.dumps({"id": t["id"], "running": t["running"], "pid": t.get("pid")})
        if name == "project_keys":
            pid = (args.get("project") or "").strip().lower()
            keys = secrets_by_group().get(pid) or []
            return ", ".join(keys) if keys else f"No group '{pid}'. Groups: " + ", ".join([*BUILTIN_GROUPS, *(p["id"] for p in projects_data()["projects"])])
        if name == "list_tasks":
            return json.dumps([{k: t.get(k) for k in ("id", "name", "command", "cwd", "desired", "running", "pid", "started", "restarts", "last_exit_code", "last_exit")} for t in list_tasks()], indent=1)
        if name == "control_task":
            act = args["action"]
            if not get_task(args["id"]):
                return "Not found"
            if act == "delete":
                return "Deleted" if await delete_task(args["id"]) else "Not found"
            t = start_task(args["id"]) if act == "start" else await stop_task(args["id"])
            return json.dumps({"id": t["id"], "running": t["running"], "pid": t.get("pid")})
        if name == "task_logs":
            return task_logs(args["id"], int(args.get("lines") or 100)) or "(no output yet)"
        if name == "search_app_catalog":
            if not pd_config():
                return "Pipedream is not configured (Settings > Keys: PIPEDREAM_CLIENT_ID, PIPEDREAM_CLIENT_SECRET, PIPEDREAM_PROJECT_ID)."
            apps = await pd_apps(args["query"], 12)
            return "\n".join(f"- {a['slug']}: {a['name']} ({a['auth_type']}) {a['desc'][:90]}" for a in apps) or "No apps found."
        if name == "connect_app":
            if not pd_config():
                return "Pipedream is not configured (Settings > Keys: PIPEDREAM_CLIENT_ID, PIPEDREAM_CLIENT_SECRET, PIPEDREAM_PROJECT_ID)."
            url = await pd_connect_link(args["app_slug"])
            return f"Ask the owner to open this link to connect {url.rsplit('&app=', 1)[-1]}: {url}\nAfter they finish, the app's tools become available in the next conversation turn."
        if name == "list_app_tools":
            tools = await pd_app_tools(await pd_resolve_slug(args["app_slug"]))
            return "\n".join(f"- {t['name']}: {t['description'][:140]}\n  params: {json.dumps((t.get('schema') or {}).get('properties', {}))[:600]}" for t in tools) or "No tools."
        if name == "use_app":
            return await pd_call(await pd_resolve_slug(args["app"]), args["tool"], args.get("args") or {})
        if name.startswith("app__"):
            _, slug, tool = name.split("__", 2)
            return await pd_call(slug, tool, args)
        if name.startswith("mcp__"):
            _, srv, tool = name.split("__", 2)
            server = next((s for s in mcp_servers if s["slug"] == srv), None)
            if not server:
                return "Unknown MCP server"
            return await mcp_call(server, tool, args)
        return f"Unknown tool {name}"
    except Exception as e:
        return f"Tool error: {type(e).__name__}: {e}"

# ---------------------------------------------------------------- prompt tiers: core tools always, the rest loaded on demand with more_tools

CORE_TOOLS = ["run_command", "read_file", "write_file", "edit_file", "list_dir", "grep", "glob", "web_fetch", "web_search", "read_skill", "project_keys"]
TOOL_GROUPS = {
    "apps": ["search_app_catalog", "connect_app", "list_app_tools", "use_app"],
    "media": ["generate_image", "generate_video"],
    "automations": ["create_automation", "list_automations", "update_automation", "delete_automation"],
    "tasks": ["create_task", "list_tasks", "control_task", "task_logs"],
    "agents": ["list_agents", "create_agent"],
    "skills": ["create_skill"],
    "comms": ["send_email", "send_telegram"],
}
GROUP_HELP = {"apps": "connected apps (Gmail, GitHub...) via Pipedream", "media": "generate images and videos", "automations": "scheduled automations",
              "tasks": "24/7 background tasks", "agents": "saved personas", "skills": "create a skill", "comms": "email, Telegram"}
_GROUP_OF = {n: g for g, ns in TOOL_GROUPS.items() for n in ns}


def tool_group(spec: Dict) -> str:
    n = spec["function"]["name"]
    if n in _GROUP_OF:
        return _GROUP_OF[n]
    if n.startswith("mcp__"):
        return "mcp:" + n.split("__")[1]
    return "apps"  # Pipedream per-app tools


def tools_for_tier(all_tools: List[Dict], tier: str):
    """build: everything. do: core tools plus more_tools(group) that loads the rest on demand. Returns (tools, lazy_groups)."""
    if tier == "chat":
        return [t for t in all_tools if t["function"]["name"] in ("web_search", "web_fetch")], {}
    if tier != "do":
        return list(all_tools), {}
    core, groups = [], {}
    for t in all_tools:
        if t["function"]["name"] in CORE_TOOLS:
            core.append(t)
        else:
            groups.setdefault(tool_group(t), []).append(t)
    if not groups:
        return core, {}
    desc = "; ".join(f"{g}: {GROUP_HELP.get(g) or ('MCP server ' + g[4:])} ({len(v)} tools)" for g, v in groups.items())
    more = _tool("more_tools", "Load a group of extra tools for this conversation, then call them. Groups: " + desc,
                 {"group": {"type": "string", "enum": list(groups)}})
    return core + [more], groups

# ---------------------------------------------------------------- SU tools as an MCP server (so Claude Code sees them)

SU_MCP_TOOLS = ["generate_image", "generate_video", "send_email", "send_telegram", "web_search", "web_fetch", "list_app_tools", "use_app",
                "create_automation", "list_automations", "update_automation", "delete_automation", "create_skill",
                "create_task", "list_tasks", "control_task", "task_logs",
                "list_agents", "create_agent", "search_app_catalog", "connect_app", "project_keys"]


def su_mcp_tool_list() -> List[Dict]:
    specs = {t["function"]["name"]: t["function"] for t in BUILTIN_TOOLS + (_app_tools() if pd_config() else [])}
    return [{"name": n, "description": specs[n]["description"], "inputSchema": specs[n]["parameters"]} for n in SU_MCP_TOOLS if n in specs]


async def su_mcp_call(name: str, args: Dict) -> str:
    if name not in SU_MCP_TOOLS:
        return f"Unknown tool {name}"
    return await execute_tool(name, args or {}, list_mcp())
