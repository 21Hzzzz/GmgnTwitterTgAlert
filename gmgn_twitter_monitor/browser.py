from typing import Any, Dict, Optional

from loguru import logger
from playwright.async_api import BrowserContext, Error as PlaywrightError, Page, Playwright

from . import config


class BrowserManager:
    def __init__(self):
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None

    async def launch(self, playwright: Playwright) -> Page:
        logger.info(f"正在启动浏览器，使用持久化数据目录: {config.USER_DATA_DIR}")
        launch_options: Dict[str, Any] = {
            "user_data_dir": config.USER_DATA_DIR,
            "headless": False,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--window-size=1920,1080",
                "--start-maximized",
            ],
        }
        if config.PROXY_SERVER:
            launch_options["proxy"] = {"server": config.PROXY_SERVER}
            logger.info(f"浏览器代理已启用: {config.PROXY_SERVER}")
        else:
            logger.info("浏览器代理未配置，将使用直连访问")

        self.context = await playwright.chromium.launch_persistent_context(**launch_options)
        self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
        return self.page

    def _require_page(self) -> Page:
        if self.page is None:
            raise RuntimeError("浏览器页面尚未初始化，请先调用 launch()")
        return self.page

    async def run_first_login(self, auth_url: str):
        if not auth_url:
            raise RuntimeError("首次登录需要提供 GMGN 授权 URL")

        page = self._require_page()
        logger.info("已进入首次登录模式，正在打开 GMGN 授权页面...")
        await page.goto(auth_url, wait_until="domcontentloaded", timeout=60000)
        logger.info("授权页面已加载，等待 15 秒写入浏览器登录状态...")
        await page.wait_for_timeout(15000)
        logger.success("首次登录状态已保存。")

    async def goto_monitor_page(self):
        page = self._require_page()
        logger.info(f"正在打开监控页面: {config.MONITOR_URL}")
        await page.goto(config.MONITOR_URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(5000)

    async def handle_popups(self):
        page = self._require_page()
        logger.info("正在检查并处理可能出现的弹窗或引导提示...")
        for _ in range(5):
            try:
                next_btn = page.locator(
                    "button:has-text('Next'), button:has-text('Complete'), "
                    "button:has-text('下一步'), button:has-text('完成')"
                ).first
                if await next_btn.is_visible(timeout=1000):
                    logger.info("发现弹窗/引导按钮，正在点击关闭...")
                    await next_btn.click()
                    await page.wait_for_timeout(500)
                else:
                    break
            except Exception:
                break

        try:
            await page.keyboard.press("Escape")
            await page.mouse.click(10, 10)
            await page.wait_for_timeout(1000)
        except Exception:
            pass

    async def switch_to_mine_tab(self):
        page = self._require_page()
        try:
            my_tab = page.locator("xpath=//*[text()='我的' or text()='Mine']").first
            if await my_tab.is_visible(timeout=2000):
                logger.info("正在切换到 Mine/我的 标签...")
                await my_tab.click()
                await page.wait_for_timeout(2000)
            else:
                logger.warning("未通过精确文字找到 Mine/我的 标签，尝试备用选择器...")
                backup_tab = page.locator("span:has-text('我的'), span:has-text('Mine')").first
                if await backup_tab.is_visible():
                    await backup_tab.click()
                    await page.wait_for_timeout(2000)
        except Exception as e:
            logger.error(f"切换到 Mine/我的 标签失败: {e}")

    async def save_screenshot(self):
        page = self._require_page()
        await page.screenshot(path=config.SCREENSHOT_PATH)
        logger.info(f"运行截图已保存: {config.SCREENSHOT_PATH}")

    async def recover_after_timeout(self):
        page = self._require_page()
        await page.reload(wait_until="domcontentloaded")
        logger.success("页面刷新完成，看门狗周期已重置。")
        await page.wait_for_timeout(5000)
        await self.switch_to_mine_tab()

    async def close(self):
        context = self.context
        if context:
            try:
                await context.close()
            except PlaywrightError as e:
                logger.warning(f"浏览器上下文已不可用，跳过清理错误: {e}")
            finally:
                self.context = None
                self.page = None
