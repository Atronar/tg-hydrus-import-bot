"""
Модуль связанный с запросами к Гидрусу
"""
import os
from typing import Any, Iterable
import hydrus_api

from config import CONF


def get_page_hash_by_name(
        hydrus: hydrus_api.Client,
        page_name: str,
        *,
        pages: Iterable[dict[str, Any]]|None = None
    ) -> str|None:
    """Рекуррентно достаёт из списка pages первую вкладку с нужным именем,
       заходя сначала вглубь, а потом сравнивая имена
    """
    is_root = False
    if pages is None:
        pages = hydrus.get_pages().get('pages',{}).get('pages',())
        is_root = True
    for page in pages:
        if 'pages' in page:
            if page_hash := get_page_hash_by_name(hydrus, page_name, pages=page.get('pages')):
                return page_hash
        if page_name == page.get('name'):
            return page.get('page_key')
    if is_root:
        return None
        # hydrus.add_page или что-то типа того для создания
        # именованной страницы - когда появится подобный апи
        #
        # Возврат хэша добавленной страницы
    return None

def get_tags_namespace_hash(hydrus: hydrus_api.Client, tags_namespace: str|None = None) -> str:
    '''Получение ключа пространства тегов по его названию

    Parameters
    ----------
    tags_namespace : str
        Название пространства тегов

    Returns
    -------
    str
        Непосредственно хэш-ключ, определяющий теговое пространство в гидрусе
    '''
    if tags_namespace is None:
        tags_namespace = CONF["TAGS_NAMESPACE"]
    return hydrus.get_service(service_name=tags_namespace).get('service').get('service_key')

def hydrus_add_file(
        hydrus: hydrus_api.Client,
        file: str|os.PathLike|hydrus_api.BinaryFileLike,
        *,
        page_name: str|None = None
    ) -> dict[str, str|int]:
    hydrus_added_file = hydrus.add_file(file)
    if page_name is None:
        page_name = CONF["DESTINATION_PAGE_NAME"]
    page_key = get_page_hash_by_name(hydrus, page_name)
    # page_key не возвращается, если страницы нет.
    # Если появится возможность добавлять страницы, тогда удалить
    if not page_key:
        return hydrus_added_file
    ###
    hydrus.add_files_to_page(page_key, hashes=[hydrus_added_file.get("hash"),])
    return hydrus_added_file

def hydrus_add_file_from_url(
        hydrus: hydrus_api.Client,
        url: str,
        *,
        page_name: str|None = None,
        tags: Iterable[str]|None = None,
        tags_namespace: str|None = None
    ) -> dict[str, int|str]:
    if page_name is None:
        page_name = CONF["DESTINATION_PAGE_NAME"]
    additional_tags = None
    if tags:
        tags_namespace_hash = get_tags_namespace_hash(hydrus, tags_namespace)
        additional_tags = {tags_namespace_hash: hydrus.clean_tags(tags).get('tags', [])}
    url_file_statuses: list[dict[str, int|str]] = hydrus.get_url_files(url).get("url_file_statuses", [])
    if url_file_statuses and url_file_statuses[0].get("status", None) == 2:
        # Файл уже есть - вручную проставленные теги сами не доимпортируются, к сожалению
        hydrus_add_tags(
            hydrus,
            url_file_statuses[0].get("hash", None),
            tags,
            tags_namespace=tags_namespace
        )
    hydrus.add_url(
        url,
        destination_page_name=page_name,
        service_keys_to_additional_tags=additional_tags
    )
    url_file_statuses = hydrus.get_url_files(url).get("url_file_statuses", [])
    if url_file_statuses:
        return url_file_statuses[0]
    return {}

def hydrus_add_tags(
        hydrus: hydrus_api.Client,
        file_hash: str,
        tags: Iterable[str],
        *,
        tags_namespace: str|None = None
    ):
    if not file_hash:
        return
    tags_namespace_hash = get_tags_namespace_hash(hydrus, tags_namespace)
    hydrus.add_tags(
        hashes=[file_hash,],
        service_keys_to_tags={tags_namespace_hash: hydrus.clean_tags(tags).get('tags', [])}
    )

def hydrus_add_urls(hydrus: hydrus_api.Client, file_hash: str, urls: Iterable[str]):
    if not file_hash:
        return
    hydrus.associate_url(hashes=[file_hash,], urls_to_add=urls)

def hydrus_import(
        hydrus: hydrus_api.Client,
        content_file: str|os.PathLike|hydrus_api.BinaryFileLike = None,
        *,
        page_name: str|None = None,
        urls: Iterable[str] = None,
        tags: Iterable[str] = None,
        tags_namespace: str|None = None
    ) -> list[dict[str, str|int]]:
    if content_file:
        added_file = hydrus_add_file(hydrus, content_file)
        if tags: hydrus_add_tags(hydrus, added_file.get("hash"), tags, tags_namespace=tags_namespace)
        if urls: hydrus_add_urls(hydrus, added_file.get("hash"), urls)
        return [added_file]
    if urls:
        added_file = []
        for url in urls:
            if h_added_file := hydrus_add_file_from_url(
                hydrus,
                url,
                page_name=page_name,
                tags=tags,
                tags_namespace=tags_namespace
            ):
                added_file.append(h_added_file)
        return added_file
    return []

def hydrus_import_resp_to_str(resp: list[dict[str, str|int]]) -> str:
    return '\n\n'.join(
        '\n'.join(
            ': '.join(map(str, resp_dict_tuple_item))
            for resp_dict_tuple_item in resp_item.items()
        )
        for resp_item in resp
    )
