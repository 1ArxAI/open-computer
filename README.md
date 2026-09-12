# SU

A self-hosted AI operator for a Linux server you own. One person, one box, your own model keys.

SU is a web app on your server with an agent that has a shell, files, a scheduler and a process supervisor. You talk to it in a browser; it does the work on the machine and reports back. When you are away it keeps running your automations and 24/7 tasks.

**What you get**

- Chats with any model you hold a key for: OpenRouter, Anthropic, OpenAI, Gemini, DeepSeek, NVIDIA, or a local OpenAI-compatible server. Claude Code and Gemini CLI are used when installed.
- Files: a browser for the home directory with an editor, uploads and trash.
- Automations: prompts that run on a schedule with the same tools, with a log per run and email on failure.
- Tasks: long-running processes SU keeps alive, restarts, and logs.
- Skills: reusable know-how as `Skills/<name>/SKILL.md`, compatible with the open Agent Skills format.
- Terminal: a real shell in the browser, as the SU user.
- Settings: keys, connected apps through Pipedream, remote MCP servers, memory.

**What it is not**: multi-user, hosted, or safe on a shared machine. The agent runs as your Linux user with everything that user can do. Give it its own user on its own server.

## Install (Ubuntu 22.04 or 24.04)

As the Linux user who will own SU, with sudo:

```bash
git clone <repo-url> su && cd su && bash install.sh
```

The installer adds a few packages, creates a Python environment, writes `.env` with a generated token, installs a systemd service and starts it. About two minutes.

Then:

1. Make it reachable. SU listens on `127.0.0.1:8000` only. Either
   - Cloudflare tunnel: create one in Zero Trust › Networks › Tunnels pointing at `http://localhost:8000`, put its token in `.env` as `CLOUDFLARE_TUNNEL_TOKEN`, run `docker compose up -d cloudflared`; or
   - a reverse proxy you already have (Caddy, nginx) with HTTPS in front of port 8000.
   Set `SU_PUBLIC_URL` in `.env` to that address.
2. Open it. Sign in with your Linux username and password. The session ends after 10 minutes without activity (`SU_SESSION_IDLE`) and after 12 hours at most.
3. Add a model key in Settings › Advanced, or in `.env`. Pick a default in the model picker.
4. Optional: `docker compose up -d browserless` for JavaScript-heavy pages and screenshots; install Claude Code (`npm i -g @anthropic-ai/claude-code`, then `claude` to sign in) to use it as a model.

Restart after editing `.env`: `sudo systemctl restart su`. Logs: `journalctl -u su -f`.

## How it behaves

`workspace/SU.md` is the operating manual every model reads: answer first, plan briefly, act with tools, verify, report. Edit it to change how SU works. `workspace/RULES.md` holds your rules and wins over the manual. Secrets are environment variables; SU refers to them by name and never prints values.

## Security model

- **The boundary is the Linux user.** SU runs as one dedicated user and the agent can do what that user can do, shell included. Do not run it as root, and do not run it as a user whose home holds things the agent must never touch.
- **File tools are guarded.** `read_file`, `write_file`, `edit_file`, `list_dir`, `grep` and `@file` references refuse `.env`, SSH and GPG keys, shell start-up files, server code and system secrets. This stops accidents and injected reads through the tools. It is not a sandbox: `run_command` is the user's shell.
- **Fetched content is data.** Web pages, search results, emails and files reach the model wrapped as untrusted content with instructions not to obey them. `web_fetch` refuses private, loopback and cloud-metadata addresses and re-checks every redirect; list hosts you trust in `SU_FETCH_ALLOWED_HOSTS`.
- **Email cannot exfiltrate.** `send_email` delivers only to the owner's addresses and to what you put in `SU_MAIL_SEND_TO`. Incoming email is accepted from allowed senders only, after parsing the real address.
- **Network.** The gateway binds to 127.0.0.1. Proxy headers are trusted only from a local proxy (or with `SU_TRUST_PROXY=1`). Five failed sign-ins from one address lock login for 10 minutes. Responses carry a Content-Security-Policy and `noindex`.
- **Secrets** live in `.env` (mode 600) and are referred to by name in prompts; values are never printed.

Found a hole? See [SECURITY.md](SECURITY.md).

## Layout

```
server/        FastAPI gateway (main.py) and the agent (agent.py); templates/index.html is the whole UI
workspace/     SU.md, RULES.md, Projects/ (one folder per job), Skills/
data/          conversations, automations, tasks, runs, memory (JSON files)
.env           your settings and keys
systemd/       the service unit installed by install.sh
```

## API

Everything the UI does is an HTTP API behind the Bearer token in `.env`:

```bash
curl -s http://127.0.0.1:8000/zo/ask -H "Authorization: Bearer $SU_TOKEN" -H "Content-Type: application/json" -d '{"input":"What is running on this box?"}'
```

## Updating

```bash
git pull && server/venv/bin/pip install -q -r server/requirements.txt && sudo systemctl restart su
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Small, focused pull requests; no new dependencies without a reason in the description.

## Licence

MIT. Inspired by Zo Computer; not affiliated with it.
