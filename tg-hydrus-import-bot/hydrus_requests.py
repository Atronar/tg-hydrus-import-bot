"""
Модуль связанный с запросами к Гидрусу
"""
import ipaddress
import os
import tempfile
import time
from typing import Iterable, cast
from urllib.parse import urlparse
from cachetools import TTLCache
import hydrus_api
from loguru import logger
import requests
from duplicates import is_priority_url
from hydrus_api_enums import (
    AddedFileStatus,
    DuplicateRelationship,
    HydrusPermission,
    PageType,
)
import hydrus_api_typing
from media_info import is_exact_duplicate, is_image_mime, probe_media


HYDRUS_TIMEOUT = (5.0, 60.0)
"""Таймаут (подключение, чтение) для запросов к Hydrus API."""


class InsecureApiUrlError(ValueError):
    """HYDRUS_API_URL передаёт токен Hydrus по незашифрованному HTTP."""


class _TimeoutSession(requests.Session):
    """Сессия requests с таймаутом по умолчанию.

    hydrus_api не задаёт таймаут, поэтому без него зависший Hydrus
    блокирует единственный поток обработки запросов.
    """

    def __init__(self, timeout: tuple[float, float] = HYDRUS_TIMEOUT):
        super().__init__()
        self._timeout = timeout

    def request(self, *args, **kwargs):
        kwargs.setdefault("timeout", self._timeout)
        return super().request(*args, **kwargs)


# Импорт по ссылкам в Hydrus асинхронный: после add_url статус
# приходится опрашивать. Общий дедлайн не даёт одному видео
# удерживать обработчик сообщения до 10 минут.
URL_IMPORT_TIMEOUT = 60.0
URL_IMPORT_INITIAL_DELAY = 2.0
URL_IMPORT_MAX_DELAY = 15.0

# Человекочитаемые названия статусов импорта Hydrus
_ADDED_FILE_STATUS_NAMES = {
    AddedFileStatus.UNKNOWN: "файл отсутствует, ссылка известна",
    AddedFileStatus.SUCCESSFULLY_IMPORTED: "успешно импортирован",
    AddedFileStatus.ALREADY_IN_DATABASE: "уже есть в базе",
    AddedFileStatus.PREVIOUSLY_DELETED: "ранее удалён",
    AddedFileStatus.FAILED_TO_IMPORT: "не удалось импортировать",
    AddedFileStatus.VETOED: "отклонён",
}


class HydrusRequests:
    """Основной класс модуля
    Позволяет производить действия, связанные с Гидрусом

    Parameters
    ----------
    hydrus : hydrus_api.Client | str
        Либо уже готовый объект клиента, который и будет использоваться,
        либо токен, по которому и будет создан объект клиента
        
    api_url : str
        Адрес, по которому осуществляется доступ к API
        Если пуст, то используется адрес клиента по умолчанию
        
    default_tags_namespace : str
        Теговое пространство, которое будет использоваться по умолчанию
        
    default_destination_page_name : str
        Страница в клиенте, на которую будут выводиться результаты импорта по умолчанию
    """
    def __init__(
            self,
            hydrus: hydrus_api.Client|str,
            *,
            api_url: str|None = None,
            default_tags_namespace: str|None = None,
            default_destination_page_name: str|None = None,
            allow_insecure_api_url: bool = False
        ):
        self.allow_insecure_api_url = allow_insecure_api_url
        self.set_client(hydrus, api_url=api_url)
        self.set_default_tags_namespace(default_tags_namespace)
        self.set_default_destination_page_name(default_destination_page_name)
        # Ключи сервисов и открытые страницы меняются редко, но не никогда:
        # короткий TTL не даёт данным устареть насовсем.
        self._tags_namespace_cache: TTLCache[str, str] = TTLCache(
            maxsize=32,
            ttl=3600
        )
        self._pages_cache: TTLCache[str, Iterable[hydrus_api_typing.Page]] = TTLCache(
            maxsize=1,
            ttl=60
        )
        # Страница для повторного импорта ссылок, по которым Hydrus
        # не сообщил статус. Создаётся один раз за сессию.
        self._fallback_page_key: str | None = None
        self._fallback_page_name: str | None = None
        self._fallback_page_unavailable = False
        self.get_permission_info()

    @staticmethod
    def _is_loopback_host(host: str) -> bool:
        """Проверяет, что хост относится к локальной машине."""
        if host.lower() in ("localhost", "127.0.0.1", "::1"):
            return True
        try:
            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            return False

    @classmethod
    def _warn_if_insecure_api_url(
        cls,
        api_url: str|None,
        *,
        allow_insecure: bool = False
    ) -> None:
        """Проверяет, что токен Hydrus не передаётся по HTTP в сеть.

        По умолчанию небезопасное подключение запрещено. Явное разрешение
        (ALLOW_INSECURE_HYDRUS=true) оставляет только предупреждение.
        """
        if not api_url:
            return
        parsed = urlparse(api_url)
        if parsed.scheme != "http" or cls._is_loopback_host(parsed.hostname or ""):
            return
        message = (
            "HYDRUS_API_URL использует HTTP: токен Hydrus передаётся "
            "по сети без шифрования. Используйте HTTPS или localhost. "
            "Если риск осознан, установите ALLOW_INSECURE_HYDRUS=true."
        )
        if allow_insecure:
            logger.warning(message)
            return
        raise InsecureApiUrlError(message)

    def set_client(self, hydrus: hydrus_api.Client|str, *, api_url: str|None = None):
        """Установка значения клиента

        Parameters
        ----------
        hydrus : hydrus_api.Client | str
            Либо уже готовый объект клиента,
            либо токен, по которому и будет создан объект клиента
        
        api_url : str
            Адрес, по которому осуществляется доступ к API
            Если пуст, то используется адрес клиента по умолчанию
        """
        if isinstance(hydrus, str):
            if not api_url:
                api_url = hydrus_api.DEFAULT_API_URL
            self.client = hydrus_api.Client(
                access_key=hydrus,
                api_url=api_url,
                session=_TimeoutSession()
            )
        else:
            self.client = hydrus
        self._warn_if_insecure_api_url(
            getattr(self.client, "api_url", None) or api_url,
            allow_insecure=self.allow_insecure_api_url
        )

    def set_default_tags_namespace(self, tags_namespace: str|None):
        """Установка тегового пространства по умолчанию"""
        self.default_tags_namespace = tags_namespace

    def set_default_destination_page_name(self, destination_page_name: str|None):
        """Установка страницы назначения импорта по умолчанию"""
        self.destination_page_name = destination_page_name

    def get_permission_info(self) -> set[HydrusPermission]:
        """Получение списка разрешений"""
        self.permission_info = cast(
            hydrus_api_typing.PermissionInfo,
            self.client.verify_access_key()
        )
        self.permissions = {
            HydrusPermission(permission)
            for permission in self.permission_info.get('basic_permissions')
        }
        logger.debug(self.permissions)
        return self.permissions

    def check_permission(self, permission: int|HydrusPermission) -> bool:
        """Проверка наличия права доступа"""
        return permission in self.permissions

    def _get_root_pages(self) -> Iterable[hydrus_api_typing.Page]:
        """Возвращает корневые страницы клиента, кэшируя ответ API."""
        if "root" not in self._pages_cache:
            # get_pages() возвращает словарь вида {'pages':{'pages': [список страниц в корне]}}
            self._pages_cache["root"] = cast(
                hydrus_api_typing.TopPages,
                self.client.get_pages()
            ).get('pages', {}).get('pages', ())
        return self._pages_cache["root"]

    def get_page_hash_by_name(
            self,
            page_name: str,
            *,
            pages: Iterable[hydrus_api_typing.Page]|None = None
        ) -> str|None:
        """Рекуррентно достаёт из списка pages первую вкладку с нужным именем,
        заходя сначала вглубь, а потом сравнивая имена

        Parameters
        ----------
        page_name : str
            Имя страницы, которую ищем

        pages : Iterable[dict[str, Any]] | None
            Список страниц, по которым ведётся поиск
            При None мы получаем список всех страниц в клиенте и уже по ним ведём поиск

        Returns
        -------
        str
            Хэш-ключ, однозначно идентифицирующий страницу, по которому осуществляется доступ к ней
        """
        # Без прав на страницы не имеет смысла
        if not self.check_permission(HydrusPermission.PAGES):
            logger.warning('Отсутствует доступ "manage pages"')
            return None

        # Если страницы не переданы, значит мы в корне
        # и следует получить список всех открытых страниц
        is_root = False
        if pages is None:
            pages = self._get_root_pages()
            is_root = True
        if pages is None:
            raise ValueError("Не удалось получить страницы")
        for page in pages:
            # page может содержать вложенный словарь по ключу 'pages'
            # в таком случае просто переходим на уровень вглубь, пока не достигнем дна
            if 'pages' in page:
                if page_hash := self.get_page_hash_by_name(page_name, pages=page.get('pages')):
                    # возвращается, собственно, значение из самой глубины.
                    # условие выше предотвращает возврат None,
                    # означающего отсутствие найденной страницы
                    return page_hash
            # Уже дно, либо глубже страница не нашлась
            # Возвращаем наверх хэш-ключ, если название страницы — искомое
            if page_name == page.get('name'):
                return page.get('page_key')
        # Вложенные страницы кончились — мы не нашли искомую страницу
        if is_root:
            # Если мы в корне, значит необходимо создать страницу
            # с заданным именем и вернуть уже её хэш-ключ
            ### TODO
            return None
            # self.client.add_page или что-то типа того для создания
            # именованной страницы - когда появится подобный апи
            #
            # Возврат хэша добавленной страницы
            ###
        # Мы ничего не нашли, но ещё не в корне — сообщаем наверх продолжать искать
        return None

    def get_tags_namespace_hash(self, tags_namespace: str|None = None) -> str|None:
        """Получение ключа пространства тегов по его названию

        Parameters
        ----------
        tags_namespace : str | None
            Название пространства тегов
            Если None, то берётся из конфига "TAGS_NAMESPACE"

        Returns
        -------
        str
            Непосредственно хэш-ключ, однозначно определяющий теговое пространство в Гидрусе
        """
        # Необходимо одно из прав доступа
        if not (
            {
                HydrusPermission.FILES_IMPORT_DELETE,
                HydrusPermission.TAGS_EDIT,
                HydrusPermission.PAGES,
                HydrusPermission.FILES_SEARCH_FETCH
            } & self.permissions
        ):
            logger.warning(
                'Требуется одно из прав доступа: '
                '"import and delete files", '
                '"edit file tags", '
                '"manage pages", '
                '"search and fetch files"'
            )
            return None
        if tags_namespace is None:
            tags_namespace = self.default_tags_namespace
        if not tags_namespace:
            return None
        if tags_namespace in self._tags_namespace_cache:
            return self._tags_namespace_cache[tags_namespace]
        service_key = cast(
            hydrus_api_typing.GetServiceResponse,
            self.client.get_service(service_name=tags_namespace)
        ).get('service').get('service_key')
        if service_key:
            self._tags_namespace_cache[tags_namespace] = service_key
        return service_key

    def add_file(
            self,
            file: str|os.PathLike|hydrus_api.BinaryFileLike,
            *,
            page_name: str|None = None
        ) -> hydrus_api_typing.AddedFile|None:
        """Добавление файла в Гидрус

        Parameters
        ----------
        file : str | os.PathLike | hydrus_api.BinaryFileLike
            Добавляемый файл
            str и PathLike - путь к файлу в файловой системе
            BinaryFileLike - сам файл в виде двоичных данных

        page_name : str | None
            Название страницы, на которую будет добавлен файл
            Если None, то берётся из конфига "DESTINATION_PAGE_NAME"
            Если конфиг пуст, то игнор

        Returns
        -------
        dict[str, str | int]
            Возвращается ответ добавления файла, содержащий число status и строки hash и note
            Подробнее: https://hydrusnetwork.github.io/hydrus/developer_api.html#add_files_add_file
        """
        # Без наличия права на добавление не имеет смысла
        if not self.check_permission(HydrusPermission.FILES_IMPORT_DELETE):
            logger.warning('Отсутствует доступ "import and delete files"')
            return None
        # Добавление файла в Гидрус
        hydrus_added_file = cast(hydrus_api_typing.AddedFile, self.client.add_file(file))
        # Берём ключ страницы по имени
        if page_name is None:
            page_name = self.destination_page_name
        if page_name is None:
            page_key = None
        else:
            page_key = self.get_page_hash_by_name(page_name)
        # page_key не возвращается, если страницы нет — например конфиг пустой.
        if not page_key:
            return hydrus_added_file
        # Добавленный файл отправляем на страницу в клиенте
        self.client.add_files_to_page(page_key, hashes=[hydrus_added_file.get("hash"),])
        return hydrus_added_file

    @staticmethod
    def _failed_status(ex: Exception) -> hydrus_api_typing.AddedFile:
        """Синтетический статус для ссылки, которую не удалось обработать."""
        return {
            "status": int(AddedFileStatus.FAILED_TO_IMPORT),
            "hash": "",
            "note": str(ex),
        }

    @staticmethod
    def _timeout_status(note: str) -> hydrus_api_typing.AddedFile:
        """Статус ссылки, по которой Hydrus не сообщил результат импорта."""
        return {
            "status": int(AddedFileStatus.FAILED_TO_IMPORT),
            "hash": "",
            "note": note,
        }

    def _create_url_page(
            self,
            page_name: str,
            urls: Iterable[str] | None = None
        ) -> dict[str, object] | None:
        """Создаёт страницу URL-импорта Hydrus.

        Пустой список ссылок допустим: страницу можно создать заранее
        и потом добавлять на неё ссылки через ``add_url``.
        """
        try:
            response = self.client._api_request(
                "POST",
                "/manage_pages/new_page",
                json={
                    "page_type": int(PageType.URL_DOWNLOADER),
                    "page_name": page_name,
                    "urls": list(urls or []),
                    "focus_page": False,
                }
            )
            return response.json()
        except (AttributeError, hydrus_api.HydrusAPIException) as ex:
            logger.warning(f"Не удалось создать страницу {page_name}: {ex}")
            return None

    def _get_or_create_fallback_page(self) -> dict[str, object] | None:
        """Возвращает страницу для повторного импорта ссылок.

        Страница создаётся один раз за сессию и переиспользуется, чтобы
        повторные попытки не порождали бесконечное число страниц.
        """
        if self._fallback_page_key:
            return {
                "page_key": self._fallback_page_key,
                "page_name": self._fallback_page_name,
            }
        if self._fallback_page_unavailable:
            return None
        if not self.check_permission(HydrusPermission.PAGES):
            logger.warning('Отсутствует доступ "manage pages"')
            self._fallback_page_unavailable = True
            return None

        page_name = (
            f"HC fallback {time.strftime('%Y%m%d-%H%M%S')}"
            f"-{time.time_ns() % 1000000:06d}"
        )
        page_info = self._create_url_page(page_name)
        page_key = (page_info or {}).get("page_key")
        if not isinstance(page_key, str) or not page_key:
            self._fallback_page_unavailable = True
            return None

        self._fallback_page_key = page_key
        self._fallback_page_name = page_name
        logger.info(
            f"Создана страница {page_name} для повторного импорта ссылок"
        )
        return page_info

    def reimport_url(self, url: str) -> dict[str, object] | None:
        """Запускает повторный импорт ссылки на новой странице Hydrus.

        Если Hydrus помнит ссылку, но файла уже нет (status 0), обычный
        ``add_url`` на существующую страницу не переобрабатывает её.
        Новая страница URL-импорта заставляет Hydrus скачать ссылку заново.
        """
        if not url:
            return None
        if not self.check_permission(HydrusPermission.PAGES):
            logger.warning('Отсутствует доступ "manage pages"')
            return None

        page_name = (
            f"HC reimport {time.strftime('%Y%m%d-%H%M%S')}"
            f"-{time.time_ns() % 1000000:06d}"
        )
        page_info = self._create_url_page(page_name, [url])
        if not page_info:
            return None

        logger.info(
            f"Ссылка {url} известна Hydrus, но файла нет; "
            f"создана страница {page_name} для повторного импорта"
        )
        return page_info

    def _poll_url_statuses(
            self,
            pending: list[tuple[int, str]],
            statuses: list[list[hydrus_api_typing.AddedFile] | None],
            *,
            precheck_hashes: list[set[str]],
            cleaned_tags: list[str],
            tags_namespace_hash: str | None,
            tagged_hashes: set[str]
        ) -> list[tuple[int, str]]:
        """Опрашивает статусы ссылок до общего таймаута.

        Hydrus возвращает все файлы, уже привязанные к ссылке, а не один.
        Поэтому ссылка считается готовой, когда набор её файлов перестал
        меняться между опросами. Возвращает ссылки, по которым статус так
        и не появился.
        """
        deadline = time.monotonic() + URL_IMPORT_TIMEOUT
        delay = URL_IMPORT_INITIAL_DELAY
        last_hashes: dict[int, frozenset[str]] = {}
        stable_counts: dict[int, int] = {}
        while pending and time.monotonic() < deadline:
            still_pending: list[tuple[int, str]] = []
            for index, url in pending:
                # Дедлайн проверяем и внутри прохода: один медленный запрос
                # не должен растягивать общее ожидание.
                if time.monotonic() >= deadline:
                    still_pending.append((index, url))
                    continue
                try:
                    url_file_statuses = cast(
                        hydrus_api_typing.URLFiles,
                        self.client.get_url_files(url)
                    ).get("url_file_statuses", [])
                except hydrus_api.HydrusAPIException as ex:
                    logger.warning(f"Не удалось опросить статус {url}: {ex}")
                    still_pending.append((index, url))
                    continue

                # status 0 означает, что Hydrus помнит ссылку, но файла
                # ещё/уже нет. Это не результат импорта: такие записи
                # не считаем файлами ссылки.
                final_statuses = [
                    status for status in url_file_statuses
                    if status.get("status") != AddedFileStatus.UNKNOWN
                ]
                if not final_statuses:
                    still_pending.append((index, url))
                    continue

                prepared: list[hydrus_api_typing.AddedFile] = []
                for status in final_statuses:
                    file_hash = status.get("hash") or ""
                    # get_url_files возвращает статус 2 для любого файла,
                    # уже находящегося в базе, в том числе только что
                    # импортированного. Новыми считаем только те файлы,
                    # которых не было до нашей попытки импорта.
                    if file_hash and file_hash not in precheck_hashes[index]:
                        status = {**status, "_imported_now": True}
                    prepared.append(status)

                    # К уже существующему файлу теги из add_url не
                    # доимпортируются, поэтому добавляем их вручную.
                    if (
                        cleaned_tags
                        and status.get("status") == AddedFileStatus.ALREADY_IN_DATABASE
                        and file_hash
                        and file_hash not in tagged_hashes
                    ):
                        try:
                            self._add_tags_cleaned(
                                file_hash,
                                cleaned_tags,
                                tags_namespace_hash
                            )
                            tagged_hashes.add(file_hash)
                        except hydrus_api.HydrusAPIException as ex:
                            logger.warning(
                                f"Не удалось добавить теги к {url}: {ex}"
                            )

                statuses[index] = prepared
                hashes = frozenset(
                    status.get("hash") or "" for status in prepared
                )
                if last_hashes.get(index) == hashes:
                    stable_counts[index] = stable_counts.get(index, 1) + 1
                else:
                    stable_counts[index] = 1
                last_hashes[index] = hashes

                # Ждём, пока список файлов перестанет пополняться.
                if stable_counts[index] < 2:
                    still_pending.append((index, url))
            pending = still_pending
            if pending and time.monotonic() < deadline:
                time.sleep(delay)
                delay = min(delay * 1.5, URL_IMPORT_MAX_DELAY)
        return pending

    def _add_files_from_urls(
            self,
            urls: Iterable[str],
            *,
            page_name: str|None = None,
            tags: Iterable[str]|None = None,
            tags_namespace: str|None = None
        ) -> tuple[list[str], list[hydrus_api_typing.AddedFile|None]]:
        """Добавление в Гидрус нескольких ссылок с сохранением их порядка.

        Все ссылки ставятся в очередь импорта сразу, после чего их статусы
        опрашиваются пакетно. Это быстрее последовательного ожидания каждой
        ссылки и не зависит от эвристики по слову "video" в URL.

        Parameters
        ----------
        urls : Iterable[str]
            URL контента в сети. Дубликаты удаляются с сохранением порядка.

        page_name : str | None
            Название страницы, на которую будет добавлен файл
            Если None, то берётся из конфига "DESTINATION_PAGE_NAME"

        tags : Iterable[str] | None
            Добавляемые к файлам теги помимо тех, что захватит импорт

        tags_namespace : str | None
            Название тегового пространства, в которое запишутся теги
            Если None, то берётся из конфига "TAGS_NAMESPACE"

        Returns
        -------
        tuple[list[str], list[list[AddedFile] | None]]
            Уникальные ссылки и списки их статусов в том же порядке.
            Для ссылок, по которым статус так и не появился, остаётся None.
        """
        # Без прав на работу со ссылками не имеет смысла
        if not self.check_permission(HydrusPermission.URL_IMPORT_EDIT):
            logger.warning('Отсутствует доступ "import and edit urls"')
            return [], []

        # dict.fromkeys сохраняет порядок первого появления и убирает дубликаты
        unique_urls = list(dict.fromkeys(urls))
        if not unique_urls:
            return [], []
        tag_list = list(tags) if tags else []
        # Страница в клиенте, на которую добавится импорт
        if page_name is None:
            page_name = self.destination_page_name

        additional_tags = None
        tags_namespace_hash: str|None = None
        cleaned_tags: list[str] = []
        # clean_tags делается один раз на весь пакет ссылок
        if tag_list:
            # service_keys_to_additional_tags требует права на работу с тегами
            if self.check_permission(HydrusPermission.TAGS_EDIT):
                if tags_namespace_hash := self.get_tags_namespace_hash(tags_namespace):
                    # note: clean_tags возвращает не list[str], а {'tags': list[str], ...}
                    cleaned_tags = cast(
                        hydrus_api_typing.CleanedTags,
                        self.client.clean_tags(tag_list)
                    ).get('tags', [])
                    additional_tags = {tags_namespace_hash: cleaned_tags}
            else:
                logger.warning('Отсутствует доступ "edit file tags"')

        statuses: list[list[hydrus_api_typing.AddedFile] | None] = (
            [None] * len(unique_urls)
        )
        precheck_hashes: list[set[str]] = [set() for _ in unique_urls]
        pending: list[tuple[int, str]] = []
        add_url_notes: dict[int, str] = {}
        tagged_hashes: set[str] = set()

        # Сначала ставим в очередь все ссылки, сохраняя их порядок
        for index, url in enumerate(unique_urls):
            url_file_statuses = []
            try:
                url_file_statuses = cast(
                    hydrus_api_typing.URLFiles,
                    self.client.get_url_files(url)
                ).get("url_file_statuses", [])
            except hydrus_api.HydrusAPIException as ex:
                # Не прерываем весь пакет из-за одной ссылки: add_url
                # может сработать, а статус потом выяснится при опросе.
                logger.warning(f"Не удалось проверить статус {url}: {ex}")

            final_precheck = [
                status for status in url_file_statuses
                if status.get("status") != AddedFileStatus.UNKNOWN
            ]
            precheck_hashes[index] = {
                status.get("hash") or "" for status in final_precheck
            } - {""}

            should_poll = True
            reimported = False
            if final_precheck:
                statuses[index] = final_precheck
                # К уже существующим файлам теги из add_url не
                # доимпортируются, поэтому добавляем их вручную.
                if cleaned_tags:
                    for status in final_precheck:
                        file_hash = status.get("hash")
                        if (
                            status.get("status") == AddedFileStatus.ALREADY_IN_DATABASE
                            and file_hash
                            and file_hash not in tagged_hashes
                        ):
                            try:
                                self._add_tags_cleaned(
                                    file_hash,
                                    cleaned_tags,
                                    tags_namespace_hash
                                )
                                tagged_hashes.add(file_hash)
                            except hydrus_api.HydrusAPIException as ex:
                                logger.warning(
                                    f"Не удалось добавить теги к {url}: {ex}"
                                )
                # Все файлы уже есть в базе — опрашивать ссылку не нужно
                if all(
                    status.get("status") == AddedFileStatus.ALREADY_IN_DATABASE
                    for status in final_precheck
                ):
                    should_poll = False

            if any(
                status.get("status") == AddedFileStatus.UNKNOWN
                for status in url_file_statuses
            ):
                # Hydrus помнит ссылку, но файла уже нет. Обычный
                # add_url на существующую страницу её не переобработает,
                # поэтому создаём отдельную страницу импорта.
                page_info = self.reimport_url(url)
                if page_info:
                    reimported = True
                    should_poll = True
                    reimport_page_name = page_info.get("page_name")
                    if reimport_page_name:
                        add_url_notes[index] = (
                            f"создана страница {reimport_page_name}"
                        )
                else:
                    should_poll = False

            # Собственно, импорт, как в клиенте
            if not reimported:
                try:
                    add_url_result = self.client.add_url(
                        url,
                        destination_page_name=page_name,
                        service_keys_to_additional_tags=additional_tags
                    )
                    if isinstance(add_url_result, dict):
                        result_text = add_url_result.get("human_result_text")
                        if result_text:
                            add_url_notes[index] = str(result_text)
                except hydrus_api.HydrusAPIException as ex:
                    logger.error(f"Не удалось поставить в очередь {url}: {ex}")
                    # Если файлы уже были в базе, их статусы важнее ошибки.
                    # Иначе ошибка add_url и есть результат импорта.
                    has_ready_status = any(
                        status.get("status") in (
                            AddedFileStatus.ALREADY_IN_DATABASE,
                            AddedFileStatus.PREVIOUSLY_DELETED,
                        )
                        for status in final_precheck
                    )
                    if not has_ready_status:
                        statuses[index] = [self._failed_status(ex)]
                        should_poll = False

            if should_poll:
                pending.append((index, url))

        # Пакетный опрос с экспоненциальной паузой
        pending = self._poll_url_statuses(
            pending,
            statuses,
            precheck_hashes=precheck_hashes,
            cleaned_tags=cleaned_tags,
            tags_namespace_hash=tags_namespace_hash,
            tagged_hashes=tagged_hashes
        )

        # Hydrus иногда не сообщает статус для ссылки, добавленной на
        # страницу назначения. Одна повторная попытка на отдельной
        # странице помогает, а сама страница переиспользуется.
        fallback_attempted = False
        if pending:
            fallback_page = self._get_or_create_fallback_page()
            fallback_key = (fallback_page or {}).get("page_key")
            if isinstance(fallback_key, str) and fallback_key:
                fallback_attempted = True
                retry_pending: list[tuple[int, str]] = []
                for index, url in pending:
                    try:
                        self.client.add_url(
                            url,
                            destination_page_key=fallback_key,
                            service_keys_to_additional_tags=additional_tags
                        )
                    except hydrus_api.HydrusAPIException as ex:
                        logger.warning(
                            f"Не удалось повторить импорт {url} "
                            f"на странице fallback: {ex}"
                        )
                    retry_pending.append((index, url))
                pending = self._poll_url_statuses(
                    retry_pending,
                    statuses,
                    precheck_hashes=precheck_hashes,
                    cleaned_tags=cleaned_tags,
                    tags_namespace_hash=tags_namespace_hash,
                    tagged_hashes=tagged_hashes
                )

        if pending:
            timeout = (
                f"{URL_IMPORT_TIMEOUT:g}"
                if isinstance(URL_IMPORT_TIMEOUT, float)
                else str(URL_IMPORT_TIMEOUT)
            )
            logger.warning(
                f"Не дождались статуса импорта для {len(pending)} ссылок"
            )
            for index, url in pending:
                note = (
                    f"{url}: Hydrus не сообщил статус импорта за {timeout} с. "
                    "Ссылка может быть ещё в очереди или отклонена."
                )
                if add_url_note := add_url_notes.get(index):
                    note = f"{note} Дополнительно: {add_url_note}"
                if fallback_attempted:
                    note = (
                        f"{note} Повторный импорт на отдельной странице "
                        "тоже не дал статуса."
                    )
                timeout_status = self._timeout_status(note)
                if statuses[index]:
                    statuses[index] = [*statuses[index], timeout_status]
                else:
                    statuses[index] = [timeout_status]
        return unique_urls, statuses

    def add_files_from_urls(
            self,
            urls: Iterable[str],
            *,
            page_name: str|None = None,
            tags: Iterable[str]|None = None,
            tags_namespace: str|None = None
        ) -> list[hydrus_api_typing.AddedFile]:
        """Добавление ссылок и возврат только полученных статусов."""
        _, statuses = self._add_files_from_urls(
            urls,
            page_name=page_name,
            tags=tags,
            tags_namespace=tags_namespace
        )
        return [
            status
            for url_statuses in statuses if url_statuses
            for status in url_statuses
        ]

    def add_files_from_urls_detailed(
            self,
            urls: Iterable[str],
            *,
            page_name: str|None = None,
            tags: Iterable[str]|None = None,
            tags_namespace: str|None = None
        ) -> list[tuple[str, hydrus_api_typing.AddedFile]]:
        """Добавление ссылок и возврат пар «ссылка — первый статус».

        Оставлено для совместимости. Если Hydrus вернул для ссылки
        несколько файлов, здесь будет только первый из них; полный
        список доступен в :meth:`add_files_from_urls_detailed_multi`.
        Ссылки без статуса пропускаются.
        """
        unique_urls, statuses = self._add_files_from_urls(
            urls,
            page_name=page_name,
            tags=tags,
            tags_namespace=tags_namespace
        )
        return [
            (url, url_statuses[0])
            for url, url_statuses in zip(unique_urls, statuses)
            if url_statuses
        ]

    def add_files_from_urls_detailed_multi(
            self,
            urls: Iterable[str],
            *,
            page_name: str|None = None,
            tags: Iterable[str]|None = None,
            tags_namespace: str|None = None
        ) -> list[tuple[str, list[hydrus_api_typing.AddedFile]]]:
        """Добавление ссылок и возврат всех файлов каждой ссылки.

        Hydrus может привязать к одной ссылке несколько файлов,
        например изображений с одной страницы.
        """
        unique_urls, statuses = self._add_files_from_urls(
            urls,
            page_name=page_name,
            tags=tags,
            tags_namespace=tags_namespace
        )
        return [
            (url, url_statuses)
            for url, url_statuses in zip(unique_urls, statuses)
            if url_statuses
        ]

    def add_file_from_url(
            self,
            url: str,
            *,
            page_name: str|None = None,
            tags: Iterable[str]|None = None,
            tags_namespace: str|None = None
        ) -> hydrus_api_typing.AddedFile|None:
        """Добавление одного файла в Гидрус по ссылке.

        Оставлено для совместимости; пакетная обработка — в
        :meth:`add_files_from_urls`.
        """
        added_files = self.add_files_from_urls(
            [url],
            page_name=page_name,
            tags=tags,
            tags_namespace=tags_namespace
        )
        return added_files[0] if added_files else None

    def _add_tags_cleaned(
            self,
            file_hash: str|None,
            tags: Iterable[str],
            tags_namespace_hash: str|None
        ):
        """Добавляет уже очищенные теги в известное пространство тегов."""
        if not file_hash or not tags or not tags_namespace_hash:
            return
        self.client.add_tags(
            hashes=[file_hash],
            service_keys_to_tags={tags_namespace_hash: list(tags)}
        )

    def add_tags(
            self,
            file_hash: str,
            tags: Iterable[str],
            *,
            tags_namespace: str|None = None
        ):
        """Добавление к файлу тегов
        Подробнее: https://hydrusnetwork.github.io/hydrus/developer_api.html#add_tags_add_tags

        Parameters
        ----------
        file_hash : str
            Хэш-ключ однозначно определяющий файл, к которому добавляются теги

        tags : Iterable[str]
            Добавляемые к файлу теги

        tags_namespace : str | None
            Название тегового пространства, в которое запишутся теги
            Если None, то берётся из конфига "DESTINATION_PAGE_NAME"
        """
        # Без прав на работу с тегами не имеет смысла
        if not self.check_permission(HydrusPermission.TAGS_EDIT):
            logger.warning('Отсутствует доступ "edit file tags"')
            return
        # Как бы то ни было, пустые хэш и теги бессмысленны
        if not file_hash or not tags:
            return

        # Получаем пространство тегов и очищаем теги
        if tags_namespace_hash := self.get_tags_namespace_hash(tags_namespace):
            cleaned_tags = cast(
                hydrus_api_typing.CleanedTags,
                self.client.clean_tags(tags)
            ).get('tags', [])
            # note: clean_tags возвращает не list[str], а {'tags': list[str], ...}
            self._add_tags_cleaned(file_hash, cleaned_tags, tags_namespace_hash)

    def add_urls(self, file_hash: str, urls: Iterable[str]):
        """Добавление к файлу ссылок
        Подробнее: https://hydrusnetwork.github.io/hydrus/developer_api.html#add_urls_associate_url

        Parameters
        ----------
        file_hash : str
            Хэш-ключ однозначно определяющий файл, к которому добавляются ссылки

        urls : Iterable[str]
            Добавляемые к файлу ссылки
        """
        # Без прав на работу со ссылками не имеет смысла
        if not self.check_permission(HydrusPermission.URL_IMPORT_EDIT):
            logger.warning('Отсутствует доступ "import and edit urls"')
            return
        # Как бы то ни было, пустые хэш и ссылки бессмысленны
        if not file_hash or not urls:
            return
        self.client.associate_url(hashes=[file_hash,], urls_to_add=urls)

    def import_content(
            self,
            content_file: str|os.PathLike|hydrus_api.BinaryFileLike|None = None,
            *,
            page_name: str|None = None,
            urls: Iterable[str]|None = None,
            tags: Iterable[str]|None = None,
            tags_namespace: str|None = None
        ) -> list[hydrus_api_typing.AddedFile]:
        """Добавление файла в Гидрус

        Parameters
        ----------
        content_file : str | os.PathLike | hydrus_api.BinaryFileLike | None
            Непосредственно добавляемый файл
            str и PathLike - путь к файлу в файловой системе
            BinaryFileLike - сам файл в виде двоичных данных
            None - не использовать этот метод добавления

        page_name : str | None
            Название страницы, на которую будет добавлен файл
            Если None, то берётся из конфига "DESTINATION_PAGE_NAME"

        urls : Iterable[str]
            Список ссылок, из которых импортируется контент
            Помимо того, если указан content_file — добавляемые к файлу ссылки

        tags : Iterable[str]
            Добавляемые к файлу теги помимо тех, что захватит импорт

        tags_namespace : str | None
            Название тегового пространства, в которое запишутся теги
            Если None, то берётся из конфига "DESTINATION_PAGE_NAME"

        Returns
        -------
        list[dict[str, str | int]]
            Возвращается список ответов добавления файла,
            содержащие число status и строки hash и note
            Подробнее:
            https://hydrusnetwork.github.io/hydrus/developer_api.html#add_urls_get_url_files
            (первый элемент "url_file_statuses")
            Каждый элемент списка - ответ добавления content_file и каждого элемента urls
        """
        # Материализуем итерируемые аргументы: они используются несколько раз
        url_list = list(urls) if urls else []
        tag_list = list(tags) if tags else []
        added_file: list[hydrus_api_typing.AddedFile] = []
        # Импорт файлов в Гидрус по ссылкам.
        # Результат сохраняет порядок ссылок, загруженных успешно.
        if url_list:
            added_file.extend(
                self.add_files_from_urls(
                    url_list,
                    page_name=page_name,
                    tags=tag_list,
                    tags_namespace=tags_namespace
                )
            )
        # Добавление собственно файла в Гидрус
        if content_file and (added_content := self.add_file(content_file, page_name=page_name)):
            added_file.insert(0, added_content)
            # Дописывание тегов и ссылок к файлу
            if tag_list:
                self.add_tags(
                    added_content.get("hash"),
                    tag_list,
                    tags_namespace=tags_namespace
                )
            if url_list:
                self.add_urls(added_content.get("hash"), url_list)
        # Возврат итогового результата
        return added_file

    @staticmethod
    def convert_import_resp_to_str(resp: list[hydrus_api_typing.AddedFile]) -> str:
        """Всего лишь метод, преобразовывающий ответ hydrus_import (в виде списка словарей),
        в человекочитаемом формате сообщения

        Parameters
        ----------
        list[dict[str, str | int]]
            Список ответов добавления файла
            Подробнее:
            https://hydrusnetwork.github.io/hydrus/developer_api.html#add_urls_get_url_files
            (первый элемент "url_file_statuses")

        Returns
        -------
        str
            Человекочитаемый текст, каждый ответ отделён пустой строкой,
            а содержимое ответов расписано построчно
        """
        replies: list[str] = []
        for resp_item in resp:
            parts: list[str] = []
            for key, value in resp_item.items():
                # Служебные поля не показываем пользователю.
                if key.startswith("_"):
                    continue
                if key != "status":
                    parts.append(f"{key}: {value}")
                    continue
                try:
                    status = AddedFileStatus(value)
                except (TypeError, ValueError):
                    parts.append(f"status: неизвестный статус {value}")
                else:
                    parts.append(
                        f"status: {_ADDED_FILE_STATUS_NAMES[status]} ({value})"
                    )
            replies.append('\n'.join(parts))
        return '\n\n'.join(replies)

    def get_main_file_hash(self, file_hash: str) -> str|None:
        """Получение основного хэша файла

        Parameters
        ----------
        file_hash : str
            Хэш-ключ в sha256, однозначно определяющий файл

        Returns
        -------
        str | None
            Хэш файла, как он хранится в системе, либо None, если файл отсутствует
            или недоступны связи файлов.
            Если king недоступен локально, возвращается сам файл — так рекомендует
            документация Hydrus, поскольку скачать нелокальный king нельзя.
        """
        if not self.check_permission(HydrusPermission.RELATIONSHIPS_EDIT):
            logger.warning('Отсутствует доступ "manage file relationships"')
            return None
        # Убираем sha256: и прочие из начала
        if ':' in file_hash:
            file_hash = file_hash.split(':',1)[-1]

        file_relationships = cast(
            hydrus_api_typing.FileRelationships,
            self.client.get_file_relationships(hashes=[file_hash,])
        )
        relationship = file_relationships.get("file_relationships", {}).get(file_hash)
        if not relationship:
            return None
        # king_is_local означает, что king лежит на диске и его можно скачать
        # через /get_files/file. Если его нет локально, представителем группы
        # остаётся сам файл.
        if relationship.get("king_is_local", False):
            return relationship.get("king", file_hash)
        return file_hash

    def get_file(self, file_hash: str):
        """Получение файла по хэшу

        Parameters
        ----------
        file_hash : str
            Хэш-ключ однозначно определяющий файл, к которому добавляются ссылки

        Returns
        -------
        Response | None
            http-ответ, содержащий сам файл.
            Требует права "search and fetch files" и "manage file relationships":
            без второго нельзя определить king, поэтому исходный хэш не подставляется.
            Подробнее: https://hydrusnetwork.github.io/hydrus/developer_api.html#get_files_file
        """
        # Проверка доступа и хэша
        if not self.check_permission(HydrusPermission.FILES_SEARCH_FETCH):
            logger.warning('Отсутствует доступ "search and fetch files"')
            return None
        if not self.check_permission(HydrusPermission.RELATIONSHIPS_EDIT):
            logger.warning('Отсутствует доступ "manage file relationships"')
            return None
        if not file_hash:
            return None
        # Без возможности определить king поведение не подменяется исходным хэшем:
        # отсутствие права делает функцию невыполнимой.
        if not (main_file_hash := self.get_main_file_hash(file_hash)):
            return None
        return self.client.get_file(hash_=main_file_hash)

    def get_file_bytes(self, file_hash: str):
        """Получение файла строго по указанному хэшу, без подмены king.

        Нужно для проверки дубликатов только что импортированного файла:
        ``get_file`` вернул бы king, а сравнить нужно именно новый файл.
        Требует только права "search and fetch files".
        """
        if not self.check_permission(HydrusPermission.FILES_SEARCH_FETCH):
            logger.warning('Отсутствует доступ "search and fetch files"')
            return None
        if not file_hash:
            return None
        return self.client.get_file(hash_=file_hash)

    def get_tags(
        self,
        file_hash: str,
        displayed_tags: bool = True,
        tags_namespace: str|None = None
    ) -> list[str]:
        """Получение тегов сохранённого файла по его хэшу

        Parameters
        ----------
        file_hash : str
            Хэш-ключ однозначно определяющий файл, теги которого нужны
            Если файл является дубликатом, берутся теги его king

        displayed_tags : bool
            True — вернуть отображаемые теги, False — хранимые

        tags_namespace : str | None
            Название тегового пространства, из которого берутся теги
            Если None, то берётся из конфига "TAGS_NAMESPACE"

        Returns
        -------
        list[str]
            Список текущих тегов указанного пространства.
            Требует права "search and fetch files" и "manage file relationships":
            без второго нельзя определить king, поэтому теги исходного хэша
            не подставляются.
        """
        if not self.check_permission(HydrusPermission.FILES_SEARCH_FETCH):
            logger.warning('Отсутствует доступ "search and fetch files"')
            return []
        if not self.check_permission(HydrusPermission.RELATIONSHIPS_EDIT):
            logger.warning('Отсутствует доступ "manage file relationships"')
            return []
        if not file_hash:
            return []
        # Теги дубликата берутся у king. Без права на relationships
        # определить king нельзя, поэтому подменять его исходным хэшем нельзя.
        if not (main_file_hash := self.get_main_file_hash(file_hash)):
            return []
        file_metadata = (
            self
            .client
            .get_file_metadata(hashes=[main_file_hash,])
            .get('metadata', [])
        )
        if not file_metadata:
            return []
        tags_namespaces: dict[str, hydrus_api_typing.TagsNamespace] = (
            file_metadata[0].get('tags', {})
        )
        # Получаем пространство тегов
        if tags_namespace_hash := self.get_tags_namespace_hash(tags_namespace):
            tags = tags_namespaces.get(tags_namespace_hash, {})
            if displayed_tags:
                tags_list = tags.get("display_tags", {})
            else:
                tags_list = tags.get("storage_tags", {})
            return tags_list.get("0", [])
        return []

    def _is_local_file(self, file_hash: str) -> bool:
        """Проверяет, что файл действительно доступен в локальной базе Hydrus.

        Записи-призраки (например, hash из чужой базы) тоже имеют file_id,
        поэтому ориентироваться на него нельзя: признак наличия файла —
        is_local в ответе /get_files/file_metadata.
        """
        if not file_hash:
            return False
        file_metadata = (
            self
            .client
            .get_file_metadata(hashes=[file_hash,])
            .get('metadata', [])
        )
        if not file_metadata:
            return False
        return bool(file_metadata[0].get('is_local'))

    def _add_tags_to_existing(
        self,
        file_hash: str,
        tags: Iterable[str] | None
    ) -> None:
        """Добавляет теги существующему файлу, если они переданы."""
        if tags:
            self.add_tags(file_hash, tags)

    def find_exact_duplicate(
        self,
        file: str | os.PathLike | hydrus_api.BinaryFileLike,
        *,
        mime: str | None = None,
        tags: Iterable[str] | None = None
    ) -> hydrus_api_typing.AddedFile | None:
        """Ищет точный дубликат файла, который уже есть в Hydrus.

        Сначала проверяется sha256: Hydrus сам считает такой файл
        дубликатом. Для изображений затем ищутся кандидаты с перцептивным
        расстоянием 0 и сравниваются размеры. Для остальных файлов
        перцептивного поиска нет: используется только sha256. Найденному
        файлу сразу добавляются теги, повторный импорт не выполняется.
        """
        hashes = self.generate_hashes(file)
        file_hash = hashes.get('hash')
        if file_hash and (main_hash := self.get_main_file_hash(file_hash)):
            if self._is_local_file(main_hash):
                self._add_tags_to_existing(main_hash, tags)
                return {
                    "status": int(AddedFileStatus.ALREADY_IN_DATABASE),
                    "hash": main_hash,
                    "note": "точный дубликат уже есть в базе",
                }

        if not is_image_mime(mime):
            return None

        perceptual_hashes = hashes.get('perceptual_hashes') or []
        if not perceptual_hashes:
            return None

        candidates = self.find_similar_files(perceptual_hashes, 0)
        if not candidates:
            return None

        new_info = probe_media(file, mime)
        for candidate_hash in candidates:
            existing_info = self.get_file_info(candidate_hash)
            if (
                existing_info
                and is_exact_duplicate(new_info, existing_info)
            ):
                self._add_tags_to_existing(candidate_hash, tags)
                return {
                    "status": int(AddedFileStatus.ALREADY_IN_DATABASE),
                    "hash": candidate_hash,
                    "note": "точный дубликат уже есть в базе",
                }
        return None

    def find_upload_duplicate(
        self,
        file: str | os.PathLike | hydrus_api.BinaryFileLike,
        *,
        mime: str | None = None,
        tags: Iterable[str] | None = None,
        max_distance: int = 5
    ) -> dict[str, object]:
        """Ищет дубликат загружаемого файла до его импорта.

        Возвращает ``{"kind": "exact", "status": ...}`` для точного
        дубликата, ``{"kind": "near", "distance": ..., "hashes": ...,
        "new_info": MediaInfo}`` для похожего файла и ``{"kind": "none"}``.
        Точный дубликат не импортируется, теги сразу добавляются
        существующему файлу. Перцептивный поиск выполняется только для
        изображений; для остальных файлов проверяется только sha256.
        """
        try:
            hashes = self.generate_hashes(file)
        except hydrus_api.HydrusAPIException as ex:
            logger.warning(f"Не удалось получить хэши файла: {ex}")
            return {"kind": "none"}

        try:
            file_hash = hashes.get("hash")
            if file_hash and (main_hash := self.get_main_file_hash(file_hash)):
                if self._is_local_file(main_hash):
                    self._add_tags_to_existing(main_hash, tags)
                    return {
                        "kind": "exact",
                        "status": {
                            "status": int(AddedFileStatus.ALREADY_IN_DATABASE),
                            "hash": main_hash,
                            "note": "точный дубликат уже есть в базе",
                        },
                    }
        except hydrus_api.HydrusAPIException as ex:
            logger.warning(f"Не удалось проверить sha256 файла: {ex}")

        if not is_image_mime(mime):
            return {"kind": "none"}

        perceptual_hashes = hashes.get("perceptual_hashes") or []
        if not perceptual_hashes:
            return {"kind": "none"}

        try:
            exact_candidates = self.find_similar_files(perceptual_hashes, 0)
            if exact_candidates:
                new_info = probe_media(file, mime)
                for candidate_hash in exact_candidates:
                    existing_info = self.get_file_info(candidate_hash)
                    if (
                        existing_info
                        and is_exact_duplicate(new_info, existing_info)
                    ):
                        self._add_tags_to_existing(candidate_hash, tags)
                        return {
                            "kind": "exact",
                            "status": {
                                "status": int(
                                    AddedFileStatus.ALREADY_IN_DATABASE
                                ),
                                "hash": candidate_hash,
                                "note": "точный дубликат уже есть в базе",
                            },
                        }
                return {
                    "kind": "near",
                    "distance": 0,
                    "hashes": exact_candidates,
                    "new_info": new_info,
                }

            if max_distance <= 0:
                return {"kind": "none"}
            similar_candidates = self.find_similar_files(
                perceptual_hashes,
                max_distance
            )
        except hydrus_api.HydrusAPIException as ex:
            logger.warning(f"Не удалось найти дубликаты файла: {ex}")
            return {"kind": "none"}

        if similar_candidates:
            return {
                "kind": "near",
                "distance": max_distance,
                "hashes": similar_candidates,
                "new_info": probe_media(file, mime),
            }
        return {"kind": "none"}

    def find_url_duplicates(
        self,
        new_hash: str,
        max_distance: int
    ) -> dict[str, object] | None:
        """Ищет похожие файлы для только что добавленного в Гидрус файла.

        Файл уже добавлен в Hydrus, поэтому используется поиск по его
        хэшу sha256: Hydrus сам возьмёт сохранённый нужный хэш,
        скачивать содержимое не нужно. Сначала проверяется расстояние 0
        (точный дубликат), затем — расстояние до ``max_distance``.
        Возвращает словарь ``{"distance": int, "hashes": list[str]}``
        или None.

        На данный момент в самом Гидрусе для не-изображений
        перцептивный поиск не реализован и здесь он вернёт им лишь
        точные копии, но мы не можем исключать появления подобного
        механизма в Гидрусе в будущем.
        """
        # Исключаем сам файл и его king: Hydrus может вернуть их в выдаче.
        excluded = {new_hash}
        if main_hash := self.get_main_file_hash(new_hash):
            excluded.add(main_hash)

        try:
            exact = self.find_similar_files_by_hash(
                new_hash,
                0,
                exclude_hashes=excluded
            )
            if exact:
                return {"distance": 0, "hashes": exact}

            if max_distance <= 0:
                return None
            similar = self.find_similar_files_by_hash(
                new_hash,
                max_distance,
                exclude_hashes=excluded
            )
        except hydrus_api.HydrusAPIException as ex:
            logger.warning(f"Не удалось найти дубликаты {new_hash}: {ex}")
            return None

        if similar:
            return {"distance": max_distance, "hashes": similar}
        return None

    def resolve_url_duplicate(
        self,
        url: str,
        new_hash: str,
        existing_hash: str,
        *,
        distance: int,
        priority_domains: frozenset[str] | set[str],
        tags: Iterable[str] | None = None
    ) -> dict[str, object]:
        """Разрешает дубликат ссылки по приоритету доменов.

        Для расстояния 0 применяется матрица приоритетов: если у
        существующего файла есть приоритетная ссылка, остаётся он;
        если приоритетна только новая ссылка — остаётся новый файл.
        В остальных случаях возвращается ``action="pending"`` для выбора
        пользователем. Для расстояния больше 0 выбор всегда за пользователем.
        """
        result: dict[str, object] = {
            "action": "pending",
            "url": url,
            "new_hash": new_hash,
            "existing_hash": existing_hash,
            "distance": distance,
        }
        if distance > 0:
            return result

        try:
            existing_info = self.get_file_info(existing_hash)
        except hydrus_api.HydrusAPIException as ex:
            logger.warning(f"Не удалось получить данные {existing_hash}: {ex}")
            return result
        if not existing_info:
            return result

        existing_urls = [
            existing_url
            for existing_url in (existing_info.get("known_urls") or [])
            if isinstance(existing_url, str)
        ]
        if not existing_urls:
            return result

        existing_priority = any(
            is_priority_url(existing_url, priority_domains)
            for existing_url in existing_urls
        )
        new_priority = is_priority_url(url, priority_domains)

        if existing_priority:
            # Обе ссылки приоритетные или новая не приоритетная:
            # сохраняем существующий файл.
            action = "kept_existing"
        elif new_priority:
            action = "kept_new"
        else:
            return result

        try:
            if action == "kept_existing":
                self._add_tags_to_existing(existing_hash, tags)
                self.add_urls(existing_hash, [url])
                self.set_duplicate_relationship(
                    existing_hash,
                    new_hash,
                    merge_content=True,
                    delete_worse=True
                )
            else:
                self._add_tags_to_existing(new_hash, tags)
                self.set_duplicate_relationship(
                    new_hash,
                    existing_hash,
                    merge_content=True,
                    delete_worse=True
                )
        except hydrus_api.HydrusAPIException as ex:
            logger.warning(f"Не удалось разрешить дубликат {new_hash}: {ex}")
            return result

        result["action"] = action
        return result

    def _has_search_permissions(self) -> bool:
        if not self.check_permission(HydrusPermission.FILES_SEARCH_FETCH):
            logger.warning('Отсутствует доступ "search and fetch files"')
            return False
        if not self.check_permission(HydrusPermission.RELATIONSHIPS_EDIT):
            logger.warning('Отсутствует доступ "manage file relationships"')
            return False
        return True

    def _filter_local_kings(
        self,
        found_hashes: Iterable[str],
        exclude_hashes: Iterable[str] | None = None
    ) -> list[str]:
        """Оставляет локальные king-файлы без повторов и исключённых хэшей."""
        excluded = set(exclude_hashes or ())
        result: list[str] = []
        for found_hash in found_hashes:
            main_hash = self.get_main_file_hash(found_hash)
            if (
                main_hash
                and main_hash not in result
                and main_hash not in excluded
                and self._is_local_file(main_hash)
            ):
                result.append(main_hash)
        return result

    def find_similar_files(
        self,
        perceptual_hashes: Iterable[str],
        max_distance: int,
        *,
        exclude_hashes: Iterable[str]|None = None
    ) -> list[str]:
        """Ищет локальные файлы с похожими перцептивными хэшами.

        Используется до добавления файла в Hydrus: перцептивные хэши
        передаются в запрос ``system:similar to data``. Возвращает хэши
        king-файлов в порядке, который вернул Hydrus. Дубликаты и
        нелокальные записи отфильтровываются. ``exclude_hashes``
        позволяет не возвращать сам импортируемый файл.
        """
        hashes = [file_hash for file_hash in perceptual_hashes if file_hash]
        if not hashes:
            return []
        if not self._has_search_permissions():
            return []

        query = (
            f"system:similar to data {' '.join(hashes)} "
            f"distance {max(0, max_distance)}"
        )
        found_hashes: list[str] = (
            self.client.search_files(
                [query],
                return_hashes=True
            ).get('hashes', [])
        )
        return self._filter_local_kings(found_hashes, exclude_hashes)

    def find_similar_files_by_hash(
        self,
        file_hash: str,
        max_distance: int,
        *,
        exclude_hashes: Iterable[str]|None = None
    ) -> list[str]:
        """Ищет похожие файлы по sha256-хэшу уже добавленного файла.

        Hydrus использует сохранённый перцептивный хэш, поэтому
        содержимое не скачивается. Возвращает sha256-хэши king-файлов без
        повторов и нелокальных записей.
        """
        if not file_hash:
            return []
        if not self._has_search_permissions():
            return []

        query = (
            f"system:similar to {file_hash} "
            f"distance {max(0, max_distance)}"
        )
        found_hashes: list[str] = (
            self.client.search_files(
                [query],
                return_hashes=True
            ).get('hashes', [])
        )
        return self._filter_local_kings(found_hashes, exclude_hashes)

    def get_file_info(self, file_hash: str) -> dict[str, object] | None:
        """Возвращает метаданные файла, включая известные ссылки."""
        if not file_hash:
            return None
        if not self.check_permission(HydrusPermission.FILES_SEARCH_FETCH):
            logger.warning('Отсутствует доступ "search and fetch files"')
            return None

        file_metadata = (
            self.client
            .get_file_metadata(
                hashes=[file_hash,],
                detailed_url_information=True
            )
            .get('metadata', [])
        )
        if not file_metadata:
            return None
        return file_metadata[0]

    def set_duplicate_relationship(
        self,
        better_hash: str,
        worse_hash: str,
        *,
        relationship: DuplicateRelationship|int = DuplicateRelationship.BETTER,
        merge_content: bool = True,
        delete_worse: bool = False
    ) -> bool:
        """Задаёт связь между файлами в системе дубликатов Hydrus.

        ``better_hash`` и ``worse_hash`` задают порядок файлов в паре.
        ``delete_worse`` удаляет второй файл в той же операции, оставляя
        в базе связь с лучшей версией.
        """
        if not self.check_permission(HydrusPermission.RELATIONSHIPS_EDIT):
            logger.warning('Отсутствует доступ "manage file relationships"')
            return False
        if not better_hash or not worse_hash or better_hash == worse_hash:
            return False

        payload: dict[str, object] = {
            "hash_a": better_hash,
            "hash_b": worse_hash,
            "relationship": int(relationship),
            "do_default_content_merge": merge_content,
        }
        if delete_worse:
            payload["delete_b"] = True

        self.client.set_file_relationships([payload])
        return True

    def get_file_hash(self, file: str|os.PathLike|hydrus_api.BinaryFileLike) -> str|None:
        """Получение хэша файла

        Parameters
        ----------
        file : str | os.PathLike | hydrus_api.BinaryFileLike
            Добавляемый файл
            str и PathLike - путь к файлу в файловой системе
            BinaryFileLike - сам файл в виде двоичных данных

        Returns
        -------
        str | None
            Хэш локально доступного файла (для дубликата — хэш его king),
            либо None, если ни точного, ни похожего файла нет.

        Сначала проверяется точное совпадение sha256, затем — похожие
        изображения через system:similar to data с перцептивным хэшем.
        Записи-призраки отсекаются проверкой is_local.
        """
        # Проверка доступа
        if not self.check_permission(HydrusPermission.FILES_IMPORT_DELETE):
            logger.warning('Отсутствует доступ "import and delete files"')
            return None
        if not self.check_permission(HydrusPermission.RELATIONSHIPS_EDIT):
            logger.warning('Отсутствует доступ "edit file relationships"')
            return None

        # Генерируем хэши один раз: повторное чтение file-like объекта
        # после первого read() возвращает пустые данные
        hashes = self.generate_hashes(file)

        # Для уже известного удалённого дубликата сразу находится king.
        # Но relationship может существовать и для записи-призрака,
        # поэтому возвращаем хэш только при наличии локального файла.
        file_hash = hashes.get('hash')
        if file_hash and (main_hash := self.get_main_file_hash(file_hash)):
            if self._is_local_file(main_hash):
                logger.debug(main_hash)
                return main_hash
            logger.debug(f"Файл {main_hash} отсутствует локально")

        # Файл, прошедший через Telegram, обычно пережат и имеет другой sha256,
        # поэтому ищем похожий файл по перцептивному хэшу. Ключевое слово data
        # обязательно: без него Hydrus трактует хэш как sha256 файла.
        perceptual_hashes = hashes.get('perceptual_hashes')
        if not perceptual_hashes:
            return None
        if not self.check_permission(HydrusPermission.FILES_SEARCH_FETCH):
            logger.warning('Отсутствует доступ "search and fetch files"')
            return None

        found_hashes: list[str] = (
            self
            .client
            .search_files(
                [f"system:similar to data {' '.join(perceptual_hashes)} distance 5",],
                return_hashes=True
            )
            .get('hashes', [])
        )
        for found_hash in found_hashes:
            if main_hash := self.get_main_file_hash(found_hash):
                if self._is_local_file(main_hash):
                    logger.debug(main_hash)
                    return main_hash
        return None

    def generate_hashes(
        self,
        path_or_file: str | os.PathLike | hydrus_api.BinaryFileLike
    ) -> hydrus_api_typing.FileHashes:
        """Generate hashes for an arbitrary file.
        Получение хэшей из Гидруса

        В библиотеке питона не реализована возможность использования как аргумента самих данных,
        что и исправлено здесь

        Parameters
        ----------
        path_or_file : str | os.PathLike | hydrus_api.BinaryFileLike
            Файл, для которого генерируются хэши
            str и PathLike - путь к файлу в файловой системе
            BinaryFileLike - сам файл в виде двоичных данных

        Returns
        -------
        dict
            Хэши файла
        """
        if isinstance(path_or_file, (os.PathLike, str)):
            return cast(
                hydrus_api_typing.FileHashes,
                self.client.generate_hashes(path_or_file)
            )

        # file-like объект может быть не в начале: хэши считаются по всему файлу
        if hasattr(path_or_file, "seek"):
            path_or_file.seek(0)
        data = path_or_file.read()
        # Возвращаем указатель в начало, чтобы объект можно было использовать повторно
        if hasattr(path_or_file, "seek"):
            path_or_file.seek(0)

        # Публичный API hydrus-api умеет генерировать хэши только по пути.
        # Если внутренний endpoint доступен — отправляем байты напрямую,
        # иначе откатываемся на публичный метод через временный файл.
        private_request = getattr(self.client, "_api_request", None)
        private_path = getattr(self.client, "_GENERATE_HASHES_PATH", None)
        if callable(private_request) and private_path:
            response = private_request(
                "POST",
                private_path,
                data=data,
                headers={"Content-Type": "application/octet-stream"}
            )
            return cast(hydrus_api_typing.FileHashes, response.json())

        fd, temp_path = tempfile.mkstemp(prefix="hydrus_hashes_", suffix=".bin")
        try:
            with os.fdopen(fd, "wb") as temp_file:
                temp_file.write(data)
            return cast(
                hydrus_api_typing.FileHashes,
                self.client.generate_hashes(temp_path)
            )
        finally:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
