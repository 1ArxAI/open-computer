#!/usr/bin/env bash
# SU installer for Ubuntu 22.04 / 24.04. Run as the Linux user who will own SU (needs sudo):  bash install.sh
set -euo pipefail
HOME_DIR="$(cd "$(dirname "$0")" && pwd)"
ME="$(id -un)"
[ "$ME" != "root" ] || { echo "Run this as a normal user with sudo, not as root (SU signs you in with that user's Linux password)."; exit 1; }
command -v sudo >/dev/null || { echo "sudo is required"; exit 1; }

echo "== packages"
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3 python3-venv python3-dev build-essential git curl openssl ripgrep >/dev/null

echo "== python environment"
python3 -m venv "$HOME_DIR/server/venv"
"$HOME_DIR/server/venv/bin/pip" install -q --upgrade pip
"$HOME_DIR/server/venv/bin/pip" install -q -r "$HOME_DIR/server/requirements.txt"

echo "== settings"
mkdir -p "$HOME_DIR/workspace/Projects" "$HOME_DIR/workspace/Skills" "$HOME_DIR/data"
if [ ! -f "$HOME_DIR/.env" ]; then
  cp "$HOME_DIR/.env.example" "$HOME_DIR/.env"
  sed -i "s|^SU_TOKEN=.*|SU_TOKEN=$(openssl rand -hex 24)|; s|^SU_SESSION_SECRET=.*|SU_SESSION_SECRET=$(openssl rand -hex 32)|" "$HOME_DIR/.env"
  chmod 600 "$HOME_DIR/.env"
fi
[ -f "$HOME_DIR/workspace/RULES.md" ] || cp "$HOME_DIR/workspace/RULES.example.md" "$HOME_DIR/workspace/RULES.md"

echo "== service"
sed "s|__USER__|$ME|g; s|__HOME__|$HOME_DIR|g" "$HOME_DIR/systemd/su.service" | sudo tee /etc/systemd/system/su.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now su >/dev/null
sleep 3
if curl -fsS "http://127.0.0.1:$(grep -E '^SU_PORT=' "$HOME_DIR/.env" | cut -d= -f2)/health" >/dev/null; then
  echo
  echo "SU is running. Sign in with user '$ME' and your Linux password."
  echo "It listens on 127.0.0.1 only; put a Cloudflare tunnel or a reverse proxy in front (see README), then add a model key in Settings."
  echo "Script token (for the API):  $(grep -E '^SU_TOKEN=' "$HOME_DIR/.env" | cut -d= -f2)"
else
  echo "The service did not answer. Check:  journalctl -u su -n 50"; exit 1
fi
