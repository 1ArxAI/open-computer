"""The OpenAI-compatible tool loop: run_agent."""
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
from .automations import list_automations
from .config import MAX_STEPS, WORKSPACE, env, now_iso
from .conversations import history_transcript, load_conversation, model_history, save_conversation
from .mcp import list_mcp, mcp_tool_specs
from .personas import agent_system_extra, get_agent, persona_scopes, scope_filter
from .pipedream import _app_tools, pd_config, pd_connected_slugs, pd_tool_specs
from .planner import _end_with_plan_or_final, ensure_digest, fold_history, plan_turn
from .prompt import system_prompt
from .providers import PROVIDERS, _gemini_native_ok, cc_available, default_model, gemini_cli_available, get_custom_providers, split_model
from .runtime_claude import run_claude_code
from .runtime_gemini_cli import run_gemini_cli
from .runtime_gemini_native import run_gemini_native
from .security import is_safe_file_path
from .tasks import get_task, task_logs
from .tools import BUILTIN_TOOLS, execute_tool, tools_for_tier

def refs_context(refs: Optional[List[Dict[str, Any]]]) -> str:
    """Owner typed @Name (or dropped a row) in the composer: inline the chat transcript, automation or task so no tool call is needed."""
    out = []
    for r in (refs or [])[:6]:
        t, rid = r.get("type"), str(r.get("id") or "")
        if t == "chat":
            c = load_conversation(rid)
            if not c:
                continue
            body = (f"Summary of the earlier part: {c['summary']}\n" if c.get("summary") else "") + history_transcript(c, c.get("folded_upto", 0), limit_chars=4000)
            out.append(f'Chat "{c.get("title")}" ({rid}):\n{body}')
        elif t == "automation":
            a = next((x for x in list_automations() if x.get("id") == rid), None)
            if not a:
                continue
            keep = {k: a.get(k) for k in ("id", "name", "schedule", "schedule_text", "notify", "model", "agent", "enabled", "last_run", "last_status") if a.get(k) is not None}
            out.append(f'Automation "{a.get("name")}": {json.dumps(keep)}\nPrompt: {(a.get("prompt") or "")[:1500]}')
        elif t == "task":
            tk = get_task(rid)
            if not tk:
                continue
            keep = {k: tk.get(k) for k in ("id", "name", "command", "cwd", "running", "desired", "restarts", "last_exit_code", "started") if tk.get(k) is not None}
            out.append(f'Task "{tk.get("name")}": {json.dumps(keep)}\nLast log lines:\n{task_logs(rid, 30)[-2000:]}')
        elif t in ("file", "folder", "dir"):
            rel = (r.get("path") or rid or r.get("name") or "").strip().rstrip("/")
            safe, err, p = is_safe_file_path(rel, allow_write=False)  # same rules as read_file: no .env, keys or server code
            if not safe:
                out.append(f'File "{rel}": blocked ({err})')
                p = Path("/nonexistent")
            elif not p.exists():
                p = next((c for c in WORKSPACE.rglob(Path(rel).name) if is_safe_file_path(c, allow_write=False)[0]), p)
            if p.exists():
                if p.is_file():
                    try:
                        content = p.read_text(encoding="utf-8", errors="replace")
                        if len(content) > 6000:
                            content = content[:6000] + f"\n... [truncated, {len(content)} total characters]"
                        out.append(f'File "{rel}":\n```\n{content}\n```')
                    except Exception as e:
                        out.append(f'File "{rel}": could not read ({e})')
                elif p.is_dir():
                    try:
                        entries = [f"{e.name}/" if e.is_dir() else e.name for e in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())) if not e.name.startswith(".")][:60]
                        out.append(f'Folder "{rel}/":\n' + "\n".join(f"  - {entry}" for entry in entries))
                    except Exception as e:
                        out.append(f'Folder "{rel}/": could not list ({e})')
    if not out:
        return ""
    return "\n\nItems the owner referenced with @ in this message. Use them directly; the ids are for tools:\n\n" + "\n\n".join(out)



class _Fn:
    def __init__(self, name="", arguments=""):
        self.name, self.arguments = name, arguments


class _TC:
    def __init__(self, id, fn, extra=None):
        self.id, self.type, self.function, self._extra = id, "function", fn, extra or {}

    def model_dump(self, exclude_none=True):
        d = {"id": self.id, "type": "function", "function": {"name": self.function.name, "arguments": self.function.arguments}}
        d.update(self._extra)
        return d


class _Msg:
    def __init__(self, content, tool_calls):
        self.content, self.tool_calls = content, tool_calls


class _Resp:
    def __init__(self, msg):
        self.choices = [types_SimpleNamespace(message=msg)]


import types as _types
types_SimpleNamespace = _types.SimpleNamespace


async def _stream_completion(client, model: str, messages: List[Dict], kw: Dict, emit):
    """Stream the completion, emitting text deltas as they arrive; assemble tool calls by index. Returns a response-like object."""
    stream = await client.chat.completions.create(model=model, messages=messages, stream=True, **kw)
    if not hasattr(stream, "__aiter__"):  # provider or stub returned a full response
        return stream
    text, calls = "", {}
    async for chunk in stream:
        if not chunk.choices:
            continue
        d = chunk.choices[0].delta
        if d is None:
            continue
        if d.content:
            text += d.content
            await emit({"type": "delta", "text": d.content})
        for tc in d.tool_calls or []:
            slot = calls.setdefault(tc.index, _TC(tc.id or "", _Fn()))
            if tc.id:
                slot.id = tc.id
            if tc.function:
                if tc.function.name:
                    slot.function.name += tc.function.name
                if tc.function.arguments:
                    slot.function.arguments += tc.function.arguments
            extra = getattr(tc, "model_extra", None) or {}
            for k, v in extra.items():
                if v is not None:
                    slot._extra[k] = v
    ordered = [calls[i] for i in sorted(calls)]
    for i, tc in enumerate(ordered):
        if not tc.id:
            tc.id = f"call_{i}"
    return _Resp(_Msg(text, ordered or None))

# ---------------------------------------------------------------- the loop


async def run_agent(conv: Dict, user_input: str, model: Optional[str], on_event: Optional[Callable[[Dict], Any]] = None,
                    extra_system: str = "", agent_id: Optional[str] = None, plan_first: bool = False, mode: Optional[str] = None, approve: bool = False) -> str:
    """Append user_input to conv, run the tool loop, persist, return final text."""
    if mode in ("chat", "build"):
        conv["mode"] = mode  # the composer reopens the chat in the mode it was last used in
    persona = get_agent(agent_id or conv.get("agent"))
    if persona:
        conv["agent"] = persona["id"]
        model = model or persona.get("model") or None
        extra_system = agent_system_extra(persona) + extra_system
    async def emit(ev: Dict):
        if ev.get("type") not in ("delta", "status", "timing"):
            conv.setdefault("events", []).append({**ev, "t": now_iso()})
        if on_event:
            r = on_event(ev)
            if asyncio.iscoroutine(r):
                await r

    t0 = time.time(); timing: Dict[str, Any] = {"prep_ms": 0, "model_ms": [], "tools_ms": []}
    model = model or conv.get("model") or default_model()
    conv["model"] = model
    if model.startswith("claude-code:"):
        if not cc_available():
            msg = "Claude Code is not available (not installed, or switched offline in Settings > Models)."
            await emit({"type": "error", "text": msg})
            conv["messages"] += [{"role": "user", "content": user_input}, {"role": "assistant", "content": msg}]; save_conversation(conv)
            return msg
        tier, projects = "build", None
        if plan_first and env().get("SU_PLAN_FIRST", "1") != "0":
            await ensure_digest()
            pt = await plan_turn(conv, user_input, extra_system, emit, model, mode, approve)
            tier, extra_system, model, projects = pt["tier"], pt["extra_system"], pt["model"], pt.get("projects"); conv["model"] = model
            if pt.get("final") is not None:
                return await _end_with_plan_or_final(conv, user_input, pt, emit)
            if pt.get("announce"):
                await emit({"type": "text", "text": pt["announce"]})
                conv["messages"] += [{"role": "user", "content": user_input}, {"role": "assistant", "content": pt["announce"]}]; save_conversation(conv)
                conv["_user_appended"] = True
        return await run_claude_code(conv, user_input, model.split(":", 1)[1], on_event, extra_system, scope=persona_scopes(persona), tier=tier, projects=projects)
    if model.startswith("gemini-cli:"):
        if not gemini_cli_available():
            msg = "Gemini CLI is not available (needs GEMINI_API_KEY, or it is switched offline in Settings > Models)."
            await emit({"type": "error", "text": msg})
            conv["messages"] += [{"role": "user", "content": user_input}, {"role": "assistant", "content": msg}]; save_conversation(conv)
            return msg
        return await run_gemini_cli(conv, user_input, model.split(":", 1)[1], on_event, extra_system, scope=persona_scopes(persona))
    tier, announce, preload, projects = "build", None, [], None
    if plan_first and env().get("SU_PLAN_FIRST", "1") != "0":
        await ensure_digest()
        pt = await plan_turn(conv, user_input, extra_system, emit, model, mode, approve)
        tier, extra_system, model, preload, projects = pt["tier"], pt["extra_system"], pt["model"], pt.get("groups") or [], pt.get("projects"); conv["model"] = model
        if pt.get("final") is not None:
            return await _end_with_plan_or_final(conv, user_input, pt, emit)
        announce = pt.get("announce")
        if model.startswith("claude-code:"):  # approved build routed to the build model
            return await run_agent(conv, user_input, model, on_event, extra_system, agent_id, plan_first=False)
    provider, m = split_model(model)
    try:
        client = _providers.client_for(provider)
    except Exception as e:
        await emit({"type": "error", "text": str(e)})
        conv["messages"].append({"role": "user", "content": user_input}); save_conversation(conv)
        conv["messages"].append({"role": "assistant", "content": str(e)})
        save_conversation(conv)
        return str(e)
    servers = list_mcp()
    tools = BUILTIN_TOOLS + mcp_tool_specs(servers)
    connected_apps: List[str] = []
    if pd_config():
        tools += _app_tools()
        try:
            tools += await pd_tool_specs()
            connected_apps = await pd_connected_slugs()
        except Exception as e:
            await emit({"type": "text", "text": f"(Pipedream apps unavailable: {e})"})
    tools = scope_filter(tools, persona_scopes(persona))
    tools, lazy_groups = tools_for_tier(tools, tier)
    for g in preload:  # groups the planner named are loaded up front; the rest stay behind more_tools
        tools += lazy_groups.pop(g, [])
    conv["messages"].append({"role": "user", "content": user_input})
    if announce:
        await emit({"type": "text", "text": announce})
        conv["messages"].append({"role": "assistant", "content": announce})
    save_conversation(conv)
    await fold_history(conv)
    apps_note = ("Connected apps via Pipedream (use list_app_tools then use_app): " + ", ".join(connected_apps) + "\n"
                 if connected_apps else ("No app accounts connected yet; use connect_app to get the owner an OAuth link.\n" if pd_config() else ""))
    system_text = system_prompt(extra_system + "\n" + apps_note, tier=tier, projects=projects)
    timing["prep_ms"] = int((time.time() - t0) * 1000); timing["tools"] = len(tools); timing["system_chars"] = len(system_text); timing["tier"] = tier; timing["projects"] = projects
    timing["tool_schema_chars"] = len(json.dumps(tools))
    if provider == "google" and _gemini_native_ok():
        conv["messages"].pop()  # run_gemini_native appends the user turn itself
        final = await run_gemini_native(conv, user_input, m, tools, system_text, emit, servers)
        timing["total_ms"] = int((time.time() - t0) * 1000)
        await emit({"type": "timing", **timing})
        if not conv.get("title") or conv["title"] == "New chat":
            conv["title"] = user_input.strip().splitlines()[0][:60]
        save_conversation(conv)
        return final
    messages = [{"role": "system", "content": system_text}] + model_history(conv)
    final = ""
    is_local = bool((PROVIDERS.get(provider) or {}).get("local") or "local" in provider.lower() or "127.0.0.1" in provider.lower())
    prov_label = (PROVIDERS.get(provider) or {}).get("label") or next((x["name"] for x in get_custom_providers() if x["id"] == provider), provider.capitalize())
    budget = int(0.75 * int(env().get("SU_CONTEXT_BUDGET") or (32768 if is_local else 120000)))  # ponytail: SU_CONTEXT_BUDGET overrides the guess; providers do not report it
    for step in range(MAX_STEPS):
        est = (len(system_text) + len(json.dumps(tools)) + sum(len(json.dumps(x)) for x in messages[1:])) // 4
        if est > budget:
            final = f"Stopped: this conversation has reached the model's context budget (about {est} tokens). Start a new chat, or pick a model with a larger context."
            await emit({"type": "error", "text": final})
            conv["messages"].append({"role": "assistant", "content": final})
            break
        kw = {"tools": tools, "tool_choice": "auto"} if tools else {}
        if is_local and not env().get("SU_LOCAL_THINK"):
            kw["extra_body"] = {"reasoning_effort": "none", "chat_template_kwargs": {"enable_thinking": False}}  # thinking runs at ~6 tok/s on the CPU server; SU_LOCAL_THINK=1 allows it
        r = None
        for attempt, wait in enumerate((0, 5, 10, 20, 40)):
            if wait:
                await emit({"type": "status", "text": f"{prov_label} is rate limiting; retrying in {wait}s (attempt {attempt + 1}/5)"})
                await asyncio.sleep(wait)
            else:
                await emit({"type": "status", "text": "Thinking" + (f" (step {step + 1})" if step else "")})
            try:
                tm = time.time()
                r = await _stream_completion(client, m, messages, kw, emit)
                timing["model_ms"].append(int((time.time() - tm) * 1000))
                break
            except Exception as e:
                code = getattr(e, "status_code", None)
                if code in (429, 502, 503, 529) and attempt < 4:
                    continue
                final = f"Model error ({provider}:{m}): {e}"
                if code == 429:
                    final += "\n\nThe free endpoint is rate limiting right now. Wait a minute and resend, or pick another model in the picker."
                await emit({"type": "error", "text": final})
                conv["messages"].append({"role": "assistant", "content": final})
                break
        if r is None:
            break
        msg = r.choices[0].message
        assistant = {"role": "assistant", "content": msg.content or ""}
        if msg.tool_calls:
            # keep every field the provider returned (Gemini 3 needs its thought_signature echoed back on the next turn)
            assistant["tool_calls"] = [tc.model_dump(exclude_none=True) if hasattr(tc, "model_dump") else
                                       {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                                       for tc in msg.tool_calls]
        conv["messages"].append(assistant)
        messages.append(assistant)
        if msg.content and msg.tool_calls:
            await emit({"type": "text", "text": msg.content})
        if not msg.tool_calls:
            final = msg.content or ""
            await emit({"type": "final", "text": final, "streamed": True})
            break
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except Exception:
                args = {"_raw": tc.function.arguments}
            await emit({"type": "tool_call", "name": tc.function.name, "args": args})
            tt = time.time()
            if tc.function.name == "more_tools":
                specs = lazy_groups.get(str(args.get("group", "")), [])
                have = {t["function"]["name"] for t in tools}
                tools += [t for t in specs if t["function"]["name"] not in have]
                out = ("Loaded: " + ", ".join(t["function"]["name"] for t in specs)) if specs else f"No such group. Groups: {', '.join(lazy_groups) or 'none'}"
            else:
                out = await execute_tool(tc.function.name, args, servers)
                if is_local and len(out) > 6000:
                    out = out[:6000] + f"\n[truncated: {len(out) - 6000} more characters; ask for a smaller range]"
            timing["tools_ms"].append(int((time.time() - tt) * 1000))
            await emit({"type": "tool_result", "name": tc.function.name, "output": out[:1500]})
            tool_msg = {"role": "tool", "tool_call_id": tc.id, "content": out}
            conv["messages"].append(tool_msg)
            messages.append(tool_msg)
        save_conversation(conv)
    else:
        final = f"Stopped after {MAX_STEPS} steps."
        await emit({"type": "error", "text": final})
    timing["total_ms"] = int((time.time() - t0) * 1000)
    await emit({"type": "timing", **timing})
    if not conv.get("title") or conv["title"] == "New chat":
        conv["title"] = user_input.strip().splitlines()[0][:60]
    save_conversation(conv)
    return final
