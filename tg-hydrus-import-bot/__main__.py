"""
Точка входа программы
"""

import sys
import asyncio

from loguru import logger

from start_script import start_script
from tools import prepare_temp_folder
from config import CONF

logger.remove()
logger.add(
    sys.stderr,
    level=CONF["LOG_LEVEL"]
)
logger.add(
    CONF["LOG_PATH"],
    format="{time} {level} {message}",
    level=CONF["LOG_LEVEL"],
    rotation="1 week",
    compression="zip",
)

logger.info("Программа запущена.")

@logger.catch
def main():
    """Точка входа в программу
    """
    prepare_temp_folder()
    asyncio.run(start_script())


while True:
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Программа остановлена пользователем.")
        sys.exit()
