from typing import NotRequired, TypedDict


class PermissionInfo(TypedDict):
    basic_permissions: list[int]
    human_description: str

class Service(TypedDict):
    name: str
    service_key: str
    type: int
    type_pretty: str

class GetServiceResponse(TypedDict):
    service: Service

class AddedFile(TypedDict):
    status: int
    hash: str
    note: str

class URLFiles(TypedDict):
    normalised_url: str
    url_file_statuses: list[AddedFile]

class AddedURL(TypedDict):
    human_result_text: str
    normalised_url: str

class CleanedTags(TypedDict):
    tags: list[str]

class Page(TypedDict):
    name: str
    page_key: str
    page_state: int
    page_type: int
    selected: bool
    pages: NotRequired[list['Page']]

class TopPages(TypedDict):
    pages: Page

class TagsNamespace(TypedDict):
    name: str
    type: int
    type_pretty: str
    storage_tags: dict[str, list[str]]
    display_tags: dict[str, list[str]]

class FileRelationship(TypedDict):
    is_king: bool
    king: str
    king_is_on_file_domain: bool
    king_is_local: bool

class FileRelationships(TypedDict):
    file_relationships: dict[str, FileRelationship]

class FileHashes(TypedDict):
    hash: str
    perceptual_hashes: NotRequired[list[str]]
    pixel_hash: NotRequired[str]
