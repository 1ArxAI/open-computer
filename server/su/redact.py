"""Keep secret values out of what the model sees. Every tool result passes through redact(): any .env value whose key looks
like a credential is replaced by [KEY_NAME redacted]. A prompt injection can then make the model print a secret, and it still
never reaches the model, the transcript, or an outbound request the model composes."""
import re
from typing import Dict, List

from .config import env, env_file_keys

_SECRET_KEY = re.compile(r"(KEY|TOKEN|SECRET|PASS|PWD|PASSWORD|CREDENTIAL|PRIVATE|DSN)", re.I)
_MIN_LEN = 8  # shorter values (ports, flags, 'true') are not worth hiding and would shred normal text


def secret_values() -> List[tuple]:
    e = env()
    out = []
    for k in env_file_keys():
        if not _SECRET_KEY.search(k):
            continue
        v = (e.get(k) or "").strip()
        if len(v) >= _MIN_LEN and not v.lower().startswith(("http://", "https://")):
            out.append((k, v))
    out.sort(key=lambda kv: -len(kv[1]))  # longest first so a value that contains another is replaced whole
    return out


def redact(text: str) -> str:
    if not text:
        return text
    for k, v in secret_values():
        if v in text:
            text = text.replace(v, f"[{k} redacted]")
    return text
