import asyncio
import json
from contextlib import suppress
from datetime import datetime, timezone, timedelta

import aiohttp
from loguru import logger

from . import config as cfg


TEXT_ENRICHMENT_ACTIONS = {"tweet", "reply", "quote", "repost"}


def _diag_enabled(message_or_handle) -> bool:
    if isinstance(message_or_handle, dict):
        handle = (message_or_handle.get("author") or {}).get("handle") or ""
    else:
        handle = str(message_or_handle or "")
    return handle.lower() in cfg.DIAG_HANDLES


def _diag_log(message_or_handle, text: str, *, level: str = "info") -> None:
    if not _diag_enabled(message_or_handle):
        return
    handle = (
        ((message_or_handle.get("author") or {}).get("handle") or "")
        if isinstance(message_or_handle, dict)
        else str(message_or_handle or "")
    )
    log = getattr(logger, level)
    log(f"🔎 诊断下游: @{handle} {text}")


def _is_instagram_message(message: dict) -> bool:
    author = message.get("author") or {}
    tags = author.get("tags") or []
    return message.get("platform_flag") == 4 and "instagram" in tags


def _should_run_text_enrichment(message: dict) -> bool:
    if _is_instagram_message(message):
        from . import config as cfg
        if not cfg.INSTAGRAM_TRANSLATION_ENABLE:
            return False
    return message.get("action") in TEXT_ENRICHMENT_ACTIONS


class BaseDistributor:
    """分发器基类，所有通道必须继承并实现 distribute 方法。"""

    async def start(self) -> None:
        """启动分发器（子类可覆盖）。"""

    async def stop(self) -> None:
        """停止分发器（子类可覆盖）。"""

    async def distribute(self, message: dict) -> None:
        raise NotImplementedError


# ---------------------------------------------------------------------------
#  Telegram 频道推送分发器
# ---------------------------------------------------------------------------
class TelegramDistributor(BaseDistributor):
    """通过 Telegram Bot API 将消息推送到指定频道。

    支持按 author.handle 白名单过滤；内置 429 Rate-Limit 自动退避重试。
    """

    def __init__(self, bot_token: str, default_channel_id: str, enable_default: bool = False, channel_map: dict[str, str] | None = None, filter_handles: list[str] | None = None, storage=None):
        self.bot_token = bot_token
        self.default_channel_id = default_channel_id
        self.enable_default = enable_default
        self.channel_map = channel_map or {}
        self.filter_handles = [h.lower() for h in (filter_handles or [])]
        self.storage = storage
        self.api_base = f"https://api.telegram.org/bot{bot_token}"
        self._session: aiohttp.ClientSession | None = None
        # Future 用于解决 TG_FAST 与 TG_UPDATE 的竞态条件
        self._msg_history: dict[str, asyncio.Future] = {}
        self._filtered_sent_keys: dict[tuple[str, str], None] = {}

    async def start(self):
        if not self.bot_token or (not self.default_channel_id and not self.channel_map):
            logger.info("📱 Telegram 分发器未配置 Token/Channel，已跳过启动")
            return
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
        filter_desc = ", ".join(self.filter_handles) if self.filter_handles else "全部"
        logger.success(f"📱 Telegram 分发器已启动 (默认开启: {self.enable_default}, 分组数: {len(self.channel_map)}, 过滤: {filter_desc})")

    async def stop(self):
        if self._session:
            await self._session.close()
            logger.info("📱 Telegram 分发器已关闭")

    def _should_forward(self, message: dict) -> bool:
        """根据白名单判断是否需要转发该消息。"""
        if not self.filter_handles:
            return True
        handle = message.get("author", {}).get("handle", "")
        return handle.lower() in self.filter_handles

    def _target_channel_ids(self, handle: str) -> list[str]:
        """返回路由群组，并在启用时始终加入 ALL 全量群组。"""
        target_ids = list(self.channel_map.get(handle.lower(), []))
        if (
            self.enable_default
            and self.default_channel_id
            and self.default_channel_id not in target_ids
        ):
            target_ids.insert(0, self.default_channel_id)
        return target_ids

    @staticmethod
    def _track_matches(category: str, keywords: list[str]) -> bool:
        normalized_category = "".join((category or "").casefold().split())
        return any("".join(kw.casefold().split()) in normalized_category for kw in keywords if kw)

    @staticmethod
    def _escape_html(text: str) -> str:
        """转义 HTML 特殊字符。"""
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    @staticmethod
    def _wrap_blockquote(content: str, raw_text_len: int, threshold: int = 128) -> str:
        """根据原始文本长度决定是否使用可折叠 blockquote。

        ≤ threshold: 普通 blockquote，完整展示
        > threshold: expandable blockquote，折叠后默认显示 3 行
        """
        tag = "blockquote expandable" if raw_text_len > threshold else "blockquote"
        return f"<{tag}>{content}</blockquote>"

    def _format_followers(self, count: int | None) -> str:
        """格式化粉丝数为可读字符串。"""
        if not count:
            return ""
        if count >= 1_000_000:
            return f" · {count / 1_000_000:.1f}M 粉丝"
        if count >= 1_000:
            return f" · {count / 1_000:.1f}K 粉丝"
        return f" · {count} 粉丝"

    @staticmethod
    def _build_tweet_url(message: dict, handle: str, action: str) -> str:
        """根据消息类型构建 Twitter 帖子原文链接（x.com 真实链接）。"""
        tweet_id = message.get("tweet_id", "")
        reference = message.get("reference") or {}
        ref_handle = reference.get("author_handle")
        ref_tweet_id = reference.get("tweet_id")

        if action in ("tweet", "reply", "quote", "pin", "unpin"):
            if tweet_id and handle:
                return f"https://x.com/{handle}/status/{tweet_id}"
        elif action == "repost":
            if ref_handle and ref_tweet_id:
                return f"https://x.com/{ref_handle}/status/{ref_tweet_id}"
            elif tweet_id and handle:
                return f"https://x.com/{handle}/status/{tweet_id}"
        elif action == "delete_post":
            if ref_handle and ref_tweet_id:
                return f"https://x.com/{ref_handle}/status/{ref_tweet_id}"
            elif tweet_id and handle:
                return f"https://x.com/{handle}/status/{tweet_id}"
        elif action in ("follow", "unfollow"):
            t_handle = message.get("unfollow_target", {}).get("handle")
            if t_handle:
                return f"https://x.com/{t_handle}"
        elif action in ("photo", "description", "name", "banner"):
            if handle:
                return f"https://x.com/{handle}"
        return ""

    def _compute_link_preview_options(self, message: dict, handle: str, action: str) -> dict:
        """根据消息数据计算 TG link_preview_options（预览链接 + 是否禁用）。

        该方法可在 TG_FAST 和 TG_UPDATE 中复用，确保 cp=1 编辑时
        能用完整 reference 数据重新计算出正确的预览链接。
        """
        content = message.get("content", {}) or {}
        reference = message.get("reference") or {}
        all_media = (content.get("media") or []) + (reference.get("media") or [])

        has_video = any(m.get("type") == "video" for m in all_media)
        first_photo_url = next(
            (m.get("url") for m in all_media
             if m.get("type") in ("photo", "image", "thumbnail") and m.get("url")),
            None,
        )
        photo_count = sum(
            1 for m in all_media
            if m.get("type") in ("photo", "image", "thumbnail") and m.get("url")
        )

        preview_url = None
        disable_preview = False

        from . import config
        if action == "banner":
            banner_change = message.get("banner_change") or {}
            preview_url = banner_change.get("after") or banner_change.get("before")
        elif handle and handle.lower() in config.BINANCE_SQUARE_HANDLES:
            preview_url = first_photo_url or next(
                (m.get("url") for m in all_media if m.get("url")), None
            )
            if not preview_url:
                disable_preview = True
        elif not has_video and photo_count == 1 and first_photo_url:
            preview_url = first_photo_url
        else:
            if action in ("follow", "unfollow"):
                t_handle = message.get("unfollow_target", {}).get("handle")
                if t_handle:
                    preview_url = f"https://vxtwitter.com/{t_handle}"
            elif action == "repost":
                ref_handle = reference.get("author_handle")
                ref_tweet_id = reference.get("tweet_id")
                if ref_handle and ref_tweet_id:
                    preview_url = f"https://fxtwitter.com/{ref_handle}/status/{ref_tweet_id}"
                elif message.get("tweet_id") and handle:
                    preview_url = f"https://fxtwitter.com/{handle}/status/{message.get('tweet_id')}"
            elif action in ("reply", "quote"):
                ref_handle = reference.get("author_handle")
                ref_tweet_id = reference.get("tweet_id")
                has_content_media = len(content.get("media") or []) > 0
                if has_content_media and message.get("tweet_id") and handle:
                    preview_url = f"https://fxtwitter.com/{handle}/status/{message.get('tweet_id')}"
                elif ref_handle and ref_tweet_id:
                    preview_url = f"https://fxtwitter.com/{ref_handle}/status/{ref_tweet_id}"
                else:
                    tweet_id = message.get("tweet_id", "")
                    if tweet_id and handle:
                        preview_url = f"https://fxtwitter.com/{handle}/status/{tweet_id}"
            elif action == "delete_post":
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
            else:
                if handle:
                    preview_url = f"https://vxtwitter.com/{handle}"

        lpo = {"is_disabled": disable_preview, "prefer_large_media": True}
        if preview_url:
            lpo["url"] = preview_url
        return lpo

    def _format_message(self, msg: dict, include_text: bool = True) -> str:
        """将标准化 JSON 组装为 TG HTML 头部。"""
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
            "banner": "🖼️ 更换横幅",
            "description": "⇧ 简介更新",
            "name": "📛 更改昵称",
            "pin": "📌 置顶推文",
            "unpin": "📍 取消置顶",
        }
        action_text = action_map.get(action, f"❓ {action}")

        lines = []
        author_link = f'👤 <a href="https://x.com/{handle}">{author_name} @{handle}</a>{author_followers}'

        # ──── 关注/取关 ────
        if action in ("follow", "unfollow") and unfollow_target:
            lines.append(f"<b>{action_text}</b>")
            lines.append(author_link)
            t_handle = unfollow_target.get("handle", "?")
            t_name = self._escape_html(unfollow_target.get("name") or t_handle)
            t_followers = self._format_followers(unfollow_target.get("followers"))
            t_link = f'<a href="https://x.com/{t_handle}">{t_name} @{t_handle}</a>{t_followers}'
            prefix = "✅ 关注了" if action == "follow" else "❌ 取关了"
            lines.append(f"{prefix} {t_link}")
            return "\n".join(lines)

        # ──── 其他动作 ────
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

        # ──── delete_post ────
        if action == "delete_post" and msg.get("original_action"):
            orig_label = action_map.get(msg.get("original_action"), msg.get("original_action"))
            lines.append(f"  ↳ 原类型: {orig_label}")

        # ──── photo ────
        if action == "photo":
            avatar_change = msg.get("avatar_change")
            if avatar_change:
                b = avatar_change.get("before", "")
                a = avatar_change.get("after", "")
                lines.append("")
                if b:
                    lines.append(f'🅰️ <a href="{b}">旧头像</a>')
                if a:
                    lines.append(f'🅱️ <a href="{a}">新头像</a>')

        # ──── description ────
        if action == "description":
            bio_change = msg.get("bio_change")
            if bio_change:
                lines.append("\n<b>旧简介:</b>")
                lines.append(self._escape_html(bio_change.get("before", "")))
                lines.append("\n<b>新简介:</b>")
                lines.append(self._escape_html(bio_change.get("after", "")))
        elif action == "banner":
            banner_change = msg.get("banner_change")
            if banner_change:
                before = banner_change.get("before", "")
                after = banner_change.get("after", "")
                lines.append("")
                if before:
                    lines.append(f'🅰️ <a href="{before}">旧横幅</a>')
                if after:
                    lines.append(f'🅱️ <a href="{after}">新横幅</a>')
        else:
            if include_text:
                content = msg.get("content") or {}
                text = content.get("text")
                if text:
                    if len(text) > 800:
                        text = text[:800] + "...\n[⬇️ 正文过长已截断]"
                    lines.append("")
                    lines.append(self._wrap_blockquote(self._escape_html(text), len(text)))

                # 展示 reference.text（被回复/引用/转推/删帖的原文），用 blockquote 区分
                reference = msg.get("reference") or {}
                ref_text = reference.get("text")
                if ref_text:
                    if len(ref_text) > 500:
                        ref_text = ref_text[:500] + "...\n[⬇️ 原推过长已截断]"
                    lines.append("")
                    lines.append(self._wrap_blockquote(f"💬 原推：\n{self._escape_html(ref_text)}", len(ref_text)))

        return "\n".join(lines)

    async def _send_api(self, endpoint: str, payload: dict) -> dict | None:
        """统一调用 TG API，内置 429 自动退避。返回响应 dict 或 None。"""
        try:
            async with self._session.post(
                f"{self.api_base}/{endpoint}", json=payload
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                if resp.status == 429:
                    data = await resp.json()
                    retry_after = data.get("parameters", {}).get("retry_after", 5)
                    logger.warning(f"📱 TG 被限流，{retry_after}s 后重试")
                    await asyncio.sleep(retry_after)
                    async with self._session.post(
                        f"{self.api_base}/{endpoint}", json=payload
                    ) as retry_resp:
                        if retry_resp.status == 200:
                            return await retry_resp.json()
                        body = await retry_resp.text()
                        logger.error(f"📱 TG 重试仍失败 [{retry_resp.status}]: {body[:200]}")
                        return None
                body = await resp.text()
                logger.error(f"📱 TG 推送失败 [{resp.status}]: {body[:200]}")
                return None
        except asyncio.TimeoutError:
            logger.error("📱 TG 推送超时 (15s)")
            return None
        except aiohttp.ClientError as e:
            logger.error(f"📱 TG 推送网络异常: {e}")
            return None
        except Exception as e:
            logger.error(f"📱 TG 推送未知异常: {e}")
            return None

    async def send_summary(self, target_channel_id: str, text: str) -> bool:
        """发送频道定时摘要。"""
        if not self._session or not target_channel_id or not text:
            return False

        text_to_send = text
        if len(text_to_send) > 3900:
            text_to_send = text_to_send[:3900] + "\n\n[摘要过长，内容已截断]"

        payload = {
            "chat_id": target_channel_id,
            "text": text_to_send,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        result = await self._send_api("sendMessage", payload)
        ok = bool(result and result.get("ok"))
        if ok:
            logger.info(f"🧾 TG 频道摘要推送成功 -> {target_channel_id}")
            message_id = (result.get("result") or {}).get("message_id")
            if message_id:
                await self._pin_summary(target_channel_id, message_id)
        return ok

    async def _pin_summary(self, target_channel_id: str, message_id: int) -> None:
        """置顶最新频道摘要；权限不足时不影响摘要发送结果。"""
        payload = {
            "chat_id": target_channel_id,
            "message_id": message_id,
            "disable_notification": True,
        }
        result = await self._send_api("pinChatMessage", payload)
        if result and result.get("ok"):
            logger.info(f"📌 TG 频道摘要已置顶 -> {target_channel_id} #{message_id}")
        else:
            logger.warning(f"📌 TG 频道摘要置顶失败 -> {target_channel_id} #{message_id}")

    async def _translate_and_edit(
        self,
        message_id: int,
        header_no_text: str,
        footer: str,
        message: dict,
        translated_dict: dict[str, str],
        target_channel_id: str,
        link_preview_options: dict | None = None,
        current_text: str | None = None,
        current_link_preview_options: dict | None = None,
    ) -> None:
        """使用预翻译结果编辑已发送的 TG 消息，替换英文正文为中文。"""
        content = message.get("content", {}) or {}
        reference = message.get("reference") or {}
        bio_change = message.get("bio_change") or {}
        text_parts = {}
        if _should_run_text_enrichment(message):
            if content.get("text"):
                text_parts["content"] = content["text"]
            if reference.get("text"):
                text_parts["reference"] = reference["text"]
            if bio_change.get("after"):
                text_parts["bio"] = bio_change["after"]

        # 获取翻译后的文本（如果返回 dict 中缺失，则 fallback 到原文）
        main_text = translated_dict.get("content") or text_parts.get("content", "")
        ref_text = translated_dict.get("reference") or text_parts.get("reference", "")
        bio_text = translated_dict.get("bio") or text_parts.get("bio", "")

        # 提取分析字段（analyzer 返回时会包含 category / summary）
        category = translated_dict.get("category", "")
        summary = translated_dict.get("summary", "")

        # 判断内容是否真的有改变（分析结果也算改变）。如果没有翻译差异，
        # 仍然刷新为 cp=1 完整版，避免 TG_FAST 快照缺少 reply/quote 原文。
        has_analysis = bool(category or summary)
        if (not has_analysis and
            main_text == text_parts.get("content", "") and 
            ref_text == text_parts.get("reference", "") and 
            bio_text == text_parts.get("bio", "")):
            base_text = f"{self._format_message(message)}\n\n{footer}"
            if (
                current_text
                and base_text[:4096] == current_text[:4096]
                and (link_preview_options or {}) == (current_link_preview_options or {})
            ):
                logger.info(f"🌐 翻译结果与原文相同，完整版无需刷新: {target_channel_id}")
                return

            payload = {
                "chat_id": target_channel_id,
                "message_id": message_id,
                "text": base_text[:4096],
                "parse_mode": "HTML",
            }
            if link_preview_options:
                payload["link_preview_options"] = link_preview_options

            result = await self._send_api("editMessageText", payload)
            handle = message.get("author", {}).get("handle", "?")
            if result and result.get("ok"):
                logger.info(f"📱 TG 完整版刷新成功: @{handle} -> {target_channel_id}")
            else:
                logger.warning(f"📱 TG 完整版刷新失败: @{handle} -> {target_channel_id}")
            return

        def format_part(translated: str, original: str, is_ref: bool = False) -> str:
            limit = 500 if is_ref else 800
            if len(translated) > limit: 
                translated = translated[:limit] + "...\n[⬇️ 译文过长已截断]"
            escaped = self._escape_html(translated)
            
            # 如果原文较短（<=80字符）且有实际翻译，附加斜体原文做对比
            if original and len(original) <= 80 and original.strip() != translated.strip():
                # 排查纯表情或纯标点：要求必须包含至少一个字母或数字
                if any(c.isalpha() or c.isdigit() for c in original):
                    # 为了美观，去掉末尾的回车并包裹在括号斜体中
                    orig_clean = original.strip().replace('\n', ' ')
                    escaped += f"\n(<i>{self._escape_html(orig_clean)}</i>)"
            return escaped

        # ──── 组装分析区块（置顶） ────
        analysis_block = ""
        if has_analysis:
            analysis_lines = []
            if category:
                analysis_lines.append(f"🏷️ 赛道: <b>{self._escape_html(category)}</b>")
            if summary:
                analysis_lines.append(f"📋 摘要: {self._escape_html(summary)}")
            analysis_content = "\n".join(analysis_lines)
            analysis_block = f"<blockquote>{analysis_content}</blockquote>\n\n"

        translated_html_parts = []
        if main_text or bio_text:
            t_text = main_text if main_text else bio_text
            o_text = text_parts.get("content", "") if main_text else text_parts.get("bio", "")
            translated_html_parts.append(self._wrap_blockquote(format_part(t_text, o_text, is_ref=False), len(t_text)))
        if ref_text:
            o_ref = text_parts.get("reference", "")
            escaped_ref = format_part(ref_text, o_ref, is_ref=True)
            translated_html_parts.append(self._wrap_blockquote(f"💬 原推翻译：\n{escaped_ref}", len(ref_text)))

        translated_html = "\n\n".join(translated_html_parts)
        
        separator = "—— 🌐 中文翻译 ——\n" if not has_analysis else "—— 🧠 AI 分析 + 翻译 ——\n"
        current_header_no_text = self._format_message(message, include_text=False)
        new_text = f"{current_header_no_text}\n\n{separator}{analysis_block}{translated_html}\n\n{footer}"

        handle = message.get("author", {}).get("handle", "?")

        payload = {
            "chat_id": target_channel_id,
            "message_id": message_id,
            "text": new_text[:4096],
            "parse_mode": "HTML",
        }
        # 保持与 sendMessage 一致的预览设置，防止编辑时卡片丢失
        if link_preview_options:
            payload["link_preview_options"] = link_preview_options

        result = await self._send_api("editMessageText", payload)

        if result and result.get("ok"):
            log_tag = "🧠 TG 分析+翻译" if has_analysis else "🌐 TG 翻译"
            logger.info(f"{log_tag}追加成功: @{handle} -> {target_channel_id}")
        else:
            logger.warning(f"🌐 TG 翻译追加失败: @{handle} -> {target_channel_id}")

    def _build_footer(self, message: dict, handle: str, action: str) -> str:
        tz_cst = timezone(timedelta(hours=8))
        ts = message.get("timestamp", 0)
        tweet_time = datetime.fromtimestamp(ts, tz=tz_cst).strftime("%Y-%m-%d %H:%M:%S") if ts else "未知"
        footer = f"🕒 推文时间: {tweet_time}"

        tweet_url = self._build_tweet_url(message, handle, action)
        if tweet_url:
            footer += f"\n🔗 <a href=\"{tweet_url}\">查看原文</a>"
        return footer

    async def _send_media_change_group(
        self,
        message: dict,
        handle: str,
        target_channel_id: str,
        time_log_str: str,
        change_key: str,
        success_label: str,
    ) -> bool:
        change = message.get(change_key) or {}
        before_url = change.get("before", "")
        after_url = change.get("after", "")
        if not before_url or not after_url:
            return False

        caption = self._format_message(message)[:1024]
        media = json.dumps([
            {"type": "photo", "media": before_url, "caption": caption, "parse_mode": "HTML"},
            {"type": "photo", "media": after_url},
        ])
        payload = {"chat_id": target_channel_id, "media": media}
        result = await self._send_api("sendMediaGroup", payload)
        if result and result.get("ok"):
            logger.info(f"📱 TG {success_label}推送成功: @{handle} -> {target_channel_id} | {time_log_str}")
            if self.storage:
                try:
                    resp_result = result.get("result")
                    msg_id = ""
                    if isinstance(resp_result, list) and resp_result:
                        msg_id = resp_result[0].get("message_id", "")
                    self.storage.record_delivery_background(
                        message,
                        platform="telegram",
                        target_id=target_channel_id,
                        target_label=target_channel_id,
                        external_message_id=msg_id,
                    )
                except Exception as e:
                    logger.debug(f"🗄️ TG delivery 记录失败: {e}")
            return True
        return False

    async def _distribute_to_channel(self, message: dict, handle: str, action: str, target_channel_id: str, time_log_str: str) -> dict | None:
        """推送原文到单个频道，返回推送上下文（含 msg_id）供后续翻译编辑使用。"""
        _diag_log(message, f"TG 准备发送 channel={target_channel_id} action={action}")
        # ──── photo 动作：由于 FxTwitter 无法展示换头像前后的两张图，需要保留 sendMediaGroup ────
        if action == "photo":
            if await self._send_media_change_group(
                message, handle, target_channel_id, time_log_str, "avatar_change", "头像变更"
            ):
                _diag_log(message, f"TG media group 已发送 channel={target_channel_id} action=photo")
                return None  # photo 动作不需要后续翻译编辑

        # ──── banner 动作：展示横幅前后对比图 ────
        if action == "banner":
            if await self._send_media_change_group(
                message, handle, target_channel_id, time_log_str, "banner_change", "横幅变更"
            ):
                _diag_log(message, f"TG media group 已发送 channel={target_channel_id} action=banner")
                return None  # banner 动作不需要后续翻译编辑

        # ──── 计算时间尾部 + 帖子链接 ────
        footer = self._build_footer(message, handle, action)

        # ──── 头部与正文 ────
        header = self._format_message(message)
        initial_text = f"{header}\n\n{footer}"
        
        # ──── 动态计算预览链接 (统一由 _compute_link_preview_options 计算) ────
        link_preview_options = self._compute_link_preview_options(message, handle, action)

        payload = {
            "chat_id": target_channel_id,
            "text": initial_text[:4096],
            "parse_mode": "HTML",
            "link_preview_options": link_preview_options
        }
        
        result = await self._send_api("sendMessage", payload)
        
        if result and result.get("ok"):
            logger.info(f"📱 TG 极简推送成功: @{handle} -> {target_channel_id} | {time_log_str}")

            resp_result = result.get("result")
            msg_id = None
            if isinstance(resp_result, dict):
                msg_id = resp_result.get("message_id")
            elif isinstance(resp_result, list) and len(resp_result) > 0:
                msg_id = resp_result[0].get("message_id")

            if msg_id:
                if self.storage:
                    try:
                        self.storage.record_delivery_background(
                            message,
                            platform="telegram",
                            target_id=target_channel_id,
                            target_label=target_channel_id,
                            external_message_id=msg_id,
                        )
                    except Exception as e:
                        logger.debug(f"🗄️ TG delivery 记录失败: {e}")

                header_no_text = self._format_message(message, include_text=False)
                return {
                    "msg_id": msg_id,
                    "header_no_text": header_no_text,
                    "footer": footer,
                    "channel_id": target_channel_id,
                    "link_preview_options": link_preview_options,
                    "initial_text": initial_text[:4096],
                }
            _diag_log(message, f"TG sendMessage 成功但缺少 message_id channel={target_channel_id}", level="warning")
        else:
            _diag_log(message, f"TG sendMessage 失败 channel={target_channel_id}", level="warning")
        return None

    async def _pre_translate(self, message: dict) -> dict[str, str] | None:
        """翻译一次，供所有频道复用。
        
        优先 await Hub 层创建的共享分析 Task（与推送原文并发，不阻塞）；
        若无 Task 则走原 translator 纯翻译链路。
        """
        if not _should_run_text_enrichment(message):
            return None

        # 优先 await Hub 层创建的共享分析 Task
        analysis_task = message.get("_ai_analysis_task")
        if analysis_task is not None:
            return await analysis_task

        content = message.get("content", {}) or {}
        reference = message.get("reference") or {}
        bio_change = message.get("bio_change") or {}
        text_parts = {}
        if _should_run_text_enrichment(message):
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

    async def _send_filtered_track_channels(
        self,
        message: dict,
        handle: str,
        action: str,
        h_lower: str,
        filtered_channels: list[str],
        handle_filter_map: dict[str, list[str]],
        translated_dict: dict[str, str],
        time_log_str: str,
    ) -> None:
        """AI category 命中赛道过滤后，补发对应 TG 频道并立即编辑为分析版。"""
        if not filtered_channels:
            return

        category = translated_dict.get("category", "")
        passed_filtered = []
        for cid in filtered_channels:
            kws = handle_filter_map.get(cid, [])
            sent_key = (message.get("_internal_id") or message.get("tweet_id") or "", cid)
            if sent_key[0] and sent_key in self._filtered_sent_keys:
                _diag_log(message, f"TG赛道过滤跳过: 频道 {cid} 已补发过")
                logger.debug(f"📱 TG赛道过滤: @{h_lower} 已推送过频道 {cid}，跳过重复补发")
                continue
            if not category:
                _diag_log(message, f"TG赛道过滤跳过: 无 AI category channel={cid}")
                logger.debug(f"📱 TG赛道过滤: @{h_lower} 无 AI category，跳过频道 {cid}")
                continue
            if not self._track_matches(category, kws):
                _diag_log(message, f"TG赛道过滤跳过: category='{category}' 不含 {kws} channel={cid}")
                logger.debug(f"📱 TG赛道过滤: @{h_lower} category='{category}' 不含 {kws}，跳过频道 {cid}")
                continue
            if sent_key[0]:
                self._filtered_sent_keys[sent_key] = None
            passed_filtered.append(cid)

        if not passed_filtered:
            _diag_log(message, f"TG赛道过滤: 无命中频道 filtered={filtered_channels}")
            return

        filtered_push_tasks = [
            self._distribute_to_channel(message, handle, action, cid, time_log_str)
            for cid in passed_filtered
        ]
        filtered_results = await asyncio.gather(*filtered_push_tasks, return_exceptions=True)

        filtered_edit_tasks = []
        for cid, r in zip(passed_filtered, filtered_results):
            sent_key = (message.get("_internal_id") or message.get("tweet_id") or "", cid)
            if isinstance(r, dict) and "msg_id" in r:
                filtered_edit_tasks.append(
                    self._translate_and_edit(
                        r["msg_id"], r["header_no_text"], r["footer"],
                        message, translated_dict, r["channel_id"], r["link_preview_options"],
                        r.get("initial_text"), r.get("link_preview_options")
                    )
                )
            elif isinstance(r, Exception):
                if sent_key[0]:
                    self._filtered_sent_keys.pop(sent_key, None)
                logger.warning(f"📱 TG赛道过滤推送异常: @{h_lower} -> {cid}: {r}")
            else:
                if sent_key[0]:
                    self._filtered_sent_keys.pop(sent_key, None)

        if len(self._filtered_sent_keys) > 2000:
            self._filtered_sent_keys = dict(list(self._filtered_sent_keys.items())[-1000:])
        if filtered_edit_tasks:
            await asyncio.gather(*filtered_edit_tasks, return_exceptions=True)

    async def distribute(self, message: dict) -> None:
        if not self._session:
            _diag_log(message, "TG 跳过: session 未启动", level="warning")
            return
        if not self._should_forward(message):
            _diag_log(message, f"TG 跳过: 不在白名单 filter={self.filter_handles}", level="warning")
            return

        handle = message.get("author", {}).get("handle", "?")
        action = message.get("action", "")
        target = message.get("_dispatch_target")
        internal_id = message.get("_internal_id")
        _diag_log(message, f"TG 进入分发 target={target} action={action} internal_id={internal_id or ''}")

        # 核心：动态路由
        h_lower = handle.lower()
        target_channel_ids = self._target_channel_ids(h_lower)

        if not target_channel_ids:
            _diag_log(message, "TG 跳过: 未命中路由且 ALL 群组未启用", level="warning")
            return

        # ── 按赛道过滤规则拆分频道 ──
        # normal_channels: 无过滤，走正常 TG_FAST + TG_UPDATE 流程
        # filtered_channels: 有赛道限制，等 TG_UPDATE 拿到 AI category 后发送
        from . import config as _cfg
        _handle_filter_map = _cfg.TG_CHANNEL_TRACK_FILTER.get(h_lower, {})
        normal_channels = [cid for cid in target_channel_ids if cid not in _handle_filter_map]
        filtered_channels = [cid for cid in target_channel_ids if cid in _handle_filter_map]
        _diag_log(
            message,
            f"TG 路由: targets={target_channel_ids} normal={normal_channels} "
            f"filtered={filtered_channels} track_filter={_handle_filter_map}"
        )

        tz_cst = timezone(timedelta(hours=8))
        ts = message.get("timestamp", 0)
        tweet_time = datetime.fromtimestamp(ts, tz=tz_cst).strftime("%Y-%m-%d %H:%M:%S") if ts else "未知"
        push_time = datetime.now(tz=tz_cst).strftime("%Y-%m-%d %H:%M:%S")
        time_log_str = f"| 🕐 推文时间: {tweet_time} 📡 推送时间: {push_time}"

        if target == "TG_UPDATE":
            if not _should_run_text_enrichment(message):
                _diag_log(message, f"TG_UPDATE 跳过: 非文本增强动作 action={action}")
                logger.debug(f"📱 TG_UPDATE 跳过非正文动作: {action}")
                return

            push_contexts = []
            history_future = None
            if internal_id and internal_id in self._msg_history:
                history = self._msg_history[internal_id]
                if isinstance(history, asyncio.Future):
                    history_future = history
                else:
                    push_contexts = history
            elif not filtered_channels:
                _diag_log(message, f"TG_UPDATE 跳过: 找不到 _msg_history internal_id={internal_id}", level="warning")
                logger.warning(f"📱 TG_UPDATE 找不到 _msg_history: {internal_id[:20] if internal_id else 'None'}")
                return

            translate_future = asyncio.create_task(self._pre_translate(message))

            if history_future is not None:
                try:
                    push_contexts = await asyncio.wait_for(asyncio.shield(history_future), timeout=20)
                except asyncio.TimeoutError:
                    _diag_log(message, f"TG_UPDATE 跳过编辑: 等待普通频道推送结果超时 internal_id={internal_id}", level="warning")
                    logger.warning(f"📱 TG_UPDATE 等待普通频道推送结果超时，跳过编辑: {internal_id[:20] if internal_id else 'None'}")
                    push_contexts = []

            if not push_contexts:
                if not filtered_channels:
                    _diag_log(message, "TG_UPDATE 跳过编辑: push_contexts 为空", level="warning")
                    logger.warning("📱 TG_UPDATE push_contexts 为空，跳过编辑")
                    translate_future.cancel()
                    with suppress(asyncio.CancelledError):
                        await translate_future
                    return

            # 使用 cp=1 完整数据重新计算预览链接（TG_FAST cp=0 可能缺少 reference）
            updated_lpo = self._compute_link_preview_options(message, handle, action)
            updated_footer = self._build_footer(message, handle, action)
            updated_base_text = f"{self._format_message(message)}\n\n{updated_footer}"[:4096]

            refresh_tasks = []
            for r in push_contexts:
                refresh_tasks.append(
                    self._translate_and_edit(
                        r["msg_id"], r["header_no_text"], updated_footer,
                        message, {}, r["channel_id"], updated_lpo,
                        r.get("initial_text"), r.get("link_preview_options")
                    )
                )
            if refresh_tasks:
                await asyncio.gather(*refresh_tasks, return_exceptions=True)

            try:
                translate_result = await translate_future
            except Exception as e:
                _diag_log(message, f"TG_UPDATE 翻译/分析异常，已刷新完整版: {e}", level="warning")
                logger.warning(f"📱 TG_UPDATE 翻译/分析异常，已刷新完整版: {e}")
                return
            if not isinstance(translate_result, dict) or not translate_result:
                _diag_log(message, "TG_UPDATE 翻译/分析结果为空，已刷新完整版")
                return

            translated_dict = translate_result

            # 过滤频道不依赖 TG_FAST 的普通频道发送结果。拿到 AI category 后按需补发。
            await self._send_filtered_track_channels(
                message,
                handle,
                action,
                h_lower,
                filtered_channels,
                _handle_filter_map,
                translated_dict,
                time_log_str,
            )

            edit_tasks = []
            for r in push_contexts:
                edit_tasks.append(
                    self._translate_and_edit(
                        r["msg_id"], r["header_no_text"], updated_footer,
                        message, translated_dict, r["channel_id"], updated_lpo,
                        updated_base_text, updated_lpo
                    )
                )
            if edit_tasks:
                await asyncio.gather(*edit_tasks, return_exceptions=True)
            return

        if target == "TG_FAST":
            # 立即创建 Future，让 TG_UPDATE 可以 await 等待推送完成
            if internal_id and internal_id not in self._msg_history:
                self._msg_history[internal_id] = asyncio.get_event_loop().create_future()
                # 滚动清理
                if len(self._msg_history) > 1000:
                    oldest_key = next(iter(self._msg_history))
                    old_future = self._msg_history.pop(oldest_key)
                    if not old_future.done():
                        old_future.set_result([])

            # TG_FAST 只推无过滤频道（过滤频道等 TG_UPDATE 拿到 AI category 再决定）
            push_tasks = [
                self._distribute_to_channel(message, handle, action, cid, time_log_str)
                for cid in normal_channels
            ]
            try:
                all_results = await asyncio.gather(*push_tasks, return_exceptions=True) if push_tasks else []
                valid_push_contexts = [r for r in all_results if isinstance(r, dict) and "msg_id" in r]
                exceptions = [r for r in all_results if isinstance(r, Exception)]
                fail_count = len(all_results) - len(valid_push_contexts) - len(exceptions)
                _diag_log(
                    message,
                    f"TG_FAST 完成: normal_channels={normal_channels} "
                    f"success={len(valid_push_contexts)} fail={fail_count} exceptions={len(exceptions)}"
                )
            except Exception as e:
                _diag_log(message, f"TG_FAST 异常: {e}", level="error")
                valid_push_contexts = []

            # 设置 Future 结果，解除 TG_UPDATE 的 await 阻塞
            if internal_id and internal_id in self._msg_history:
                future = self._msg_history[internal_id]
                if not future.done():
                    future.set_result(valid_push_contexts)
            return

        # ──── 阶段 1：推送原文（normal_channels）+ 翻译 并发执行 ────
        # 推送任务列表（normal_channels 直接推）
        push_tasks = [
            self._distribute_to_channel(message, handle, action, cid, time_log_str)
            for cid in normal_channels
        ]
        # 翻译任务（只调一次 DeepSeek）
        translate_task = self._pre_translate(message)

        # 并发：所有频道推送 + DeepSeek 翻译 同时执行
        all_results = await asyncio.gather(
            *push_tasks, translate_task if translate_task else asyncio.sleep(0), return_exceptions=True
        )

        # 拆分结果：前 N 个是推送结果，最后一个是翻译结果
        push_results = all_results[:-1]
        translate_result = all_results[-1]

        valid_push_contexts = []
        for r in push_results:
            if isinstance(r, dict) and "msg_id" in r:
                valid_push_contexts.append(r)
        push_exceptions = [r for r in push_results if isinstance(r, Exception)]
        _diag_log(
            message,
            f"TG 推送完成: normal_channels={normal_channels} "
            f"success={len(valid_push_contexts)} exceptions={len(push_exceptions)} "
            f"translate_result={'exception' if isinstance(translate_result, Exception) else bool(translate_result)}"
        )

        if internal_id and valid_push_contexts:
            self._msg_history[internal_id] = valid_push_contexts
            if len(self._msg_history) > 1000:
                self._msg_history.pop(next(iter(self._msg_history)))

        # ──── 阶段 2：翻译完成后，批量编辑所有频道 ────
        if isinstance(translate_result, Exception):
            logger.error(f"🌐 翻译异常: {translate_result}")
            return
        if not translate_result:
            return  # 无需翻译或翻译失败

        translated_dict = translate_result
        edit_tasks = []
        for r in valid_push_contexts:
            edit_tasks.append(
                self._translate_and_edit(
                    r["msg_id"], r["header_no_text"], r["footer"],
                    message, translated_dict, r["channel_id"], r["link_preview_options"],
                    r.get("initial_text"), r.get("link_preview_options")
                )
            )

        if edit_tasks:
            await asyncio.gather(*edit_tasks, return_exceptions=True)

        # ──── 阶段 3：赛道过滤频道 — 拿到 AI category 后按需发送 ────
        await self._send_filtered_track_channels(
            message,
            handle,
            action,
            h_lower,
            filtered_channels,
            _handle_filter_map,
            translated_dict,
            time_log_str,
        )


# ---------------------------------------------------------------------------
#  分发器集线器
# ---------------------------------------------------------------------------
class DistributorHub:
    """管理所有分发器的生命周期与消息扇出。"""

    def __init__(self, distributors: list[BaseDistributor] | None = None, storage=None):
        self.distributors = distributors or []
        self.storage = storage
        self._shared_translation_tasks = {}

    async def start_all(self) -> None:
        """依次启动所有分发器。"""
        for d in self.distributors:
            try:
                await d.start()
            except Exception as e:
                logger.error(f"❌ 分发器启动失败: {type(d).__name__} - {e}")

    async def stop_all(self) -> None:
        """依次停止所有分发器。"""
        for d in self.distributors:
            try:
                await d.stop()
            except Exception as e:
                logger.error(f"❌ 分发器停止失败: {type(d).__name__} - {e}")

    async def publish(self, message: dict) -> None:
        """将消息广播到所有分发器（并发执行，单个失败不影响其余）。
        
        对 AI_ANALYZE_HANDLES 中的 handle，创建共享的分析 Task（不阻塞），
        注入 message['_ai_analysis_task']，供 Telegram 分发器异步等待。
        这样原文推送不会被分析阻塞，保持“先发后改”的低延迟策略。
        """
        target = message.get("_dispatch_target", "TG_FAST")

        if self.storage:
            try:
                self.storage.record_message_background(message)
            except Exception as e:
                logger.debug(f"🗄️ message 记录失败: {e}")

        # ──── 创建共享分析 Task（不 await，与推送原文并发） ────
        from . import config as cfg
        handle = message.get("author", {}).get("handle", "").lower()
        if (
            handle in cfg.AI_ANALYZE_HANDLES
            and target != "TG_FAST"
            and _should_run_text_enrichment(message)
        ):
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

            if text_parts:
                content_hash = hash(json.dumps(text_parts, sort_keys=True))
                if content_hash in self._shared_translation_tasks:
                    message["_ai_analysis_task"] = self._shared_translation_tasks[content_hash]
                else:
                    from .analyzer import analyze_tweet
                    logger.info(f"🧠 Hub: 创建 @{handle} 的共享分析 Task ({target})")
                    # 创建 Task 但不 await，多个分发器可以安全地 await 同一个 Task
                    task = asyncio.create_task(analyze_tweet(text_parts, handle=handle))
                    self._shared_translation_tasks[content_hash] = task
                    # 确保执行完成后从共享字典中移除，避免内存泄漏
                    task.add_done_callback(lambda t: self._shared_translation_tasks.pop(content_hash, None))
                    message["_ai_analysis_task"] = task

        tasks = []
        task_distributors = []  # 记录实际参与分发的 distributor，与 tasks 一一对应
        for distributor in self.distributors:
            is_tg = isinstance(distributor, TelegramDistributor)
            if target == "TG_FAST":
                if is_tg:
                    tasks.append(distributor.distribute(message))
                    task_distributors.append(distributor)
            elif target == "TG_UPDATE":
                if is_tg:
                    tasks.append(distributor.distribute(message))
                    task_distributors.append(distributor)
        _diag_log(
            message,
            f"Hub 任务选择: target={target} distributors="
            f"{[type(d).__name__ for d in task_distributors]}"
        )
        if not tasks:
            _diag_log(message, f"Hub 跳过: target={target} 无可执行 distributor", level="warning")
            return
            
        results = await asyncio.gather(*tasks, return_exceptions=True)
        exception_count = sum(1 for result in results if isinstance(result, Exception))
        _diag_log(
            message,
            f"Hub 任务完成: target={target} distributors={len(task_distributors)} "
            f"exceptions={exception_count}"
        )
        for distributor, result in zip(task_distributors, results):
            if isinstance(result, Exception):
                _diag_log(
                    message,
                    f"Hub distributor 异常: {type(distributor).__name__} - {result}",
                    level="error",
                )
                logger.error(f"❌ 分发失败: {type(distributor).__name__} - {result}")
