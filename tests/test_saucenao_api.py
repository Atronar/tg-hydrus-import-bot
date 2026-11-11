"""Тесты разбора ответов SauceNAO без сетевых запросов."""

import unittest
from io import BytesIO
from unittest.mock import patch

from saucenao_api import (
    SauceNAO,
    SauceNaoTooManyRequests,
    SauseNAOException,
)


def make_result(
    *,
    similarity: str = "95.0",
    index_id: int = 0,
    data: dict | None = None,
) -> dict:
    return {
        "header": {
            "similarity": similarity,
            "thumbnail": "",
            "index_id": index_id,
            "index_name": "test",
            "dupes": 0,
            "hidden": 0,
        },
        "data": data or {},
    }


class ResponseHandlingTests(unittest.TestCase):
    def setUp(self):
        self.sauce = SauceNAO("key")
        self.sauce.set_similarity(80)

    def test_filters_by_similarity(self):
        response = {
            "header": {"status": 0},
            "results": [
                make_result(similarity="90.0"),
                make_result(similarity="70.0"),
            ],
        }
        self.assertEqual(len(self.sauce.resp_handle(response)), 1)

    def test_too_many_requests(self):
        response = {
            "header": {"status": -2, "message": "Daily Search Limit Exceeded"},
            "results": [],
        }
        with self.assertRaises(SauceNaoTooManyRequests) as ctx:
            self.sauce.resp_handle(response)
        self.assertTrue(ctx.exception.daily_limit)

    def test_other_error(self):
        response = {
            "header": {"status": 1, "message": "error"},
            "results": [],
        }
        with self.assertRaises(SauseNAOException):
            self.sauce.resp_handle(response)


class SourcesTests(unittest.TestCase):
    def setUp(self):
        self.sauce = SauceNAO("key")

    def test_ext_urls(self):
        results = [make_result(data={"ext_urls": ["https://a", "https://b"]})]
        self.assertEqual(
            self.sauce._get_sources_from_results(results),
            ["https://a", "https://b"],
        )

    def test_getchu(self):
        results = [make_result(data={"getchu_id": "123"})]
        self.assertEqual(
            self.sauce._get_sources_from_results(results),
            ["http://www.getchu.com/soft.phtml?id=123"],
        )

    def test_nhentai(self):
        results = [
            make_result(index_id=18, data={"eng_name": "some name"})
        ]
        self.assertEqual(
            self.sauce._get_sources_from_results(results),
            ["https://nhentai.net/search/?q=some%20name"],
        )

    def test_kemono(self):
        results = [
            make_result(
                index_id=43,
                data={"service": "patreon", "user_id": "1", "id": "2"},
            )
        ]
        self.assertEqual(
            self.sauce._get_sources_from_results(results),
            ["https://kemono.su/patreon/user/1/post/2"],
        )

    def test_get_sources_uses_search(self):
        results = [make_result(data={"ext_urls": ["https://a"]})]
        with patch.object(self.sauce, "search", return_value=results):
            self.assertEqual(self.sauce.get_sources(b"data"), ["https://a"])


class SettingsTests(unittest.TestCase):
    def test_similarity_is_clamped(self):
        sauce = SauceNAO("key")
        sauce.set_similarity(-1)
        self.assertEqual(sauce.minsim, 0)
        sauce.set_similarity(101)
        self.assertEqual(sauce.minsim, 100)
        sauce.set_similarity(50)
        self.assertEqual(sauce.minsim, 50)

    def test_cache_key_for_url(self):
        sauce = SauceNAO("key")
        self.assertEqual(
            sauce._get_cache_key("https://example.com/a b"),
            ("url", "https%3A//example.com/a%20b"),
        )

    def test_cache_key_for_content(self):
        sauce = SauceNAO("key")
        first = sauce._get_cache_key(b"data")
        second = sauce._get_cache_key(BytesIO(b"data"))
        self.assertEqual(first, second)
        self.assertEqual(first[0], "content")

    def test_build_params_contains_api_key_and_db(self):
        sauce = SauceNAO("secret")
        params = sauce._build_params(url="https://example.com")
        self.assertEqual(params["api_key"], "secret")
        self.assertEqual(params["db"], 999)
        self.assertEqual(params["url"], "https://example.com")


if __name__ == "__main__":
    unittest.main()
