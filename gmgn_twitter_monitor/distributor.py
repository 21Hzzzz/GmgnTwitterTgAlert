import asyncio
import json
from datetime import datetime, timedelta, timezone

import aiohttp
from loguru import logger


class BaseDistributor:
    """Base class for message distributors."""

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def distribute(self, message: dict) -> None:
        raise NotImplementedError


class LoggingDistributor(BaseDistributor):
    async def distribute(self, message: dict) -> None:
        logger.debug(f"完整标准 JSON: {message}")


class TelegramDistributor(BaseDistributor):
    """Send standardized GMGN messages to Telegram groups/channels."""

    def __init__(
        self,
        bot_token: str,
        default_channel_id: str = "",
        enable_default: bool = False,
        main_channel_id: str = "",
        enable_main: bool = False,
        channel_map: dict[str, list[str]] | None = None,
        filter_handles: list[str] | None = None,
    ):
        self.bot_token = bot_token
        self.default_channel_id = default_channel_id
        self.enable_default = enable_default
        self.main_channel_id = main_channel_id
        self.enable_main = enable_main
        self.channel_map = channel_map or {}
        self.filter_handles = [h.lower().lstrip("@") for h in (filter_handles or [])]
        self.api_base = f"https://api.telegram.org/bot{bot_token}"
        self._session: aiohttp.ClientSession | None = None

    async def start(self):
        has_default = self.enable_default and self.default_channel_id
        has_main = self.enable_main and self.main_channel_id
        has_routes = bool(self.channel_map)
        if not self.bot_token or not (has_default or has_main or has_routes):
            logger.info("Telegram 分发器未配置 Token/Channel，已跳过启动")
            return
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
        filter_desc = ", ".join(self.filter_handles) if self.filter_handles else "全部"
        logger.success(
            "Telegram 分发器已启动 "
            f"(默认群开启: {bool(has_default)}, 未路由主群开启: {bool(has_main)}, "
            f"分组数: {len(self.channel_map)}, 过滤: {filter_desc})"
        )

    async def stop(self):
        if self._session:
            await self._session.close()
            logger.info("Telegram 分发器已关闭")

    @staticmethod
    def _normalize_handle(handle: str | None) -> str:
        return (handle or "").strip().lower().lstrip("@")

    @staticmethod
    def _append_unique(targets: list[str], channel_id: str | None) -> None:
        if channel_id and channel_id not in targets:
            targets.append(channel_id)

    def resolve_target_channel_ids(self, handle: str | None) -> list[str]:
        """Return Telegram target IDs for a handle, preserving order and uniqueness."""
        normalized = self._normalize_handle(handle)
        if self.filter_handles and normalized not in self.filter_handles:
            return []

        targets: list[str] = []
        if self.enable_default:
            self._append_unique(targets, self.default_channel_id)

        routed_channel_ids = self.channel_map.get(normalized, [])
        for channel_id in routed_channel_ids:
            self._append_unique(targets, channel_id)

        if not routed_channel_ids and self.enable_main:
            self._append_unique(targets, self.main_channel_id)

        return targets

    @staticmethod
    def _escape_html(text: str) -> str:
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def _format_followers(self, count: int | None) -> str:
        if not count:
            return ""
        if count >= 1_000_000:
            return f" · {count / 1_000_000:.1f}M 粉丝"
        if count >= 1_000:
            return f" · {count / 1_000:.1f}K 粉丝"
        return f" · {count} 粉丝"

    def _format_message(self, msg: dict, include_text: bool = True) -> str:
        action = msg.get("action", "unknown")
        author = msg.get("author", {})
        handle = author.get("handle", "unknown")
        author_name = self._escape_html(author.get("name") or handle)
        author_followers = self._format_followers(author.get("followers"))
        unfollow_target = msg.get("unfollow_target")

        action_map = {
            "tweet": "📝 发布新推文",
            "repost": "🔄 转推",
            "reply": "💬 回复",
            "quote": "📌 引用推文",
            "follow": "✅ 新增关注",
            "unfollow": "❌ 取消关注",
            "delete_post": "🗑️ 删除推文",
            "photo": "🖼️ 更换头像",
            "description": "⇧ 简介更新",
            "name": "📛 更改昵称",
            "pin": "📌 置顶推文",
            "unpin": "📍 取消置顶",
        }
        action_text = action_map.get(action, f"❓ {action}")

        lines = []
        author_link = f'👤 <a href="https://x.com/{handle}">{author_name} @{handle}</a>{author_followers}'

        if action in ("follow", "unfollow") and unfollow_target:
            lines.append(f"<b>{action_text}</b>")
            lines.append(author_link)
            target_handle = unfollow_target.get("handle", "?")
            target_name = self._escape_html(unfollow_target.get("name") or target_handle)
            target_followers = self._format_followers(unfollow_target.get("followers"))
            target_link = (
                f'<a href="https://x.com/{target_handle}">'
                f"{target_name} @{target_handle}</a>{target_followers}"
            )
            prefix = "✅ 关注了" if action == "follow" else "❌ 取关了"
            lines.append(f"{prefix} {target_link}")
            return "\n".join(lines)

        lines.append(f"<b>{action_text}</b>")
        lines.append(author_link)

        if action in ("repost", "reply", "quote", "delete_post"):
            reference = msg.get("reference") or {}
            ref_handle = reference.get("author_handle")
            ref_name = self._escape_html(reference.get("author_name") or ref_handle or "?")
            ref_followers = self._format_followers(reference.get("author_followers"))
            if ref_handle:
                ref_link = f'<a href="https://x.com/{ref_handle}">{ref_name} @{ref_handle}</a>{ref_followers}'
                prefix_map = {"repost": "🔄 转推了", "reply": "💬 回复了", "quote": "📌 引用了"}
                if action == "delete_post":
                    prefix = prefix_map.get(msg.get("original_action", ""), "↳ 原属于")
                else:
                    prefix = prefix_map.get(action, "➡️ 指向")
                lines.append(f"{prefix} {ref_link}")

        if action == "delete_post" and msg.get("original_action"):
            orig_label = action_map.get(msg.get("original_action"), msg.get("original_action"))
            lines.append(f"  ↳ 原类型: {orig_label}")

        if action == "photo":
            avatar_change = msg.get("avatar_change")
            if avatar_change:
                before_url = avatar_change.get("before", "")
                after_url = avatar_change.get("after", "")
                lines.append("")
                if before_url:
                    lines.append(f'🅰️ <a href="{before_url}">旧头像</a>')
                if after_url:
                    lines.append(f'🅱️ <a href="{after_url}">新头像</a>')

        if action == "description":
            bio_change = msg.get("bio_change")
            if bio_change:
                lines.append("\n<b>旧简介:</b>")
                lines.append(self._escape_html(bio_change.get("before", "")))
                lines.append("\n<b>新简介:</b>")
                lines.append(self._escape_html(bio_change.get("after", "")))
        elif include_text:
            content = msg.get("content") or {}
            text = content.get("text")
            if text:
                if len(text) > 800:
                    text = text[:800] + "...\n[⬇️ 正文过长已截断]"
                lines.append("")
                lines.append(self._escape_html(text))

            reference = msg.get("reference") or {}
            ref_text = reference.get("text")
            if ref_text:
                if len(ref_text) > 500:
                    ref_text = ref_text[:500] + "...\n[⬇️ 原推过长已截断]"
                lines.append("")
                lines.append(f"<blockquote>💬 原推：\n{self._escape_html(ref_text)}</blockquote>")

        return "\n".join(lines)

    async def _send_api(self, endpoint: str, payload: dict) -> dict | None:
        if not self._session:
            return None

        try:
            async with self._session.post(f"{self.api_base}/{endpoint}", json=payload) as resp:
                if resp.status == 200:
                    return await resp.json()
                if resp.status == 429:
                    data = await resp.json()
                    retry_after = data.get("parameters", {}).get("retry_after", 5)
                    logger.warning(f"TG 被限流，{retry_after}s 后重试")
                    await asyncio.sleep(retry_after)
                    async with self._session.post(f"{self.api_base}/{endpoint}", json=payload) as retry_resp:
                        if retry_resp.status == 200:
                            return await retry_resp.json()
                        body = await retry_resp.text()
                        logger.error(f"TG 重试仍失败 [{retry_resp.status}]: {body[:200]}")
                        return None
                body = await resp.text()
                logger.error(f"TG 推送失败 [{resp.status}]: {body[:200]}")
                return None
        except asyncio.TimeoutError:
            logger.error("TG 推送超时 (15s)")
            return None
        except aiohttp.ClientError as e:
            logger.error(f"TG 推送网络异常: {e}")
            return None
        except Exception as e:
            logger.error(f"TG 推送未知异常: {e}")
            return None

    async def _translate_and_edit(
        self,
        message_id: int,
        _header_no_text: str,
        footer: str,
        message: dict,
        translated_dict: dict[str, str],
        target_channel_id: str,
        link_preview_options: dict | None = None,
    ) -> None:
        content = message.get("content", {}) or {}
        reference = message.get("reference") or {}
        bio_change = message.get("bio_change") or {}
        text_parts = {}
        if content.get("text"):
            text_parts["content"] = content["text"]
        if reference.get("text"):
            text_parts["reference"] = reference["text"]
        if bio_change.get("after"):
            text_parts["bio"] = bio_change["after"]

        main_text = translated_dict.get("content") or text_parts.get("content", "")
        ref_text = translated_dict.get("reference") or text_parts.get("reference", "")
        bio_text = translated_dict.get("bio") or text_parts.get("bio", "")

        if (
            main_text == text_parts.get("content", "")
            and ref_text == text_parts.get("reference", "")
            and bio_text == text_parts.get("bio", "")
        ):
            logger.info(f"翻译结果与原文相同，跳过编辑: {target_channel_id}")
            return

        def format_part(translated: str, is_ref: bool = False) -> str:
            limit = 500 if is_ref else 800
            if len(translated) > limit:
                translated = translated[:limit] + "...\n[⬇️ 译文过长已截断]"
            return self._escape_html(translated)

        translated_html_parts = []
        if main_text or bio_text:
            translated_text = main_text if main_text else bio_text
            translated_html_parts.append(format_part(translated_text, is_ref=False))
        if ref_text:
            escaped_ref = format_part(ref_text, is_ref=True)
            translated_html_parts.append(f"<blockquote>💬 原推翻译：\n{escaped_ref}</blockquote>")

        original_html = self._format_message(message)
        translated_html = "\n\n".join(translated_html_parts)
        new_text = f"{original_html}\n\n—— 🇨🇳 中文翻译 ——\n{translated_html}\n\n{footer}"

        payload = {
            "chat_id": target_channel_id,
            "message_id": message_id,
            "text": new_text[:4096],
            "parse_mode": "HTML",
        }
        if link_preview_options:
            payload["link_preview_options"] = link_preview_options

        result = await self._send_api("editMessageText", payload)
        handle = message.get("author", {}).get("handle", "?")
        if result and result.get("ok"):
            logger.info(f"TG 翻译追加成功: @{handle} -> {target_channel_id}")
        else:
            logger.warning(f"TG 翻译追加失败: @{handle} -> {target_channel_id}")

    async def _distribute_to_channel(
        self,
        message: dict,
        handle: str,
        action: str,
        target_channel_id: str,
        time_log_str: str,
    ) -> dict | None:
        if action == "photo":
            avatar_change = message.get("avatar_change") or {}
            before_url = avatar_change.get("before", "")
            after_url = avatar_change.get("after", "")

            if before_url and after_url:
                caption = self._format_message(message)[:1024]
                media = [
                    {"type": "photo", "media": before_url, "caption": caption, "parse_mode": "HTML"},
                    {"type": "photo", "media": after_url},
                ]
                payload = {"chat_id": target_channel_id, "media": json.dumps(media)}
                result = await self._send_api("sendMediaGroup", payload)
                if result and result.get("ok"):
                    logger.info(f"TG 头像变更推送成功: @{handle} -> {target_channel_id} | {time_log_str}")
                return None

        tz_cst = timezone(timedelta(hours=8))
        ts = message.get("timestamp", 0)
        tweet_time = datetime.fromtimestamp(ts, tz=tz_cst).strftime("%Y-%m-%d %H:%M:%S") if ts else "未知"
        footer = f"🕒 推文时间: {tweet_time}"

        header = self._format_message(message)
        initial_text = f"{header}\n\n{footer}"

        preview_url = None
        if action in ("follow", "unfollow"):
            target_handle = message.get("unfollow_target", {}).get("handle")
            if target_handle:
                preview_url = f"https://vxtwitter.com/{target_handle}"
        elif action == "repost":
            reference = message.get("reference") or {}
            ref_handle = reference.get("author_handle")
            ref_tweet_id = reference.get("tweet_id")
            if ref_handle and ref_tweet_id:
                preview_url = f"https://fxtwitter.com/{ref_handle}/status/{ref_tweet_id}"
            elif message.get("tweet_id") and handle:
                preview_url = f"https://fxtwitter.com/{handle}/status/{message.get('tweet_id')}"
        elif action in ("reply", "quote"):
            reference = message.get("reference") or {}
            ref_handle = reference.get("author_handle")
            ref_tweet_id = reference.get("tweet_id")
            content = message.get("content") or {}
            has_media = len(content.get("media") or []) > 0

            if has_media and message.get("tweet_id") and handle:
                preview_url = f"https://fxtwitter.com/{handle}/status/{message.get('tweet_id')}"
            elif ref_handle and ref_tweet_id:
                preview_url = f"https://fxtwitter.com/{ref_handle}/status/{ref_tweet_id}"
            else:
                tweet_id = message.get("tweet_id", "")
                if tweet_id and handle:
                    preview_url = f"https://fxtwitter.com/{handle}/status/{tweet_id}"
        elif action == "delete_post":
            reference = message.get("reference") or {}
            ref_handle = reference.get("author_handle")
            ref_tweet_id = reference.get("tweet_id")
            if ref_handle and ref_tweet_id:
                preview_url = f"https://fxtwitter.com/{ref_handle}/status/{ref_tweet_id}"
            else:
                tweet_id = message.get("tweet_id", "")
                if tweet_id and handle:
                    preview_url = f"https://fxtwitter.com/{handle}/status/{tweet_id}"
        elif action in ("tweet", "pin", "unpin"):
            tweet_id = message.get("tweet_id", "")
            if tweet_id and handle:
                preview_url = f"https://fxtwitter.com/{handle}/status/{tweet_id}"
        elif handle:
            preview_url = f"https://vxtwitter.com/{handle}"

        link_preview_options = {"is_disabled": False, "prefer_large_media": True}
        if preview_url:
            link_preview_options["url"] = preview_url

        payload = {
            "chat_id": target_channel_id,
            "text": initial_text[:4096],
            "parse_mode": "HTML",
            "link_preview_options": link_preview_options,
        }

        result = await self._send_api("sendMessage", payload)
        if result and result.get("ok"):
            logger.info(f"TG 推送成功: @{handle} -> {target_channel_id} | {time_log_str}")

            resp_result = result.get("result")
            msg_id = None
            if isinstance(resp_result, dict):
                msg_id = resp_result.get("message_id")
            elif isinstance(resp_result, list) and resp_result:
                msg_id = resp_result[0].get("message_id")

            if msg_id:
                return {
                    "msg_id": msg_id,
                    "header_no_text": self._format_message(message, include_text=False),
                    "footer": footer,
                    "channel_id": target_channel_id,
                    "link_preview_options": link_preview_options,
                }
        return None

    async def _pre_translate(self, message: dict) -> dict[str, str] | None:
        content = message.get("content", {}) or {}
        reference = message.get("reference") or {}
        bio_change = message.get("bio_change") or {}
        text_parts = {}
        if content.get("text"):
            text_parts["content"] = content["text"]
        if reference.get("text"):
            text_parts["reference"] = reference["text"]
        if bio_change.get("after"):
            text_parts["bio"] = bio_change["after"]

        if not text_parts:
            return None

        from .translator import translate_texts

        return await translate_texts(text_parts)

    async def distribute(self, message: dict) -> None:
        if not self._session:
            return

        handle = message.get("author", {}).get("handle", "?")
        action = message.get("action", "")
        target_channel_ids = self.resolve_target_channel_ids(handle)
        if not target_channel_ids:
            return

        tz_cst = timezone(timedelta(hours=8))
        ts = message.get("timestamp", 0)
        tweet_time = datetime.fromtimestamp(ts, tz=tz_cst).strftime("%Y-%m-%d %H:%M:%S") if ts else "未知"
        push_time = datetime.now(tz=tz_cst).strftime("%Y-%m-%d %H:%M:%S")
        time_log_str = f"| 推文时间: {tweet_time} 推送时间: {push_time}"

        push_tasks = [
            self._distribute_to_channel(message, handle, action, channel_id, time_log_str)
            for channel_id in target_channel_ids
        ]
        translate_task = self._pre_translate(message)

        all_results = await asyncio.gather(*push_tasks, translate_task, return_exceptions=True)
        push_results = all_results[:-1]
        translate_result = all_results[-1]

        if isinstance(translate_result, Exception):
            logger.error(f"翻译异常: {translate_result}")
            return
        if not translate_result:
            return

        edit_tasks = []
        for result in push_results:
            if isinstance(result, Exception) or result is None:
                continue
            edit_tasks.append(
                self._translate_and_edit(
                    result["msg_id"],
                    result["header_no_text"],
                    result["footer"],
                    message,
                    translate_result,
                    result["channel_id"],
                    result["link_preview_options"],
                )
            )

        if edit_tasks:
            await asyncio.gather(*edit_tasks, return_exceptions=True)


class DistributorHub:
    """Manage distributor lifecycle and fan out messages."""

    def __init__(self, distributors: list[BaseDistributor] | None = None):
        self.distributors = distributors or []

    async def start_all(self) -> None:
        for distributor in self.distributors:
            try:
                await distributor.start()
            except Exception as e:
                logger.error(f"分发器启动失败: {type(distributor).__name__} - {e}")

    async def stop_all(self) -> None:
        for distributor in self.distributors:
            try:
                await distributor.stop()
            except Exception as e:
                logger.error(f"分发器停止失败: {type(distributor).__name__} - {e}")

    async def publish(self, message: dict) -> None:
        tasks = [distributor.distribute(message) for distributor in self.distributors]
        if not tasks:
            return

        results = await asyncio.gather(*tasks, return_exceptions=True)
        for distributor, result in zip(self.distributors, results):
            if isinstance(result, Exception):
                logger.error(f"分发失败: {type(distributor).__name__} - {result}")
