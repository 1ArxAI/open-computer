"""Tool permission scopes for personas: resource:action keys, named presets, and the tool filter.
A persona carries `scopes` (a list of keys or one preset name); the legacy `scope` preset name still works."""
from typing import Dict, Iterable, List, Set, Union

SCOPES: Dict[str, Set[str]] = {
    "files:read": {"read_file", "list_dir", "grep", "glob", "read_skill", "tool_docs"},
    "files:write": {"write_file", "edit_file", "copy_file", "create_skill"},
    "shell": {"run_command"},
    "web": {"web_search", "web_fetch", "web_research", "view_webpage"},
    "comms": {"send_email", "send_telegram"},
    "media": {"generate_image", "edit_image", "generate_video", "generate_speech", "transcribe", "generate_diagram"},
    "automations": {"create_automation", "list_automations", "update_automation", "delete_automation"},
    "tasks": {"create_task", "list_tasks", "control_task", "task_logs"},
    "agents": {"list_agents", "create_agent"},
    "rules": {"create_rule", "list_rules", "edit_rule", "delete_rule"},
    "secrets": {"project_keys"},
    "apps": {"search_app_catalog", "connect_app", "list_app_tools", "use_app"},  # plus every app__* tool
    "mcp": set(),  # every mcp__* tool
}
PRESETS: Dict[str, List[str]] = {
    "all": list(SCOPES),
    "workspace": [k for k in SCOPES if k not in ("shell", "comms", "tasks")],
    "read_only": ["files:read", "web", "secrets"],
    "read": ["files:read", "web", "secrets"],  # legacy name
    "chat": [],
}
_TOOL_SCOPE = {t: s for s, ts in SCOPES.items() for t in ts}


def expand(scopes: Union[None, str, Iterable[str]]) -> Set[str]:
    """Preset name or list of keys (presets allowed inside the list) -> set of scope keys."""
    if scopes is None or scopes == "":
        return set(SCOPES)
    if isinstance(scopes, str):
        scopes = [scopes]
    out: Set[str] = set()
    for s in scopes:
        s = str(s).strip().lower()
        if s in PRESETS:
            out.update(PRESETS[s])
        elif s in SCOPES:
            out.add(s)
    return out


def scope_of(tool_name: str) -> str:
    if tool_name.startswith("app__"):
        return "apps"
    if tool_name.startswith("mcp__"):
        return "mcp"
    return _TOOL_SCOPE.get(tool_name, "files:read")  # unknown tools are treated as harmless reads


def allowed(tool_name: str, scopes: Union[None, str, Iterable[str]]) -> bool:
    return scope_of(tool_name) in expand(scopes)


def filter_tools(tools: List[Dict], scopes: Union[None, str, Iterable[str]]) -> List[Dict]:
    keep = expand(scopes)
    if not keep:
        return []
    return [t for t in tools if scope_of(t["function"]["name"]) in keep]


def claude_disallow(scopes: Union[None, str, Iterable[str]]) -> List[str]:
    """Claude Code built-in tools to disallow for a scope set."""
    keep = expand(scopes)
    if not keep:
        return ["*"]
    out = []
    if "shell" not in keep:
        out.append("Bash")
    if "files:write" not in keep:
        out += ["Write", "Edit", "NotebookEdit"]
    return out


def describe() -> Dict:
    return {"scopes": sorted(SCOPES), "presets": {k: v for k, v in PRESETS.items() if k != "read"},
            "note": "A persona with an empty scope list is chat-only. app__* tools need 'apps', mcp__* tools need 'mcp'."}
