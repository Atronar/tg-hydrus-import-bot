import tempfile
import unittest
from pathlib import Path

from duplicates import (
    is_priority_url,
    load_priority_domains,
    url_hostname,
)


class PriorityDomainsTests(unittest.TestCase):
    def test_missing_file_means_no_priority_domains(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            domains = load_priority_domains(
                Path(temp_dir) / "missing.txt"
            )
        self.assertEqual(domains, frozenset())

    def test_comments_blank_lines_and_leading_dots_are_ignored(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "domains.txt"
            path.write_text(
                "# comment\n"
                "\n"
                "  Example.COM  # inline comment\n"
                ".pixiv.net\n",
                encoding="utf-8",
            )
            domains = load_priority_domains(path)

        self.assertEqual(
            domains,
            frozenset({"example.com", "pixiv.net"})
        )

    def test_exact_domain_and_subdomain_match(self):
        domains = frozenset({"example.com"})

        self.assertTrue(
            is_priority_url("https://example.com/image.jpg", domains)
        )
        self.assertTrue(
            is_priority_url("https://cdn.example.com/image.jpg", domains)
        )
        self.assertFalse(
            is_priority_url("https://notexample.com/image.jpg", domains)
        )

    def test_case_and_port_do_not_affect_matching(self):
        domains = frozenset({"example.com"})

        self.assertTrue(
            is_priority_url("https://CDN.Example.COM:8443/image.jpg", domains)
        )

    def test_url_without_hostname_is_not_priority(self):
        domains = frozenset({"example.com"})

        self.assertFalse(is_priority_url("not a url", domains))
        self.assertFalse(is_priority_url("", domains))

    def test_empty_domain_list_never_matches(self):
        self.assertFalse(
            is_priority_url("https://example.com/image.jpg", frozenset())
        )

    def test_url_hostname_is_lowercased_and_normalized(self):
        self.assertEqual(
            url_hostname("https://CDN.Example.COM.:8443/image.jpg"),
            "cdn.example.com",
        )
        self.assertIsNone(url_hostname("not a url"))


if __name__ == "__main__":
    unittest.main()
