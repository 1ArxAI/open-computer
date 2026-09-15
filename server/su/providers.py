"""Provider registry, custom providers, model discovery, default model, CLI availability, google-genai client."""
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

from .config import HOME, KEY_ALIASES, PROVIDERS_FILE, _read, _write, env

# ---------------------------------------------------------------- providers

PROVIDERS = {
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "key": "OPENROUTER_API_KEY", "label": "OpenRouter"},
    "openai": {"base_url": "https://api.openai.com/v1", "key": "OPENAI_API_KEY", "label": "OpenAI"},
    "google": {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai/", "key": "GEMINI_API_KEY", "label": "Gemini API"},
    "anthropic": {"base_url": "https://api.anthropic.com/v1", "key": "ANTHROPIC_API_KEY", "label": "Anthropic"},
    "groq": {"base_url": "https://api.groq.com/openai/v1", "key": "GROQ_API_KEY", "label": "Groq"},
    "deepseek": {"base_url": "https://api.deepseek.com", "key": "DEEPSEEK_API_KEY", "label": "DeepSeek"},
    "mistral": {"base_url": "https://api.mistral.ai/v1", "key": "MISTRAL_API_KEY", "label": "Mistral AI"},
    "together": {"base_url": "https://api.together.xyz/v1", "key": "TOGETHER_API_KEY", "label": "Together AI"},
    "xai": {"base_url": "https://api.x.ai/v1", "key": "XAI_API_KEY", "label": "xAI (Grok)"},
    "perplexity": {"base_url": "https://api.perplexity.ai", "key": "PERPLEXITY_API_KEY", "label": "Perplexity"},
    "cerebras": {"base_url": "https://api.cerebras.ai/v1", "key": "CEREBRAS_API_KEY", "label": "Cerebras"},
    "cohere": {"base_url": "https://api.cohere.com/v2", "key": "COHERE_API_KEY", "label": "Cohere"},
    "sambanova": {"base_url": "https://api.sambanova.ai/v1", "key": "SAMBANOVA_API_KEY", "label": "SambaNova"},
    "fireworks": {"base_url": "https://api.fireworks.ai/inference/v1", "key": "FIREWORKS_API_KEY", "label": "Fireworks AI"},
    "deepinfra": {"base_url": "https://api.deepinfra.com/v1/openai", "key": "DEEPINFRA_API_KEY", "label": "DeepInfra"},
    "siliconflow": {"base_url": "https://api.siliconflow.cn/v1", "key": "SILICONFLOW_API_KEY", "label": "SiliconFlow"},
    "moonshot": {"base_url": "https://api.moonshot.cn/v1", "key": "MOONSHOT_API_KEY", "label": "Moonshot AI"},
    "ai21": {"base_url": "https://api.ai21.com/studio/v1", "key": "AI21_API_KEY", "label": "AI21 Labs"},
    "novita": {"base_url": "https://api.novita.ai/v3/openai", "key": "NOVITA_API_KEY", "label": "Novita AI"},
    "hyperbolic": {"base_url": "https://api.hyperbolic.xyz/v1", "key": "HYPERBOLIC_API_KEY", "label": "Hyperbolic"},
    "lepton": {"base_url": "https://api.lepton.ai/v1", "key": "LEPTON_API_KEY", "label": "Lepton AI"},
    "minimax": {"base_url": "https://api.minimax.chat/v1", "key": "MINIMAX_API_KEY", "label": "MiniMax"},
    "nvidia": {"base_url": "https://integrate.api.nvidia.com/v1", "key": "NVIDIA_API_KEY", "label": "NVIDIA Build"},
    # local: any OpenAI-compatible server (llama-server, Ollama, vLLM); LOCAL_LLM_URL holds its base URL
    "local": {"base_url": "http://127.0.0.1:11434/v1", "key": "LOCAL_LLM_URL", "label": "Local (OpenAI-compatible)", "local": True},
}

# Curated fallback models if live API query fails or is unreachable
SEED_MODELS = {
    "openrouter": [
        "anthropic/claude-3.7-sonnet", "anthropic/claude-3.5-sonnet", "anthropic/claude-3.5-haiku",
        "openai/gpt-4o", "openai/gpt-4o-mini", "openai/o3-mini",
        "google/gemini-2.5-flash", "google/gemini-2.0-flash-exp:free",
        "deepseek/deepseek-chat", "deepseek/deepseek-r1",
        "meta-llama/llama-3.3-70b-instruct", "qwen/qwen-2.5-72b-instruct", "mistralai/mistral-large-2411",
        "minimax/minimax-m3:free", "nvidia/nemotron-3.5-lightning:free"
    ],
    "openai": ["gpt-4o", "gpt-4o-mini", "o3-mini", "o1", "gpt-4-turbo"],
    "google": ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.0-flash-lite", "gemini-1.5-pro", "gemini-1.5-flash"],
    "anthropic": ["claude-3-7-sonnet-20250219", "claude-3-5-sonnet-20241022", "claude-3-5-haiku-20241022", "claude-3-opus-20240229"],
    "groq": ["llama-3.3-70b-versatile", "llama-3.1-8b-instant", "deepseek-r1-distill-llama-70b", "mixtral-8x7b-32768"],
    "deepseek": ["deepseek-chat", "deepseek-reasoner"],
    "mistral": ["mistral-large-latest", "mistral-small-latest", "codestral-latest"],
    "together": ["meta-llama/Llama-3.3-70B-Instruct-Turbo", "deepseek-ai/DeepSeek-R1", "Qwen/Qwen2.5-72B-Instruct-Turbo"],
    "xai": ["grok-2-latest", "grok-2-vision-latest", "grok-beta"],
    "perplexity": ["sonar-pro", "sonar", "sonar-reasoning"],
    "cerebras": ["llama3.3-70b", "llama3.1-8b"],
    "cohere": ["command-r-plus", "command-r"],
    "sambanova": ["Meta-Llama-3.3-70B-Instruct", "DeepSeek-R1-Distill-Llama-70B"],
    "fireworks": ["accounts/fireworks/models/llama-v3p3-70b-instruct", "accounts/fireworks/models/deepseek-r1"],
    "deepinfra": ["meta-llama/Llama-3.3-70B-Instruct", "deepseek-ai/DeepSeek-R1"],
    "siliconflow": ["deepseek-ai/DeepSeek-V3", "deepseek-ai/DeepSeek-R1"],
    "moonshot": ["moonshot-v1-8k", "moonshot-v1-32k", "kimi-k2.5"],
    "ai21": ["jamba-1.5-large", "jamba-1.5-mini"],
    "novita": ["meta-llama/llama-3.3-70b-instruct", "deepseek/deepseek-r1"],
    "hyperbolic": ["meta-llama/Llama-3.3-70B-Instruct", "deepseek-ai/DeepSeek-R1"],
    "lepton": ["llama3-3-70b", "deepseek-r1"],
    "minimax": ["MiniMax-Text-01"],
    "nvidia": ["nvidia/nemotron-3.5-lightning-30b-a3b", "nvidia/nemotron-3-super-120b-a12b", "deepseek-ai/deepseek-v4-pro-0813"],
    "local": [],
}
_models_cache: Dict[str, Any] = {}


def get_custom_providers() -> List[Dict[str, Any]]:
    """Return all user-configured providers from providers.json (with automatic migration from legacy .env keys)."""
    providers = _read(PROVIDERS_FILE, None)
    if providers is None:
        e = env()
        migrated = []
        templates = [
            ("openai", "OpenAI", "https://api.openai.com/v1", "OPENAI_API_KEY"),
            ("openrouter", "OpenRouter", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
            ("deepseek", "DeepSeek", "https://api.deepseek.com/v1", "DEEPSEEK_API_KEY"),
            ("groq", "Groq", "https://api.groq.com/openai/v1", "GROQ_API_KEY"),
            ("google", "Gemini", "https://generativelanguage.googleapis.com/v1beta/openai/", "GEMINI_API_KEY"),
            ("anthropic", "Anthropic", "https://api.anthropic.com/v1", "ANTHROPIC_API_KEY"),
            ("local", "Local Ollama", "http://127.0.0.1:11434/v1", "LOCAL_LLM_URL"),
        ]
        for pid, name, url, key_var in templates:
            val = e.get(key_var) or e.get(key_var.lower())
            if val:
                migrated.append({
                    "id": pid,
                    "name": name,
                    "base_url": val if key_var == "LOCAL_LLM_URL" else url,
                    "api_key": "" if key_var == "LOCAL_LLM_URL" else val,
                    "enabled": True,
                    "models": [],
                    "created_at": time.time()
                })
        save_custom_providers(migrated)
        return migrated
    return providers


def save_custom_providers(providers: List[Dict[str, Any]]):
    _write(PROVIDERS_FILE, providers)


async def test_provider_connection(base_url: str, api_key: str = "") -> Dict[str, Any]:
    """Test connection to an OpenAI-compatible endpoint and fetch available models."""
    url = (base_url or "").strip().rstrip("/")
    if not url:
        return {"ok": False, "models": [], "error": "URL cannot be empty."}

    headers = {"Accept": "application/json"}
    if api_key:
        if "anthropic.com" in url:
            headers["x-api-key"] = api_key
            headers["anthropic-version"] = "2023-06-01"
        else:
            headers["Authorization"] = f"Bearer {api_key}"

    endpoints = [f"{url}/models"]
    if not url.endswith("/v1"):
        endpoints.append(f"{url}/v1/models")
    if "11434" in url or "ollama" in url.lower():
        endpoints.append(f"{url.removesuffix('/v1')}/api/tags")

    last_error = "Could not connect to provider."
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            for ep in endpoints:
                try:
                    resp = await client.get(ep, headers=headers)
                    if resp.status_code == 200:
                        data = resp.json()
                        raw_models = []
                        if isinstance(data, dict):
                            src_list = data.get("data") if isinstance(data.get("data"), list) else (data.get("models") if isinstance(data.get("models"), list) else [])
                        elif isinstance(data, list):
                            src_list = data
                        else:
                            src_list = []
                            
                        raw_models = []
                        for m in src_list:
                            if isinstance(m, str):
                                mid = m
                                cap = {}
                                im = ["text"]
                            elif isinstance(m, dict):
                                mid = m.get("id") or m.get("name")
                                if not mid: continue
                                cap = m.get("capabilities") or {}
                                arch = m.get("architecture") or {}
                                im = arch.get("input_modalities") or ["text"]
                            else:
                                continue
                            if not mid: continue
                            
                            mid_lower = mid.lower()
                            is_reasoning = bool(cap.get("reasoning") or cap.get("thinking") or any(k in mid_lower for k in ("reasoning", "thinking", "r1", "o1", "o3", "deepseek-r1")))
                            is_tools = bool(cap.get("tool_calling") or "tool" in mid_lower or any(k in mid_lower for k in ("claude", "gpt-4", "gpt-5", "deepseek", "qwen", "gemini")))
                            is_vision = bool("image" in im or any(k in mid_lower for k in ("vision", "vl", "image", "multimodal", "gpt-4o", "flash", "gemini")))
                            
                            raw_models.append({
                                "id": mid,
                                "im": im,
                                "reasoning": is_reasoning,
                                "tools": is_tools,
                                "vision": is_vision
                            })


                        if raw_models:
                            exclude = ("embed", "whisper", "tts", "moderation", "babbage", "davinci", "curie", "ada", "realtime")
                            filtered = [m for m in raw_models if m and not any(bad in m["id"].lower() for bad in exclude)]
                            return {"ok": True, "models": filtered if filtered else raw_models, "error": None}
                    elif resp.status_code in (401, 403):
                        return {"ok": False, "models": [], "error": f"Authentication failed (HTTP {resp.status_code}). Check your API key."}
                    else:
                        last_error = f"HTTP {resp.status_code}: {resp.text[:120]}"
                except Exception as e:
                    last_error = str(e)
    except Exception as e:
        last_error = str(e)

    return {"ok": False, "models": [], "error": last_error}


async def add_custom_provider(name: str, base_url: str, api_key: str = "", pid: Optional[str] = None) -> Dict[str, Any]:
    providers = get_custom_providers()
    name = (name or "").strip()
    base_url = (base_url or "").strip().rstrip("/")
    api_key = (api_key or "").strip()

    if not pid:
        base_slug = re.sub(r'[^a-zA-Z0-9_-]', '_', name.lower()).strip('_') or "provider"
        pid = base_slug
        count = 1
        existing_ids = {p["id"] for p in providers}
        while pid in existing_ids:
            count += 1
            pid = f"{base_slug}_{count}"

    test_res = await test_provider_connection(base_url, api_key)
    models = test_res["models"] if test_res["ok"] else []

    if not models:
        for known_key, seed_list in SEED_MODELS.items():
            if known_key in pid.lower() or known_key in base_url.lower():
                models = list(seed_list)
                break

    existing_idx = next((i for i, p in enumerate(providers) if p["id"] == pid), None)
    new_prov = {
        "id": pid,
        "name": name,
        "base_url": base_url,
        "api_key": api_key,
        "enabled": True,
        "models": models,
        "created_at": time.time()
    }

    if existing_idx is not None:
        if not api_key and providers[existing_idx].get("api_key"):
            new_prov["api_key"] = providers[existing_idx]["api_key"]
        providers[existing_idx] = new_prov
    else:
        providers.append(new_prov)

    save_custom_providers(providers)
    _models_cache.clear()
    return new_prov


def delete_custom_provider(pid: str) -> bool:
    providers = get_custom_providers()
    new_list = [p for p in providers if p["id"] != pid]
    if len(new_list) != len(providers):
        save_custom_providers(new_list)
        _models_cache.clear()
        return True
    return False


def disabled_providers() -> set:
    return {x.strip() for x in env().get("SU_DISABLED_PROVIDERS", "").split(",") if x.strip()}


def image_only_providers() -> set:
    return {x.strip() for x in env().get("SU_IMAGE_ONLY_PROVIDERS", "").split(",") if x.strip()}


def _get_provider_key(provider: str) -> Optional[str]:
    custom = get_custom_providers()
    for p in custom:
        if p["id"] == provider:
            return p.get("api_key") or "configured"
    cfg = PROVIDERS.get(provider)
    if not cfg:
        return None
    e = env()
    key_name = cfg["key"]
    val = e.get(key_name) or e.get(key_name.lower())
    if val:
        return val
    for alias in KEY_ALIASES.get(key_name, []):
        val = e.get(alias) or e.get(alias.lower())
        if val:
            return val
    return None


def configured_providers() -> List[str]:
    custom = get_custom_providers()
    if custom:
        return [p["id"] for p in custom if p.get("enabled", True)]
    off = disabled_providers()
    res = []
    for p in PROVIDERS:
        if p in off:
            continue
        if _get_provider_key(p):
            res.append(p)
    return res


def chat_providers() -> List[str]:
    return [p for p in configured_providers() if p not in image_only_providers()]


_EFFORT_TIER = re.compile(r"-(low|medium|high|xhigh|max|ultra|none|tiered|batch)$|:batch$")

def _model_company(mid: str) -> str:
    m = mid.lower()
    if "claude" in m or "anthropic" in m: return "Anthropic"
    if "openai" in m or "gpt" in m or "o1" in m or "o3" in m or "o4" in m: return "OpenAI"
    if "gemini" in m or "google" in m or "antigravity" in m or "gemma" in m: return "Google"
    if "deepseek" in m or "ds/" in m: return "DeepSeek"
    if "llama" in m or "meta" in m: return "Meta"
    if "qwen" in m: return "Qwen"
    if "mistral" in m or "codestral" in m: return "Mistral"
    if "x-ai" in m or "grok" in m: return "xAI"
    if "minimax" in m: return "MiniMax"
    if "moonshot" in m or "kimi" in m: return "Moonshot"
    if "z-ai" in m or "glm" in m or "zai" in m: return "Zhipu AI"
    if "amazon" in m or "nova" in m: return "Amazon"
    if "auto/" in m: return "Auto"
    if "/" in mid: return mid.split("/", 1)[0].capitalize()
    return "Other"

def filter_top_per_company(models: list, limit_per_company: int = 3) -> list:
    seen: Dict[str, int] = {}
    out = []
    # 1st pass: non-effort tier
    for m in models:
        mid = m.get("id") if isinstance(m, dict) else m
        if not mid or _EFFORT_TIER.search(mid): continue
        comp = _model_company(mid)
        if seen.get(comp, 0) < limit_per_company:
            seen[comp] = seen.get(comp, 0) + 1
            out.append(m)
    # 2nd pass: any remaining
    for m in models:
        mid = m.get("id") if isinstance(m, dict) else m
        if not mid or m in out: continue
        comp = _model_company(mid)
        if seen.get(comp, 0) < limit_per_company:
            seen[comp] = seen.get(comp, 0) + 1
            out.append(m)
    return out


def provider_status() -> List[Dict[str, Any]]:
    """Return the clean list of user-configured AI providers."""
    custom = get_custom_providers()
    rows = []
    for p in custom:
        key = p.get("api_key", "")
        masked = f"{key[:3]}...{key[-4:]}" if len(key) > 7 else ("••••" if key else "None (local)")

        p_filters = p.get("filters") or {}
        live = p.get("models") or []
        filtered_live = []
        for m in live:
            is_dict = isinstance(m, dict)
            m_id = m.get("id") if is_dict else m
            if not m_id: continue
            if is_dict:
                reasoning = m.get("reasoning", False)
                tools = m.get("tools", False)
                vision = m.get("vision", False)
            else:
                reasoning = "reasoning" in m_id.lower() or "thinking" in m_id.lower()
                tools = True
                vision = "vision" in m_id.lower() or "vl" in m_id.lower()
                
            if p_filters.get("reasoning") and not reasoning: continue
            if p_filters.get("tools") and not tools: continue
            if p_filters.get("vision") and not vision: continue
            
            # extract string name for frontend compatibility
            filtered_live.append(m_id)
            
        filtered_live = filter_top_per_company(filtered_live, 3)
            
        rows.append({
            "id": p["id"],
            "name": p.get("name", p["id"]),
            "label": p.get("name", p["id"]),
            "base_url": p["base_url"],
            "configured": bool(key or "127.0.0.1" in p["base_url"] or "localhost" in p["base_url"]),
            "has_key": bool(key),
            "masked_key": masked,
            "enabled": p.get("enabled", True),
            "models": filtered_live,
            "model_count": len(filtered_live),
            "filters": p_filters,
            "kind": "api"
        })
    return rows


def set_provider_enabled(pid: str, enabled: bool) -> set:
    custom = get_custom_providers()
    for p in custom:
        if p["id"] == pid:
            p["enabled"] = enabled
            save_custom_providers(custom)
            break
    off = disabled_providers()
    (off.discard if enabled else off.add)(pid)
    _models_cache.clear()
    return off


def runtimes() -> List[str]:
    return configured_providers() + (["claude-code"] if CLAUDE_BIN else []) + (["gemini-cli"] if gemini_cli_available() else [])


def split_model(model: str):
    if not model:
        ps = configured_providers()
        return (ps[0] if ps else "openai"), ""
    custom = get_custom_providers()
    custom_map = {}
    for x in custom:
        custom_map[x["id"]] = x["id"]
        custom_map[x["id"].lower()] = x["id"]
        if x.get("name"):
            custom_map[x["name"].lower()] = x["id"]

    if ":" in model:
        p, m = model.split(":", 1)
        if p in custom_map:
            return custom_map[p], m
        if p.lower() in custom_map:
            return custom_map[p.lower()], m
        if p in PROVIDERS:
            return p, m
    elif "/" in model:
        prefix, rest = model.split("/", 1)
        if prefix in custom_map:
            return custom_map[prefix], model
        if prefix.lower() in custom_map:
            return custom_map[prefix.lower()], model

    ps = configured_providers()
    return (ps[0] if ps else "openai"), model


def client_for(provider: str) -> AsyncOpenAI:
    custom = get_custom_providers()
    p_match = next((p for p in custom if p["id"] == provider or p["id"].lower() == provider.lower() or p.get("name", "").lower() == provider.lower()), None)
    if p_match:
        if not p_match.get("enabled", True):
            raise RuntimeError(f"{p_match.get('name', provider)} is switched offline in Settings > Models.")
        base_url = p_match["base_url"].rstrip("/")
        api_key = p_match.get("api_key") or "none"
        timeout = 600 if ("127.0.0.1" in base_url or "localhost" in base_url) else 180
        return AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=timeout)

    if provider in disabled_providers():
        raise RuntimeError(f"{provider} is switched offline in Settings > Models.")
    cfg = PROVIDERS.get(provider)
    e = env()
    if not cfg:
        base_url = e.get(f"{provider.upper()}_BASE_URL") or e.get(f"{provider.upper()}_LLM_URL")
        key = e.get(f"{provider.upper()}_API_KEY") or e.get(f"{provider.upper()}_KEY") or "custom"
        if not base_url:
            raise RuntimeError(f"Unknown provider '{provider}'.")
        return AsyncOpenAI(base_url=base_url.rstrip("/"), api_key=key, timeout=180)
    key = _get_provider_key(provider)
    if not key:
        raise RuntimeError(f"{cfg['label']} is not configured. Add it in Settings > Models.")
    if cfg.get("local"):
        return AsyncOpenAI(base_url=key.rstrip("/"), api_key="local", timeout=600)
    base_url = cfg["base_url"]
    override_url = e.get(f"{provider.upper()}_BASE_URL") or e.get(f"{cfg['key'].replace('_API_KEY', '')}_BASE_URL")
    if override_url:
        base_url = override_url.rstrip("/")
    return AsyncOpenAI(base_url=base_url, api_key=key, timeout=180)


CAP_LETTER = {"text": "T", "image": "I", "video": "V", "audio": "A", "file": "F"}
_family_mods: Dict[str, List[str]] = {}  # "minimax-m3" -> ["text","image","video"], learned from OpenRouter metadata


def _family(model_id: str) -> str:
    return model_id.split("/")[-1].split(":")[0].lower().replace("-instruct", "").replace("-0731", "").replace("-0813", "")


def _caps(mods: List[str]) -> str:
    order = ["text", "image", "video", "audio", "file"]
    return "".join(CAP_LETTER[m] for m in order if m in mods)


async def _local_models(base_url: str) -> Dict[str, List[str]]:
    """name -> input modalities from llama-server: /v1/models lists them, /props?model= reports vision/audio."""
    root = base_url.rstrip("/").removesuffix("/v1")
    out: Dict[str, List[str]] = {}
    async with httpx.AsyncClient(timeout=10) as c:
        for m in (await c.get(root + "/v1/models")).json().get("data", []):
            mods = {}
            try:
                mods = (await c.get(root + "/props", params={"model": m["id"], "autoload": "false"})).json().get("modalities") or {}
            except Exception:
                pass
            out[m["id"]] = ["text"] + (["image"] if mods.get("vision") else []) + (["audio"] if mods.get("audio") else [])
    return out


async def available_models() -> List[Dict[str, Any]]:
    out = []
    seen_ids = set()
    custom = get_custom_providers()
    active_custom = [p for p in custom if p.get("enabled", True)] if custom else []
    custom_pids = {p["id"] for p in active_custom}

    for p in active_custom:
        pid = p["id"]
        vendor = p.get("name") or pid
        base_url = p.get("base_url", "")
        api_key = p.get("api_key", "")
        
        cached = _models_cache.get(pid)
        if cached and time.time() - cached.get("t", 0) < 300:
            live = cached.get("live", [])
            mods = cached.get("mods", {})
        else:
            stored = p.get("models") or []
            mods = {}
            if not stored:
                test_res = await test_provider_connection(base_url, api_key)
                if test_res["ok"] and test_res["models"]:
                    stored = test_res["models"]
                    p["models"] = stored
                    save_custom_providers(custom)
            
            # Apply user-selected UI filters for this custom provider
            p_filters = p.get("filters") or {}
            live = []
            for m in stored:
                is_dict = isinstance(m, dict)
                m_id = m.get("id") if is_dict else m
                if not m_id: continue
                
                if is_dict:
                    reasoning = m.get("reasoning", False)
                    tools = m.get("tools", False)
                    vision = m.get("vision", False)
                else:
                    mid_l = m_id.lower()
                    reasoning = any(k in mid_l for k in ("reasoning", "thinking", "r1", "o1", "o3", "deepseek-r1"))
                    tools = True
                    vision = any(k in mid_l for k in ("vision", "vl", "image", "multimodal", "gpt-4o", "flash", "gemini"))
                    
                if p_filters.get("reasoning") and not reasoning: continue
                if p_filters.get("tools") and not tools: continue
                if p_filters.get("vision") and not vision: continue
                live.append(m)
                
            # Filter to top 3 models per company (latest / top of each company)
            live = filter_top_per_company(live, 3)
            
            # Ensure default model is retained if set
            current_def = env().get("SU_DEFAULT_MODEL")
            if current_def and current_def.startswith(f"{pid}:"):
                def_mid = current_def.split(":", 1)[1]
                if not any((m.get("id") if isinstance(m, dict) else m) == def_mid for m in live):
                    for st_m in stored:
                        if (st_m.get("id") if isinstance(st_m, dict) else st_m) == def_mid:
                            live.insert(0, st_m)
                            break
                            
            _models_cache[pid] = {"t": time.time(), "live": live, "mods": mods}

        for m_obj in live:
            # Handle both string (legacy) and dict (new)
            is_dict = isinstance(m_obj, dict)
            m_id = m_obj.get("id") if is_dict else m_obj
            if not m_id: continue
            
            key = f"{pid}:{m_id}"
            if key in seen_ids:
                continue
            seen_ids.add(key)
            free = m_id.endswith(":free") or "127.0.0.1" in base_url or "localhost" in base_url
            
            if is_dict:
                im = m_obj.get("im") or ["text"]
                reasoning = m_obj.get("reasoning", False)
                tools = m_obj.get("tools", False)
                vision = m_obj.get("vision", False)
                if vision and "image" not in im: im.append("image")
            else:
                im = mods.get(m_id) or _family_mods.get(_family(m_id)) or ["text", "image"]
                reasoning = "reasoning" in m_id.lower() or "thinking" in m_id.lower()
                tools = True
                vision = "vision" in m_id.lower() or "vl" in m_id.lower()
                
            out.append({
                "model_name": key,
                "label": f"{m_id.split('/', 1)[-1]}{' (free)' if free else ''} · {vendor}",
                "vendor": vendor,
                "free": free,
                "input": im,
                "caps": _caps(im),
                "reasoning": reasoning,
                "tools": tools,
                "vision": vision
            })

    providers = [p for p in chat_providers() if p not in custom_pids]
    for p in providers:
        cached = _models_cache.get(p)
        if cached and time.time() - cached.get("t", 0) < 300:
            continue
        mods = {}
        live = []
        try:
            if p == "anthropic":
                key = _get_provider_key(p)
                async with httpx.AsyncClient(timeout=10) as c:
                    r = await c.get("https://api.anthropic.com/v1/models", headers={"x-api-key": key, "anthropic-version": "2023-06-01"})
                    if r.status_code == 200:
                        data = r.json().get("data", [])
                        live = [m["id"] for m in data if "claude" in m.get("id", "")]
                if not live:
                    live = SEED_MODELS.get("anthropic", [])
            elif p == "openrouter":
                key = _get_provider_key(p)
                async with httpx.AsyncClient(timeout=15) as c:
                    headers = {"Authorization": f"Bearer {key}"} if key else {}
                    r = await c.get("https://openrouter.ai/api/v1/models", headers=headers)
                    if r.status_code == 200:
                        data = r.json().get("data", [])
                        img_mods = []
                        for m in data:
                            mid = m.get("id", "")
                            arch = m.get("architecture") or {}
                            im = arch.get("input_modalities", ["text"])
                            om = arch.get("output_modalities", [])
                            if im:
                                mods[mid] = im
                                _family_mods.setdefault(_family(mid), im)
                            if "image" in om:
                                img_mods.append(f"openrouter:{mid}")
                        if img_mods:
                            _models_cache["openrouter_images"] = img_mods
                        top_prefixes = ("anthropic/", "openai/", "google/", "deepseek/", "meta-llama/", "qwen/", "mistralai/", "x-ai/", "minimax/")
                        live = [m["id"] for m in data if m.get("id") and (m["id"].startswith(top_prefixes) or m["id"].endswith(":free"))]
                        if not live:
                            live = [m["id"] for m in data[:80]]
            elif p == "google" and _gemini_native_ok():
                try:
                    live = list(await gemini_models())
                except Exception:
                    live = []
                if not live:
                    try:
                        res = await client_for(p).models.list()
                        live = [m.id.replace("models/", "") for m in res.data if "gemini" in m.id and "embedding" not in m.id]
                    except Exception:
                        live = SEED_MODELS.get("google", [])
            elif (PROVIDERS.get(p) or {}).get("local"):
                base_url = env().get(PROVIDERS[p].get("key", "")) or PROVIDERS[p].get("base_url", "")
                local_info = await _local_models(base_url)
                live = list(local_info.keys())
                mods = dict(local_info)
            else:
                client = client_for(p)
                res = await client.models.list()
                raw_ids = [m.id.replace("models/", "") for m in res.data]
                exclude = ("embed", "whisper", "tts", "moderation", "babbage", "davinci", "curie", "ada", "realtime")
                live = [mid for mid in raw_ids if not any(bad in mid.lower() for bad in exclude)]
                if not live and raw_ids:
                    live = raw_ids[:40]
        except Exception as e:
            print(f"Error listing models for {p}: {e}")
            live = None
        _models_cache[p] = {"t": time.time(), "live": live, "mods": mods}

    for p in providers:
        curated = SEED_MODELS.get(p, [])
        cached = _models_cache.get(p) or {}
        live, mods = cached.get("live"), cached.get("mods", {})
        ids = list(live) if live else list(curated)
        current_def = env().get("SU_DEFAULT_MODEL")
        if current_def and current_def.startswith(f"{p}:"):
            m_id = current_def.split(":", 1)[1]
            if m_id not in ids:
                ids.insert(0, m_id)
        cfg = PROVIDERS.get(p, {})
        vendor = cfg.get("label", p.capitalize())
        for i in ids:
            key = f"{p}:{i}"
            if key in seen_ids:
                continue
            seen_ids.add(key)
            free = i.endswith(":free") or p == "nvidia" or (p == "google" and "flash" in i) or bool(cfg.get("local"))
            im = mods.get(i) or _family_mods.get(_family(i)) or (["text", "image", "video", "audio", "file"] if p == "google" else ["text"])
            
            reasoning = "reasoning" in i.lower() or "thinking" in i.lower() or "o1" in i.lower() or "o3" in i.lower() or "r1" in i.lower()
            tools = True
            vision = "image" in im or "vision" in i.lower() or "vl" in i.lower()

            out.append({
                "model_name": key,
                "label": f"{i.split('/', 1)[-1]}{' (free)' if free else ''} · {vendor}",
                "vendor": vendor,
                "free": free,
                "input": im,
                "caps": _caps(im),
                "reasoning": reasoning,
                "tools": tools,
                "vision": vision
            })

    if gemini_cli_available():
        out = [{"model_name": f"gemini-cli:{m}", "label": f"{m} · Gemini CLI", "vendor": "Gemini CLI",
                "free": "flash" in m, "input": ["text", "image", "video", "audio", "file"], "caps": "TIVAF"} for m in GEMINI_CLI_MODELS] + out
    if cc_available():
        out = [{"model_name": f"claude-code:{m}", "label": f"Claude {m.capitalize()} · Claude Code (subscription)", "vendor": "Claude Code",
                "free": False, "input": ["text", "image", "file"], "caps": "TIF"} for m in CC_MODELS] + out
    return out


def default_model() -> str:
    e = env()
    if e.get("SU_DEFAULT_MODEL"):
        return e["SU_DEFAULT_MODEL"]
    custom = get_custom_providers()
    for p in custom:
        if p.get("enabled", True) and p.get("models"):
            first_m = p["models"][0]
            first_id = first_m.get("id") if isinstance(first_m, dict) else first_m
            return f"{p['id']}:{first_id}"
    if cc_available():
        return "claude-code:sonnet"
    ps = chat_providers()
    if not ps:
        return ""
    p = ps[0]
    live = (_models_cache.get(p) or {}).get("live")
    if live:
        return f"{p}:{live[0]}"
    for seed in SEED_MODELS.get(p, []):
        return f"{p}:{seed}"
    return f"{p}:default"


def set_default_model(model: str):
    p = HOME / ".env"
    lines = [l for l in (p.read_text(encoding="utf-8").splitlines() if p.exists() else []) if not l.startswith("SU_DEFAULT_MODEL=")]
    if model:
        lines.append(f"SU_DEFAULT_MODEL={model}")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")

# ---------------------------------------------------------------- Claude Code runtime (owner's subscription, like Zo's ACP agents)

CLAUDE_BIN = next((b for b in (str(Path.home() / ".local/bin/claude"), shutil.which("claude") or "") if b and Path(b).exists()), None)
CC_MODELS = ["sonnet", "opus", "fable", "haiku"]


def cc_available() -> bool:
    return bool(CLAUDE_BIN) and "claude-code" not in disabled_providers()


# ---------------------------------------------------------------- Gemini CLI runtime (GEMINI_API_KEY; free Gemini Flash quota)

GEMINI_BIN = next((b for b in (str(Path.home() / ".local/bin/gemini"), shutil.which("gemini") or "") if b and Path(b).exists()), None)
GEMINI_CLI_MODELS = ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.1-pro-preview"]


def gemini_cli_logged_in() -> bool:
    """True only when the CLI holds a Google OAuth token (oauth_creds.json); gemini-credentials.json is just its encrypted API-key store."""
    return (HOME.parent / ".gemini" / "oauth_creds.json").exists()


def gemini_cli_available() -> bool:
    return bool(GEMINI_BIN and (env().get("GEMINI_API_KEY") or gemini_cli_logged_in())) and "gemini-cli" not in disabled_providers()

# ---------------------------------------------------------------- Gemini native (google-genai SDK): first-class provider, not the OpenAI shim

def _gemini_client():
    from google import genai
    key = env().get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("Gemini API is not configured. Add GEMINI_API_KEY in Settings > Keys.")
    if "google" in disabled_providers():
        raise RuntimeError("Gemini API is switched offline in Settings > Models.")
    return genai.Client(api_key=key)


def _gemini_native_ok() -> bool:
    try:
        import google.genai  # noqa: F401
        return env().get("SU_GEMINI_NATIVE", "1") != "0"
    except Exception:
        return False


def _clean_schema(sc: Dict) -> Dict:
    if not isinstance(sc, dict):
        return sc
    out = {}
    for k, v in sc.items():
        if k in ("additionalProperties", "$schema", "default"):
            continue
        out[k] = _clean_schema(v) if isinstance(v, dict) else ([_clean_schema(x) if isinstance(x, dict) else x for x in v] if isinstance(v, list) else v)
    return out


def _gemini_tools(tools: List[Dict]):
    from google.genai import types
    decls = [types.FunctionDeclaration(name=t["function"]["name"], description=t["function"]["description"][:1024],
                                       parameters_json_schema=_clean_schema(t["function"]["parameters"])) for t in tools]
    return [types.Tool(function_declarations=decls)] if decls else None


async def gemini_simple(model: str, prompt: str, json_mode: bool = False) -> str:
    """One-shot native call (used for schedule parsing and other structured helpers)."""
    from google.genai import types
    client = _gemini_client()
    r = await client.aio.models.generate_content(model=model, contents=prompt, config=types.GenerateContentConfig(
        temperature=0, response_mime_type="application/json" if json_mode else None))
    return r.text or ""


async def gemini_models() -> List[str]:
    client = _gemini_client()
    out = []
    async for m in await client.aio.models.list():
        if "generateContent" in (m.supported_actions or []):
            out.append((m.name or "").replace("models/", ""))
    return out
