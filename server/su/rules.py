"""Owner rules as objects: an instruction and an optional condition ("when ..."). Stored in data/su/rules.json.
The free-text workspace/RULES.md still applies; these are the rules the agent itself can create and edit."""
import uuid
from typing import Dict, List, Optional

from .config import DATA, _read, _tool, _write, now_iso

RULES_JSON = DATA / "rules.json"


def list_rules() -> List[Dict]:
    return (_read(RULES_JSON, {}) or {}).get("rules", [])


def _save(rules: List[Dict]):
    _write(RULES_JSON, {"rules": rules})


def create_rule(instruction: str, condition: str = "") -> Dict:
    r = {"id": "r_" + uuid.uuid4().hex[:8], "instruction": instruction.strip(), "condition": (condition or "").strip(), "created": now_iso()}
    _save(list_rules() + [r])
    return r


def edit_rule(rid: str, instruction: Optional[str] = None, condition: Optional[str] = None) -> Optional[Dict]:
    rules = list_rules()
    r = next((x for x in rules if x["id"] == rid), None)
    if not r:
        return None
    if instruction is not None:
        r["instruction"] = instruction.strip()
    if condition is not None:
        r["condition"] = condition.strip()
    _save(rules)
    return r


def delete_rule(rid: str) -> bool:
    rules = list_rules()
    keep = [x for x in rules if x["id"] != rid]
    if len(keep) == len(rules):
        return False
    _save(keep)
    return True


def rules_text() -> str:
    return "\n".join(f"- {r['instruction']}" + (f" (when: {r['condition']})" if r.get("condition") else "") for r in list_rules() if r.get("instruction"))


TOOLS = [
    _tool("create_rule", "Create a standing rule the owner wants followed in every chat and automation. Use when the owner says 'always', 'never' or 'from now on'.",
          {"instruction": {"type": "string"}, "condition": {"type": "string", "description": "Optional: when the rule applies, e.g. 'when emailing clients'"}}, ["instruction"]),
    _tool("list_rules", "List the owner's rules with ids.", {}),
    _tool("edit_rule", "Edit a rule by id.", {"id": {"type": "string"}, "instruction": {"type": "string"}, "condition": {"type": "string"}}, ["id"]),
    _tool("delete_rule", "Delete a rule by id.", {"id": {"type": "string"}}),
]


async def handle(name: str, args: Dict) -> Optional[str]:
    if name == "create_rule":
        r = create_rule(args["instruction"], args.get("condition", ""))
        return f"Rule {r['id']} created."
    if name == "list_rules":
        return "\n".join(f"{r['id']}: {r['instruction']}" + (f" (when: {r['condition']})" if r.get("condition") else "") for r in list_rules()) or "No rules yet."
    if name == "edit_rule":
        r = edit_rule(args["id"], args.get("instruction"), args.get("condition"))
        return f"Rule {r['id']} updated." if r else "Not found"
    if name == "delete_rule":
        return "Deleted" if delete_rule(args["id"]) else "Not found"
    return None
