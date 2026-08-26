"""社交推文机器翻译：Google → Microsoft → 腾讯交互翻译。无需 API Key。

普通推文走本模块；`AI_ANALYZE_HANDLES`（如白毛股神 aleabitoreddit）仍走 analyzer.py 的 DeepSeek 分析+翻译。
接口与旧 DeepSeek 版一致：`translate_texts({"content": "...", "reference": "..."})`。
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid

import aiohttp
from loguru import logger

from . import config

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
_TIMEOUT = aiohttp.ClientTimeout(sock_connect=5, total=12)
_GOOGLE_MAX_CHARS = 4500
_DEFAULT_PROVIDERS = ("google", "microsoft", "transmart")

_bing_lock = asyncio.Lock()
_bing_cache: dict = {
    "token": "",
    "key": "",
    "ig": "",
    "expire_at": 0.0,
    "cookies": None,
}


def _headers() -> dict[str, str]:
    return {"User-Agent": _UA}


def _needs_translate(text: str) -> bool:
    """纯 emoji / 过短 / 已是中文主导 → 不请求机器翻译。"""
    raw = (text or "").strip()
    if not raw or len(raw) < 2:
        return False
    cjk = sum(1 for c in raw if "\u4e00" <= c <= "\u9fff")
    letters = sum(1 for c in raw if c.isalpha())
    if letters + cjk < 2:
        return False
    total = letters + cjk
    if total and cjk / total >= 0.5:
        return False
    return True


def _usable(src: str, out: str) -> bool:
    text = (out or "").strip()
    if not text:
        return False
    if text.lower() == (src or "").strip().lower():
        return False
    return "原文为中文" not in text


def _parse_google_payload(data) -> str:
    if not isinstance(data, list) or not data:
        raise ValueError("google payload empty")
    first = data[0]
    # translate.google.com/translate_a/t 与 clients5: [["中文","en"]]
    if isinstance(first, list) and first and isinstance(first[0], str):
        return first[0].strip()
    # translate_a/single: [[["中文","hello",...], ...], ...]
    if isinstance(first, list):
        parts = []
        for chunk in first:
            if isinstance(chunk, list) and chunk and isinstance(chunk[0], str):
                parts.append(chunk[0])
        out = "".join(parts).strip()
        if out:
            return out
    raise ValueError("unexpected google payload")


async def _google_translate(session: aiohttp.ClientSession, text: str) -> str:
    if len(text) > _GOOGLE_MAX_CHARS:
        raise ValueError(f"text too long for google ({len(text)})")
    endpoints = (
        (
            "https://translate.google.com/translate_a/t",
            {"client": "gtx", "sl": "auto", "tl": "zh-CN"},
        ),
        (
            "https://clients5.google.com/translate_a/t",
            {"client": "dict-chrome-ex", "sl": "auto", "tl": "zh-CN"},
        ),
    )
    last_err: Exception | None = None
    for url, params in endpoints:
        try:
            async with session.post(
                url,
                params=params,
                data={"q": text},
                headers={
                    **_headers(),
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            ) as resp:
                resp.raise_for_status()
                return _parse_google_payload(await resp.json())
        except Exception as e:
            last_err = e
            logger.warning(f"[translate/google] {url.split('/')[2]} 失败: {e}")
    raise last_err or RuntimeError("google translate failed")


async def _microsoft_edge_translate(session: aiohttp.ClientSession, text: str) -> str:
    async with session.post(
        "https://edge.microsoft.com/translate/translatetext",
        params={"from": "", "to": "zh-Hans"},
        json=[text],
        headers={**_headers(), "Content-Type": "application/json"},
    ) as resp:
        resp.raise_for_status()
        data = await resp.json()
        return str(data[0]["translations"][0]["text"]).strip()


async def _bing_refresh_locked(session: aiohttp.ClientSession) -> None:
    async with session.get(
        "https://www.bing.com/translator", headers=_headers()
    ) as resp:
        resp.raise_for_status()
        html = await resp.text()
        cookies = {k: v.value for k, v in resp.cookies.items()}
    ig_m = re.search(r'IG:"([^"]+)"', html)
    tok_m = re.search(
        r"params_AbusePreventionHelper\s*=\s*\[(\d+)\s*,\s*\"([^\"]+)\"",
        html,
    )
    if not ig_m or not tok_m:
        raise RuntimeError("bing translator page missing token")
    issued_ms = int(tok_m.group(1))
    _bing_cache["ig"] = ig_m.group(1)
    _bing_cache["key"] = tok_m.group(1)
    _bing_cache["token"] = tok_m.group(2)
    _bing_cache["cookies"] = cookies
    _bing_cache["expire_at"] = min(issued_ms / 1000.0 + 480.0, time.time() + 480.0)


async def _microsoft_bing_translate(session: aiohttp.ClientSession, text: str) -> str:
    async with _bing_lock:
        now = time.time()
        if _bing_cache["expire_at"] <= now + 30 or not _bing_cache["token"]:
            await _bing_refresh_locked(session)
        ig = _bing_cache["ig"]
        key = _bing_cache["key"]
        token = _bing_cache["token"]
        cookies = _bing_cache["cookies"]
    url = (
        f"https://www.bing.com/ttranslatev3?isVertical=1&&IG={ig}&IID=translator.5024.1"
    )
    async with session.post(
        url,
        data={
            "fromLang": "auto-detect",
            "to": "zh-Hans",
            "text": text,
            "token": token,
            "key": key,
        },
        cookies=cookies,
        headers={
            **_headers(),
            "Referer": "https://www.bing.com/translator",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    ) as resp:
        resp.raise_for_status()
        data = await resp.json()
    row = data[0] if isinstance(data, list) else data
    return str(row["translations"][0]["text"]).strip()


async def _microsoft_translate(session: aiohttp.ClientSession, text: str) -> str:
    try:
        return await _microsoft_edge_translate(session, text)
    except Exception as e:
        logger.warning(f"[translate/microsoft] Edge 失败，改走 Bing: {e}")
        return await _microsoft_bing_translate(session, text)


async def _transmart_translate(session: aiohttp.ClientSession, text: str) -> str:
    client_key = (
        f"browser-chrome-128.0.0-Linux-{uuid.uuid4()}-{int(time.time() * 1000)}"
    )
    async with session.post(
        "https://transmart.qq.com/api/imt",
        json={
            "header": {
                "fn": "auto_translation",
                "session": "",
                "client_key": client_key,
                "user": "",
            },
            "type": "plain",
            "model_category": "normal",
            "source": {"lang": "auto", "text_list": [text]},
            "target": {"lang": "zh"},
        },
        headers={
            **_headers(),
            "Content-Type": "application/json",
            "Referer": "https://transmart.qq.com/",
        },
    ) as resp:
        resp.raise_for_status()
        data = await resp.json()
    header = data.get("header") if isinstance(data, dict) else None
    if isinstance(header, dict) and header.get("ret_code") not in (
        "succ",
        "success",
        None,
    ):
        raise RuntimeError(f"transmart ret_code={header.get('ret_code')}")
    parts = data.get("auto_translation") if isinstance(data, dict) else None
    if not isinstance(parts, list) or not parts:
        raise RuntimeError("transmart empty translation")
    return str(parts[0]).strip()


_PROVIDER_FUNCS = {
    "google": _google_translate,
    "microsoft": _microsoft_translate,
    "transmart": _transmart_translate,
}


def _provider_order() -> tuple[str, ...]:
    raw = getattr(config, "TRANSLATE_PROVIDERS", _DEFAULT_PROVIDERS)
    if isinstance(raw, str):
        names = [p.strip().lower() for p in raw.split(",") if p.strip()]
    elif isinstance(raw, (list, tuple)):
        names = [str(p).strip().lower() for p in raw if str(p).strip()]
    else:
        names = list(_DEFAULT_PROVIDERS)
    out = tuple(n for n in names if n in _PROVIDER_FUNCS)
    return out or _DEFAULT_PROVIDERS


async def _translate_one(
    session: aiohttp.ClientSession, src: str, providers: tuple[str, ...]
) -> tuple[str, str]:
    """英/外文 → 中文。返回 (译文, provider)；全部失败则 ("", "")。"""
    for name in providers:
        fn = _PROVIDER_FUNCS.get(name)
        if not fn:
            continue
        try:
            out = await fn(session, src)
        except Exception as e:
            logger.warning(f"[translate/{name}] 失败: {e}")
            continue
        if _usable(src, out):
            return out.strip(), name
        logger.warning(f"[translate/{name}] 译文不可用，换下一个")
    return "", ""


async def translate_texts(texts_dict: dict[str, str]) -> dict[str, str] | None:
    """批量翻译多个文本字段。

    输入 dict，例如 {"content": "...", "reference": "..."}
    返回翻译后的 dict，原样保留键名。全部失败或无需翻译时返回 None。
    """
    if not texts_dict:
        return None

    valid_texts: dict[str, str] = {}
    for k, v in texts_dict.items():
        if not v or not str(v).strip():
            continue
        text = str(v)
        if len(text) > _GOOGLE_MAX_CHARS:
            text = text[:_GOOGLE_MAX_CHARS] + "...\n[⬇️ 原文过长已截断]"
        if _needs_translate(text):
            valid_texts[k] = text

    if not valid_texts:
        return None

    providers = _provider_order()
    async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
        keys = list(valid_texts.keys())
        results = await asyncio.gather(
            *(_translate_one(session, valid_texts[k], providers) for k in keys)
        )

    out: dict[str, str] = {}
    hit_providers: list[str] = []
    for key, (translated, provider) in zip(keys, results):
        if translated:
            out[key] = translated
            hit_providers.append(f"{key}:{provider}")

    if not out:
        logger.warning("🌐 机器翻译全部失败")
        return None

    logger.info(f"🌐 机器翻译完成 {' '.join(hit_providers)}")
    return out
