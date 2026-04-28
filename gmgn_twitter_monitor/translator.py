"""DeepSeek 翻译模块 — 用于将推文内容翻译为中文。

采用极简 prompt + deepseek-chat 非流式输出，追求最低延迟。
"""

import asyncio
import aiohttp
from loguru import logger

from . import config

# 系统 prompt：极简指令，减少 token 消耗和推理时间
SYSTEM_PROMPT = (
    "你是推文翻译器。将用户给的英文推文翻译为简体中文。"
    "只输出翻译结果，不要解释，不要添加任何额外文字。"
    "保留原文中的 @用户名、$代币符号、URL 链接和 emoji 不翻译。"
    '如果原文已经是中文，直接输出"无需翻译"。'
)


async def translate_text(text: str) -> str | None:
    """调用 DeepSeek API 翻译文本。

    返回翻译结果字符串，失败或无需翻译时返回 None。
    """
    if not config.DEEPSEEK_API_KEY:
        return None

    if not text or len(text.strip()) < 5:
        return None

    # 快速判断：纯中文内容跳过翻译
    cjk_count = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    if cjk_count / max(len(text), 1) > 0.5:
        return None

    # 针对超长推文进行截断（加速翻译，同时防止加上翻译后突破 Telegram 4096 字符上限）
    if len(text) > 1500:
        text = text[:1500] + "...\n[原文过长已截断]"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config.DEEPSEEK_API_KEY}",
    }
    payload = {
        "model": config.DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        "stream": False,
        "temperature": 0.3,
        "max_tokens": 1024,
    }

    # 重试机制：最多尝试 2 次
    MAX_RETRIES = 2
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            timeout = aiohttp.ClientTimeout(total=45)  # 代理可能额外增加耗时
            
            # 使用 WARP 代理连接，开启 rdns=True 防止本地 DNS 解析失败/投毒导致的 Connection closed
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

                    if "无需翻译" in result:
                        return None

                    return result

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
