"""Тесты модели конфигурации."""

import unittest

from pydantic import ValidationError

from config import ConfigModel
from tests import TEST_CONFIG


class ConfigModelTests(unittest.TestCase):
    def test_valid_config(self):
        config = ConfigModel(**TEST_CONFIG)
        self.assertEqual(config.TG_ADMIN_ID, [1])
        self.assertEqual(config.TAGS_NAMESPACE, "test tags")

    def test_invalid_bot_token(self):
        data = {**TEST_CONFIG, "TG_BOT_TOKEN": "invalid"}
        with self.assertRaises(ValidationError):
            ConfigModel(**data)

    def test_invalid_hydrus_token(self):
        data = {**TEST_CONFIG, "HYDRUS_TOKEN": "x" * 64}
        with self.assertRaises(ValidationError):
            ConfigModel(**data)

    def test_negative_admin_id(self):
        data = {**TEST_CONFIG, "TG_ADMIN_ID": [-1]}
        with self.assertRaises(ValidationError):
            ConfigModel(**data)

    def test_unknown_content_type(self):
        data = {**TEST_CONFIG, "CONTENT_TYPES": ["unknown"]}
        with self.assertRaises(ValidationError):
            ConfigModel(**data)

    def test_empty_saucenao_token_is_allowed(self):
        config = ConfigModel(**{**TEST_CONFIG, "SAUCENAO_TOKEN": ""})
        self.assertEqual(config.SAUCENAO_TOKEN, "")

    def test_proxy_defaults_to_empty(self):
        config = ConfigModel(**TEST_CONFIG)
        self.assertEqual(config.TG_PROXY, "")

    def test_proxy_is_stripped(self):
        config = ConfigModel(
            **{**TEST_CONFIG, "TG_PROXY": "  http://127.0.0.1:8118  "}
        )
        self.assertEqual(config.TG_PROXY, "http://127.0.0.1:8118")


if __name__ == "__main__":
    unittest.main()
