from io import IOBase
from typing import Iterable, TypedDict
import requests


class HeaderIndex(TypedDict):
    status: int
    parent_id: int
    id: int
    results: int

class ResponseJSONHeader(TypedDict):
    user_id: str
    account_type: str
    short_limit: str
    long_limit: str
    long_remaining: int
    short_remaining: int
    status: int
    results_requested: int
    index: dict[str, HeaderIndex]
    search_depth: str
    minimum_similarity: float
    query_image_display: str
    query_image: str
    results_returned: int

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

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.db = 999
        self.minsim = 80

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
        params = {
            'output_type': self.OUTPUT_TYPE,
            'api_key': self.api_key,
            'db': self.db,
            'url': url
        }
        resp = requests.get(self.API_URL, params=params, timeout=60)
        return self.resp_handle(resp.json())

    def search_file(self, file: IOBase|bytes) -> list[Result]:
        """Поиск изображения с помощью сервиса

        Принимает файл в виде файлового объекта или в байтовом представлении

        Возвращается список словарей-результатов поиска
        """
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
        resp = requests.post(self.API_URL, params=params, files=files, timeout=60)
        return self.resp_handle(resp.json())

    def resp_handle(self, json_resp: ResponseJSON) -> list[Result]:
        """Обработка результатов запроса к сервису и возврат только списка результатов
        """
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
