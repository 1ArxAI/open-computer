"""Quick model helpers, request triage, plan handling, memory extraction, JSON conversion."""
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
from .config import AGENT_NAME, TZ, _write, env, log, now_iso
from .conversations import history_transcript, save_conversation
from .digest import DIGEST_FILE, _digest_hash, _digest_source, digest_text
from .memory import MEMORY_FILE, MEMORY_MAX, _mem_lock, memory_facts, memory_text
from .pipedream import _pd_slugs_cache
from .projects import secrets_block
from .prompt import STYLE
from .providers import SEED_MODELS, _gemini_native_ok, cc_available, chat_providers, default_model, gemini_cli_available, gemini_simple, split_model
from .runtime_claude import cc_quick
from .skills import list_skills

async def ensure_digest():
    if digest_text() or not quick_available() or not _digest_source():
        return
    try:
        out = await quick("Condense the owner rules and operating manual below into at most 250 tokens of terse imperative bullet points. "
                          "Keep every hard rule: secrets, approvals, folders, what may be sent outward, reporting, machines. Drop examples, "
                          "schedule syntax and explanations. Output only the bullets.\n\n" + _digest_source(), timeout=150)
        if out:
            DIGEST_FILE.write_text(f"<!-- source:{_digest_hash()} -->\n{out.strip()}\n", encoding="utf-8")
    except Exception as e:
        print("digest error", e)


async def remember_turn(conv: Dict, user_input: str, answer: str):
    """After a chat turn: pull out the few facts worth keeping (owner, preferences, projects, decisions, people, key names).
    Runs in the background on the cheap model; a paragraph usually yields nothing or one line."""
    if len(user_input.strip()) < 12 or _APPROVE_RE.match(user_input) or not quick_available():
        return
    facts = memory_facts()
    known = "\n".join(f"{f['id']}: {f['text']}" for f in facts[-MEMORY_MAX:]) or "(none)"
    prompt = (f"Known facts:\n{known}\n\nNew exchange:\nOwner: {user_input[:2000]}\nSU: {answer[:2000]}\n\n"
              "Extract only durable facts worth remembering in future chats about the owner, their preferences, projects, tools, "
              "decisions, people. Not task chatter, not what SU did this turn, not anything already known, and never lists of secret "
              "key names or which project a key belongs to (the projects list already holds that). "
              "Each fact under 20 words, specific, standalone. If a known fact is now outdated, list its id under drop. "
              'Output only JSON: {"add": ["..."], "drop": ["id"]}. Usually add is empty.')
    try:
        out = await quick(prompt, timeout=60)
        j = _json_in(out) or {}
    except Exception as e:
        print("memory error", e); return
    add = [str(t).strip() for t in (j.get("add") or []) if str(t).strip()][:3]
    drop = set(str(i) for i in (j.get("drop") or []))
    if not add and not drop:
        return
    async with _mem_lock:
        facts = [f for f in memory_facts() if f["id"] not in drop]
        have = {f["text"].lower() for f in facts}
        facts += [{"id": uuid.uuid4().hex[:8], "text": t, "ts": now_iso(), "conv": conv.get("id")} for t in add if t.lower() not in have]
        _write(MEMORY_FILE, {"facts": facts[-MEMORY_MAX * 2:]})


def _json_in(text: str):
    t = re.sub(r"^```(?:json)?|```$", "", (text or "").strip(), flags=re.M).strip()
    try:
        return json.loads(t)
    except Exception:
        m = re.search(r"\{.*\}", t, re.S)
        return json.loads(m.group(0)) if m else None


async def to_schema(text: str, schema: Dict):
    """Structured output for /zo/ask: the answer as an object matching the JSON Schema. Parses the model's own JSON first,
    otherwise one cheap conversion pass; on failure returns {"error": ..., "text": ...} rather than guessing."""
    try:
        obj = _json_in(text)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    if not quick_available():
        return {"error": "answer was not valid JSON and no conversion model is available", "text": text}
    try:
        out = await quick(f"Convert this answer into a JSON object that matches the JSON Schema. Use only information in the answer; "
                          f"null for anything missing. Output only the JSON.\n\nSchema:\n{json.dumps(schema)}\n\nAnswer:\n{text[:6000]}", timeout=60)
        obj = _json_in(out)
        return obj if isinstance(obj, dict) else {"error": "conversion did not return an object", "text": text}
    except Exception as e:
        return {"error": f"conversion failed: {e}", "text": text}


def quick_model(model: Optional[str] = None) -> str:
    """The model for short helper calls (triage, memory, folding, digest, JSON conversion): SU_QUICK_MODEL, else the given
    or default chat model. Any configured provider works; the Claude Code CLI is used only when the model is a claude-code one."""
    m = env().get("SU_QUICK_MODEL") or model or default_model()
    if m.startswith("gemini-cli:"):  # the Gemini CLI is a full harness, not a one-shot endpoint; use the API or first chat provider
        ps = chat_providers()
        m = "google:gemini-2.5-flash" if "google" in ps else (f"{ps[0]}:{(SEED_MODELS.get(ps[0]) or ['default'])[0]}" if ps else m)
    return m


def quick_available() -> bool:
    m = quick_model()
    return cc_available() if m.startswith("claude-code:") else bool(m and not m.startswith("gemini-cli:") and chat_providers())


async def quick(prompt: str, system: str = "", model: Optional[str] = None, timeout: int = 60) -> str:
    """One-shot text completion on the quick model, no tools. Raises on failure so callers can fall back."""
    m = quick_model(model)
    if m.startswith("claude-code:"):
        if not cc_available():
            raise RuntimeError("Claude Code is not available")
        return await cc_quick(prompt, system=system, model=m.split(":", 1)[1], timeout=timeout)
    provider, mid = split_model(m)
    if provider == "google" and _gemini_native_ok():
        return (await asyncio.wait_for(gemini_simple(mid, (system + "\n\n" if system else "") + prompt), timeout=timeout)).strip()
    msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    r = await asyncio.wait_for(_providers.client_for(provider).chat.completions.create(model=mid, messages=msgs, temperature=0), timeout=timeout)
    return (r.choices[0].message.content or "").strip()


def build_model_for(model: str) -> str:
    """Model for a Build turn: SU_BUILD_MODEL when set and usable, otherwise the model the owner picked."""
    b = env().get("SU_BUILD_MODEL", "").strip()
    if not b or model != default_model():
        return model
    if b.startswith("claude-code:") and not cc_available():
        return model
    if b.startswith("gemini-cli:") and not gemini_cli_available():
        return model
    return b


PLANNER = f"""You are {AGENT_NAME}'s first-responder. The owner just sent a message. Decide and answer in one shot. You have NO tools: write plain text only, never tool calls or XML.
Output format: first line exactly `MODE: direct`, `MODE: do` or `MODE: build`, then a blank line, then the text.
- `direct`: a question, greeting or small request answerable from what you know and the conversation (no tools needed). Text = the complete short answer. Use the memory facts when they answer it.
- `do`: a small task on the machine (commands, files, a lookup, a status check). Text = empty. On the first line add the tool groups the worker needs, for example `MODE: do TOOLS: tasks`.
- For `do` and `build`, also add the secret groups the worker will need, by id from the facts, for example `MODE: do TOOLS: tasks PROJECTS: pi, emailer` (ids: profile, ai, tools, or a project id). Omit PROJECTS when no keys are needed. Groups: apps (connected apps such as Gmail, GitHub), media (make images or videos), automations (scheduled automations), tasks (24/7 background tasks), agents (saved personas), skills (create a skill), comms (email, Telegram). Shell, files and web need no group.
- `build`: multi-step work (create, schedule, set up, connect, send to people, write code or documents). Text = the owner's first reply: what you understood (one sentence); what you will do (steps, schedule, tools, apps, files); what you already have (name the secrets, connected apps, skills from the facts); what is missing (exact KEY_NAMEs or apps) or "Missing: nothing". Under 120 words. End with "Starting now." unless something missing makes it impossible, then end with what the owner must provide.
Never claim to have done anything yet. """ + STYLE


def quick_facts() -> str:
    """Compact facts for the fast pass (secrets names, apps, skills): no manual, no rules, keeps it to a few hundred tokens."""
    e = env()
    skills = ", ".join(s["name"] for s in list_skills()) or "(none)"
    apps = ", ".join(_pd_slugs_cache.get("v", [])) or "(none connected)"
    return (f"Facts. {secrets_block()}\nConnected apps: {apps}. Installed skills: {skills}. "
            f"Tools the worker has: shell, files, web search/fetch, send_email, generate_image, use_app for connected apps, create_automation, create_skill. "
            f"Time: {datetime.now(TZ).strftime('%A %d %B %Y %H:%M')} {TZ.key}.\n" + memory_text())


async def first_reply(conv: Dict, user_input: str, system: str, model: Optional[str] = None) -> Optional[Dict[str, str]]:
    """Returns {"mode": "direct"|"do"|"build", "text": ...} or None if the quick pass failed."""
    if not quick_available():
        return None
    history = history_transcript(conv, limit_chars=3000)
    prompt = (f"Conversation so far:\n{history}\n\n" if history else "") + f"Owner's message:\n{user_input}"
    try:
        out = await quick(prompt, system=quick_facts() + "\n\n" + PLANNER + (("\n\n" + system.strip()) if system and system.strip() else ""), model=model)
    except Exception as e:
        log.warning("triage failed, running as build: %s", e)
        return None
    out = re.sub(r"^```[a-z]*\s*|```\s*$", "", out.strip(), flags=re.M)  # tolerate a code fence or a line of preamble before MODE:
    m = re.search(r"^\s*MODE:\s*(direct|do|build)[ \t]*(?:TOOLS:\s*((?:(?!PROJECTS:)[A-Za-z0-9_:\-, ])*))?[ \t]*(?:PROJECTS:\s*([A-Za-z0-9_\-, ]*))?[ \t]*\n*(.*)$", out, re.S | re.I | re.M)
    if not m:
        log.warning("triage output had no MODE line, running as build: %r", out[:120])
        return None
    text = re.sub(r"<(invoke|function_calls|parameter|antml:[a-z_]+)[^>]*>.*?(</\1>|$)", "", m.group(4), flags=re.S).strip()
    groups = [g.strip().lower() for g in (m.group(2) or "").split(",") if g.strip() and g.strip().lower() != "none"]
    projects = [g.strip().lower() for g in (m.group(3) or "").split(",") if g.strip() and g.strip().lower() != "none"]
    return {"mode": m.group(1).lower(), "text": text, "groups": groups, "projects": projects}


_APPROVE_RE = re.compile(r"^\s*(build|build it|go|go ahead|yes|yes please|ok|okay|do it|proceed|start|approved?)\b[\s.!]*$", re.I)


async def plan_turn(conv: Dict, user_input: str, extra_system: str, emit, model: str, mode: Optional[str] = None, approve: bool = False) -> Dict[str, Any]:
    """Triage for a Build turn. Chat mode answers with web only. Build mode reads the request: a direct answer ends the turn,
    a small task runs with the core tools, and multi-step work first returns a plan (`final` + `plan`) that waits for the
    owner to press Run (approve=True on the next request). Nothing typed in the box approves a plan."""
    pend = conv.get("pending_build")
    if mode == "chat":  # web answers only, no machine, no triage
        conv.pop("pending_build", None)
        return {"tier": "chat", "extra_system": extra_system, "model": model}
    if pend and approve:
        conv.pop("pending_build", None)
        model = build_model_for(model)
        extra = (extra_system + '\n\nThe owner approved this plan: """' + pend["plan"] + '"""\nfor this request: """' + pend["input"] +
                 '"""\nDo the work now. Your final message is the closing report only: what was done, what was verified, what is left.')
        return {"tier": "build", "extra_system": extra, "model": model, "projects": pend.get("projects") or []}
    if pend:
        conv.pop("pending_build", None)  # the owner said something else: the old plan is dropped and this message is read afresh
    if not quick_available():  # no model for triage: run as a build without pretending to read the request first
        return {"tier": "build", "extra_system": extra_system, "model": build_model_for(model)}
    await emit({"type": "status", "text": "Reading your request"})
    fr = await first_reply(conv, user_input, extra_system, model=model)
    if not fr:
        return {"tier": "build", "extra_system": extra_system, "model": model}
    if fr["mode"] == "direct" and fr["text"]:
        return {"tier": "chat", "extra_system": extra_system, "model": model, "final": fr["text"]}
    if fr["mode"] == "build" and fr["text"]:
        if env().get("SU_CONFIRM_BUILD", "1") != "0":
            plan = re.sub(r"\s*Starting now\.?\s*$", "", fr["text"]).rstrip()
            conv["pending_build"] = {"input": user_input, "plan": plan, "projects": fr.get("projects") or [], "model": build_model_for(model)}
            return {"tier": "chat", "extra_system": extra_system, "model": model, "final": plan, "plan": True}
        model = build_model_for(model)
        extra =(extra_system + '\n\nYou have ALREADY sent the owner this first reply, so do not repeat or rephrase it: """' + fr["text"] +
                 '"""\nNow do the work it describes. Your final message is the closing report only: what was done, what was verified, what is left.')
        return {"tier": "build", "extra_system": extra, "model": model, "announce": fr["text"], "projects": fr.get("projects") or []}
    return {"tier": "do", "extra_system": extra_system, "model": model, "groups": fr.get("groups") or [], "projects": fr.get("projects") or []}


def _finish_turn(conv: Dict, user_input: str, text: str, plan: bool = False):
    conv["messages"] += [{"role": "user", "content": user_input}, {"role": "assistant", "content": text, **({"plan": True} if plan else {})}]
    if not conv.get("title") or conv["title"] == "New chat":
        conv["title"] = user_input.strip().splitlines()[0][:60]
    save_conversation(conv)


async def _end_with_plan_or_final(conv: Dict, user_input: str, pt: Dict[str, Any], emit) -> str:
    """A turn that ends in the planner: either a direct answer or a plan card waiting for Run."""
    text = pt["final"]
    _finish_turn(conv, user_input, text, plan=bool(pt.get("plan")))
    if pt.get("plan"):
        pend = conv.get("pending_build") or {}
        await emit({"type": "plan", "text": text, "model": pend.get("model") or conv.get("model"), "input": user_input})
    else:
        await emit({"type": "final", "text": text})
    return text


FOLD_AT, FOLD_KEEP = 24, 8


async def fold_history(conv: Dict):
    """Long chats: fold older turns into a short summary (kept in conv['summary']); stored messages stay intact for the UI."""
    msgs = conv["messages"]; start = conv.get("folded_upto", 0)
    if len(msgs) - start <= FOLD_AT or not quick_available():
        return
    cut = len(msgs) - FOLD_KEEP
    while cut > start and msgs[cut].get("role") != "user":
        cut -= 1
    if cut <= start:
        return
    lines = []
    for m in msgs[start:cut]:
        if m.get("role") == "tool":
            lines.append(f"tool result: {str(m.get('content') or '')[:200]}")
        elif m.get("tool_calls"):
            lines.append("assistant called: " + ", ".join(tc.get("function", {}).get("name", "?") for tc in m["tool_calls"]))
        elif m.get("content"):
            lines.append(f"{m['role']}: {str(m['content'])[:800]}")
    try:
        out = await quick((f"Previous summary:\n{conv.get('summary', '')}\n\n" if conv.get("summary") else "") + "New turns:\n" + "\n".join(lines)
                          + "\n\nWrite the updated summary of this conversation in at most 300 tokens: decisions, facts, file paths, open items. Plain text.", timeout=60)
    except Exception as e:
        print("fold error", e); return
    if out:
        conv["summary"], conv["folded_upto"] = out, cut
        save_conversation(conv)
