import asyncio
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone, timedelta
from typing import Set

import aiohttp
import websockets
from loguru import logger
from websockets.server import WebSocketServerProtocol

from .translator import translate_text

class BaseDistributor:
    """分发器基类，所有通道必须继承并实现 distribute 方法。"""

    async def start(self) -> None:
        """启动分发器（子类可覆盖）。"""

    async def stop(self) -> None:
        """停止分发器（子类可覆盖）。"""

    async def distribute(self, message: dict) -> None:
        raise NotImplementedError


# ---------------------------------------------------------------------------
#  日志分发器
# ---------------------------------------------------------------------------
class LoggingDistributor(BaseDistributor):
    async def distribute(self, message: dict) -> None:
        logger.debug(f"📝 完整标准 JSON: {message}")


# ---------------------------------------------------------------------------
#  WebSocket 实时广播分发器
# ---------------------------------------------------------------------------
class WebSocketDistributor(BaseDistributor):
    def __init__(self, host: str, port: int, token: str, heartbeat_interval: int):
        self.host = host
        self.port = port
        self.token = token
        self.heartbeat_interval = heartbeat_interval
        self.clients: Set[WebSocketServerProtocol] = set()
        self.server = None

    async def start(self):
        """启动 WebSocket server"""
        self.server = await websockets.serve(
            self._handle_client,
            self.host,
            self.port,
            ping_interval=self.heartbeat_interval,
            ping_timeout=self.heartbeat_interval * 2,
        )
        logger.success(f"🌐 WebSocket 分发服务已启动: ws://{self.host}:{self.port}")

    async def stop(self):
        """关闭 WebSocket server"""
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            logger.info("🔌 WebSocket 分发服务已关闭")

    async def _handle_client(self, websocket: WebSocketServerProtocol):
        """处理单个客户端连接"""
        client_addr = f"{websocket.remote_address[0]}:{websocket.remote_address[1]}"

        try:
            # 等待客户端发送 token 鉴权
            auth_msg = await asyncio.wait_for(websocket.recv(), timeout=10.0)
            auth_data = json.loads(auth_msg)

            if auth_data.get("token") != self.token:
                await websocket.send(json.dumps({"error": "Invalid token"}))
                await websocket.close(1008, "Authentication failed")
                logger.warning(f"❌ 客户端 {client_addr} 鉴权失败")
                return

            # 鉴权成功，加入客户端集合
            self.clients.add(websocket)
            logger.success(f"✅ 客户端 {client_addr} 已连接 (当前在线: {len(self.clients)})")

            # 发送欢迎消息
            await websocket.send(json.dumps({"status": "connected", "message": "Authentication successful"}))

            # 保持连接，等待客户端断开
            try:
                async for _ in websocket:
                    pass  # 忽略客户端发来的消息，只做单向广播
            except websockets.exceptions.ConnectionClosed:
                pass

        except asyncio.TimeoutError:
            logger.warning(f"⏱️ 客户端 {client_addr} 鉴权超时")
        except json.JSONDecodeError:
            logger.warning(f"❌ 客户端 {client_addr} 发送的鉴权消息格式错误")
        except Exception as e:
            logger.error(f"❌ 处理客户端 {client_addr} 时发生错误: {e}")
        finally:
            self.clients.discard(websocket)
            logger.info(f"🔌 客户端 {client_addr} 已断开 (当前在线: {len(self.clients)})")

    async def distribute(self, message: dict) -> None:
        """广播消息给所有已连接客户端"""
        if not self.clients:
            return  # 无客户端时直接跳过

        message_json = json.dumps(message, ensure_ascii=False)
        disconnected_clients = set()

        for client in self.clients:
            try:
                await client.send(message_json)
            except websockets.exceptions.ConnectionClosed:
                disconnected_clients.add(client)
            except Exception as e:
                logger.error(f"❌ 向客户端 {client.remote_address} 发送消息失败: {e}")
                disconnected_clients.add(client)

        # 清理断开的客户端
        for client in disconnected_clients:
            self.clients.discard(client)

        if disconnected_clients:
            logger.info(f"🧹 已清理 {len(disconnected_clients)} 个断开的客户端 (当前在线: {len(self.clients)})")


# ---------------------------------------------------------------------------
#  Telegram 频道推送分发器
# ---------------------------------------------------------------------------
class TelegramDistributor(BaseDistributor):
    """通过 Telegram Bot API 将消息推送到指定频道。

    支持按 author.handle 白名单过滤；内置 429 Rate-Limit 自动退避重试。
    """

    def __init__(self, bot_token: str, default_channel_id: str, enable_default: bool = False, channel_map: dict[str, str] | None = None, filter_handles: list[str] | None = None):
        self.bot_token = bot_token
        self.default_channel_id = default_channel_id
        self.enable_default = enable_default
        self.channel_map = channel_map or {}
        self.filter_handles = [h.lower() for h in (filter_handles or [])]
        self.api_base = f"https://api.telegram.org/bot{bot_token}"
        self._session: aiohttp.ClientSession | None = None

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

    @staticmethod
    def _escape_html(text: str) -> str:
        """转义 HTML 特殊字符。"""
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def _format_followers(self, count: int | None) -> str:
        """格式化粉丝数为可读字符串。"""
        if not count:
            return ""
        if count >= 1_000_000:
            return f" · {count / 1_000_000:.1f}M 粉丝"
        if count >= 1_000:
            return f" · {count / 1_000:.1f}K 粉丝"
        return f" · {count} 粉丝"

    def _format_message(self, msg: dict) -> str:
        """将标准化 JSON 组装为 TG HTML 富文本消息。"""
        action = msg.get("action", "unknown")
        author = msg.get("author", {})
        content = msg.get("content", {})
        reference = msg.get("reference")
        tweet_id = msg.get("tweet_id", "")
        unfollow_target = msg.get("unfollow_target")
        original_action = msg.get("original_action")

        action_map = {
            "tweet": "📝 发布新推文",
            "repost": "🔄 转推",
            "reply": "💬 回复",
            "quote": "📌 引用推文",
            "follow": "➕ 新增关注",
            "unfollow": "👋 取消关注",
            "delete_post": "🗑️ 删除推文",
            "photo": "🖼️ 更换头像",
            "description": "⇧ 简介更新",
            "name": "📛 更改昵称",
        }
        action_text = action_map.get(action, f"❓ {action}")

        handle = author.get("handle", "unknown")
        name = author.get("name", handle)
        followers = author.get("followers")

        lines: list[str] = []
        lines.append(f"<b>{action_text}</b>")

        # delete_post 标注被删推文的原始类型
        if action == "delete_post" and original_action:
            orig_label = action_map.get(original_action, original_action)
            lines.append(f"  ↳ 原始类型: {orig_label}")

        lines.append("")

        # 作者信息
        lines.append(
            f'👤 <a href="https://x.com/{handle}">{self._escape_html(name)}</a>'
            f" <code>@{handle}</code>{self._format_followers(followers)}"
        )
        lines.append("")

        # ──── follow/unfollow 专用区块 ────
        if action in ("follow", "unfollow") and unfollow_target:
            t_handle = unfollow_target.get("handle", "?")
            t_name = unfollow_target.get("name", t_handle)
            t_bio = unfollow_target.get("bio", "")
            t_followers = unfollow_target.get("followers")

            action_icon = "✅ 关注了" if action == "follow" else "❌ 取关了"
            lines.append(
                f'{action_icon} <a href="https://x.com/{t_handle}">'
                f"{self._escape_html(t_name)}</a>"
                f" <code>@{t_handle}</code>{self._format_followers(t_followers)}"
            )
            if t_bio:
                lines.append(f"  ┃ {self._escape_html(t_bio[:200])}")
            lines.append("")
            return "\n".join(lines)

        # ──── photo 头像变更区块 ────
        avatar_change = msg.get("avatar_change")
        if action == "photo" and avatar_change:
            before_url = avatar_change.get("before", "")
            after_url = avatar_change.get("after", "")
            lines.append("旧头像 → 新头像")
            lines.append("")
            if before_url:
                lines.append(f'🅰️ <a href="{before_url}">旧头像</a>')
            if after_url:
                lines.append(f'🅱️ <a href="{after_url}">新头像</a>')
            return "\n".join(lines)

        # ──── description 简介更新区块 ────
        bio_change = msg.get("bio_change")
        if action == "description" and bio_change:
            before_bio = bio_change.get("before", "")
            after_bio = bio_change.get("after", "")
            
            import difflib
            diff_lines = list(difflib.ndiff(before_bio.splitlines(), after_bio.splitlines()))
            old_bio_lines = []
            for dline in diff_lines:
                if dline.startswith("- "):
                    old_bio_lines.append(f"<s>{self._escape_html(dline[2:])}</s>")
                elif dline.startswith("  "):
                    old_bio_lines.append(self._escape_html(dline[2:]))
            
            lines.append("旧简介：")
            if old_bio_lines:
                lines.append("\n".join(old_bio_lines))
            else:
                lines.append("无")
            lines.append("")
            
            lines.append("新简介：")
            lines.append(self._escape_html(after_bio))
            lines.append("")
            
            lines.append(f'🖼 <a href="https://x.com/{handle}">原文</a>')
            lines.append("")
            
            tz_cst = timezone(timedelta(hours=8))
            ts = msg.get("timestamp", 0)
            if ts:
                tweet_time = datetime.fromtimestamp(ts, tz=tz_cst).strftime("%Y-%m-%d %H:%M:%S")
            else:
                tweet_time = "未知"
            push_time = datetime.now(tz=tz_cst).strftime("%Y-%m-%d %H:%M:%S")
            lines.append(f"🕐 推文时间: {tweet_time}")
            lines.append(f"📡 推送时间: {push_time}")
            
            return "\n".join(lines)

        # ──── 正文 ────
        text = content.get("text", "")
        if text:
            lines.append(f"<blockquote>{self._escape_html(text)}</blockquote>")
            lines.append("")

        # ──── 引用/转推来源 ────
        if reference:
            ref_handle = reference.get("author_handle", "")
            ref_text = reference.get("text", "")
            ref_tweet_id = reference.get("tweet_id", "")
            ref_type_map = {
                "retweeted": "🔄 转推自",
                "replied_to": "💬 回复",
                "quoted": "📌 引用自",
                "deleted": "🗑️ 原引用",
                "referenced": "↩️ 关联",
            }
            ref_label = ref_type_map.get(reference.get("type", ""), "↩️ 关联")

            ref_link = f'<a href="https://x.com/{ref_handle}">@{ref_handle}</a>'
            if ref_tweet_id and ref_handle:
                ref_link = (
                    f'<a href="https://x.com/{ref_handle}/status/{ref_tweet_id}">'
                    f"@{ref_handle}</a>"
                )
            lines.append(f"┃ {ref_label} {ref_link}")
            if ref_text:
                lines.append(f"┃ {self._escape_html(ref_text[:280])}")

            # 引用推文的媒体（仅显示非图片，图片由 sendPhoto 嵌入）
            ref_media = reference.get("media", [])
            for m in ref_media:
                m_url = m.get("url", "")
                m_type = m.get("type", "media")
                if m_url and m_type not in ("thumbnail", "image"):
                    lines.append(f'┃ 📎 <a href="{m_url}">[{m_type}]</a>')
            lines.append("")

        # ──── 媒体附件（仅显示非图片，图片由 sendPhoto 嵌入） ────
        media_list = content.get("media", [])
        if media_list:
            for m in media_list:
                m_type = m.get("type", "media")
                m_url = m.get("url", "")
                if m_url and m_type not in ("thumbnail", "image"):
                    lines.append(f'📎 <a href="{m_url}">[{m_type}]</a>')
            if any(m.get("type") not in ("thumbnail", "image") and m.get("url") for m in media_list):
                lines.append("")

        # ──── 原推链接 ────
        if tweet_id and handle:
            tweet_url = f"https://x.com/{handle}/status/{tweet_id}"
            lines.append(f'🔗 <a href="{tweet_url}">查看原推</a>')

        # ──── 时间信息 ────
        tz_cst = timezone(timedelta(hours=8))
        ts = msg.get("timestamp", 0)
        if ts:
            tweet_time = datetime.fromtimestamp(ts, tz=tz_cst).strftime("%Y-%m-%d %H:%M:%S")
        else:
            tweet_time = "未知"
        push_time = datetime.now(tz=tz_cst).strftime("%Y-%m-%d %H:%M:%S")
        lines.append("")
        lines.append(f"🕐 推文时间: {tweet_time}")
        lines.append(f"📡 推送时间: {push_time}")

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

    async def _translate_and_edit(self, message_id: int, original_text: str,
                                  is_caption: bool, message: dict, target_channel_id: str) -> None:
        """异步翻译推文并编辑已发送的 TG 消息，追加中文翻译。"""
        # 收集所有需要翻译的文本内容
        content = message.get("content", {}) or {}
        reference = message.get("reference") or {}
        bio_change = message.get("bio_change") or {}
        text_parts = []
        if content.get("text"):
            text_parts.append(content["text"])
        if reference.get("text"):
            text_parts.append(reference["text"])
        if bio_change.get("after"):
            text_parts.append(bio_change["after"])

        if not text_parts:
            return

        combined = "\n---\n".join(text_parts)
        translated = await translate_text(combined)
        if not translated:
            return

        # 拼接翻译结果到原文本末尾
        separator = "\n\n—— 🌐 中文翻译 ——\n"
        new_text = original_text + separator + self._escape_html(translated)

        handle = message.get("author", {}).get("handle", "?")

        if is_caption:
            payload = {
                "chat_id": target_channel_id,
                "message_id": message_id,
                "caption": new_text[:1024],
                "parse_mode": "HTML",
            }
            result = await self._send_api("editMessageCaption", payload)
        else:
            payload = {
                "chat_id": target_channel_id,
                "message_id": message_id,
                "text": new_text[:4096],
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            }
            result = await self._send_api("editMessageText", payload)

        if result and result.get("ok"):
            logger.info(f"🌐 TG 翻译追加成功: @{handle}")
        else:
            logger.warning(f"🌐 TG 翻译追加失败: @{handle}")

    @staticmethod
    def _collect_image_urls(message: dict) -> list[str]:
        """从消息中收集所有 image 类型的 URL（content.media + reference.media）。"""
        urls: list[str] = []
        for source in (message.get("content", {}), message.get("reference") or {}):
            for m in source.get("media", []):
                if isinstance(m, dict) and m.get("type") == "image" and m.get("url"):
                    urls.append(m["url"])
        return urls

    async def _distribute_to_channel(self, message: dict, handle: str, action: str, target_channel_id: str, time_log_str: str) -> None:
        # ──── photo 动作：发送前后头像对比 ────
        if action == "photo":
            avatar_change = message.get("avatar_change") or {}
            before_url = avatar_change.get("before", "")
            after_url = avatar_change.get("after", "")

            if before_url and after_url:
                caption = self._format_message(message)[:1024]
                media = json.dumps([
                    {"type": "photo", "media": before_url, "caption": caption, "parse_mode": "HTML"},
                    {"type": "photo", "media": after_url},
                ])
                payload = {"chat_id": target_channel_id, "media": media}
                result = await self._send_api("sendMediaGroup", payload)
                if result and result.get("ok"):
                    logger.info(f"📱 TG 头像变更推送成功: @{handle} {time_log_str}")
                return

        # ──── 收集推文中的图片 ────
        image_urls = self._collect_image_urls(message)

        if image_urls:
            # 方案 2：稳定支持多图，发送原生图片媒体
            caption = self._format_message(message)[:1024] # 图片 caption 限制 1024
            
            if len(image_urls) == 1:
                payload = {
                    "chat_id": target_channel_id,
                    "photo": image_urls[0],
                    "caption": caption,
                    "parse_mode": "HTML"
                }
                result = await self._send_api("sendPhoto", payload)
            else:
                media_list = []
                # TG sendMediaGroup 限制最多 10 个媒体
                for i, url in enumerate(image_urls[:10]):
                    media_item = {"type": "photo", "media": url}
                    if i == 0:
                        media_item["caption"] = caption
                        media_item["parse_mode"] = "HTML"
                    media_list.append(media_item)
                
                payload = {
                    "chat_id": target_channel_id,
                    "media": json.dumps(media_list)
                }
                result = await self._send_api("sendMediaGroup", payload)
            is_caption = True
        else:
            caption = self._format_message(message)[:4096]
            # 无图片时，仍然尝试将链接加入预览（如果有其他媒体等情况）
            payload = {
                "chat_id": target_channel_id,
                "text": caption,
                "parse_mode": "HTML",
                "link_preview_options": {"is_disabled": False, "prefer_large_media": True}
            }
            result = await self._send_api("sendMessage", payload)
            is_caption = False
        
        if result and result.get("ok"):
            img_info = f" (带{len(image_urls)}图)" if image_urls else ""
            logger.info(f"📱 TG 推送成功{img_info}: @{handle} {time_log_str}")

        # ──── 异步翻译 + 编辑追加 ────
        if result and result.get("ok"):
            resp_result = result.get("result")
            msg_id = None
            if isinstance(resp_result, dict):
                msg_id = resp_result.get("message_id")
            elif isinstance(resp_result, list) and len(resp_result) > 0:
                msg_id = resp_result[0].get("message_id")

            if msg_id:
                asyncio.create_task(
                    self._translate_and_edit(msg_id, caption, is_caption, message, target_channel_id)
                )

    async def distribute(self, message: dict) -> None:
        if not self._session:
            return
        if not self._should_forward(message):
            return

        handle = message.get("author", {}).get("handle", "?")
        action = message.get("action", "")

        # 核心：动态路由
        h_lower = handle.lower()
        target_channel_ids = self.channel_map.get(h_lower, [])
        if not target_channel_ids:
            if not self.enable_default:
                return  # 未在专用分组中，且大杂烩频道关闭，则丢弃
            target_channel_ids = [self.default_channel_id] if self.default_channel_id else []

        if not target_channel_ids:
            return

        tz_cst = timezone(timedelta(hours=8))
        ts = message.get("timestamp", 0)
        tweet_time = datetime.fromtimestamp(ts, tz=tz_cst).strftime("%Y-%m-%d %H:%M:%S") if ts else "未知"
        push_time = datetime.now(tz=tz_cst).strftime("%Y-%m-%d %H:%M:%S")
        time_log_str = f"| 🕐 推文时间: {tweet_time} 📡 推送时间: {push_time}"

        # 针对每个订阅该 handle 的频道并发推送
        tasks = [
            self._distribute_to_channel(message, handle, action, cid, time_log_str)
            for cid in target_channel_ids
        ]
        await asyncio.gather(*tasks, return_exceptions=True)


# ---------------------------------------------------------------------------
#  Webhook HTTP POST 分发器
# ---------------------------------------------------------------------------
class WebhookDistributor(BaseDistributor):
    """通过 HTTP POST 将 JSON 消息推送到 Webhook 端点。

    支持 HMAC-SHA256 签名校验（X-Signature-SHA256 头），方便接收端验证来源。
    """

    def __init__(self, url: str, secret: str = ""):
        self.url = url
        self.secret = secret
        self._session: aiohttp.ClientSession | None = None

    async def start(self):
        if not self.url:
            logger.info("🪝 Webhook 分发器未配置 URL，已跳过启动")
            return
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
        logger.success(f"🪝 Webhook 分发器已启动 (目标: {self.url})")

    async def stop(self):
        if self._session:
            await self._session.close()
            logger.info("🪝 Webhook 分发器已关闭")

    async def distribute(self, message: dict) -> None:
        if not self.url or not self._session:
            return

        body = json.dumps(message, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}

        if self.secret:
            signature = hmac.new(
                self.secret.encode(), body, hashlib.sha256
            ).hexdigest()
            headers["X-Signature-SHA256"] = signature

        try:
            async with self._session.post(self.url, data=body, headers=headers) as resp:
                if resp.status < 300:
                    logger.debug(f"🪝 Webhook 推送成功 [{resp.status}]")
                else:
                    resp_body = await resp.text()
                    logger.error(f"🪝 Webhook 推送失败 [{resp.status}]: {resp_body[:200]}")
        except asyncio.TimeoutError:
            logger.error("🪝 Webhook 推送超时 (10s)")
        except aiohttp.ClientError as e:
            logger.error(f"🪝 Webhook 推送网络异常: {e}")
        except Exception as e:
            logger.error(f"🪝 Webhook 推送未知异常: {e}")


# ---------------------------------------------------------------------------
#  分发器集线器
# ---------------------------------------------------------------------------
class DistributorHub:
    """管理所有分发器的生命周期与消息扇出。"""

    def __init__(self, distributors: list[BaseDistributor] | None = None):
        self.distributors = distributors or []

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
        """将消息广播到所有分发器（并发执行，单个失败不影响其余）。"""
        tasks = [distributor.distribute(message) for distributor in self.distributors]
        if not tasks:
            return
            
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for distributor, result in zip(self.distributors, results):
            if isinstance(result, Exception):
                logger.error(f"❌ 分发失败: {type(distributor).__name__} - {result}")
