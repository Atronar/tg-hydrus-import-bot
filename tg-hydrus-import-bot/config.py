"""
Управление конфигурацией
"""

import json
from typing import TypedDict

class ConfigDict(TypedDict):
    LOG_LEVEL: str|int
    LOG_PATH: str
    TG_ADMIN_ID: list[int]
    TG_BOT_TOKEN: str
    HYDRUS_TOKEN: str
    HYDRUS_API_URL: str
    TIME_TO_SLEEP: float
    TAGS_NAMESPACE: str
    DESTINATION_PAGE_NAME: str
    TEMP_PATH: str
    CONTENT_TYPES: list[str]
    SAUCENAO_TOKEN: str

__CONFPATH = ".conf.json"
"""Local path to json config"""

# Load configuration at runtime
with open(__CONFPATH, "r", encoding="utf-8") as f:
    CONF: ConfigDict = json.loads(f.read())
    """ Global configuration variable """
