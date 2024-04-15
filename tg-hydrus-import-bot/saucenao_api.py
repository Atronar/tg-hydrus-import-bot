from io import IOBase
import time
from typing import Iterable, NotRequired, TypedDict
from loguru import logger
import requests


class HeaderIndex(TypedDict):
    status: int
    parent_id: int
    id: int
    results: int

class ResponseJSONHeader(TypedDict):
    status: int
    # successed
    user_id: NotRequired[str]
    account_type: NotRequired[str]
    short_limit: NotRequired[str]
    long_limit: NotRequired[str]
    long_remaining: NotRequired[int]
    short_remaining: NotRequired[int]
    results_requested: NotRequired[int]
    index: NotRequired[dict[str, HeaderIndex]]
    search_depth: NotRequired[str]
    minimum_similarity: NotRequired[float]
    query_image_display: NotRequired[str]
    query_image: NotRequired[str]
    results_returned: NotRequired[int]
    # failed
    message: NotRequired[str]

class ResultHeader(TypedDict):
    similarity: str
    thumbnail: str
    index_id: int
    index_name: str
    dupes: int
    hidden: int

ResultData = dict[str, list[str]|str|int]

class Result(TypedDict):
    """Один из результатов поиска изображения
    """
    header: ResultHeader
    data: ResultData

class ResponseJSON(TypedDict):
    """Данные ответа запроса к SauceNAO
    """
    header: ResponseJSONHeader
    results: list[Result]

class SauceNAO:
    """Класс, позволяющий производить поиск с помощью SauceNAO
    Для инициализации объекта требуется ключ API с сайта
    """
    API_URL = "https://saucenao.com/search.php"
    OUTPUT_TYPE = 2

    def __init__(self, api_key: str, wait_daily_limit=False):
        self.api_key = api_key
        self.db = 999
        self.minsim = 80
        self.daily_limit_time = 0
        self.wait_daily_limit = wait_daily_limit

    def set_similarity(self, value: float):
        """Установка уровня минимального сходства
        Поиск будет возвращать только те результаты, которые имеют установленный уровень сходства
        """
        if value < 0:
            self.minsim = 0
        elif value > 100:
            self.minsim = 100
        else:
            self.minsim = value

    def search(self, file: str|IOBase|bytes) -> list[Result]:
        """Поиск изображения с помощью сервиса

        Может принимать как строку с адресом изображения в сети,
        так и сам файл в виде файлового объекта или в байтовом представлении

        Возвращается список словарей-результатов поиска
        """
        if isinstance(file, (IOBase, bytes)):
            return self.search_file(file)
        return self.search_url(file)

    def search_url(self, url: str) -> list[Result]:
        """Поиск изображения с помощью сервиса

        Принимает адрес изображения в сети

        Возвращается список словарей-результатов поиска
        """
        if (time_end_limit := self.daily_limit_time + 24*3600) > (now := time.time()):
            # Мы не можем проводить поиск, если достигнут лимит
            if self.wait_daily_limit:
                time.sleep(time_end_limit - now)
            else:
                return []

        params = {
            'output_type': self.OUTPUT_TYPE,
            'api_key': self.api_key,
            'db': self.db,
            'url': url
        }
        retry = True
        while retry:
            try:
                retry = False
                resp = requests.get(self.API_URL, params=params, timeout=60)
                return self.resp_handle(resp.json())
            except SauceNaoTooManyRequests as exc:
                logger.info(exc.message)
                if not exc.daily_limit:
                    # Упёрлись в 30-секундный лимит, ждём
                    retry = True
                    time.sleep(30)
                else:
                    # Упёрлись в суточный лимит, записываем время
                    self.daily_limit_time = time.time()
                if self.wait_daily_limit:
                    # Ждём окончания суточного лимита, если хотим ждать
                    retry = True
                    time.sleep(24*3600)
        return []

    def search_file(self, file: IOBase|bytes) -> list[Result]:
        """Поиск изображения с помощью сервиса

        Принимает файл в виде файлового объекта или в байтовом представлении

        Возвращается список словарей-результатов поиска
        """
        if (time_end_limit := self.daily_limit_time + 24*3600) > (now := time.time()):
            # Мы не можем проводить поиск, если достигнут лимит
            if self.wait_daily_limit:
                time.sleep(time_end_limit - now)
            else:
                return []

        params = {
            'output_type': self.OUTPUT_TYPE,
            'api_key': self.api_key,
            'db': self.db
        }
        if isinstance(file, IOBase):
            file_pos = file.tell()
            raw_file = file.read()
            file.seek(file_pos)
        else:
            raw_file = file
        files = {'file': ("image.png", raw_file)}
        retry = True
        while retry:
            try:
                retry = False
                resp = requests.post(self.API_URL, params=params, files=files, timeout=60)
                return self.resp_handle(resp.json())
            except SauceNaoTooManyRequests as exc:
                logger.info(exc.message)
                if not exc.daily_limit:
                    # Упёрлись в 30-секундный лимит, ждём
                    retry = True
                    time.sleep(30)
                else:
                    # Упёрлись в суточный лимит, записываем время
                    self.daily_limit_time = time.time()
                if self.wait_daily_limit:
                    # Ждём окончания суточного лимита, если хотим ждать
                    retry = True
                    time.sleep(24*3600)
        return []

    def resp_handle(self, json_resp: ResponseJSON) -> list[Result]:
        """Обработка результатов запроса к сервису и возврат только списка результатов
        """
        status = json_resp.get("header").get("status")
        if status == -2:
            raise SauceNaoTooManyRequests(json_resp.get("header").get("message", ""))
        elif status:
            header = json_resp.get("header")
            raise SauseNAOException(header.get("status"), header.get("message", ""))
        results = json_resp.get("results")
        results = [
            result
            for result in results
            if float(result.get("header").get("similarity")) > self.minsim
        ]
        return results

    def get_sources(self, file: str|IOBase|bytes) -> list[str]:
        """Получение списка ссылок-источников для заданного файла

        Может принимать как строку с адресом изображения в сети,
        так и сам файл в виде файлового объекта или в байтовом представлении

        Возвращается список строк с адресами страниц в сети
        """
        results = self.search(file)
        sources: list[str] = []
        for result in results:
            ext_urls = result.get("data").get("ext_urls", [])
            if isinstance(ext_urls, Iterable):
                sources.extend(ext_urls)
        return sources

class SauseNAOException(Exception):
    def __init__(self, status: int, message: str, *args: object):
        self.status = status
        self.message = message
        super().__init__(*args)
        
    def __str__(self) -> str:
        return f"{self.status}: {self.message}"
        
class SauceNaoTooManyRequests(SauseNAOException):
    def __init__(self, message: str, *args: object):
        self.daily_limit = False
        if "Daily Search Limit Exceeded" in message:
            self.daily_limit = True
        super().__init__(-2, message, *args)
