import json
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
        self.context = await playwright.chromium.launch_persistent_context(
            user_data_dir=config.USER_DATA_DIR,
            headless=False,
            proxy={"server": config.PROXY_SERVER},
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--window-size=1920,1080",
                "--start-maximized",
            ],
        )
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

    async def run_login(self, auth_url: str):
        logger.info("正在访问 GMGN 授权登录页面...")
        await self.page.goto(auth_url, wait_until="domcontentloaded", timeout=60000)
        logger.info("授权页面已加载，等待凭证写入浏览器数据目录...")
        await self.page.wait_for_timeout(8000)
        logger.success("GMGN 授权完成，浏览器登录态已保存。")

    async def goto_monitor_page(self):
        logger.info(f"正在跳转监控目标网站: {config.MONITOR_URL}")
        await self.page.goto(config.MONITOR_URL, wait_until="domcontentloaded", timeout=60000)
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
        selectors = (
            "xpath=//*[@role='tab' and (normalize-space()='我的' or normalize-space()='Mine' "
            "or normalize-space()='关注' or normalize-space()='Following')]",
            "xpath=//*[normalize-space()='我的' or normalize-space()='Mine' "
            "or normalize-space()='关注' or normalize-space()='Following']",
        )
        my_tab = None
        for selector in selectors:
            candidate = self.page.locator(selector).first
            if await candidate.is_visible(timeout=2000):
                my_tab = candidate
                break

        if my_tab is None:
            raise RuntimeError(
                "无法定位 Mine/Following 标签页，可能是 GMGN UI 已更改或登录态失效。"
            )

        logger.info("找到【Mine/Following】标签，正在切换...")
        try:
            # GMGN's tab handler can make Locator.click() wait for a navigation
            # even after the click has already taken effect. A DOM click avoids
            # that navigation auto-wait and still invokes the React click handler.
            await my_tab.evaluate("element => element.click()")
            await self.page.wait_for_timeout(1500)
        except Exception as error:
            logger.warning(f"DOM 点击 Mine/Following 失败，尝试强制点击: {error}")
            try:
                await my_tab.click(force=True, timeout=5000, no_wait_after=True)
                await self.page.wait_for_timeout(1500)
            except Exception as fallback_error:
                logger.warning(
                    "Mine/Following 标签切换未完成，但不会中断已经建立的上游监听: "
                    f"{fallback_error}"
                )
                return False

        selected = await my_tab.get_attribute("aria-selected")
        if selected == "true":
            logger.success("已切换到 Mine/Following 标签页。")
            return True

        logger.warning(
            "Mine/Following 标签未返回选中状态，继续保持上游 WebSocket 监听。"
        )
        return False

    async def save_screenshot(self):
        await self.page.screenshot(path=config.SCREENSHOT_PATH)
        logger.info(f"界面已准备完毕，运行截图已保存: {config.SCREENSHOT_PATH}")

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
