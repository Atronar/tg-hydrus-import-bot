"""
Управление конфигурацией
"""

import json
import os
import re
from pathlib import Path
from typing import Annotated, Literal
import shutil
import sys

from pydantic import BaseModel, StringConstraints, Field, ValidationError, field_validator

_WINDOWS_ENV_PATTERN = re.compile(r"%([^%]+)%")

def _expand_path(value: str) -> str:
    """Раскрывает переменные окружения и ~ в путях.

    os.path.expandvars на Windows понимает и %VAR%, и $VAR; на POSIX —
    только $VAR, поэтому %VAR% раскрываем отдельно. Это позволяет
    использовать в конфиге привычный для Windows путь %TEMP%\\... и
    при этом не зависеть от текущей директории.
    """
    if not value:
        return value
    value = os.path.expandvars(value)
    if os.name != "nt":
        value = _WINDOWS_ENV_PATTERN.sub(
            lambda match: os.environ.get(match.group(1), match.group(0)),
            value,
        )
    return os.path.expanduser(value)

class ConfigModel(BaseModel):
    LOG_LEVEL: int|Literal["TRACE","DEBUG","INFO","SUCCESS","WARNING","ERROR","CRITICAL"]
    LOG_PATH: str
    TG_ADMIN_ID: list[Annotated[int, Field(ge=0)]]
    TG_BOT_TOKEN: Annotated[
        str, 
        StringConstraints(strip_whitespace=True, pattern=r'^\d+:\S{35}$')
    ]
    TG_PROXY: Annotated[str, StringConstraints(strip_whitespace=True)] = ""
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
    ALLOW_INSECURE_HYDRUS: bool = False
    DUPLICATE_MAX_DISTANCE: Annotated[int, Field(ge=0)] = 5
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

    @field_validator("LOG_PATH", "TEMP_PATH")
    @classmethod
    def _expand_path_fields(cls, value: str) -> str:
        return _expand_path(value)

class ConfigError(Exception):
    """Конфиг не удалось прочитать или разобрать."""


__CONFPATH = ".conf.json"
"""Local path to json config"""

if not Path(__CONFPATH).exists():
    module_path = Path(__file__).absolute().parent.parent
    example_path = module_path / ".conf.json.example"
    shutil.copyfile(example_path, __CONFPATH)
    print("Создан новый конфиг. Заполните его и перезапустите бота!")
    sys.exit(0)

_ENV_OVERRIDES = ("TG_BOT_TOKEN", "HYDRUS_TOKEN", "SAUCENAO_TOKEN")

def load_config(path: str|Path = __CONFPATH) -> ConfigModel:
    """Загружает конфиг и применяет переменные окружения до валидации."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as ex:
        raise ConfigError(f"Не удалось прочитать {path}: {ex}") from ex
    for name in _ENV_OVERRIDES:
        if name in os.environ:
            data[name] = os.environ[name]
    return ConfigModel(**data)

# Load configuration at runtime
try:
    CONF = load_config()
    """ Global configuration variable """
except ConfigError as e:
    print(f"Ошибка конфига: {e}")
    sys.exit(1)
except ValidationError as e:
    errors = "\n".join([f"- {err['loc'][0]}: {err['msg']}" for err in e.errors()])
    print(f"Ошибки в конфиге:\n{errors}")
    sys.exit(1)
