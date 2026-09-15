"""Image and video generation."""
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

from . import providers as _providers
from .config import TZ, WORKSPACE, env
from .providers import _gemini_client, _get_provider_key, get_custom_providers, split_model
from .security import is_safe_file_path, is_safe_url

# ---------------------------------------------------------------- media generation (Zo's Image / Video model rows)

def media_models() -> Dict[str, Optional[str]]:
    e = env()
    image = e.get("SU_IMAGE_MODEL") or next(iter(available_image_models()), None)  # first model a configured provider offers
    return {"image": image, "video": e.get("SU_VIDEO_MODEL") or None}


def _media_out_path(path: Optional[str], ext: str) -> Path:
    if path:
        out = Path(path)
        out = out if out.is_absolute() else WORKSPACE / out
    else:
        out = WORKSPACE / "Projects" / "media" / f"{datetime.now(TZ).strftime('%Y%m%d-%H%M%S')}.{ext}"
    out.parent.mkdir(parents=True, exist_ok=True)
    return out




async def _gemini_image(model: str, prompt: str, path: Optional[str], reference: Optional[str]) -> str:
    """Native Gemini image generation (google-genai SDK)."""
    from google.genai import types
    client = _gemini_client()
    parts: List[Any] = [types.Part.from_text(text=prompt)]
    if reference:
        ref = Path(reference) if reference.startswith("/") else WORKSPACE / reference
        parts.append(types.Part.from_bytes(data=ref.read_bytes(), mime_type="image/png" if ref.suffix.lower() == ".png" else "image/jpeg"))
    r = await client.aio.models.generate_content(model=model, contents=[types.Content(role="user", parts=parts)],
                                                 config=types.GenerateContentConfig(response_modalities=["IMAGE", "TEXT"]))
    cand = (r.candidates or [None])[0]
    for part in (cand.content.parts if cand and cand.content else []):
        if part.inline_data and part.inline_data.data:
            ext = "jpg" if "jpeg" in (part.inline_data.mime_type or "") else "png"
            out = _media_out_path(path, ext)
            out.write_bytes(part.inline_data.data)
            return f"Image saved to {out} ({out.stat().st_size // 1024} KB). View: /api/file/raw?path={out}"
    return f"The image model google:{model} returned no image. Text: {(r.text or '')[:300]}"


PROVIDER_IMAGE_MODELS = {
    "openai": ["dall-e-3", "dall-e-2"],
    "openrouter": [
        "google/gemini-2.5-flash-image",
        "google/gemini-3.1-flash-image",
        "google/gemini-2.0-flash-exp:free",
        "black-forest-labs/flux-1-schnell",
        "black-forest-labs/flux-1-dev",
        "recraft/recraft-v3",
        "stabilityai/stable-diffusion-3-medium",
        "openai/dall-e-3",
    ],
    "google": [
        "gemini-2.5-flash-image",
        "gemini-3.1-flash-image",
        "imagen-3.0-generate-002",
    ],
    "together": [
        "black-forest-labs/FLUX.1-schnell",
        "stabilityai/stable-diffusion-xl-base-1.0",
    ],
    "fal": [
        "fal-ai/flux/schnell",
        "fal-ai/flux/dev",
        "fal-ai/recraft-v3",
        "fal-ai/fast-sdxl",
    ],
}


def available_image_models() -> List[str]:
    options = []
    e = env()
    custom = get_custom_providers()

    for p in custom:
        if not p.get("enabled", True):
            continue
        pid = p["id"]
        base_url = p.get("base_url", "").lower()

        # 1. Models dynamically discovered from provider /models endpoint
        for m in p.get("models", []):
            is_dict = isinstance(m, dict)
            m_id = m.get("id") if is_dict else m
            if not m_id:
                continue
            m_low = m_id.lower()
            if any(k in m_low for k in ("flux", "dall-e", "imagen", "recraft", "stable-diffusion", "sdxl", "-image", "/image", "midjourney")):
                if "vision" not in m_low or "image" in m_low:
                    entry = f"{pid}:{m_id}"
                    if entry not in options:
                        options.append(entry)

        # 2. Known provider curated image models
        prov_key = None
        if "openai" in pid.lower() or "openai.com" in base_url:
            prov_key = "openai"
        elif "openrouter" in pid.lower() or "openrouter.ai" in base_url:
            prov_key = "openrouter"
        elif "google" in pid.lower() or "googleapis" in base_url:
            prov_key = "google"
        elif "together" in pid.lower() or "together" in base_url:
            prov_key = "together"

        if prov_key and prov_key in PROVIDER_IMAGE_MODELS:
            for m in PROVIDER_IMAGE_MODELS[prov_key]:
                entry = f"{pid}:{m}"
                if entry not in options:
                    options.append(entry)

    # 3. Fal image models if FAL_KEY configured
    if e.get("FAL_KEY"):
        for m in PROVIDER_IMAGE_MODELS["fal"]:
            if m not in options:
                options.append(m)

    # 4. Legacy .env fallback if no custom providers configured
    if not custom:
        if _get_provider_key("openrouter"):
            for m in PROVIDER_IMAGE_MODELS["openrouter"]:
                entry = f"openrouter:{m}"
                if entry not in options:
                    options.append(entry)
        if _get_provider_key("openai"):
            for m in PROVIDER_IMAGE_MODELS["openai"]:
                entry = f"openai:{m}"
                if entry not in options:
                    options.append(entry)
        if _get_provider_key("google"):
            for m in PROVIDER_IMAGE_MODELS["google"]:
                entry = f"google:{m}"
                if entry not in options:
                    options.append(entry)
        if _get_provider_key("together"):
            for m in PROVIDER_IMAGE_MODELS["together"]:
                entry = f"together:{m}"
                if entry not in options:
                    options.append(entry)

    # 5. Default popular models if nothing found
    if not options:
        options = [
            "openai:dall-e-3",
            "openai:dall-e-2",
            "openrouter:google/gemini-2.5-flash-image",
            "openrouter:black-forest-labs/flux-1-schnell",
            "google:gemini-2.5-flash-image",
            "fal-ai/flux/schnell",
        ]

    current = e.get("SU_IMAGE_MODEL")
    if current and current not in options:
        options.insert(0, current)
    return options


def image_providers_online() -> List[str]:
    """Image-capable providers that are configured and online, in preference order."""
    custom = get_custom_providers()
    if custom:
        res = [p["id"] for p in custom if p.get("enabled", True)]
        if env().get("FAL_KEY") and "fal" not in res:
            res.append("fal")
        return res

    ps = _providers.configured_providers()
    e = env()
    allowed = ["openrouter", "google", "openai", "together", "local"]
    res = [p for p in allowed if p in ps]
    if e.get("FAL_KEY") and "fal" not in res:
        res.append("fal")
    return res


async def _save_image_data(img_source: str, path: Optional[str] = None) -> str:
    """Save image from URL (http/https), data URI, or raw base64 string to disk."""
    import base64
    ext = "png"
    img_bytes = None

    img_source = (img_source or "").strip()
    if not img_source:
        return "Failed to save image: empty image data received."

    if img_source.startswith("data:"):
        header, b64_str = img_source.split(",", 1)
        if "jpeg" in header or "jpg" in header:
            ext = "jpg"
        elif "webp" in header:
            ext = "webp"
        try:
            img_bytes = base64.b64decode(b64_str.strip())
        except Exception as e:
            return f"Failed to decode base64 image data: {e}"
    elif img_source.startswith("http://") or img_source.startswith("https://"):
        safe, err = is_safe_url(img_source)
        if not safe:
            return f"Blocked unsafe image download URL '{img_source}': {err}"
        try:
            async with httpx.AsyncClient(timeout=120, follow_redirects=True) as c:
                resp = await c.get(img_source)
                if resp.status_code != 200:
                    return f"Failed to download generated image: HTTP {resp.status_code}"
                img_bytes = resp.content
                ctype = resp.headers.get("content-type", "")
                if "jpeg" in ctype or "jpg" in ctype:
                    ext = "jpg"
                elif "webp" in ctype:
                    ext = "webp"
        except Exception as e:
            return f"Failed to download generated image: {e}"
    else:
        # Assume raw base64 string
        try:
            img_bytes = base64.b64decode(img_source)
        except Exception:
            return "Failed to decode image data."

    if not img_bytes:
        return "Failed to save image: received 0 bytes."

    out = _media_out_path(path, ext)
    out.write_bytes(img_bytes)
    return f"Image saved to {out} ({out.stat().st_size // 1024} KB). View: /api/file/raw?path={out}"


async def _generate_via_images_api(client: AsyncOpenAI, model: str, prompt: str, reference: Optional[str] = None) -> tuple[Optional[str], Optional[str]]:
    """
    Call POST /v1/images/generations (or edits if reference is provided).
    Returns (img_source, error_message).
    """
    base_url = str(client.base_url).rstrip("/")
    api_key = client.api_key or ""
    last_err = None

    # 1. If reference image exists, try client.images.edit first
    if reference:
        ref_path = Path(reference) if reference.startswith("/") else WORKSPACE / reference
        if ref_path.exists():
            try:
                with open(ref_path, "rb") as img_file:
                    res = await client.images.edit(image=img_file, prompt=prompt, model=model, n=1)
                    if res and res.data:
                        item = res.data[0]
                        src = getattr(item, "url", None) or getattr(item, "b64_json", None)
                        if src:
                            return src, None
            except Exception as e:
                last_err = str(e)

    # 2. Try client.images.generate (POST /v1/images/generations)
    for kwargs in [{"size": "1024x1024"}, {}]:
        try:
            res = await client.images.generate(model=model, prompt=prompt, n=1, **kwargs)
            if res and res.data:
                item = res.data[0]
                src = getattr(item, "url", None) or getattr(item, "b64_json", None)
                if not src and isinstance(item, dict):
                    src = item.get("url") or item.get("b64_json")
                if src:
                    return src, None
        except Exception as e:
            last_err = str(e)
            err_lower = str(e).lower()
            if any(k in err_lower for k in ("size", "dimension", "resolution", "parameter")):
                continue
            if "/" in model and ("model" in err_lower or "not found" in err_lower):
                short_m = model.split("/", 1)[-1]
                try:
                    res = await client.images.generate(model=short_m, prompt=prompt, n=1, **kwargs)
                    if res and res.data:
                        item = res.data[0]
                        src = getattr(item, "url", None) or getattr(item, "b64_json", None)
                        if src:
                            return src, None
                except Exception as e2:
                    last_err = str(e2)
            break

    # 3. Direct HTTP POST fallback to /images/generations
    endpoints = [f"{base_url}/images/generations"]
    if not base_url.endswith("/v1"):
        endpoints.append(f"{base_url}/v1/images/generations")
    else:
        endpoints.append(f"{base_url.removesuffix('/v1')}/images/generations")

    headers = {"Content-Type": "application/json"}
    if api_key and api_key != "none":
        headers["Authorization"] = f"Bearer {api_key}"

    candidate_models = [model]
    if "/" in model:
        candidate_models.append(model.split("/", 1)[-1])

    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as http_client:
        for ep in endpoints:
            for cand in candidate_models:
                for body in [{"model": cand, "prompt": prompt, "n": 1, "size": "1024x1024"},
                             {"model": cand, "prompt": prompt, "n": 1},
                             {"prompt": prompt, "n": 1}]:
                    try:
                        resp = await http_client.post(ep, headers=headers, json=body)
                        if resp.status_code == 200:
                            j = resp.json()
                            if isinstance(j, dict):
                                data_list = j.get("data") or j.get("images") or []
                                if data_list and isinstance(data_list, list):
                                    first = data_list[0]
                                    if isinstance(first, dict):
                                        src = first.get("url") or first.get("b64_json") or first.get("image") or first.get("image_url")
                                        if src:
                                            return src, None
                                    elif isinstance(first, str):
                                        return first, None
                                src = j.get("url") or j.get("b64_json") or j.get("image")
                                if src:
                                    return src, None
                            elif isinstance(j, list) and j:
                                first = j[0]
                                src = first.get("url") if isinstance(first, dict) else (first if isinstance(first, str) else None)
                                if src:
                                    return src, None
                        elif resp.status_code not in (404, 405):
                            last_err = f"HTTP {resp.status_code}: {resp.text[:300]}"
                    except Exception as e3:
                        last_err = str(e3)

    return None, last_err


async def generate_image(prompt: str, path: Optional[str] = None, reference: Optional[str] = None) -> str:
    model = media_models()["image"]
    if not model:
        avail = available_image_models()
        model = avail[0] if avail else "openai:dall-e-3"
    provider, m = split_model(model)
    online = image_providers_online()
    if provider not in online and not model.startswith("fal-ai/"):
        if not online:
            return "No image provider is online. Add an AI provider in Settings > Models."
        provider = online[0]
        m = "dall-e-3" if "openai" in provider else ("google/gemini-2.5-flash-image" if "openrouter" in provider else m)

    # 1. Native Google SDK (if configured with google provider)
    if provider == "google":
        try:
            return await _gemini_image(m, prompt, path, reference)
        except Exception as e:
            code = getattr(e, "code", None)
            hint = " Gemini image generation is not part of the free tier; enable billing on the key or turn OpenRouter on." if code == 429 else ""
            return f"Gemini image error: {str(e)[:300]}.{hint}"

    # 2. Fal.ai queue
    if provider in ("fal", "fal-ai") or model.startswith("fal-ai/"):
        fal_key = env().get("FAL_KEY")
        if not fal_key:
            return "FAL_KEY is missing in Settings. Add FAL_KEY to use fal.ai image generation."
        try:
            fal_model = m if not m.startswith("fal-ai/") else m
            async with httpx.AsyncClient(timeout=120) as c:
                r = await c.post(
                    f"https://fal.run/{fal_model}",
                    headers={"Authorization": f"Key {fal_key}", "Content-Type": "application/json"},
                    json={"prompt": prompt, "image_size": "square_hd"}
                )
                j = r.json()
                images = j.get("images", [])
                if images and images[0].get("url"):
                    return await _save_image_data(images[0]["url"], path)
                return f"fal.ai error: {j.get('detail') or str(j)[:300]}"
        except Exception as e:
            return f"fal.ai image error: {str(e)[:300]}"

    # 3. Universal image generation: POST /v1/images/generations
    client = _providers.client_for(provider)
    img_src, gen_err = await _generate_via_images_api(client, m, prompt, reference)
    if img_src:
        return await _save_image_data(img_src, path)

    # 4. Fallback for endpoints that only support chat completions with image modality (e.g. legacy OpenRouter)
    can_fallback_chat = gen_err and any(k in gen_err.lower() for k in ("404", "not found", "405", "unsupported"))
    if can_fallback_chat:
        try:
            content: Any = prompt
            if reference:
                ref = Path(reference) if reference.startswith("/") else WORKSPACE / reference
                import base64
                b64 = base64.b64encode(ref.read_bytes()).decode()
                mime = "image/png" if ref.suffix.lower() == ".png" else "image/jpeg"
                content = [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}]
            r = await client.chat.completions.create(model=m, messages=[{"role": "user", "content": content}], extra_body={"modalities": ["image", "text"]})
            msg = r.choices[0].message.model_dump()
            imgs = msg.get("images") or []
            if imgs:
                u = imgs[0].get("image_url", {}).get("url", "")
                if u:
                    return await _save_image_data(u, path)
        except Exception:
            pass

    return f"Image generation failed: {gen_err or 'No image returned by provider.'}"


async def generate_video(prompt: str, path: Optional[str] = None, image: Optional[str] = None, seconds: int = 5) -> str:
    model = media_models()["video"]
    e = env()
    if not model:
        return ("No video model is configured. Add SU_VIDEO_MODEL in Settings > Keys (a fal.ai model id such as "
                "fal-ai/minimax/hailuo-02/standard/text-to-video for MiniMax Hailuo 6 s clips) together with FAL_KEY. Until then, tell the owner a video model is needed rather than building one by hand.")
    if model.startswith("google:"):
        return await _veo_video(model.split(":", 1)[1], prompt, path, image, seconds)
    if not e.get("FAL_KEY"):
        return "SU_VIDEO_MODEL is set but FAL_KEY is missing in Settings > Keys."
    # fal.ai queue API: submit, poll, download. ponytail: fal only; add other vendors when someone asks.
    allowed = (6, 10) if "hailuo" in model else (5, 10)  # fal.ai: Hailuo takes 6|10 s, Kling/others 5|10 s
    body: Dict[str, Any] = {"prompt": prompt, "duration": str(min(allowed, key=lambda a: abs(a - seconds)))}
    if image:
        safe, err, ref = is_safe_file_path(image, allow_write=False)
        if not safe:
            return f"Blocked image reference for video: {err}"
        import base64
        body["image_url"] = "data:image/png;base64," + base64.b64encode(ref.read_bytes()).decode()
    h = {"Authorization": f"Key {e['FAL_KEY']}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(f"https://queue.fal.run/{model}", headers=h, json=body)
        if r.status_code >= 400:
            return f"fal.ai error {r.status_code}: {r.text[:300]}"
        j = r.json()
        status_url, response_url = j.get("status_url"), j.get("response_url")
        for _ in range(180):
            await asyncio.sleep(5)
            st = (await c.get(status_url, headers=h)).json()
            if st.get("status") == "COMPLETED":
                break
            if st.get("status") in ("FAILED", "ERROR"):
                return f"fal.ai job failed: {str(st)[:300]}"
        else:
            return "fal.ai job did not finish within 15 minutes."
        res = (await c.get(response_url, headers=h)).json()
        vid = (res.get("video") or {}).get("url") or (res.get("videos") or [{}])[0].get("url")
        if not vid:
            return f"fal.ai returned no video url: {str(res)[:300]}"
        data = (await c.get(vid, timeout=300)).content
    out = _media_out_path(path, "mp4")
    out.write_bytes(data)
    return f"Video saved to {out} ({out.stat().st_size // 1024} KB). View: /api/file/raw?path={out}"




async def _veo_video(model: str, prompt: str, path: Optional[str], image: Optional[str], seconds: int) -> str:
    """Veo via the Gemini API: predictLongRunning -> poll operation -> download. Paid feature on Google's side."""
    key = env().get("GEMINI_API_KEY")
    if not key:
        return "GEMINI_API_KEY is missing in Settings > Keys (needed for Veo video)."
    base = "https://generativelanguage.googleapis.com/v1beta"
    h = {"x-goog-api-key": key, "Content-Type": "application/json"}
    inst: Dict[str, Any] = {"prompt": prompt}
    if image:
        ref = Path(image) if image.startswith("/") else WORKSPACE / image
        import base64
        inst["image"] = {"bytesBase64Encoded": base64.b64encode(ref.read_bytes()).decode(), "mimeType": "image/png" if ref.suffix.lower() == ".png" else "image/jpeg"}
    params: Dict[str, Any] = {"aspectRatio": "16:9", "durationSeconds": max(4, min(8, int(seconds)))}
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(f"{base}/models/{model}:predictLongRunning", headers=h, json={"instances": [inst], "parameters": params})
        if r.status_code >= 400:
            return f"Veo error {r.status_code}: {r.text[:400]}"
        op = r.json().get("name")
        for _ in range(120):
            await asyncio.sleep(6)
            st = (await c.get(f"{base}/{op}", headers=h)).json()
            if st.get("done"):
                break
        else:
            return "Veo did not finish within 12 minutes."
        if st.get("error"):
            return f"Veo failed: {str(st['error'])[:400]}"
        resp = st.get("response") or {}
        samples = (resp.get("generateVideoResponse") or resp).get("generatedSamples") or []
        uri = (samples[0].get("video") or {}).get("uri") if samples else None
        if not uri:
            return f"Veo returned no video: {str(resp)[:400]}"
        data = (await c.get(uri, headers={"x-goog-api-key": key}, follow_redirects=True, timeout=600)).content
    out = _media_out_path(path, "mp4")
    out.write_bytes(data)
    return f"Video saved to {out} ({out.stat().st_size // 1024} KB). View: /api/file/raw?path={out}"
