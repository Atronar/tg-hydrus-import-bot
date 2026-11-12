"""
Основной файл программы
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import partial, wraps
import html
import io
import os
import re
import urllib.request
from uuid import uuid4

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from aiogram import F, exceptions
import hydrus_api
from loguru import logger

from config import CONF
from tools import get_temp_folder, multiline_to_hashtags, split_text
from hydrus_requests import HydrusRequests
from saucenao_api import SauceNAO, SauseNAOException
from tg_parse_requests import get_tags_from_msg, get_urls_from_msg, \
    answer_disabled_content, send_content_from_response, get_success_reply_str, \
    mime_subtype, _content_length, MAX_PHOTO_SIZE

MAX_MESSAGE_LENGTH = 4000

HELP_TEXT = """Бот импортирует контент из Telegram в Hydrus Network.

Отправьте фото, видео, анимацию, видеосообщение, аудио, голосовое сообщение, документ или текст. Теги-хештеги и ссылки берутся из подписи или текста сообщения.

Команды:
/to_tags — показать теги, извлечённые из хештегов в сообщении
/to_hashtags — преобразовать построчный список тегов в хештеги
/sauce_by_hash <хэш>... — найти источники через SauceNAO и импортировать их
/tags_by_hash <хэш>... — показать теги файлов по хэшам
/hashtags_by_hash <хэш>... — показать хештеги файлов по хэшам
/get_hash — в подписи к фото: показать перцептивный хэш изображения

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
    """Отправляет одно сообщение с одной повторной попыткой."""
    try:
        return await _answer_once(msg, text, **kwargs)
    except exceptions.TelegramRetryAfter as ex:
        logger.warning(f"Telegram просит подождать {ex.retry_after} сек. {ex}")
        await asyncio.sleep(ex.retry_after)
        return await _answer_once(msg, text, **kwargs)
    except exceptions.TelegramNetworkError as ex:
        logger.warning(f"Сетевая ошибка. Повтор через 60 секунд. {ex}")
        await asyncio.sleep(60)
        return await _answer_once(msg, text, **kwargs)


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

    try:
        reset_destination()
        await bot.download(file_id, destination)
    except exceptions.TelegramRetryAfter as ex:
        logger.warning(f"Telegram просит подождать {ex.retry_after} сек. {ex}")
        await asyncio.sleep(ex.retry_after)
        reset_destination()
        await bot.download(file_id, destination)
    except exceptions.TelegramNetworkError as ex:
        logger.warning(f"Сетевая ошибка. Повтор через 60 секунд. {ex}")
        await asyncio.sleep(60)
        reset_destination()
        await bot.download(file_id, destination)


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

    @dp.message(CommandStart())
    async def start_handler(msg: Message):
        logger.info("Запуск start_handler")
        await answer_with_retry(msg, HELP_TEXT)

    @dp.message(Command("help"))
    async def help_handler(msg: Message):
        logger.info("Запуск help_handler")
        await answer_with_retry(msg, HELP_TEXT)

    @dp.message(F.text.contains('/to_tags'))
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

    @dp.message(F.text.contains('/to_hashtags'))
    async def to_hashtags_handler(msg: Message):
        logger.info("Запуск to_hashtags_handler")
        if not msg.text:
            return
        reply = multiline_to_hashtags(
            msg.text.replace('/to_hashtags', '', 1),
            remove_namespaces=True
        )
        if not reply:
            await answer_with_retry(msg, "Теги не найдены")
            return
        logger.info(f"Отправленный ответ: {reply[:100]}")
        await answer_long_with_retry(msg, reply)

    @dp.message(F.text.contains('/sauce_by_hash'))
    async def sauce_by_hash_handler(msg: Message):
        logger.info("Запуск sauce_by_hash_handler")
        if not msg.text:
            return
        if not sauce_nao:
            await answer_with_retry(msg, "SauceNAO не настроен")
            return
        hashes = msg.text.replace('/sauce_by_hash', '', 1).split()
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

    @dp.message(F.text.contains('/tags_by_hash'))
    async def tags_by_hash_handler(msg: Message):
        logger.info("Запуск tags_by_hash_handler")
        if not msg.text:
            return
        hashes = msg.text.replace('/tags_by_hash', '', 1).split()
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

    @dp.message(F.text.contains('/hashtags_by_hash'))
    async def hashtags_by_hash_handler(msg: Message):
        logger.info("Запуск hashtags_by_hash_handler")
        if not msg.text:
            return
        hashes = msg.text.replace('/hashtags_by_hash', '', 1).split()
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

            resp = await run_hydrus(
                hydrus.import_content,
                content_file,
                tags=tags,
                urls=urls
            )
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

            file_format = mime_subtype(
                getattr(msg_content, "mime_type", None),
                "mp4"
            )
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

                resp = await run_hydrus(
                    hydrus.import_content,
                    content_file,
                    tags=tags,
                    urls=urls
                )
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

            mime = mime_subtype(msg_content.mime_type)
            file_format = "mp3" if mime in ("mpeg", "mp3") else (mime or "mp3")
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

                resp = await run_hydrus(
                    hydrus.import_content,
                    content_file,
                    tags=tags,
                    urls=urls
                )
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

            file_format = mime_subtype(msg_content.mime_type)
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

                resp = await run_hydrus(
                    hydrus.import_content,
                    content_file,
                    tags=tags,
                    urls=urls
                )
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
            # Скачиваем ссылки, проставляем теги ко всем ним
            resp = await run_hydrus(
                hydrus.import_content,
                urls=urls,
                tags=tags
            )

            logger.debug(f"{resp}")
            resp_str = hydrus.convert_import_resp_to_str(resp)

            # Отправка собственно скачанного контента
            content_size = []
            for resp_item in resp:
                content_file = await run_hydrus(
                    hydrus.get_file,
                    resp_item.get("hash", None)
                )
                if content_file:
                    if (
                        (content_size_item := content_file.headers.get("Content-Length"))
                        and content_size_item.isdecimal()
                    ):
                        content_size.append(int(content_size_item))
                    await send_content_from_response(
                        content_file,
                        msg,
                        resp_item.get("hash", "file")
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

    await dp.start_polling(bot)

    await bot.session.close()
    logger.info(f"Программа приостановлена на {CONF.TIME_TO_SLEEP} сек.")
    await asyncio.sleep(CONF.TIME_TO_SLEEP)
