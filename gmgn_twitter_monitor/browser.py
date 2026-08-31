import json
import re
from contextlib import suppress

from playwright.async_api import BrowserContext, Page, Playwright
from loguru import logger

from . import config


class BrowserManager:
    def __init__(self):
        self.context: BrowserContext | None = None
        self.page: Page | None = None

    async def launch(self, playwright: Playwright) -> Page:
        logger.info(f"正在启动浏览器，使用持久化数据目录: {config.USER_DATA_DIR}")
        launch_options = dict(
            user_data_dir=config.USER_DATA_DIR,
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--window-size=1920,1080",
                "--start-maximized",
            ],
        )
        if config.PROXY_SERVER:
            launch_options["proxy"] = {"server": config.PROXY_SERVER}
        self.context = await playwright.chromium.launch_persistent_context(**launch_options)
        await self._install_ws_subscription_filter()
        restored_pages = list(self.context.pages)
        self.page = await self.context.new_page()
        self._install_page_console_bridge(self.page)
        closed_count = 0
        for page in restored_pages:
            if page is self.page:
                continue
            with suppress(Exception):
                await page.close()
                closed_count += 1
        if restored_pages:
            logger.info(
                f"已关闭 {closed_count}/{len(restored_pages)} 个持久化恢复页面，"
                "使用已注入脚本的新页面"
            )
        return self.page

    async def _install_ws_subscription_filter(self) -> None:
        if not self.context or not config.GMGN_BLOCK_WS_SUBSCRIBE_CHANNELS:
            return

        blocked_channels_json = json.dumps(config.GMGN_BLOCK_WS_SUBSCRIBE_CHANNELS)
        script = f"""
(() => {{
  const blockedChannels = new Set({blocked_channels_json});
  const originalSend = WebSocket.prototype.send;

  WebSocket.prototype.send = function(data) {{
    try {{
      const text = typeof data === "string" ? data : "";
      const compact = text.replace(/\\s+/g, "");
      for (const channel of blockedChannels) {{
        if (
          compact.includes('"action":"subscribe"') &&
          compact.includes('"channel":"' + channel + '"')
        ) {{
          console.info("[GmgnTwitterClaw] blocked WS subscribe:", channel);
          return;
        }}
      }}
    }} catch (error) {{
      // Keep the page behavior intact if the guard itself ever fails.
    }}
    return originalSend.apply(this, arguments);
  }};
}})();
"""
        await self.context.add_init_script(script)
        logger.success(
            "已安装 GMGN WS 订阅降噪脚本，屏蔽频道: "
            + ", ".join(config.GMGN_BLOCK_WS_SUBSCRIBE_CHANNELS)
        )

    def _install_page_console_bridge(self, page: Page) -> None:
        def handle_console(msg):
            text = msg.text
            if "[GmgnTwitterClaw]" in text:
                logger.info(f"浏览器控制台: {text}")

        page.on("console", handle_console)

    async def run_first_login_if_needed(self):
        if not config.FIRST_RUN_LOGIN:
            return

        if not config.AUTH_URL:
            raise RuntimeError("FIRST_RUN_LOGIN=True 时必须在 .env 中设置 AUTH_URL")

        logger.info("检测到开启了首次运行登录模式，正在访问授权登录网页...")
        await self.page.goto(config.AUTH_URL, wait_until="networkidle")
        logger.info("授权网页加载完成，正在等待 8 秒钟让网站将凭证写入本地缓存文件...")
        await self.page.wait_for_timeout(8000)
        logger.success("网站缓存吸录完毕！下一次启动可将 FIRST_RUN_LOGIN 改回 False。")

    async def goto_monitor_page(self):
        logger.info(f"正在跳转监控目标网站: {config.MONITOR_URL}")
        await self.page.goto(config.MONITOR_URL, wait_until="networkidle")
        await self.page.wait_for_timeout(5000)

    async def handle_popups(self):
        logger.info("正在尝试处理可能存在的更新提示弹窗...")
        for _ in range(5):
            try:
                next_btn = self.page.locator("button:has-text('Next'), button:has-text('Complete'), button:has-text('下一步'), button:has-text('完成')").first
                if await next_btn.is_visible(timeout=1000):
                    logger.info("发现更新提示继续按钮，正在点击关闭...")
                    await next_btn.click()
                    await self.page.wait_for_timeout(500)
                else:
                    break
            except Exception:
                break

        try:
            await self.page.keyboard.press("Escape")
            await self.page.mouse.click(10, 10)
            await self.page.wait_for_timeout(1000)
        except Exception:
            pass

    async def switch_to_mine_tab(self):
        try:
            my_tab = self.page.locator("xpath=//*[text()='我的' or text()='Mine' or text()='关注' or text()='Following']").first
            if await my_tab.is_visible(timeout=2000):
                logger.info("找到【我的/Mine/Following】标签，正在切换...")
                await my_tab.click()
                await self.page.wait_for_timeout(2000)
            else:
                logger.warning("未能通过精确文字找到【我的/Mine/Following】标签元素，尝试通过相关类名寻找...")
                backup_tab = self.page.locator("span:has-text('我的'), span:has-text('Mine'), span:has-text('关注'), span:has-text('Following')").first
                if await backup_tab.is_visible():
                    await backup_tab.click()
                    await self.page.wait_for_timeout(2000)
                else:
                    raise RuntimeError("无法定位到目标标签页！可能是 UI 更改或登录状态（Cookie）已失效。")
        except Exception as e:
            logger.error(f"切换标签页时出错: {e}")
            raise

    async def save_screenshot(self):
        await self.page.screenshot(path=config.SCREENSHOT_PATH)
        logger.info(f"界面已准备完毕，运行截图已保存: {config.SCREENSHOT_PATH}")

    async def resolve_visible_reference(self, raw_item: dict) -> dict | None:
        """从 GMGN 页面里为缺失 cp=1 的快照消息兜底提取引用卡片。

        GMGN 偶尔只通过 WS 下发快照版 reply/quote，但页面已经渲染出完整引用卡片。
        这里扫描当前可见卡片，补出 su/sc，供下游沿用现有 reference 展示逻辑。
        """
        if not self.page or not raw_item:
            return None

        content = raw_item.get("c") if isinstance(raw_item.get("c"), dict) else {}
        author = raw_item.get("u") if isinstance(raw_item.get("u"), dict) else {}
        query = {
            "handle": author.get("s") or "",
            "text": content.get("t") or "",
            "tweetId": raw_item.get("ti") or "",
            "authorAvatar": author.get("a") or "",
        }

        try:
            result = await self.page.evaluate(
                """
({ handle, text, tweetId, authorAvatar }) => {
  const clean = (value) => String(value || "")
    .replace(/\\s+/g, " ")
    .trim();
  const isVisible = (element) => {
    const style = window.getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return style.display !== "none" &&
      style.visibility !== "hidden" &&
      rect.width > 80 &&
      rect.height > 40 &&
      rect.bottom >= 0 &&
      rect.right >= 0 &&
      rect.top <= window.innerHeight &&
      rect.left <= window.innerWidth;
  };
  const avatarFile = (url) => {
    try {
      return new URL(url).pathname.split("/").pop() || "";
    } catch (_) {
      return "";
    }
  };
  const avatarNeedle = avatarFile(authorAvatar);
  const normalizeMediaUrl = (url) => {
    if (!url) return "";
    try {
      const parsed = new URL(url, window.location.href);
      const encoded = parsed.searchParams.get("url");
      if (encoded) return decodeURIComponent(encoded);
      return parsed.href;
    } catch (_) {
      return String(url);
    }
  };
  const parseHandleLine = (line) => {
    const match = line.match(/^(.+?)\\s+@([A-Za-z0-9_]{1,20})(?:\\s|$)/);
    if (!match) return null;
    return { name: clean(match[1]), handle: match[2] };
  };
  const scoreCandidate = (element) => {
    const bodyText = clean(element.innerText);
    if (!bodyText.includes("@" + handle)) return 0;
    if (text && !bodyText.includes(clean(text))) return 0;
    let score = 1;
    if (tweetId && bodyText.includes(tweetId)) score += 2;
    if (avatarNeedle && Array.from(element.querySelectorAll("img")).some((img) => (img.src || "").includes(avatarNeedle))) score += 4;
    const rect = element.getBoundingClientRect();
    if (rect.height < 900) score += 2;
    if (rect.height < 500) score += 1;
    score -= Math.max(0, bodyText.length - 2500) / 1000;
    return score;
  };

	  const candidates = Array.from(document.querySelectorAll("article, [role='article'], div"))
	    .filter(isVisible)
	    .map((element) => ({ element, score: scoreCandidate(element) }))
	    .filter((item) => item.score > 0)
	    .sort((a, b) => b.score - a.score);
	  const extractFromRoot = (root) => {
	    const lines = String(root.innerText || "").split(/\\n+/).map(clean).filter(Boolean);
	    const mainHandleIndex = lines.findIndex((line) => line.includes("@" + handle));
	    const refLineIndex = lines.findIndex((line, index) => {
	      if (index <= mainHandleIndex) return false;
	      const parsed = parseHandleLine(line);
	      return parsed && parsed.handle.toLowerCase() !== String(handle).toLowerCase();
	    });
	    if (refLineIndex === -1) return null;

	    const refAuthor = parseHandleLine(lines[refLineIndex]);
	    if (!refAuthor) return null;

	    const stopPatterns = [
	      /^\\d{4}-\\d{2}-\\d{2}/,
	      /^耗时[:：]/,
	      /^BASIC\\b/i,
	      /^SKIP\\b/i,
	      /^狙击决策轨迹/,
	      /^接收推特信号/,
	      /^策略匹配/,
	      /^白名单库检索/,
	      /^检查决策/
	    ];
	    const refTextLines = [];
	    for (let index = refLineIndex + 1; index < lines.length; index += 1) {
	      const line = lines[index];
	      if (stopPatterns.some((pattern) => pattern.test(line))) break;
	      if (parseHandleLine(line) && refTextLines.length > 0) break;
	      if (line === text || line === "回复" || line === "引用") continue;
	      refTextLines.push(line);
	    }

	    const refText = refTextLines.join("\\n").trim();
	    if (!refText) return null;

	    const media = [];
	    for (const img of Array.from(root.querySelectorAll("img"))) {
	      const src = normalizeMediaUrl(img.currentSrc || img.src);
	      if (!src || (avatarNeedle && src.includes(avatarNeedle))) continue;
	      if (/profile_images\\//.test(src)) continue;
	      if (!/(pbs\\.twimg\\.com|twimg\\.com|gmgn|binance|static)/i.test(src)) continue;
	      media.push({ t: "image", u: src });
	    }

	    return {
	      si: "",
	      su: { s: refAuthor.handle, n: refAuthor.name },
	      sc: { t: refText, m: media.slice(0, 4) }
	    };
	  };

	  for (const candidate of candidates.slice(0, 20)) {
	    const extracted = extractFromRoot(candidate.element);
	    if (extracted) return extracted;
	  }
	  return null;
	}
                """,
                query,
            )
        except Exception as exc:
            logger.debug(f"DOM 引用兜底解析失败: {exc}")
            return None

        if not isinstance(result, dict):
            return None

        sc = result.get("sc") if isinstance(result.get("sc"), dict) else {}
        su = result.get("su") if isinstance(result.get("su"), dict) else {}
        ref_text = sc.get("t") or ""
        ref_handle = su.get("s") or ""
        if not ref_text or not ref_handle:
            return None

        ref_text = re.sub(r"\s+\n", "\n", ref_text).strip()
        logger.info(
            f"🧩 DOM 引用兜底命中: @{author.get('s') or '?'} -> @{ref_handle} "
            f"ref_len={len(ref_text)} tweet_id={raw_item.get('ti') or ''}"
        )
        result["sc"]["t"] = ref_text
        return result

    async def recover_after_timeout(self, force_goto: bool = False):
        if force_goto:
            logger.info("执行完整导航恢复，重新进入监控目标页面...")
            await self.goto_monitor_page()
        else:
            await self.page.reload(wait_until="domcontentloaded")
            logger.success("网页刷新指令下发完成，看门狗周期重置。")
        await self.page.wait_for_timeout(5000)
        await self.switch_to_mine_tab()
        await self.save_screenshot()

    async def close(self):
        if self.context:
            with suppress(Exception):
                await self.context.close()
