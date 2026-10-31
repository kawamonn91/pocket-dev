"""エージェントの通し（HTTP + WebSocket）テスト: 実際にサーバを起動して叩く"""
import base64
import json
import os
import socket
import struct
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

from agent import websocket as ws
from agent.server import AgentConfig, build_server

TOKEN = "test-token-123"


class WsClient:
    """テスト用の最小 WebSocket クライアント"""

    def __init__(self, host: str, port: int, path: str):
        self.sock = socket.create_connection((host, port), timeout=5)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(request.encode("ascii"))
        self.rfile = self.sock.makefile("rb")

        status_line = self.rfile.readline().decode("ascii").strip()
        headers = {}
        while True:
            line = self.rfile.readline().decode("ascii").strip()
            if not line:
                break
            name, _, value = line.partition(":")
            headers[name.lower()] = value.strip()

        self.status_line = status_line
        self.headers = headers
        self.expected_accept = ws.compute_accept(key)

    def send_json(self, payload: dict):
        frame = ws.encode_client_frame(ws.OP_TEXT, json.dumps(payload).encode("utf-8"))
        self.sock.sendall(frame)

    def recv_frame(self):
        """サーバ→クライアント（マスクなし）の 1 フレームを読む"""
        header = self.rfile.read(2)
        if not header or len(header) < 2:
            return None, b""
        opcode = header[0] & 0x0F
        length = header[1] & 0x7F
        if length == 126:
            length = struct.unpack(">H", self.rfile.read(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", self.rfile.read(8))[0]
        payload = self.rfile.read(length) if length else b""
        return opcode, payload

    def read_output_until(self, needle: str, timeout: float = 5.0) -> str:
        output = ""
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.sock.settimeout(max(0.1, deadline - time.time()))
            try:
                opcode, payload = self.recv_frame()
            except socket.timeout:
                break
            if opcode is None:
                break
            if opcode == ws.OP_BINARY:
                output += payload.decode("utf-8", errors="replace")
                if needle in output:
                    return output
        return output

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class ServerTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.TemporaryDirectory()
        cls.root = os.path.join(cls.tmpdir.name, "workspace")
        os.makedirs(cls.root)
        with open(os.path.join(cls.tmpdir.name, "secret.txt"), "w", encoding="utf-8") as f:
            f.write("見えてはいけない")

        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cls.web_dir = os.path.join(project_root, "web")
        config = AgentConfig(
            root=cls.root,
            web_dir=cls.web_dir,
            bind="127.0.0.1",
            port=0,  # 空きポートを自動割り当て
            token=TOKEN,
            shell=["/bin/sh"],
        )
        cls.server = build_server(config)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmpdir.cleanup()

    def url(self, path: str, with_token: bool = True) -> str:
        separator = "&" if "?" in path else "?"
        suffix = f"{separator}token={TOKEN}" if with_token else ""
        return f"http://127.0.0.1:{self.port}{path}{suffix}"

    def get_json(self, path: str, with_token: bool = True) -> dict:
        with urllib.request.urlopen(self.url(path, with_token), timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    def post_json(self, path: str, payload: dict) -> dict:
        request = urllib.request.Request(
            self.url(path),
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))


class AuthTest(ServerTestBase):
    def test_health_needs_no_token(self):
        self.assertTrue(self.get_json("/health", with_token=False)["ok"])

    def test_api_without_token_is_401(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.get_json("/api/fs/list", with_token=False)
        self.assertEqual(caught.exception.code, 401)

    def test_wrong_token_is_401(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/fs/list?token=wrong", timeout=5)
        self.assertEqual(caught.exception.code, 401)

    def test_root_redirects_to_terminal_and_sets_cookie(self):
        request = urllib.request.Request(self.url("/"), method="GET")

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None

        opener = urllib.request.build_opener(NoRedirect)
        try:
            opener.open(request, timeout=5)
            self.fail("リダイレクトされませんでした")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 302)
            self.assertEqual(e.headers.get("Location"), "/terminal.html")
            self.assertIn("pocketdev_token", e.headers.get("Set-Cookie", ""))


class CookieIssuanceTest(ServerTestBase):
    """URL のトークンで開いたページから、JS の fetch が続けて使えること"""

    def test_page_opened_with_url_token_gets_a_cookie(self):
        with urllib.request.urlopen(self.url("/ide.html"), timeout=5) as response:
            self.assertEqual(response.status, 200)
            self.assertIn("pocketdev_token", response.headers.get("Set-Cookie", ""))

    def test_cookie_alone_authorizes_the_api(self):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/fs/list",
            headers={"Cookie": f"pocketdev_token={TOKEN}"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            self.assertTrue(json.loads(response.read().decode("utf-8"))["ok"])

    def test_cookie_request_does_not_reissue_cookie(self):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/fs/list",
            headers={"Cookie": f"pocketdev_token={TOKEN}"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            self.assertIsNone(response.headers.get("Set-Cookie"))

    def test_both_pages_are_served(self):
        for page in ("/terminal.html", "/ide.html"):
            with self.subTest(page=page):
                with urllib.request.urlopen(self.url(page), timeout=5) as response:
                    body = response.read().decode("utf-8")
                self.assertEqual(response.status, 200)
                self.assertIn("pocket-dev", body)


class FileApiTest(ServerTestBase):
    def test_write_then_read_roundtrip(self):
        written = self.post_json("/api/fs/write", {"path": "notes/memo.txt", "content": "日本語の内容\n"})
        self.assertTrue(written["ok"])

        read = self.get_json("/api/fs/read?path=notes/memo.txt")
        self.assertEqual(read["content"], "日本語の内容\n")

    def test_list_shows_written_file(self):
        self.post_json("/api/fs/write", {"path": "listed.txt", "content": "x"})
        names = [e["name"] for e in self.get_json("/api/fs/list?path=")["entries"]]
        self.assertIn("listed.txt", names)

    def test_traversal_is_403(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.get_json("/api/fs/read?path=../secret.txt")
        self.assertEqual(caught.exception.code, 403)

    def test_missing_file_is_400(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.get_json("/api/fs/read?path=nope.txt")
        self.assertEqual(caught.exception.code, 400)

    def test_unknown_endpoint_is_404(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.get_json("/api/nope")
        self.assertEqual(caught.exception.code, 404)

    def test_raw_endpoint_serves_pdf_bytes(self):
        pdf_path = os.path.join(self.root, "doc.pdf")
        with open(pdf_path, "wb") as f:
            f.write(b"%PDF-1.7\n1 0 obj\n")

        with urllib.request.urlopen(self.url("/api/fs/raw?path=doc.pdf"), timeout=5) as response:
            self.assertEqual(response.headers.get("Content-Type"), "application/pdf")
            self.assertTrue(response.read().startswith(b"%PDF"))

    def test_info_reports_root(self):
        self.assertEqual(self.get_json("/api/info")["root"], os.path.realpath(self.root))

    def test_non_dict_json_post_is_400(self):
        request = urllib.request.Request(
            self.url("/api/fs/write"),
            data=json.dumps(["not", "a", "dict"]).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=5)
        self.assertEqual(caught.exception.code, 400)

    def test_sibling_directory_static_traversal_is_rejected(self):
        sibling_dir = self.web_dir + "_sibling"
        os.makedirs(sibling_dir, exist_ok=True)
        sibling_file = os.path.join(sibling_dir, "secret.txt")
        with open(sibling_file, "w", encoding="utf-8") as f:
            f.write("sibling-secret")

        def _cleanup():
            if os.path.exists(sibling_file):
                os.remove(sibling_file)
            if os.path.exists(sibling_dir):
                os.rmdir(sibling_dir)

        self.addCleanup(_cleanup)

        sibling_name = os.path.basename(sibling_dir)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(self.url(f"/../{sibling_name}/secret.txt"), timeout=5)
        self.assertEqual(caught.exception.code, 403)


class PtyWebSocketTest(ServerTestBase):
    def connect(self, query: str = "") -> WsClient:
        client = WsClient("127.0.0.1", self.port, f"/ws/pty?token={TOKEN}{query}")
        self.addCleanup(client.close)
        return client

    @unittest.skipUnless(hasattr(os, "forkpty"), "POSIX forkpty required")
    def test_handshake_is_accepted(self):
        client = self.connect()
        self.assertIn("101", client.status_line)
        self.assertEqual(client.headers.get("sec-websocket-accept"), client.expected_accept)

    @unittest.skipUnless(hasattr(os, "forkpty"), "POSIX forkpty required")
    def test_command_runs_in_the_container_shell(self):
        client = self.connect()
        client.send_json({"type": "input", "data": "echo POCKET_OK\n"})
        self.assertIn("POCKET_OK", client.read_output_until("POCKET_OK"))

    @unittest.skipUnless(hasattr(os, "forkpty"), "POSIX forkpty required")
    def test_shell_starts_in_workspace_root(self):
        client = self.connect()
        client.send_json({"type": "input", "data": "pwd\n"})
        self.assertIn(os.path.basename(self.root), client.read_output_until("workspace"))

    @unittest.skipUnless(hasattr(os, "forkpty"), "POSIX forkpty required")
    def test_resize_message_is_applied(self):
        client = self.connect("&cols=80&rows=24")
        client.send_json({"type": "resize", "cols": 132, "rows": 43})
        client.send_json({"type": "input", "data": "stty size\n"})
        self.assertIn("43 132", client.read_output_until("43 132"))

    @unittest.skipUnless(hasattr(os, "forkpty"), "POSIX forkpty required")
    def test_cwd_outside_root_falls_back_to_root(self):
        client = self.connect("&cwd=../")
        client.send_json({"type": "input", "data": "pwd\n"})
        self.assertIn(os.path.basename(self.root), client.read_output_until("workspace"))

    def test_websocket_without_token_is_rejected(self):
        client = WsClient("127.0.0.1", self.port, "/ws/pty")
        self.addCleanup(client.close)
        self.assertIn("401", client.status_line)

    @unittest.skipUnless(hasattr(os, "forkpty"), "POSIX forkpty required")
    def test_malformed_message_does_not_kill_the_session(self):
        client = self.connect()
        client.send_json({"type": "unknown-kind"})
        client.sock.sendall(ws.encode_client_frame(ws.OP_TEXT, b"{not json"))
        client.send_json({"type": "input", "data": "echo STILL_ALIVE\n"})
        self.assertIn("STILL_ALIVE", client.read_output_until("STILL_ALIVE"))


if __name__ == "__main__":
    unittest.main()
