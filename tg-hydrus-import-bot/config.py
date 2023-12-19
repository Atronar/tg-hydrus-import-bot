"""
Управление конфигурацией
"""

import json
from typing import Any

CONF: dict[str, Any] = {}
""" Global configuration variable """

__CONFPATH = ".conf.json"
"""Local path to json config"""

# Load configuration at runtime
with open(__CONFPATH, "r", encoding="utf-8") as f:
    CONF = json.loads(f.read())
