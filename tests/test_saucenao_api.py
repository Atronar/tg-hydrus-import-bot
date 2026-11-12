"""Тесты разбора ответов SauceNAO без сетевых запросов."""

import unittest
from io import BytesIO
from unittest.mock import Mock, patch

import requests
from loguru import logger

from saucenao_api import (
    SauceNAO,
    SauceNAOError,
    SauceNaoTooManyRequests,
    SauseNAOException,
    _redact_secrets,
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

    def test_cache_key_includes_account_and_settings(self):
        sauce = SauceNAO("secret")
        first = sauce._cache_key_for_url("https://example.com")
        sauce.set_similarity(50)
        second = sauce._cache_key_for_url("https://example.com")
        sauce.db = 1
        third = sauce._cache_key_for_url("https://example.com")
        self.assertNotEqual(first, second)
        self.assertNotEqual(second, third)


def make_http_response(status_code: int, *, headers=None, json_data=None):
    response = Mock()
    response.status_code = status_code
    response.headers = headers or {}
    response.json.return_value = json_data or {
        "header": {"status": 0},
        "results": [],
    }
    if status_code >= 400:
        response.raise_for_status.side_effect = requests.HTTPError(
            response=response
        )
    return response


class RequestRetryTests(unittest.TestCase):
    def setUp(self):
        self.sauce = SauceNAO("key")

    def test_retry_429_uses_retry_after(self):
        responses = [
            make_http_response(429, headers={"Retry-After": "0"}),
            make_http_response(200),
        ]
        with (
            patch.object(
                self.sauce.session, "request", side_effect=responses
            ) as request,
            patch("saucenao_api.time.sleep") as sleep,
        ):
            self.assertEqual(self.sauce._execute_search("GET"), [])

        self.assertEqual(request.call_count, 2)
        sleep.assert_called_once_with(0.0)

    def test_retry_after_is_capped(self):
        responses = [
            make_http_response(429, headers={"Retry-After": "3600"}),
            make_http_response(200),
        ]
        with (
            patch.object(
                self.sauce.session, "request", side_effect=responses
            ),
            patch("saucenao_api.time.sleep") as sleep,
        ):
            self.assertEqual(self.sauce._execute_search("GET"), [])

        sleep.assert_called_once_with(self.sauce.MAX_RETRY_DELAY)

    def test_retry_5xx_then_success(self):
        responses = [
            make_http_response(503),
            make_http_response(200),
        ]
        with (
            patch.object(
                self.sauce.session, "request", side_effect=responses
            ) as request,
            patch("saucenao_api.time.sleep") as sleep,
        ):
            self.assertEqual(self.sauce._execute_search("GET"), [])

        self.assertEqual(request.call_count, 2)
        sleep.assert_called_once_with(2.0)

    def test_does_not_retry_client_error(self):
        response = make_http_response(400)
        with (
            patch.object(
                self.sauce.session, "request", return_value=response
            ) as request,
            patch("saucenao_api.time.sleep") as sleep,
        ):
            with self.assertRaises(SauceNAOError):
                self.sauce._execute_search("GET")

        self.assertEqual(request.call_count, 1)
        sleep.assert_not_called()

    def test_daily_limit_does_not_sleep(self):
        response = make_http_response(
            200,
            json_data={
                "header": {
                    "status": -2,
                    "message": "Daily Search Limit Exceeded",
                },
                "results": [],
            },
        )
        with (
            patch.object(
                self.sauce.session, "request", return_value=response
            ),
            patch("saucenao_api.time.sleep") as sleep,
        ):
            with self.assertRaises(SauceNAOError):
                self.sauce._execute_search("GET")

        self.assertGreater(self.sauce.daily_limit_time, 0)
        sleep.assert_not_called()

    def test_search_raises_on_failure(self):
        with patch.object(
            self.sauce,
            "_execute_search",
            side_effect=SauceNAOError("boom"),
        ):
            with self.assertRaises(SauceNAOError):
                self.sauce.search_file(b"data")

    def test_get_sources_raises_on_failure(self):
        with patch.object(
            self.sauce,
            "search",
            side_effect=SauceNAOError("boom"),
        ):
            with self.assertRaises(SauceNAOError):
                self.sauce.get_sources(b"data")

    def test_cache_is_per_instance_and_not_shared(self):
        first = SauceNAO("key-1")
        with patch.object(
            first, "_execute_search", return_value=[make_result()]
        ) as execute:
            first.search_file(b"data")
            first.search_file(b"data")
        self.assertEqual(execute.call_count, 1)

        second = SauceNAO("key-2")
        with patch.object(
            second, "_execute_search", return_value=[]
        ) as execute:
            second.search_file(b"data")
        self.assertEqual(execute.call_count, 1)


class SecretRedactionTests(unittest.TestCase):
    def test_redacts_api_key_in_url(self):
        text = (
            "GET https://saucenao.com/search.php"
            "?output_type=2&api_key=deadbeef&db=999"
        )
        redacted = _redact_secrets(text)
        self.assertNotIn("deadbeef", redacted)
        self.assertIn("api_key=***", redacted)
        self.assertIn("db=999", redacted)

    def test_request_error_log_does_not_contain_key(self):
        sauce = SauceNAO("secret-key")
        messages: list[str] = []
        sink = logger.add(
            lambda message: messages.append(message.record["message"]),
            level="DEBUG",
        )
        try:
            with (
                patch.object(
                    sauce.session,
                    "request",
                    side_effect=requests.ConnectionError(
                        "failed: https://saucenao.com/search.php"
                        "?api_key=secret-key&db=999"
                    ),
                ),
                patch("saucenao_api.time.sleep"),
            ):
                with self.assertRaises(SauceNAOError):
                    sauce._execute_search("GET")
        finally:
            logger.remove(sink)

        output = "\n".join(messages)
        self.assertNotIn("secret-key", output)
        self.assertIn("api_key=***", output)


if __name__ == "__main__":
    unittest.main()
