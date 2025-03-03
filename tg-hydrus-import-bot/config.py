﻿"""
Управление конфигурацией
"""

import json
import os
from typing import Annotated, Literal
import shutil

from pydantic import BaseModel, StringConstraints, Field, ValidationError

class ConfigModel(BaseModel):
    LOG_LEVEL: str|int
    LOG_PATH: str
    TG_ADMIN_ID: list[Annotated[int, Field(ge=0)]]
    TG_BOT_TOKEN: Annotated[str, StringConstraints(strip_whitespace=True, pattern=r'^\d+:\S{35}$')]
    HYDRUS_TOKEN: Annotated[str, StringConstraints(strip_whitespace=True, min_length=64, max_length=64)]
    HYDRUS_API_URL: str
    TIME_TO_SLEEP: Annotated[float, Field(gt=0)]
    TAGS_NAMESPACE: str
    DESTINATION_PAGE_NAME: str
    TEMP_PATH: str
    CONTENT_TYPES: list[Literal[ "photo", "video", "animation", "video_note", "audio", "voice", "document", "text" ]]
    SAUCENAO_TOKEN: str

__CONFPATH = ".conf.json"
"""Local path to json config"""

if not os.path.exists(__CONFPATH):
    module_path = os.path.dirname(os.path.dirname(__file__))
    shutil.copyfile(os.path.join(module_path, ".conf.json.example"), __CONFPATH)
    print("Создан новый конфиг. Заполните его и перезапустите бота!")
    exit(0)

# Load configuration at runtime
try:
    with open(__CONFPATH, "r", encoding="utf-8") as f:
        CONF = ConfigModel(**json.load(f))
        """ Global configuration variable """
except ValidationError as e:
    print(f"Ошибка в конфиге: {e}")
    exit(1)

# Опциональное переопределение секретов через окружение
CONF.TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", CONF.TG_BOT_TOKEN)
CONF.HYDRUS_TOKEN = os.getenv("HYDRUS_TOKEN", CONF.HYDRUS_TOKEN)
CONF.SAUCENAO_TOKEN = os.getenv("SAUCENAO_TOKEN", CONF.SAUCENAO_TOKEN)
