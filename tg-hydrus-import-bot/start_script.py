"""
Основной файл программы
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from functools import partial, wraps
import html
import io
import os
import re
import time
import urllib.request
from uuid import uuid4

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaAudio,
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
    Message,
)
from aiogram import F, exceptions
import hydrus_api
from loguru import logger

from config import CONF
from duplicates import load_priority_domains
from tools import bytes_strformat, get_temp_folder, multiline_to_hashtags, split_text
from hydrus_api_enums import AddedFileStatus, DuplicateRelationship
from hydrus_requests import HydrusRequests
from saucenao_api import SauceNAO, SauseNAOException
from tg_parse_requests import get_tags_from_msg, get_urls_from_msg, \
    answer_disabled_content, send_content_from_response, get_success_reply_str, \
    mime_subtype, _content_length, MAX_PHOTO_SIZE

MAX_MESSAGE_LENGTH = 4000
MAX_TELEGRAM_ATTEMPTS = 3
TELEGRAM_NETWORK_RETRY_DELAY = 60

HELP_TEXT = """Бот импортирует контент из Telegram в Hydrus Network.

Отправьте фото, видео, анимацию, видеосообщение, аудио, голосовое сообщение, документ или текст. Теги-хештеги и ссылки берутся из подписи или текста сообщения.

Команды:
/to_tags — показать теги, извлечённые из хештегов в сообщении
/to_hashtags — преобразовать построчный список тегов в хештеги
/sauce_by_hash <хэш>... — найти источники через SauceNAO и импортировать их
/tags_by_hash <хэш>... — показать теги файлов по хэшам
/hashtags_by_hash <хэш>... — показать хештеги файлов по хэшам
/get_hash — в подписи к фото: показать хэш файла, если он уже есть в Hydrus

Командам с хэшами нужны права Hydrus "search and fetch files" и "manage file relationships". Команды SauceNAO работают, только если задан SAUCENAO_TOKEN."""

# Все синхронные запросы к Hydrus идут через один поток:
# это не блокирует event loop и не даёт использовать requests.Session конкурентно.
_HYDRUS_EXECUTOR = ThreadPoolExecutor(
    max_workers=1,
    thread_name_prefix="hydrus"
)


async def run_hydrus(func, *args, **kwargs):
    """Выполняет синхронную операцию Hydrus в отдельном потоке."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        _HYDRUS_EXECUTOR,
        partial(func, *args, **kwargs)
    )


def make_temp_file_path(file_unique_id: str, suffix: str = "") -> str:
    """Возвращает уникальный путь во временной папке.

    file_unique_id сам по себе не уникален между разными сообщениями,
    поэтому добавляем случайный префикс.
    """
    safe_id = re.sub(r"[^A-Za-z0-9_-]+", "", file_unique_id)[:64] or "file"
    safe_suffix = re.sub(r"[^A-Za-z0-9]+", "", suffix)[:16]
    filename = f"{uuid4().hex}_{safe_id}"
    if safe_suffix:
        filename = f"{filename}.{safe_suffix}"
    return os.path.join(get_temp_folder(), filename)


def remove_temp_file(path: str) -> None:
    """Удаляет временный файл, не мешая обработке ошибок."""
    try:
        os.unlink(path)
    except OSError:
        pass


def _sauce_file_error(headers) -> str | None:
    """Возвращает причину, по которой файл нельзя отправить в SauceNAO."""
    content_type = headers.get("Content-Type", "")
    if not content_type.lower().startswith("image/"):
        return "файл не является изображением"
    if _content_length(headers) > MAX_PHOTO_SIZE:
        return "файл слишком большой для SauceNAO"
    return None


def parse_hash_args(args: str | None) -> list[str]:
    """Разбирает список хэшей из аргументов команды."""
    return (args or "").split()


async def import_file_with_duplicates(
    hydrus: HydrusRequests,
    content: str | io.IOBase,
    *,
    mime: str | None,
    tags: list[str]
) -> list[dict]:
    """Импортирует файл, если точного дубликата ещё нет.

    Точный дубликат не загружается повторно: теги добавляются
    существующему файлу, а в ответ попадает статус «уже в базе».
    """
    duplicate = await run_hydrus(
        hydrus.find_exact_duplicate,
        content,
        mime=mime,
        tags=tags
    )
    if duplicate:
        return [duplicate]
    if hasattr(content, "seek"):
        content.seek(0)
    return await run_hydrus(
        hydrus.import_content,
        content,
        tags=tags
    )


DUPLICATE_PENDING_TTL = 3600
_PENDING_DUPLICATES: dict[str, dict] = {}


def _cleanup_pending_duplicates(now: float | None = None) -> None:
    """Удаляет решения по дубликатам, которые слишком долго ждали выбора."""
    current = time.time() if now is None else now
    for token, pending in list(_PENDING_DUPLICATES.items()):
        if current - pending.get("created_at", current) > DUPLICATE_PENDING_TTL:
            _PENDING_DUPLICATES.pop(token, None)


def _store_pending_duplicate(data: dict) -> str:
    """Сохраняет ожидающее решение и возвращает его токен."""
    _cleanup_pending_duplicates()
    token = uuid4().hex
    _PENDING_DUPLICATES[token] = {**data, "created_at": time.time()}
    return token


def _take_pending_duplicate(token: str) -> dict | None:
    """Возвращает и удаляет ожидающее решение по токену."""
    _cleanup_pending_duplicates()
    return _PENDING_DUPLICATES.pop(token, None)


def _duplicate_keyboard(token: str) -> InlineKeyboardMarkup:
    """Клавиатура выбора для пары похожих файлов."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text="Оставить первый (существующий)",
                callback_data=f"dup:keep_existing:{token}"
            )],
            [InlineKeyboardButton(
                text="Оставить второй (новый)",
                callback_data=f"dup:keep_new:{token}"
            )],
            [InlineKeyboardButton(
                text="Оставить оба — альтернативы",
                callback_data=f"dup:both_alt:{token}"
            )],
            [InlineKeyboardButton(
                text="Оставить оба — не связаны",
                callback_data=f"dup:both_unrelated:{token}"
            )],
        ]
    )


def _format_timestamp(value: object) -> str | None:
    """Форматирует время Hydrus, если оно известно."""
    if not isinstance(value, (int, float)) or value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M")
    except (OSError, OverflowError, ValueError):
        return None


def _format_file_info(info: dict | None) -> str:
    """Кратко описывает файл по метаданным Hydrus."""
    if not info:
        return "нет данных"
    parts: list[str] = []
    if mime := info.get("mime"):
        parts.append(str(mime))
    if isinstance(size := info.get("size"), int):
        parts.append(bytes_strformat(size))
    width = info.get("width")
    height = info.get("height")
    if isinstance(width, int) and isinstance(height, int):
        parts.append(f"{width}×{height}")
    if isinstance(duration := info.get("duration"), (int, float)):
        parts.append(f"{duration:.1f} с")
    for label, key in (("изменён", "time_modified"), ("архивирован", "time_archived")):
        if timestamp := _format_timestamp(info.get(key)):
            parts.append(f"{label}: {timestamp}")
    urls = [
        url for url in (info.get("known_urls") or [])
        if isinstance(url, str)
    ]
    if urls:
        shown = ", ".join(urls[:3])
        if len(urls) > 3:
            shown = f"{shown} …"
        parts.append(f"ссылки: {shown}")
    return "; ".join(parts) or "нет данных"


def format_duplicate_details(pending: dict) -> str:
    """Текст с параметрами существующего и нового файлов."""
    distance = int(pending.get("distance", 0))
    if distance:
        similarity = f"Похож в пределах {distance}."
    else:
        similarity = "Перцептивное расстояние 0."
    lines = [
        "Найден похожий файл.",
        similarity,
        "",
        "Первый (существующий):",
        f"hash: {pending.get('existing_hash')}",
        _format_file_info(pending.get("existing_info")),
        "",
        "Второй (новый):",
        f"hash: {pending.get('new_hash')}",
        _format_file_info(pending.get("new_info")),
    ]
    if url := pending.get("url"):
        lines.extend(["", f"Новая ссылка: {url}"])
    return "\n".join(lines)


def _media_for_duplicate(
    content: bytes,
    info: dict | None,
    label: str,
    file_hash: str
):
    """Создаёт элемент медиагруппы по MIME-типу файла."""
    mime = str((info or {}).get("mime") or "")
    extension = mime_subtype(mime, "bin") or "bin"
    input_file = BufferedInputFile(
        content,
        f"{label}_{file_hash[:8]}.{extension}"
    )
    if mime.startswith("image/"):
        return InputMediaPhoto(media=input_file)
    if mime.startswith("video/"):
        return InputMediaVideo(media=input_file, supports_streaming=True)
    if mime.startswith("audio/"):
        return InputMediaAudio(media=input_file)
    return InputMediaDocument(media=input_file)


async def send_duplicate_prompt(
    bot: Bot,
    msg: Message,
    hydrus: HydrusRequests,
    pending: dict
) -> None:
    """Показывает оба файла и клавиатуру выбора."""
    media = []
    for file_hash, info, label in (
        (pending.get("existing_hash"), pending.get("existing_info"), "existing"),
        (pending.get("new_hash"), pending.get("new_info"), "new"),
    ):
        if not file_hash:
            continue
        response = await run_hydrus(hydrus.get_file_bytes, file_hash)
        if not response:
            continue
        try:
            content = getattr(response, "content", b"")
            if content:
                media.append(
                    _media_for_duplicate(content, info, label, file_hash)
                )
        finally:
            close = getattr(response, "close", None)
            if close:
                close()

    if len(media) == 2:
        try:
            await bot.send_media_group(
                msg.chat.id,
                media,
                reply_to_message_id=msg.message_id
            )
        except (exceptions.TelegramBadRequest, exceptions.TelegramNetworkError) as ex:
            logger.warning(f"Не удалось отправить медиагруппу дубликатов: {ex}")

    await msg.answer(
        format_duplicate_details(pending),
        reply_markup=_duplicate_keyboard(str(pending["token"]))
    )


async def apply_duplicate_action(
    hydrus: HydrusRequests,
    pending: dict,
    action: str
) -> str:
    """Применяет выбор пользователя к паре дубликатов."""
    existing_hash = str(pending.get("existing_hash") or "")
    new_hash = str(pending.get("new_hash") or "")
    url = pending.get("url")
    tags = list(pending.get("tags") or [])

    if action == "keep_existing":
        if url:
            await run_hydrus(hydrus.add_urls, existing_hash, [url])
        if tags:
            await run_hydrus(hydrus.add_tags, existing_hash, tags)
        applied = await run_hydrus(
            hydrus.set_duplicate_relationship,
            existing_hash,
            new_hash,
            merge_content=True,
            delete_worse=True
        )
        if applied:
            return "Оставлен существующий файл, новая копия удалена."
        return "Не удалось оставить существующий файл. Смотри логи."

    if action == "keep_new":
        if tags:
            await run_hydrus(hydrus.add_tags, new_hash, tags)
        applied = await run_hydrus(
            hydrus.set_duplicate_relationship,
            new_hash,
            existing_hash,
            merge_content=True,
            delete_worse=True
        )
        if applied:
            return "Оставлен новый файл, существующая копия удалена."
        return "Не удалось оставить новый файл. Смотри логи."

    if action == "both_alt":
        applied = await run_hydrus(
            hydrus.set_duplicate_relationship,
            new_hash,
            existing_hash,
            relationship=DuplicateRelationship.ALTERNATE,
            merge_content=False,
            delete_worse=False
        )
        if applied:
            return "Оставлены оба файла как альтернативы."
        return "Не удалось связать файлы. Смотри логи."

    if action == "both_unrelated":
        applied = await run_hydrus(
            hydrus.set_duplicate_relationship,
            new_hash,
            existing_hash,
            relationship=DuplicateRelationship.FALSE_POSITIVE,
            merge_content=False,
            delete_worse=False
        )
        if applied:
            return "Оставлены оба файла, связь не создана."
        return "Не удалось убрать связь. Смотри логи."

    return "Неизвестное действие."


async def _answer_once(msg: Message, text: str, **kwargs) -> Message | None:
    """Отправляет одно сообщение, не роняя обработчик на отказе Telegram."""
    try:
        return await msg.answer(
            text,
            reply_to_message_id=msg.message_id,
            **kwargs
        )
    except exceptions.TelegramBadRequest as ex:
        logger.error(f"Telegram отклонил сообщение: {ex}")
        return None


async def _answer_with_retry_once(msg: Message, text: str, **kwargs) -> Message | None:
    """Отправляет одно сообщение, повторяя попытку при временных сбоях."""
    for attempt in range(MAX_TELEGRAM_ATTEMPTS):
        try:
            return await _answer_once(msg, text, **kwargs)
        except exceptions.TelegramRetryAfter as ex:
            if attempt + 1 >= MAX_TELEGRAM_ATTEMPTS:
                raise
            logger.warning(f"Telegram просит подождать {ex.retry_after} сек. {ex}")
            await asyncio.sleep(ex.retry_after)
        except exceptions.TelegramNetworkError as ex:
            if attempt + 1 >= MAX_TELEGRAM_ATTEMPTS:
                raise
            logger.warning(
                f"Сетевая ошибка. Повтор через {TELEGRAM_NETWORK_RETRY_DELAY} секунд. {ex}"
            )
            await asyncio.sleep(TELEGRAM_NETWORK_RETRY_DELAY)
    return None


async def answer_with_retry(msg: Message, text: str, **kwargs) -> Message | None:
    """Отправляет текст, разбивая слишком длинные сообщения.

    Пустые сообщения не отправляются: Telegram отвечает на них BadRequest.
    """
    if not text or not text.strip():
        logger.warning("Пустое сообщение не отправлено")
        return None

    result = None
    for fragment in split_text(text, MAX_MESSAGE_LENGTH):
        result = await _answer_with_retry_once(msg, fragment, **kwargs)
    return result


async def answer_long_with_retry(msg: Message, text: str, **kwargs):
    """Отправляет длинный текст несколькими сообщениями."""
    return await answer_with_retry(msg, text, **kwargs)


def handle_request_errors(func):
    """Отвечает пользователю, если Hydrus или Telegram вернули ошибку.

    Без этого обработчик молча падал, и пользователь не получал ответа.
    """
    @wraps(func)
    async def wrapper(msg: Message, *args, **kwargs):
        try:
            return await func(msg, *args, **kwargs)
        except hydrus_api.HydrusAPIException as ex:
            logger.error(f"Ошибка Hydrus API: {ex}")
            await answer_with_retry(msg, "Ошибка Hydrus API. Смотри логи.")
        except exceptions.TelegramBadRequest as ex:
            logger.error(f"Telegram отклонил ответ: {ex}")
            await answer_with_retry(
                msg,
                "Telegram отклонил ответ. Смотри логи."
            )
        except Exception as ex:
            # Не оставляем пользователя без ответа при неожиданной ошибке.
            logger.exception(f"Непредвиденная ошибка: {ex}")
            await answer_with_retry(
                msg,
                "Непредвиденная ошибка. Смотри логи."
            )
    return wrapper


async def download_with_retry(
    bot: Bot,
    file_id: str,
    destination: str | io.IOBase
):
    """Скачивает файл, повторяя попытку при сетевой ошибке.

    Буфер перед каждой попыткой очищается, чтобы повтор не дописывал
    данные в уже частично заполненный файл.
    """
    def reset_destination():
        if hasattr(destination, "seek") and hasattr(destination, "truncate"):
            destination.seek(0)
            destination.truncate(0)

    for attempt in range(MAX_TELEGRAM_ATTEMPTS):
        reset_destination()
        try:
            await bot.download(file_id, destination)
            return
        except exceptions.TelegramRetryAfter as ex:
            if attempt + 1 >= MAX_TELEGRAM_ATTEMPTS:
                raise
            logger.warning(f"Telegram просит подождать {ex.retry_after} сек. {ex}")
            await asyncio.sleep(ex.retry_after)
        except exceptions.TelegramNetworkError as ex:
            if attempt + 1 >= MAX_TELEGRAM_ATTEMPTS:
                raise
            logger.warning(
                f"Сетевая ошибка. Повтор через {TELEGRAM_NETWORK_RETRY_DELAY} секунд. {ex}"
            )
            await asyncio.sleep(TELEGRAM_NETWORK_RETRY_DELAY)


def get_telegram_proxy(configured_proxy: str | None = None) -> str | None:
    """Возвращает прокси для Telegram Bot API.

    Приоритет: настройка TG_PROXY, переменные окружения, системный прокси.
    Пустая строка или None означают прямое подключение.
    """
    explicit_proxy = (configured_proxy or "").strip()
    if explicit_proxy:
        return explicit_proxy

    proxy = next(
        (
            value.strip()
            for name in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy")
            if (value := os.getenv(name, "")).strip()
        ),
        "",
    )
    if not proxy:
        proxies = urllib.request.getproxies()
        if os.name == "nt" and not proxies.get("https"):
            # getproxies() не читает реестр, если задан NO_PROXY,
            # поэтому системный прокси Windows запрашиваем отдельно.
            registry_getter = getattr(urllib.request, "getproxies_registry", None)
            if registry_getter:
                try:
                    proxies.update(registry_getter())
                except OSError as ex:
                    logger.warning(f"Не удалось прочитать системный прокси: {ex}")
        proxy = (proxies.get("https") or proxies.get("http") or "").strip()

    if proxy:
        try:
            if urllib.request.proxy_bypass("api.telegram.org"):
                return None
        except OSError:
            pass
    return proxy or None


async def start_script():
    proxy = get_telegram_proxy(CONF.TG_PROXY)
    session = AiohttpSession(proxy=proxy)
    if proxy:
        logger.info("Telegram Bot API: используется прокси")
    bot = Bot(token=CONF.TG_BOT_TOKEN, session=session)
    dp = Dispatcher()
    # Единый фильтр для всех сообщений: только администраторы из конфига
    dp.message.filter(F.from_user.id.in_(CONF.TG_ADMIN_ID))
    dp.callback_query.filter(F.from_user.id.in_(CONF.TG_ADMIN_ID))
    hydrus = await run_hydrus(
        HydrusRequests,
        CONF.HYDRUS_TOKEN,
        api_url=CONF.HYDRUS_API_URL,
        default_tags_namespace=CONF.TAGS_NAMESPACE,
        default_destination_page_name=CONF.DESTINATION_PAGE_NAME,
        allow_insecure_api_url=CONF.ALLOW_INSECURE_HYDRUS
    )
    if CONF.SAUCENAO_TOKEN:
        sauce_nao = SauceNAO(CONF.SAUCENAO_TOKEN)
        sauce_nao.set_similarity(80)
    else:
        sauce_nao = None
    priority_domains = load_priority_domains()

    @dp.message(CommandStart())
    async def start_handler(msg: Message):
        logger.info("Запуск start_handler")
        await answer_with_retry(msg, HELP_TEXT)

    @dp.message(Command("help"))
    async def help_handler(msg: Message):
        logger.info("Запуск help_handler")
        await answer_with_retry(msg, HELP_TEXT)

    @dp.message(Command("to_tags"))
    async def to_tags_handler(msg: Message):
        logger.info("Запуск to_tags_handler")
        if not msg.text:
            return
        reply = '\n'.join(get_tags_from_msg(msg))
        if not reply:
            await answer_with_retry(msg, "Теги не найдены")
            return
        logger.info(f"Отправленный ответ: {reply[:100]}")
        for fragment in split_text(reply, MAX_MESSAGE_LENGTH - 100):
            await answer_with_retry(
                msg,
                f"<pre>{html.escape(fragment)}</pre>",
                parse_mode="HTML"
            )

    @dp.message(Command("to_hashtags"))
    async def to_hashtags_handler(msg: Message, command: CommandObject):
        logger.info("Запуск to_hashtags_handler")
        if not msg.text:
            return
        reply = multiline_to_hashtags(
            command.args or "",
            remove_namespaces=True
        )
        if not reply:
            await answer_with_retry(msg, "Теги не найдены")
            return
        logger.info(f"Отправленный ответ: {reply[:100]}")
        await answer_long_with_retry(msg, reply)

    @dp.message(Command("sauce_by_hash"))
    async def sauce_by_hash_handler(msg: Message, command: CommandObject):
        logger.info("Запуск sauce_by_hash_handler")
        if not msg.text:
            return
        if not sauce_nao:
            await answer_with_retry(msg, "SauceNAO не настроен")
            return
        hashes = parse_hash_args(command.args)
        if not hashes:
            await answer_with_retry(msg, "Не указан хэш")
            return
        for hash_str in hashes:
            logger.info(f"Обработка {hash_str}")
            try:
                content_file = await run_hydrus(hydrus.get_file, hash_str)
                if not content_file:
                    await answer_with_retry(msg, f"{hash_str}: файл не найден")
                    continue
                try:
                    if file_error := _sauce_file_error(content_file.headers):
                        await answer_with_retry(
                            msg,
                            f"{hash_str}: {file_error}"
                        )
                        continue
                    content = content_file.content
                    content_size = _content_length(content_file.headers)
                finally:
                    content_file.close()
                try:
                    urls = await asyncio.to_thread(
                        sauce_nao.get_sources,
                        content
                    )
                except SauseNAOException as ex:
                    logger.error(f"Ошибка SauceNAO для {hash_str}: {ex}")
                    await answer_with_retry(
                        msg,
                        f"{hash_str}: ошибка SauceNAO ({ex})"
                    )
                    continue
                if not urls:
                    await answer_with_retry(msg, f"{hash_str}: источники не найдены")
                    continue
                resp = await run_hydrus(hydrus.import_content, urls=urls)
            except hydrus_api.HydrusAPIException as ex:
                logger.error(f"Ошибка Hydrus для {hash_str}: {ex}")
                await answer_with_retry(msg, f"{hash_str}: ошибка Hydrus API")
                continue

            resp_str = hydrus.convert_import_resp_to_str(resp)
            reply = get_success_reply_str(
                "хэш",
                resp_str,
                content_size=content_size
            )
            logger.info(f"Отправленный ответ: {reply[:100]}")
            await answer_long_with_retry(msg, reply)

    @dp.message(Command("tags_by_hash"))
    async def tags_by_hash_handler(msg: Message, command: CommandObject):
        logger.info("Запуск tags_by_hash_handler")
        if not msg.text:
            return
        hashes = parse_hash_args(command.args)
        if not hashes:
            await answer_with_retry(msg, "Не указан хэш")
            return
        for hash_str in hashes:
            logger.info(f"Обработка {hash_str}")
            try:
                tags = await run_hydrus(hydrus.get_tags, hash_str)
            except hydrus_api.HydrusAPIException as ex:
                logger.error(f"Ошибка Hydrus для {hash_str}: {ex}")
                await answer_with_retry(msg, f"{hash_str}: ошибка Hydrus API")
                continue
            if not tags:
                await answer_with_retry(msg, f"{hash_str}: теги не найдены")
                continue
            reply = f"{hash_str}\n\n" + '\n'.join(tags)
            logger.info(f"Отправленный ответ: {reply[:100]}")
            await answer_long_with_retry(msg, reply)

    @dp.message(Command("hashtags_by_hash"))
    async def hashtags_by_hash_handler(msg: Message, command: CommandObject):
        logger.info("Запуск hashtags_by_hash_handler")
        if not msg.text:
            return
        hashes = parse_hash_args(command.args)
        if not hashes:
            await answer_with_retry(msg, "Не указан хэш")
            return
        for hash_str in hashes:
            logger.info(f"Обработка {hash_str}")
            try:
                tags = await run_hydrus(hydrus.get_tags, hash_str)
            except hydrus_api.HydrusAPIException as ex:
                logger.error(f"Ошибка Hydrus для {hash_str}: {ex}")
                await answer_with_retry(msg, f"{hash_str}: ошибка Hydrus API")
                continue
            hashtags = multiline_to_hashtags(
                '\n'.join(tags),
                remove_namespaces=True
            )
            if not hashtags:
                await answer_with_retry(msg, f"{hash_str}: теги не найдены")
                continue
            reply = f"{hash_str}\n\n{hashtags}"
            logger.info(f"Отправленный ответ: {reply[:100]}")
            await answer_long_with_retry(msg, reply)

    @dp.message(F.photo, F.caption.contains('/get_hash'))
    async def get_hash_handler(msg: Message):
        logger.info("Запуск get_hash_handler")
        if not msg.photo:
            return
        photo = msg.photo[-1]
        content_file = io.BytesIO()
        await download_with_retry(bot, photo.file_id, content_file)

        try:
            resp = await run_hydrus(hydrus.get_file_hash, content_file)
        except hydrus_api.HydrusAPIException as ex:
            logger.warning(f"Не удалось получить хэш из буфера: {ex}")
            temp_path = make_temp_file_path(photo.file_unique_id, "jpg")
            try:
                await download_with_retry(bot, photo.file_id, temp_path)
                resp = await run_hydrus(hydrus.get_file_hash, temp_path)
            except hydrus_api.HydrusAPIException as retry_ex:
                logger.error(f"Не удалось получить хэш из файла: {retry_ex}")
                resp = None
            finally:
                remove_temp_file(temp_path)

        reply = resp or "Файл не найден в системе или недостаточно прав"
        logger.info(f"Отправленный ответ: {reply}")
        await answer_with_retry(msg, reply)

    @dp.message()
    @handle_request_errors
    async def message_handler(msg: Message):
        logger.info("Запуск message_handler")
        sauce_error: str | None = None
        pending_prompts: list[dict] = []
        pending_new_hashes: set[str] = set()
        if msg_content := msg.photo:
            if not (msg.photo and "photo" in CONF.CONTENT_TYPES):
                await answer_disabled_content(msg, "photo")
                return

            msg_content = msg_content[-1]
            content_file = io.BytesIO()

            # Из текста достаём теги и ссылки
            tags = get_tags_from_msg(msg)
            urls = get_urls_from_msg(msg)

            await bot.download(
                msg_content.file_id,
                content_file
            )

            if not (tags or urls) and sauce_nao:
                try:
                    urls = await asyncio.to_thread(
                        sauce_nao.get_sources, content_file
                    )
                except SauseNAOException as ex:
                    logger.error(f"Ошибка SauceNAO: {ex}")
                    sauce_error = (
                        "SauceNAO: не удалось выполнить поиск источников."
                    )

            resp = []
            if urls:
                resp.extend(await run_hydrus(
                    hydrus.add_files_from_urls,
                    urls,
                    tags=tags
                ))
            resp.extend(await import_file_with_duplicates(
                hydrus,
                content_file,
                mime="image/jpeg",
                tags=tags
            ))
            logger.debug(f"{resp}")
            resp_str = hydrus.convert_import_resp_to_str(resp)

            content_file.seek(0, io.SEEK_END)

            reply = get_success_reply_str(
                "фото",
                resp_str,
                content_size=content_file.tell()
            )
        elif msg_content := (msg.video or msg.animation or msg.video_note):
            if not ((msg.video and "video" in CONF.CONTENT_TYPES) \
                or (msg.animation and "animation" in CONF.CONTENT_TYPES) \
                or (msg.video_note and "video_note" in CONF.CONTENT_TYPES)):
                await answer_disabled_content(msg, type(msg_content).__name__)
                return

            mime = getattr(msg_content, "mime_type", None) or "video/mp4"
            file_format = mime_subtype(mime, "mp4")
            content_file = make_temp_file_path(
                msg_content.file_unique_id,
                file_format
            )
            try:
                # Из текста достаём теги и ссылки
                tags = get_tags_from_msg(msg)
                urls = get_urls_from_msg(msg)

                await bot.download(
                    msg_content.file_id,
                    content_file
                )

                resp = []
                if urls:
                    resp.extend(await run_hydrus(
                        hydrus.add_files_from_urls,
                        urls,
                        tags=tags
                    ))
                resp.extend(await import_file_with_duplicates(
                    hydrus,
                    content_file,
                    mime=mime,
                    tags=tags
                ))
                logger.debug(f"{resp}")
                resp_str = hydrus.convert_import_resp_to_str(resp)

                reply = get_success_reply_str(
                    "видео",
                    resp_str,
                    content_size=os.path.getsize(content_file)
                )
            finally:
                remove_temp_file(content_file)
        elif msg_content := (msg.audio or msg.voice):
            if not ((msg.audio and "audio" in CONF.CONTENT_TYPES) \
                or (msg.voice and "voice" in CONF.CONTENT_TYPES)):
                await answer_disabled_content(msg, type(msg_content).__name__)
                return

            mime = msg_content.mime_type or "audio/mpeg"
            file_format = mime_subtype(mime, "mp3")
            if file_format in ("mpeg", "mp3"):
                file_format = "mp3"
            content_file = make_temp_file_path(
                msg_content.file_unique_id,
                file_format
            )
            try:
                # Из текста достаём теги и ссылки
                tags = get_tags_from_msg(msg)
                if msg_content_title := getattr(msg_content, "title", None):
                    tags.append(f"title:{msg_content_title}")
                if msg_content_performer := getattr(msg_content, "performer", None):
                    tags.append(f"artist:{msg_content_performer}")
                urls = get_urls_from_msg(msg)

                await bot.download(
                    msg_content.file_id,
                    content_file
                )

                resp = []
                if urls:
                    resp.extend(await run_hydrus(
                        hydrus.add_files_from_urls,
                        urls,
                        tags=tags
                    ))
                resp.extend(await import_file_with_duplicates(
                    hydrus,
                    content_file,
                    mime=mime,
                    tags=tags
                ))
                logger.debug(f"{resp}")
                resp_str = hydrus.convert_import_resp_to_str(resp)

                reply = get_success_reply_str(
                    "аудио",
                    resp_str,
                    content_size=os.path.getsize(content_file)
                )
            finally:
                remove_temp_file(content_file)
        elif msg_content := msg.document:
            if not (msg.document and "document" in CONF.CONTENT_TYPES):
                await answer_disabled_content(msg, type(msg_content).__name__)
                return

            mime = msg_content.mime_type
            file_format = mime_subtype(mime)
            content_file = make_temp_file_path(
                msg_content.file_unique_id,
                file_format
            )
            try:
                # Из текста достаём теги и ссылки
                tags = get_tags_from_msg(msg)
                urls = get_urls_from_msg(msg)

                await bot.download(
                    msg_content.file_id,
                    content_file
                )

                resp = []
                if urls:
                    resp.extend(await run_hydrus(
                        hydrus.add_files_from_urls,
                        urls,
                        tags=tags
                    ))
                resp.extend(await import_file_with_duplicates(
                    hydrus,
                    content_file,
                    mime=mime,
                    tags=tags
                ))
                logger.debug(f"{resp}")
                resp_str = hydrus.convert_import_resp_to_str(resp)

                reply = get_success_reply_str(
                    "документ",
                    resp_str,
                    content_size=os.path.getsize(content_file)
                )
            finally:
                remove_temp_file(content_file)
        elif msg.text:
            if not (msg.text and "text" in CONF.CONTENT_TYPES):
                await answer_disabled_content(msg, "text")
                return

            tags = get_tags_from_msg(msg)
            urls = get_urls_from_msg(msg)
            # Скачиваем ссылки, проставляем теги ко всем ним.
            # Для каждой новой ссылки проверяем перцептивные дубликаты.
            resp: list[dict] = []
            send_hashes: list[str] = []
            for url, status in await run_hydrus(
                hydrus.add_files_from_urls_detailed,
                urls,
                tags=tags
            ):
                resp.append(status)
                file_hash = status.get("hash") or ""
                status_code = status.get("status")
                if (
                    status_code == int(AddedFileStatus.SUCCESSFULLY_IMPORTED)
                    and file_hash
                ):
                    duplicate = await run_hydrus(
                        hydrus.find_url_duplicates,
                        file_hash,
                        CONF.DUPLICATE_MAX_DISTANCE
                    )
                    if duplicate:
                        resolved = None
                        candidate_hashes = list(duplicate.get("hashes", []))
                        for candidate_hash in candidate_hashes:
                            result = await run_hydrus(
                                hydrus.resolve_url_duplicate,
                                url,
                                file_hash,
                                candidate_hash,
                                distance=int(duplicate.get("distance", 0)),
                                priority_domains=priority_domains,
                                tags=tags
                            )
                            if result.get("action") != "pending":
                                resolved = result
                                break
                        if resolved:
                            if resolved.get("action") == "kept_existing":
                                file_hash = str(resolved.get("existing_hash") or "")
                                resp[-1] = {
                                    "status": int(
                                        AddedFileStatus.ALREADY_IN_DATABASE
                                    ),
                                    "hash": file_hash,
                                    "note": (
                                        "оставлен существующий файл, "
                                        "новая копия удалена"
                                    ),
                                }
                            elif resolved.get("action") == "kept_new":
                                resp[-1] = {
                                    "status": int(
                                        AddedFileStatus.SUCCESSFULLY_IMPORTED
                                    ),
                                    "hash": file_hash,
                                    "note": (
                                        "оставлен новый файл, "
                                        "существующая копия удалена"
                                    ),
                                }
                        elif candidate_hashes:
                            candidate_hash = candidate_hashes[0]
                            distance = int(duplicate.get("distance", 0))
                            existing_info = await run_hydrus(
                                hydrus.get_file_info,
                                candidate_hash
                            )
                            new_info = await run_hydrus(
                                hydrus.get_file_info,
                                file_hash
                            )
                            token = _store_pending_duplicate({
                                "url": url,
                                "new_hash": file_hash,
                                "existing_hash": candidate_hash,
                                "distance": distance,
                                "tags": list(tags),
                                "existing_info": existing_info,
                                "new_info": new_info,
                            })
                            pending_prompts.append({
                                "token": token,
                                "url": url,
                                "new_hash": file_hash,
                                "existing_hash": candidate_hash,
                                "distance": distance,
                                "tags": list(tags),
                                "existing_info": existing_info,
                                "new_info": new_info,
                            })
                            pending_new_hashes.add(file_hash)
                            resp[-1] = {
                                **status,
                                "note": (
                                    f"найден похожий файл, "
                                    f"похож в пределах {distance}"
                                ),
                            }
                if (
                    file_hash
                    and file_hash not in pending_new_hashes
                    and status_code in (
                        int(AddedFileStatus.SUCCESSFULLY_IMPORTED),
                        int(AddedFileStatus.ALREADY_IN_DATABASE),
                    )
                ):
                    send_hashes.append(file_hash)

            logger.debug(f"{resp}")
            resp_str = hydrus.convert_import_resp_to_str(resp)

            # Отправка собственно скачанного контента.
            # Хэши уже удалённых дубликатов сюда не попадают.
            content_size = []
            for file_hash in dict.fromkeys(send_hashes):
                content_file = await run_hydrus(
                    hydrus.get_file,
                    file_hash
                )
                if content_file:
                    if content_size_item := _content_length(content_file.headers):
                        content_size.append(content_size_item)
                    await send_content_from_response(
                        content_file,
                        msg,
                        file_hash
                    )

            reply = get_success_reply_str(
                "текст",
                resp_str,
                content_size=content_size
            )
        else:
            logger.warning(f"{msg}")
            reply = "Неизвестный тип.\n" \
                "Смотри логи.\n" \
                f"{msg.message_id}"
        if sauce_error:
            reply = f"{reply}\n{sauce_error}"
        logger.info(f"Отправленный ответ: {reply}")
        await answer_with_retry(msg, reply)
        for pending in pending_prompts:
            try:
                await send_duplicate_prompt(bot, msg, hydrus, pending)
            except Exception as ex:
                logger.error(f"Не удалось показать выбор дубликата: {ex}")

    @dp.callback_query(F.data.startswith("dup:"))
    async def duplicate_callback_handler(callback: CallbackQuery):
        logger.info("Запуск duplicate_callback_handler")
        parts = (callback.data or "").split(":", 2)
        if len(parts) != 3:
            await callback.answer()
            return
        _, action, token = parts
        pending = _take_pending_duplicate(token)
        if not pending:
            await callback.answer("Решение устарело", show_alert=True)
            return

        try:
            result_text = await apply_duplicate_action(
                hydrus,
                pending,
                action
            )
        except hydrus_api.HydrusAPIException as ex:
            logger.error(f"Ошибка применения решения дубликата: {ex}")
            result_text = "Ошибка Hydrus API. Смотри логи."

        await callback.answer("Готово")
        if callback.message and hasattr(callback.message, "edit_text"):
            try:
                await callback.message.edit_text(
                    result_text,
                    reply_markup=None
                )
            except exceptions.TelegramBadRequest as ex:
                logger.warning(f"Не удалось изменить сообщение: {ex}")

    await dp.start_polling(bot)

    await bot.session.close()
    logger.info(f"Программа приостановлена на {CONF.TIME_TO_SLEEP} сек.")
    await asyncio.sleep(CONF.TIME_TO_SLEEP)
