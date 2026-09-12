# 🖥️ Open Computer

**Your personal, self-hosted AI cloud computer.** Turn any Linux VPS into an autonomous personal computer with a web desktop, native Linux terminal, file explorer, 24/7 background agents, and scheduled automations.

[🌐 **Live Website & Interactive Showcase**](https://shieldspprt.github.io/open-computer/) · [⭐ **GitHub Repository**](https://github.com/shieldspprt/open-computer)

---

> [!NOTE]
> ### 💡 Inspired by Zo Computer
> This open-source project is directly inspired by the visionary work of the team at **[Zo Computer](https://zo.computer)**. Zo pioneered the concept of a cloud-native personal computer for AI.
> 
> If you want a fully managed, high-performance cloud computer that works out of the box with zero setup, we warmly recommend checking out **[zo.computer](https://zo.computer)**.
> 
> **Open Computer** is a community-driven, self-hosted open-source alternative built for developers, tinkerers, and self-hosters who want to run this entire experience on their own personal Linux server, VPS, or homelab.

---

## ❤️ The Story: Recreating My Favorite Tool in the World

> *"Once you experience what it feels like to have a real personal computer powered by an AI API, there is simply no coming back."*

I'll be completely honest: **I really love [Zo Computer](https://zo.computer).**

The first time I used Zo, something deep clicked for me. If you have an AI API key (from Anthropic, Google, or OpenAI), Zo is the last thing you will ever need. It isn't just another chat interface or a plugin squeezed into an editor sidebar—it is a **pure superpower**. It gives you a living, autonomous computer in the cloud that never sleeps. A companion that can write code, run background scrapers, manage files, execute shell commands, and do real work for you while you're offline.

Once you start using a tool like this in your daily workflow, your entire standard for computing changes. Going back to isolated local scripts, manual SSH sessions, and fragmented browser tabs feels like stepping back a decade. **There is simply no coming back.**

So I decided to recreate my favorite product—out of sheer love, homage, the deep fun of hacking from scratch, and the practical need to have this superpower running 24/7 on my own VPS hardware for maximum productivity and freedom.

### 🌊 The Emerging Trend: Why GUI + Global Tops the Agentic OS Wave

The world is waking up to this new era. Projects like **Omarchy** are proving that an OS with an AI agent as master is the next frontier of operating systems. The developer ecosystem is moving at breakneck speed toward autonomous environments.

Yet, as exciting as terminal-only or raw agentic OS experiments are, **Zo and Open Computer still top the paradigm for two fundamental reasons: it is a true visual GUI, and it is Global.**

1. **A True Visual GUI (Human + AI in Harmony)**: Purely headless or terminal-bound agent OSes can feel isolating and opaque. Humans are visual creatures. We want to see our workspace—a dual-pane file explorer, live tabbed code editing with syntax highlighting, visual system metrics, scheduled automations cards, and a dedicated Recycle Bin. Open Computer preserves full human agency alongside autonomous AI power. You are in the cockpit, with the AI as your tireless co-pilot.
2. **Global & Everywhere**: It lives in the cloud, accessible instantly from any browser on earth. Whether you're on a dual-monitor desktop at your desk, typing on a cheap Chromebook, holding an iPad at a coffee shop, or checking a task from your phone on the train—your entire environment, terminal sessions, background daemons, and AI agent travel with you anywhere in the world.

Open Computer is a labor of love: 100% self-hosted, 100% open-source, and free forever for anyone with a Linux server.

---

## 📸 Screenshots

| **AI Workspace & Home** | **Native Linux Terminal** |
|:---:|:---:|
| ![Home](docs/images/home.png) | ![Terminal](docs/images/terminal.png) |

| **File Explorer & Recycle Bin** | **Integrated Code Editor** |
|:---:|:---:|
| ![Files](docs/images/files.png) | ![Editor](docs/images/editor.png) |

| **24/7 Scheduled Automations** | **Integrations & MCP Hub** |
|:---:|:---:|
| ![Automations](docs/images/automations.png) | ![Integrations](docs/images/integrations.png) |

---

## ✨ Features at a Glance

- 🧠 **Bring Your Own Models**: Chat with any LLM using your own keys—OpenRouter, Anthropic (Claude), OpenAI, Google Gemini, DeepSeek, NVIDIA NIM, or run completely offline with local models via **Ollama** or **vLLM**.
- 💻 **Real In-Browser Terminal**: A true Linux PTY powered by `xterm.js` with ANSI color support, window resizing, and direct shell execution.
- 📁 **File Manager & Code Editor**: Browse your home directory, view/edit source code in a tabbed editor, upload files, and safely manage deletions with an integrated **Recycle Bin**.
- ⏰ **Scheduled Automations**: Tell your agent in plain English to run tasks periodically (e.g. *"Check competitor prices every morning at 8 AM and email me the summary"*).
- 🔄 **24/7 Background Tasks**: Keep long-running processes alive (web scrapers, Telegram bots, Discord bots, monitors) with automatic supervisor restarts.
- 🔌 **Integrations & MCP Tools**: Connect remote Model Context Protocol (MCP) servers and third-party APIs.
- 🛡️ **Hardened Security**: Built with defense-in-depth protection against Server-Side Request Forgery (SSRF), prompt injection, directory traversal, and server reconnaissance.

---

## ⚡ The 2-Minute AI Setup (Zero Manual Commands)

> [!TIP]
> **Don't want to type Linux commands manually? You don't have to!**
> 
> Simply copy this prompt, fill in your server details, and paste it into your favorite AI coding assistant (we strongly recommend **Antigravity** or Claude Code):
> 
> ```text
> Here is my Ubuntu VPS (IP: <YOUR_SERVER_IP>, user: suop, password: <YOUR_PASSWORD>).
> 
> Please connect to my server via SSH, clone https://github.com/shieldspprt/open-computer.git, run bash install.sh, and set up Open Computer for me.
> ```
> 
> Your AI assistant will handle package installations, virtual environment creation, systemd service configuration, and security checks automatically. You'll be ready to log in and chat in **2 minutes flat!**

---

## 🚀 Manual Beginner-Friendly Step-by-Step Guide

Prefer running the commands yourself? Follow these 4 easy steps:

### Prerequisites
- A Linux server or VPS running **Ubuntu 22.04 or 24.04** (e.g. Hetzner, Contabo, DigitalOcean, Linode, or a spare PC/Raspberry Pi). A $4–$6/month box with 2GB+ RAM is plenty!
- SSH access to your server.

---

### Step 1: Create a Dedicated User

For your server's security, Open Computer should run as a standard user (not as `root`).

Log into your server via SSH as `root`, then run:

```bash
# 1. Create a new user (e.g. named 'suop')
adduser suop

# 2. Grant sudo permissions
usermod -aG sudo suop

# 3. Switch to your new user
su - suop
```

---

### Step 2: Download and Install

While logged in as your new user, clone the repository and run the automated installer:

```bash
git clone https://github.com/shieldspprt/open-computer.git
cd open-computer
bash install.sh
```

**What the installer does automatically:**
1. Installs required packages (Python, venv, git, curl, ripgrep).
2. Sets up an isolated Python virtual environment.
3. Generates secure random session secrets and tokens in `.env`.
4. Configures and enables a `systemd` background service (`su.service`).

When finished, the script will output:
```text
SU is running. Sign in with user 'suop' and your Linux password.
```

---

### Step 3: Access Open Computer

By default, Open Computer listens securely on `127.0.0.1:8000` so it is not exposed naked to the internet. Choose either method to access it:

#### Option A: Quick SSH Port Forwarding (Easiest for local use)
From your laptop or local computer terminal, run:

```bash
ssh -L 8000:127.0.0.1:8000 suop@your-server-ip
```

Now open **http://localhost:8000** in your web browser!

#### Option B: Free Cloudflare Tunnel (Recommended for remote access)
To access your personal computer from anywhere with free SSL (`https://yourname.domain.com`):
1. Create a free tunnel in your **Cloudflare Zero Trust** dashboard pointing to `http://localhost:8000`.
2. Add your tunnel token to `.env` as `CLOUDFLARE_TUNNEL_TOKEN=...`
3. Start the tunnel container:
   ```bash
   docker compose up -d cloudflared
   ```

---

### Step 4: Sign In and Add Your First Model Key

1. **Sign In**: Enter your Linux username (e.g. `suop`) and the Linux password you set in Step 1.
2. **Add a Model Key**:
   - In the web interface, click **Settings (⚙️)** in the bottom-left corner.
   - Click **AI** or **Advanced**.
   - Paste an API key from [OpenRouter](https://openrouter.ai), [Anthropic](https://console.anthropic.com), [OpenAI](https://platform.openai.com), or [Google AI Studio](https://aistudio.google.com).
   - *(Optional)* If you have local Ollama running, enter `http://127.0.0.1:11434/v1` under `LOCAL_LLM_URL`.
3. **Start Creating**: Type your first prompt in the home chat box and watch your personal AI computer work!

---

## 🛠️ Managing Your Service

```bash
# Restart Open Computer
sudo systemctl restart su

# Check live logs
journalctl -u su -f

# Check service status
systemctl status su
```

---

## 🔒 Security Principles

Open Computer is designed for **single-user personal ownership**:

- **Local Process Boundary**: The AI agent runs with the permissions of your dedicated Linux user. It cannot escape into other users' private directories.
- **Protected Environment**: Dangerous files like `.env`, SSH keys (`~/.ssh`), GPG keys, and gateway code are shielded from the agent's file tools.
- **SSRF Defense**: Network web fetches automatically block private IP ranges (`10.0.0.0/8`, `192.168.0.0/16`, `127.0.0.0/8`, `169.254.169.254` cloud metadata) and unsafe ports.
- **Session Protection**: Automatic sign-out after 10 minutes of inactivity (`SU_SESSION_IDLE`) and brute-force IP rate limiting.

---

## 🤝 Contributing

Contributions, bug reports, and suggestions are warmly welcome!
- Check out [CONTRIBUTING.md](CONTRIBUTING.md) to get started.
- For vulnerability reports, please review [SECURITY.md](SECURITY.md).

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).

---

*Special thanks to the [Zo Computer](https://zo.computer) team for inspiring this self-hosted journey.*
