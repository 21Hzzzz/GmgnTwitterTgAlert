import unittest

from gmgn_twitter_monitor.browser import BrowserManager, _normalize_google_verification_code


class GoogleVerificationCodeTests(unittest.TestCase):
    def test_accepts_six_digits(self):
        self.assertEqual(_normalize_google_verification_code("123456"), "123456")

    def test_strips_whitespace_between_digits(self):
        self.assertEqual(_normalize_google_verification_code(" 123 456\n"), "123456")

    def test_rejects_non_six_digit_code(self):
        for code in ("", "12345", "1234567", "abc123", "12-456"):
            with self.subTest(code=code):
                with self.assertRaises(ValueError):
                    _normalize_google_verification_code(code)


class FirstLoginFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_waits_before_checking_google_verification(self):
        events = []

        class FakePage:
            async def goto(self, url, **kwargs):
                events.append(("goto", url, kwargs))

            async def wait_for_timeout(self, timeout_ms):
                events.append(("wait", timeout_ms))

            async def screenshot(self, **kwargs):
                events.append(("screenshot", kwargs))

        manager = BrowserManager()
        manager.page = FakePage()

        async def fake_handle_google_verification(provider):
            events.append(("check_google_verification", provider))

        manager._handle_google_verification_if_present = fake_handle_google_verification

        provider = lambda: "123456"
        await manager.run_first_login("https://gmgn.ai/auth", provider)

        self.assertEqual(events[0][0], "goto")
        self.assertEqual(events[1], ("wait", 15000))
        self.assertEqual(events[2][0], "screenshot")
        self.assertTrue(events[2][1]["path"].endswith("first_login_after_auth.png"))
        self.assertEqual(events[3], ("check_google_verification", provider))
        self.assertEqual(events[4], ("wait", 15000))

    async def test_types_pin_code_from_first_visible_input(self):
        events = []

        class FakeKeyboard:
            async def type(self, text, **kwargs):
                events.append(("type", text, kwargs))

        class FakePage:
            keyboard = FakeKeyboard()

        class FakeInput:
            def __init__(self, index):
                self.index = index

            async def fill(self, text, **kwargs):
                events.append(("fill", self.index, text, kwargs))

            async def click(self, **kwargs):
                events.append(("click", self.index, kwargs))

        manager = BrowserManager()
        manager.page = FakePage()
        inputs = [FakeInput(index) for index in range(6)]

        async def fake_visible_inputs(dialog):
            return inputs

        manager._visible_inputs = fake_visible_inputs

        await manager._fill_google_verification_code(object(), "123456")

        self.assertEqual([event[:3] for event in events[:6]], [
            ("fill", 0, ""),
            ("fill", 1, ""),
            ("fill", 2, ""),
            ("fill", 3, ""),
            ("fill", 4, ""),
            ("fill", 5, ""),
        ])
        self.assertEqual(events[6][0], "click")
        self.assertEqual(events[6][1], 0)
        self.assertEqual(events[7], ("type", "123456", {"delay": 50}))


if __name__ == "__main__":
    unittest.main()
