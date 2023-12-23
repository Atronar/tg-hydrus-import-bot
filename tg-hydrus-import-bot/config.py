"""
Управление конфигурацией
"""

import json
from typing import Any, Literal

CONF: dict[Literal[
    "LOG_LEVEL",
    "LOG_PATH",
    "TG_ADMIN_ID",
    "TG_BOT_TOKEN",
    "HYDRUS_TOKEN",
    "TIME_TO_SLEEP",
    "TAGS_NAMESPACE",
    "DESTINATION_PAGE_NAME",
    "TEMP_PATH",
    "CONTENT_TYPES",
], Any] = {}
""" Global configuration variable """

__CONFPATH = ".conf.json"
"""Local path to json config"""

# Load configuration at runtime
with open(__CONFPATH, "r", encoding="utf-8") as f:
    CONF = json.loads(f.read())
