"""
Управление конфигурацией
"""

import json
import os
from pathlib import Path
from typing import Annotated, Literal
import shutil
import sys

from pydantic import BaseModel, StringConstraints, Field, ValidationError

class ConfigModel(BaseModel):
    LOG_LEVEL: int|Literal["TRACE","DEBUG","INFO","SUCCESS","WARNING","ERROR","CRITICAL"]
    LOG_PATH: str
    TG_ADMIN_ID: list[Annotated[int, Field(ge=0)]]
    TG_BOT_TOKEN: Annotated[
        str, 
        StringConstraints(strip_whitespace=True, pattern=r'^\d+:\S{35}$')
    ]
    HYDRUS_TOKEN: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=64,
            max_length=64,
            pattern=r'^[\da-f]*$'
        )
    ]
    HYDRUS_API_URL: str
    TIME_TO_SLEEP: Annotated[float, Field(gt=0)]
    TAGS_NAMESPACE: str
    DESTINATION_PAGE_NAME: str
    TEMP_PATH: str
    CONTENT_TYPES: list[Literal[
        "photo",
        "video",
        "animation",
        "video_note",
        "audio",
        "voice",
        "document",
        "text"
    ]]
    SAUCENAO_TOKEN: Annotated[
        str,
        StringConstraints(strip_whitespace=True, pattern=r'^[\da-f]*$')
    ] = ""

__CONFPATH = ".conf.json"
"""Local path to json config"""

if not Path(__CONFPATH).exists():
    module_path = Path(__file__).absolute().parent.parent
    example_path = module_path / ".conf.json.example"
    shutil.copyfile(example_path, __CONFPATH)
    print("Создан новый конфиг. Заполните его и перезапустите бота!")
    sys.exit(0)

# Load configuration at runtime
try:
    with open(__CONFPATH, "r", encoding="utf-8") as f:
        CONF = ConfigModel(**json.load(f))
        """ Global configuration variable """
        CONF = CONF.model_copy(update={
            "TG_BOT_TOKEN": os.getenv("TG_BOT_TOKEN", CONF.TG_BOT_TOKEN),
            "HYDRUS_TOKEN": os.getenv("HYDRUS_TOKEN", CONF.HYDRUS_TOKEN),
            "SAUCENAO_TOKEN": os.getenv("SAUCENAO_TOKEN", CONF.SAUCENAO_TOKEN)
        })
except ValidationError as e:
    errors = "\n".join([f"- {err['loc'][0]}: {err['msg']}" for err in e.errors()])
    print(f"Ошибки в конфиге:\n{errors}")
    sys.exit(1)
