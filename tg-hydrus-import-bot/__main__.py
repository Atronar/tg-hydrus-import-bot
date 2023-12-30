"""
Точка входа программы
"""

import sys
import asyncio
import time
import urllib.parse

import hydrus_api
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
    """Точка входа в программу"""
    prepare_temp_folder()
    try:
        asyncio.run(start_script())
    except hydrus_api.DatabaseLocked as ex:
        if ex.response.status_code==503:
            parsed_url = urllib.parse.urlparse(ex.response.url)
            logger.info("Не удалось подключиться к Hydrus. "
                        f"Адрес подключения {parsed_url.scheme}://{parsed_url.netloc}")
            logger.debug(f"{ex.response.reason}: {ex.response.url}")
            logger.info(f"Программа приостановлена на {CONF['TIME_TO_SLEEP']} сек.")
            time.sleep(CONF['TIME_TO_SLEEP'])
        else:
            raise ex


while True:
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Программа остановлена пользователем.")
        sys.exit()
