"""Self-check for agent.py with a stubbed model. Run: SU_HOME=/tmp/su_test python test_agent.py"""
import asyncio
import json
import os
import tempfile
import types
from datetime import datetime

os.environ.setdefault("SU_HOME", tempfile.mkdtemp())
import agent  # noqa: E402


class FakeMsg:
    def __init__(self, content=None, tool_calls=None):
        self.content, self.tool_calls = content, tool_calls


class FakeTC:
    def __init__(self, i, name, args):
        self.id = i
        self.function = types.SimpleNamespace(name=name, arguments=json.dumps(args))


class FakeClient:
    """First call: tool call; second: final answer."""
    def __init__(self):
        self.calls = 0
        self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self.create))

    async def create(self, **kw):
        self.calls += 1
        if self.calls == 1:
            msg = FakeMsg(None, [FakeTC("t1", "write_file", {"path": "Projects/t/out.txt", "content": "hello"})])
        elif self.calls == 2:
            assert kw["messages"][-1]["role"] == "tool" and "Wrote" in kw["messages"][-1]["content"]
            msg = FakeMsg(None, [FakeTC("t2", "run_command", {"command": "cat Projects/t/out.txt"})])
        else:
            assert "hello" in kw["messages"][-1]["content"]
            msg = FakeMsg("done: file says hello")
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])


async def main():
    fake = FakeClient()
    agent.providers.client_for = lambda provider: fake
    agent.providers.configured_providers = lambda: ["openrouter"]
    events = []
    conv = agent.new_conversation("New chat")
    out = await agent.run_agent(conv, "write hello then read it", "openrouter:x", lambda e: e["type"] not in ("status", "timing", "delta") and events.append(e["type"]))
    assert out == "done: file says hello", out
    assert events == ["tool_call", "tool_result", "tool_call", "tool_result", "final"], events
    assert (agent.WORKSPACE / "Projects/t/out.txt").read_text() == "hello"
    saved = agent.load_conversation(conv["id"])
    assert saved["messages"][0]["role"] == "user" and saved["messages"][-1]["content"] == out
    assert agent.list_conversations()[0]["title"] == "write hello then read it"

    # schedule maths (Europe/London)
    mon = datetime(2026, 9, 7, 10, 0, tzinfo=agent.TZ)  # Monday
    assert agent.next_run({"type": "interval", "minutes": 480}, mon) == datetime(2026, 9, 7, 18, 0, tzinfo=agent.TZ)
    assert agent.next_run({"type": "times", "times": ["08:00", "13:00"], "days": [0, 1, 2, 3, 4]}, mon) == datetime(2026, 9, 7, 13, 0, tzinfo=agent.TZ)
    fri = datetime(2026, 9, 11, 14, 0, tzinfo=agent.TZ)
    assert agent.next_run({"type": "times", "times": ["09:00"], "days": [0, 1, 2, 3, 4]}, fri) == datetime(2026, 9, 14, 9, 0, tzinfo=agent.TZ)
    assert agent.next_run({"type": "once", "at": "2026-01-01T09:00"}, mon) is None
    assert agent.next_run({"type": "monthly", "day": 1, "time": "09:00"}, mon) == datetime(2026, 10, 1, 9, 0, tzinfo=agent.TZ)
    assert agent.describe_schedule({"type": "monthly", "day": 15, "time": "18:00"}) == "Monthly on day 15 at 6:00 pm"
    assert agent.describe_schedule({"type": "interval", "minutes": 480}) == "Every 8 hour(s)"
    assert agent.describe_schedule({"type": "times", "times": ["08:00", "20:00"], "days": list(range(7))}) == "Daily at 8:00 am, 8:00 pm"
    assert agent.describe_schedule({"type": "times", "times": ["09:30"], "days": [0, 1, 2, 3, 4]}) == "Every weekday at 9:30 am"

    # automations store + skills
    a = agent.save_automation({"name": "A", "prompt": "p", "schedule": {"type": "interval", "minutes": 60}})
    assert a["next_run"] and agent.get_automation(a["id"])["schedule_text"] == "Hourly"
    assert agent.delete_automation(a["id"]) and agent.get_automation(a["id"]) is None
    p = agent.create_skill("My Skill", "does things", "# Body\nsteps", {"go.py": "print(1)"})
    s = agent.list_skills()[0]
    assert s["name"] == "my-skill" and s["description"] == "does things" and s["scripts"] == ["go.py"], s
    assert "# Body" in agent.read_skill("my-skill") and "scripts/go.py" in agent.read_skill("my-skill")
    assert "run_command" in agent.system_prompt() and "my-skill" in agent.system_prompt()
    # RRULE schedules (Zo syntax)
    assert agent.rrule.next_after("FREQ=DAILY;BYHOUR=9;BYMINUTE=0", mon) == datetime(2026, 9, 8, 9, 0, tzinfo=agent.TZ)
    assert agent.rrule.next_after("FREQ=WEEKLY;BYDAY=MO,WE,FR;BYHOUR=14;BYMINUTE=30", mon) == datetime(2026, 9, 7, 14, 30, tzinfo=agent.TZ)
    assert agent.rrule.next_after("FREQ=HOURLY;INTERVAL=6", mon) == datetime(2026, 9, 7, 12, 0, tzinfo=agent.TZ)
    assert agent.rrule.next_after("FREQ=MINUTELY;INTERVAL=30", mon.replace(minute=10)) == datetime(2026, 9, 7, 10, 30, tzinfo=agent.TZ)
    assert agent.rrule.next_after("FREQ=MONTHLY;BYMONTHDAY=1;BYHOUR=9;BYMINUTE=0", mon) == datetime(2026, 10, 1, 9, 0, tzinfo=agent.TZ)
    assert agent.rrule.next_after("FREQ=YEARLY;BYMONTH=4;BYMONTHDAY=3;BYHOUR=13;BYMINUTE=0;COUNT=1", mon) == datetime(2027, 4, 3, 13, 0, tzinfo=agent.TZ)
    assert agent.rrule.is_once("FREQ=DAILY;BYHOUR=14;BYMINUTE=0;COUNT=1") and not agent.rrule.is_once("FREQ=DAILY;BYHOUR=14")
    assert agent.rrule.describe("FREQ=WEEKLY;BYDAY=MO,WE;BYHOUR=14;BYMINUTE=30") == "Mon, Wed at 2:30 pm"
    for bad in ("FREQ=ONCE", "DTSTART=20260101;FREQ=DAILY", "FREQ=DAILY;BYHOUR=25"):
        try: agent.rrule.parse(bad); raise AssertionError(bad)
        except ValueError: pass
    a = agent.save_automation({"name": "R", "prompt": "p", "schedule": "FREQ=HOURLY;INTERVAL=6"})
    assert a["schedule"]["type"] == "rrule" and a["schedule_text"] == "Every 6 hours" and a["next_run"], a
    agent.delete_automation(a["id"])
    # file edit operations, all or nothing
    f = agent.WORKSPACE / "Projects/t/ops.txt"; f.write_text("alpha\nbeta\ngamma\n")
    out = agent.files.edit_file(str(f), [{"op": "replace_block", "block": "beta", "text": "BETA"}, {"op": "insert_after", "block": "BETA\n", "text": "delta\n"}, {"op": "append_line", "text": "end"}])
    assert "3 operations" in out and f.read_text() == "alpha\nBETA\ndelta\ngamma\nend\n", (out, f.read_text())
    assert "occurs 0 times" in agent.files.edit_file(str(f), [{"op": "delete_block", "block": "nope"}]) and "end" in f.read_text()
    assert "2-3 of 5" in agent.files.read_file(str(f), 2, 3) and "2: BETA" in agent.files.read_file(str(f), 2, 3)
    # scopes: presets and keys
    names = lambda ts: {t["function"]["name"] for t in ts}
    assert "run_command" not in names(agent.scopes.filter_tools(agent.BUILTIN_TOOLS, "workspace")) and "read_file" in names(agent.scopes.filter_tools(agent.BUILTIN_TOOLS, "workspace"))
    assert names(agent.scopes.filter_tools(agent.BUILTIN_TOOLS, ["files:read"])) <= agent.scopes.SCOPES["files:read"] and agent.scopes.filter_tools(agent.BUILTIN_TOOLS, "chat") == []
    assert agent.scopes.claude_disallow("read_only") == ["Bash", "Write", "Edit", "NotebookEdit"] and agent.scopes.claude_disallow("all") == []
    # rules objects reach the prompt
    r = agent.create_rule("Never email clients", "when drafting outreach")
    assert "Never email clients (when: when drafting outreach)" in agent.system_prompt() and agent.delete_rule(r["id"])
    # services: PORT only for networked modes, private URL for http
    t = agent.save_task({"name": "My Api", "command": "python3 -m http.server $PORT", "mode": "http", "port": 8090})
    assert t["label"] == "my-api" and agent.services.service_env(t)["PORT"] == "8090" and t["url"].endswith("/api/svc/my-api/"), t
    assert "PORT" not in agent.services.service_env(agent.save_task({"name": "w", "command": "x", "mode": "process"}))
    print("OK: agent loop, persistence, schedule maths, rrule, automations store, skills, file ops, scopes, rules, services")


asyncio.run(main())
