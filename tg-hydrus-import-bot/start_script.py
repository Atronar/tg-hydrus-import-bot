"""
Основной файл программы
"""

import asyncio
import os
import io

from aiogram import Bot, Dispatcher
from aiogram.types import Message
from aiogram import F
from loguru import logger

from config import CONF
from tools import get_temp_folder
from hydrus_requests import HydrusRequests
from tg_parse_requests import get_tags_from_msg, get_urls_from_msg, \
    answer_disabled_content, send_content_from_response, get_success_reply_str

async def start_script():
    bot = Bot(token=CONF['TG_BOT_TOKEN'])
    dp = Dispatcher()
    hydrus = HydrusRequests(CONF['HYDRUS_TOKEN'])

    @dp.message(F.from_user.id.in_(CONF['TG_ADMIN_ID']))
    async def message_handler(msg: Message):
        if msg_content := msg.photo:
            if not (msg.photo and "photo" in CONF["CONTENT_TYPES"]):
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
            if not ((msg.video and "video" in CONF["CONTENT_TYPES"]) \
                or (msg.animation and "animation" in CONF["CONTENT_TYPES"]) \
                or (msg.video_note and "video_note" in CONF["CONTENT_TYPES"])):
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
        #    if not (msg.animation and "animation" in CONF["CONTENT_TYPES"]):
        #        await answer_disabled_content(msg, type(msg_content).__name__)
        #        return
        #     ...
        # elif msg_content := msg.video_note:
        #    if not (msg.video_note and "video_note" in CONF["CONTENT_TYPES"]):
        #        await answer_disabled_content(msg, type(msg_content).__name__)
        #        return
        #     ...
        elif msg_content := (msg.audio or msg.voice):
            if not ((msg.audio and "audio" in CONF["CONTENT_TYPES"]) \
                or (msg.voice and "voice" in CONF["CONTENT_TYPES"])):
                await answer_disabled_content(msg, type(msg_content).__name__)
                return

            file_format = "mp3" if msg_content.mime_type=="audio/mpeg" else msg_content.mime_type.split('/')[-1]
            content_file = os.path.join(
                get_temp_folder(),
                f"{msg_content.file_unique_id}.{file_format}"
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
        #    if not (msg.voice and "voice" in CONF["CONTENT_TYPES"]):
        #        await answer_disabled_content(msg, type(msg_content).__name__)
        #        return
        #     ...
        elif msg_content := msg.document:
            if not (msg.document and "document" in CONF["CONTENT_TYPES"]):
                await answer_disabled_content(msg, type(msg_content).__name__)
                return

            content_file = os.path.join(
                get_temp_folder(),
                f"{msg_content.file_unique_id}.{msg_content.mime_type.split('/')[-1]}"
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
            if not (msg.text and "text" in CONF["CONTENT_TYPES"]):
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
                    if (content_size_item := content_file.headers.get("Content-Length")) \
                    and content_size_item.isdecimal():
                        content_size.append(int(content_size_item))
                    await send_content_from_response(content_file, msg, resp_item.get("hash", "file"))

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
    logger.info(f"Программа приостановлена на {CONF['TIME_TO_SLEEP']} сек.")
    await asyncio.sleep(CONF['TIME_TO_SLEEP'])
