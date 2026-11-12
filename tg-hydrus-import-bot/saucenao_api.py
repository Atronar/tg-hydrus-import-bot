import hashlib
from io import BytesIO, IOBase
import re
import threading
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, NotRequired, TypedDict
import urllib.parse
import cachetools
from loguru import logger
import requests


_SECRET_PATTERN = re.compile(r"(api_key=)[^&\s\"']+", re.IGNORECASE)


def _redact_secrets(text: str) -> str:
    """Скрывает API-ключ в тексте исключений и логов."""
    return _SECRET_PATTERN.sub(r"\1***", text)


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


class SauceNAOError(SauseNAOException):
    """Не удалось выполнить запрос к SauceNAO.

    Отличает сбой (сеть, лимиты, некорректный ответ) от валидного
    ответа без подходящих результатов: пустой список означает,
    что поиск прошёл, но ничего не найдено.
    """
    def __init__(self, message: str, *args: object):
        super().__init__(-1, message, *args)


class BaseSauceNAO:
    """Базовый класс с общими методами для SauceNAO"""
    API_URL = "https://saucenao.com/search.php"
    OUTPUT_TYPE = 2
    # Короткие повторы: 2, 4, 8, 16 c. Дольше держать обработчик бессмысленно.
    MAX_RETRIES = 5
    RETRY_DELAY = 2.0
    MAX_RETRY_DELAY = 30.0
    SHORT_LIMIT_DELAY = 30.0
    TIMEOUT = 60

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
        # Оставлен для совместимости: суточный лимит больше не блокирует поток.
        self.wait_daily_limit = wait_daily_limit
        self.proxies = proxies
        self._cache = cachetools.TTLCache(maxsize=cache_size, ttl=cache_ttl)
        self._cache_lock = threading.Lock()
        self.session = requests.Session()
        self._session_lock = threading.Lock()

    def _get_cache_key(self, file: str|IOBase|bytes):
        if isinstance(file, str):
            return ('url', urllib.parse.quote(file))
        content = self._read_file_content(file)
        return ('content', hashlib.md5(content).hexdigest())

    def _cache_scope(self) -> tuple[str, int, float]:
        """Общая часть ключа кэша: результаты разных аккаунтов/настроек не смешиваются."""
        return (
            hashlib.sha256(self.api_key.encode()).hexdigest(),
            self.db,
            self.minsim,
        )

    def _cache_key_for_url(self, url: str):
        return (*self._cache_scope(), 'url', urllib.parse.quote(url))

    def _cache_key_for_content(self, content: bytes):
        return (*self._cache_scope(), 'content', hashlib.md5(content).hexdigest())

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
        """Проверка суточного лимита.

        Не спит сутками: обработчик Telegram не должен зависать на 24 часа.
        """
        if (remaining_time := self.daily_limit_time + 24*3600 - time.time()) > 0:
            logger.warning(
                "Достигнут дневной лимит SauceNAO, "
                f"осталось {remaining_time:.0f} c"
            )
            return remaining_time
        return None

    @staticmethod
    def _retry_after(response: requests.Response) -> float | None:
        """Разбирает Retry-After (секунды или HTTP-дата)."""
        value = response.headers.get("Retry-After")
        if not value:
            return None
        try:
            return max(0.0, float(value))
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(value)
            except (TypeError, ValueError):
                return None
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            return max(
                0.0,
                (retry_at - datetime.now(timezone.utc)).total_seconds()
            )

    def _backoff(self, attempt: int) -> float:
        return min(self.RETRY_DELAY * (2 ** attempt), self.MAX_RETRY_DELAY)

    def _execute_search(
        self,
        method: str,
        **request_args
    ) -> list[Result]:
        """Выполняет запрос, повторяя только 429/5xx и сетевые ошибки.

        Пустой список — валидный ответ без подходящих результатов.
        При сбое возбуждается SauceNAOError, чтобы вызывающий код мог
        отличить ошибку от «ничего не найдено».
        """
        for attempt in range(self.MAX_RETRIES):
            try:
                with self._session_lock:
                    response = self.session.request(
                        method,
                        self.API_URL,
                        **request_args,
                        timeout=self.TIMEOUT,
                        proxies=self.proxies
                    )

                if response.status_code == 429:
                    delay = self._retry_after(response)
                    if delay is None:
                        delay = self.SHORT_LIMIT_DELAY
                    # Не спим часами в обработчике: даже Retry-After ограничиваем.
                    delay = min(delay, self.MAX_RETRY_DELAY)
                    if attempt + 1 < self.MAX_RETRIES:
                        logger.info(f"SauceNAO 429, повтор через {delay:.0f} c")
                        time.sleep(delay)
                        continue
                    logger.error("SauceNAO: исчерпаны повторы после 429")
                    raise SauceNAOError("исчерпаны повторы после 429")

                if 500 <= response.status_code < 600:
                    if attempt + 1 < self.MAX_RETRIES:
                        delay = self._backoff(attempt)
                        logger.info(
                            f"SauceNAO {response.status_code}, "
                            f"повтор через {delay:.0f} c"
                        )
                        time.sleep(delay)
                        continue
                    logger.error(
                        f"SauceNAO: сервер вернул {response.status_code}"
                    )
                    raise SauceNAOError(
                        f"сервер вернул {response.status_code}"
                    )

                if response.status_code == 413:
                    logger.error("SauceNAO: файл слишком большой")
                    raise SauceNAOError("файл слишком большой")

                response.raise_for_status()
                return self.resp_handle(self._process_response(response))

            except SauceNaoTooManyRequests as exc:
                if exc.daily_limit:
                    self.daily_limit_time = time.time()
                    logger.warning(_redact_secrets(exc.message))
                    raise SauceNAOError("исчерпан дневной лимит") from exc
                if attempt + 1 < self.MAX_RETRIES:
                    logger.info(
                        f"SauceNAO: короткий лимит, "
                        f"повтор через {self.SHORT_LIMIT_DELAY:.0f} c"
                    )
                    time.sleep(self.SHORT_LIMIT_DELAY)
                    continue
                raise SauceNAOError("превышен лимит запросов") from exc
            except SauceNAOError:
                raise
            except SauseNAOException as exc:
                message = _redact_secrets(str(exc))
                logger.error(f"SauceNAO API error: {message}")
                raise SauceNAOError(message) from exc
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response else "?"
                logger.error(
                    f"SauceNAO HTTP error {status}: {_redact_secrets(str(exc))}"
                )
                raise SauceNAOError(f"HTTP {status}") from exc
            except requests.RequestException as exc:
                logger.error(
                    f"SauceNAO request failed: {_redact_secrets(str(exc))}"
                )
                if attempt + 1 < self.MAX_RETRIES:
                    delay = self._backoff(attempt)
                    logger.info(f"Повтор SauceNAO через {delay:.0f} c")
                    time.sleep(delay)
                    continue
                raise SauceNAOError("сетевая ошибка") from exc
            except (TypeError, ValueError, AttributeError, KeyError) as exc:
                logger.error(
                    f"SauceNAO invalid response: {_redact_secrets(str(exc))}"
                )
                raise SauceNAOError("некорректный ответ") from exc

        raise SauceNAOError("не удалось выполнить запрос")

    def _process_response(self, response: requests.Response) -> ResponseJSON:
        """Обработка и валидация ответа"""
        try:
            json_data = response.json()
        except ValueError:
            raise ValueError("Invalid JSON response")
        
        if not isinstance(json_data, dict):
            raise TypeError("Unexpected response format")
        
        return ResponseJSON(**json_data)

    def search_url(self, url: str) -> list[Result]:
        """Поиск изображения с помощью сервиса

        Принимает адрес изображения в сети

        Возвращается список словарей-результатов поиска
        """
        key = self._cache_key_for_url(url)
        with self._cache_lock:
            if key in self._cache:
                return self._cache[key]
        if self._handle_rate_limit():
            raise SauceNAOError("исчерпан дневной лимит SauceNAO")

        params = self._build_params(url=url)
        results = self._execute_search("GET", params=params)
        with self._cache_lock:
            self._cache[key] = results
        return results

    def search_file(self, file: IOBase|bytes) -> list[Result]:
        """Поиск изображения с помощью сервиса

        Принимает файл в виде файлового объекта или в байтовом представлении

        Возвращается список словарей-результатов поиска
        """
        content = self._read_file_content(file)
        key = self._cache_key_for_content(content)
        with self._cache_lock:
            if key in self._cache:
                return self._cache[key]
        if self._handle_rate_limit():
            raise SauceNAOError("исчерпан дневной лимит SauceNAO")

        params = self._build_params()
        files = {'file': ("image.png", content)}

        results = self._execute_search("POST", params=params, files=files)
        with self._cache_lock:
            self._cache[key] = results
        return results

    def get_sources(self, file: str|IOBase|bytes) -> list[str]:
        """Получение списка ссылок-источников для заданного файла

        Может принимать как строку с адресом изображения в сети,
        так и сам файл в виде файлового объекта или в байтовом представлении

        Возвращается список строк с адресами страниц в сети
        """
        results = self.search(file)
        return self._get_sources_from_results(results)

