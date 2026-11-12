"""Тесты чистых функций из tools.py."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import tools
from tools import (
    bytes_strformat,
    camelCase_to_snake_case,
    get_temp_folder,
    make_safe_filename,
    multiline_to_hashtags,
    prepare_temp_folder,
    split_text,
    url_with_schema,
)


class MultilineToHashtagsTests(unittest.TestCase):
    def test_keeps_namespaces_when_not_removing(self):
        self.assertEqual(
            multiline_to_hashtags("artist:foo\nseries:bar"),
            "#artist_foo #series_bar",
        )

    def test_removes_known_namespaces_and_groups_categories(self):
        self.assertEqual(
            multiline_to_hashtags(
                "artist:foo\nseries:bar\ncharacter:baz\nmeta:qux\nplain tag",
                remove_namespaces=True,
            ),
            "#foo\n#bar\n#baz\n#qux\n#plain_tag",
        )

    def test_does_not_corrupt_tags_containing_artist(self):
        self.assertEqual(
            multiline_to_hashtags(
                "artist:my_artist_name\nseries:my_series_name\ncharacter:my_character_name",
                remove_namespaces=True,
            ),
            "#my_artist_name\n#my_series_name\n#my_character_name",
        )

    def test_sanitizes_telegram_forbidden_characters(self):
        self.assertEqual(
            multiline_to_hashtags("foo.bar (baz) & qux"),
            "#foobar_baz_and_qux",
        )

    def test_empty_and_whitespace_input_gives_empty_string(self):
        self.assertEqual(multiline_to_hashtags(""), "")
        self.assertEqual(multiline_to_hashtags("   \n\n  "), "")
        self.assertEqual(multiline_to_hashtags("", remove_namespaces=True), "")

    def test_deduplicates_hashtags(self):
        self.assertEqual(
            multiline_to_hashtags("foo\nfoo\nbar"),
            "#foo #bar",
        )

    def test_unknown_namespace_keeps_value(self):
        self.assertEqual(
            multiline_to_hashtags("unknown:value", remove_namespaces=True),
            "#value",
        )


class TextHelpersTests(unittest.TestCase):
    def test_split_text(self):
        self.assertEqual(split_text("abcdef", 2), ["ab", "cd", "ef"])
        self.assertEqual(split_text("", 2), [])
        self.assertEqual(split_text("abc", 10), ["abc"])

    def test_split_text_counts_utf16_units(self):
        parts = split_text("😀" * 3000, 4000)
        self.assertEqual([len(part) for part in parts], [2000, 1000])
        for part in parts:
            units = sum(2 if ord(char) > 0xFFFF else 1 for char in part)
            self.assertLessEqual(units, 4000)

    def test_split_text_does_not_split_surrogate_pair(self):
        self.assertEqual(split_text("😀😀", 3), ["😀", "😀"])

    def test_split_text_rejects_non_positive_size(self):
        with self.assertRaises(ValueError):
            split_text("abc", 0)

    def test_url_with_schema(self):
        self.assertEqual(url_with_schema("https://example.com"), "https://example.com")
        self.assertEqual(url_with_schema("HTTP://example.com"), "HTTP://example.com")
        self.assertEqual(url_with_schema("//example.com/a"), "https://example.com/a")
        self.assertEqual(url_with_schema("ftp://example.com"), "ftp://example.com")
        self.assertEqual(url_with_schema("example.com/a"), "https://example.com/a")

    def test_make_safe_filename(self):
        self.assertEqual(make_safe_filename(""), "file")
        self.assertEqual(make_safe_filename("CON"), "__CON")
        self.assertEqual(make_safe_filename("..."), "\uff0e" * 3)
        self.assertNotIn("/", make_safe_filename("a/b"))
        self.assertNotIn("\\", make_safe_filename("a\\b"))
        self.assertEqual(make_safe_filename("name. "), "name")

    def test_bytes_strformat_returns_human_readable_value(self):
        value = bytes_strformat(1024)
        self.assertIsInstance(value, str)
        self.assertTrue(value)

    def test_camel_case_to_snake_case(self):
        self.assertEqual(camelCase_to_snake_case("VideoNote"), "video_note")
        self.assertEqual(camelCase_to_snake_case("JPEGQuality"), "jpeg_quality")
        self.assertEqual(camelCase_to_snake_case("already_snake"), "already_snake")


class TempFolderTests(unittest.TestCase):
    def test_rejects_filesystem_root(self):
        with patch.object(tools.CONF, "TEMP_PATH", str(Path.cwd().anchor)):
            with self.assertRaises(ValueError):
                prepare_temp_folder()

    def test_rejects_current_directory(self):
        with patch.object(tools.CONF, "TEMP_PATH", str(Path.cwd())):
            with self.assertRaises(ValueError):
                prepare_temp_folder()

    def test_rejects_parent_of_current_directory(self):
        with patch.object(tools.CONF, "TEMP_PATH", str(Path.cwd().parent)):
            with self.assertRaises(ValueError):
                prepare_temp_folder()

    def test_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "target"
            target.mkdir()
            link = Path(temp_dir) / "link"
            try:
                link.symlink_to(target, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("Символические ссылки недоступны")
            with patch.object(tools.CONF, "TEMP_PATH", str(link)):
                with self.assertRaises(ValueError):
                    prepare_temp_folder()

    def test_creates_and_clears_safe_folder(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_path = Path(temp_dir) / "temp"
            temp_path = base_path / tools.TEMP_SUBDIR
            with patch.object(tools.CONF, "TEMP_PATH", str(base_path)):
                prepare_temp_folder()
                self.assertTrue(base_path.is_dir())
                self.assertTrue(temp_path.is_dir())
                (base_path / "keep.txt").write_bytes(b"keep")
                (temp_path / "old.tmp").write_bytes(b"old")
                prepare_temp_folder()
                self.assertTrue(base_path.is_dir())
                self.assertTrue(temp_path.is_dir())
                self.assertTrue((base_path / "keep.txt").exists())
                self.assertFalse((temp_path / "old.tmp").exists())

    def test_get_temp_folder_expands_environment_variable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(os.environ, {"HYDRUS_TEST_TEMP": temp_dir}):
                with patch.object(
                    tools.CONF,
                    "TEMP_PATH",
                    "$HYDRUS_TEST_TEMP/temp",
                ):
                    self.assertEqual(
                        get_temp_folder(),
                        Path(temp_dir) / "temp" / tools.TEMP_SUBDIR,
                    )


if __name__ == "__main__":
    unittest.main()
