"""PTY セッションの単体テスト"""
import os
import time
import unittest

from agent.pty_session import PtySession

SHELL = "/bin/sh"


@unittest.skipUnless(hasattr(os, "forkpty"), "POSIX forkpty required")
class PtySessionTest(unittest.TestCase):
    def start(self, **kwargs):
        session = PtySession([SHELL], cwd=os.getcwd(), **kwargs)
        session.start()
        self.addCleanup(session.terminate)
        return session

    def read_until(self, session, needle: str, timeout: float = 5.0) -> str:
        output = ""
        deadline = time.time() + timeout
        while time.time() < deadline:
            chunk = session.read(timeout=0.1)
            if chunk is None:
                break
            output += chunk.decode("utf-8", errors="replace")
            if needle in output:
                return output
        self.fail(f"{needle!r} が出力されませんでした: {output!r}")

    def test_command_output_is_readable(self):
        session = self.start()
        session.write(b"echo POCKET_OK\n")
        self.assertIn("POCKET_OK", self.read_until(session, "POCKET_OK"))

    def test_shell_is_interactive_with_a_real_tty(self):
        """PTY 経由なので tty として認識される（vim や top が動く条件）"""
        session = self.start()
        session.write(b"test -t 0 && echo IS_TTY\n")
        self.assertIn("IS_TTY", self.read_until(session, "IS_TTY"))

    def test_starts_in_requested_directory(self):
        session = self.start()
        session.write(b"pwd\n")
        self.assertIn(os.path.realpath(os.getcwd()), self.read_until(session, os.path.basename(os.getcwd())))

    def test_resize_is_visible_to_the_shell(self):
        session = self.start(cols=80, rows=24)
        session.resize(120, 40)
        session.write(b"stty size\n")
        self.assertIn("40 120", self.read_until(session, "40 120"))

    def test_environment_marks_a_color_terminal(self):
        session = self.start()
        session.write(b"echo $TERM\n")
        self.assertIn("xterm-256color", self.read_until(session, "xterm-256color"))

    def test_exit_is_reported_as_eof(self):
        session = self.start()
        session.write(b"exit\n")

        deadline = time.time() + 5.0
        while time.time() < deadline:
            if session.read(timeout=0.1) is None:
                break
        else:
            self.fail("EOF が返りませんでした")
        self.assertFalse(session.is_alive)

    def test_terminate_is_idempotent(self):
        session = self.start()
        session.terminate()
        session.terminate()
        self.assertFalse(session.is_alive)

    def test_missing_command_exits_instead_of_hanging(self):
        session = PtySession(["/nonexistent/shell"], cwd=os.getcwd())
        session.start()
        self.addCleanup(session.terminate)

        deadline = time.time() + 5.0
        while time.time() < deadline:
            if session.read(timeout=0.1) is None:
                return
        self.fail("存在しないコマンドで EOF が返りませんでした")


if __name__ == "__main__":
    unittest.main()
