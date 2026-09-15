"""Deeper web work: web_research (search, then read the top pages) and view_webpage (headless browser text + screenshot).
Search and fetch use TinyFish's free endpoints when a key is set; the browser is the optional browserless container."""
import re
from datetime import datetime
from typing import Dict, List, Optional

import httpx

from .config import TZ, WORKSPACE, _tool, env
from .security import is_safe_url
from .web import _strip_html, _web_fetch, _web_search, tinyfish_fetch


async def web_research(query: str, time_range: str = "", pages: int = 4) -> str:
    found = await _web_search(query, time_range, "")
    urls = re.findall(r"^\s+(https?://\S+)$", found, re.M)[: max(1, min(int(pages or 4), 6))]
    if not urls:
        return found
    texts: Dict[str, str] = {}
    fetched = await tinyfish_fetch(urls)
    for u in urls:
        body = fetched.get(u) or ""
        if not body:
            page = await _web_fetch(u)
            body = re.sub(r"^<untrusted_web_content[^>]*>\n|\n</untrusted_web_content>$", "", page)
        texts[u] = body[:2500]
    excerpts = "\n\n".join(f"### {u}\n{t}" for u, t in texts.items())
    return f'<untrusted_web_content query="{query}" kind="research">\n{found}\n\n## Page excerpts\n{excerpts}\n</untrusted_web_content>'


async def view_webpage(url: str) -> str:
    base = env().get("BROWSERLESS_URL", "").rstrip("/")
    if not base:
        return "No browser configured. Set BROWSERLESS_URL (see docker-compose.yml) to render pages that need JavaScript."
    safe, err = is_safe_url(url)
    if not safe:
        return f"<security_error>Blocked URL '{url}': {err}</security_error>"
    token = env().get("BROWSERLESS_TOKEN", "")
    q = f"?token={token}" if token else ""
    async with httpx.AsyncClient(timeout=90) as c:
        html = (await c.post(f"{base}/content{q}", json={"url": url, "gotoOptions": {"waitUntil": "networkidle2"}})).text
        shot = await c.post(f"{base}/screenshot{q}", json={"url": url, "options": {"fullPage": False, "type": "png"}})
    out = WORKSPACE / "Projects" / "media" / f"page-{datetime.now(TZ).strftime('%Y%m%d-%H%M%S')}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    if shot.status_code == 200 and shot.headers.get("content-type", "").startswith("image"):
        out.write_bytes(shot.content)
        shot_note = f"\nScreenshot: {out.relative_to(WORKSPACE)}"
    else:
        shot_note = f"\n(screenshot failed: HTTP {shot.status_code})"
    return f'<untrusted_web_content url="{url}" kind="browser">\n{_strip_html(html)[:40000]}\n</untrusted_web_content>{shot_note}'


TOOLS = [
    _tool("web_research", "Search, then read the top pages and return excerpts with sources. Slower and deeper than web_search.",
          {"query": {"type": "string"}, "time_range": {"type": "string", "enum": ["anytime", "day", "week", "month", "year"]},
           "pages": {"type": "integer", "description": "Pages to read, default 4, max 6"}}, ["query"]),
    _tool("view_webpage", "Render a page in the headless browser and return its text plus a screenshot file. Use for pages that need JavaScript.", {"url": {"type": "string"}}),
]


async def handle(name: str, args: Dict) -> Optional[str]:
    if name == "web_research":
        return await web_research(args["query"], args.get("time_range") or "", int(args.get("pages") or 4))
    if name == "view_webpage":
        return await view_webpage(args["url"])
    return None
