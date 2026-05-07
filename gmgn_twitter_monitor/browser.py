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

    async def _click_first_visible(self, selectors: list[str], description: str) -> bool:
        page = self._require_page()
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                if await locator.is_visible(timeout=500):
                    await locator.click(timeout=3000)
                    logger.info(f"已处理弹窗控件: {description}")
                    await page.wait_for_timeout(500)
                    return True
            except Exception:
                continue
        return False

    async def _remove_blocking_modal_overlays(self) -> bool:
        page = self._require_page()
        try:
            removed = await page.evaluate(
                """
                () => {
                    const selectors = [
                        ".pi-modal-wrap",
                        ".pi-modal-mask",
                        ".chakra-modal__content-container",
                        ".chakra-modal__overlay",
                        "[role='dialog']"
                    ];
                    let removed = false;
                    for (const el of document.querySelectorAll(selectors.join(","))) {
                        const style = window.getComputedStyle(el);
                        const rect = el.getBoundingClientRect();
                        const visible = (
                            style.display !== "none" &&
                            style.visibility !== "hidden" &&
                            rect.width > 0 &&
                            rect.height > 0
                        );
                        if (visible) {
                            el.remove();
                            removed = true;
                        }
                    }
                    document.body.style.overflow = "";
                    document.documentElement.style.overflow = "";
                    return removed;
                }
                """
            )
            if removed:
                logger.warning("发现阻挡页面操作的弹窗遮罩，已执行兜底清理")
                await page.wait_for_timeout(500)
            return bool(removed)
        except Exception as e:
            logger.debug(f"弹窗遮罩兜底清理跳过: {e}")
            return False

    async def handle_popups(self):
        page = self._require_page()
        logger.info("正在检查并处理可能出现的弹窗或引导提示...")
        guide_selectors = [
            "button:has-text('Next')",
            "button:has-text('Complete')",
            "button:has-text('下一步')",
            "button:has-text('完成')",
            "button:has-text('Got it')",
            "button:has-text('知道了')",
        ]
        close_selectors = [
            ".pi-modal-wrap .pi-modal-close",
            ".pi-modal-wrap .pi-modal-close-x",
            ".pi-modal-wrap button[aria-label='Close']",
            ".pi-modal-wrap button[aria-label='close']",
            ".pi-modal-wrap [aria-label='Close']",
            ".pi-modal-wrap [aria-label='close']",
            ".chakra-modal__content-container button[aria-label='Close']",
            ".chakra-modal__content-container button[aria-label='close']",
            "[role='dialog'] button[aria-label='Close']",
            "[role='dialog'] button[aria-label='close']",
            "[role='dialog'] [class*='close']",
        ]

        for _ in range(6):
            clicked = await self._click_first_visible(guide_selectors, "引导按钮")
            clicked = await self._click_first_visible(close_selectors, "关闭按钮") or clicked
            if not clicked:
                break

        try:
            await page.keyboard.press("Escape")
            await page.mouse.click(10, 10)
            await page.wait_for_timeout(1000)
        except Exception:
            pass

        await self._remove_blocking_modal_overlays()

    async def switch_to_mine_tab(self):
        page = self._require_page()
        last_error: Exception | None = None
        tab_selectors = [
            "xpath=//*[normalize-space(text())='我的' or normalize-space(text())='Mine']",
            "div[role='tab']:has-text('我的')",
            "div[role='tab']:has-text('Mine')",
            "span:has-text('我的')",
            "span:has-text('Mine')",
        ]

        for attempt in range(2):
            if attempt:
                logger.info("重新清理弹窗后再次尝试切换 Mine/我的 标签...")
                await self.handle_popups()

            for selector in tab_selectors:
                try:
                    tab = page.locator(selector).first
                    if await tab.is_visible(timeout=2000):
                        logger.info("正在切换到 Mine/我的 标签...")
                        await tab.click(timeout=10000)
                        await page.wait_for_timeout(2000)
                        return
                except Exception as e:
                    last_error = e

        try:
            fallback_tab = page.locator(
                "xpath=//*[normalize-space(text())='我的' or normalize-space(text())='Mine']"
            ).first
            if await fallback_tab.is_visible(timeout=1000):
                logger.warning("常规点击 Mine/我的 标签失败，尝试强制点击兜底...")
                await fallback_tab.click(force=True, timeout=3000)
                await page.wait_for_timeout(2000)
                return
        except Exception as e:
            last_error = e

        if last_error:
            logger.error(f"切换到 Mine/我的 标签失败: {last_error}")
        else:
            logger.error("切换到 Mine/我的 标签失败: 未找到标签元素")

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
