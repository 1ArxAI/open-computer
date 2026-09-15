"""Speech and transcription through any OpenAI-compatible provider's /audio endpoints.
SU_SPEECH_MODEL and SU_TRANSCRIBE_MODEL are provider:model, e.g. openai:whisper-1 or groq:whisper-large-v3. Nothing is assumed."""
import asyncio
import shutil
from pathlib import Path
from typing import Optional

from . import providers as _providers
from .config import WORKSPACE, _ws_path, env
from .providers import split_model


def _model(key: str, what: str):
    m = env().get(key, "").strip()
    if not m:
        raise RuntimeError(f"Set {key} to provider:model in Settings > Keys ({what}).")
    return split_model(m)


async def generate_speech(text: str, out_path: Optional[str] = None, voice: Optional[str] = None) -> str:
    provider, model = _model("SU_SPEECH_MODEL", "an OpenAI-compatible text-to-speech model")
    out = _ws_path(out_path) if out_path else WORKSPACE / "Projects" / "media" / "speech.mp3"
    out.parent.mkdir(parents=True, exist_ok=True)
    client = _providers.client_for(provider)
    r = await client.audio.speech.create(model=model, voice=voice or env().get("SU_SPEECH_VOICE") or "alloy", input=text[:4000])
    out.write_bytes(r.content if hasattr(r, "content") else await r.aread())
    return f"Saved speech to {out.relative_to(WORKSPACE) if out.is_relative_to(WORKSPACE) else out}"


async def transcribe(path: str, language: Optional[str] = None) -> str:
    provider, model = _model("SU_TRANSCRIBE_MODEL", "an OpenAI-compatible speech-to-text model")
    src = _ws_path(path)
    if not src.exists():
        return f"File not found: {path}"
    audio = src
    if src.suffix.lower() in (".mp4", ".mov", ".mkv", ".webm", ".avi"):
        if not shutil.which("ffmpeg"):
            return "Video transcription needs ffmpeg on the box."
        audio = src.with_suffix(".transcribe.wav")
        proc = await asyncio.create_subprocess_exec("ffmpeg", "-y", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", str(audio),
                                                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await proc.wait()
    client = _providers.client_for(provider)
    with open(audio, "rb") as f:
        kw = {"model": model, "file": f}
        if language and language != "auto":
            kw["language"] = language
        r = await client.audio.transcriptions.create(**kw)
    if audio is not src:
        audio.unlink(missing_ok=True)
    text = getattr(r, "text", None) or str(r)
    out = src.with_suffix(src.suffix + ".txt")
    out.write_text(text, encoding="utf-8")
    return f"Transcript saved to {out.name}:\n{text[:8000]}"
