"""
Точка входа программы
"""

import sys
import asyncio

from loguru import logger

from config import CONF
from start_script import start_script
from tools import prepare_temp_folder

logger.add(
    "./logs/debug.log",
    format="{time} {level} {message}",
    level="DEBUG",
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
