"""
ファイル操作 API
すべてのパスを root 配下に封じ込める（シンボリックリンクによる脱出も防ぐ）。
"""
import codecs
import mimetypes
import os
import shutil

# エディタで開ける最大サイズ
MAX_TEXT_BYTES = 4 * 1024 * 1024
# バイナリ判定に使う先頭バイト数
SNIFF_BYTES = 8192


class FileServiceError(Exception):
    """利用者に返して良いファイル操作エラー"""


class PathOutsideRootError(FileServiceError):
    """root の外を参照しようとした"""


class FileService:
    def __init__(self, root: str):
        self.root = os.path.realpath(root)

    # ------------------------------------------------------------ パス解決

    def resolve(self, rel_path: str) -> str:
        """
        相対パスを root 配下の絶対パスに解決する。
        `..` やシンボリックリンクで root を抜けようとした場合は拒否する。
        """
        rel_path = (rel_path or "").strip().lstrip("/")
        candidate = os.path.join(self.root, rel_path)

        # 存在しないパス（新規作成）は、実在する親まで遡って判定する
        probe = candidate
        while not os.path.exists(probe):
            parent = os.path.dirname(probe)
            if parent == probe:
                break
            probe = parent

        real_probe = os.path.realpath(probe)
        if real_probe != self.root and not real_probe.startswith(self.root + os.sep):
            raise PathOutsideRootError(f"root の外は参照できません: {rel_path}")

        resolved = os.path.realpath(candidate) if os.path.exists(candidate) else os.path.normpath(candidate)
        if resolved != self.root and not resolved.startswith(self.root + os.sep):
            raise PathOutsideRootError(f"root の外は参照できません: {rel_path}")
        return resolved

    def to_relative(self, abs_path: str) -> str:
        rel = os.path.relpath(abs_path, self.root)
        return "" if rel == "." else rel.replace(os.sep, "/")

    # ------------------------------------------------------------ 読み取り

    def list_dir(self, rel_path: str = "") -> dict:
        """ディレクトリ一覧（ディレクトリ優先 → 名前順）"""
        target = self.resolve(rel_path)
        if not os.path.isdir(target):
            raise FileServiceError(f"ディレクトリではありません: {rel_path}")

        entries = []
        try:
            for entry in os.scandir(target):
                try:
                    is_dir = entry.is_dir()
                    stat = entry.stat()
                    size = 0 if is_dir else stat.st_size
                    mtime = stat.st_mtime
                except OSError:
                    is_dir, size, mtime = False, 0, 0
                entries.append({
                    "name": entry.name,
                    "path": self.to_relative(os.path.join(target, entry.name)),
                    "type": "dir" if is_dir else "file",
                    "size": size,
                    "mtime": mtime,
                })
        except OSError as e:
            raise FileServiceError(f"読み取れません: {e}") from e

        entries.sort(key=lambda item: (item["type"] != "dir", item["name"].lower()))
        return {"path": self.to_relative(target), "entries": entries}

    def read_text(self, rel_path: str) -> dict:
        target = self.resolve(rel_path)
        if not os.path.isfile(target):
            raise FileServiceError(f"ファイルがありません: {rel_path}")

        size = os.path.getsize(target)
        if size > MAX_TEXT_BYTES:
            raise FileServiceError(f"大きすぎて開けません ({size:,} バイト)")

        with open(target, "rb") as f:
            raw = f.read()
        if is_probably_binary(raw):
            raise FileServiceError("バイナリファイルはエディタで開けません")

        return {
            "path": self.to_relative(target),
            "content": raw.decode("utf-8", errors="replace"),
            "size": size,
            "mtime": os.path.getmtime(target),
        }

    def read_bytes(self, rel_path: str) -> tuple[bytes, str]:
        """PDF プレビューや画像表示用。(内容, MIMEタイプ) を返す"""
        target = self.resolve(rel_path)
        if not os.path.isfile(target):
            raise FileServiceError(f"ファイルがありません: {rel_path}")
        with open(target, "rb") as f:
            data = f.read()
        mime, _ = mimetypes.guess_type(target)
        return data, mime or "application/octet-stream"

    # ------------------------------------------------------------ 書き込み

    def write_text(self, rel_path: str, content: str) -> dict:
        """一時ファイル経由で書き込み、書き込み途中の破損を防ぐ"""
        target = self.resolve(rel_path)
        if os.path.isdir(target):
            raise FileServiceError(f"ディレクトリには書き込めません: {rel_path}")

        os.makedirs(os.path.dirname(target), exist_ok=True)
        tmp_path = f"{target}.pocketdev.tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8", newline="") as f:
                f.write(content)
            os.replace(tmp_path, target)
        except OSError as e:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            raise FileServiceError(f"保存できません: {e}") from e

        return {"path": self.to_relative(target), "size": os.path.getsize(target),
                "mtime": os.path.getmtime(target)}

    def make_dir(self, rel_path: str) -> dict:
        target = self.resolve(rel_path)
        try:
            os.makedirs(target, exist_ok=True)
        except OSError as e:
            raise FileServiceError(f"作成できません: {e}") from e
        return {"path": self.to_relative(target), "type": "dir"}

    def delete(self, rel_path: str, recursive: bool = False) -> dict:
        target = self.resolve(rel_path)
        if target == self.root:
            raise FileServiceError("ルートは削除できません")
        if not os.path.exists(target):
            raise FileServiceError(f"存在しません: {rel_path}")

        try:
            if os.path.isdir(target):
                if recursive:
                    shutil.rmtree(target)
                else:
                    os.rmdir(target)  # 空でなければ失敗する
            else:
                os.remove(target)
        except OSError as e:
            raise FileServiceError(f"削除できません: {e}") from e
        return {"path": self.to_relative(target), "deleted": True}

    def rename(self, rel_path: str, new_rel_path: str) -> dict:
        source = self.resolve(rel_path)
        destination = self.resolve(new_rel_path)
        if source == self.root:
            raise FileServiceError("ルートは移動できません")
        if os.path.exists(destination):
            raise FileServiceError(f"既に存在します: {new_rel_path}")

        try:
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            os.rename(source, destination)
        except OSError as e:
            raise FileServiceError(f"移動できません: {e}") from e
        return {"path": self.to_relative(destination)}


def is_probably_binary(data: bytes) -> bool:
    """NUL バイトの有無と UTF-8 デコード可否でバイナリを判定する"""
    head = data[:SNIFF_BYTES]
    if b"\x00" in head:
        return True

    # final=False なので、先頭だけ切り出してマルチバイト文字が途中で切れても
    # エラーにならない（未完のバイト列は保留される）
    decoder = codecs.getincrementaldecoder("utf-8")()
    try:
        decoder.decode(head)
    except UnicodeDecodeError:
        return True
    return False
