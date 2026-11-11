"""
Основной файл программы
"""

import asyncio
import html
import io
import os
import urllib.request

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.types import Message
from aiogram import F, exceptions
import hydrus_api
from loguru import logger

from config import CONF
from tools import get_temp_folder, multiline_to_hashtags, split_text
from hydrus_requests import HydrusRequests
from saucenao_api import SauceNAO
from tg_parse_requests import get_tags_from_msg, get_urls_from_msg, \
    answer_disabled_content, send_content_from_response, get_success_reply_str

MAX_MESSAGE_LENGTH = 4000


async def answer_with_retry(msg: Message, text: str, **kwargs) -> Message:
    """Отправляет сообщение, повторяя попытку при сетевой ошибке."""
    try:
        return await msg.answer(
            text,
            reply_to_message_id=msg.message_id,
            **kwargs
        )
    except exceptions.TelegramRetryAfter as ex:
        logger.warning(f"Telegram просит подождать {ex.retry_after} сек. {ex}")
        await asyncio.sleep(ex.retry_after)
        return await msg.answer(
            text,
            reply_to_message_id=msg.message_id,
            **kwargs
        )
    except exceptions.TelegramNetworkError as ex:
        logger.warning(f"Сетевая ошибка. Повтор через 60 секунд. {ex}")
        await asyncio.sleep(60)
        return await msg.answer(
            text,
            reply_to_message_id=msg.message_id,
            **kwargs
        )


async def answer_long_with_retry(msg: Message, text: str, **kwargs):
    """Отправляет длинный текст несколькими сообщениями."""
    for fragment in split_text(text, MAX_MESSAGE_LENGTH) or [""]:
        await answer_with_retry(msg, fragment, **kwargs)


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
    hydrus = HydrusRequests(
        CONF.HYDRUS_TOKEN,
        api_url=CONF.HYDRUS_API_URL,
        default_tags_namespace=CONF.TAGS_NAMESPACE,
        default_destination_page_name=CONF.DESTINATION_PAGE_NAME
    )
    if CONF.SAUCENAO_TOKEN:
        sauce_nao = SauceNAO(CONF.SAUCENAO_TOKEN)
        sauce_nao.set_similarity(80)
    else:
        sauce_nao = None

    @dp.message(F.from_user.id.in_(CONF.TG_ADMIN_ID), F.text.contains('/to_tags'))
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

    @dp.message(F.from_user.id.in_(CONF.TG_ADMIN_ID), F.text.contains('/to_hashtags'))
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

    @dp.message(F.from_user.id.in_(CONF.TG_ADMIN_ID), F.text.contains('/sauce_by_hash'))
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
                content_file = await asyncio.to_thread(hydrus.get_file, hash_str)
                if not content_file:
                    await answer_with_retry(msg, f"{hash_str}: файл не найден")
                    continue
                urls = await asyncio.to_thread(
                    sauce_nao.get_sources,
                    content_file.content
                )
                if not urls:
                    await answer_with_retry(msg, f"{hash_str}: источники не найдены")
                    continue
                resp = await asyncio.to_thread(hydrus.import_content, urls=urls)
            except hydrus_api.HydrusAPIException as ex:
                logger.error(f"Ошибка Hydrus для {hash_str}: {ex}")
                await answer_with_retry(msg, f"{hash_str}: ошибка Hydrus API")
                continue

            resp_str = hydrus.convert_import_resp_to_str(resp)
            content_length = content_file.headers.get("Content-Length")
            reply = get_success_reply_str(
                "хэш",
                resp_str,
                content_size=int(content_length) if content_length else 0
            )
            logger.info(f"Отправленный ответ: {reply[:100]}")
            await answer_long_with_retry(msg, reply)

    @dp.message(F.from_user.id.in_(CONF.TG_ADMIN_ID), F.text.contains('/tags_by_hash'))
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
                tags = await asyncio.to_thread(hydrus.get_tags, hash_str)
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

    @dp.message(F.from_user.id.in_(CONF.TG_ADMIN_ID), F.text.contains('/hashtags_by_hash'))
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
                tags = await asyncio.to_thread(hydrus.get_tags, hash_str)
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

    @dp.message(F.from_user.id.in_(CONF.TG_ADMIN_ID), F.photo, F.caption.contains('/get_hash'))
    async def get_hash_handler(msg: Message):
        logger.info("Запуск get_hash_handler")
        if not msg.photo:
            return
        photo = msg.photo[-1]
        content_file = io.BytesIO()
        await download_with_retry(bot, photo.file_id, content_file)

        try:
            resp = await asyncio.to_thread(hydrus.get_file_hash, content_file)
        except hydrus_api.HydrusAPIException as ex:
            logger.warning(f"Не удалось получить хэш из буфера: {ex}")
            temp_path = os.path.join(
                get_temp_folder(),
                f"{photo.file_unique_id}.jpg"
            )
            try:
                await download_with_retry(bot, photo.file_id, temp_path)
                resp = await asyncio.to_thread(hydrus.get_file_hash, temp_path)
            except hydrus_api.HydrusAPIException as retry_ex:
                logger.error(f"Не удалось получить хэш из файла: {retry_ex}")
                resp = None
            finally:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass

        reply = resp or "Файл не найден в системе или недостаточно прав"
        logger.info(f"Отправленный ответ: {reply}")
        await answer_with_retry(msg, reply)

    @dp.message(F.from_user.id.in_(CONF.TG_ADMIN_ID))
    async def message_handler(msg: Message):
        logger.info("Запуск message_handler")
        if msg_content := msg.photo:
            if not (msg.photo and "photo" in CONF.CONTENT_TYPES):
                await answer_disabled_content(msg, "photo")
                return

            msg_content = msg_content.pop()
            content_file = io.BytesIO()

            # Из текста достаём теги и ссылки
            tags = get_tags_from_msg(msg)
            urls = get_urls_from_msg(msg)

            await bot.download(
                msg_content.file_id,
                content_file
            )

            if not (tags or urls) and sauce_nao:
                urls = sauce_nao.get_sources(content_file)

            resp = hydrus.import_content(content_file, tags=tags, urls=urls)
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

            file_format = getattr(msg_content, "mime_type", "mp4").split('/')[-1]
            content_file = os.path.join(
                get_temp_folder(),
                f"{msg_content.file_unique_id}.{file_format}"
            )

            # Из текста достаём теги и ссылки
            tags = get_tags_from_msg(msg)
            urls = get_urls_from_msg(msg)

            await bot.download(
                msg_content.file_id,
                content_file
            )

            resp = hydrus.import_content(content_file, tags=tags, urls=urls)
            logger.debug(f"{resp}")
            resp_str = hydrus.convert_import_resp_to_str(resp)

            reply = get_success_reply_str(
                "видео",
                resp_str,
                content_size=os.path.getsize(content_file)
            )
        # elif msg_content := msg.animation:
        #    if not (msg.animation and "animation" in CONF.CONTENT_TYPES):
        #        await answer_disabled_content(msg, type(msg_content).__name__)
        #        return
        #     ...
        # elif msg_content := msg.video_note:
        #    if not (msg.video_note and "video_note" in CONF.CONTENT_TYPES):
        #        await answer_disabled_content(msg, type(msg_content).__name__)
        #        return
        #     ...
        elif msg_content := (msg.audio or msg.voice):
            if not ((msg.audio and "audio" in CONF.CONTENT_TYPES) \
                or (msg.voice and "voice" in CONF.CONTENT_TYPES)):
                await answer_disabled_content(msg, type(msg_content).__name__)
                return

            if mime_type := msg_content.mime_type:
                file_format = "mp3" if mime_type=="audio/mpeg" else mime_type.split('/')[-1]
            else:
                file_format = "mp3"
            content_file = os.path.join(
                get_temp_folder(),
                os.path.extsep.join([msg_content.file_unique_id, file_format])
            )

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

            resp = hydrus.import_content(content_file, tags=tags, urls=urls)
            logger.debug(f"{resp}")
            resp_str = hydrus.convert_import_resp_to_str(resp)

            reply = get_success_reply_str(
                "аудио",
                resp_str,
                content_size=os.path.getsize(content_file)
            )
        # elif msg_content := msg.voice:
        #    if not (msg.voice and "voice" in CONF.CONTENT_TYPES):
        #        await answer_disabled_content(msg, type(msg_content).__name__)
        #        return
        #     ...
        elif msg_content := msg.document:
            if not (msg.document and "document" in CONF.CONTENT_TYPES):
                await answer_disabled_content(msg, type(msg_content).__name__)
                return

            if mime_type := msg_content.mime_type:
                file_format = mime_type.split('/')[-1]
            else:
                file_format = ""
            content_file = os.path.join(
                get_temp_folder(),
                os.path.extsep.join([msg_content.file_unique_id, file_format])
            )

            # Из текста достаём теги и ссылки
            tags = get_tags_from_msg(msg)
            urls = get_urls_from_msg(msg)

            await bot.download(
                msg_content.file_id,
                content_file
            )

            resp = hydrus.import_content(content_file, tags=tags, urls=urls)
            logger.debug(f"{resp}")
            resp_str = hydrus.convert_import_resp_to_str(resp)

            reply = get_success_reply_str(
                "документ",
                resp_str,
                content_size=os.path.getsize(content_file)
            )
        elif msg.text:
            if not (msg.text and "text" in CONF.CONTENT_TYPES):
                await answer_disabled_content(msg, "text")
                return

            tags = get_tags_from_msg(msg)
            urls = get_urls_from_msg(msg)
            # Скачиваем ссылки, проставляем теги ко всем ним
            resp = hydrus.import_content(urls=urls, tags=tags)

            logger.debug(f"{resp}")
            resp_str = hydrus.convert_import_resp_to_str(resp)

            # Отправка собственно скачанного контента
            content_size = []
            for resp_item in resp:
                if content_file := hydrus.get_file(resp_item.get("hash", None)):
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
        logger.info(f"Отправленный ответ: {reply}")
        await msg.answer(reply, reply_to_message_id=msg.message_id)

    await dp.start_polling(bot)

    await bot.session.close()
    logger.info(f"Программа приостановлена на {CONF.TIME_TO_SLEEP} сек.")
    await asyncio.sleep(CONF.TIME_TO_SLEEP)
