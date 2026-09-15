"""Saved agents (personas): name, instructions, default model, tool scopes."""
import re
import uuid
from typing import Dict, List, Optional

from .config import DATA, _read, _write, now_iso
from .scopes import PRESETS, filter_tools

AGENTS_DIR = DATA / "agents"
AGENTS_DIR.mkdir(parents=True, exist_ok=True)
SCOPES = PRESETS  # preset names, kept for older callers


def list_agents() -> List[Dict]:
    items = [a for a in (_read(p) for p in AGENTS_DIR.glob("*.json")) if a]
    items.sort(key=lambda a: a.get("name", "").lower())
    return items


def get_agent(aid: Optional[str]) -> Optional[Dict]:
    if not aid:
        return None
    a = _read(AGENTS_DIR / f"{aid}.json")
    if a:
        return a
    return next((x for x in list_agents() if x.get("handle") == aid or x.get("name") == aid), None)


def save_agent(a: Dict) -> Dict:
    a.setdefault("id", "ag_" + uuid.uuid4().hex[:8])
    a["handle"] = re.sub(r"[^a-z0-9]+", "-", (a.get("handle") or a.get("name", "agent")).lower()).strip("-")
    scopes = a.get("scopes")
    if isinstance(scopes, str):
        scopes = [s.strip() for s in scopes.split(",") if s.strip()]
    a["scopes"] = scopes if scopes is not None else [a.get("scope") or "all"]
    a["scope"] = a.get("scope") or (a["scopes"][0] if len(a["scopes"]) == 1 and a["scopes"][0] in PRESETS else "custom")
    a.setdefault("model", None)
    a.setdefault("created", now_iso())
    _write(AGENTS_DIR / f"{a['id']}.json", a)
    return a


def delete_agent(aid: str) -> bool:
    p = AGENTS_DIR / f"{aid}.json"
    if p.exists():
        p.unlink()
        return True
    return False


def persona_scopes(a: Optional[Dict]):
    """The scope list a persona runs with; None (every tool) when there is no persona."""
    if not a:
        return None
    return a.get("scopes") or a.get("scope") or "all"


def scope_filter(tools: List[Dict], scopes) -> List[Dict]:
    return filter_tools(tools, scopes)


def agent_system_extra(a: Optional[Dict]) -> str:
    if not a:
        return ""
    return f"You are acting as the agent '{a['name']}'. Follow these instructions above all else:\n{a.get('prompt', '')}\n"
