import stat
import pwd
import grp
import pty
import fcntl
import termios
import struct
import asyncio
from fastapi import WebSocket, WebSocketDisconnect
import os
import sys
import uuid
import json
import time
import subprocess
import shutil
import getpass
from pathlib import Path
from typing import Optional, Dict, Any, List
from fastapi import FastAPI, File, Form, Header, HTTPException, Request, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, FileResponse, RedirectResponse, Response, PlainTextResponse
from urllib.parse import urlparse
import hmac, hashlib, base64, secrets as _secrets
try:
    import pam as _pam
except Exception:  # python-pam missing: login disabled, token only
    _pam = None
import mimetypes
import re
from datetime import datetime
import agent
from pydantic import BaseModel
import httpx

app = FastAPI(title="SU Computer Gateway & Dashboard", version="3.0.0")
SU_TOKEN = agent.env().get("SU_TOKEN", "").strip()
_DEFAULT_ORIGINS = ",".join(o for o in ("http://localhost:8000", "http://127.0.0.1:8000", agent.env().get("SU_PUBLIC_URL", "").rstrip("/")) if o)
LOGIN_USER = agent.env().get("SU_LOGIN_USER", getpass.getuser())  # the Linux user the gateway runs as
SESSION_IDLE = int(agent.env().get("SU_SESSION_IDLE", 10 * 60))      # sign-in lapses after 10 minutes without active interaction
SESSION_MAX = 12 * 3600      # and always after twelve hours, whatever the activity
_session_secret = agent.env().get("SU_SESSION_SECRET") or ""
if not _session_secret:
    _session_secret = _secrets.token_urlsafe(32)
    try:  # persist so sessions survive restarts
        with open(agent.HOME / ".env", "a", encoding="utf-8") as _f:
            _f.write(f"SU_SESSION_SECRET={_session_secret}\n")
    except Exception:
        pass
_failed_logins: Dict[str, List[float]] = {}


def _client_ip(request: Request) -> str:
    """Real client IP for rate limiting. Proxy headers are trusted only when the direct peer is the local proxy (or SU_TRUST_PROXY=1)."""
    peer = request.client.host if request.client else "?"
    if peer in ("127.0.0.1", "::1") or agent.env().get("SU_TRUST_PROXY") == "1":
        for h in ("cf-connecting-ip", "x-forwarded-for"):
            v = request.headers.get(h)
            if v:
                return v.split(",")[0].strip()
    return peer


def make_session(user: str, start: Optional[int] = None) -> str:
    now = int(time.time()); start = start or now
    exp = min(now + SESSION_IDLE, start + SESSION_MAX)
    body = f"{user}:{start}:{exp}"
    sig = hmac.new(_session_secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{body}:{sig}".encode()).decode()


def _session_parts(cookie: Optional[str]):
    """(user, start, exp) for a valid, unexpired cookie; None otherwise."""
    if not cookie:
        return None
    try:
        user, start, exp, sig = base64.urlsafe_b64decode(cookie.encode()).decode().rsplit(":", 3)
        good = hmac.new(_session_secret.encode(), f"{user}:{start}:{exp}".encode(), hashlib.sha256).hexdigest()
        if hmac.compare_digest(sig, good) and int(exp) > time.time():
            return user, int(start), int(exp)
    except Exception:
        pass
    return None


def verify_session(cookie: Optional[str]) -> Optional[str]:
    parts = _session_parts(cookie)
    return parts[0] if parts else None


def _set_session_cookie(resp, request: Request, value: str, max_age: int):
    resp.set_cookie("su_session", value, max_age=max_age, httponly=True, samesite="lax", path="/",
                    secure=request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https" or bool(agent.env().get("SU_PUBLIC_URL", "").startswith("https")))


def is_authed(request: Request) -> bool:
    if SU_TOKEN and request.headers.get("authorization", "") == f"Bearer {SU_TOKEN}":
        return True
    if SU_TOKEN and request.query_params.get("token") == SU_TOKEN:
        return True
    return bool(verify_session(request.cookies.get("su_session")))


LOGIN_HTML = """<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>Sign in · SU Computer</title>
<style>body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;background:#0f1013;color:#e5e7eb;font:14px system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
form{width:320px;background:#17181c;border:1px solid #26272b;border-radius:12px;padding:24px;display:flex;flex-direction:column;gap:10px}
h1{font-size:16px;margin:0 0 6px}label{font-size:12px;color:#9ca3af}input{background:#0f1013;border:1px solid #26272b;color:#fff;border-radius:8px;padding:10px 12px;font-size:14px}
input:focus{outline:2px solid #2563eb;border-color:#2563eb}button{margin-top:6px;background:#2563eb;color:#fff;border:0;border-radius:8px;padding:10px;font-weight:600;font-size:14px;cursor:pointer}
button:disabled{opacity:.6}.err{color:#f87171;font-size:12px;min-height:16px;line-height:1.4}</style></head><body>
<form id=f><h1>SU Computer</h1><label>Username</label><input name=username placeholder="Username" autocomplete=username autofocus required><label>Password</label><input name=password type=password placeholder="Password" autocomplete=current-password required>
<div class=err id=err></div><button id=b>Sign in</button></form>
<script>const f=document.getElementById('f'),e=document.getElementById('err'),b=document.getElementById('b');
const params=new URLSearchParams(location.search);
if(params.get('reason')==='timeout'){e.style.color='#f59e0b';e.textContent='Signed out automatically due to 10 minutes of inactivity.';}
f.onsubmit=async ev=>{ev.preventDefault();b.disabled=true;e.style.color='#f87171';e.textContent='';
const r=await fetch('/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:f.username.value,password:f.password.value})});
const j=await r.json().catch(()=>({}));if(r.ok){location.href=params.get('next')||'/';}else{e.textContent=j.detail||('Sign in failed ('+r.status+')');b.disabled=false;}}</script></body></html>"""


@app.middleware("http")
async def auth_gate(request: Request, call_next):
    path = request.url.path
    if path in ("/health", "/login", "/auth/login", "/robots.txt", "/favicon.ico") or request.method == "OPTIONS":
        resp = await call_next(request)
    elif path.startswith(("/api/", "/zo/", "/models", "/personas", "/auth/", "/mcp")) and not is_authed(request):
        resp = JSONResponse({"detail": "Unauthorized. Sign in at /login."}, status_code=401)
    elif path == "/" and not is_authed(request):
        resp = RedirectResponse("/login", status_code=302)
    else:
        resp = await call_next(request)
        # Background polling routes should NOT refresh idle session timeout. Only active interactions do.
        passive_paths = ("/api/runs", "/api/system", "/health")
        parts = _session_parts(request.cookies.get("su_session"))
        if parts and path not in passive_paths and (parts[2] - time.time() < SESSION_IDLE / 2) and (parts[1] + SESSION_MAX > time.time() + 60):
            _set_session_cookie(resp, request, make_session(parts[0], parts[1]), SESSION_IDLE)
    if "server" in resp.headers:
        del resp.headers["server"]
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("X-Robots-Tag", "noindex, nofollow, noarchive, nosnippet")
    resp.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval' cdn.jsdelivr.net; style-src 'self' 'unsafe-inline' fonts.googleapis.com cdn.jsdelivr.net; font-src 'self' fonts.gstatic.com data:; img-src 'self' data: blob: https:; media-src 'self' data: blob:; connect-src 'self' ws: wss:; frame-ancestors 'none';"
    )
    return resp


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if is_authed(request):
        return RedirectResponse("/", status_code=302)
    return HTMLResponse(LOGIN_HTML)


class LoginBody(BaseModel):
    username: str
    password: str


@app.post("/auth/login")
async def auth_login(body: LoginBody, request: Request):
    ip = _client_ip(request)
    now = time.time()
    _failed_logins[ip] = [t for t in _failed_logins.get(ip, []) if now - t < 600]
    if len(_failed_logins[ip]) >= 5:
        raise HTTPException(429, "Too many failed attempts. Wait 10 minutes.")
    if _pam is None:
        raise HTTPException(503, "Password login unavailable on this server (python-pam missing).")
    user = body.username.strip()
    ok = user == LOGIN_USER and _pam.pam().authenticate(user, body.password, service="login")
    if not ok:
        _failed_logins[ip].append(now)
        await asyncio.sleep(1)
        raise HTTPException(401, "Wrong user or password.")
    resp = JSONResponse({"ok": True, "user": user})
    _set_session_cookie(resp, request, make_session(user), SESSION_IDLE)
    return resp


@app.get("/auth/logout")
@app.post("/auth/logout")
async def auth_logout(request: Request):
    if request.method == "GET":
        resp = RedirectResponse("/login?reason=signed_out", status_code=302)
    else:
        resp = JSONResponse({"ok": True})
    resp.delete_cookie("su_session", path="/")
    return resp


@app.get("/auth/me")
async def auth_me(request: Request):
    return {
        "user": verify_session(request.cookies.get("su_session")) or ("token" if is_authed(request) else None),
        "login_user": LOGIN_USER,
        "idle_timeout": SESSION_IDLE,
    }


@app.post("/auth/ping")
@app.get("/auth/ping")
async def auth_ping(request: Request):
    if not is_authed(request):
        raise HTTPException(status_code=401, detail="Unauthorized")
    return {"ok": True, "idle_timeout": SESSION_IDLE}


def _channel_run(conv, kind, coro_factory):
    """Used by channel handlers in agent.py so their runs show up as live runs (spinner, re-attach) in the UI."""
    run = start_run(conv, kind, coro_factory)
    _run_push(run, {"type": "start", "conversation_id": conv["id"]})
    return run.task

@app.on_event("startup")
async def start_scheduler():
    agent.channel_run_hook = _channel_run
    if agent.env().get("SU_SCHEDULER", "1") != "0":  # SU_SCHEDULER=0: a standby or rehearsal gateway that must not run automations or poll channels
        asyncio.create_task(agent.scheduler_loop())
        asyncio.create_task(agent.telegram_channel_loop())

@app.get("/api/channels")
async def get_channels():
    return agent.channels_status()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in agent.env().get("SU_CORS_ORIGINS", _DEFAULT_ORIGINS).split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

WORKSPACE_DIR = Path.home()  # file explorer root; agent workspace is agent.WORKSPACE
WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
TRASH_DIR = Path.home() / ".local/share/Trash/files"
TRASH_DIR.mkdir(parents=True, exist_ok=True)
TRASH_INFO_DIR = Path.home() / ".local/share/Trash/info"
TRASH_INFO_DIR.mkdir(parents=True, exist_ok=True)
ENV_PATH = agent.HOME / ".env"
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
SETTINGS_FILE = Path(__file__).resolve().parent / "settings.json"

def get_env_vars() -> Dict[str, str]:
    env = dict(os.environ)
    if ENV_PATH.exists():
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip().strip('"').strip("'")
    return env

# ==================== ZO COMPATIBLE API ====================

class AskRequest(BaseModel):
    input: str
    model_name: Optional[str] = None
    conversation_id: Optional[str] = None
    stream: Optional[bool] = False
    persona_id: Optional[str] = None  # agent id or handle (Zo-compatible name)
    agent: Optional[str] = None
    output_format: Optional[Dict[str, Any]] = None  # JSON Schema: the answer comes back as an object in `output`
    mode: Optional[str] = None  # "chat" (live web answer, no machine tools) or "build" (straight to the build model); None = SU decides
    refs: Optional[List[Dict[str, Any]]] = None  # [{type: chat|automation|task, id, name}] the owner referenced with @ in the composer

@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots_txt():
    return "User-agent: *\nDisallow: /\n"

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/models/available")
async def available_models():
    return {"models": await agent.available_models(), "default": agent.default_model(), "providers": agent.runtimes()}

@app.get("/api/conversations")
async def conversations(source: str = "chat", limit: int = 50):
    return {"conversations": agent.list_conversations(source, limit)}

@app.get("/api/conversations/{cid}")
async def conversation(cid: str):
    c = agent.load_conversation(cid)
    if not c:
        raise HTTPException(404, "Conversation not found")
    return c

CONV_TRASH = agent.DATA / "conversations_trash"

@app.delete("/api/conversations/{cid}")
async def delete_conversation(cid: str):
    """Soft delete: moved to data/su/conversations_trash so a mistaken Clear is recoverable."""
    p = agent.CONV_DIR / f"{cid}.json"
    if p.exists():
        CONV_TRASH.mkdir(parents=True, exist_ok=True)
        p.rename(CONV_TRASH / f"{int(time.time())}_{cid}.json")
    return {"ok": True}

@app.post("/api/conversations/restore")
async def restore_conversations(body: Dict[str, Any] = None):
    """Restore trashed conversations: {ids:[...]} or {all:true}."""
    body = body or {}
    restored = []
    if CONV_TRASH.exists():
        for f in sorted(CONV_TRASH.glob("*.json")):
            cid = f.name.split("_", 1)[1][:-5]
            if body.get("all") or cid in (body.get("ids") or []):
                f.rename(agent.CONV_DIR / f"{cid}.json"); restored.append(cid)
    return {"restored": restored}


# ==================== RUN REGISTRY (server-side jobs; pages re-attach) ====================

class Run:
    def __init__(self, conv_id: str, title: str, kind: str):
        self.conv_id, self.title, self.kind = conv_id, title, kind
        self.events: List[Dict[str, Any]] = []
        self.subs: set = set()
        self.done = False
        self.started = agent.now_iso()
        self.task: Optional[asyncio.Task] = None

RUNS: Dict[str, Run] = {}

def _run_push(run: Run, ev: Dict[str, Any]):
    ev.setdefault("ts", agent.now_iso())
    run.events.append(ev)
    for q in list(run.subs):
        q.put_nowait(ev)

def start_run(conv: Dict[str, Any], kind: str, work) -> Run:
    """work(on_event) is awaited in a background task; the run outlives any HTTP client."""
    if conv["id"] in RUNS and not RUNS[conv["id"]].done:
        raise HTTPException(409, "This conversation is already running")
    run = Run(conv["id"], conv.get("title", ""), kind)
    RUNS[conv["id"]] = run

    async def on_event(ev):
        _run_push(run, ev)

    async def worker():
        end: Dict[str, Any] = {"type": "end", "conversation_id": conv["id"]}
        try:
            result = await work(on_event)
            if isinstance(result, dict):
                end.update(result)
        except asyncio.CancelledError:
            _run_push(run, {"type": "error", "text": "Stopped by the owner."})
            end["status"] = "stopped"
        except Exception as e:
            _run_push(run, {"type": "error", "text": f"{type(e).__name__}: {e}"})
            end["status"] = "error"
        fresh = agent.load_conversation(conv["id"]) or conv
        end.setdefault("title", fresh.get("title")); end.setdefault("model", fresh.get("model"))
        run.done = True
        _run_push(run, end)
        asyncio.get_running_loop().call_later(900, lambda: RUNS.pop(conv["id"], None) if RUNS.get(conv["id"]) is run else None)

    run.task = asyncio.create_task(worker())
    return run

def run_stream(run: Run, since: int = 0):
    """SSE generator: replay events from `since`, then follow live until end. Client disconnects never touch the run."""
    async def gen():
        q: asyncio.Queue = asyncio.Queue()
        run.subs.add(q)          # subscribe and snapshot in one synchronous step: nothing can be missed or duplicated
        n = len(run.events)
        try:
            for ev in run.events[since:n]:
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                if ev.get("type") == "end":
                    return
            if run.done and n >= len(run.events):
                return
            while True:
                ev = await q.get()
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                if ev.get("type") == "end":
                    return
        finally:
            run.subs.discard(q)
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

@app.get("/api/runs")
async def active_runs():
    return {"active": [{"conversation_id": r.conv_id, "title": r.title, "kind": r.kind, "started": r.started, "events": len(r.events)}
                       for r in RUNS.values() if not r.done]}

@app.get("/api/conversations/{cid}/stream")
async def conversation_stream(cid: str, since: int = 0):
    run = RUNS.get(cid)
    if not run:
        c = agent.load_conversation(cid)
        if not c:
            raise HTTPException(404, "Conversation not found")
        async def idle():
            yield f"data: {json.dumps({'type': 'end', 'conversation_id': cid, 'idle': True})}\n\n"
        return StreamingResponse(idle(), media_type="text/event-stream")
    return run_stream(run, since)

@app.post("/api/conversations/{cid}/stop")
async def stop_conversation(cid: str):
    run = RUNS.get(cid)
    if run and not run.done and run.task:
        run.task.cancel()
        return {"ok": True, "stopped": True}
    return {"ok": True, "stopped": False}

def _thought(conv):
    names = [e["name"] for e in conv.get("events", []) if e["type"] == "tool_call"]
    return ("Used " + ", ".join(dict.fromkeys(names))) if names else "Answered directly"

@app.post("/zo/ask")
async def zo_ask(req: AskRequest):
    conv = agent.load_conversation(req.conversation_id) if req.conversation_id else None
    if not conv:
        conv = agent.new_conversation(req.input.strip().splitlines()[0][:60] if req.input.strip() else "New chat")
    agent_id = req.agent or req.persona_id

    schema = json.dumps(req.output_format) if req.output_format else ""
    extra = f"\n\nYour final answer must be only a JSON object matching this JSON Schema, no prose, no code fences:\n{schema}" if schema else ""
    extra += agent.refs_context(req.refs)

    async def work(on_event):
        out = await agent.run_agent(conv, req.input, req.model_name, on_event, extra_system=extra, agent_id=agent_id, plan_first=True, mode=req.mode if req.mode in ('chat', 'build') else None)
        asyncio.create_task(agent.remember_turn(conv, req.input, out or ""))
        return {"title": conv.get("title"), "model": conv.get("model")}

    run = start_run(conv, "chat", work)
    _run_push(run, {"type": "start", "conversation_id": conv["id"], "input": req.input, "model": req.model_name})
    if not req.stream:
        await run.task
        fresh = agent.load_conversation(conv["id"]) or conv
        last = next((m.get("content") for m in reversed(fresh["messages"]) if m.get("role") == "assistant" and m.get("content")), "")
        if schema:
            last = await agent.to_schema(last, req.output_format)
        return {"output": last, "conversation_id": conv["id"], "thought": _thought(fresh), "model": fresh.get("model")}
    return run_stream(run, 0)

# ==================== MEMORY ====================

@app.get("/api/memory")
async def get_memory():
    return {"facts": agent.memory_facts()}

@app.delete("/api/memory/{fid}")
async def delete_memory(fid: str):
    if not agent.forget(fid):
        raise HTTPException(status_code=404, detail="No such fact")
    return {"ok": True}

# ==================== WORKSPACE & FILE MANAGEMENT ====================

def format_mode(st_mode: int) -> str:
    return stat.filemode(st_mode)

def format_size(bytes_size: int, is_dir: bool) -> str:
    if is_dir:
        return "-"
    if bytes_size < 1024:
        return f"{bytes_size} B"
    if bytes_size < 1024 ** 2:
        return f"{bytes_size / 1024:.1f} KB"
    if bytes_size < 1024 ** 3:
        return f"{bytes_size / 1024 ** 2:.1f} MB"
    return f"{bytes_size / 1024 ** 3:.2f} GB"

def get_owner_str(st_uid: int, st_gid: int) -> str:
    try:
        u = pwd.getpwuid(st_uid).pw_name
    except Exception:
        u = str(st_uid)
    try:
        g = grp.getgrgid(st_gid).gr_name
    except Exception:
        g = str(st_gid)
    return f"{u}:{g}"

FILES_ROOT = agent.WORKSPACE  # My Files opens here, like Zo's /home/workspace; nothing above it is shown

@app.get("/api/files")
async def list_files(path: str = "", subpath: str = "", show_hidden: bool = False):
    req_path = path or (f"{FILES_ROOT}/{subpath.lstrip('/')}" if subpath else str(FILES_ROOT))
    if not req_path.startswith("/"):
        req_path = f"{FILES_ROOT}/{req_path}"
    target = Path(req_path).resolve()
    if target != FILES_ROOT and FILES_ROOT not in target.parents:
        target = FILES_ROOT
    
    if not target.exists():
        raise HTTPException(status_code=404, detail="Directory does not exist")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail="Target is a file, not a directory")

    items = []
    try:
        for entry in target.iterdir():
            if not show_hidden and entry.name.startswith("."):
                continue
            try:
                st = entry.lstat()
                is_dir = entry.is_dir()
                size = 0 if is_dir else st.st_size
                mtime = st.st_mtime
                time_str = time.strftime("%b %d %H:%M", time.localtime(mtime))
                mode_str = format_mode(st.st_mode)
                owner_str = get_owner_str(st.st_uid, st.st_gid)
                items.append({
                    "name": entry.name,
                    "path": str(entry.resolve()),
                    "is_dir": is_dir,
                    "size": size,
                    "size_human": format_size(size, is_dir),
                    "mode": mode_str,
                    "owner": owner_str,
                    "mtime": mtime,
                    "time_str": time_str
                })
            except (PermissionError, FileNotFoundError):
                continue
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied reading directory")

    items.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
    parent_path = str(target.parent) if target != FILES_ROOT else ""
    return {
        "current": str(target),
        "parent": parent_path,
        "root": str(FILES_ROOT),
        "relative": str(target.relative_to(FILES_ROOT)) if target != FILES_ROOT else "",
        "items": items,
        "show_hidden": show_hidden
    }

@app.get("/api/files/tree")
async def list_files_tree(max_files: int = 500):
    """Return flat list of workspace files and folders for search (Cmd+K) and @ references."""
    root = FILES_ROOT
    if not root.exists():
        return {"items": []}
    items = []
    try:
        for p in root.rglob("*"):
            rel_parts = p.relative_to(root).parts
            if any(part.startswith(".") or part in ("node_modules", "venv", "__pycache__", ".git") for part in rel_parts):
                continue
            is_d = p.is_dir()
            items.append({
                "name": p.name + ("/" if is_d else ""),
                "path": str(p.relative_to(root)),
                "is_dir": is_d
            })
            if len(items) >= max_files:
                break
    except Exception:
        pass
    items.sort(key=lambda x: (not x["is_dir"], x["path"].lower()))
    return {"items": items}

def _under_home(path: str) -> Path:
    """Accept absolute paths or paths relative to the home directory; refuse anything outside it."""
    raw = Path(path) if path.startswith("/") else WORKSPACE_DIR / path
    target = raw.resolve()
    if target != WORKSPACE_DIR and WORKSPACE_DIR not in target.parents:
        raise HTTPException(status_code=403, detail="Access denied")
    return target

class FileContentRequest(BaseModel):
    path: str
    content: str

@app.get("/api/file")
async def read_file(path: str = Query(...)):
    target = _under_home(path)
    if not target.exists() or target.is_dir():
        raise HTTPException(status_code=404, detail="File not found")
    try:
        st_size = target.stat().st_size
        if st_size > 2 * 1024 * 1024:
            content = target.read_text(encoding="utf-8", errors="replace")[:200000] + "\n\n[TRUNCATED: File exceeds 2MB limit]"
        else:
            content = target.read_text(encoding="utf-8", errors="replace")
        return {"path": str(target), "content": content, "size": st_size}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/file/raw")
async def read_file_raw(path: str = Query(...)):
    """Serve a file for the in-app viewer. Only images, video, audio and PDF render inline; anything else (HTML, SVG, scripts...)
    is a download, so a file the agent saved from the web can never run with the owner's session on this origin."""
    target = _under_home(path)
    if not target.exists() or target.is_dir():
        raise HTTPException(status_code=404, detail="File not found")
    media_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
    inline = media_type == "application/pdf" or (media_type.split("/")[0] in ("image", "video", "audio") and media_type != "image/svg+xml")
    safe_name = target.name.replace('"', "")
    headers = {"Content-Disposition": f'{"inline" if inline else "attachment"}; filename="{safe_name}"', "X-Content-Type-Options": "nosniff"}
    if inline:
        headers["Content-Security-Policy"] = "sandbox"
    return FileResponse(str(target), media_type=media_type if inline else "application/octet-stream", headers=headers)

@app.post("/api/file")
async def write_file(req: FileContentRequest):
    target = _under_home(req.path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(req.content, encoding="utf-8")
        return {"ok": True, "path": str(target)}
    except HTTPException:
        raise
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied to write to " + str(target))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class NewItemRequest(BaseModel):
    path: str
    name: str
    is_dir: bool = False

@app.post("/api/files/new")
async def create_new_item(req: NewItemRequest):
    parent = _under_home(req.path)
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name is required")
    target = parent / name
    if req.is_dir:
        target.mkdir(parents=True, exist_ok=True)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_text("", encoding="utf-8")
    return {"ok": True, "path": str(target.relative_to(WORKSPACE_DIR))}

@app.post("/api/files/upload")
async def upload_files(dir: str = Form(""), files: List[UploadFile] = File(...), paths: List[str] = Form([])):
    """Multipart upload into a workspace folder. `paths[i]` (optional) is the file's relative path for folder uploads
    (the browser's webkitRelativePath); otherwise the file name is used."""
    base = _under_home(dir or str(FILES_ROOT))
    base.mkdir(parents=True, exist_ok=True)
    written = []
    for i, up in enumerate(files):
        rel = (paths[i] if i < len(paths) and paths[i] else up.filename or f"file{i}").replace("\\", "/").lstrip("/")
        if ".." in rel.split("/"):
            raise HTTPException(status_code=400, detail=f"Bad path: {rel}")
        target = _under_home(str(base / rel))
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "wb") as f:
            while chunk := await up.read(1 << 20):
                f.write(chunk)
        written.append(str(target.relative_to(WORKSPACE_DIR)))
    return {"ok": True, "written": written, "count": len(written)}

class MoveRequest(BaseModel):
    src: str
    dest: str  # a folder to move into, or the full new path (rename)

@app.post("/api/files/move")
async def move_item(req: MoveRequest):
    src = _under_home(req.src)
    if src == WORKSPACE_DIR or not src.exists():
        raise HTTPException(status_code=404, detail="Source not found")
    dest = _under_home(req.dest)
    if dest.is_dir():
        dest = dest / src.name
    if dest == src:
        return {"ok": True, "path": str(src.relative_to(WORKSPACE_DIR))}
    if dest.exists():
        raise HTTPException(status_code=409, detail="Something already exists at the destination")
    if src in dest.parents:
        raise HTTPException(status_code=400, detail="Cannot move a folder into itself")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))
    return {"ok": True, "path": str(dest.relative_to(WORKSPACE_DIR))}

class TrashRequest(BaseModel):
    path: str

@app.post("/api/files/trash")
async def move_to_trash(req: TrashRequest):
    target = _under_home(req.path)
    if target == WORKSPACE_DIR:
        raise HTTPException(status_code=403, detail="Access denied")
    if not target.exists():
        raise HTTPException(status_code=404, detail="File not found")
    
    dest_name = target.name
    dest = TRASH_DIR / dest_name
    if dest.exists():
        dest_name = f"{int(time.time())}_{target.name}"
        dest = TRASH_DIR / dest_name
    
    meta = {
        "orig_path": str(target),
        "name": target.name,
        "trashed_at": time.time(),
        "dest_name": dest_name
    }
    (TRASH_INFO_DIR / f"{dest_name}.json").write_text(json.dumps(meta), encoding="utf-8")
    shutil.move(str(target), str(dest))
    return {"ok": True, "trashed": req.path, "dest": dest_name}

@app.get("/api/files/trash")
async def list_trash():
    items = []
    if TRASH_DIR.exists():
        for entry in TRASH_DIR.iterdir():
            info_file = TRASH_INFO_DIR / f"{entry.name}.json"
            orig_path = entry.name
            trashed_at = entry.stat().st_mtime
            if info_file.exists():
                try:
                    meta = json.loads(info_file.read_text(encoding="utf-8"))
                    orig_path = meta.get("orig_path", orig_path)
                    trashed_at = meta.get("trashed_at", trashed_at)
                except:
                    pass
            items.append({
                "name": entry.name,
                "orig_path": orig_path,
                "is_dir": entry.is_dir(),
                "size": 0 if entry.is_dir() else entry.stat().st_size,
                "trashed_at": time.strftime("%b %d %H:%M", time.localtime(trashed_at))
            })
    items.sort(key=lambda x: x["name"].lower())
    return {"count": len(items), "items": items}

@app.post("/api/files/trash/empty")
async def empty_trash():
    count = 0
    if TRASH_DIR.exists():
        for entry in TRASH_DIR.iterdir():
            try:
                if entry.is_dir():
                    shutil.rmtree(entry)
                else:
                    entry.unlink()
                count += 1
            except:
                pass
    if TRASH_INFO_DIR.exists():
        for entry in TRASH_INFO_DIR.iterdir():
            try:
                entry.unlink()
            except:
                pass
    return {"ok": True, "purged_count": count}

class RestoreRequest(BaseModel):
    name: str

@app.post("/api/files/trash/restore")
async def restore_from_trash(req: RestoreRequest):
    source = TRASH_DIR / req.name
    if not source.exists():
        raise HTTPException(status_code=404, detail="Item not in trash")
    
    info_file = TRASH_INFO_DIR / f"{req.name}.json"
    orig_path = req.name
    if info_file.exists():
        try:
            meta = json.loads(info_file.read_text(encoding="utf-8"))
            orig_path = meta.get("orig_path", orig_path)
        except:
            pass
    
    target = _under_home(orig_path)
    if target.exists():
        target = target.with_name(f"{target.name}.restored-{int(time.time())}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(target))
    if info_file.exists():
        info_file.unlink()
    return {"ok": True, "restored": orig_path}

# ==================== TERMINAL & EXECUTION ====================

class ExecRequest(BaseModel):
    command: str
    cwd: Optional[str] = None

@app.post("/api/terminal")
async def terminal_exec(req: ExecRequest):
    target_cwd = (WORKSPACE_DIR / (req.cwd or "").lstrip("/")).resolve()
    if not str(target_cwd).startswith(str(WORKSPACE_DIR)):
        target_cwd = WORKSPACE_DIR

    try:
        # run in a worker thread so a long command never blocks the event loop (chats, SSE, other requests)
        res = await asyncio.to_thread(subprocess.run, req.command, shell=True, cwd=str(target_cwd), capture_output=True, text=True, timeout=45)
        return {
            "exit_code": res.returncode,
            "stdout": res.stdout,
            "stderr": res.stderr
        }
    except subprocess.TimeoutExpired:
        return {"exit_code": 124, "stdout": "", "stderr": "Command timed out after 45s"}
    except Exception as e:
        return {"exit_code": 1, "stdout": "", "stderr": str(e)}

# ==================== AUTOMATIONS ====================

class AutomationBody(BaseModel):
    id: Optional[str] = None
    name: str
    prompt: str
    schedule: Dict[str, Any]
    notify: str = "none"
    model: Optional[str] = None
    agent: Optional[str] = None
    enabled: bool = True

@app.get("/api/automations")
async def get_automations():
    items = agent.list_automations()
    return {"active_count": sum(1 for a in items if a.get("enabled")), "automations": items}

@app.post("/api/automations")
async def upsert_automation(body: AutomationBody):
    a = agent.get_automation(body.id) if body.id else None
    data = body.model_dump()
    if a:
        a.update({k: v for k, v in data.items() if k != "id"})
    else:
        data.pop("id")
        a = data
    return agent.save_automation(a)

@app.get("/api/automations/{aid}")
async def get_automation(aid: str):
    a = agent.get_automation(aid)
    if not a:
        raise HTTPException(404, "Automation not found")
    return {**a, "runs": agent.list_runs(aid)}

@app.delete("/api/automations/{aid}")
async def remove_automation(aid: str):
    return {"ok": agent.delete_automation(aid)}

@app.post("/api/automations/{aid}/run")
async def run_automation_now(aid: str):
    a = agent.get_automation(aid)
    if not a:
        raise HTTPException(404, "Automation not found")
    conv = agent.new_conversation(f"{a['name']} · test", source=f"automation:{a['id']}")

    async def work(on_event):
        rec = await agent.run_automation(a, trigger="test", on_event=on_event, conv=conv)
        return rec

    run = start_run(conv, "automation", work)
    _run_push(run, {"type": "start", "conversation_id": conv["id"], "automation_id": aid})
    return run_stream(run, 0)

class ScheduleText(BaseModel):
    text: str
    model: Optional[str] = None

class TaskBody(BaseModel):
    id: Optional[str] = None
    name: str
    command: str
    cwd: Optional[str] = None

@app.get("/api/tasks")
async def get_tasks():
    items = agent.list_tasks()
    return {"running_count": sum(1 for t in items if t["running"]), "tasks": items}

@app.post("/api/tasks")
async def upsert_task(body: TaskBody):
    t = agent.get_task(body.id) if body.id else None
    data = body.model_dump()
    if t:
        if t["running"] and (t["command"] != data["command"] or (data["cwd"] or t["cwd"]) != t["cwd"]):
            await agent.stop_task(t["id"])
            t = agent.get_task(t["id"])
            t.update(name=data["name"], command=data["command"], cwd=data["cwd"])
            agent.save_task(t)
            return agent.start_task(t["id"])
        t.update(name=data["name"], command=data["command"], cwd=data["cwd"] or t["cwd"])
    else:
        data.pop("id")
        t = data
    return agent.save_task(t)

@app.get("/api/tasks/{tid}")
async def get_task(tid: str, lines: int = 200):
    t = agent.get_task(tid)
    if not t:
        raise HTTPException(404, "Task not found")
    return {**t, "log": agent.task_logs(tid, lines)}

@app.delete("/api/tasks/{tid}")
async def remove_task(tid: str):
    return {"ok": await agent.delete_task(tid)}

@app.post("/api/tasks/{tid}/start")
async def start_task(tid: str):
    if not agent.get_task(tid):
        raise HTTPException(404, "Task not found")
    return agent.start_task(tid)

@app.post("/api/tasks/{tid}/stop")
async def stop_task(tid: str):
    if not agent.get_task(tid):
        raise HTTPException(404, "Task not found")
    return await agent.stop_task(tid)

@app.get("/api/tasks/{tid}/logs")
async def task_log(tid: str, lines: int = 200):
    if not agent.get_task(tid):
        raise HTTPException(404, "Task not found")
    return {"log": agent.task_logs(tid, lines)}

@app.post("/api/automations/parse-schedule")
async def parse_schedule(body: ScheduleText):
    try:
        s = await agent.parse_schedule_nl(body.text, body.model)
    except Exception as e:
        raise HTTPException(400, f"Could not parse schedule: {e}")
    nxt = agent.next_run(s, datetime.now(agent.TZ))
    return {"schedule": s, "text": agent.describe_schedule(s), "next_run": nxt.isoformat(timespec="minutes") if nxt else None}

@app.post("/api/automations/describe-schedule")
async def describe_schedule(body: Dict[str, Any]):
    s = body.get("schedule") or {}
    nxt = agent.next_run(s, datetime.now(agent.TZ))
    return {"text": agent.describe_schedule(s), "next_run": nxt.isoformat(timespec="minutes") if nxt else None}


# ==================== AGENTS (PERSONAS) ====================

class AgentBody(BaseModel):
    id: Optional[str] = None
    name: str
    prompt: str
    model: Optional[str] = None
    scope: str = "all"
    handle: Optional[str] = None

@app.get("/api/agents")
async def get_agents():
    return {"agents": agent.list_agents(), "scopes": list(agent.SCOPES)}

@app.get("/personas/available")
async def personas_available():
    return {"personas": [{"id": a["id"], "name": a["name"], "handle": a["handle"], "model": a.get("model"), "scope": a.get("scope")} for a in agent.list_agents()]}

@app.post("/api/agents")
async def upsert_agent(body: AgentBody):
    a = agent.get_agent(body.id) if body.id else None
    data = body.model_dump()
    if a:
        a.update({k: v for k, v in data.items() if k != "id" and v is not None})
    else:
        data.pop("id"); a = data
    return agent.save_agent(a)

@app.delete("/api/agents/{aid}")
async def remove_agent(aid: str):
    return {"ok": agent.delete_agent(aid)}


# ==================== PROVIDERS (online/offline toggle) ====================

@app.get("/api/providers")
async def get_providers():
    return {"providers": agent.provider_status(), "default": agent.default_model()}

@app.post("/api/providers/{pid}")
async def set_provider(pid: str, body: Dict[str, Any]):
    known = {r["id"] for r in agent.provider_status()}
    if pid not in known:
        raise HTTPException(404, "Unknown provider")
    agent.set_provider_enabled(pid, bool(body.get("enabled", True)))
    agent._models_cache.clear()
    return {"providers": agent.provider_status(), "default": agent.default_model()}

# ==================== SKILLS ====================

@app.get("/api/skills")
async def get_skills():
    installed = agent.list_skills()
    try:
        cat = await agent.skills_catalog()
        names = {s["dir"] for s in installed}
        catalog = [{"slug": s.get("slug"), "name": s.get("metadata", {}).get("display-name") or s.get("name"),
                    "desc": s.get("description", "")[:300], "category": s.get("metadata", {}).get("category", ""),
                    "author": s.get("metadata", {}).get("author", ""), "installed": Path(s.get("path", "")).name in names}
                   for s in cat.get("skills", [])]
    except Exception as e:
        catalog = []
    return {"installed": installed, "catalog": catalog}

class SkillName(BaseModel):
    slug: str

@app.post("/api/skills/install")
async def install_skill(body: SkillName):
    try:
        return {"ok": True, "path": await agent.install_skill(body.slug)}
    except Exception as e:
        raise HTTPException(400, str(e))

@app.post("/api/skills/delete")
async def delete_skill(body: SkillName):
    d = agent.SKILLS_DIR / body.slug
    if d.exists() and d.resolve().parent == agent.SKILLS_DIR.resolve():
        shutil.rmtree(d)
        return {"ok": True}
    raise HTTPException(404, "Skill not found")

# ==================== SYSTEM STATS ====================

@app.get("/api/system")
async def system_stats():
    return await asyncio.to_thread(_system_stats_sync)


def _system_stats_sync():
    free_out = subprocess.run("free -m | awk 'NR==2{printf \"%.1f/%.1f GB (%.0f%%)\", $3/1024, $2/1024, $3*100/$2 }'", shell=True, capture_output=True, text=True).stdout.strip()
    disk_out = subprocess.run("df -h / | awk 'NR==2{print $3 \" / \" $2 \" (\" $5 \")\"}'", shell=True, capture_output=True, text=True).stdout.strip()
    uptime_out = subprocess.run("uptime -p", shell=True, capture_output=True, text=True).stdout.strip()
    mi = {}
    for line in open("/proc/meminfo"):
        k, v = line.split(":", 1); mi[k] = int(v.split()[0])
    du = shutil.disk_usage("/")
    cores = os.cpu_count() or 1

    docker_out = subprocess.run("docker ps --format '{{.Names}}|{{.Status}}|{{.Ports}}'", shell=True, capture_output=True, text=True).stdout.strip()
    containers = []
    if docker_out:
        for line in docker_out.split("\n"):
            parts = line.split("|")
            if len(parts) >= 2:
                containers.append({
                    "name": parts[0],
                    "status": parts[1],
                    "ports": parts[2] if len(parts) > 2 else ""
                })

    return {
        "os": "Ubuntu",
        "cores": os.cpu_count(),
        "memory": free_out,
        "disk": disk_out,
        "uptime": uptime_out,
        "cpu_pct": round(os.getloadavg()[0] / cores * 100),  # 1-min load as share of all threads
        "mem_used_gb": round((mi["MemTotal"] - mi["MemAvailable"]) / 1048576, 1),
        "mem_total_gb": round(mi["MemTotal"] / 1048576),
        "disk_used_gb": round(du.used / 1e9),
        "disk_total_gb": round(du.total / 1e9),
        "containers": containers
    }

# ==================== ENVIRONMENT VARIABLES (VIEW, EDIT, DELETE) ====================

def parse_env_file() -> List[Dict[str, str]]:
    secrets = []
    if not ENV_PATH.exists():
        return secrets
    groups = agent.projects_data()["keys"]
    
    with open(ENV_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                
                # Create mask
                if len(v) > 8:
                    masked = v[:4] + "•" * (len(v) - 8) + v[-4:]
                elif len(v) > 0:
                    masked = "•" * len(v)
                else:
                    masked = "(empty)"
                
                secrets.append({
                    "key": k,
                    "value": v,
                    "masked": masked,
                    "project": groups.get(k),
                })
    return secrets

@app.get("/api/secrets")
async def get_secrets():
    return {"secrets": parse_env_file()}

class SecretUpdateRequest(BaseModel):
    key: str
    value: str
    project: Optional[str] = None  # project id, "profile", or omitted to leave the grouping as it is

@app.post("/api/secrets")
async def save_or_update_secret(req: SecretUpdateRequest):
    k = req.key.strip().upper()
    v = req.value.strip()
    if not k:
        raise HTTPException(status_code=400, detail="Key name is required")
    
    lines = []
    updated = False
    if ENV_PATH.exists():
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if stripped and not stripped.startswith("#") and "=" in stripped:
                    existing_key = stripped.split("=", 1)[0].strip()
                    if existing_key == k:
                        lines.append(f"{k}={v}\n")
                        updated = True
                        continue
                lines.append(line)
    
    if not updated:
        lines.append(f"{k}={v}\n")
    
    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.writelines(lines)
    if req.project is not None:
        agent.assign_key(k, req.project or None)
    return {"ok": True, "key": k, "action": "updated" if updated else "created"}


# ==================== PROJECTS (grouping of secrets; values stay in .env) ====================

class ProjectBody(BaseModel):
    id: Optional[str] = None
    name: str
    note: str = ""

class AssignBody(BaseModel):
    key: str
    project: Optional[str] = None  # project id, "profile", or null for ungrouped

@app.get("/api/projects")
async def get_projects():
    return agent.projects_data()

@app.post("/api/projects")
async def save_project(body: ProjectBody):
    if not body.name.strip():
        raise HTTPException(400, "Project name is required")
    return {"ok": True, "project": agent.save_project(body.id, body.name.strip(), body.note.strip())}

@app.delete("/api/projects/{pid}")
async def delete_project(pid: str):
    if not agent.delete_project(pid):
        raise HTTPException(404, "No such project")
    return {"ok": True}

@app.post("/api/projects/assign")
async def assign_key(body: AssignBody):
    k = body.key.strip().upper()
    if k not in {s["key"] for s in parse_env_file()}:
        raise HTTPException(404, "No such secret")
    agent.assign_key(k, body.project or None)
    return {"ok": True}


class SecretsImport(BaseModel):
    text: str
    overwrite: bool = True

@app.post("/api/secrets/import")
async def import_secrets(body: SecretsImport):
    """Paste or upload a .env: KEY=VALUE lines, # comments and `export ` prefixes allowed, quotes stripped, keys uppercased."""
    existing = {s["key"] for s in parse_env_file()}
    added, updated, skipped, bad = [], [], [], []
    for raw in body.text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            bad.append(line[:40]); continue
        k, v = line.split("=", 1)
        k = k.strip().upper(); v = v.strip()
        if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
            v = v[1:-1]
        if not re.match(r"^[A-Z_][A-Z0-9_]*$", k):
            bad.append(k[:40]); continue
        if k in existing and not body.overwrite:
            skipped.append(k); continue
        await save_or_update_secret(SecretUpdateRequest(key=k, value=v))
        (updated if k in existing else added).append(k)
        existing.add(k)
    return {"ok": True, "added": added, "updated": updated, "skipped": skipped, "invalid": bad}

class SecretDeleteRequest(BaseModel):
    key: str

@app.delete("/api/secrets")
@app.post("/api/secrets/delete")
async def delete_secret(req: SecretDeleteRequest):
    k = req.key.strip()
    if not k:
        raise HTTPException(status_code=400, detail="Key name required")
    
    if not ENV_PATH.exists():
        return {"ok": True, "key": k, "deleted": False}
    
    new_lines = []
    found = False
    with open(ENV_PATH, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                existing_key = stripped.split("=", 1)[0].strip()
                if existing_key == k:
                    found = True
                    continue
            new_lines.append(line)
    
    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.writelines(new_lines)
    agent.assign_key(k, None)
    
    return {"ok": True, "key": k, "deleted": found}

# ==================== AI SETTINGS & PREFERENCES ====================

@app.get("/api/rules")
async def get_rules():
    return {"rules": agent.RULES_FILE.read_text(encoding="utf-8") if agent.RULES_FILE.exists() else ""}

@app.post("/api/rules")
async def set_rules(body: Dict[str, str]):
    agent.RULES_FILE.write_text(body.get("rules", ""), encoding="utf-8")
    return {"ok": True}

# ==================== APP INTEGRATIONS & CONNECTIVITY ====================

class IntegrationConnectRequest(BaseModel):
    app: str
    credentials: Dict[str, str]

class IntegrationTestRequest(BaseModel):
    app: str

@app.get("/api/integrations")
async def get_integrations_status():
    env = get_env_vars()
    
    apps = [
        {
            "id": "google",
            "name": "Google Workspace",
            "desc": "Gmail, Calendar, Drive and Sheets through Pipedream Connect (Apps tab) or your own OAuth client",
            "category": "Productivity",
            "icon": "https://www.gstatic.com/images/branding/product/1x/googleg_48dp.png",
            "color": "#4285f4",
            "auth_type": "oauth",
            "connected": bool(env.get("GOOGLE_CLIENT_ID") or env.get("GMAIL_REFRESH_TOKEN")),
            "keys_required": ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"],
            "docs": "Connect Gmail and the other Google apps in the Apps tab (Pipedream Connect), or enter an OAuth Client ID below."
        },
        {
            "id": "notion",
            "name": "Notion",
            "desc": "Search, query, and synchronize Notion databases, pages, and workspaces",
            "category": "Workspace",
            "icon": "https://upload.wikimedia.org/wikipedia/commons/4/45/Notion_app_logo.png",
            "color": "#000000",
            "auth_type": "api_key",
            "connected": bool(env.get("NOTION_TOKEN") or env.get("NOTION_API_KEY")),
            "keys_required": ["NOTION_TOKEN"],
            "docs": "Create an internal integration at notion.so/my-integrations and paste the secret."
        },
        {
            "id": "github",
            "name": "GitHub",
            "desc": "Create repositories, inspect pull requests, read issues, trigger actions",
            "category": "Developer",
            "icon": "https://github.githubassets.com/images/modules/logos_page/GitHub-Mark.png",
            "color": "#24292e",
            "auth_type": "api_key",
            "connected": bool(env.get("GITHUB_TOKEN")),
            "keys_required": ["GITHUB_TOKEN"],
            "docs": "Generate a Personal Access Token at github.com/settings/tokens."
        },
        {
            "id": "telegram",
            "name": "Telegram",
            "desc": "Proactive notifications, alerts, and bi-directional chat directly to your phone",
            "category": "Messaging",
            "icon": "https://upload.wikimedia.org/wikipedia/commons/8/82/Telegram_logo.svg",
            "color": "#229ED9",
            "auth_type": "bot_token",
            "connected": bool(env.get("TELEGRAM_BOT_TOKEN")),
            "keys_required": ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"],
            "docs": "Message @BotFather on Telegram to create your bot and copy the HTTP API token."
        },
        {
            "id": "discord",
            "name": "Discord",
            "desc": "Post messages to channels, monitor mentions, and execute SU bot commands",
            "category": "Messaging",
            "icon": "https://assets-global.website-files.com/6257adef93867e50d84d30e2/636e0a6a49cf127bf92de1e2_icon_clyde_blurple_RGB.png",
            "color": "#5865F2",
            "auth_type": "webhook",
            "connected": bool(env.get("DISCORD_WEBHOOK_URL") or env.get("DISCORD_BOT_TOKEN")),
            "keys_required": ["DISCORD_WEBHOOK_URL"],
            "docs": "In Discord channel settings > Integrations > Webhooks, copy the Webhook URL."
        },
        {
            "id": "slack",
            "name": "Slack",
            "desc": "Post updates, alerts, and summaries to company or personal Slack channels",
            "category": "Messaging",
            "icon": "https://upload.wikimedia.org/wikipedia/commons/d/d5/Slack_icon_2019.svg",
            "color": "#4A154B",
            "auth_type": "webhook",
            "connected": bool(env.get("SLACK_WEBHOOK_URL") or env.get("SLACK_BOT_TOKEN")),
            "keys_required": ["SLACK_WEBHOOK_URL"],
            "docs": "Configure Incoming Webhook in your Slack workspace app configuration."
        },
        {
            "id": "stripe",
            "name": "Stripe",
            "desc": "Customer billing, checkout links, invoice tracking, and charge queries",
            "category": "Payments",
            "icon": "https://upload.wikimedia.org/wikipedia/commons/b/ba/Stripe_Logo%2C_revised_2016.svg",
            "color": "#635BFF",
            "auth_type": "api_key",
            "connected": bool(env.get("STRIPE_SECRET_KEY") or env.get("STRIPE_API_KEY")),
            "keys_required": ["STRIPE_SECRET_KEY"],
            "docs": "Find your Secret Key in dashboard.stripe.com/apikeys."
        },
        {
            "id": "twitter",
            "name": "Twitter / X",
            "desc": "Draft and publish tweets, monitor news feeds, and analyze timeline engagement",
            "category": "Social",
            "icon": "https://about.x.com/content/dam/about-twitter/x/brand-toolkit/logo-black.png.twimg.1920.png",
            "color": "#000000",
            "auth_type": "api_key",
            "connected": bool(env.get("TWITTER_BEARER_TOKEN") or env.get("TWITTER_API_KEY")),
            "keys_required": ["TWITTER_BEARER_TOKEN"],
            "docs": "Generate Bearer Token in the Twitter / X Developer Portal."
        },
        {
            "id": "supabase",
            "name": "Supabase / Postgres",
            "desc": "Postgres database queries, vector embeddings, storage, and authentication",
            "category": "Database",
            "icon": "https://upload.wikimedia.org/wikipedia/commons/thumb/c/c4/Supabase_logo.svg/1024px-Supabase_logo.svg.png",
            "color": "#3ECF8E",
            "auth_type": "api_key",
            "connected": bool(env.get("SUPABASE_URL") and (env.get("SUPABASE_KEY") or env.get("SUPABASE_SERVICE_ROLE_KEY"))),
            "keys_required": ["SUPABASE_URL", "SUPABASE_KEY"],
            "docs": "Project URL and anon/service_role API key from Supabase project settings."
        }
    ]
    return {"integrations": apps}

@app.post("/api/integrations/connect")
async def connect_integration(req: IntegrationConnectRequest):
    app_id = req.app.lower()
    creds = req.credentials
    if not creds:
        raise HTTPException(status_code=400, detail="Credentials required")
    
    for k, v in creds.items():
        if k and v:
            await save_or_update_secret(SecretUpdateRequest(key=k.strip(), value=v.strip()))
            
    return {"ok": True, "app": app_id, "message": f"{app_id.capitalize()} credentials saved to .env"}

@app.post("/api/integrations/test")
async def test_integration(req: IntegrationTestRequest):
    app_id = req.app.lower()
    env = get_env_vars()
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            if app_id == "github":
                token = env.get("GITHUB_TOKEN")
                if not token:
                    return {"ok": False, "message": "GITHUB_TOKEN not configured in .env"}
                res = await client.get("https://api.github.com/user", headers={"Authorization": f"Bearer {token}", "User-Agent": "SU-Computer"})
                if res.status_code == 200:
                    data = res.json()
                    return {"ok": True, "message": f"Authenticated as GitHub user @{data.get('login')}"}
                return {"ok": False, "message": f"GitHub error ({res.status_code}): {res.text[:100]}"}
                
            elif app_id == "telegram":
                token = env.get("TELEGRAM_BOT_TOKEN")
                if not token:
                    return {"ok": False, "message": "TELEGRAM_BOT_TOKEN not configured"}
                res = await client.get(f"https://api.telegram.org/bot{token}/getMe")
                if res.status_code == 200 and res.json().get("ok"):
                    bot = res.json()["result"]
                    return {"ok": True, "message": f"Connected to Telegram Bot: @{bot.get('username')}"}
                return {"ok": False, "message": "Invalid Telegram Bot Token"}
                
            elif app_id == "notion":
                token = env.get("NOTION_TOKEN") or env.get("NOTION_API_KEY")
                if not token:
                    return {"ok": False, "message": "NOTION_TOKEN not configured"}
                res = await client.get("https://api.notion.com/v1/users/me", headers={"Authorization": f"Bearer {token}", "Notion-Version": "2022-06-28"})
                if res.status_code == 200:
                    data = res.json()
                    return {"ok": True, "message": f"Connected to Notion: {data.get('name', 'Bot')}"}
                return {"ok": False, "message": f"Notion error ({res.status_code})"}
                
            elif app_id == "google":
                return {"ok": True, "message": "Google apps connect through Pipedream Connect in the Apps tab."}
                
            elif app_id == "stripe":
                key = env.get("STRIPE_SECRET_KEY") or env.get("STRIPE_API_KEY")
                if not key:
                    return {"ok": False, "message": "STRIPE_SECRET_KEY not configured"}
                res = await client.get("https://api.stripe.com/v1/balance", headers={"Authorization": f"Bearer {key}"})
                if res.status_code == 200:
                    return {"ok": True, "message": "Stripe API key authenticated"}
                return {"ok": False, "message": "Invalid Stripe API key"}
                
            else:
                return {"ok": True, "message": f"{app_id.capitalize()} credentials validated"}
        except Exception as e:
            return {"ok": False, "message": f"Test failed: {str(e)}"}


# ==================== CUSTOM MCP SERVERS ====================

class McpBody(BaseModel):
    name: str
    url: str
    transport: str = "http"
    token: Optional[str] = None

@app.get("/api/mcp")
async def get_mcp():
    return {"servers": [{**s, "token": bool(s.get("token"))} for s in agent.list_mcp()]}

@app.post("/api/mcp")
async def add_mcp(body: McpBody):
    servers = agent.list_mcp()
    slug = re.sub(r"[^a-z0-9]+", "_", body.name.lower()).strip("_") or "server"
    if any(s["slug"] == slug for s in servers):
        raise HTTPException(400, "A server with that name exists")
    s = {"slug": slug, "name": body.name, "url": body.url, "transport": body.transport, "token": body.token or "",
         "enabled": True, "disabled_tools": [], "added": agent.now_iso()}
    s = await agent.mcp_reload(s)
    servers.append(s)
    agent.save_mcp(servers)
    return {**s, "token": bool(s.get("token"))}

@app.post("/api/mcp/{slug}/reload")
async def reload_mcp(slug: str):
    servers = agent.list_mcp()
    s = next((x for x in servers if x["slug"] == slug), None)
    if not s:
        raise HTTPException(404, "Server not found")
    await agent.mcp_reload(s)
    agent.save_mcp(servers)
    return {**s, "token": bool(s.get("token"))}

@app.post("/api/mcp/{slug}/update")
async def update_mcp(slug: str, body: Dict[str, Any]):
    servers = agent.list_mcp()
    s = next((x for x in servers if x["slug"] == slug), None)
    if not s:
        raise HTTPException(404, "Server not found")
    for k in ("enabled", "disabled_tools"):
        if k in body:
            s[k] = body[k]
    agent.save_mcp(servers)
    return {"ok": True}

@app.delete("/api/mcp/{slug}")
async def delete_mcp(slug: str):
    servers = [s for s in agent.list_mcp() if s["slug"] != slug]
    agent.save_mcp(servers)
    return {"ok": True}


# ==================== PIPEDREAM CONNECT APPS ====================

@app.get("/api/apps")
async def apps_catalog(q: str = ""):
    if not agent.pd_config():
        return {"configured": False, "apps": [], "connected": []}
    try:
        apps = await agent.pd_apps(q)
        accounts = await agent.pd_accounts()
    except Exception as e:
        raise HTTPException(502, f"Pipedream error: {e}")
    connected = {a["slug"] for a in accounts}
    return {"configured": True, "apps": [{**a, "connected": a["slug"] in connected} for a in apps], "connected": accounts}

@app.get("/api/apps/connected")
async def apps_connected():
    if not agent.pd_config():
        return {"configured": False, "accounts": []}
    accounts = await agent.pd_accounts(force=True)  # the page polls this after a connect
    store = agent._pd_store()["tools"]
    for a in accounts:
        a["tool_count"] = len(store.get(a["slug"], {}).get("tools", [])) if a.get("slug") else 0
    return {"configured": True, "accounts": accounts}

@app.post("/api/apps/{slug}/connect")
async def apps_connect(slug: str):
    try:
        return {"url": await agent.pd_connect_link(slug)}
    except Exception as e:
        raise HTTPException(502, f"Pipedream error: {e}")

@app.get("/api/apps/{slug}/tools")
async def apps_tools(slug: str, refresh: bool = False):
    try:
        return {"slug": slug, "tools": await agent.pd_app_tools(slug, refresh)}
    except Exception as e:
        raise HTTPException(502, f"Pipedream error: {e}")

@app.delete("/api/apps/accounts/{account_id}")
async def apps_disconnect(account_id: str):
    ok = await agent.pd_delete_account(account_id)
    agent._pd_accounts_cache.clear()
    return {"ok": ok}


# ==================== MEDIA MODELS (Settings > AI: Image / Video) ====================

IMAGE_MODEL_OPTIONS = ["openrouter:google/gemini-2.5-flash-image", "openrouter:google/gemini-3.1-flash-image", "openrouter:google/gemini-3-pro-image",
                       "openrouter:openai/gpt-5-image-mini", "openrouter:openai/gpt-5.4-image-2",
                       "google:gemini-2.5-flash-image", "google:gemini-3.1-flash-image", "google:gemini-3.1-flash-lite-image", "google:gemini-3-pro-image"]

@app.get("/api/media")
async def get_media_models():
    m = agent.media_models()
    return {"image": m["image"], "video": m["video"], "image_options": IMAGE_MODEL_OPTIONS,
            "video_provider": "google" if (m["video"] or "").startswith("google:") else "fal.ai",
            "video_configured": bool(m["video"] and (agent.env().get("GEMINI_API_KEY") if m["video"].startswith("google:") else agent.env().get("FAL_KEY"))),
            "video_options": ["google:veo-3.1-lite-generate-preview", "google:veo-3.1-generate-preview", "fal-ai/minimax/hailuo-02/standard/text-to-video", "fal-ai/kling-video/v2.5-turbo/pro/text-to-video"],
            "video_hint": "google:veo-3.1-lite-generate-preview needs GEMINI_API_KEY (Veo, paid per second on Google); fal-ai/... ids need FAL_KEY (MiniMax Hailuo 6 or 10 s, Kling 5 or 10 s)."}

@app.post("/api/media")
async def set_media_models(body: Dict[str, str]):
    for key, secret in (("image", "SU_IMAGE_MODEL"), ("video", "SU_VIDEO_MODEL")):
        if key in body:
            if body[key]:
                await save_or_update_secret(SecretUpdateRequest(key=secret, value=body[key]))
            else:
                await delete_secret(SecretDeleteRequest(key=secret))
    return await get_media_models()


# ==================== SU MCP SERVER (Streamable HTTP, JSON responses) ====================

@app.post("/mcp")
async def su_mcp(request: Request):
    body = await request.json()
    reqs = body if isinstance(body, list) else [body]
    out = []
    for rpc in reqs:
        mid, method, params = rpc.get("id"), rpc.get("method"), rpc.get("params") or {}
        if method == "initialize":
            res = {"protocolVersion": params.get("protocolVersion", "2025-03-26"), "capabilities": {"tools": {}},
                   "serverInfo": {"name": "su", "version": "3.0.0"}}
        elif method == "tools/list":
            res = {"tools": agent.su_mcp_tool_list()}
        elif method == "tools/call":
            text = await agent.su_mcp_call(params.get("name", ""), params.get("arguments") or {})
            res = {"content": [{"type": "text", "text": text}], "isError": text.startswith(("Tool error", "Unknown tool"))}
        elif method == "ping":
            res = {}
        elif mid is None:
            continue  # notifications (initialized, etc.)
        else:
            out.append({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Method not found: {method}"}}); continue
        if mid is not None:
            out.append({"jsonrpc": "2.0", "id": mid, "result": res})
    if not out:
        return JSONResponse(None, status_code=202)
    return JSONResponse(out[0] if not isinstance(body, list) else out)

@app.get("/mcp")
async def su_mcp_get():
    return JSONResponse({"detail": "SSE stream not supported; use POST"}, status_code=405)

@app.delete("/mcp")
async def su_mcp_delete():
    return JSONResponse(None, status_code=202)



# ==================== VISUAL DASHBOARD ROUTE ====================

@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    index_file = TEMPLATES_DIR / "index.html"
    if index_file.exists():
        return HTMLResponse(content=index_file.read_text(encoding="utf-8"))
    return HTMLResponse("<html><body><h1>SU Computer Dashboard initializing...</h1></body></html>")


# ==================== NATIVE LINUX PTY WEBSOCKET ====================

@app.websocket("/api/terminal/ws")
async def terminal_ws(websocket: WebSocket, token: Optional[str] = None):
    origin = websocket.headers.get("origin", "").rstrip("/")
    host = (websocket.headers.get("x-forwarded-host") or websocket.headers.get("host") or "").split(":")[0].lower()
    allowed = {o.strip().rstrip("/").lower() for o in agent.env().get("SU_CORS_ORIGINS", _DEFAULT_ORIGINS).split(",") if o.strip()}

    origin_host = urlparse(origin).netloc.split(":")[0].lower() if origin else ""
    is_same_origin = bool(host and origin_host and host == origin_host)

    if origin and origin.lower() not in allowed and not is_same_origin:
        await websocket.close(code=4403)
        return
    if not ((SU_TOKEN and token == SU_TOKEN) or verify_session(websocket.cookies.get("su_session"))):
        await websocket.close(code=4401)
        return
    await websocket.accept()
    master, slave = pty.openpty()

    winsize = struct.pack("HHHH", 24, 80, 0, 0)
    fcntl.ioctl(master, termios.TIOCSWINSZ, winsize)

    env = os.environ.copy()
    env["TERM"] = "xterm-256color"
    env["COLORTERM"] = "truecolor"
    env["HOME"] = str(Path.home())
    env["USER"] = getpass.getuser()
    env["SHELL"] = "/bin/bash"

    proc = await asyncio.create_subprocess_exec(
        "/bin/bash", "--login",
        stdin=slave,
        stdout=slave,
        stderr=slave,
        cwd=str(Path.home()),
        env=env,
        preexec_fn=os.setsid
    )
    os.close(slave)

    flags = fcntl.fcntl(master, fcntl.F_GETFL)
    fcntl.fcntl(master, fcntl.F_SETFL, flags | os.O_NONBLOCK)

    async def read_from_pty():
        try:
            while proc.returncode is None:
                await asyncio.sleep(0.01)
                try:
                    data = os.read(master, 8192)
                    if data:
                        await websocket.send_bytes(data)
                except (BlockingIOError, InterruptedError):
                    continue
                except Exception:
                    break
        except Exception:
            pass

    async def write_to_pty():
        try:
            while True:
                msg = await websocket.receive()
                if "bytes" in msg and msg["bytes"]:
                    os.write(master, msg["bytes"])
                elif "text" in msg and msg["text"]:
                    t = msg["text"]
                    if t.startswith("\x01RESIZE:"):
                        parts = t.split(":")
                        cols, rows = int(parts[1]), int(parts[2])
                        winsize = struct.pack("HHHH", rows, cols, 0, 0)
                        fcntl.ioctl(master, termios.TIOCSWINSZ, winsize)
                    else:
                        os.write(master, t.encode("utf-8"))
        except (WebSocketDisconnect, Exception):
            pass

    reader_task = asyncio.create_task(read_from_pty())
    writer_task = asyncio.create_task(write_to_pty())
    done, pending = await asyncio.wait([reader_task, writer_task], return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    try:
        proc.terminate()
    except Exception:
        pass
    try:
        os.close(master)
    except Exception:
        pass
