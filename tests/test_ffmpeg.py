"""Тесты выбора уровня логирования ffmpeg."""

import importlib
import unittest

import ffmpeg
from ffmpeg import _ffmpeg_loglevel


class FfmpegLoglevelTests(unittest.TestCase):
    def test_named_level_is_mapped(self):
        self.assertEqual(_ffmpeg_loglevel("TRACE"), "trace")
        self.assertEqual(_ffmpeg_loglevel("DEBUG"), "debug")
        self.assertEqual(_ffmpeg_loglevel("CRITICAL"), "fatal")

    def test_numeric_level_is_mapped(self):
        self.assertEqual(_ffmpeg_loglevel(5), "trace")
        self.assertEqual(_ffmpeg_loglevel(20), "info")
        self.assertEqual(_ffmpeg_loglevel(50), "fatal")

    def test_unknown_level_falls_back_to_info(self):
        self.assertEqual(_ffmpeg_loglevel(15), "info")
        self.assertEqual(_ffmpeg_loglevel("UNKNOWN"), "info")

    def test_module_imports_with_numeric_config_level(self):
        # Раньше числовой LOG_LEVEL ронял импорт модуля с KeyError.
        original = ffmpeg.CONF.LOG_LEVEL
        try:
            ffmpeg.CONF.LOG_LEVEL = 20
            importlib.reload(ffmpeg)
            self.assertIn("-loglevel info", ffmpeg._FFMPEG_GLOBAL_OPTIONS)
        finally:
            ffmpeg.CONF.LOG_LEVEL = original
            importlib.reload(ffmpeg)
