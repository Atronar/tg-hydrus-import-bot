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
