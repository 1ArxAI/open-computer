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
    print("OK: agent loop, persistence, schedule maths, automations store, skills")


asyncio.run(main())
