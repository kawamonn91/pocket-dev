"""
pocket-dev エージェント本体
スマホの proot コンテナ内で動き、同一端末のブラウザ（ターミナル）と
タブレットのブラウザ（IDE）に、PTY とファイル操作を提供する。

標準ライブラリのみで動作するため、コンテナ内で pip install は不要。
"""
import argparse
import json
import logging
import mimetypes
import os
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import auth, websocket
from .files import FileService, FileServiceError, PathOutsideRootError
from .pty_session import PtySession

log = logging.getLogger("pocket-dev")

DEFAULT_PORT = 8765
DEFAULT_BIND = "127.0.0.1"
MAX_BODY_BYTES = 8 * 1024 * 1024
PTY_IDLE_TIMEOUT = 0.1


class QuietThreadingHTTPServer(ThreadingHTTPServer):
    """
    切断時のトレースバックを抑制する。
    モバイルではタブを閉じる・スリープするたびに接続が切れるため、
    既定の実装だとログがトレースバックで埋まる。
    """
    daemon_threads = True

    def handle_error(self, request, client_address):
        error = sys.exc_info()[1]
        if isinstance(error, (ConnectionResetError, BrokenPipeError, ConnectionAbortedError, TimeoutError)):
            log.debug("接続が切断されました: %s (%s)", client_address, type(error).__name__)
            return
        log.exception("リクエスト処理中に例外が発生しました: %s", client_address)


class AgentConfig:
    def __init__(self, root: str, web_dir: str, bind: str, port: int, token: str, shell: list[str]):
        self.root = os.path.realpath(root)
        self.web_dir = os.path.realpath(web_dir)
        self.bind = bind
        self.port = port
        self.token = token
        self.shell = shell


class AgentHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "pocket-dev"
    config: AgentConfig
    files: FileService

    # ------------------------------------------------------------ 共通処理

    def log_message(self, fmt, *args):
        log.info("%s %s", self.address_string(), fmt % args)

    def _send_auth_cookie_if_needed(self):
        """URL のトークンで認証した場合、以後の fetch 用に Cookie を発行する"""
        if getattr(self, "_issue_cookie", False):
            self.send_header("Set-Cookie", auth.cookie_header(self.config.token))
            self._issue_cookie = False

    def _send_json(self, payload: dict, status: int = 200, extra_headers=None):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._send_auth_cookie_if_needed()
        for key, value in (extra_headers or []):
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, data: bytes, content_type: str, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self._send_auth_cookie_if_needed()
        self.end_headers()
        self.wfile.write(data)

    def _send_error_json(self, status: int, message: str):
        self._send_json({"ok": False, "error": message}, status=status)

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > MAX_BODY_BYTES:
            raise ValueError("リクエストが大きすぎます")
        raw = self.rfile.read(length)
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("JSON オブジェクトが必要です")
        return data

    def _authorized(self, query: dict) -> bool:
        provided = auth.extract_token(self.headers, query)
        if not auth.is_valid(self.config.token, provided):
            return False
        # Cookie 以外（URL のトークン）で来た場合は Cookie を発行しておく
        self._issue_cookie = auth.extract_token(self.headers, {}) != provided
        return True

    # ---------------------------------------------------------------- GET

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == "/health":
            self._send_json({"ok": True, "service": "pocket-dev"})
            return

        if not self._authorized(query):
            self._send_error_json(401, "認証トークンが必要です")
            return

        # URL のトークンを Cookie に移し、以後のリクエストを素通りさせる
        if path in ("/", "/index.html"):
            self._redirect("/terminal.html")
            return

        if path == "/ws/pty":
            self._handle_pty_websocket(query)
            return

        if path.startswith("/api/"):
            self._handle_api_get(path, query)
            return

        self._serve_static(path)

    def _redirect(self, location: str):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Set-Cookie", auth.cookie_header(self.config.token))
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _handle_api_get(self, path: str, query: dict):
        rel_path = (query.get("path") or [""])[0]
        try:
            if path == "/api/fs/list":
                self._send_json({"ok": True, **self.files.list_dir(rel_path)})
            elif path == "/api/fs/read":
                self._send_json({"ok": True, **self.files.read_text(rel_path)})
            elif path == "/api/fs/raw":
                data, mime = self.files.read_bytes(rel_path)
                self._send_bytes(data, mime)
            elif path == "/api/info":
                self._send_json({
                    "ok": True,
                    "root": self.config.root,
                    "shell": self.config.shell,
                    "hostname": socket.gethostname(),
                })
            else:
                self._send_error_json(404, "不明なエンドポイントです")
        except PathOutsideRootError as e:
            self._send_error_json(403, str(e))
        except FileServiceError as e:
            self._send_error_json(400, str(e))

    def _serve_static(self, path: str):
        rel = path.lstrip("/") or "terminal.html"
        target = os.path.realpath(os.path.join(self.config.web_dir, rel))
        if target != self.config.web_dir and not target.startswith(self.config.web_dir + os.sep):
            self._send_error_json(403, "許可されていないパスです")
            return
        if not os.path.isfile(target):
            self._send_error_json(404, "見つかりません")
            return

        mime, _ = mimetypes.guess_type(target)
        with open(target, "rb") as f:
            self._send_bytes(f.read(), mime or "application/octet-stream")

    # --------------------------------------------------------------- POST

    def do_POST(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)

        if not self._authorized(query):
            self._send_error_json(401, "認証トークンが必要です")
            return

        try:
            body = self._read_json_body()
        except (ValueError, UnicodeDecodeError) as e:
            self._send_error_json(400, f"リクエストが不正です: {e}")
            return

        handlers = {
            "/api/fs/write": lambda: self.files.write_text(body.get("path", ""), body.get("content", "")),
            "/api/fs/mkdir": lambda: self.files.make_dir(body.get("path", "")),
            "/api/fs/delete": lambda: self.files.delete(body.get("path", ""), bool(body.get("recursive"))),
            "/api/fs/rename": lambda: self.files.rename(body.get("path", ""), body.get("to", "")),
        }
        handler = handlers.get(parsed.path)
        if not handler:
            self._send_error_json(404, "不明なエンドポイントです")
            return

        try:
            self._send_json({"ok": True, **handler()})
        except PathOutsideRootError as e:
            self._send_error_json(403, str(e))
        except FileServiceError as e:
            self._send_error_json(400, str(e))

    # ----------------------------------------------------------- WebSocket

    def _handle_pty_websocket(self, query: dict):
        key = self.headers.get("Sec-WebSocket-Key")
        if (self.headers.get("Upgrade") or "").lower() != "websocket" or not key:
            self._send_error_json(400, "WebSocket アップグレードが必要です")
            return

        self.send_response(101, "Switching Protocols")
        for name, value in websocket.handshake_headers(key):
            self.send_header(name, value)
        self.end_headers()
        self.close_connection = True

        connection = websocket.WebSocketConnection(self.rfile, self.wfile)
        cols = _int_arg(query, "cols", 80)
        rows = _int_arg(query, "rows", 24)
        cwd = query.get("cwd", [""])[0]

        try:
            start_dir = self.files.resolve(cwd) if cwd else self.config.root
        except PathOutsideRootError:
            start_dir = self.config.root
        if not os.path.isdir(start_dir):
            start_dir = self.config.root

        session = PtySession(self.config.shell, cwd=start_dir, cols=cols, rows=rows)
        session.start()
        log.info("PTY を開始しました (pid=%s, %sx%s, cwd=%s)", session.pid, cols, rows, start_dir)

        pump = threading.Thread(target=_pump_output, args=(session, connection), daemon=True)
        pump.start()

        try:
            while not connection.closed:
                message = connection.recv()
                if message is None:
                    break
                _, payload = message
                _dispatch_client_message(session, payload)
        finally:
            session.terminate()
            connection.close()
            log.info("PTY を終了しました")


def _int_arg(query: dict, name: str, default: int) -> int:
    try:
        return int(query.get(name, [default])[0])
    except (TypeError, ValueError):
        return default


def _dispatch_client_message(session: PtySession, payload: bytes):
    """クライアントからの JSON メッセージを PTY に反映する"""
    try:
        message = json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return

    kind = message.get("type")
    if kind == "input":
        session.write(str(message.get("data", "")).encode("utf-8"))
    elif kind == "resize":
        session.resize(message.get("cols", session.cols), message.get("rows", session.rows))


def _pump_output(session: PtySession, connection: websocket.WebSocketConnection):
    """PTY の出力を WebSocket に転送する"""
    while not connection.closed:
        data = session.read(timeout=PTY_IDLE_TIMEOUT)
        if data is None:
            connection.send_text(json.dumps({"type": "exit"}))
            connection.close()
            return
        if data:
            connection.send_binary(data)


def detect_lan_ip() -> str:
    """LAN 側 IP を推定する（外部に通信はしない）"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 1))  # TEST-NET-1: 実際には送信されない
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def build_server(config: AgentConfig) -> ThreadingHTTPServer:
    handler = type("BoundAgentHandler", (AgentHandler,), {
        "config": config,
        "files": FileService(config.root),
    })
    return QuietThreadingHTTPServer((config.bind, config.port), handler)


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="pocket-dev エージェント")
    parser.add_argument("--root", default=os.path.expanduser("~/workspace"),
                        help="公開する作業ディレクトリ（既定: ~/workspace）")
    parser.add_argument("--bind", default=DEFAULT_BIND,
                        help="待受アドレス。既定は端末内のみ(127.0.0.1)。"
                             "タブレットから繋ぐ場合のみ 0.0.0.0 を指定する")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--token", default=os.environ.get("POCKETDEV_TOKEN", ""),
                        help="認証トークン（省略時は自動生成）")
    parser.add_argument("--shell", default=os.environ.get("SHELL", "/bin/bash"))
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    os.makedirs(args.root, exist_ok=True)
    token = args.token or auth.generate_token()
    config = AgentConfig(
        root=args.root,
        web_dir=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web"),
        bind=args.bind,
        port=args.port,
        token=token,
        shell=[args.shell, "-l"],
    )

    server = build_server(config)
    host = detect_lan_ip() if args.bind not in ("127.0.0.1", "localhost") else "127.0.0.1"
    print("=" * 64)
    print(f"  pocket-dev agent  (root: {config.root})")
    print(f"  ターミナル : http://{host}:{config.port}/?token={token}")
    print(f"  IDE        : http://{host}:{config.port}/ide.html?token={token}")
    if args.bind in ("127.0.0.1", "localhost"):
        print("  ※ この端末内からのみ接続できます（タブレットから使うには --bind 0.0.0.0）")
    else:
        print("  ※ LAN 内に公開中。トークンを知る端末のみ接続できます")
    print("=" * 64)
    sys.stdout.flush()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n終了します")
    finally:
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
