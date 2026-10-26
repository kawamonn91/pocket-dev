"""
トークン認証
root 相当のシェルを提供するため、すべてのエンドポイントで必須とする。
"""
import secrets
from http.cookies import SimpleCookie

COOKIE_NAME = "pocketdev_token"
HEADER_NAME = "X-Auth-Token"
QUERY_NAME = "token"


def generate_token(num_bytes: int = 24) -> str:
    return secrets.token_urlsafe(num_bytes)


def is_valid(expected: str, provided: str | None) -> bool:
    """タイミング攻撃を避けるため定数時間で比較する"""
    if not expected or not provided:
        return False
    return secrets.compare_digest(expected, provided)


def extract_token(headers, query: dict) -> str | None:
    """ヘッダ → クエリ → Cookie の順にトークンを探す"""
    header_token = headers.get(HEADER_NAME)
    if header_token:
        return header_token.strip()

    query_token = query.get(QUERY_NAME)
    if query_token:
        return query_token[0] if isinstance(query_token, list) else query_token

    raw_cookie = headers.get("Cookie")
    if raw_cookie:
        cookie = SimpleCookie()
        try:
            cookie.load(raw_cookie)
        except Exception:
            return None
        morsel = cookie.get(COOKIE_NAME)
        if morsel:
            return morsel.value
    return None


def cookie_header(token: str) -> str:
    """同一オリジンに限定した Cookie（LAN 内 http でも動くよう Secure は付けない）"""
    return f"{COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Strict"
