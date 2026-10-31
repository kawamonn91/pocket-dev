"""WebSocket フレーム処理の単体テスト"""
import io
import struct
import unittest

from agent import websocket as ws


class FakeWFile(io.BytesIO):
    def flush(self):
        pass


class ComputeAcceptTest(unittest.TestCase):
    def test_rfc6455_example(self):
        # RFC 6455 の例示値
        self.assertEqual(
            ws.compute_accept("dGhlIHNhbXBsZSBub25jZQ=="),
            "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=",
        )


class EncodeFrameTest(unittest.TestCase):
    def test_short_payload_uses_7bit_length(self):
        frame = ws.encode_frame(ws.OP_TEXT, b"hi")
        self.assertEqual(frame, b"\x81\x02hi")

    def test_medium_payload_uses_16bit_length(self):
        frame = ws.encode_frame(ws.OP_BINARY, b"x" * 200)
        self.assertEqual(frame[0], 0x82)
        self.assertEqual(frame[1], 126)
        self.assertEqual(struct.unpack(">H", frame[2:4])[0], 200)

    def test_large_payload_uses_64bit_length(self):
        frame = ws.encode_frame(ws.OP_BINARY, b"x" * 70000)
        self.assertEqual(frame[1], 127)
        self.assertEqual(struct.unpack(">Q", frame[2:10])[0], 70000)


class RecvTest(unittest.TestCase):
    def connection(self, inbound: bytes):
        return ws.WebSocketConnection(io.BytesIO(inbound), FakeWFile())

    def test_masked_text_frame_is_unmasked(self):
        conn = self.connection(ws.encode_client_frame(ws.OP_TEXT, "こんにちは".encode("utf-8")))
        opcode, payload = conn.recv()

        self.assertEqual(opcode, ws.OP_TEXT)
        self.assertEqual(payload.decode("utf-8"), "こんにちは")

    def test_fragmented_message_is_joined(self):
        first = bytearray(ws.encode_client_frame(ws.OP_TEXT, b"abc"))
        first[0] &= 0x7F  # FIN を落とす
        rest = ws.encode_client_frame(ws.OP_CONTINUATION, b"def")
        conn = self.connection(bytes(first) + rest)

        opcode, payload = conn.recv()
        self.assertEqual((opcode, payload), (ws.OP_TEXT, b"abcdef"))

    def test_ping_is_answered_with_pong(self):
        inbound = ws.encode_client_frame(ws.OP_PING, b"beat") + \
            ws.encode_client_frame(ws.OP_TEXT, b"after")
        conn = self.connection(inbound)

        opcode, payload = conn.recv()
        self.assertEqual(payload, b"after")
        self.assertEqual(conn._wfile.getvalue(), ws.encode_frame(ws.OP_PONG, b"beat"))

    def test_close_frame_ends_connection(self):
        conn = self.connection(ws.encode_client_frame(ws.OP_CLOSE, b""))
        self.assertIsNone(conn.recv())
        self.assertTrue(conn.closed)

    def test_unmasked_client_frame_is_rejected(self):
        conn = self.connection(ws.encode_frame(ws.OP_TEXT, b"nope"))
        self.assertIsNone(conn.recv())
        self.assertTrue(conn.closed)

    def test_oversized_frame_is_rejected(self):
        header = bytes([0x81, 0x80 | 127]) + struct.pack(">Q", ws.MAX_FRAME_BYTES + 1)
        conn = self.connection(header + b"\x00\x00\x00\x00")
        self.assertIsNone(conn.recv())

    def test_truncated_stream_returns_none(self):
        conn = self.connection(b"\x81")
        self.assertIsNone(conn.recv())

    def test_mid_frame_disconnect_returns_none(self):
        # Header specifies 10 bytes, but stream cuts off after mask and 3 bytes of payload
        truncated_payload = bytes([0x81, 0x8A]) + b"\x00\x00\x00\x00" + b"abc"
        conn = self.connection(truncated_payload)
        self.assertIsNone(conn.recv())
        self.assertTrue(conn.closed)

        # Header specifies 16-bit extended length (126), but stream cuts off mid-length
        truncated_length = bytes([0x81, 0x80 | 126, 0x01])
        conn = self.connection(truncated_length)
        self.assertIsNone(conn.recv())
        self.assertTrue(conn.closed)

    def test_fragmented_continuation_exceeding_max_frame_bytes_closes_with_1009(self):
        orig_max = ws.MAX_FRAME_BYTES
        try:
            ws.MAX_FRAME_BYTES = 10
            first = bytearray(ws.encode_client_frame(ws.OP_TEXT, b"123456"))
            first[0] &= 0x7F  # Clear FIN bit
            cont = ws.encode_client_frame(ws.OP_CONTINUATION, b"789012")
            conn = self.connection(bytes(first) + cont)

            self.assertIsNone(conn.recv())
            self.assertTrue(conn.closed)
            # Verify close frame sent with code 1009
            sent = conn._wfile.getvalue()
            self.assertEqual(sent[0] & 0x0F, ws.OP_CLOSE)
            code = struct.unpack(">H", sent[2:4])[0]
            self.assertEqual(code, 1009)
        finally:
            ws.MAX_FRAME_BYTES = orig_max


if __name__ == "__main__":
    unittest.main()
