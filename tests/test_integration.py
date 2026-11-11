"""Опциональные интеграционные тесты с живыми Hydrus и Telegram.

Запуск:
    RUN_INTEGRATION=1 python run_tests.py

Без RUN_INTEGRATION тесты пропускаются, чтобы обычный прогон не зависел
от запущенного Hydrus и доступности api.telegram.org.
"""

import asyncio
import json
import os
import unittest
from pathlib import Path

import hydrus_api
from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession

from hydrus_requests import HydrusRequests
from start_script import get_telegram_proxy
from tests import PROJECT_ROOT

RUN_INTEGRATION = os.getenv("RUN_INTEGRATION") == "1"
CONFIG_PATH = PROJECT_ROOT / ".conf.json"


def load_live_config() -> dict:
    if not CONFIG_PATH.exists():
        raise unittest.SkipTest(f"Нет файла {CONFIG_PATH}")
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


@unittest.skipUnless(RUN_INTEGRATION, "нужен RUN_INTEGRATION=1")
class HydrusIntegrationTests(unittest.TestCase):
    def test_access_key_and_permissions(self):
        config = load_live_config()
        hydrus = HydrusRequests(
            config["HYDRUS_TOKEN"],
            api_url=config.get("HYDRUS_API_URL") or None,
            default_tags_namespace=config.get("TAGS_NAMESPACE"),
            default_destination_page_name=config.get("DESTINATION_PAGE_NAME"),
        )
        self.assertIsInstance(hydrus.permission_info, dict)
        self.assertTrue(hydrus.permissions)
        self.assertIn(1, [int(permission) for permission in hydrus.permissions])

    def test_client_verify_access_key(self):
        config = load_live_config()
        client = hydrus_api.Client(
            access_key=config["HYDRUS_TOKEN"],
            api_url=config.get("HYDRUS_API_URL") or hydrus_api.DEFAULT_API_URL,
        )
        info = client.verify_access_key()
        self.assertIn("basic_permissions", info)


@unittest.skipUnless(RUN_INTEGRATION, "нужен RUN_INTEGRATION=1")
class TelegramIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_me(self):
        config = load_live_config()
        proxy = get_telegram_proxy(config.get("TG_PROXY"))
        bot = Bot(
            token=config["TG_BOT_TOKEN"],
            session=AiohttpSession(proxy=proxy),
        )
        try:
            me = await asyncio.wait_for(bot.get_me(), timeout=30)
            self.assertTrue(me.is_bot)
            self.assertTrue(me.username)
        finally:
            await bot.session.close()


if __name__ == "__main__":
    unittest.main()
