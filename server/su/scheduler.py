"""Automation runs and the 60-second scheduler loop."""
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

from . import channels as _channels
from . import rrule
from .automations import get_automation, list_automations, save_automation
from .channels import email_channel_tick
from .config import HOME, RUNS_DIR, TZ, _write, env, now_iso
from .conversations import new_conversation
from .loop import run_agent
from .providers import cc_available, default_model
from .tasks import tasks_tick
from .web import send_email

# ---------------------------------------------------------------- scheduler


async def run_automation(a: Dict, trigger: str = "schedule", on_event=None, conv: Optional[Dict] = None) -> Dict:
    run_id = datetime.now(TZ).strftime("%Y%m%d-%H%M%S")
    conv = conv or new_conversation(f"{a['name']} · {run_id}", source=f"automation:{a['id']}")
    rec = {"id": run_id, "automation_id": a["id"], "trigger": trigger, "started": now_iso(), "conversation_id": conv["id"], "status": "running"}
    d = RUNS_DIR / a["id"]
    d.mkdir(parents=True, exist_ok=True)
    _write(d / f"{run_id}.json", rec)
    notify = a.get("notify", "none")
    model = a.get("model") or automation_default_model()
    extra = (f"This is an automation run of '{a['name']}' ({a.get('schedule_text')}). Owner notification channel: {notify}. "
             + ("Use send_email only when there is a result worth reporting; stay silent otherwise. " if notify == "email" else "")
             + ("Use send_telegram only when there is a result worth reporting; stay silent otherwise. " if notify == "telegram" else "")
             + "Finish with a one-paragraph summary of what you did.")
    try:
        final = await asyncio.wait_for(run_agent(conv, a["prompt"], model, on_event, extra_system=extra, agent_id=a.get("agent")), timeout=1800)
        rec["status"] = "error" if final.startswith(("Model error", "Stopped after")) else "ok"
        rec["summary"] = final[:2000]
    except Exception as e:
        rec["status"], rec["summary"] = "error", f"{type(e).__name__}: {e}"
    rec["finished"] = now_iso()
    _write(d / f"{run_id}.json", rec)
    if rec["status"] == "error":
        await asyncio.to_thread(send_email, f"[SU] Automation failed: {a['name']}", rec["summary"])
    fresh = get_automation(a["id"]) or a
    fresh["last_run"], fresh["last_status"] = rec["finished"], rec["status"]
    if trigger == "schedule":
        s = fresh.get("schedule") or {}
        if s.get("type") == "once" or (s.get("type") == "rrule" and rrule.is_once(s.get("rrule", ""))):
            fresh["enabled"] = False
    save_automation(fresh)
    return rec



async def tunnel_url_tick():
    """Quick tunnels get a new trycloudflare.com URL on every restart; keep SU_PUBLIC_URL in step with it."""
    e = env()
    container = e.get("SU_TUNNEL_CONTAINER", "").strip()
    if not container or e.get("CLOUDFLARE_TUNNEL_TOKEN") or e.get("SU_PUBLIC_HOST"):
        return
    proc = await asyncio.create_subprocess_exec("docker", "logs", container, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    out, _ = await asyncio.wait_for(proc.communicate(), timeout=20)
    found = re.findall(r"https://[a-z0-9-]+\.trycloudflare\.com", out.decode("utf-8", "replace"))
    url = found[-1] if found else ""
    if url and url != e.get("SU_PUBLIC_URL"):
        p = HOME / ".env"
        lines = [l for l in p.read_text(encoding="utf-8").splitlines() if not l.startswith("SU_PUBLIC_URL=")]
        p.write_text("\n".join(lines + [f"SU_PUBLIC_URL={url}"]) + "\n", encoding="utf-8")

def automation_default_model() -> str:
    """Unattended runs get the strongest harness available: Gemini CLI (free with a Google login), then Claude Code, then the chat default."""
    e = env()
    if e.get("SU_AUTOMATION_MODEL"):
        return e["SU_AUTOMATION_MODEL"]
    if cc_available():
        return "claude-code:haiku"  # unattended runs on the cheap model; build with the strong one in chat
    return default_model()


_running_lock = asyncio.Lock()


async def scheduler_loop():
    while True:
        try:
            now = datetime.now(TZ)
            for a in list_automations():
                if a.get("enabled") and a.get("next_run") and datetime.fromisoformat(a["next_run"]) <= now:
                    async with _running_lock:  # ponytail: one run at a time; 3.7 GB box
                        if _channels.channel_run_hook:
                            conv = new_conversation(f"{a['name']} · {datetime.now(TZ).strftime('%Y%m%d-%H%M%S')}", source=f"automation:{a['id']}")
                            async def _work(on_event, _a=a, _conv=conv):
                                return await run_automation(_a, on_event=on_event, conv=_conv)
                            await _channels.channel_run_hook(conv, "automation", _work)
                        else:
                            await run_automation(a)
        except Exception as e:
            print("scheduler error", e)
        try:
            async with _running_lock:
                await email_channel_tick()
        except Exception as e:
            print("email channel error", e)
        try:
            tasks_tick()
        except Exception as e:
            print("tasks error", e)
        try:
            await tunnel_url_tick()
        except Exception:
            pass
        await asyncio.sleep(60)
