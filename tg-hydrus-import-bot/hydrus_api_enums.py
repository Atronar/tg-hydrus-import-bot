from enum import IntEnum


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
