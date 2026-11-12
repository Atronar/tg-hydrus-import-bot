"""Тесты вспомогательных функций start_script.py."""

import os
import threading
import unittest
from io import BytesIO
from unittest.mock import AsyncMock, patch

from aiogram import exceptions

from start_script import (
    MAX_MESSAGE_LENGTH,
    answer_long_with_retry,
    answer_with_retry,
    download_with_retry,
    get_telegram_proxy,
    make_temp_file_path,
    remove_temp_file,
    run_hydrus,
)
from tests.helpers import FakeMessage, fake_bot_download, telegram_exception


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


class RunHydrusTests(unittest.IsolatedAsyncioTestCase):
    async def test_runs_in_dedicated_thread(self):
        thread_name = await run_hydrus(
            lambda: threading.current_thread().name
        )
        self.assertTrue(thread_name.startswith("hydrus"))

    async def test_passes_args_and_kwargs(self):
        self.assertEqual(await run_hydrus(lambda a, b=0: a + b, 1, b=2), 3)


if __name__ == "__main__":
    unittest.main()
