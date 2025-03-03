"""
Управление конфигурацией
"""

import json
from typing import Annotated, Literal
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
    CONTENT_TYPES: list[Literal[ "photo", "video", "animation", "video_note", "document", "text" ]]
    SAUCENAO_TOKEN: str

__CONFPATH = ".conf.json"
"""Local path to json config"""

# Load configuration at runtime
try:
    with open(__CONFPATH, "r", encoding="utf-8") as f:
        CONF = ConfigModel(**json.load(f))
        """ Global configuration variable """
except ValidationError as e:
    print(f"Ошибка в конфиге: {e}")
    exit(1)
