"""File tools: read by line range or PDF pages, edit with an operation list, copy, list with ignore patterns, grep with filters.
Every path goes through is_safe_file_path."""
import asyncio
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

from .config import WORKSPACE, _tool
from .security import is_safe_file_path

MAX_CHARS = 60000


def _guard(raw, write=False):
    safe, err, p = is_safe_file_path(raw, allow_write=write)
    return (None, f"<security_error>Blocked: {err}</security_error>") if not safe else (p, "")


def read_file(path: str, start_line: Optional[int] = None, end_line: Optional[int] = None, pdf_start_page: Optional[int] = None, pdf_end_page: Optional[int] = None) -> str:
    p, err = _guard(path)
    if err:
        return err
    if not p.exists() or p.is_dir():
        return f"File not found: {path}"
    if p.suffix.lower() == ".pdf":
        if not shutil.which("pdftotext"):
            return "PDF reading needs the `pdftotext` command (package poppler-utils). Install it, or convert the file first."
        cmd = ["pdftotext", "-layout"] + (["-f", str(pdf_start_page)] if pdf_start_page else []) + (["-l", str(pdf_end_page)] if pdf_end_page else []) + [str(p), "-"]
        text = subprocess.run(cmd, capture_output=True, text=True, timeout=120).stdout
        return f'<untrusted_file_content path="{p.name}" pages="{pdf_start_page or 1}-{pdf_end_page or "end"}">\n{text[:MAX_CHARS]}\n</untrusted_file_content>'
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    total = len(lines)
    if start_line or end_line:
        a = max(1, int(start_line or 1)); b = min(total, int(end_line or total))
        body = "\n".join(f"{i}: {lines[i - 1]}" for i in range(a, b + 1))
        return f'<untrusted_file_content path="{p.name}" lines="{a}-{b} of {total}">\n{body[:MAX_CHARS]}\n</untrusted_file_content>'
    body = "\n".join(lines)
    note = f" (truncated to {MAX_CHARS} chars of {len(body)}; use start_line/end_line)" if len(body) > MAX_CHARS else ""
    return f'<untrusted_file_content path="{p.name}" lines="{total}"{note}>\n{body[:MAX_CHARS]}\n</untrusted_file_content>'


def edit_file(path: str, operations: Optional[List[Dict]] = None, old: Optional[str] = None, new: Optional[str] = None) -> str:
    """operations: [{op: replace_block|insert_after|insert_before|delete_block|append_line, block?, text?}]. The old/new pair is the single-replace form."""
    p, err = _guard(path, write=True)
    if err:
        return err
    if not p.exists() or p.is_dir():
        return f"File not found: {path}"
    text = p.read_text(encoding="utf-8")
    ops = list(operations or [])
    if old is not None and not ops:
        ops = [{"op": "replace_block", "block": old, "text": new or ""}]
    if not ops:
        return "Edit refused: give operations or old/new."
    for i, o in enumerate(ops, 1):
        kind, block, new_text = o.get("op"), o.get("block", ""), o.get("text", "")
        if kind == "append_line":
            text = text + ("" if text.endswith("\n") or not text else "\n") + new_text + "\n"
            continue
        n = text.count(block) if block else 0
        if n != 1:
            return f"Edit refused at operation {i} ({kind}): the block occurs {n} times, it must occur exactly once. Nothing was written."
        if kind == "replace_block":
            text = text.replace(block, new_text, 1)
        elif kind == "insert_after":
            text = text.replace(block, block + new_text, 1)
        elif kind == "insert_before":
            text = text.replace(block, new_text + block, 1)
        elif kind == "delete_block":
            text = text.replace(block, "", 1)
        else:
            return f"Edit refused at operation {i}: unknown op '{kind}'. Nothing was written."
    p.write_text(text, encoding="utf-8")
    return f"Edited {p} ({len(ops)} operation{'s' if len(ops) != 1 else ''})"


def copy_file(source: str, dest: str) -> str:
    src, err = _guard(source)
    if err:
        return err
    dst, err = _guard(dest, write=True)
    if err:
        return err
    if not src.exists():
        return f"Not found: {source}"
    if dst.is_dir():
        dst = dst / src.name
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True)
    else:
        shutil.copy2(src, dst)
    return f"Copied {src} -> {dst}"


def list_dir(path: Optional[str] = None, ignore: Optional[List[str]] = None) -> str:
    p, err = _guard(path or WORKSPACE)
    if err:
        return err
    if not p.exists() or not p.is_dir():
        return f"Directory not found: {path}"
    skip = set(ignore or []) | {".git", "node_modules", "__pycache__"}
    rows = [f"{'d' if x.is_dir() else 'f'} {x.name}" for x in sorted(p.iterdir()) if x.name not in skip]
    return "\n".join(rows)[:20000] or "(empty)"


async def grep(pattern: str, path: Optional[str] = None, include: Optional[str] = None, exclude: Optional[str] = None, case_sensitive: bool = True) -> str:
    root, err = _guard(path or WORKSPACE)
    if err:
        return err
    argv = ["grep", "-rnE" if case_sensitive else "-rniE", "-I", "--exclude-dir=.git", "--exclude-dir=node_modules", "--exclude-dir=.ssh", "--exclude=.env*"]
    if include:
        argv.append(f"--include={include}")
    if exclude:
        argv.append(f"--exclude={exclude}")
    proc = await asyncio.create_subprocess_exec(*argv, "--", pattern, str(root), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    out, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
    return "\n".join(out.decode("utf-8", "replace").splitlines()[:200]) or "No matches."


TOOLS = [
    _tool("copy_file", "Copy a file or folder inside the workspace.", {"source": {"type": "string"}, "dest": {"type": "string", "description": "File path, or a folder to copy into"}}),
]


async def handle(name: str, args: Dict) -> Optional[str]:
    if name == "read_file":
        return read_file(args["path"], args.get("start_line"), args.get("end_line"), args.get("pdf_start_page"), args.get("pdf_end_page"))
    if name == "edit_file":
        return edit_file(args["path"], args.get("operations"), args.get("old"), args.get("new"))
    if name == "copy_file":
        return copy_file(args["source"], args["dest"])
    if name == "list_dir":
        return list_dir(args.get("path"), args.get("ignore"))
    if name == "grep":
        return await grep(args["pattern"], args.get("path"), args.get("include"), args.get("exclude"), args.get("case_sensitive", True))
    return None
