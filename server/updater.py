"""
Open Computer Update Manager
Handles automatic update checks against upstream git repository / GitHub API,
and one-click self-updating with graceful service restart.
"""

import asyncio
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

# Base directory of Open Computer repository
HOME = Path(os.environ.get("SU_HOME", str(Path(__file__).resolve().parent.parent)))

_update_cache: Dict[str, Any] = {}
_update_lock = asyncio.Lock()


def get_repo_dir() -> Path:
    return HOME


def _run_git_sync(args: list, timeout: int = 15) -> Dict[str, Any]:
    """Run a git command synchronously with timeout."""
    git_bin = shutil.which("git")
    if not git_bin:
        return {"ok": False, "out": "", "err": "git executable not found in PATH", "code": 127}
    try:
        p = subprocess.run(
            [git_bin] + args,
            cwd=str(HOME),
            capture_output=True,
            text=True,
            timeout=timeout
        )
        return {
            "ok": p.returncode == 0,
            "out": p.stdout.strip(),
            "err": p.stderr.strip(),
            "code": p.returncode
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "out": "", "err": f"git {' '.join(args)} timed out after {timeout}s", "code": 124}
    except Exception as e:
        return {"ok": False, "out": "", "err": str(e), "code": 1}


async def run_git_async(args: list, timeout: int = 30) -> Dict[str, Any]:
    """Run git command in an executor to avoid blocking the event loop."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _run_git_sync, args, timeout)


def get_repo_slug() -> str:
    """Extract GitHub owner/repo from git remote origin URL, defaulting to shieldspprt/open-computer."""
    res = _run_git_sync(["remote", "get-url", "origin"], timeout=5)
    if res["ok"] and res["out"]:
        m = re.search(r"github\.com[:/]([^/]+/[^/.]+)(?:\.git)?", res["out"])
        if m:
            return m.group(1)
    return "shieldspprt/open-computer"


def get_current_version() -> Dict[str, str]:
    """Get currently checked out commit, branch, and last commit info."""
    rev = _run_git_sync(["rev-parse", "--short", "HEAD"], timeout=5)
    branch = _run_git_sync(["rev-parse", "--abbrev-ref", "HEAD"], timeout=5)
    msg = _run_git_sync(["log", "-1", "--format=%s"], timeout=5)
    date_str = _run_git_sync(["log", "-1", "--format=%cd", "--date=relative"], timeout=5)
    full_sha = _run_git_sync(["rev-parse", "HEAD"], timeout=5)

    return {
        "commit": rev["out"] if rev["ok"] else "unknown",
        "commit_long": full_sha["out"] if full_sha["ok"] else "unknown",
        "branch": branch["out"] if branch["ok"] and branch["out"] != "HEAD" else "main",
        "message": msg["out"] if msg["ok"] else "",
        "date": date_str["out"] if date_str["ok"] else "",
        "repo": get_repo_slug(),
    }


def _check_github_api_fallback(repo: str, branch: str, timeout: int = 10) -> Optional[Dict[str, Any]]:
    """Fallback check using GitHub public REST API via standard library urllib."""
    import json
    import urllib.request

    url = f"https://api.github.com/repos/{repo}/commits/{branch}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "OpenComputer-UpdateChecker", "Accept": "application/vnd.github.v3+json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                sha = data.get("sha", "")
                commit_info = data.get("commit", {})
                msg = commit_info.get("message", "").splitlines()[0] if commit_info.get("message") else ""
                date_iso = commit_info.get("committer", {}).get("date", "")
                return {
                    "sha": sha[:7],
                    "sha_full": sha,
                    "message": msg,
                    "date": date_iso,
                    "html_url": data.get("html_url") or f"https://github.com/{repo}/commit/{sha}"
                }
    except Exception as e:
        print(f"GitHub API update check fallback error: {e}")
    return None


async def check_for_updates(force: bool = False) -> Dict[str, Any]:
    """
    Check if an update is available on upstream remote.
    Caches check results for 15 minutes unless force=True.
    """
    global _update_cache
    now = time.time()
    
    if not force and _update_cache and (now - _update_cache.get("cached_at", 0) < 900):
        return _update_cache

    current = get_current_version()
    branch = current["branch"] or "main"
    repo = current["repo"]
    release_url = f"https://github.com/{repo}/commits/{branch}"

    update_available = False
    latest_commit = current["commit"]
    latest_message = current["message"]
    latest_date = current["date"]
    commits_behind = 0

    # 1. Try git fetch first
    fetch_res = await run_git_async(["fetch", "origin", branch], timeout=15)
    if not fetch_res["ok"]:
        # Fallback to fetching via public HTTPS if origin remote failed
        fetch_res = await run_git_async(["fetch", f"https://github.com/{repo}.git", branch], timeout=15)

    if fetch_res["ok"]:
        count_res = await run_git_async(["rev-list", "--count", "HEAD..FETCH_HEAD"], timeout=5)
        if count_res["ok"] and count_res["out"].isdigit():
            commits_behind = int(count_res["out"])

        latest_rev = await run_git_async(["rev-parse", "--short", "FETCH_HEAD"], timeout=5)
        latest_msg = await run_git_async(["log", "-1", "--format=%s", "FETCH_HEAD"], timeout=5)
        latest_dt = await run_git_async(["log", "-1", "--format=%cd", "--date=relative", "FETCH_HEAD"], timeout=5)

        if latest_rev["ok"] and latest_rev["out"]:
            latest_commit = latest_rev["out"]
        if latest_msg["ok"] and latest_msg["out"]:
            latest_message = latest_msg["out"]
        if latest_dt["ok"] and latest_dt["out"]:
            latest_date = latest_dt["out"]

        if commits_behind > 0 or (latest_commit != "unknown" and latest_commit != current["commit"]):
            update_available = True
    else:
        # 2. Fallback to GitHub API
        loop = asyncio.get_running_loop()
        api_data = await loop.run_in_executor(None, _check_github_api_fallback, repo, branch, 10)
        if api_data:
            latest_commit = api_data["sha"]
            latest_message = api_data["message"]
            latest_date = api_data["date"]
            if current["commit"] != "unknown" and latest_commit != current["commit"]:
                update_available = True
                commits_behind = 1

    result = {
        "ok": True,
        "update_available": update_available,
        "current_commit": current["commit"],
        "latest_commit": latest_commit,
        "current_branch": branch,
        "commits_behind": commits_behind,
        "current_message": current["message"],
        "latest_message": latest_message,
        "latest_date": latest_date,
        "release_url": release_url,
        "repo": repo,
        "last_checked": datetime.now(timezone.utc).isoformat(),
        "cached_at": now,
    }
    _update_cache = result
    return result


async def _schedule_restart():
    """Wait briefly for HTTP response to be flushed, then restart service."""
    await asyncio.sleep(1.5)
    for svc in dict.fromkeys((os.environ.get("SU_SERVICE_NAME", "").strip() or "su", "su")):
        try:
            proc = await asyncio.create_subprocess_exec(
                "sudo", "-n", "systemctl", "restart", svc,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            await asyncio.wait_for(proc.wait(), timeout=3)
        except Exception:
            pass

    # 2. In all cases, exiting the process causes systemd (Restart=always) to cleanly restart uvicorn
    os._exit(0)


async def _restore_stash() -> str:
    """Re-apply the pre-update stash. On conflict, put the tree back at HEAD so the service can still start;
    the stash survives a failed pop, so nothing is lost. (A half-applied pop once left merge markers in two
    modules and the gateway crash-looped behind a Cloudflare 502.)"""
    pop = await run_git_async(["stash", "pop"], timeout=10)
    if pop["ok"]:
        return ""
    await run_git_async(["reset", "--hard", "HEAD"], timeout=10)
    return " Local edits conflicted with the update and were kept in git stash (see: git stash show -p) instead of being applied."


async def apply_update() -> Dict[str, Any]:
    """
    Apply updates:
    1. Stash any local modifications to tracked files
    2. Git pull the latest commits from upstream
    3. Update pip requirements if requirements.txt exists and changed
    4. Trigger a graceful process restart
    """
    if _update_lock.locked():
        return {"ok": False, "error": "An update is already in progress."}

    async with _update_lock:
        current = get_current_version()
        branch = current["branch"] or "main"
        repo = current["repo"]
        old_commit = current["commit"]

        # Check for dirty working tree
        status_res = await run_git_async(["status", "--porcelain", "--untracked-files=no"], timeout=10)  # untracked files never conflict
        stashed = False
        if status_res["ok"] and status_res["out"]:
            stash_res = await run_git_async(["stash", "push", "-m", "opencomputer-autoupdate-backup"], timeout=15)
            if stash_res["ok"]:
                stashed = True

        # Pull latest changes
        pull_res = await run_git_async(["pull", "origin", branch], timeout=45)
        if not pull_res["ok"]:
            # Fallback to direct HTTPS pull
            pull_res = await run_git_async(["pull", f"https://github.com/{repo}.git", branch], timeout=45)

        if not pull_res["ok"]:
            if stashed:
                await _restore_stash()
            return {
                "ok": False,
                "error": f"Failed to pull latest changes: {pull_res['err'] or pull_res['out']}"
            }

        # Restore stashed changes if any
        note = await _restore_stash() if stashed else ""

        # Check and update Python requirements if venv pip exists
        pip_path = HOME / "server" / "venv" / "bin" / "pip"
        req_path = HOME / "server" / "requirements.txt"
        if pip_path.exists() and req_path.exists():
            try:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(
                    None,
                    lambda: subprocess.run(
                        [str(pip_path), "install", "-q", "-r", str(req_path)],
                        cwd=str(HOME),
                        capture_output=True,
                        timeout=120
                    )
                )
            except Exception as e:
                print(f"Warning: pip install requirements failed: {e}")

        # Invalidate update cache
        global _update_cache
        _update_cache.clear()

        # Get new version info
        new_version = get_current_version()
        new_commit = new_version["commit"]

        # Schedule service restart in background
        asyncio.create_task(_schedule_restart())

        return {
            "ok": True,
            "message": f"Successfully updated from {old_commit} to {new_commit}. Open Computer is restarting...{note}",
            "old_commit": old_commit,
            "new_commit": new_commit,
            "branch": branch,
            "latest_message": new_version["message"]
        }
