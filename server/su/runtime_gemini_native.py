"""Tool loop on the native Gemini SDK."""
import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import signal
import smtplib
import subprocess
import tarfile
import time
import uuid
import io
from datetime import datetime, timedelta
from email.mime.text import MIMEText
import email.utils
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
from urllib.parse import urlparse, urljoin
from zoneinfo import ZoneInfo
import ipaddress
import socket
import httpx
import yaml
from openai import AsyncOpenAI

from .config import MAX_STEPS
from .conversations import save_conversation
from .providers import _gemini_client, _gemini_tools
from .tools import execute_tool

async def run_gemini_native(conv: Dict, user_input: str, model: str, tools: List[Dict], system: str, emit, mcp_servers: List[Dict]) -> str:
    from google.genai import types, errors
    client = _gemini_client()
    if conv.get("gemini_contents") and conv.get("gemini_seen") == len(conv.get("messages", [])):
        contents = [types.Content.model_validate(c) for c in conv["gemini_contents"]]
    else:  # turns were added by other models: rebuild a text-only history (no stale function-call parts)
        contents = []
        for m in conv.get("messages", []):
            txt = (m.get("content") or "").strip()
            if m.get("role") == "user" and txt:
                contents.append(types.Content(role="user", parts=[types.Part.from_text(text=txt)]))
            elif m.get("role") == "assistant" and (txt or m.get("tool_calls")):
                names = ", ".join(tc.get("function", {}).get("name", "tool") for tc in (m.get("tool_calls") or []))
                contents.append(types.Content(role="model", parts=[types.Part.from_text(text=txt or f"(used tools: {names})")]))
    contents.append(types.Content(role="user", parts=[types.Part.from_text(text=user_input)]))
    conv["messages"].append({"role": "user", "content": user_input}); save_conversation(conv)
    cfg = types.GenerateContentConfig(system_instruction=system, tools=_gemini_tools(tools), temperature=0.3,
                                      automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))
    final = ""
    for step in range(MAX_STEPS):
        resp = None
        for attempt, wait in enumerate((0, 5, 10, 20, 40)):
            if wait:
                await emit({"type": "status", "text": f"Gemini API is rate limiting; retrying in {wait}s (attempt {attempt + 1}/5)"})
                await asyncio.sleep(wait)
            else:
                await emit({"type": "status", "text": "Thinking" + (f" (step {step + 1})" if step else "")})
            try:
                resp = await _gemini_stream(client, model, contents, cfg, emit)
                break
            except errors.APIError as e:
                code = getattr(e, "code", None) or getattr(e, "status_code", None)
                if code in (429, 500, 502, 503) and attempt < 4:
                    continue
                final = f"Model error (google:{model}): {code} {getattr(e, 'message', str(e))[:400]}"
                if code == 429:
                    final += "\n\nGemini quota reached (free tier is 20 requests per day per model). Pick another Gemini model, enable billing on the key, or switch provider."
                await emit({"type": "error", "text": final})
                conv["messages"].append({"role": "assistant", "content": final})
                break
            except Exception as e:
                final = f"Model error (google:{model}): {type(e).__name__}: {str(e)[:400]}"
                await emit({"type": "error", "text": final})
                conv["messages"].append({"role": "assistant", "content": final})
                break
        if resp is None:
            break
        cand = (resp.candidates or [None])[0]
        content = cand.content if cand and cand.content else types.Content(role="model", parts=[types.Part.from_text(text=resp.text or "")])
        if not content.parts:
            content = types.Content(role="model", parts=[types.Part.from_text(text=resp.text or "")])
        contents.append(content)
        calls = [p for p in content.parts if p.function_call]
        text = "".join(p.text for p in content.parts if p.text and not p.thought)
        assistant = {"role": "assistant", "content": text}
        if calls:
            assistant["tool_calls"] = [{"id": f"g{step}_{i}", "type": "function", "function": {"name": p.function_call.name, "arguments": json.dumps(dict(p.function_call.args or {}))}} for i, p in enumerate(calls)]
        conv["messages"].append(assistant)
        if text and calls:
            await emit({"type": "text", "text": text})
        if not calls:
            final = text
            await emit({"type": "final", "text": final, "streamed": True})
            break
        parts = []
        for i, p in enumerate(calls):
            name, args = p.function_call.name, dict(p.function_call.args or {})
            await emit({"type": "tool_call", "name": name, "args": args})
            out = await execute_tool(name, args, mcp_servers)
            await emit({"type": "tool_result", "name": name, "output": out[:1500]})
            conv["messages"].append({"role": "tool", "tool_call_id": f"g{step}_{i}", "content": out})
            parts.append(types.Part.from_function_response(name=name, response={"result": out}))
        contents.append(types.Content(role="user", parts=parts))
        conv["gemini_contents"] = [c.model_dump(mode="json", exclude_none=True) for c in contents]
        save_conversation(conv)
    else:
        final = f"Stopped after {MAX_STEPS} steps."
        await emit({"type": "error", "text": final})
    conv["gemini_contents"] = [c.model_dump(mode="json", exclude_none=True) for c in contents]
    conv["gemini_seen"] = len(conv["messages"])
    return final


async def _gemini_stream(client, model, contents, cfg, emit):
    """Stream a native Gemini response, emitting text deltas; returns the final aggregated response."""
    from google.genai import types
    parts_acc: List[Any] = []
    last = None
    async for chunk in await client.aio.models.generate_content_stream(model=model, contents=contents, config=cfg):
        last = chunk
        cand = (chunk.candidates or [None])[0]
        if not cand or not cand.content or not cand.content.parts:
            continue
        for p in cand.content.parts:
            if p.text and not p.thought:
                await emit({"type": "delta", "text": p.text})
            parts_acc.append(p)
    # merge adjacent text parts
    merged: List[Any] = []
    for p in parts_acc:
        if p.text and not p.thought and merged and merged[-1].text and not merged[-1].thought and not merged[-1].function_call:
            merged[-1] = types.Part(text=merged[-1].text + p.text, thought_signature=merged[-1].thought_signature or p.thought_signature)
        else:
            merged.append(p)
    content = types.Content(role="model", parts=merged or [types.Part.from_text(text="")])
    return _types.SimpleNamespace(candidates=[_types.SimpleNamespace(content=content)], text="".join(p.text or "" for p in merged if p.text and not p.thought))
