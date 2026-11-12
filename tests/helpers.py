"""Вспомогательные объекты для тестов без реальных Hydrus/Telegram."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

from cachetools import TTLCache
from aiogram import exceptions
from aiogram.methods import SendMessage


class JsonResponse:
    """Минимальный ответ requests.Response для generate_hashes."""

    def __init__(self, payload: dict[str, Any]):
        self._payload = payload

    def json(self) -> dict[str, Any]:
        return self._payload


class FakeResponse:
    """Минимальный requests.Response, который использует send_content_from_response."""

    def __init__(
        self,
        content: bytes = b"",
        *,
        headers: dict[str, str] | None = None,
        chunks: list[bytes] | None = None,
    ):
        self.content = content
        self.headers = headers or {}
        self._chunks = chunks
        self.closed = False

    def iter_content(self, chunk_size: int):
        if self._chunks is not None:
            yield from self._chunks
        elif self.content:
            yield self.content

    def close(self):
        self.closed = True


class FakeMessage:
    """Сообщение Telegram, записывающее вызовы методов ответа."""

    def __init__(
        self,
        *,
        text: str | None = None,
        caption: str | None = None,
        message_id: int = 1,
        photo: list[Any] | None = None,
        file_unique_id: str = "unique",
    ):
        self.text = text
        self.caption = caption
        self.message_id = message_id
        self.photo = photo or []
        self.document = SimpleNamespace(file_unique_id=file_unique_id)
        self.answers: list[tuple[str, dict[str, Any]]] = []
        self.media: list[tuple[str, Any, dict[str, Any]]] = []
        self.media_data: list[tuple[str, bytes, dict[str, Any]]] = []

    async def answer(self, text: str, **kwargs):
        self.answers.append((text, kwargs))
        return self

    async def _answer_media(self, kind: str, file: Any, **kwargs):
        # FSInputFile удаляется сразу после отправки, поэтому сохраняем данные
        if hasattr(file, "path"):
            data = Path(file.path).read_bytes()
        else:
            data = file.data
        self.media.append((kind, file, kwargs))
        self.media_data.append((kind, data, kwargs))
        return self

    async def answer_photo(self, file: Any, **kwargs):
        return await self._answer_media("photo", file, **kwargs)

    async def answer_video(self, file: Any, **kwargs):
        return await self._answer_media("video", file, **kwargs)

    async def answer_animation(self, file: Any, **kwargs):
        return await self._answer_media("animation", file, **kwargs)

    async def answer_audio(self, file: Any, **kwargs):
        return await self._answer_media("audio", file, **kwargs)

    async def answer_document(self, file: Any, **kwargs):
        return await self._answer_media("document", file, **kwargs)


class FakeHydrusClient:
    """Клиент Hydrus API с настраиваемыми ответами и записью вызовов."""

    _GENERATE_HASHES_PATH = "/add_files/generate_hashes"

    def __init__(self):
        self.relationships: dict[str, dict[str, Any]] = {}
        self.metadata: dict[str, dict[str, Any]] = {}
        self.service_key = "tag_service_key"
        self.service_keys: dict[str, str] = {}
        self.pages: dict[str, Any] = {"pages": {"pages": []}}
        self.search_hashes: list[str] = []
        self.generated_hashes: dict[str, Any] = {"hash": "generated_hash"}
        self.cleaned_tags: list[str] = ["cleaned"]
        # Очередь ответов get_url_files для имитации асинхронного импорта
        self.url_file_statuses: dict[str, list[list[dict[str, Any]]]] = {}
        self.url_errors: dict[str, Exception] = {}
        self.add_url_errors: dict[str, Exception] = {}
        self.file_response = object()
        self.calls: list[tuple[str, Any]] = []

    def get_file_relationships(self, hashes):
        self.calls.append(("get_file_relationships", hashes))
        return {"file_relationships": self.relationships}

    def get_service(self, service_name):
        self.calls.append(("get_service", service_name))
        return {
            "service": {
                "service_key": self.service_keys.get(service_name, self.service_key)
            }
        }

    def get_pages(self):
        self.calls.append(("get_pages", None))
        return self.pages

    def get_file_metadata(self, hashes):
        self.calls.append(("get_file_metadata", hashes))
        return {"metadata": [self.metadata[h] for h in hashes if h in self.metadata]}

    def get_file(self, hash_):
        self.calls.append(("get_file", hash_))
        return self.file_response

    def search_files(self, tags, return_hashes=False):
        self.calls.append(("search_files", (tags, return_hashes)))
        return {"hashes": self.search_hashes}

    def generate_hashes(self, path):
        self.calls.append(("generate_hashes", path))
        return self.generated_hashes

    def _api_request(self, method, path, data=None, headers=None):
        self.calls.append(("_api_request", (method, path, data, headers)))
        return JsonResponse(self.generated_hashes)

    def clean_tags(self, tags):
        self.calls.append(("clean_tags", list(tags)))
        return {"tags": self.cleaned_tags}

    def add_tags(self, hashes, service_keys_to_tags):
        self.calls.append(("add_tags", (hashes, service_keys_to_tags)))

    def associate_url(self, hashes, urls_to_add):
        self.calls.append(("associate_url", (hashes, urls_to_add)))

    def add_file(self, file):
        self.calls.append(("add_file", file))
        return {"status": 1, "hash": "file_hash"}

    def add_url(self, url, **kwargs):
        self.calls.append(("add_url", (url, kwargs)))
        if error := self.add_url_errors.get(url):
            raise error

    def get_url_files(self, url):
        self.calls.append(("get_url_files", url))
        if error := self.url_errors.get(url):
            raise error
        responses = self.url_file_statuses.get(url)
        if responses:
            return {"url_file_statuses": responses.pop(0)}
        return {"url_file_statuses": [{"status": 1, "hash": "url_hash"}]}


def make_hydrus(permissions, client: FakeHydrusClient | None = None):
    """Создаёт HydrusRequests без побочных эффектов конструктора."""
    from hydrus_requests import HydrusRequests

    obj = object.__new__(HydrusRequests)
    obj.client = client or FakeHydrusClient()
    obj.permissions = set(permissions)
    obj.default_tags_namespace = "test tags"
    obj.destination_page_name = "test page"
    obj.allow_insecure_api_url = False
    obj._tags_namespace_cache = TTLCache(maxsize=32, ttl=3600)
    obj._pages_cache = TTLCache(maxsize=1, ttl=60)
    return obj


def telegram_exception(exception_type):
    """Создаёт исключение aiogram с валидным TelegramMethod."""
    method = SendMessage(chat_id=1, text="test")
    if exception_type is exceptions.TelegramRetryAfter:
        return exception_type(method, "retry", retry_after=0)
    return exception_type(method, "network error")


def fake_bot_download(responses: list[bytes | Exception]):
    """Бот, который последовательно отдаёт байты или бросает исключение."""
    bot = Mock()
    bot.calls = 0

    async def download(file_id, destination):
        response = responses[bot.calls]
        bot.calls += 1
        if isinstance(response, Exception):
            destination.write(b"partial")
            raise response
        destination.write(response)

    bot.download = download
    return bot
