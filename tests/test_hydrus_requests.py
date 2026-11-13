"""Тесты HydrusRequests на поддельном клиенте Hydrus API."""

import os
import unittest
from io import BytesIO
from unittest.mock import Mock, patch

import hydrus_api
import requests
from PIL import Image

from hydrus_api_enums import (
    AddedFileStatus,
    DuplicateRelationship,
    HydrusPermission,
)
from hydrus_requests import (
    HYDRUS_TIMEOUT,
    HydrusRequests,
    InsecureApiUrlError,
    _TimeoutSession,
)
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
            patch.object(hydrus, "_is_local_file", return_value=True),
        ):
            self.assertEqual(hydrus.get_file_hash(BytesIO(b"data")), "king")
        gen.assert_called_once()
        main.assert_called_once_with("h")

    def test_phantom_hash_is_not_returned(self):
        client = FakeHydrusClient()
        hydrus = make_hydrus(
            {
                HydrusPermission.FILES_IMPORT_DELETE,
                HydrusPermission.RELATIONSHIPS_EDIT,
                HydrusPermission.FILES_SEARCH_FETCH,
            },
            client,
        )
        with (
            patch.object(
                hydrus,
                "generate_hashes",
                return_value={"hash": "h", "perceptual_hashes": ["perceptual"]},
            ),
            patch.object(hydrus, "get_main_file_hash", return_value="phantom"),
            patch.object(hydrus, "_is_local_file", return_value=False),
        ):
            self.assertIsNone(hydrus.get_file_hash(BytesIO(b"data")))
        self.assertIn(
            (
                "search_files",
                (
                    ["system:similar to data perceptual distance 5"],
                    True,
                ),
            ),
            client.calls,
        )

    def test_similar_hash_returns_king(self):
        client = FakeHydrusClient()
        client.search_hashes = ["found"]
        hydrus = make_hydrus(
            {
                HydrusPermission.FILES_IMPORT_DELETE,
                HydrusPermission.RELATIONSHIPS_EDIT,
                HydrusPermission.FILES_SEARCH_FETCH,
            },
            client,
        )
        with (
            patch.object(
                hydrus,
                "generate_hashes",
                return_value={
                    "hash": "h",
                    "perceptual_hashes": ["perceptual1", "perceptual2"],
                },
            ),
            patch.object(
                hydrus,
                "get_main_file_hash",
                side_effect=[None, "king"],
            ) as main,
            patch.object(hydrus, "_is_local_file", return_value=True),
        ):
            self.assertEqual(hydrus.get_file_hash(BytesIO(b"data")), "king")
        main.assert_any_call("found")
        self.assertIn(
            (
                "search_files",
                (
                    [
                        "system:similar to data "
                        "perceptual1 perceptual2 distance 5"
                    ],
                    True,
                ),
            ),
            client.calls,
        )

    def test_phantom_similar_hash_is_not_returned(self):
        client = FakeHydrusClient()
        client.search_hashes = ["phantom"]
        hydrus = make_hydrus(
            {
                HydrusPermission.FILES_IMPORT_DELETE,
                HydrusPermission.RELATIONSHIPS_EDIT,
                HydrusPermission.FILES_SEARCH_FETCH,
            },
            client,
        )
        with (
            patch.object(
                hydrus,
                "generate_hashes",
                return_value={"hash": "h", "perceptual_hashes": ["perceptual"]},
            ),
            patch.object(
                hydrus,
                "get_main_file_hash",
                side_effect=[None, "phantom"],
            ),
            patch.object(hydrus, "_is_local_file", return_value=False),
        ):
            self.assertIsNone(hydrus.get_file_hash(BytesIO(b"data")))

    def test_requires_search_permission_for_similar(self):
        client = FakeHydrusClient()
        hydrus = make_hydrus(
            {
                HydrusPermission.FILES_IMPORT_DELETE,
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
        self.assertFalse(
            any(call[0] == "search_files" for call in client.calls)
        )

    def test_returns_none_when_nothing_found(self):
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


class LocalFileCheckTests(unittest.TestCase):
    def test_missing_metadata_is_not_local(self):
        hydrus = make_hydrus(set())
        self.assertFalse(hydrus._is_local_file("unknown"))

    def test_non_local_file_is_not_local(self):
        client = FakeHydrusClient()
        client.metadata["remote"] = {"is_local": False}
        hydrus = make_hydrus(set(), client)
        self.assertFalse(hydrus._is_local_file("remote"))

    def test_local_file_is_local(self):
        client = FakeHydrusClient()
        client.metadata["local"] = {"is_local": True}
        hydrus = make_hydrus(set(), client)
        self.assertTrue(hydrus._is_local_file("local"))
        self.assertIn(("get_file_metadata", ["local"]), client.calls)

    def test_empty_hash_is_not_local(self):
        hydrus = make_hydrus(set())
        self.assertFalse(hydrus._is_local_file(""))


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

    def test_add_url_error_does_not_abort_batch(self):
        client = FakeHydrusClient()
        client.add_url_errors["u2"] = hydrus_api.HydrusAPIException("boom")
        hydrus = make_hydrus({HydrusPermission.URL_IMPORT_EDIT}, client)

        result = hydrus.add_files_from_urls(["u1", "u2", "u3"])

        self.assertEqual(len(result), 3)
        self.assertEqual(
            [item["status"] for item in result],
            [1, int(AddedFileStatus.FAILED_TO_IMPORT), 1],
        )
        self.assertIn("boom", result[1]["note"])

    def test_adds_tags_when_duplicate_is_found_during_polling(self):
        client = FakeHydrusClient()
        client.url_file_statuses = {
            "u1": [[], [{"status": 2, "hash": "h1"}]],
        }
        hydrus = make_hydrus(
            {HydrusPermission.URL_IMPORT_EDIT, HydrusPermission.TAGS_EDIT},
            client,
        )

        with patch("hydrus_requests.time.sleep"):
            result = hydrus.add_files_from_urls(["u1"], tags=["tag"])

        self.assertEqual(result, [{"status": 2, "hash": "h1"}])
        self.assertIn(
            ("add_tags", (["h1"], {"tag_service_key": ["cleaned"]})),
            client.calls,
        )

    def test_stops_polling_at_deadline(self):
        client = FakeHydrusClient()
        client.url_file_statuses = {"u1": [[]]}
        hydrus = make_hydrus({HydrusPermission.URL_IMPORT_EDIT}, client)

        with patch("hydrus_requests.URL_IMPORT_TIMEOUT", 0), \
                patch("hydrus_requests.time.sleep"):
            result = hydrus.add_files_from_urls(["u1"])

        self.assertEqual(result, [])


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
    def test_remote_http_raises_by_default(self):
        with patch("hydrus_requests.logger.warning") as warning:
            with self.assertRaises(InsecureApiUrlError):
                HydrusRequests._warn_if_insecure_api_url(
                    "http://hydrus.example.com:45869"
                )
        warning.assert_not_called()

    def test_remote_http_allowed_with_explicit_flag(self):
        with patch("hydrus_requests.logger.warning") as warning:
            HydrusRequests._warn_if_insecure_api_url(
                "http://hydrus.example.com:45869",
                allow_insecure=True,
            )
        warning.assert_called_once()

    def test_loopback_and_https_are_allowed(self):
        for url in (
            "http://127.0.0.1:45869",
            "http://localhost:45869",
            "http://[::1]:45869",
            "https://hydrus.example.com:45869",
        ):
            for allow_insecure in (False, True):
                with self.subTest(url=url, allow_insecure=allow_insecure):
                    with patch("hydrus_requests.logger.warning") as warning:
                        HydrusRequests._warn_if_insecure_api_url(
                            url,
                            allow_insecure=allow_insecure,
                        )
                    warning.assert_not_called()

    @patch("hydrus_requests.hydrus_api.Client")
    def test_constructor_rejects_remote_http(self, client_class):
        client_class.return_value.api_url = "http://hydrus.example.com:45869"
        with self.assertRaises(InsecureApiUrlError):
            HydrusRequests("a" * 64)
        client_class.return_value.verify_access_key.assert_not_called()

    @patch("hydrus_requests.hydrus_api.Client")
    def test_constructor_allows_remote_http_when_enabled(self, client_class):
        client_class.return_value.api_url = "http://hydrus.example.com:45869"
        client_class.return_value.verify_access_key.return_value = {
            "basic_permissions": []
        }
        with patch("hydrus_requests.logger.warning") as warning:
            hydrus = HydrusRequests(
                "a" * 64,
                allow_insecure_api_url=True,
            )
        warning.assert_called_once()
        self.assertEqual(hydrus.permissions, set())


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


class TimeoutSessionTests(unittest.TestCase):
    def test_default_timeout_is_applied(self):
        session = _TimeoutSession()
        with patch.object(
            requests.Session, "request", return_value=Mock()
        ) as request:
            session.request("GET", "https://hydrus.example.com")
        request.assert_called_once()
        self.assertEqual(
            request.call_args.kwargs["timeout"],
            HYDRUS_TIMEOUT,
        )

    def test_explicit_timeout_is_preserved(self):
        session = _TimeoutSession()
        with patch.object(
            requests.Session, "request", return_value=Mock()
        ) as request:
            session.request("GET", "https://hydrus.example.com", timeout=(1, 2))
        self.assertEqual(request.call_args.kwargs["timeout"], (1, 2))

    @patch("hydrus_requests.hydrus_api.Client")
    def test_token_client_uses_timeout_session(self, client_class):
        client_class.return_value.verify_access_key.return_value = {
            "basic_permissions": []
        }
        client_class.return_value.api_url = "https://hydrus.example.com"

        HydrusRequests("a" * 64)

        session = client_class.call_args.kwargs["session"]
        self.assertIsInstance(session, _TimeoutSession)
        self.assertEqual(session._timeout, HYDRUS_TIMEOUT)


class FindSimilarFilesTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeHydrusClient()
        self.hydrus = make_hydrus(
            {
                HydrusPermission.FILES_SEARCH_FETCH,
                HydrusPermission.RELATIONSHIPS_EDIT,
            },
            self.client,
        )

    def test_builds_query_and_filters_candidates(self):
        self.client.search_hashes = ["h1", "h2", "h1"]
        self.client.relationships = {
            "h1": {"king": "k1", "king_is_local": True},
            "h2": {"king": "k2", "king_is_local": True},
        }
        self.client.metadata = {
            "k1": {"is_local": True},
            "k2": {"is_local": True},
        }

        result = self.hydrus.find_similar_files(["p1", "p2"], 5)

        self.assertEqual(result, ["k1", "k2"])
        self.assertIn(
            (
                "search_files",
                (
                    ["system:similar to data p1 p2 distance 5"],
                    True,
                ),
            ),
            self.client.calls,
        )

    def test_non_local_candidates_are_filtered(self):
        self.client.search_hashes = ["h1", "h2"]
        self.client.relationships = {
            "h1": {"king": "k1", "king_is_local": True},
            "h2": {"king": "k2", "king_is_local": True},
        }
        self.client.metadata = {
            "k1": {"is_local": True},
            "k2": {"is_local": False},
        }

        self.assertEqual(
            self.hydrus.find_similar_files(["p1"], 5),
            ["k1"],
        )

    def test_without_search_permission_returns_empty(self):
        self.hydrus = make_hydrus(
            {HydrusPermission.RELATIONSHIPS_EDIT},
            self.client,
        )
        self.client.search_hashes = ["h1"]

        self.assertEqual(self.hydrus.find_similar_files(["p1"], 5), [])
        self.assertNotIn(
            "search_files",
            [call[0] for call in self.client.calls],
        )

    def test_without_relationships_permission_returns_empty(self):
        self.hydrus = make_hydrus(
            {HydrusPermission.FILES_SEARCH_FETCH},
            self.client,
        )
        self.client.search_hashes = ["h1"]

        self.assertEqual(self.hydrus.find_similar_files(["p1"], 5), [])
        self.assertNotIn(
            "search_files",
            [call[0] for call in self.client.calls],
        )

    def test_empty_hashes_return_empty(self):
        self.assertEqual(self.hydrus.find_similar_files([], 5), [])
        self.assertEqual(self.hydrus.find_similar_files([""], 5), [])


class GetFileInfoTests(unittest.TestCase):
    def test_returns_metadata(self):
        client = FakeHydrusClient()
        client.metadata["h"] = {"hash": "h", "known_urls": ["u"]}
        hydrus = make_hydrus(
            {HydrusPermission.FILES_SEARCH_FETCH}, client
        )

        self.assertEqual(
            hydrus.get_file_info("h"),
            {"hash": "h", "known_urls": ["u"]},
        )

    def test_missing_metadata_returns_none(self):
        hydrus = make_hydrus(
            {HydrusPermission.FILES_SEARCH_FETCH}, FakeHydrusClient()
        )

        self.assertIsNone(hydrus.get_file_info("missing"))

    def test_without_permission_returns_none(self):
        client = FakeHydrusClient()
        client.metadata["h"] = {"hash": "h"}
        hydrus = make_hydrus(set(), client)

        self.assertIsNone(hydrus.get_file_info("h"))
        self.assertNotIn(
            "get_file_metadata",
            [call[0] for call in client.calls],
        )


class SetDuplicateRelationshipTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeHydrusClient()
        self.hydrus = make_hydrus(
            {HydrusPermission.RELATIONSHIPS_EDIT}, self.client
        )

    def test_better_with_deletion_builds_payload(self):
        result = self.hydrus.set_duplicate_relationship(
            "better",
            "worse",
            relationship=DuplicateRelationship.BETTER,
            merge_content=True,
            delete_worse=True,
        )

        self.assertTrue(result)
        self.assertEqual(
            self.client.duplicate_relationship_payloads,
            [[
                {
                    "hash_a": "better",
                    "hash_b": "worse",
                    "relationship": int(DuplicateRelationship.BETTER),
                    "do_default_content_merge": True,
                    "delete_b": True,
                }
            ]],
        )

    def test_alternates_without_merge(self):
        self.assertTrue(
            self.hydrus.set_duplicate_relationship(
                "a",
                "b",
                relationship=DuplicateRelationship.ALTERNATE,
                merge_content=False,
            )
        )
        payload = self.client.duplicate_relationship_payloads[0][0]
        self.assertEqual(
            payload["relationship"],
            int(DuplicateRelationship.ALTERNATE),
        )
        self.assertFalse(payload["do_default_content_merge"])
        self.assertNotIn("delete_b", payload)

    def test_without_permission_returns_false(self):
        hydrus = make_hydrus(set(), self.client)

        self.assertFalse(
            hydrus.set_duplicate_relationship("a", "b")
        )
        self.assertEqual(self.client.duplicate_relationship_payloads, [])

    def test_same_hash_returns_false(self):
        self.assertFalse(
            self.hydrus.set_duplicate_relationship("a", "a")
        )
        self.assertEqual(self.client.duplicate_relationship_payloads, [])


def make_image(width: int = 100, height: int = 80) -> BytesIO:
    buffer = BytesIO()
    Image.new("RGB", (width, height), "red").save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


class FindExactDuplicateTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeHydrusClient()
        self.client.cleaned_tags = ["tag"]
        self.hydrus = make_hydrus(
            {
                HydrusPermission.FILES_IMPORT_DELETE,
                HydrusPermission.FILES_SEARCH_FETCH,
                HydrusPermission.RELATIONSHIPS_EDIT,
                HydrusPermission.TAGS_EDIT,
            },
            self.client,
        )

    def test_exact_sha256_returns_existing_and_adds_tags(self):
        self.client.generated_hashes = {"hash": "new"}
        self.client.relationships = {
            "new": {"king": "existing", "king_is_local": True}
        }
        self.client.metadata = {
            "existing": {"is_local": True}
        }

        duplicate = self.hydrus.find_exact_duplicate(
            BytesIO(b"data"),
            mime="image/jpeg",
            tags=["tag"],
        )

        self.assertEqual(duplicate["status"], int(AddedFileStatus.ALREADY_IN_DATABASE))
        self.assertEqual(duplicate["hash"], "existing")
        self.assertIn(
            ("add_tags", (["existing"], {"tag_service_key": ["tag"]})),
            self.client.calls,
        )

    def test_phantom_sha256_is_not_returned(self):
        self.client.generated_hashes = {"hash": "new"}
        self.client.relationships = {
            "new": {"king": "phantom", "king_is_local": True}
        }
        self.client.metadata = {
            "phantom": {"is_local": False}
        }

        self.assertIsNone(
            self.hydrus.find_exact_duplicate(
                BytesIO(b"data"), mime="image/jpeg"
            )
        )

    def test_perceptual_zero_returns_not_larger_image(self):
        self.client.generated_hashes = {
            "hash": "new",
            "perceptual_hashes": ["perceptual"],
        }
        self.client.search_hashes = ["candidate"]
        self.client.relationships = {
            "candidate": {"king": "existing", "king_is_local": True}
        }
        self.client.metadata = {
            "existing": {
                "is_local": True,
                "width": 100,
                "height": 80,
                "mime": "image/png",
            }
        }

        duplicate = self.hydrus.find_exact_duplicate(
            make_image(100, 80),
            mime="image/png",
            tags=["tag"],
        )

        self.assertEqual(duplicate["hash"], "existing")
        self.assertIn(
            ("add_tags", (["existing"], {"tag_service_key": ["tag"]})),
            self.client.calls,
        )

    def test_perceptual_zero_returns_none_when_new_is_larger(self):
        self.client.generated_hashes = {
            "hash": "new",
            "perceptual_hashes": ["perceptual"],
        }
        self.client.search_hashes = ["candidate"]
        self.client.relationships = {
            "candidate": {"king": "existing", "king_is_local": True}
        }
        self.client.metadata = {
            "existing": {
                "is_local": True,
                "width": 100,
                "height": 80,
            }
        }

        self.assertIsNone(
            self.hydrus.find_exact_duplicate(
                make_image(120, 90), mime="image/png"
            )
        )

    def test_without_perceptual_hashes_returns_none(self):
        self.client.generated_hashes = {"hash": "new"}
        self.client.relationships = {}

        self.assertIsNone(
            self.hydrus.find_exact_duplicate(
                BytesIO(b"data"), mime="image/jpeg"
            )
        )


if __name__ == "__main__":
    unittest.main()
