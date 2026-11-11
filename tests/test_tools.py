"""Тесты чистых функций из tools.py."""

import unittest

from tools import (
    bytes_strformat,
    camelCase_to_snake_case,
    make_safe_filename,
    multiline_to_hashtags,
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


if __name__ == "__main__":
    unittest.main()
