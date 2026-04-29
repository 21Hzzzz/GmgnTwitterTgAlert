"""DeepSeek 翻译模块 — 用于将推文内容翻译为中文。

采用结构化 JSON 提交，分别独立翻译多个文本字段，避免文本拼接导致特殊标点或结构丢失。
"""

import asyncio
import json
import aiohttp
from loguru import logger

from . import config

SYSTEM_PROMPT = (
    "你是推文翻译器。用户会输入一段 JSON，包含多个字段（如 content, reference 等）。\n"
    "请将其中所有的英文或其它外语推文翻译为简体中文，并以严格的 JSON 格式返回，保持原有键名不变。\n"
    "规则：\n"
    "1. 只输出翻译结果，不要解释，绝对不要添加任何 markdown 代码块（如 ```json）。\n"
    "2. 保留原文中的 @用户名、$代币符号、URL 链接和 emoji 不翻译。\n"
    "3. 如果某段文本已经是中文，或者只是短标点符号（如 `!`、`?` 等），则原样保留它的内容。\n"
    "4. 返回结果必须是合法的 JSON 对象。"
)

async def translate_texts(texts_dict: dict[str, str]) -> dict[str, str] | None:
    """调用 DeepSeek API 批量翻译多个文本字段。

    输入 dict，例如 {"content": "...", "reference": "..."}
    返回翻译后的 dict，原样保留键名。失败时返回 None。
    """
    if not config.DEEPSEEK_API_KEY or not texts_dict:
        return None

    valid_texts = {k: v for k, v in texts_dict.items() if v and len(v.strip()) > 0}
    if not valid_texts:
        return None

    # 针对超长推文进行截断，限制投喂给大模型的字数
    for k, v in valid_texts.items():
        if len(v) > 500:
            valid_texts[k] = v[:500] + "...\n[原文过长已截断]"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config.DEEPSEEK_API_KEY}",
    }
    payload = {
        "model": config.DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(valid_texts, ensure_ascii=False)},
        ],
        "stream": False,
        "temperature": 0.3,
        "max_tokens": 2048,
        "response_format": {"type": "json_object"}
    }

    MAX_RETRIES = 2
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            timeout = aiohttp.ClientTimeout(total=45)
            
            proxy_url = getattr(config, "PROXY_SERVER", "socks5://127.0.0.1:40000")
            from aiohttp_socks import ProxyConnector
            connector = ProxyConnector.from_url(proxy_url, rdns=True) if proxy_url else None
            
            async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
                async with session.post(
                    f"{config.DEEPSEEK_BASE_URL}/chat/completions",
                    headers=headers,
                    json=payload,
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        logger.error(f"🌐 DeepSeek 翻译失败 [{resp.status}]: {body[:200]}")
                        if resp.status >= 500 and attempt < MAX_RETRIES:
                            await asyncio.sleep(2)
                            continue
                        return None

                    data = await resp.json()
                    result = data["choices"][0]["message"]["content"].strip()

                    # 容错：去除 markdown json 代码块
                    if result.startswith("```json"):
                        result = result[7:]
                    if result.startswith("```"):
                        result = result[3:]
                    if result.endswith("```"):
                        result = result[:-3]
                        
                    result = result.strip()
                    try:
                        return json.loads(result)
                    except json.JSONDecodeError:
                        logger.error(f"🌐 翻译结果无法解析为 JSON: {result}")
                        return None

        except asyncio.TimeoutError:
            logger.warning(f"🌐 DeepSeek 翻译超时 (第 {attempt} 次尝试)")
            if attempt < MAX_RETRIES:
                continue
            return None
        except aiohttp.ClientError as e:
            logger.warning(f"🌐 DeepSeek 翻译网络异常 (第 {attempt} 次尝试): {e}")
            if attempt < MAX_RETRIES:
                await asyncio.sleep(1)
                continue
            return None
        except Exception as e:
            logger.error(f"🌐 DeepSeek 翻译发生预期外错误: {repr(e)}")
            return None

    return None
