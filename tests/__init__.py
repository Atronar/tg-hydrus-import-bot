"""Общая настройка тестов.

Тесты не должны зависеть от рабочего .conf.json, поэтому для них
создаётся временный каталог с заведомо валидным конфигом.
"""

import atexit
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODULE_DIR = PROJECT_ROOT / "tg-hydrus-import-bot"

TEST_ROOT = Path(tempfile.mkdtemp(prefix="tg-hydrus-import-bot-tests-"))
TEST_CONFIG = {
    "LOG_LEVEL": "CRITICAL",
    "LOG_PATH": str(TEST_ROOT / "test.log"),
    "TG_ADMIN_ID": [1],
    "TG_BOT_TOKEN": "123456:" + "A" * 35,
    "TG_PROXY": "",
    "HYDRUS_TOKEN": "a" * 64,
    "HYDRUS_API_URL": "http://127.0.0.1:45869",
    "TIME_TO_SLEEP": 1,
    "TAGS_NAMESPACE": "test tags",
    "DESTINATION_PAGE_NAME": "test page",
    "TEMP_PATH": str(TEST_ROOT / "temp"),
    "CONTENT_TYPES": [
        "photo",
        "video",
        "animation",
        "video_note",
        "audio",
        "voice",
        "document",
        "text",
    ],
    "SAUCENAO_TOKEN": "",
}

(TEST_ROOT / ".conf.json").write_text(
    json.dumps(TEST_CONFIG, ensure_ascii=False, indent=2),
    encoding="utf-8",
)
Path(TEST_CONFIG["TEMP_PATH"]).mkdir(parents=True, exist_ok=True)

# Конфиг читается из текущего каталога, а модули лежат в каталоге с дефисом.
os.chdir(TEST_ROOT)
sys.path.insert(0, str(MODULE_DIR))

atexit.register(shutil.rmtree, TEST_ROOT, ignore_errors=True)

# Логи приложения в тестах не нужны.
from loguru import logger  # noqa: E402

logger.remove()
