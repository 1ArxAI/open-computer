"""24/7 task supervisor."""
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

from .config import HOME, TASK_DIR, WORKSPACE, _read, _write, env, log, now_iso
from .services import normalise, service_env, service_url

# tasks (24/7 background processes) ------------------------------------------
# A task is a shell command the gateway keeps running: started/stopped from the UI or by the agent, logs to a file,
# restarted by the scheduler tick if it dies while "desired" is running. Processes get their own session so they
# survive gateway restarts; we re-attach by pid + a per-start nonce in the process environment.

_TASK_PROCS: Dict[str, subprocess.Popen] = {}
TASK_LOG_MAX = 5 * 1024 * 1024


def _proc_env() -> Dict[str, str]:
    return {**os.environ, **{k: v for k, v in env().items() if k.isupper()}, "HOME": str(HOME.parent), "TERM": "dumb"}


def _task_log(tid: str) -> Path:
    return TASK_DIR / f"{tid}.log"


def _pid_alive(pid: Optional[int], nonce: str = "") -> bool:
    """Alive and really ours: the per-start nonce is in the process environment (survives exec, defeats pid reuse)."""
    if not pid:
        return False
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        if stat.rsplit(")", 1)[1].split()[0] == "Z":
            return False
        return f"SU_TASK_RUN={nonce}".encode() in Path(f"/proc/{pid}/environ").read_bytes().split(b"\0") if nonce else True
    except Exception:
        return False


def task_alive(t: Dict) -> bool:
    proc = _TASK_PROCS.get(t["id"])
    if proc is not None:
        if proc.poll() is None:
            return True
        _TASK_PROCS.pop(t["id"], None)
        t["last_exit_code"], t["last_exit"], t["pid"] = proc.returncode, now_iso(), None
        _write(TASK_DIR / f"{t['id']}.json", t)
        return False
    return _pid_alive(t.get("pid"), t.get("nonce", ""))


_TS_PREFIX = re.compile(r"^\[?\d{4}-\d\d-\d\d[ T][\d:.,+]+\]?\s*|^\d\d:\d\d:\d\d(?:[.,]\d+)?\s*")
_ERR_RE = re.compile(r"\b(error|traceback|exception|failed|fatal)\b", re.I)


def task_activity(tid: str) -> Dict:
    """Heartbeat from the log tail: last line (timestamp stripped), seconds since the last write, errors in the last 200 lines."""
    p = _task_log(tid)
    if not p.exists():
        return {}
    with open(p, "rb") as f:
        f.seek(max(0, p.stat().st_size - 64 * 1024))
        lines = [l for l in f.read().decode("utf-8", "replace").splitlines()[-200:] if l.strip() and not l.startswith("--- ")]
    if not lines:
        return {}
    return {"last_line": _TS_PREFIX.sub("", lines[-1]).strip()[:120], "last_age_s": int(time.time() - p.stat().st_mtime),
            "errors": sum(1 for l in lines if _ERR_RE.search(l))}


def _task_view(t: Dict) -> Dict:
    return {**t, "running": task_alive(t), "url": service_url(t), **task_activity(t["id"])}


def list_tasks() -> List[Dict]:
    items = [_task_view(t) for t in (_read(p) for p in TASK_DIR.glob("*.json")) if t]
    items.sort(key=lambda t: t.get("created") or "")
    return items


def get_task(tid: str) -> Optional[Dict]:
    t = _read(TASK_DIR / f"{tid}.json")
    return _task_view(t) if t else None


def save_task(t: Dict) -> Dict:
    t.setdefault("id", "t_" + uuid.uuid4().hex[:10])
    t.setdefault("desired", "stopped")
    t.setdefault("restarts", 0)
    t.setdefault("created", now_iso())
    t["cwd"] = t.get("cwd") or str(WORKSPACE)
    normalise(t)
    t.pop("running", None); t.pop("url", None)
    _write(TASK_DIR / f"{t['id']}.json", t)
    return _task_view(t)


def _systemd_user_ok() -> bool:
    rt = f"/run/user/{os.getuid()}"
    if not (shutil.which("systemd-run") and Path(rt).exists()):
        return False
    try:
        return subprocess.run(["systemd-run", "--user", "--scope", "--quiet", "--collect", "true"], env={**os.environ, "XDG_RUNTIME_DIR": rt},
                              capture_output=True, timeout=5).returncode == 0
    except Exception:
        return False


def start_task(tid: str) -> Dict:
    t = get_task(tid)
    if not t:
        raise KeyError(tid)
    t["desired"] = "running"
    if not t["running"]:
        nonce = uuid.uuid4().hex[:8]
        argv, penv = ["bash", "-c", t["command"]], {**_proc_env(), **service_env(t), "SU_TASK_ID": tid, "SU_TASK_RUN": nonce}
        if _systemd_user_ok():  # own transient scope: outside the gateway's cgroup, so it survives gateway restarts
            penv["XDG_RUNTIME_DIR"] = f"/run/user/{os.getuid()}"
            argv = ["systemd-run", "--user", "--scope", "--quiet", "--collect", "--unit", f"su-task-{tid}-{nonce}"] + argv
        with open(_task_log(tid), "ab") as log:
            log.write(f"\n--- start {now_iso()} ---\n".encode())
            proc = subprocess.Popen(argv, cwd=t["cwd"], env=penv, stdout=log, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, start_new_session=True)
        _TASK_PROCS[tid] = proc
        t["pid"], t["started"], t["nonce"] = proc.pid, now_iso(), nonce
    return save_task(t)


async def stop_task(tid: str) -> Dict:
    t = get_task(tid)
    if not t:
        raise KeyError(tid)
    t["desired"] = "stopped"
    running = t["running"]
    save_task(t)
    if running and t.get("pid"):
        for sig, wait in ((signal.SIGTERM, 5.0), (signal.SIGKILL, 2.0)):
            try:
                os.killpg(t["pid"], sig)  # start_new_session => pgid == pid, so children die too
            except ProcessLookupError:
                break
            deadline = time.monotonic() + wait
            while time.monotonic() < deadline and _pid_alive(t["pid"], t.get("nonce", "")):
                await asyncio.sleep(0.2)
            if not _pid_alive(t["pid"], t.get("nonce", "")):
                break
        proc = _TASK_PROCS.pop(tid, None)
        if proc is not None:
            proc.poll()
        with open(_task_log(tid), "ab") as log:
            log.write(f"--- stopped {now_iso()} ---\n".encode())
    t = _read(TASK_DIR / f"{tid}.json") or t
    t["pid"] = None
    return save_task(t)


async def delete_task(tid: str) -> bool:
    if not get_task(tid):
        return False
    await stop_task(tid)
    (TASK_DIR / f"{tid}.json").unlink(missing_ok=True)
    _task_log(tid).unlink(missing_ok=True)
    return True


def task_logs(tid: str, lines: int = 200) -> str:
    p = _task_log(tid)
    if not p.exists():
        return ""
    with open(p, "rb") as f:
        f.seek(max(0, p.stat().st_size - 256 * 1024))
        return "\n".join(f.read().decode("utf-8", "replace").splitlines()[-lines:])


def tasks_tick():
    """Restart dead tasks that should be running; cap log files. ponytail: one restart per 60 s tick is the backoff."""
    for t in list_tasks():
        if t["desired"] == "running" and not t["running"]:
            t["restarts"] = t.get("restarts", 0) + 1
            save_task(t)
            start_task(t["id"])
        p = _task_log(t["id"])
        if p.exists() and p.stat().st_size > TASK_LOG_MAX:
            p.write_bytes(b"--- log trimmed ---\n" + p.read_bytes()[-TASK_LOG_MAX // 4:])
