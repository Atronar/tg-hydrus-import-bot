﻿"""
Модуль связанный с запросами к Гидрусу
"""
import os
import time
from typing import Iterable, cast
import hydrus_api
from loguru import logger
from hydrus_api_enums import HydrusPermission, AddedFileStatus
import hydrus_api_typing


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
            default_destination_page_name: str|None = None
        ):
        self.set_client(hydrus, api_url=api_url)
        self.set_default_tags_namespace(default_tags_namespace)
        self.set_default_destination_page_name(default_destination_page_name)
        self.get_permission_info()

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
            self.client = hydrus_api.Client(access_key=hydrus, api_url=api_url)
        else:
            self.client = hydrus

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
            # get_pages() возвращает словарь вида {'pages':{'pages': [список страниц в корне]}}
            pages = cast(
                hydrus_api_typing.TopPages,
                self.client.get_pages()
            ).get('pages',{}).get('pages',())
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
        return cast(
                hydrus_api_typing.GetServiceResponse,
                self.client.get_service(service_name=tags_namespace)
        ).get('service').get('service_key')

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

    def add_file_from_url(
            self,
            url: str,
            *,
            page_name: str|None = None,
            tags: Iterable[str]|None = None,
            tags_namespace: str|None = None
        ) -> hydrus_api_typing.AddedFile|None:
        """Добавление файла в Гидрус по ссылке
        Подробнее: https://hydrusnetwork.github.io/hydrus/developer_api.html#add_urls_add_url

        Parameters
        ----------
        url : str
            URL контента в сети
            Контент - не обязательно файл, а лишь то, для чего у Гидруса есть импортёр

        page_name : str | None
            Название страницы, на которую будет добавлен файл
            Если None, то берётся из конфига "DESTINATION_PAGE_NAME"

        tags : Iterable[str] | None
            Добавляемые к файлу теги помимо тех, что захватит импорт

        tags_namespace : str | None
            Название тегового пространства, в которое запишутся теги
            Если None, то берётся из конфига "DESTINATION_PAGE_NAME"

        Returns
        -------
        dict[str, str | int]
            Возвращается ответ добавления файла, содержащий число status и строки hash и note
            Подробнее:
            https://hydrusnetwork.github.io/hydrus/developer_api.html#add_urls_get_url_files
            (первый элемент "url_file_statuses")
        """
        # Без прав на работу со ссылками не имеет смысла
        if not self.check_permission(HydrusPermission.URL_IMPORT_EDIT):
            logger.warning('Отсутствует доступ "import and edit urls"')
            return None
        # Страница в клиенте, на которую добавится импорт
        if page_name is None:
            page_name = self.destination_page_name
        additional_tags = None
        # Создание принимаемого словаря добавляемых тегов
        # в пространстве для service_keys_to_additional_tags
        if tags:
            # service_keys_to_additional_tags требует права на работу с тегами
            if self.check_permission(HydrusPermission.TAGS_EDIT):
                if tags_namespace_hash := self.get_tags_namespace_hash(tags_namespace):
                    # note: clean_tags возвращает не list[str], а {'tags': list[str], ...}
                    additional_tags = {
                        tags_namespace_hash: cast(
                            hydrus_api_typing.CleanedTags,
                            self.client.clean_tags(tags)
                        ).get('tags', [])
                    }
            else:
                logger.warning('Отсутствует доступ "edit file tags"')
        # Проверяем существование контента — к ранее добавленным файлам дополнительно указанные
        # в service_keys_to_additional_tags теги не добавятся, только вытащенные
        # из импорта по ссылке поэтому добавляем их вручную
        url_file_statuses = cast(
            hydrus_api_typing.URLFiles,
            self.client.get_url_files(url)
        ).get("url_file_statuses", [])
        if (
            tags
            and url_file_statuses
            and url_file_statuses[0].get("status", None) == AddedFileStatus.ALREADY_IN_DATABASE
        ):
            # Файл уже есть - вручную проставленные теги сами не доимпортируются, к сожалению
            self.add_tags(
                url_file_statuses[0].get("hash", None),
                tags,
                tags_namespace=tags_namespace
            )
        # Собственно, импорт, как в клиенте
        self.client.add_url(
            url,
            destination_page_name=page_name,
            service_keys_to_additional_tags=additional_tags
        )
        # Получаем и возвращаем статус добавления
        timeout_lenght = 60
        timeout_sleep = 2
        if 'video' in url:
            timeout_lenght *= 10
            timeout_sleep = 30
        timeout_end = time.time() + timeout_lenght
        while time.time() < timeout_end:
            url_file_statuses = cast(
                hydrus_api_typing.URLFiles,
                self.client.get_url_files(url)
            ).get("url_file_statuses", [])
            if url_file_statuses:
                return url_file_statuses[0]
            time.sleep(timeout_sleep)
        return None

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

        additional_tags = None
        # Получаем пространство тегов
        if tags_namespace_hash := self.get_tags_namespace_hash(tags_namespace):
            additional_tags = {
                tags_namespace_hash: cast(
                    hydrus_api_typing.CleanedTags,
                    self.client.clean_tags(tags)
                ).get('tags', [])
            }
        # И добавляем теги
        # note: clean_tags возвращает не list[str], а {'tags': list[str], ...}
        self.client.add_tags(
            hashes=[file_hash,],
            service_keys_to_tags=additional_tags
        )

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
        added_file: list[hydrus_api_typing.AddedFile] = []
        # Импорт файлов в Гидрус по ссылкам
        if urls:
            for url in urls:
                if h_added_file := self.add_file_from_url(
                    url,
                    page_name=page_name,
                    tags=tags,
                    tags_namespace=tags_namespace
                ):
                    added_file.append(h_added_file)
        # Добавление собственно файла в Гидрус
        if content_file and (added_content := self.add_file(content_file, page_name=page_name)):
            added_file.insert(0, added_content)
            # Дописывание тегов и ссылок к файлу
            if tags: self.add_tags(added_content.get("hash"), tags, tags_namespace=tags_namespace)
            if urls: self.add_urls(added_content.get("hash"), urls)
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
        return '\n\n'.join(
            '\n'.join(
                ': '.join(map(str, resp_dict_tuple_item))
                for resp_dict_tuple_item in resp_item.items()
            )
            for resp_item in resp
        )

    def get_file(self, file_hash: str):
        """Получение файла по хэшу

        Parameters
        ----------
        file_hash : str
            Хэш-ключ однозначно определяющий файл, к которому добавляются ссылки

        Returns
        -------
        Response | None
            http-ответ, содержащий сам файл
            Подробнее: https://hydrusnetwork.github.io/hydrus/developer_api.html#get_files_file
        """
        # Проверка доступа и хэша
        if not self.check_permission(HydrusPermission.FILES_SEARCH_FETCH):
            logger.warning('Отсутствует доступ "search and fetch files"')
            return None
        if not file_hash:
            return None
        return self.client.get_file(hash_=file_hash)
