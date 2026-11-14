"""Тесты вспомогательных функций start_script.py."""

import os
import threading
import time
import unittest
from io import BytesIO
from unittest.mock import AsyncMock, Mock, patch

from aiogram import exceptions
import hydrus_api
from hydrus_api_enums import HydrusPermission

from start_script import (
    DUPLICATE_PENDING_TTL,
    HELP_TEXT,
    MAX_MESSAGE_LENGTH,
    _PENDING_DUPLICATES,
    _duplicate_keyboard,
    _store_pending_duplicate,
    _take_pending_duplicate,
    answer_long_with_retry,
    answer_with_retry,
    apply_duplicate_action,
    download_with_retry,
    format_duplicate_details,
    import_file_or_prompt,
    import_urls_with_duplicates,
    get_telegram_proxy,
    handle_request_errors,
    make_temp_file_path,
    remove_temp_file,
    run_hydrus,
    _sauce_file_error,
    parse_hash_args,
)
from media_info import MediaInfo
from tg_parse_requests import MAX_PHOTO_SIZE
from tests.helpers import (
    FakeHydrusClient,
    FakeMessage,
    fake_bot_download,
    make_hydrus,
    telegram_exception,
)


class RetryMessage(FakeMessage):
    def __init__(self, failures: list[Exception]):
        super().__init__()
        self.failures = failures

    async def answer(self, text: str, **kwargs):
        if self.failures:
            raise self.failures.pop(0)
        return await super().answer(text, **kwargs)


class AnswerWithRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_success(self):
        msg = FakeMessage(message_id=5)
        await answer_with_retry(msg, "hello")
        self.assertEqual(msg.answers, [("hello", {"reply_to_message_id": 5})])

    async def test_retry_after(self):
        msg = RetryMessage([telegram_exception(exceptions.TelegramRetryAfter)])
        with patch("start_script.asyncio.sleep", new=AsyncMock()) as sleep:
            await answer_with_retry(msg, "hello")
        sleep.assert_awaited_once()
        self.assertEqual(len(msg.answers), 1)

    async def test_network_error(self):
        msg = RetryMessage([telegram_exception(exceptions.TelegramNetworkError)])
        with patch("start_script.asyncio.sleep", new=AsyncMock()) as sleep:
            await answer_with_retry(msg, "hello")
        sleep.assert_awaited_once()
        self.assertEqual(len(msg.answers), 1)

    async def test_long_message_is_split(self):
        msg = FakeMessage()
        await answer_long_with_retry(msg, "a" * (MAX_MESSAGE_LENGTH + 1))
        self.assertEqual(len(msg.answers), 2)
        self.assertEqual(len(msg.answers[0][0]), MAX_MESSAGE_LENGTH)
        self.assertEqual(msg.answers[1][0], "a")

    async def test_empty_message_is_not_sent(self):
        for text in ("", "   \n\t"):
            with self.subTest(text=text):
                msg = FakeMessage()
                await answer_long_with_retry(msg, text)
                self.assertEqual(msg.answers, [])

    async def test_bad_request_is_logged_and_not_raised(self):
        msg = RetryMessage([telegram_exception(exceptions.TelegramBadRequest)])
        self.assertIsNone(await answer_with_retry(msg, "hello"))
        self.assertEqual(msg.answers, [])

    async def test_bad_request_after_network_retry_is_not_raised(self):
        msg = RetryMessage(
            [
                telegram_exception(exceptions.TelegramNetworkError),
                telegram_exception(exceptions.TelegramBadRequest),
            ]
        )
        with patch("start_script.asyncio.sleep", new=AsyncMock()):
            self.assertIsNone(await answer_with_retry(msg, "hello"))
        self.assertEqual(msg.answers, [])

    async def test_repeated_retry_after_is_bounded(self):
        msg = RetryMessage(
            [
                telegram_exception(exceptions.TelegramRetryAfter)
                for _ in range(3)
            ]
        )
        with patch("start_script.asyncio.sleep", new=AsyncMock()) as sleep:
            with self.assertRaises(exceptions.TelegramRetryAfter):
                await answer_with_retry(msg, "hello")
        self.assertEqual(sleep.await_count, 2)

    async def test_long_message_via_answer_with_retry_is_split(self):
        msg = FakeMessage()
        await answer_with_retry(msg, "a" * (MAX_MESSAGE_LENGTH + 1))
        self.assertEqual(len(msg.answers), 2)


class DownloadWithRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_success(self):
        bot = fake_bot_download([b"content"])
        destination = BytesIO()
        await download_with_retry(bot, "file", destination)
        self.assertEqual(destination.getvalue(), b"content")
        self.assertEqual(bot.calls, 1)

    async def test_retry_resets_partial_data(self):
        bot = fake_bot_download(
            [telegram_exception(exceptions.TelegramNetworkError), b"content"]
        )
        destination = BytesIO()
        with patch("start_script.asyncio.sleep", new=AsyncMock()):
            await download_with_retry(bot, "file", destination)
        self.assertEqual(destination.getvalue(), b"content")
        self.assertEqual(bot.calls, 2)

    async def test_retry_after_resets_partial_data(self):
        bot = fake_bot_download(
            [telegram_exception(exceptions.TelegramRetryAfter), b"content"]
        )
        destination = BytesIO()
        with patch("start_script.asyncio.sleep", new=AsyncMock()):
            await download_with_retry(bot, "file", destination)
        self.assertEqual(destination.getvalue(), b"content")
        self.assertEqual(bot.calls, 2)

    async def test_repeated_network_error_is_bounded(self):
        bot = fake_bot_download(
            [
                telegram_exception(exceptions.TelegramNetworkError)
                for _ in range(3)
            ]
        )
        destination = BytesIO()
        with patch("start_script.asyncio.sleep", new=AsyncMock()):
            with self.assertRaises(exceptions.TelegramNetworkError):
                await download_with_retry(bot, "file", destination)
        self.assertEqual(bot.calls, 3)


class TelegramProxyTests(unittest.TestCase):
    def test_explicit_proxy_wins(self):
        with patch.dict(os.environ, {"HTTPS_PROXY": "http://env:8080"}, clear=True):
            self.assertEqual(
                get_telegram_proxy("socks5://127.0.0.1:1080"),
                "socks5://127.0.0.1:1080",
            )

    def test_environment_proxy(self):
        with (
            patch.dict(os.environ, {"HTTPS_PROXY": "http://env:8080"}, clear=True),
            patch("start_script.urllib.request.proxy_bypass", return_value=False),
        ):
            self.assertEqual(get_telegram_proxy(), "http://env:8080")

    def test_system_proxy(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch(
                "start_script.urllib.request.getproxies",
                return_value={"https": "http://127.0.0.1:8118"},
            ),
            patch("start_script.urllib.request.proxy_bypass", return_value=False),
        ):
            self.assertEqual(get_telegram_proxy(), "http://127.0.0.1:8118")

    def test_windows_registry_proxy_when_no_proxy_is_set(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("start_script.os.name", "nt"),
            patch("start_script.urllib.request.getproxies", return_value={}),
            patch(
                "start_script.urllib.request.getproxies_registry",
                return_value={"https": "http://127.0.0.1:8118"},
            ),
            patch("start_script.urllib.request.proxy_bypass", return_value=False),
        ):
            self.assertEqual(get_telegram_proxy(), "http://127.0.0.1:8118")

    def test_proxy_bypass_disables_proxy(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch(
                "start_script.urllib.request.getproxies",
                return_value={"https": "http://127.0.0.1:8118"},
            ),
            patch("start_script.urllib.request.proxy_bypass", return_value=True),
        ):
            self.assertIsNone(get_telegram_proxy())

    def test_no_proxy(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("start_script.urllib.request.getproxies", return_value={}),
            patch("start_script.urllib.request.getproxies_registry", return_value={}),
            patch("start_script.urllib.request.proxy_bypass", return_value=False),
        ):
            self.assertIsNone(get_telegram_proxy())


class TempFileTests(unittest.TestCase):
    def test_paths_are_unique_and_safe(self):
        first = make_temp_file_path("../a/b", "jpeg")
        second = make_temp_file_path("../a/b", "jpeg")
        self.assertNotEqual(first, second)
        self.assertTrue(first.endswith(".jpeg"))
        self.assertNotIn("..", os.path.basename(first))
        self.assertNotIn("/", os.path.basename(first))
        self.assertNotIn("\\", os.path.basename(first))

    def test_remove_temp_file(self):
        path = make_temp_file_path("file", "txt")
        with open(path, "w", encoding="utf-8") as file:
            file.write("data")
        remove_temp_file(path)
        self.assertFalse(os.path.exists(path))
        remove_temp_file(path)  # повторное удаление не должно падать


class ParseHashArgsTests(unittest.TestCase):
    def test_splits_hashes(self):
        self.assertEqual(parse_hash_args("abc def"), ["abc", "def"])

    def test_empty_args(self):
        self.assertEqual(parse_hash_args(None), [])
        self.assertEqual(parse_hash_args(""), [])


class SauceFileErrorTests(unittest.TestCase):
    def test_accepts_small_image(self):
        self.assertIsNone(
            _sauce_file_error(
                {"Content-Type": "image/jpeg", "Content-Length": "1024"}
            )
        )

    def test_rejects_non_image(self):
        self.assertEqual(
            _sauce_file_error({"Content-Type": "video/mp4"}),
            "файл не является изображением",
        )

    def test_rejects_missing_content_type(self):
        self.assertEqual(
            _sauce_file_error({}),
            "файл не является изображением",
        )

    def test_rejects_too_large_image(self):
        self.assertEqual(
            _sauce_file_error(
                {
                    "Content-Type": "image/png",
                    "Content-Length": str(MAX_PHOTO_SIZE + 1),
                }
            ),
            "файл слишком большой для SauceNAO",
        )

    def test_ignores_malformed_content_length(self):
        self.assertIsNone(
            _sauce_file_error(
                {"Content-Type": "image/png", "Content-Length": "unknown"}
            )
        )


class ImportUrlsWithDuplicatesTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _PENDING_DUPLICATES.clear()

    def tearDown(self):
        _PENDING_DUPLICATES.clear()

    @staticmethod
    async def _run_hydrus(func, *args, **kwargs):
        return func(*args, **kwargs)

    def _hydrus(self, statuses):
        hydrus = Mock()
        hydrus.add_files_from_urls_detailed.return_value = statuses
        hydrus.find_url_duplicates.return_value = None
        hydrus.get_file_info.return_value = {"hash": "info"}
        return hydrus

    async def test_new_file_without_duplicate_is_sent(self):
        hydrus = self._hydrus([
            ("https://example.com/a", {"status": 1, "hash": "new", "note": ""})
        ])
        resp, send_hashes = await import_urls_with_duplicates(
            hydrus,
            ["https://example.com/a"],
            tags=["tag"],
            priority_domains=set(),
            pending_prompts=[],
        )
        self.assertEqual(resp[0]["hash"], "new")
        self.assertEqual(send_hashes, ["new"])

    async def test_exact_existing_replaces_status(self):
        hydrus = self._hydrus([
            ("https://example.com/a", {"status": 1, "hash": "new", "note": ""})
        ])
        hydrus.find_url_duplicates.return_value = {
            "distance": 0,
            "hashes": ["existing"],
        }
        hydrus.resolve_url_duplicate.return_value = {
            "action": "kept_existing",
            "existing_hash": "existing",
        }
        with patch("start_script.run_hydrus", new=self._run_hydrus):
            resp, send_hashes = await import_urls_with_duplicates(
                hydrus,
                ["https://example.com/a"],
                tags=["tag"],
                priority_domains=set(),
                pending_prompts=[],
            )

        self.assertEqual(resp[0]["status"], 2)
        self.assertEqual(resp[0]["hash"], "existing")
        self.assertEqual(send_hashes, ["existing"])

    async def test_already_in_database_skips_duplicate_check(self):
        hydrus = self._hydrus([
            ("https://example.com/a", {"status": 2, "hash": "existing", "note": ""})
        ])
        with patch("start_script.run_hydrus", new=self._run_hydrus):
            resp, send_hashes = await import_urls_with_duplicates(
                hydrus,
                ["https://example.com/a"],
                tags=["tag"],
                priority_domains=set(),
                pending_prompts=[],
            )

        self.assertEqual(resp[0]["status"], 2)
        self.assertEqual(send_hashes, ["existing"])
        hydrus.find_url_duplicates.assert_not_called()

    async def test_pending_duplicate_is_not_sent_and_prompted(self):
        hydrus = self._hydrus([
            ("https://example.com/a", {"status": 1, "hash": "new", "note": ""})
        ])
        hydrus.find_url_duplicates.return_value = {
            "distance": 5,
            "hashes": ["existing"],
        }
        hydrus.resolve_url_duplicate.return_value = {"action": "pending"}
        pending_prompts: list[dict] = []
        with patch("start_script.run_hydrus", new=self._run_hydrus):
            resp, send_hashes = await import_urls_with_duplicates(
                hydrus,
                ["https://example.com/a"],
                tags=["tag"],
                priority_domains=set(),
                pending_prompts=pending_prompts,
            )

        self.assertEqual(resp[0]["status"], 1)
        self.assertEqual(send_hashes, [])
        self.assertEqual(len(pending_prompts), 1)
        self.assertEqual(pending_prompts[0]["url"], "https://example.com/a")
        self.assertIn("похож", resp[0]["note"])
        self.assertEqual(len(_PENDING_DUPLICATES), 1)

    async def test_unknown_status_then_new_file_is_checked(self):
        """После status 0 и повторного импорта новый файл проверяется."""
        client = FakeHydrusClient()
        client.url_file_statuses = {
            "https://example.com/a": [
                [{"status": 0, "hash": "dead_hash"}],
                [{"status": 1, "hash": "new_hash"}],
            ],
        }
        hydrus = make_hydrus(
            {
                HydrusPermission.URL_IMPORT_EDIT,
                HydrusPermission.PAGES,
            },
            client,
        )
        hydrus.find_url_duplicates = Mock(return_value=None)

        with (
            patch("start_script.run_hydrus", new=self._run_hydrus),
            patch("hydrus_requests.time.sleep"),
        ):
            resp, send_hashes = await import_urls_with_duplicates(
                hydrus,
                ["https://example.com/a"],
                tags=[],
                priority_domains=set(),
                pending_prompts=[],
            )

        self.assertEqual(resp[0]["status"], 1)
        self.assertEqual(resp[0]["hash"], "new_hash")
        self.assertEqual(send_hashes, ["new_hash"])
        hydrus.find_url_duplicates.assert_called_once()
        self.assertEqual(
            hydrus.find_url_duplicates.call_args.args[0],
            "new_hash",
        )
        self.assertEqual(len(client.new_page_payloads), 1)


class HandleRequestErrorsTests(unittest.IsolatedAsyncioTestCase):
    async def test_hydrus_error_is_reported_to_user(self):
        @handle_request_errors
        async def handler(msg):
            raise hydrus_api.HydrusAPIException("boom")

        msg = FakeMessage(message_id=7)
        await handler(msg)
        self.assertEqual(
            msg.answers,
            [("Ошибка Hydrus API. Смотри логи.", {"reply_to_message_id": 7})],
        )

    async def test_telegram_bad_request_is_reported_to_user(self):
        @handle_request_errors
        async def handler(msg):
            raise telegram_exception(exceptions.TelegramBadRequest)

        msg = FakeMessage(message_id=8)
        await handler(msg)
        self.assertEqual(
            msg.answers,
            [
                (
                    "Telegram отклонил ответ. Смотри логи.",
                    {"reply_to_message_id": 8},
                )
            ],
        )

    async def test_unexpected_error_is_reported_to_user(self):
        @handle_request_errors
        async def handler(msg):
            raise RuntimeError("boom")

        msg = FakeMessage(message_id=9)
        await handler(msg)
        self.assertEqual(
            msg.answers,
            [
                (
                    "Непредвиденная ошибка. Смотри логи.",
                    {"reply_to_message_id": 9},
                )
            ],
        )

    async def test_successful_handler_result_is_returned(self):
        @handle_request_errors
        async def handler(msg):
            return "ok"

        self.assertEqual(await handler(FakeMessage()), "ok")


class HelpTextTests(unittest.TestCase):
    def test_lists_all_commands(self):
        for command in (
            "/to_tags",
            "/to_hashtags",
            "/sauce_by_hash",
            "/tags_by_hash",
            "/hashtags_by_hash",
            "/get_hash",
        ):
            with self.subTest(command=command):
                self.assertIn(command, HELP_TEXT)


class RunHydrusTests(unittest.IsolatedAsyncioTestCase):
    async def test_runs_in_dedicated_thread(self):
        thread_name = await run_hydrus(
            lambda: threading.current_thread().name
        )
        self.assertTrue(thread_name.startswith("hydrus"))

    async def test_passes_args_and_kwargs(self):
        self.assertEqual(await run_hydrus(lambda a, b=0: a + b, 1, b=2), 3)


class DuplicatePromptTests(unittest.TestCase):
    def setUp(self):
        _PENDING_DUPLICATES.clear()

    def tearDown(self):
        _PENDING_DUPLICATES.clear()

    def test_formats_both_files_and_distance(self):
        pending = {
            "existing_hash": "existing",
            "new_hash": "new",
            "distance": 5,
            "url": "https://example.com/new",
            "existing_info": {
                "mime": "image/png",
                "size": 1024,
                "width": 100,
                "height": 80,
            },
            "new_info": {
                "mime": "image/jpeg",
                "size": 2048,
                "width": 90,
                "height": 70,
            },
        }

        text = format_duplicate_details(pending)

        self.assertIn("Похож в пределах 5.", text)
        self.assertIn("existing", text)
        self.assertIn("new", text)
        self.assertIn("image/png", text)
        self.assertIn("100×80", text)
        self.assertIn("https://example.com/new", text)

    def test_keyboard_contains_four_actions(self):
        keyboard = _duplicate_keyboard("token")
        buttons = [
            button
            for row in keyboard.inline_keyboard
            for button in row
        ]

        self.assertEqual(len(buttons), 4)
        self.assertEqual(
            [button.callback_data for button in buttons],
            [
                "dup:keep_existing:token",
                "dup:keep_new:token",
                "dup:both_alt:token",
                "dup:both_unrelated:token",
            ],
        )

    def test_pending_duplicate_is_returned_once(self):
        token = _store_pending_duplicate({"new_hash": "new"})

        self.assertEqual(
            _take_pending_duplicate(token)["new_hash"],
            "new",
        )
        self.assertIsNone(_take_pending_duplicate(token))

    def test_expired_pending_duplicate_is_removed(self):
        token = _store_pending_duplicate({"new_hash": "new"})
        _PENDING_DUPLICATES[token]["created_at"] = (
            time.time() - DUPLICATE_PENDING_TTL - 1
        )

        self.assertIsNone(_take_pending_duplicate(token))
        self.assertNotIn(token, _PENDING_DUPLICATES)


class FileUploadDuplicateFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _PENDING_DUPLICATES.clear()

    def tearDown(self):
        _PENDING_DUPLICATES.clear()

    async def test_near_duplicate_creates_prompt_without_import(self):
        client = FakeHydrusClient()
        client.metadata["existing"] = {
            "is_local": True,
            "mime": "image/png",
            "width": 100,
            "height": 80,
        }
        hydrus = make_hydrus(
            {
                HydrusPermission.FILES_SEARCH_FETCH,
                HydrusPermission.TAGS_EDIT,
            },
            client,
        )
        prompts: list[dict] = []

        with patch.object(
            hydrus,
            "find_upload_duplicate",
            return_value={
                "kind": "near",
                "distance": 0,
                "hashes": ["existing"],
                "new_info": MediaInfo("image", 4, 100, 80),
            },
        ):
            statuses, keep_temp = await import_file_or_prompt(
                hydrus,
                BytesIO(b"data"),
                mime="image/jpeg",
                tags=["tag"],
                pending_prompts=prompts,
            )

        self.assertFalse(keep_temp)
        self.assertEqual(statuses[0]["hash"], "existing")
        self.assertEqual(len(prompts), 1)
        self.assertEqual(prompts[0]["new_content"], b"data")
        self.assertNotIn("add_file", [call[0] for call in client.calls])

    async def test_keep_existing_does_not_import_new_file(self):
        client = FakeHydrusClient()
        client.cleaned_tags = ["tag"]
        hydrus = make_hydrus(
            {
                HydrusPermission.FILES_IMPORT_DELETE,
                HydrusPermission.TAGS_EDIT,
                HydrusPermission.RELATIONSHIPS_EDIT,
            },
            client,
        )
        pending = {
            "existing_hash": "existing",
            "new_hash": None,
            "tags": ["tag"],
            "new_content": b"data",
            "new_path": None,
        }

        result = await apply_duplicate_action(
            hydrus,
            pending,
            "keep_existing",
        )

        self.assertIn("новый не импортирован", result)
        self.assertIn(
            ("add_tags", (["existing"], {"tag_service_key": ["tag"]})),
            client.calls,
        )
        self.assertNotIn("add_file", [call[0] for call in client.calls])

    async def test_keep_new_imports_and_deletes_existing(self):
        client = FakeHydrusClient()
        client.cleaned_tags = ["tag"]
        hydrus = make_hydrus(
            {
                HydrusPermission.FILES_IMPORT_DELETE,
                HydrusPermission.TAGS_EDIT,
                HydrusPermission.RELATIONSHIPS_EDIT,
            },
            client,
        )
        pending = {
            "existing_hash": "existing",
            "new_hash": None,
            "tags": ["tag"],
            "new_content": b"data",
            "new_path": None,
        }

        result = await apply_duplicate_action(
            hydrus,
            pending,
            "keep_new",
        )

        self.assertIn("Оставлен новый файл", result)
        payload = client.duplicate_relationship_payloads[-1][0]
        self.assertEqual(payload["hash_a"], "file_hash")
        self.assertEqual(payload["hash_b"], "existing")
        self.assertTrue(payload["delete_b"])


if __name__ == "__main__":
    unittest.main()
