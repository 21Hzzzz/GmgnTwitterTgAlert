import unittest

from gmgn_twitter_monitor.browser import _normalize_google_verification_code


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


if __name__ == "__main__":
    unittest.main()
