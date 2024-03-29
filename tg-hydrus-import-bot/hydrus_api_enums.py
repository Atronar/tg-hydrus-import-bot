﻿from enum import IntEnum


class ServiceType(IntEnum):
    """
    Типы сервисов в клиенте
    https://hydrusnetwork.github.io/hydrus/developer_api.html#services_object
    """
    TAG_REPOSITORY = 0
    FILE_REPOSITORY = 1
    LOCAL_FILE_DOMAIN = 2
    LOCAL_TAG_DOMAIN = 5
    NUMERICAL_RATING = 6
    BOOL_RATING = 7
    ALL_KNOWN_TAGS = 10
    ALL_KNOWN_FILES = 11
    LOCAL_BOORU = 12
    IPFS = 13
    TRASH = 14
    ALL_LOCAL_FILES = 15
    FILE_NOTES = 17
    CLIENT_API = 18
    ALL_DELETED_FILES = 19
    LOCAL_UPDATES = 20
    ALL_LOCAL_FILE_DOMAINS = 21
    POSITIVE_INTEGER_RATING = 22
    SERVER_ADMINISTRATION = 99

class HydrusPermission(IntEnum):
    """
    Разрешения
    https://hydrusnetwork.github.io/hydrus/developer_api.html#request_new_permissions
    """
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
    TIMES = 11

class AddedFileStatus(IntEnum):
    """
    Поле status в ответе /add_files/add_file
    https://hydrusnetwork.github.io/hydrus/developer_api.html#add_files_add_file
    """
    SUCCESSFULLY_IMPORTED = 1
    ALREADY_IN_DATABASE = 2
    PREVIOUSLY_DELETED = 3
    FAILED_TO_IMPORT = 4
    VETOED = 7

class PageType(IntEnum):
    """
    Типы страниц
    https://hydrusnetwork.github.io/hydrus/developer_api.html#manage_pages_get_pages
    """
    GALLERY_DOWNLOADER = 1
    SIMPLE_DOWNLOADER = 2
    HARD_DRIVE_IMPORT = 3
    PETITIONS = 5
    FILE_SEARCH = 6
    URL_DOWNLOADER = 7
    DUPLICATES = 8
    THREAD_WATCHER = 9
    PAGE_OF_PAGES = 10

class PageState(IntEnum):
    """
    Состояния страниц
    https://hydrusnetwork.github.io/hydrus/developer_api.html#manage_pages_get_pages
    """
    READY = 0
    INITIALISING = 1
    LOADING = 2
    SEARCH_CANCELLED = 3
