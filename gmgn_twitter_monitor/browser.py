from typing import Any, Dict, Optional

from loguru import logger
from playwright.async_api import BrowserContext, Error as PlaywrightError, Page, Playwright

from . import config


class BrowserManager:
    def __init__(self):
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None

    async def launch(self, playwright: Playwright) -> Page:
        logger.info(f"Starting browser with persistent data dir: {config.USER_DATA_DIR}")
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
            logger.info(f"Browser proxy enabled: {config.PROXY_SERVER}")
        else:
            logger.info("Browser proxy is not configured; using direct connection")

        self.context = await playwright.chromium.launch_persistent_context(**launch_options)
        self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
        return self.page

    def _require_page(self) -> Page:
        if self.page is None:
            raise RuntimeError("Browser page is not initialized; call launch() first")
        return self.page

    async def run_first_login(self, auth_url: str):
        if not auth_url:
            raise RuntimeError("first-login requires a GMGN authorization URL")

        page = self._require_page()
        logger.info("First-login mode enabled; opening GMGN authorization URL...")
        await page.goto(auth_url, wait_until="domcontentloaded", timeout=60000)
        logger.info("Authorization page loaded; waiting 15s for browser state to be written...")
        await page.wait_for_timeout(15000)
        logger.success("First-login browser state has been saved.")

    async def goto_monitor_page(self):
        page = self._require_page()
        logger.info(f"Opening monitor page: {config.MONITOR_URL}")
        await page.goto(config.MONITOR_URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(5000)

    async def handle_popups(self):
        page = self._require_page()
        logger.info("Checking for popups or onboarding dialogs...")
        for _ in range(5):
            try:
                next_btn = page.locator(
                    "button:has-text('Next'), button:has-text('Complete'), "
                    "button:has-text('下一步'), button:has-text('完成')"
                ).first
                if await next_btn.is_visible(timeout=1000):
                    logger.info("Found popup/onboarding button; clicking it...")
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
                logger.info("Switching to Mine tab...")
                await my_tab.click()
                await page.wait_for_timeout(2000)
            else:
                logger.warning("Mine tab not found by exact text; trying backup selector...")
                backup_tab = page.locator("span:has-text('我的'), span:has-text('Mine')").first
                if await backup_tab.is_visible():
                    await backup_tab.click()
                    await page.wait_for_timeout(2000)
        except Exception as e:
            logger.error(f"Failed to switch to Mine tab: {e}")

    async def save_screenshot(self):
        page = self._require_page()
        await page.screenshot(path=config.SCREENSHOT_PATH)
        logger.info(f"Runtime screenshot saved: {config.SCREENSHOT_PATH}")

    async def recover_after_timeout(self):
        page = self._require_page()
        await page.reload(wait_until="domcontentloaded")
        logger.success("Page reload completed; watchdog cycle reset.")
        await page.wait_for_timeout(5000)
        await self.switch_to_mine_tab()

    async def close(self):
        context = self.context
        if context:
            try:
                await context.close()
            except PlaywrightError as e:
                logger.warning(f"Browser context is already unavailable; cleanup skipped: {e}")
            finally:
                self.context = None
                self.page = None
