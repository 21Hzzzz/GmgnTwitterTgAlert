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
                if b: lines.append(f'🅰️ <a href="{b}">旧头像</a>')
                if a: lines.append(f'🅱️ <a href="{a}">新头像</a>')

        # ──── description ────
        if action == "description":
            bio_change = msg.get("bio_change")
            if bio_change:
                lines.append("\n<b>旧简介:</b>")
                lines.append(self._escape_html(bio_change.get("before", "")))
                lines.append("\n<b>新简介:</b>")
                lines.append(self._escape_html(bio_change.get("after", "")))
        else:
            if include_text:
                content = msg.get("content") or {}
                text = content.get("text")
                if text:
                    if len(text) > 1500: text = text[:1500] + "...\n[原文过长已截断]"
                    lines.append("")
                    lines.append(self._escape_html(text))

                # 展示 reference.text（被回复/引用/转推/删帖的原文），用 blockquote 区分
                reference = msg.get("reference") or {}
                ref_text = reference.get("text")
                if ref_text:
                    if len(ref_text) > 1500: ref_text = ref_text[:1500] + "...\n[原文过长已截断]"
                    lines.append("")
                    lines.append(f"<blockquote>💬 原推：\n{self._escape_html(ref_text)}</blockquote>")

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

    async def _translate_and_edit(self, message_id: int, header_no_text: str, footer: str, message: dict, translated_dict: dict[str, str], target_channel_id: str, link_preview_options: dict | None = None) -> None:
        """使用预翻译结果编辑已发送的 TG 消息，替换英文正文为中文。"""
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

        # 获取翻译后的文本（如果返回 dict 中缺失，则 fallback 到原文）
        main_text = translated_dict.get("content") or text_parts.get("content", "")
        ref_text = translated_dict.get("reference") or text_parts.get("reference", "")
        bio_text = translated_dict.get("bio") or text_parts.get("bio", "")

        # 判断内容是否真的有改变
        if (main_text == text_parts.get("content", "") and 
            ref_text == text_parts.get("reference", "") and 
            bio_text == text_parts.get("bio", "")):
            logger.info(f"🌐 翻译结果与原文相同，跳过编辑: {target_channel_id}")
            return

        def format_part(translated: str, original: str) -> str:
            if len(translated) > 1500: 
                translated = translated[:1500] + "...\n[原文过长已截断]"
            escaped = self._escape_html(translated)
            
            # 如果原文较短（<=80字符）且有实际翻译，附加斜体原文做对比
            if original and len(original) <= 80 and original.strip() != translated.strip():
                # 排查纯表情或纯标点：要求必须包含至少一个字母或数字
                if any(c.isalpha() or c.isdigit() for c in original):
                    # 为了美观，去掉末尾的回车并包裹在括号斜体中
                    orig_clean = original.strip().replace('\n', ' ')
                    escaped += f"\n(<i>{self._escape_html(orig_clean)}</i>)"
            return escaped

        translated_html_parts = []
        if main_text or bio_text:
            t_text = main_text if main_text else bio_text
            o_text = text_parts.get("content", "") if main_text else text_parts.get("bio", "")
            translated_html_parts.append(format_part(t_text, o_text))
        if ref_text:
            o_ref = text_parts.get("reference", "")
            escaped_ref = format_part(ref_text, o_ref)
            translated_html_parts.append(f"<blockquote>💬 原推翻译：\n{escaped_ref}</blockquote>")

        translated_html = "\n\n".join(translated_html_parts)
        
        separator = "—— 🌐 中文翻译 ——\n"
        new_text = f"{header_no_text}\n\n{separator}{translated_html}\n\n{footer}"

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
            logger.info(f"🌐 TG 翻译追加成功: @{handle} -> {target_channel_id}")
        else:
            logger.warning(f"🌐 TG 翻译追加失败: @{handle} -> {target_channel_id}")

    async def _distribute_to_channel(self, message: dict, handle: str, action: str, target_channel_id: str, time_log_str: str) -> dict | None:
        """推送原文到单个频道，返回推送上下文（含 msg_id）供后续翻译编辑使用。"""
        # ──── photo 动作：由于 FxTwitter 无法展示换头像前后的两张图，需要保留 sendMediaGroup ────
        if action == "photo":
            avatar_change = message.get("avatar_change") or {}
            before_url = avatar_change.get("before", "")
            after_url = avatar_change.get("after", "")

            if before_url and after_url:
                caption = self._format_message(message)[:1024]
                import json
                media = json.dumps([
                    {"type": "photo", "media": before_url, "caption": caption, "parse_mode": "HTML"},
                    {"type": "photo", "media": after_url},
                ])
                payload = {"chat_id": target_channel_id, "media": media}
                result = await self._send_api("sendMediaGroup", payload)
                if result and result.get("ok"):
                    logger.info(f"📱 TG 头像变更推送成功: @{handle} -> {target_channel_id} | {time_log_str}")
                return None  # photo 动作不需要后续翻译编辑

        # ──── 计算时间尾部 ────
        tz_cst = timezone(timedelta(hours=8))
        ts = message.get("timestamp", 0)
        tweet_time = datetime.fromtimestamp(ts, tz=tz_cst).strftime("%Y-%m-%d %H:%M:%S") if ts else "未知"
        footer = f"🕒 推文时间: {tweet_time}"

        # ──── 头部与正文 ────
        header = self._format_message(message)
        initial_text = f"{header}\n\n{footer}"
        
        # ──── 动态计算预览链接 (使用 FxTwitter 获得更好预览) ────
        preview_url = None
        if action in ("follow", "unfollow"):
            t_handle = message.get("unfollow_target", {}).get("handle")
            if t_handle:
                preview_url = f"https://vxtwitter.com/{t_handle}"
        elif action == "repost":
            reference = message.get("reference") or {}
            ref_handle = reference.get("author_handle")
            ref_tweet_id = reference.get("tweet_id")
            if ref_handle and ref_tweet_id:
                preview_url = f"https://fxtwitter.com/{ref_handle}/status/{ref_tweet_id}"
            elif message.get("tweet_id") and handle:
                preview_url = f"https://fxtwitter.com/{handle}/status/{message.get('tweet_id')}"
        elif action in ("reply", "quote", "delete_post"):
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
        else:
            if handle:
                preview_url = f"https://vxtwitter.com/{handle}"

        link_preview_options = {"is_disabled": False, "prefer_large_media": True}
        if preview_url:
            link_preview_options["url"] = preview_url

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
                header_no_text = self._format_message(message, include_text=False)
                return {
                    "msg_id": msg_id,
                    "header_no_text": header_no_text,
                    "footer": footer,
                    "channel_id": target_channel_id,
                    "link_preview_options": link_preview_options,
                }
        return None

    async def _pre_translate(self, message: dict) -> dict[str, str] | None:
        """翻译一次，供所有频道复用。"""
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
        if not self._should_forward(message):
            return

        handle = message.get("author", {}).get("handle", "?")
        action = message.get("action", "")

        # 核心：动态路由
        h_lower = handle.lower()
        target_channel_ids = self.channel_map.get(h_lower, [])
        if not target_channel_ids:
            if not self.enable_default:
                return
            target_channel_ids = [self.default_channel_id] if self.default_channel_id else []

        if not target_channel_ids:
            return

        tz_cst = timezone(timedelta(hours=8))
        ts = message.get("timestamp", 0)
        tweet_time = datetime.fromtimestamp(ts, tz=tz_cst).strftime("%Y-%m-%d %H:%M:%S") if ts else "未知"
        push_time = datetime.now(tz=tz_cst).strftime("%Y-%m-%d %H:%M:%S")
        time_log_str = f"| 🕐 推文时间: {tweet_time} 📡 推送时间: {push_time}"

        # ──── 阶段 1：推送原文 + 翻译 并发执行 ────
        # 推送任务列表
        push_tasks = [
            self._distribute_to_channel(message, handle, action, cid, time_log_str)
            for cid in target_channel_ids
        ]
        # 翻译任务（只调一次 DeepSeek）
        translate_task = self._pre_translate(message)

        # 并发：所有频道推送 + DeepSeek 翻译 同时执行
        all_results = await asyncio.gather(
            *push_tasks, translate_task, return_exceptions=True
        )

        # 拆分结果：前 N 个是推送结果，最后一个是翻译结果
        push_results = all_results[:-1]
        translate_result = all_results[-1]

        # ──── 阶段 2：翻译完成后，批量编辑所有频道 ────
        if isinstance(translate_result, Exception):
            logger.error(f"🌐 翻译异常: {translate_result}")
            return
        if not translate_result:
            return  # 无需翻译或翻译失败

        translated_dict = translate_result
        edit_tasks = []
        for r in push_results:
            if isinstance(r, Exception) or r is None:
                continue
            edit_tasks.append(
                self._translate_and_edit(
                    r["msg_id"], r["header_no_text"], r["footer"],
                    message, translated_dict, r["channel_id"], r["link_preview_options"]
                )
            )

        if edit_tasks:
            await asyncio.gather(*edit_tasks, return_exceptions=True)


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
