"""Service modes for 24/7 tasks: process (no endpoint), http (PORT injected, reachable through the gateway or a public URL), tcp (a raw port).
A public URL is whatever the owner's tunnel or proxy provides: SU_SERVICE_URL_PATTERN, e.g. https://{label}.example.com.
Without it, http services are private at /api/svc/<label>/ behind the gateway's login."""
import re
from typing import Dict, Optional
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from .config import env

MODES = ("process", "http", "tcp")
HOP = {"connection", "keep-alive", "transfer-encoding", "host", "content-length"}


def label_of(t: Dict) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (t.get("label") or t.get("name") or "task").lower()).strip("-")[:40] or "task"


def normalise(t: Dict) -> Dict:
    t["mode"] = t.get("mode") if t.get("mode") in MODES else "process"
    t["port"] = int(t["port"]) if t.get("port") and t["mode"] != "process" else None
    t["env"] = {str(k): str(v) for k, v in (t.get("env") or {}).items() if str(k).isidentifier()}
    t["public"] = bool(t.get("public"))
    t["label"] = label_of(t)
    return t


def service_env(t: Dict) -> Dict[str, str]:
    e = dict(t.get("env") or {})
    if t.get("mode") in ("http", "tcp") and t.get("port"):
        e["PORT"] = str(t["port"])
    return e


def service_url(t: Dict) -> Optional[str]:
    if t.get("mode") == "http" and t.get("port"):
        pattern = env().get("SU_SERVICE_URL_PATTERN", "").strip()
        if t.get("public") and pattern:
            return pattern.replace("{label}", t["label"]).replace("{port}", str(t["port"]))
        base = env().get("SU_PUBLIC_URL", "").rstrip("/") or f"http://127.0.0.1:{env().get('SU_PORT') or 8000}"
        return f"{base}/api/svc/{t['label']}/"
    if t.get("mode") == "tcp" and t.get("port"):
        host = urlparse(env().get("SU_PUBLIC_URL", "")).hostname or env().get("SU_PUBLIC_HOST") or "127.0.0.1"
        return f"{host}:{t['port']}"
    return None


router = APIRouter()


@router.api_route("/api/svc/{label}/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
async def proxy(label: str, path: str, request: Request):
    """Private reverse proxy to an http service on 127.0.0.1:<port>. The gateway's login gate covers it because the path starts with /api/."""
    from .tasks import list_tasks
    t = next((x for x in list_tasks() if x.get("label") == label and x.get("mode") == "http" and x.get("port")), None)
    if not t:
        raise HTTPException(404, f"No http service named '{label}'")
    if not t.get("running"):
        raise HTTPException(503, f"Service '{label}' is not running")
    url = f"http://127.0.0.1:{t['port']}/{path}" + (f"?{request.url.query}" if request.url.query else "")
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP}
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=False) as c:
            r = await c.request(request.method, url, headers=headers, content=await request.body())
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Service '{label}' did not answer: {e}")
    return Response(content=r.content, status_code=r.status_code, headers={k: v for k, v in r.headers.items() if k.lower() not in HOP})
