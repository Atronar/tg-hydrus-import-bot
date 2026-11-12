"""Тесты HydrusRequests на поддельном клиенте Hydrus API."""

import os
import unittest
from io import BytesIO
from unittest.mock import Mock, patch

from hydrus_api_enums import HydrusPermission
from hydrus_requests import HydrusRequests
from tests.helpers import FakeHydrusClient, make_hydrus


ALL_PERMISSIONS = {
    HydrusPermission.URL_IMPORT_EDIT,
    HydrusPermission.FILES_IMPORT_DELETE,
    HydrusPermission.TAGS_EDIT,
    HydrusPermission.FILES_SEARCH_FETCH,
    HydrusPermission.PAGES,
    HydrusPermission.RELATIONSHIPS_EDIT,
}


class MainFileHashTests(unittest.TestCase):
    def test_returns_king_for_duplicate(self):
        client = FakeHydrusClient()
        client.relationships["original"] = {
            "is_king": False,
            "king": "king",
            "king_is_local": True,
        }
        hydrus = make_hydrus({HydrusPermission.RELATIONSHIPS_EDIT}, client)
        self.assertEqual(hydrus.get_main_file_hash("original"), "king")

    def test_returns_self_for_king(self):
        client = FakeHydrusClient()
        client.relationships["king"] = {
            "is_king": True,
            "king": "king",
            "king_is_local": True,
        }
        hydrus = make_hydrus({HydrusPermission.RELATIONSHIPS_EDIT}, client)
        self.assertEqual(hydrus.get_main_file_hash("king"), "king")

    def test_returns_file_itself_when_king_is_not_local(self):
        # Документация Hydrus: если king недоступен локально,
        # представителем группы остаётся сам файл.
        client = FakeHydrusClient()
        client.relationships["original"] = {
            "is_king": False,
            "king": "king",
            "king_is_local": False,
        }
        hydrus = make_hydrus({HydrusPermission.RELATIONSHIPS_EDIT}, client)
        self.assertEqual(hydrus.get_main_file_hash("original"), "original")

    def test_returns_none_for_unknown_file(self):
        hydrus = make_hydrus({HydrusPermission.RELATIONSHIPS_EDIT})
        self.assertIsNone(hydrus.get_main_file_hash("unknown"))

    def test_strips_algorithm_prefix(self):
        client = FakeHydrusClient()
        client.relationships["abc"] = {
            "is_king": True,
            "king": "abc",
            "king_is_local": True,
        }
        hydrus = make_hydrus({HydrusPermission.RELATIONSHIPS_EDIT}, client)
        self.assertEqual(hydrus.get_main_file_hash("sha256:abc"), "abc")
        self.assertEqual(client.calls[-1], ("get_file_relationships", ["abc"]))

    def test_without_permission_does_not_call_api(self):
        client = FakeHydrusClient()
        hydrus = make_hydrus({HydrusPermission.FILES_SEARCH_FETCH}, client)
        self.assertIsNone(hydrus.get_main_file_hash("original"))
        self.assertEqual(client.calls, [])


class GetFileTests(unittest.TestCase):
    def test_uses_king_when_relationships_available(self):
        client = FakeHydrusClient()
        client.relationships["original"] = {
            "is_king": False,
            "king": "king",
            "king_is_local": True,
        }
        hydrus = make_hydrus(
            {HydrusPermission.FILES_SEARCH_FETCH, HydrusPermission.RELATIONSHIPS_EDIT},
            client,
        )
        self.assertIs(hydrus.get_file("original"), client.file_response)
        self.assertEqual(client.calls[-1], ("get_file", "king"))

    def test_uses_original_when_king_is_not_local(self):
        client = FakeHydrusClient()
        client.relationships["original"] = {
            "is_king": False,
            "king": "king",
            "king_is_local": False,
        }
        hydrus = make_hydrus(
            {HydrusPermission.FILES_SEARCH_FETCH, HydrusPermission.RELATIONSHIPS_EDIT},
            client,
        )
        self.assertIs(hydrus.get_file("original"), client.file_response)
        self.assertEqual(client.calls[-1], ("get_file", "original"))

    def test_without_relationships_permission_returns_none(self):
        client = FakeHydrusClient()
        hydrus = make_hydrus({HydrusPermission.FILES_SEARCH_FETCH}, client)
        self.assertIsNone(hydrus.get_file("original"))
        self.assertEqual(client.calls, [])

    def test_without_search_permission_returns_none(self):
        client = FakeHydrusClient()
        hydrus = make_hydrus({HydrusPermission.RELATIONSHIPS_EDIT}, client)
        self.assertIsNone(hydrus.get_file("original"))
        self.assertEqual(client.calls, [])

    def test_empty_hash_returns_none(self):
        client = FakeHydrusClient()
        hydrus = make_hydrus(
            {HydrusPermission.FILES_SEARCH_FETCH, HydrusPermission.RELATIONSHIPS_EDIT},
            client,
        )
        self.assertIsNone(hydrus.get_file(""))


class GetTagsTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeHydrusClient()
        self.client.relationships["original"] = {
            "is_king": False,
            "king": "king",
            "king_is_local": True,
        }
        self.client.metadata["king"] = {
            "tags": {
                self.client.service_key: {
                    "display_tags": {"0": ["display", "tags"]},
                    "storage_tags": {"0": ["stored", "tags"]},
                }
            }
        }
        self.client.metadata["original"] = {
            "tags": {
                self.client.service_key: {
                    "display_tags": {"0": ["original"]},
                    "storage_tags": {"0": ["original_stored"]},
                }
            }
        }

    def test_returns_display_tags_of_king(self):
        hydrus = make_hydrus(
            {HydrusPermission.FILES_SEARCH_FETCH, HydrusPermission.RELATIONSHIPS_EDIT},
            self.client,
        )
        self.assertEqual(hydrus.get_tags("original"), ["display", "tags"])
        self.assertIn(("get_file_metadata", ["king"]), self.client.calls)

    def test_returns_storage_tags_of_king(self):
        hydrus = make_hydrus(
            {HydrusPermission.FILES_SEARCH_FETCH, HydrusPermission.RELATIONSHIPS_EDIT},
            self.client,
        )
        self.assertEqual(
            hydrus.get_tags("original", displayed_tags=False),
            ["stored", "tags"],
        )

    def test_without_relationships_permission_returns_empty(self):
        hydrus = make_hydrus({HydrusPermission.FILES_SEARCH_FETCH}, self.client)
        self.assertEqual(hydrus.get_tags("original"), [])
        self.assertEqual(self.client.calls, [])

    def test_returns_empty_without_search_permission(self):
        hydrus = make_hydrus({HydrusPermission.RELATIONSHIPS_EDIT}, self.client)
        self.assertEqual(hydrus.get_tags("original"), [])
        self.assertEqual(self.client.calls, [])

    def test_returns_empty_for_missing_metadata(self):
        self.client.relationships["unknown"] = {
            "is_king": True,
            "king": "unknown",
            "king_is_local": True,
        }
        hydrus = make_hydrus(
            {HydrusPermission.FILES_SEARCH_FETCH, HydrusPermission.RELATIONSHIPS_EDIT},
            self.client,
        )
        self.assertEqual(hydrus.get_tags("unknown"), [])

    def test_returns_empty_for_unknown_namespace(self):
        self.client.service_keys["unknown namespace"] = "unknown_service_key"
        hydrus = make_hydrus(
            {HydrusPermission.FILES_SEARCH_FETCH, HydrusPermission.RELATIONSHIPS_EDIT},
            self.client,
        )
        self.assertEqual(
            hydrus.get_tags("original", tags_namespace="unknown namespace"),
            [],
        )


class GetFileHashTests(unittest.TestCase):
    def test_exact_hash_returns_king(self):
        hydrus = make_hydrus(
            {
                HydrusPermission.FILES_IMPORT_DELETE,
                HydrusPermission.RELATIONSHIPS_EDIT,
            }
        )
        with (
            patch.object(hydrus, "generate_hashes", return_value={"hash": "h"}) as gen,
            patch.object(hydrus, "get_main_file_hash", return_value="king") as main,
        ):
            self.assertEqual(hydrus.get_file_hash(BytesIO(b"data")), "king")
        gen.assert_called_once()
        main.assert_called_once_with("h")

    def test_similar_hash_returns_king(self):
        client = FakeHydrusClient()
        client.search_hashes = ["found"]
        hydrus = make_hydrus(
            {
                HydrusPermission.FILES_IMPORT_DELETE,
                HydrusPermission.FILES_SEARCH_FETCH,
                HydrusPermission.RELATIONSHIPS_EDIT,
            },
            client,
        )
        with (
            patch.object(
                hydrus,
                "generate_hashes",
                return_value={"hash": "h", "perceptual_hashes": ["perceptual"]},
            ),
            patch.object(hydrus, "get_main_file_hash", side_effect=[None, "king"]),
        ):
            self.assertEqual(hydrus.get_file_hash(BytesIO(b"data")), "king")
        self.assertIn(
            (
                "search_files",
                (["system:similar to perceptual distance 5"], True),
            ),
            client.calls,
        )

    def test_returns_none_when_nothing_found(self):
        client = FakeHydrusClient()
        client.search_hashes = []
        hydrus = make_hydrus(
            {
                HydrusPermission.FILES_IMPORT_DELETE,
                HydrusPermission.FILES_SEARCH_FETCH,
                HydrusPermission.RELATIONSHIPS_EDIT,
            },
            client,
        )
        with (
            patch.object(
                hydrus,
                "generate_hashes",
                return_value={"hash": "h", "perceptual_hashes": ["perceptual"]},
            ),
            patch.object(hydrus, "get_main_file_hash", return_value=None),
        ):
            self.assertIsNone(hydrus.get_file_hash(BytesIO(b"data")))

    def test_returns_none_without_perceptual_hashes(self):
        hydrus = make_hydrus(
            {
                HydrusPermission.FILES_IMPORT_DELETE,
                HydrusPermission.RELATIONSHIPS_EDIT,
            }
        )
        with (
            patch.object(hydrus, "generate_hashes", return_value={"hash": "h"}),
            patch.object(hydrus, "get_main_file_hash", return_value=None),
        ):
            self.assertIsNone(hydrus.get_file_hash(BytesIO(b"data")))

    def test_requires_import_permission(self):
        hydrus = make_hydrus({HydrusPermission.RELATIONSHIPS_EDIT})
        with patch.object(hydrus, "generate_hashes") as gen:
            self.assertIsNone(hydrus.get_file_hash(BytesIO(b"data")))
        gen.assert_not_called()

    def test_requires_relationships_permission(self):
        hydrus = make_hydrus({HydrusPermission.FILES_IMPORT_DELETE})
        with patch.object(hydrus, "generate_hashes") as gen:
            self.assertIsNone(hydrus.get_file_hash(BytesIO(b"data")))
        gen.assert_not_called()


class GenerateHashesTests(unittest.TestCase):
    def test_file_like_is_rewound_and_sent_as_octet_stream(self):
        client = FakeHydrusClient()
        client.generated_hashes = {"hash": "abc"}
        hydrus = make_hydrus(ALL_PERMISSIONS, client)
        file = BytesIO(b"0123456789")
        file.seek(4)

        result = hydrus.generate_hashes(file)

        self.assertEqual(result, {"hash": "abc"})
        self.assertEqual(file.tell(), 0)
        self.assertIn(
            (
                "_api_request",
                (
                    "POST",
                    client._GENERATE_HASHES_PATH,
                    b"0123456789",
                    {"Content-Type": "application/octet-stream"},
                ),
            ),
            client.calls,
        )

    def test_path_is_delegated_to_client(self):
        client = FakeHydrusClient()
        client.generated_hashes = {"hash": "path_hash"}
        hydrus = make_hydrus(ALL_PERMISSIONS, client)

        result = hydrus.generate_hashes("file.bin")

        self.assertEqual(result, {"hash": "path_hash"})
        self.assertIn(("generate_hashes", "file.bin"), client.calls)

    def test_falls_back_to_public_api_without_private_endpoint(self):
        client = FakeHydrusClient()
        client._api_request = None
        client._GENERATE_HASHES_PATH = None
        client.generated_hashes = {"hash": "fallback_hash"}
        hydrus = make_hydrus(ALL_PERMISSIONS, client)

        result = hydrus.generate_hashes(BytesIO(b"data"))

        self.assertEqual(result, {"hash": "fallback_hash"})
        generate_calls = [
            call for call in client.calls if call[0] == "generate_hashes"
        ]
        self.assertEqual(len(generate_calls), 1)
        self.assertFalse(os.path.exists(generate_calls[0][1]))


class TagsAndUrlsTests(unittest.TestCase):
    def test_add_tags_cleans_and_uses_namespace(self):
        client = FakeHydrusClient()
        hydrus = make_hydrus({HydrusPermission.TAGS_EDIT}, client)

        hydrus.add_tags("file_hash", ["tag"])

        self.assertIn(("clean_tags", ["tag"]), client.calls)
        self.assertIn(
            ("add_tags", (["file_hash"], {client.service_key: ["cleaned"]})),
            client.calls,
        )

    def test_add_tags_without_permission_does_nothing(self):
        client = FakeHydrusClient()
        hydrus = make_hydrus({HydrusPermission.FILES_SEARCH_FETCH}, client)

        hydrus.add_tags("file_hash", ["tag"])

        self.assertEqual(client.calls, [])

    def test_add_tags_ignores_empty_input(self):
        client = FakeHydrusClient()
        hydrus = make_hydrus({HydrusPermission.TAGS_EDIT}, client)

        hydrus.add_tags("", ["tag"])
        hydrus.add_tags("file_hash", [])

        self.assertEqual(client.calls, [])

    def test_add_urls(self):
        client = FakeHydrusClient()
        hydrus = make_hydrus({HydrusPermission.URL_IMPORT_EDIT}, client)

        hydrus.add_urls("file_hash", ["https://example.com"])

        self.assertIn(
            ("associate_url", (["file_hash"], ["https://example.com"])),
            client.calls,
        )

    def test_add_urls_without_permission_does_nothing(self):
        client = FakeHydrusClient()
        hydrus = make_hydrus({HydrusPermission.FILES_SEARCH_FETCH}, client)

        hydrus.add_urls("file_hash", ["https://example.com"])

        self.assertEqual(client.calls, [])


class ImportContentTests(unittest.TestCase):
    def test_combines_file_and_urls(self):
        hydrus = make_hydrus(ALL_PERMISSIONS)
        hydrus.add_files_from_urls = Mock(
            return_value=[{"status": 1, "hash": "url"}]
        )
        hydrus.add_file = Mock(return_value={"status": 1, "hash": "file"})
        hydrus.add_tags = Mock()
        hydrus.add_urls = Mock()

        result = hydrus.import_content(
            BytesIO(b"data"),
            urls=["https://example.com"],
            tags=["tag"],
            page_name="page",
        )

        self.assertEqual(result, [{"status": 1, "hash": "file"}, {"status": 1, "hash": "url"}])
        hydrus.add_files_from_urls.assert_called_once_with(
            ["https://example.com"],
            page_name="page",
            tags=["tag"],
            tags_namespace=None,
        )
        hydrus.add_tags.assert_called_once_with("file", ["tag"], tags_namespace=None)
        hydrus.add_urls.assert_called_once_with("file", ["https://example.com"])


class UrlBatchImportTests(unittest.TestCase):
    def test_preserves_order_and_cleans_tags_once(self):
        client = FakeHydrusClient()
        client.url_file_statuses = {
            "u1": [[], [{"status": 1, "hash": "h1"}]],
            # Уже есть в базе — статус доступен сразу
            "u2": [[{"status": 2, "hash": "h2"}]],
            # Появился только на втором опросе
            "u3": [[], [], [{"status": 1, "hash": "h3"}]],
        }
        hydrus = make_hydrus(
            {HydrusPermission.URL_IMPORT_EDIT, HydrusPermission.TAGS_EDIT},
            client,
        )

        with patch("hydrus_requests.time.sleep"):
            result = hydrus.add_files_from_urls(
                ["u1", "u2", "u3"],
                tags=["tag"],
            )

        self.assertEqual([item["hash"] for item in result], ["h1", "h2", "h3"])
        add_url_order = [
            call[1][0]
            for call in client.calls
            if call[0] == "add_url"
        ]
        self.assertEqual(add_url_order, ["u1", "u2", "u3"])
        self.assertEqual(
            sum(1 for call in client.calls if call[0] == "clean_tags"),
            1,
        )
        self.assertIn(
            ("add_tags", (["h2"], {"tag_service_key": ["cleaned"]})),
            client.calls,
        )

    def test_deduplicates_urls_preserving_first_order(self):
        client = FakeHydrusClient()
        hydrus = make_hydrus({HydrusPermission.URL_IMPORT_EDIT}, client)

        result = hydrus.add_files_from_urls(["u1", "u2", "u1"])

        self.assertEqual(len(result), 2)
        add_url_order = [
            call[1][0]
            for call in client.calls
            if call[0] == "add_url"
        ]
        self.assertEqual(add_url_order, ["u1", "u2"])

    def test_without_permission_returns_empty(self):
        client = FakeHydrusClient()
        hydrus = make_hydrus({HydrusPermission.FILES_SEARCH_FETCH}, client)

        self.assertEqual(hydrus.add_files_from_urls(["u1"]), [])
        self.assertEqual(client.calls, [])


class CacheTests(unittest.TestCase):
    def test_tags_namespace_is_cached(self):
        client = FakeHydrusClient()
        hydrus = make_hydrus({HydrusPermission.TAGS_EDIT}, client)

        first = hydrus.get_tags_namespace_hash()
        second = hydrus.get_tags_namespace_hash()

        self.assertEqual(first, client.service_key)
        self.assertEqual(second, first)
        self.assertEqual(
            sum(1 for call in client.calls if call[0] == "get_service"),
            1,
        )

    def test_root_pages_are_cached(self):
        client = FakeHydrusClient()
        client.pages = {
            "pages": {
                "pages": [
                    {"name": "test page", "page_key": "page_key"},
                ]
            }
        }
        hydrus = make_hydrus({HydrusPermission.PAGES}, client)

        self.assertEqual(hydrus.get_page_hash_by_name("test page"), "page_key")
        self.assertEqual(hydrus.get_page_hash_by_name("test page"), "page_key")
        self.assertEqual(
            sum(1 for call in client.calls if call[0] == "get_pages"),
            1,
        )


class InsecureApiUrlTests(unittest.TestCase):
    def test_warns_for_remote_http(self):
        with patch("hydrus_requests.logger.warning") as warning:
            HydrusRequests._warn_if_insecure_api_url(
                "http://hydrus.example.com:45869"
            )
        warning.assert_called_once()

    def test_does_not_warn_for_loopback_http(self):
        for url in (
            "http://127.0.0.1:45869",
            "http://localhost:45869",
            "http://[::1]:45869",
        ):
            with self.subTest(url=url):
                with patch("hydrus_requests.logger.warning") as warning:
                    HydrusRequests._warn_if_insecure_api_url(url)
                warning.assert_not_called()

    def test_does_not_warn_for_https(self):
        with patch("hydrus_requests.logger.warning") as warning:
            HydrusRequests._warn_if_insecure_api_url(
                "https://hydrus.example.com:45869"
            )
        warning.assert_not_called()


class ConvertResponseTests(unittest.TestCase):
    def test_converts_list_of_dicts(self):
        result = HydrusRequests.convert_import_resp_to_str(
            [{"status": 1, "hash": "abc"}, {"status": 2, "hash": "def"}]
        )
        self.assertIn("status: успешно импортирован (1)", result)
        self.assertIn("hash: abc", result)
        self.assertIn("status: уже есть в базе (2)", result)
        self.assertIn("hash: def", result)

    def test_reports_failed_and_vetoed_statuses(self):
        result = HydrusRequests.convert_import_resp_to_str(
            [
                {"status": 4, "hash": "failed"},
                {"status": 7, "hash": "vetoed"},
                {"status": 3, "hash": "deleted"},
            ]
        )
        self.assertIn("не удалось импортировать (4)", result)
        self.assertIn("отклонён (7)", result)
        self.assertIn("ранее удалён (3)", result)

    def test_reports_unknown_status(self):
        result = HydrusRequests.convert_import_resp_to_str(
            [{"status": 99, "hash": "abc"}]
        )
        self.assertIn("status: неизвестный статус 99", result)


if __name__ == "__main__":
    unittest.main()
