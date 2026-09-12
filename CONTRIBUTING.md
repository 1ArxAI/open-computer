# Contributing

The whole product is three files: `server/main.py` (gateway), `server/agent.py` (agent, tools, scheduler) and `server/templates/index.html` (UI). Read the one you are changing end to end first.

## Rules

- One change per pull request, with the reason in the description.
- No new dependencies without saying what they replace and why a few lines of stdlib will not do.
- Paths come from `SU_HOME` and the home directory, never hardcoded.
- Anything the agent can be tricked into doing through fetched content is a bug; anything it can do because the user asked is a feature.
- Keep British English and plain sentences in prompts and UI text.

## Before you push

```bash
cd server && venv/bin/python -m py_compile main.py agent.py && SU_HOME=$(mktemp -d) venv/bin/python test_agent.py
```

Then run it: `sudo systemctl restart su`, sign in, send a chat, run one automation, start one task.
