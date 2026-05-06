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
            logger.info("浏览器代理未配置，将直连访问")

        self.context = await playwright.chromium.launch_persistent_context(**launch_options)
        self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
        return self.page

    def _require_page(self) -> Page:
        if self.page is None:
            raise RuntimeError("浏览器页面尚未初始化，请先调用 launch()")
        return self.page

    async def run_first_login_if_needed(self):
        if not config.FIRST_RUN_LOGIN:
            return
        if not config.AUTH_URL:
            raise RuntimeError("FIRST_RUN_LOGIN=True 时必须在 .env 中配置 AUTH_URL")

        page = self._require_page()
        logger.info("检测到开启了首次运行登录模式，正在访问授权登录网页...")
        await page.goto(config.AUTH_URL, wait_until="domcontentloaded", timeout=60000)
        logger.info("授权网页 DOM 已加载，正在等待 15 秒钟让网站将凭证写入本地缓存文件...")
        await page.wait_for_timeout(15000)
        logger.success("网站缓存吸录完毕！下一次启动可将 FIRST_RUN_LOGIN 改回 False。")

    async def goto_monitor_page(self):
        page = self._require_page()
        logger.info(f"正在跳转监控目标网站: {config.MONITOR_URL}")
        await page.goto(config.MONITOR_URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(5000)

    async def handle_popups(self):
        page = self._require_page()
        logger.info("正在尝试处理可能存在的更新提示弹窗...")
        for _ in range(5):
            try:
                next_btn = page.locator("button:has-text('Next'), button:has-text('Complete'), button:has-text('下一步'), button:has-text('完成')").first
                if await next_btn.is_visible(timeout=1000):
                    logger.info("发现更新提示继续按钮，正在点击关闭...")
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
                logger.info("找到【我的/Mine】标签，正在切换...")
                await my_tab.click()
                await page.wait_for_timeout(2000)
            else:
                logger.warning("未能通过精确文字找到【我的/Mine】标签元素，尝试通过相关类名寻找...")
                backup_tab = page.locator("span:has-text('我的'), span:has-text('Mine')").first
                if await backup_tab.is_visible():
                    await backup_tab.click()
                    await page.wait_for_timeout(2000)
        except Exception as e:
            logger.error(f"切换标签页时出错: {e}")

    async def save_screenshot(self):
        page = self._require_page()
        await page.screenshot(path=config.SCREENSHOT_PATH)
        logger.info(f"界面已准备完毕，运行截图已保存: {config.SCREENSHOT_PATH}")

    async def recover_after_timeout(self):
        page = self._require_page()
        await page.reload(wait_until="domcontentloaded")
        logger.success("网页刷新指令下发完成，看门狗周期重置。")
        await page.wait_for_timeout(5000)
        await self.switch_to_mine_tab()

    async def close(self):
        context = self.context
        if context:
            try:
                await context.close()
            except PlaywrightError as e:
                logger.warning(f"浏览器上下文关闭时已不可用，跳过清理错误: {e}")
            finally:
                self.context = None
                self.page = None
