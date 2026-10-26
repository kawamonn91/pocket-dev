"""トークン認証の単体テスト"""
import unittest

from agent import auth


class Headers(dict):
    """http.client.HTTPMessage 相当の get() を持つ簡易ヘッダ"""


class TokenTest(unittest.TestCase):
    def test_generated_tokens_are_unique_and_long(self):
        tokens = {auth.generate_token() for _ in range(20)}
        self.assertEqual(len(tokens), 20)
        self.assertTrue(all(len(t) >= 30 for t in tokens))

    def test_validation(self):
        self.assertTrue(auth.is_valid("abc", "abc"))
        self.assertFalse(auth.is_valid("abc", "abd"))
        self.assertFalse(auth.is_valid("abc", ""))
        self.assertFalse(auth.is_valid("abc", None))
        self.assertFalse(auth.is_valid("", ""))  # トークン未設定は常に拒否


class ExtractTokenTest(unittest.TestCase):
    def test_header_wins_over_query_and_cookie(self):
        headers = Headers({auth.HEADER_NAME: "from-header",
                           "Cookie": f"{auth.COOKIE_NAME}=from-cookie"})
        self.assertEqual(auth.extract_token(headers, {"token": ["from-query"]}), "from-header")

    def test_query_wins_over_cookie(self):
        headers = Headers({"Cookie": f"{auth.COOKIE_NAME}=from-cookie"})
        self.assertEqual(auth.extract_token(headers, {"token": ["from-query"]}), "from-query")

    def test_cookie_is_used_as_fallback(self):
        headers = Headers({"Cookie": f"other=1; {auth.COOKIE_NAME}=from-cookie"})
        self.assertEqual(auth.extract_token(headers, {}), "from-cookie")

    def test_missing_token_returns_none(self):
        self.assertIsNone(auth.extract_token(Headers(), {}))

    def test_broken_cookie_returns_none(self):
        self.assertIsNone(auth.extract_token(Headers({"Cookie": "=====;;;"}), {}))

    def test_cookie_header_is_scoped(self):
        header = auth.cookie_header("tok")
        self.assertIn("HttpOnly", header)
        self.assertIn("SameSite=Strict", header)


if __name__ == "__main__":
    unittest.main()
