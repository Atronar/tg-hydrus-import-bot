"""Тесты модели конфигурации."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from config import ConfigModel, load_config
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


class PathExpansionTests(unittest.TestCase):
    def test_percent_environment_variable_is_expanded(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(os.environ, {"HYDRUS_TEST_DIR": temp_dir}):
                config = ConfigModel(
                    **{
                        **TEST_CONFIG,
                        "TEMP_PATH": "%HYDRUS_TEST_DIR%/temp",
                        "LOG_PATH": "%HYDRUS_TEST_DIR%/logs/debug.log",
                    }
                )
            self.assertEqual(Path(config.TEMP_PATH), Path(temp_dir) / "temp")
            self.assertEqual(
                Path(config.LOG_PATH),
                Path(temp_dir) / "logs" / "debug.log",
            )

    def test_dollar_environment_variable_is_expanded(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(os.environ, {"HYDRUS_TEST_DIR": temp_dir}):
                config = ConfigModel(
                    **{**TEST_CONFIG, "TEMP_PATH": "$HYDRUS_TEST_DIR/temp"}
                )
            self.assertEqual(Path(config.TEMP_PATH), Path(temp_dir) / "temp")

    def test_unknown_environment_variable_is_kept(self):
        config = ConfigModel(
            **{**TEST_CONFIG, "TEMP_PATH": "%HYDRUS_UNKNOWN_VAR%/temp"}
        )
        self.assertEqual(config.TEMP_PATH, "%HYDRUS_UNKNOWN_VAR%/temp")


class LoadConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "config.json"
        self.path.write_text(
            json.dumps(TEST_CONFIG),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_env_override_is_validated(self):
        with patch.dict(os.environ, {"TG_BOT_TOKEN": "invalid"}):
            with self.assertRaises(ValidationError):
                load_config(self.path)

    def test_valid_env_override_is_applied(self):
        token = "999999:" + "B" * 35
        with patch.dict(os.environ, {"TG_BOT_TOKEN": token}):
            config = load_config(self.path)
        self.assertEqual(config.TG_BOT_TOKEN, token)


if __name__ == "__main__":
    unittest.main()
