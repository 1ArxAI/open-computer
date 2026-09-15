"""OAuth sign-in for LLM providers that offer it. Today that is OpenRouter (PKCE, https://openrouter.ai/docs/use-cases/oauth-pkce):
the owner authorises in the browser and the returned key is stored as the OpenRouter provider.
OpenAI, Anthropic and Google do not offer OAuth for API access; their subscriptions are reachable only through their own CLIs
(Claude Code, Gemini CLI), which sign in on the box and then appear as runtimes here."""
import base64
import hashlib
import secrets
import time
from typing import Dict, Optional
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from .config import AGENT_NAME, env
from .providers import _models_cache, add_custom_provider

router = APIRouter()
_pending: Dict[str, float] = {}  # code_verifier -> started_at; single owner, so a small set of live verifiers is enough
OPENROUTER_AUTH = "https://openrouter.ai/auth"
OPENROUTER_EXCHANGE = "https://openrouter.ai/api/v1/auth/keys"
OPENROUTER_BASE = "https://openrouter.ai/api/v1"


def _challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")


def _new_verifier() -> str:
    now = time.time()
    for v, t in list(_pending.items()):
        if now - t > 900:
            _pending.pop(v, None)
    v = secrets.token_urlsafe(48)
    _pending[v] = now
    return v


def _public_base(request: Request) -> str:
    return env().get("SU_PUBLIC_URL", "").rstrip("/") or str(request.base_url).rstrip("/")


@router.get("/api/oauth/openrouter/start")
async def openrouter_start(request: Request, headless: bool = False):
    """Redirect the owner to OpenRouter. With headless=true the code is shown on OpenRouter's page for manual entry."""
    v = _new_verifier()
    q = {"code_challenge": _challenge(v), "code_challenge_method": "S256", "key_label": f"{AGENT_NAME} (Open Computer)"}
    if not headless:
        q["callback_url"] = f"{_public_base(request)}/api/oauth/openrouter/callback"
    return RedirectResponse(f"{OPENROUTER_AUTH}?{urlencode(q)}", status_code=302)


async def _exchange(code: str) -> str:
    last_err: Optional[str] = None
    for v in sorted(_pending, key=_pending.get, reverse=True):  # newest first; a stale verifier just fails
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                r = await c.post(OPENROUTER_EXCHANGE, json={"code": code, "code_verifier": v, "code_challenge_method": "S256"})
            if r.status_code == 200 and r.json().get("key"):
                _pending.pop(v, None)
                return r.json()["key"]
            last_err = f"HTTP {r.status_code}: {r.text[:200]}"
        except httpx.HTTPError as e:
            last_err = str(e)
    raise HTTPException(400, f"OpenRouter did not accept the code ({last_err or 'no sign-in was started'}). Start again from Settings > Models.")


async def _store(key: str) -> Dict:
    prov = await add_custom_provider("OpenRouter", OPENROUTER_BASE, key, "openrouter")
    _models_cache.clear()
    return prov


@router.get("/api/oauth/openrouter/callback")
async def openrouter_callback(code: str = ""):
    if not code:
        raise HTTPException(400, "OpenRouter returned no code")
    await _store(await _exchange(code))
    return RedirectResponse("/?connected=openrouter", status_code=302)


@router.post("/api/oauth/openrouter/code")
async def openrouter_code(body: Dict[str, str]):
    """Headless path: the owner pastes the code OpenRouter showed on screen."""
    code = (body.get("code") or "").strip()
    if not code:
        raise HTTPException(400, "code is required")
    prov = await _store(await _exchange(code))
    return {"ok": True, "provider": prov["id"], "models": len(prov.get("models") or [])}
