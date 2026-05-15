import asyncio
import json
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Any

import aiohttp
from loguru import logger

from . import config


class DeepSeekSummarizer:
    """Generate Chinese Telegram-ready summaries with DeepSeek."""

    def __init__(self):
        self.last_error: str | None = None

    async def summarize(
        self,
        messages: list[dict[str, Any]],
        window_start: int,
        window_end: int,
    ) -> str | None:
        self.last_error = None
        if not config.DEEPSEEK_API_KEY:
            self.last_error = "DEEPSEEK_API_KEY is not configured"
            logger.warning(f"AI 定时总结已开启，但 {self.last_error}")
            return None
        if not messages:
            return None

        result = await self._call_deepseek(messages, window_start, window_end)
        if result is None:
            if not self.last_error:
                self.last_error = "invalid or empty DeepSeek response"
            return None
        return format_summary_html(result, messages, window_start, window_end)

    async def _call_deepseek(
        self,
        messages: list[dict[str, Any]],
        window_start: int,
        window_end: int,
    ) -> dict[str, Any] | None:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.DEEPSEEK_API_KEY}",
        }
        payload = {
            "window_start": _format_ts(window_start),
            "window_end": _format_ts(window_end),
            "messages": [_compact_message(row) for row in messages],
        }
        request_body = {
            "model": config.DEEPSEEK_SUMMARY_MODEL,
            "messages": [
                {"role": "system", "content": config.DEEPSEEK_SUMMARY_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            "stream": False,
            "temperature": 0.2,
            "max_tokens": 3072,
            "response_format": {"type": "json_object"},
        }

        max_retries = max(1, config.AI_SUMMARY_MAX_RETRIES)
        timeout_seconds = max(1, config.AI_SUMMARY_TIMEOUT_SECONDS)

        for attempt in range(1, max_retries + 1):
            try:
                connector = None
                if config.PROXY_SERVER:
                    from aiohttp_socks import ProxyConnector

                    connector = ProxyConnector.from_url(config.PROXY_SERVER, rdns=True)

                timeout = aiohttp.ClientTimeout(total=timeout_seconds)
                async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
                    async with session.post(
                        f"{config.DEEPSEEK_BASE_URL}/chat/completions",
                        headers=headers,
                        json=request_body,
                    ) as resp:
                        if resp.status != 200:
                            body = await resp.text()
                            self.last_error = f"DeepSeek HTTP {resp.status}: {body[:200]}"
                            logger.error(f"AI 总结失败 [{resp.status}]: {body[:200]}")
                            if resp.status >= 500 and attempt < max_retries:
                                await asyncio.sleep(2)
                                continue
                            return None

                        data = await resp.json()
                        content = data["choices"][0]["message"]["content"].strip()
                        return _parse_json_object(content)
            except asyncio.TimeoutError:
                self.last_error = f"timeout after {timeout_seconds}s on attempt {attempt}/{max_retries}"
                logger.warning(f"AI 总结超时 (第 {attempt}/{max_retries} 次尝试, {timeout_seconds}s)")
                if attempt < max_retries:
                    continue
            except aiohttp.ClientError as e:
                self.last_error = f"network error on attempt {attempt}/{max_retries}: {e}"
                logger.warning(f"AI 总结网络异常 (第 {attempt}/{max_retries} 次尝试): {e}")
                if attempt < max_retries:
                    await asyncio.sleep(1)
                    continue
            except Exception as e:
                self.last_error = f"unexpected error: {repr(e)}"
                logger.error(f"AI 总结发生预期外错误: {repr(e)}")
                return None

        return None


def format_summary_html(
    result: dict[str, Any],
    messages: list[dict[str, Any]],
    window_start: int,
    window_end: int,
) -> str:
    source_map = {int(row["id"]): row for row in messages if row.get("id") is not None}
    lines: list[str] = [
        "<b>🤖 GMGN AI 摘要</b>",
        f"{escape(_format_ts(window_start))} - {escape(_format_ts(window_end))}",
        f"共 {len(messages)} 条消息",
        "",
    ]

    important = _as_list(result.get("important"))
    watchlist = _as_list(result.get("watchlist"))

    if important:
        lines.append("<b>重要信号</b>")
        _append_items(lines, important, source_map)
    else:
        lines.append("<b>重要信号</b>")
        lines.append("本轮未发现明确高价值信号。")

    if watchlist:
        lines.append("")
        lines.append("<b>值得关注</b>")
        _append_items(lines, watchlist, source_map)

    noise_summary = result.get("noise_summary")
    stats = result.get("stats") if isinstance(result.get("stats"), dict) else {}
    useful_count = stats.get("useful_count")
    noise_count = stats.get("noise_count")

    lines.append("")
    lines.append("<b>噪音过滤</b>")
    if noise_summary:
        lines.append(escape(str(noise_summary)))
    if useful_count is not None or noise_count is not None:
        lines.append(f"有用 {useful_count or 0} 条，噪音 {noise_count or 0} 条。")

    return _join_limited(lines, limit=4096)


def _append_items(
    lines: list[str],
    items: list[Any],
    source_map: dict[int, dict[str, Any]],
) -> None:
    for index, item in enumerate(items[:8], start=1):
        if isinstance(item, dict):
            title = item.get("title") or item.get("summary") or "未命名信号"
            reason = item.get("reason") or ""
            confidence = item.get("confidence")
            source_ids = item.get("source_ids") or []
        else:
            title = str(item)
            reason = ""
            confidence = None
            source_ids = []

        suffix = f" ({escape(str(confidence))})" if confidence else ""
        lines.append(f"{index}. <b>{escape(str(title))}</b>{suffix}")
        if reason:
            lines.append(escape(str(reason)))

        sources = _format_sources(source_ids, source_map)
        if sources:
            lines.append(f"来源: {sources}")


def _format_sources(source_ids: Any, source_map: dict[int, dict[str, Any]]) -> str:
    if not isinstance(source_ids, list):
        source_ids = [source_ids]

    links: list[str] = []
    for raw_source_id in source_ids[:4]:
        try:
            source_id = int(raw_source_id)
        except (TypeError, ValueError):
            continue

        row = source_map.get(source_id)
        if not row:
            continue

        handle = (row.get("author_handle") or "unknown").lstrip("@")
        tweet_id = row.get("tweet_id")
        label = f"@{escape(handle)}"
        if tweet_id and handle != "unknown":
            url = f"https://x.com/{handle}/status/{tweet_id}"
            links.append(f'<a href="{escape(url)}">{label}</a>')
        else:
            links.append(label)

    return " / ".join(links)


def _compact_message(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "handle": row.get("author_handle"),
        "action": row.get("action"),
        "tweet_id": row.get("tweet_id"),
        "received_at": _format_ts(int(row.get("received_at") or 0)),
        "tweet_time": _format_ts(int(row.get("tweet_timestamp") or 0)) if row.get("tweet_timestamp") else "",
        "text": _truncate(row.get("content_text") or "", 700),
        "reference_text": _truncate(row.get("reference_text") or "", 500),
    }


def _parse_json_object(content: str) -> dict[str, Any] | None:
    if content.startswith("```json"):
        content = content[7:]
    if content.startswith("```"):
        content = content[3:]
    if content.endswith("```"):
        content = content[:-3]

    try:
        parsed = json.loads(content.strip())
    except json.JSONDecodeError:
        logger.error(f"AI 总结结果无法解析为 JSON: {content[:500]}")
        return None

    return parsed if isinstance(parsed, dict) else None


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "...[截断]"


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _format_ts(ts: int) -> str:
    tz_cst = timezone(timedelta(hours=8))
    return datetime.fromtimestamp(ts, tz=tz_cst).strftime("%Y-%m-%d %H:%M:%S")


def _join_limited(lines: list[str], limit: int) -> str:
    output: list[str] = []
    current_len = 0
    for line in lines:
        added_len = len(line) + (1 if output else 0)
        if current_len + added_len > limit - 20:
            output.append("...")
            break
        output.append(line)
        current_len += added_len
    return "\n".join(output)
