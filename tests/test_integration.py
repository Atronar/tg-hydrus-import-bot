"""Опциональные интеграционные тесты с живыми Hydrus и Telegram.

По умолчанию тесты запускаются, если Hydrus и Telegram доступны.
Если сервис недоступен, соответствующий класс тестов пропускается.

Принудительный запуск (недоступность сервиса считается ошибкой):
    RUN_INTEGRATION=1 python run_tests.py

Отключение интеграционных тестов:
    SKIP_INTEGRATION=1 python run_tests.py
"""

import asyncio
import json
import os
import unittest
from pathlib import Path

import hydrus_api
import requests
from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramNetworkError

from hydrus_requests import HydrusRequests
from start_script import get_telegram_proxy
from tests import PROJECT_ROOT

CONFIG_PATH = PROJECT_ROOT / ".conf.json"
RUN_INTEGRATION = os.getenv("RUN_INTEGRATION")
SKIP_INTEGRATION = os.getenv("SKIP_INTEGRATION") == "1" or RUN_INTEGRATION == "0"
FORCE_INTEGRATION = RUN_INTEGRATION == "1"


class _TimeoutSession(requests.Session):
    """Сессия requests с таймаутом, чтобы проверка доступности не висела."""

    def request(self, *args, **kwargs):
        kwargs.setdefault("timeout", 5)
        return super().request(*args, **kwargs)


def load_live_config() -> dict:
    if not CONFIG_PATH.exists():
        raise unittest.SkipTest(f"Нет файла {CONFIG_PATH}")
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def make_hydrus_client(config: dict) -> hydrus_api.Client:
    return hydrus_api.Client(
        access_key=config["HYDRUS_TOKEN"],
        api_url=config.get("HYDRUS_API_URL") or hydrus_api.DEFAULT_API_URL,
        session=_TimeoutSession(),
    )


def make_bot(config: dict) -> Bot:
    proxy = get_telegram_proxy(config.get("TG_PROXY"))
    return Bot(
        token=config["TG_BOT_TOKEN"],
        session=AiohttpSession(proxy=proxy),
    )


async def _probe_telegram(config: dict) -> None:
    bot = make_bot(config)
    try:
        await asyncio.wait_for(bot.get_me(), timeout=10)
    finally:
        await bot.session.close()


class HydrusIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if SKIP_INTEGRATION:
            raise unittest.SkipTest("интеграционные тесты отключены")
        if FORCE_INTEGRATION:
            return
        try:
            config = load_live_config()
            make_hydrus_client(config).verify_access_key()
        except unittest.SkipTest:
            raise
        except hydrus_api.ConnectionError as ex:
            raise unittest.SkipTest(f"Hydrus недоступен: {ex}") from ex

    def test_access_key_and_permissions(self):
        config = load_live_config()
        hydrus = HydrusRequests(
            make_hydrus_client(config),
            default_tags_namespace=config.get("TAGS_NAMESPACE"),
            default_destination_page_name=config.get("DESTINATION_PAGE_NAME"),
        )
        self.assertIsInstance(hydrus.permission_info, dict)
        self.assertTrue(hydrus.permissions)
        self.assertIn(1, [int(permission) for permission in hydrus.permissions])

    def test_client_verify_access_key(self):
        config = load_live_config()
        info = make_hydrus_client(config).verify_access_key()
        self.assertIn("basic_permissions", info)


class TelegramIntegrationTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        if SKIP_INTEGRATION:
            raise unittest.SkipTest("интеграционные тесты отключены")
        if FORCE_INTEGRATION:
            return
        try:
            config = load_live_config()
            asyncio.run(_probe_telegram(config))
        except unittest.SkipTest:
            raise
        except (TelegramNetworkError, asyncio.TimeoutError, OSError) as ex:
            raise unittest.SkipTest(f"Telegram недоступен: {ex}") from ex

    async def test_get_me(self):
        config = load_live_config()
        bot = make_bot(config)
        try:
            me = await asyncio.wait_for(bot.get_me(), timeout=30)
            self.assertTrue(me.is_bot)
            self.assertTrue(me.username)
        finally:
            await bot.session.close()


if __name__ == "__main__":
    unittest.main()
