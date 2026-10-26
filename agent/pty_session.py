"""
擬似端末（PTY）セッション
os.forkpty() を使うため追加の依存（node-pty 等のネイティブモジュール）が不要。
vim や top のような全画面アプリ、色、ジョブ制御がそのまま動く。
"""
import fcntl
import os
import select
import signal
import struct
import termios

DEFAULT_COLS = 80
DEFAULT_ROWS = 24
READ_CHUNK = 65536


class PtySession:
    def __init__(self, command: list[str], cwd: str, env: dict | None = None,
                 cols: int = DEFAULT_COLS, rows: int = DEFAULT_ROWS):
        self.command = command
        self.cwd = cwd
        self.cols = cols
        self.rows = rows
        self.pid = -1
        self.fd = -1

        self.env = dict(os.environ)
        self.env.update({
            "TERM": "xterm-256color",
            "COLORTERM": "truecolor",
            "LANG": self.env.get("LANG", "C.UTF-8"),
        })
        if env:
            self.env.update(env)

    def start(self):
        pid, fd = os.forkpty()
        if pid == 0:
            # 子プロセス: 作業ディレクトリを移してシェルを起動する
            try:
                os.chdir(self.cwd)
            except OSError:
                pass
            try:
                os.execvpe(self.command[0], self.command, self.env)
            except OSError:
                os._exit(127)

        self.pid = pid
        self.fd = fd
        self.resize(self.cols, self.rows)
        return pid

    @property
    def is_alive(self) -> bool:
        if self.pid <= 0:
            return False
        try:
            waited, _ = os.waitpid(self.pid, os.WNOHANG)
        except ChildProcessError:
            return False
        return waited == 0

    def resize(self, cols: int, rows: int):
        """端末サイズを変更する（SIGWINCH も自動で飛ぶ）"""
        self.cols = max(1, int(cols))
        self.rows = max(1, int(rows))
        if self.fd < 0:
            return
        try:
            packed = struct.pack("HHHH", self.rows, self.cols, 0, 0)
            fcntl.ioctl(self.fd, termios.TIOCSWINSZ, packed)
        except OSError:
            pass

    def read(self, timeout: float = 0.1) -> bytes:
        """出力を読む。タイムアウト時は空バイト列、EOF では None"""
        if self.fd < 0:
            return None
        try:
            ready, _, _ = select.select([self.fd], [], [], timeout)
        except (OSError, ValueError):
            return None
        if not ready:
            return b""
        try:
            return os.read(self.fd, READ_CHUNK) or None
        except OSError:
            return None

    def write(self, data: bytes):
        if self.fd < 0:
            return
        try:
            os.write(self.fd, data)
        except OSError:
            pass

    def terminate(self):
        """シェルを終了させ、PTY を閉じる"""
        if self.pid > 0:
            for sig in (signal.SIGHUP, signal.SIGKILL):
                try:
                    os.kill(self.pid, sig)
                except OSError:
                    break
                try:
                    waited, _ = os.waitpid(self.pid, os.WNOHANG)
                    if waited != 0:
                        break
                except ChildProcessError:
                    break
            self.pid = -1
        if self.fd >= 0:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = -1
