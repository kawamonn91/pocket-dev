"""ファイル API（特に root 封じ込め）の単体テスト"""
import os
import tempfile
import unittest

from agent.files import FileService, FileServiceError, PathOutsideRootError, is_probably_binary


class FileServiceTestBase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmpdir.name, "workspace")
        os.makedirs(self.root)
        self.outside = os.path.join(self.tmpdir.name, "secret.txt")
        with open(self.outside, "w", encoding="utf-8") as f:
            f.write("見えてはいけない内容")
        self.service = FileService(self.root)

    def tearDown(self):
        self.tmpdir.cleanup()

    def write(self, rel: str, content: str):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path


class ConfinementTest(FileServiceTestBase):
    def test_parent_traversal_is_rejected(self):
        for attempt in ("../secret.txt", "a/../../secret.txt", "/../secret.txt", "./../secret.txt"):
            with self.subTest(attempt=attempt):
                with self.assertRaises(PathOutsideRootError):
                    self.service.read_text(attempt)

    def test_absolute_path_is_treated_as_relative(self):
        self.write("etc/passwd", "root:x:0:0")
        # 先頭の / は剥がして root 配下として扱う
        self.assertIn("root:x", self.service.read_text("/etc/passwd")["content"])

    def test_symlink_escape_is_rejected(self):
        link = os.path.join(self.root, "escape")
        try:
            os.symlink(self.outside, link)
        except OSError as e:
            if getattr(e, "winerror", None) == 1314:
                self.skipTest("Windows symlink privilege required")
            raise
        with self.assertRaises(PathOutsideRootError):
            self.service.read_text("escape")

    def test_symlinked_directory_escape_is_rejected(self):
        try:
            os.symlink(self.tmpdir.name, os.path.join(self.root, "up"))
        except OSError as e:
            if getattr(e, "winerror", None) == 1314:
                self.skipTest("Windows symlink privilege required")
            raise
        with self.assertRaises(PathOutsideRootError):
            self.service.read_text("up/secret.txt")

    def test_write_outside_root_is_rejected(self):
        with self.assertRaises(PathOutsideRootError):
            self.service.write_text("../injected.txt", "x")
        self.assertFalse(os.path.exists(os.path.join(self.tmpdir.name, "injected.txt")))

    def test_root_itself_cannot_be_deleted(self):
        with self.assertRaises(FileServiceError):
            self.service.delete("")


class ListAndReadTest(FileServiceTestBase):
    def test_directories_come_first_then_names(self):
        self.write("b.txt", "b")
        self.write("A.txt", "a")
        os.makedirs(os.path.join(self.root, "zdir"))

        names = [e["name"] for e in self.service.list_dir("")["entries"]]
        self.assertEqual(names, ["zdir", "A.txt", "b.txt"])

    def test_entry_metadata(self):
        self.write("note.md", "hello")
        entry = self.service.list_dir("")["entries"][0]

        self.assertEqual(entry["type"], "file")
        self.assertEqual(entry["path"], "note.md")
        self.assertEqual(entry["size"], 5)

    def test_nested_path_uses_forward_slashes(self):
        self.write("src/app/main.py", "print(1)")
        entry = self.service.list_dir("src/app")["entries"][0]
        self.assertEqual(entry["path"], "src/app/main.py")

    def test_read_missing_file_raises(self):
        with self.assertRaises(FileServiceError):
            self.service.read_text("nope.txt")

    def test_listing_a_file_raises(self):
        self.write("a.txt", "a")
        with self.assertRaises(FileServiceError):
            self.service.list_dir("a.txt")

    def test_binary_file_is_refused(self):
        path = os.path.join(self.root, "app.bin")
        with open(path, "wb") as f:
            f.write(b"\x7fELF\x00\x01\x02")
        with self.assertRaises(FileServiceError):
            self.service.read_text("app.bin")

    def test_read_bytes_guesses_mime(self):
        path = os.path.join(self.root, "doc.pdf")
        with open(path, "wb") as f:
            f.write(b"%PDF-1.7\n")
        data, mime = self.service.read_bytes("doc.pdf")

        self.assertTrue(data.startswith(b"%PDF"))
        self.assertEqual(mime, "application/pdf")


class WriteTest(FileServiceTestBase):
    def test_write_creates_parent_directories(self):
        self.service.write_text("deep/nested/file.txt", "内容")
        self.assertEqual(self.service.read_text("deep/nested/file.txt")["content"], "内容")

    def test_write_leaves_no_temp_file(self):
        self.service.write_text("a.txt", "x")
        self.assertEqual(os.listdir(self.root), ["a.txt"])

    def test_write_to_directory_is_rejected(self):
        os.makedirs(os.path.join(self.root, "dir"))
        with self.assertRaises(FileServiceError):
            self.service.write_text("dir", "x")

    def test_rename_and_delete(self):
        self.write("old.txt", "x")
        self.service.rename("old.txt", "sub/new.txt")
        self.assertEqual(self.service.read_text("sub/new.txt")["content"], "x")

        self.service.delete("sub/new.txt")
        self.assertFalse(os.path.exists(os.path.join(self.root, "sub/new.txt")))

    def test_rename_onto_existing_is_rejected(self):
        self.write("a.txt", "a")
        self.write("b.txt", "b")
        with self.assertRaises(FileServiceError):
            self.service.rename("a.txt", "b.txt")

    def test_non_empty_dir_needs_recursive_delete(self):
        self.write("dir/a.txt", "a")
        with self.assertRaises(FileServiceError):
            self.service.delete("dir")
        self.service.delete("dir", recursive=True)
        self.assertFalse(os.path.exists(os.path.join(self.root, "dir")))


class BinaryDetectionTest(unittest.TestCase):
    def test_text_is_not_binary(self):
        self.assertFalse(is_probably_binary("日本語のテキスト\n".encode("utf-8")))

    def test_nul_byte_means_binary(self):
        self.assertTrue(is_probably_binary(b"abc\x00def"))

    def test_multibyte_cut_at_boundary_is_still_text(self):
        data = ("あ" * 4000).encode("utf-8")  # 境界で切れる
        self.assertFalse(is_probably_binary(data))


if __name__ == "__main__":
    unittest.main()
