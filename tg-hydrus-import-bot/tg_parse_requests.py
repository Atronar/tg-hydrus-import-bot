"""
Модуль связанный с обработкой и запросами к телеграму
"""
import re
from typing import Iterable
from requests import Response

from aiogram.types import BufferedInputFile, Message, MessageEntity
from loguru import logger

from tools import camelCase_to_snake_case, bytes_strformat, url_with_schema

MAX_FILE_SIZE = 50000000 # 50 Mb
MAX_PHOTO_SIZE = 10000000 # 10 Mb

def get_tags_from_msg(msg: Message) -> list[str]:
    """Достаёт из объекта сообщения телеграм список хештегов,
    заменяя подчёркивания на пробелы и отрезая символ #
    """
    if msg.caption is not None:
        return get_tags_from_str(msg.caption)
    if msg.text is not None:
        return get_tags_from_str(msg.text)
    return []

def get_tags_from_str(msg: str) -> list[str]:
    """Достаёт из текста список хештегов,
    заменяя подчёркивания на пробелы и отрезая символ #
    """
    tags = list(map(lambda x: x.replace('_', ' '), re.findall(r"(?<=#)[^\s#@]+", msg)))
    if tags:
        logger.debug(f"Теги: {tags}")
    return tags

def get_urls_from_msg(msg: Message) -> list[str]:
    """Достаёт из объекта сообщения телеграм список ссылок"""
    if msg.caption_entities is not None:
        if msg.caption is None:
            raise ValueError(msg)
        return get_urls_from_entities(msg.caption, msg.caption_entities)
    if msg.entities is not None:
        if msg.text is None:
            raise ValueError(msg)
        return get_urls_from_entities(msg.text, msg.entities)
    return []

def get_urls_from_entities(msg: str, entities: list[MessageEntity]) -> list[str]:
    """Преобразовывает список вхождений в список ссылок из текста"""
    urls = []
    for entity in entities:
        if entity.type in ("text_link") and entity.url:
            urls.append(
                url_with_schema(
                    entity.url
                )
            )
        elif entity.type in ("url"):
            urls.append(
                url_with_schema(
                    msg[entity.offset:entity.offset+entity.length]
                )
            )
    if urls:
        logger.debug(f"Ссылки: {urls}")
    return urls

def answer_disabled_content(msg: Message, content_type_config_name: str):
    """Отправляет заранее заготовленный ответ об отключённости типа контента в конфигах"""
    reply = f"Тип <code>{camelCase_to_snake_case(content_type_config_name)}</code>.\n" \
        "Отключено в конфигах."
    logger.info(f"Отправленный ответ: {reply}")
    return msg.answer(reply, reply_to_message_id=msg.message_id, parse_mode="HTML")

def send_content_from_response(content_file: Response, msg: Message, filename: str):
    """Отправка содержимого результата запроса (из Гидруса) в Телеграм,
    основываясь на его Content-Type
    """
    content_type = content_file.headers.get("Content-Type", "")
    logger.debug(f"Content-Type: {content_type}")
    content_length = int(content_file.headers.get("Content-Length", "0"))
    logger.debug(f"Content-Length: {content_length}")
    if content_length > MAX_FILE_SIZE:
        return None
    answer_kwargs = {}
    if content_type in ("video/mp4",):
        answer_function = msg.answer_video
        answer_kwargs["supports_streaming"] = True
    elif content_type in ("image/gif",):
        answer_function = msg.answer_animation
    elif content_type.startswith("image/"):
        if content_length > MAX_PHOTO_SIZE:
            return None
        answer_function = msg.answer_photo
    elif content_type in ("audio/mp3", "audio/m4a"):
        answer_function = msg.answer_audio
    else:
        answer_function = msg.answer_document
    input_file = BufferedInputFile(content_file.content, filename)
    return answer_function(
        input_file,
        reply_to_message_id=msg.message_id,
        **answer_kwargs
    )

def get_success_reply_str(
        type_content_name: str,
        resp_str: str,
        content_size: int|Iterable[int]|None = None
    ) -> str:
    """Генерация строки с успешным импортом
    """
    reply = f"Тип: {type_content_name}.\n" \
        f"{resp_str}"
    if isinstance(content_size, int):
        reply = f"{reply}\n{bytes_strformat(content_size)}"
    elif isinstance(content_size, Iterable):
        for content_size_item in content_size:
            reply = f"{reply}\n{bytes_strformat(content_size_item)}"
    return reply
