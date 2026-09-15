"""Block diagrams from D2 source (https://d2lang.com). Needs the `d2` command on the box."""
import asyncio
import shutil
from datetime import datetime
from typing import Optional

from .config import TZ, WORKSPACE, _ws_path


async def generate_diagram(code: str, out_path: Optional[str] = None) -> str:
    if not shutil.which("d2"):
        return "Diagrams need the `d2` command. Install it from https://d2lang.com/tour/install, then try again."
    out = _ws_path(out_path) if out_path else WORKSPACE / "Projects" / "media" / f"diagram-{datetime.now(TZ).strftime('%Y%m%d-%H%M%S')}.svg"
    out.parent.mkdir(parents=True, exist_ok=True)
    proc = await asyncio.create_subprocess_exec("d2", "-", str(out), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    stdout, _ = await asyncio.wait_for(proc.communicate(code.encode()), timeout=120)
    if proc.returncode != 0:
        return f"d2 failed: {stdout.decode('utf-8', 'replace')[-600:]}"
    return f"Diagram saved to {out.relative_to(WORKSPACE) if out.is_relative_to(WORKSPACE) else out}"
