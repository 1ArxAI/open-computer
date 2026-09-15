"""Web fetch, web search (TinyFish, DuckDuckGo fallback), mail config, email and Telegram sends."""
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

from .config import AGENT_NAME, env, log
from .security import is_safe_url

def _strip_html(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|noscript|svg).*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</h\d>|</li>|</tr>", "\n", html)
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    import html as h
    return h.unescape(text).strip()


async def _web_fetch(url: str) -> str:
    safe, err = is_safe_url(url)
    if not safe:
        return f"<security_error>Blocked URL '{url}': {err}</security_error>"
    e = env()
    curr_url = url
    text = ""
    ctype = ""
    try:
        async with httpx.AsyncClient(timeout=45, follow_redirects=False, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) SU-Computer"}) as c:
            for _ in range(6):
                r = await c.get(curr_url)
                if r.is_redirect and "location" in r.headers:
                    next_url = urljoin(curr_url, r.headers["location"])
                    safe_redir, redir_err = is_safe_url(next_url)
                    if not safe_redir:
                        return f"<security_error>Blocked redirect to '{next_url}': {redir_err}</security_error>"
                    curr_url = next_url
                    continue
                ctype = r.headers.get("content-type", "")
                text = r.text
                break
            if "html" in ctype and len(_strip_html(text)) < 300 and e.get("BROWSERLESS_URL"):
                safe_b, _ = is_safe_url(curr_url)
                if safe_b:
                    try:
                        b = await c.post(e["BROWSERLESS_URL"].rstrip("/") + "/content", json={"url": curr_url})
                        text = b.text
                    except Exception:
                        pass
    except Exception as ex:
        return f"<fetch_error url=\"{curr_url}\">{ex}</fetch_error>"
    clean = (_strip_html(text) if "html" in ctype or text.lstrip().startswith("<") else text)[:40000]
    return f"<untrusted_web_content url=\"{curr_url}\">\n{clean}\n</untrusted_web_content>"


def _tinyfish_key() -> str:
    e = env()
    return e.get("TINYFISH_API_KEY", "")


async def _web_search(query: str) -> str:
    key = _tinyfish_key()
    if key:  # TinyFish Search: ranked results with dates, free tier. Falls back to DuckDuckGo below on any error.
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                params = {"query": query, **({"location": env()["SU_SEARCH_LOCATION"]} if env().get("SU_SEARCH_LOCATION") else {})}
                r = await c.get("https://api.search.tinyfish.ai", params=params, headers={"X-API-Key": key})
            r.raise_for_status()
            res = r.json().get("results") or []
            if res:
                items = "\n".join(f"- {x.get('title', '')}" + (f" ({x['date']})" if x.get("date") else "") + f"\n  {x.get('url', '')}\n  {x.get('snippet', '')}" for x in res[:8])
                return f"<untrusted_web_content query=\"{query}\">\n{items}\n</untrusted_web_content>"
        except Exception as ex:
            log.warning("tinyfish search failed, using DuckDuckGo: %s", ex)
    # ponytail: DuckDuckGo HTML endpoint, no key. Swap for a paid API if it rate-limits.
    async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 SU-Computer"}) as c:
        r = await c.post("https://html.duckduckgo.com/html/", data={"q": query})
    out = []
    for m in re.finditer(r'<a rel="nofollow" class="result__a" href="([^"]+)"[^>]*>(.*?)</a>.*?class="result__snippet"[^>]*>(.*?)</a>', r.text, re.S):
        href = m.group(1)
        u = re.search(r"uddg=([^&]+)", href)
        if u:
            from urllib.parse import unquote
            href = unquote(u.group(1))
        out.append(f"- {_strip_html(m.group(2))}\n  {href}\n  {_strip_html(m.group(3))}")
        if len(out) >= 8:
            break
    return f"<untrusted_web_content query=\"{query}\">\n" + ("\n".join(out) or "No results.") + "\n</untrusted_web_content>"


def send_email(subject: str, body: str, to: Optional[str] = None) -> str:
    e = env()
    cfg = mail_config() or {}
    owner_recipients = set()
    if e.get("NOTIFY_EMAIL"):
        owner_recipients.add(e["NOTIFY_EMAIL"].strip().lower())
    for addr in (e.get("SU_MAIL_ALLOWED_FROM") or "").split(","):
        if addr.strip():
            owner_recipients.add(addr.strip().lower())
    for addr in cfg.get("allowed", []):
        if addr.strip():
            owner_recipients.add(addr.strip().lower())

    send_to = [x.strip().lower() for x in (e.get("SU_MAIL_SEND_TO") or "").split(",") if x.strip()]  # addresses, @domains, or *

    def _allowed(addr: str) -> bool:
        return addr in owner_recipients or "*" in send_to or addr in send_to or any(a.startswith("@") and addr.endswith(a) for a in send_to)

    target_to = (to or "").strip()
    if not target_to:
        if not owner_recipients:
            return "No recipient: set NOTIFY_EMAIL in Settings > Keys."
        target_to = next(iter(owner_recipients))
    else:
        # Exfiltration protection: recipients must be the owner or listed in SU_MAIL_SEND_TO
        _, parsed_to = email.utils.parseaddr(target_to)
        parsed_to = parsed_to.strip().lower()
        if not parsed_to or not _allowed(parsed_to):
            return (f"<security_error>Blocked: '{target_to}' is not an allowed recipient. SU emails the owner"
                    f"{' (' + ', '.join(sorted(owner_recipients)) + ')' if owner_recipients else ''} and addresses or @domains listed in "
                    "SU_MAIL_SEND_TO in Settings > Keys. Ask the owner to add it.</security_error>")
        target_to = parsed_to

    to = target_to
    if e.get("RESEND_API_KEY"):
        r = httpx.post("https://api.resend.com/emails", headers={"Authorization": f"Bearer {e['RESEND_API_KEY']}"},
                       json={"from": e.get("EMAIL_FROM", f"{AGENT_NAME} <onboarding@resend.dev>"), "to": [to], "subject": subject, "text": body}, timeout=30)
        return f"Resend {r.status_code}: {r.text[:200]}"
    if not e.get("SMTP_HOST") and mail_config():
        cfg = mail_config()
        _mail_send(cfg, to, subject, body)
        return f"Email sent to {to} from {cfg['from']}"
    if e.get("SMTP_HOST"):
        msg = MIMEText(body)
        msg["Subject"], msg["From"], msg["To"] = subject, e.get("EMAIL_FROM", e.get("SMTP_USER", "su@localhost")), to
        port = int(e.get("SMTP_PORT", "465"))
        with (smtplib.SMTP_SSL if port == 465 else smtplib.SMTP)(e["SMTP_HOST"], port, timeout=30) as s:
            if port != 465:
                s.starttls()
            if e.get("SMTP_USER"):
                s.login(e["SMTP_USER"], e.get("SMTP_PASS", ""))
            s.send_message(msg)
        return f"Email sent to {to}"
    return "No email provider: set RESEND_API_KEY or SMTP_HOST/SMTP_USER/SMTP_PASS in Settings > Keys."


def send_telegram(text: str) -> str:
    e = env()
    if not (e.get("TELEGRAM_BOT_TOKEN") and e.get("TELEGRAM_CHAT_ID")):
        return "Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in Settings > Keys."
    r = httpx.post(f"https://api.telegram.org/bot{e['TELEGRAM_BOT_TOKEN']}/sendMessage",
                   json={"chat_id": e["TELEGRAM_CHAT_ID"], "text": text[:4000]}, timeout=30)
    return f"Telegram {r.status_code}"


def mail_config() -> Optional[Dict[str, Any]]:
    e = env()
    if not (e.get("SU_MAIL_USER") and e.get("SU_MAIL_PASS") and e.get("SU_MAIL_IMAP_HOST")):
        return None
    allowed = [x.strip().lower() for x in (e.get("SU_MAIL_ALLOWED_FROM") or e.get("NOTIFY_EMAIL", "")).split(",") if x.strip()]
    return {"imap_host": e["SU_MAIL_IMAP_HOST"], "imap_port": int(e.get("SU_MAIL_IMAP_PORT", "993")),
            "smtp_host": e.get("SU_MAIL_SMTP_HOST") or e["SU_MAIL_IMAP_HOST"].replace("imap.", "smtp."), "smtp_port": int(e.get("SU_MAIL_SMTP_PORT", "465")),
            "user": e["SU_MAIL_USER"], "password": e["SU_MAIL_PASS"], "from": e.get("SU_MAIL_FROM") or e["SU_MAIL_USER"],
            "allowed": allowed, "passphrase": e.get("SU_MAIL_PASSPHRASE", "").strip(), "model": e.get("SU_MAIL_MODEL") or None}


def _mail_send(cfg: Dict, to: str, subject: str, body: str, in_reply_to: str = "", references: str = ""):
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"], msg["From"], msg["To"] = subject, cfg["from"], to
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = (references + " " + in_reply_to).strip()
    with (smtplib.SMTP_SSL if cfg["smtp_port"] == 465 else smtplib.SMTP)(cfg["smtp_host"], cfg["smtp_port"], timeout=30) as s:
        if cfg["smtp_port"] != 465:
            s.starttls()
        s.login(cfg["user"], cfg["password"])
        s.send_message(msg)
