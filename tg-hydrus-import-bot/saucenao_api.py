﻿import asyncio
import hashlib
from io import BytesIO, IOBase
import time
from typing import Any, NotRequired, TypedDict
import urllib.parse
import aiohttp
import asyncache
import cachetools
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

class ResultData(TypedDict):
    author_name: NotRequired[str|None]
    author_url: NotRequired[str]
    anilist_id: NotRequired[int]
    as_project: NotRequired[str]
    company: NotRequired[str]
    creator: NotRequired[list[str]|str]
    creator_name: NotRequired[str]
    da_id: NotRequired[str] # but is int as string
    eng_name: NotRequired[str]
    est_time: NotRequired[str] # "00:12:59 / 00:23:20"
    ext_urls: NotRequired[list[str]]
    fa_id: NotRequired[int]
    getchu_id: NotRequired[str] # but is int as string
    id: NotRequired[str] # but is int as string
    jp_name: NotRequired[str]
    mal_id: NotRequired[int]
    member_id: NotRequired[int]
    member_name: NotRequired[str]
    mu_id: NotRequired[int]
    nijie_id: NotRequired[int]
    part: NotRequired[str] # but is int as string
    path: NotRequired[str]
    pixiv_id: NotRequired[int]
    published: NotRequired[str] # "2021-03-04T10:21:51.000Z"
    source: NotRequired[str]
    service: NotRequired[str]
    service_name: NotRequired[str]
    title: NotRequired[str]
    type: NotRequired[str]
    user_id: NotRequired[str] # but is int as string
    user_name: NotRequired[str]
    year: NotRequired[str] # but is int as string

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

class SauseNAOException(Exception):
    def __init__(self, status: int, message: str, *args: object):
        self.status = status
        self.message = message
        super().__init__(*args)

    def __str__(self) -> str:
        return f"{self.status}: {self.message}"

class SauceNaoTooManyRequests(SauseNAOException):
    def __init__(self, message: str, *args: object):
        self.daily_limit = "Daily Search Limit Exceeded" in message
        super().__init__(-2, message, *args)


class BaseSauceNAO:
    """Базовый класс с общими методами для дочерних SauceNAO и AsyncSauceNAO"""
    API_URL = "https://saucenao.com/search.php"
    OUTPUT_TYPE = 2
    MAX_RETRIES = 30
    RETRY_DELAY = 5
    TIMEOUT = 60
    _cache = cachetools.TTLCache(maxsize=100, ttl=604800)

    def __init__(
        self,
        api_key: str,
        wait_daily_limit=False,
        *,
        proxies: dict[str, str]|None = None,
        cache_size: int = 60,
        cache_ttl: int = 604800
    ):
        self.api_key = api_key
        self.db = 999
        self.minsim = 80
        self.daily_limit_time = 0
        self.wait_daily_limit = wait_daily_limit
        self.proxies = proxies
        self._cache = cachetools.TTLCache(maxsize=cache_size, ttl=cache_ttl)

    def _get_cache_key(self, file: str|IOBase|bytes):
        if isinstance(file, str):
            return ('url', urllib.parse.quote(file))
        content = self._read_file_content(file)
        return ('content', hashlib.md5(content).hexdigest())

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

    def set_proxies(self, proxies: dict[str, str]|None):
        """Установка прокси"""
        self.proxies = proxies

    def _build_params(self, **kwargs) -> dict[str, Any]:
        """Построение базовых параметров"""
        return {
            'output_type': self.OUTPUT_TYPE,
            'api_key': self.api_key,
            'db': self.db,
            **kwargs
        }

    def _read_file_content(self, file: IOBase|bytes) -> bytes:
        """Чтение файла с сохранением позиции"""
        if isinstance(file, BytesIO):
            return file.getvalue()
        if isinstance(file, IOBase):
            pos = file.tell()
            content = file.read()
            file.seek(pos)
            return content
        return file

    def resp_handle(self, json_resp: ResponseJSON) -> list[Result]:
        """Обработка результатов запроса к сервису и возврат только списка результатов
        """
        header = json_resp.get("header")
        status = header.get("status")

        if status == -2:
            raise SauceNaoTooManyRequests(header.get("message", ""))
        elif status:
            raise SauseNAOException(status, header.get("message", ""))

        results = [
            result
            for result in json_resp.get("results")
            if float(result.get("header").get("similarity")) > self.minsim
        ]
        return results

    def _get_sources_from_results(self, results: list[Result]) -> list[str]:
        """Получение списка ссылок-источников из списка результатов поиска

        Возвращается список строк с адресами страниц в сети
        """
        sources: list[str] = []
        for result in results:
            if (ext_urls := result.get("data").get("ext_urls")):
                sources.extend(ext_urls)
            if (getchu_id := result.get("data").get("getchu_id")):
                sources.append(f'http://www.getchu.com/soft.phtml?id={getchu_id}')
            if (
                result.get("header").get("index_id") == 18
                and (name := result.get("data").get("eng_name"))
            ): # nhentai
                sources.append(f'https://nhentai.net/search/?q={urllib.parse.quote(name)}')
            if (
                result.get("header").get("index_id") == 43
                and (service_id := result.get("data").get("service"))
                and (user_id := result.get("data").get("user_id"))
                and (post_id := result.get("data").get("id"))
            ): # kemono
                sources.append(f'https://kemono.su/{service_id}/user/{user_id}/post/{post_id}')
        return sources

class SauceNAO(BaseSauceNAO):
    """Класс, позволяющий производить поиск с помощью SauceNAO
    Для инициализации объекта требуется ключ API с сайта
    """

    def search(self, file: str|IOBase|bytes) -> list[Result]:
        """Поиск изображения с помощью сервиса

        Может принимать как строку с адресом изображения в сети,
        так и сам файл в виде файлового объекта или в байтовом представлении

        Возвращается список словарей-результатов поиска
        """
        if isinstance(file, (IOBase, bytes)):
            return self.search_file(file)
        return self.search_url(file)

    def _handle_rate_limit(self) -> float | None:
        """Проверка и обработка суточного лимита"""
        if (remaining_time := self.daily_limit_time + 24*3600 - time.time()) > 0:
            logger.warning(f"Достигнут дневной лимит запросов к SauceNAO")
            # Мы не можем проводить поиск, если достигнут лимит
            if self.wait_daily_limit:
                logger.info(f"Ожидание {remaining_time:.0f} секунд")
                time.sleep(remaining_time)
            else:
                return remaining_time
        return None

    def _make_request(self, method: str, **request_kwargs) -> ResponseJSON|None:
        """Общая логика выполнения запроса с обработкой повторов"""
        for attempt in range(self.MAX_RETRIES):
            is_slept = False
            try:
                resp = requests.request(
                    method,
                    self.API_URL,
                    **request_kwargs,
                    timeout = self.TIMEOUT,
                    proxies = self.proxies
                )
                resp.raise_for_status()
                return self._process_response(resp)
                
            except SauceNaoTooManyRequests as exc:
                logger.info(exc.message)
                if not exc.daily_limit:
                    # Упёрлись в 30-секундный лимит, ждём
                    time.sleep(30)
                    is_slept = True
                else:
                    # Упёрлись в суточный лимит, записываем время
                    self.daily_limit_time = time.time()
                if self._handle_rate_limit():
                    return None
            except requests.HTTPError as exc:
                logger.error(f"HTTP error {exc.response.status_code}: {exc}")
                if exc.response.status_code == 413:
                    return None
            except requests.RequestException as exc:
                logger.error(f"Request failed: {exc}")
            
            if not is_slept and attempt < self.MAX_RETRIES - 1:
                sleep_time = self.RETRY_DELAY * (attempt + 1)
                logger.info(f"Retrying in {sleep_time}s...")
                time.sleep(sleep_time)
        
        return None

    def _execute_search(self, method: str, **request_args) -> list[Result]:
        """Выполнение поискового запроса"""
        if resp := self._make_request(method, **request_args):
            return self.resp_handle(resp)
        return []

    def _process_response(self, response: requests.Response) -> ResponseJSON:
        """Обработка и валидация ответа"""
        try:
            json_data = response.json()
        except ValueError:
            raise ValueError("Invalid JSON response")
        
        if not isinstance(json_data, dict):
            raise TypeError("Unexpected response format")
        
        return ResponseJSON(**json_data)

    @cachetools.cached(BaseSauceNAO._cache, key=BaseSauceNAO._get_cache_key)
    def search_url(self, url: str) -> list[Result]:
        """Поиск изображения с помощью сервиса

        Принимает адрес изображения в сети

        Возвращается список словарей-результатов поиска
        """
        # Проверка на достижение дневного лимита и ожидание/завершение поиска
        if self._handle_rate_limit():
            return []

        params = self._build_params(url=url)
        return self._execute_search("GET", params=params)

    @cachetools.cached(BaseSauceNAO._cache, key=BaseSauceNAO._get_cache_key)
    def search_file(self, file: IOBase|bytes) -> list[Result]:
        """Поиск изображения с помощью сервиса

        Принимает файл в виде файлового объекта или в байтовом представлении

        Возвращается список словарей-результатов поиска
        """
        # Проверка на достижение дневного лимита и ожидание/завершение поиска
        if self._handle_rate_limit():
            return []

        params = self._build_params()
        files = {'file': ("image.png", self._read_file_content(file))}

        return self._execute_search("POST", params=params, files=files)

    def get_sources(self, file: str|IOBase|bytes) -> list[str]:
        """Получение списка ссылок-источников для заданного файла

        Может принимать как строку с адресом изображения в сети,
        так и сам файл в виде файлового объекта или в байтовом представлении

        Возвращается список строк с адресами страниц в сети
        """
        results = self.search(file)
        return self._get_sources_from_results(results)

class AsyncSauceNAO(BaseSauceNAO):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.session: aiohttp.ClientSession | None = None
        self._should_close_session = False

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
            self._should_close_session = True
        return self._session

    async def close(self):
        """Явное закрытие сессии"""
        if self._session and not self._session.closed:
            await self._session.close()
            self._should_close_session = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        if self._should_close_session:
            await self.close()

    async def search(self, file: str|IOBase|bytes) -> list[Result]:
        """Асинхронный поиск изображения с помощью сервиса

        Может принимать как строку с адресом изображения в сети,
        так и сам файл в виде файлового объекта или в байтовом представлении

        Возвращается список словарей-результатов поиска
        """
        if isinstance(file, (IOBase, bytes)):
            return await self.search_file(file)
        return await self.search_url(file)

    async def _handle_rate_limit(self) -> float | None:
        """Проверка и обработка суточного лимита"""
        if (remaining_time := self.daily_limit_time + 24*3600 - time.time()) > 0:
            logger.warning(f"Достигнут дневной лимит запросов к SauceNAO")
            # Мы не можем проводить поиск, если достигнут лимит
            if self.wait_daily_limit:
                logger.info(f"Ожидание {remaining_time:.0f} секунд")
                await asyncio.sleep(remaining_time)
            else:
                return remaining_time
        return None

    async def _make_request(self, method: str, **request_kwargs) -> ResponseJSON|None:
        """Общая логика выполнения запроса с обработкой повторов"""
        session = await self._get_session()
        proxy = (
            self.proxies.get(urllib.parse.urlparse(self.API_URL).scheme) or  # https
            next(iter(self.proxies.values()), None)  # Первое значение
        ) if self.proxies else None

        for attempt in range(self.MAX_RETRIES):
            try:
                async with session.request(
                    method,
                    self.API_URL,
                    **request_kwargs,
                    timeout=aiohttp.ClientTimeout(total=self.TIMEOUT),
                    proxy=proxy
                ) as resp:
                    resp.raise_for_status()
                    return await self._process_response(resp)

            except SauceNaoTooManyRequests as exc:
                logger.info(exc.message)
                if not exc.daily_limit:
                    # Упёрлись в 30-секундный лимит, ждём
                    await asyncio.sleep(30)
                    is_slept = True
                else:
                    # Упёрлись в суточный лимит, записываем время
                    self.daily_limit_time = time.time()
                if self._handle_rate_limit():
                    return None
            except aiohttp.ClientError as exc:
                logger.error(f"Request failed: {exc}")
            
            if not is_slept and attempt < self.MAX_RETRIES - 1:
                sleep_time = self.RETRY_DELAY * (attempt + 1)
                logger.info(f"Retrying in {sleep_time}s...")
                await asyncio.sleep(sleep_time)
        
        return None

    async def _execute_search(self, method: str, **request_args) -> list[Result]:
        """Выполнение поискового запроса"""
        if resp := await self._make_request(method, **request_args):
            return self.resp_handle(resp)
        return []

    async def _process_response(self, response: aiohttp.ClientResponse) -> ResponseJSON:
        """Обработка и валидация ответа"""
        try:
            json_data = await response.json()
        except ValueError:
            raise ValueError("Invalid JSON response")
        
        if not isinstance(json_data, dict):
            raise TypeError("Unexpected response format")
        
        return ResponseJSON(**json_data)

    @asyncache.cached(cache=BaseSauceNAO._cache, key=BaseSauceNAO._get_cache_key)
    async def search_url(self, url: str) -> list[Result]:
        """Асинхронный поиск изображения с помощью сервиса

        Принимает адрес изображения в сети

        Возвращается список словарей-результатов поиска
        """
        # Проверка на достижение дневного лимита и ожидание/завершение поиска
        if self._handle_rate_limit():
            return []

        params = self._build_params(url=url)
        return await self._execute_search("GET", params=params)
    
    @asyncache.cached(cache=BaseSauceNAO._cache, key=BaseSauceNAO._get_cache_key)
    async def search_file(self, file: IOBase|bytes) -> list[Result]:
        """Асинхронный поиск изображения с помощью сервиса

        Принимает файл в виде файлового объекта или в байтовом представлении

        Возвращается список словарей-результатов поиска
        """
        # Проверка на достижение дневного лимита и ожидание/завершение поиска
        if self._handle_rate_limit():
            return []

        params = self._build_params()
        data = aiohttp.FormData()
        data.add_field('file', self._read_file_content(file), filename="image.png")
        return await self._execute_search("POST", params=params, data=data)

    async def get_sources(self, file: str|IOBase|bytes) -> list[str]:
        """Получение списка ссылок-источников для заданного файла

        Может принимать как строку с адресом изображения в сети,
        так и сам файл в виде файлового объекта или в байтовом представлении

        Возвращается список строк с адресами страниц в сети
        """
        results = await self.search(file)
        return self._get_sources_from_results(results)
