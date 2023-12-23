"""
Модуль связанный с запросами к Гидрусу
"""
from enum import IntEnum
import os
from typing import Any, Iterable, Literal
import hydrus_api
from loguru import logger

from config import CONF


class HydrusPermission(IntEnum):
    URL_IMPORT_EDIT = 0
    FILES_IMPORT_DELETE = 1
    TAGS_EDIT = 2
    FILES_SEARCH_FETCH = 3
    PAGES = 4
    COOKIES_HEADERS = 5
    DATABASE = 6
    NOTES_EDIT = 7
    RELATIONSHIPS_EDIT = 8
    RATINGS_EDIT = 9
    POPUPS = 10

class HydrusRequests:
    """Основной класс модуля
    Позволяет производить действия, связанные с Гидрусом

    Parameters
    ----------
    hydrus : hydrus_api.Client | str
        Либо уже готовый объект клиента, который и будет использоваться,
        либо токен, по которому и будет создан объект клиента
    """
    def __init__(self, hydrus: hydrus_api.Client|str):
        self.set_client(hydrus)
        self.get_permission_info()

    def set_client(self, hydrus: hydrus_api.Client|str):
        """Установка значения клиента

        Parameters
        ----------
        hydrus : hydrus_api.Client | str
            Либо уже готовый объект клиента,
            либо токен, по которому и будет создан объект клиента
        """
        if isinstance(hydrus, str):
            self.client = hydrus_api.Client(access_key=hydrus)
        else:
            self.client = hydrus

    def get_permission_info(self) -> set[HydrusPermission]:
        """Получение списка разрешений"""
        self.permission_info = self.client.verify_access_key()
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
            pages: Iterable[dict[str, Any]]|None = None
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
            pages = self.client.get_pages().get('pages',{}).get('pages',())
            is_root = True
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

    def get_tags_namespace_hash(self, tags_namespace: str|None = None) -> str:
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
        if tags_namespace is None:
            tags_namespace = CONF["TAGS_NAMESPACE"]
        return self.client.get_service(service_name=tags_namespace).get('service').get('service_key')

    def add_file(
            self,
            file: str|os.PathLike|hydrus_api.BinaryFileLike,
            *,
            page_name: str|None = None
        ) -> dict[Literal["status", "hash", "note"], str|int]:
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
        # Добавление файла в Гидрус
        hydrus_added_file = self.client.add_file(file)
        # Берём ключ страницы по имени
        if page_name is None:
            page_name = CONF["DESTINATION_PAGE_NAME"]
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
        ) -> dict[Literal["status", "hash", "note"], int|str]:
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
            Подробнее: https://hydrusnetwork.github.io/hydrus/developer_api.html#add_urls_get_url_files
            (первый элемент "url_file_statuses")
        """
        # Страница в клиенте, на которую добавится импорт
        if page_name is None:
            page_name = CONF["DESTINATION_PAGE_NAME"]
        additional_tags = None
        # Создание принимаемого словаря добавляемых тегов
        # в пространстве для service_keys_to_additional_tags
        if tags:
            tags_namespace_hash = self.get_tags_namespace_hash(tags_namespace)
            # note: clean_tags возвращает не list[str], а {'tags': list[str], ...}
            additional_tags = {tags_namespace_hash: self.client.clean_tags(tags).get('tags', [])}
        # Проверяем существование контента — к ранее добавленным файлам дополнительно указанные
        # в service_keys_to_additional_tags теги не добавятся, только вытащенные
        # из импорта по ссылке поэтому добавляем их вручную
        url_file_statuses: list[dict[Literal["status", "hash", "note"], int|str]] = \
            self.client.get_url_files(url).get("url_file_statuses", [])
        if url_file_statuses and url_file_statuses[0].get("status", None) == 2:
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
        url_file_statuses = self.client.get_url_files(url).get("url_file_statuses", [])
        if url_file_statuses:
            return url_file_statuses[0]
        return {}

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
        # Как бы то ни было, пустые хэш и теги бессмысленны
        if not file_hash or not tags:
            return
        # Получаем пространство тегов
        tags_namespace_hash = self.get_tags_namespace_hash(tags_namespace)
        # И добавляем теги
        # note: clean_tags возвращает не list[str], а {'tags': list[str], ...}
        self.client.add_tags(
            hashes=[file_hash,],
            service_keys_to_tags={tags_namespace_hash: self.client.clean_tags(tags).get('tags', [])}
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
        # Как бы то ни было, пустые хэш и ссылки бессмысленны
        if not file_hash or not urls:
            return
        self.client.associate_url(hashes=[file_hash,], urls_to_add=urls)

    def import_content(
            self,
            content_file: str|os.PathLike|hydrus_api.BinaryFileLike|None = None,
            *,
            page_name: str|None = None,
            urls: Iterable[str] = None,
            tags: Iterable[str] = None,
            tags_namespace: str|None = None
        ) -> list[dict[Literal["status", "hash", "note"], str|int]]:
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
            Возвращается список ответов добавления файла, содержащие число status и строки hash и note
            Подробнее: https://hydrusnetwork.github.io/hydrus/developer_api.html#add_urls_get_url_files
            (первый элемент "url_file_statuses")
            Каждый элемент списка - ответ добавления content_file и каждого элемента urls
        """
        added_file: list[dict[Literal["status", "hash", "note"], str|int]] = []
        # Добавление собственно файла в Гидрус
        if content_file and (added_content := self.add_file(content_file, page_name=page_name)):
            added_file.append(added_content)
            # Дописывание тегов и ссылок к файлу
            if tags: self.add_tags(added_content.get("hash"), tags, tags_namespace=tags_namespace)
            if urls: self.add_urls(added_content.get("hash"), urls)
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
        # Возврат итогового результата
        return added_file

    @staticmethod
    def convert_import_resp_to_str(resp: list[dict[str, str|int]]) -> str:
        """Всего лишь метод, преобразовывающий ответ hydrus_import (в виде списка словарей),
        в человекочитаемом формате сообщения

        Parameters
        ----------
        list[dict[str, str | int]]
            Список ответов добавления файла
            Подробнее: https://hydrusnetwork.github.io/hydrus/developer_api.html#add_urls_get_url_files
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
        if not self.check_permission(HydrusPermission.FILES_SEARCH_FETCH) or not file_hash:
            logger.warning('Отсутствует доступ "search and fetch files"')
            return None
        return self.client.get_file(hash_=file_hash)
