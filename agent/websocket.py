"""
WebSocket (RFC 6455) の最小実装
proot コンテナ内で pip install を不要にするため、標準ライブラリのみで実装している。
"""
import base64
import hashlib
import os
import struct
import threading

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

OP_CONTINUATION = 0x0
OP_TEXT = 0x1
OP_BINARY = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA

# 1フレームの受信上限（過大なフレームでメモリを食わせないため）
MAX_FRAME_BYTES = 8 * 1024 * 1024


class WebSocketError(Exception):
    pass


def compute_accept(key: str) -> str:
    """Sec-WebSocket-Key から Sec-WebSocket-Accept を計算する"""
    digest = hashlib.sha1((key + GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def encode_frame(opcode: int, payload: bytes) -> bytes:
    """サーバ→クライアントのフレームを組み立てる（マスクなし）"""
    header = bytearray([0x80 | opcode])
    length = len(payload)
    if length < 126:
        header.append(length)
    elif length < 65536:
        header.append(126)
        header += struct.pack(">H", length)
    else:
        header.append(127)
        header += struct.pack(">Q", length)
    return bytes(header) + payload


class WebSocketConnection:
    """1本の WebSocket 接続。send は複数スレッドから呼ばれるのでロックで保護する"""

    def __init__(self, rfile, wfile):
        self._rfile = rfile
        self._wfile = wfile
        self._send_lock = threading.Lock()
        self.closed = False

    # ------------------------------------------------------------------ 送信

    def _send_frame(self, opcode: int, payload: bytes):
        if self.closed:
            return
        try:
            with self._send_lock:
                self._wfile.write(encode_frame(opcode, payload))
                self._wfile.flush()
        except OSError:
            self.closed = True

    def send_text(self, text: str):
        self._send_frame(OP_TEXT, text.encode("utf-8"))

    def send_binary(self, data: bytes):
        self._send_frame(OP_BINARY, data)

    def send_pong(self, payload: bytes = b""):
        self._send_frame(OP_PONG, payload)

    def close(self, code: int = 1000, reason: str = ""):
        if not self.closed:
            self._send_frame(OP_CLOSE, struct.pack(">H", code) + reason.encode("utf-8"))
            self.closed = True

    # ------------------------------------------------------------------ 受信

    def _read_exactly(self, count: int) -> bytes:
        data = self._rfile.read(count)
        if data is None or len(data) < count:
            raise WebSocketError("接続が途中で切断されました")
        return data

    def recv(self):
        """
        次のメッセージを (opcode, payload) で返す。切断時は None。
        ping には自動で pong を返し、継続フレームは結合する。
        """
        message = bytearray()
        message_opcode = None

        while True:
            try:
                b1, b2 = self._read_exactly(2)

                fin = bool(b1 & 0x80)
                opcode = b1 & 0x0F
                masked = bool(b2 & 0x80)
                length = b2 & 0x7F

                if length == 126:
                    length = struct.unpack(">H", self._read_exactly(2))[0]
                elif length == 127:
                    length = struct.unpack(">Q", self._read_exactly(8))[0]

                if length > MAX_FRAME_BYTES:
                    self.close(1009, "frame too large")
                    return None
                if not masked:
                    # クライアントからのフレームは必ずマスクされている必要がある
                    self.close(1002, "unmasked frame")
                    return None

                mask = self._read_exactly(4)
                payload = bytearray(self._read_exactly(length)) if length else bytearray()
            except WebSocketError:
                self.closed = True
                return None

            for i in range(length):
                payload[i] ^= mask[i & 3]

            if opcode == OP_CLOSE:
                self.closed = True
                return None
            if opcode == OP_PING:
                self.send_pong(bytes(payload))
                continue
            if opcode == OP_PONG:
                continue

            if opcode != OP_CONTINUATION:
                message_opcode = opcode
            if len(message) + len(payload) > MAX_FRAME_BYTES:
                self.close(1009, "message too large")
                return None
            message += payload

            if fin:
                return message_opcode, bytes(message)


def handshake_headers(key: str) -> list[tuple[str, str]]:
    """アップグレード応答に必要なヘッダ一覧"""
    return [
        ("Upgrade", "websocket"),
        ("Connection", "Upgrade"),
        ("Sec-WebSocket-Accept", compute_accept(key)),
    ]


def generate_client_mask() -> bytes:
    """テスト用: クライアント側マスクキー"""
    return os.urandom(4)


def encode_client_frame(opcode: int, payload: bytes) -> bytes:
    """テスト用: クライアント→サーバのマスク付きフレームを組み立てる"""
    mask = generate_client_mask()
    masked = bytearray(payload)
    for i in range(len(masked)):
        masked[i] ^= mask[i & 3]

    header = bytearray([0x80 | opcode])
    length = len(payload)
    if length < 126:
        header.append(0x80 | length)
    elif length < 65536:
        header.append(0x80 | 126)
        header += struct.pack(">H", length)
    else:
        header.append(0x80 | 127)
        header += struct.pack(">Q", length)
    return bytes(header) + mask + bytes(masked)
