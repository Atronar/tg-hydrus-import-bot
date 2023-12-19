"""
Модуль связанный с обработкой и запросами к телеграму
"""
import re

from aiogram.types import Message, MessageEntity
from loguru import logger

from tools import camelCase_to_snake_case

def get_tags_from_msg(msg: Message) -> list[str]:
    if msg.caption is not None:
        return get_tags_from_str(msg.caption)
    if msg.text is not None:
        return get_tags_from_str(msg.text)
    return []

def get_tags_from_str(msg: str) -> list[str]:
    tags = list(map(lambda x: x.replace('_', ' '), re.findall(r"(?<=#)[^\s#@]+", msg)))
    if tags:
        logger.debug(f"Теги: {tags}")
    return tags

def get_urls_from_msg(msg: Message) -> list[str]:
    if msg.caption_entities is not None:
        return get_urls_from_entities(msg.caption, msg.caption_entities)
    if msg.entities is not None:
        return get_urls_from_entities(msg.text, msg.entities)
    return []

def get_urls_from_entities(msg: str, entities: list[MessageEntity]) -> list[str]:
    urls = []
    for entity in entities:
        if entity.type in ("text_link") and entity.url:
            urls.append(entity.url)
        elif entity.type in ("url"):
            urls.append(msg[entity.offset:entity.offset+entity.length])
    if urls:
        logger.debug(f"Ссылки: {urls}")
    return urls

def answer_disabled_content(msg: Message, content_type_config_name: str):
    reply = f"Тип <code>{camelCase_to_snake_case(content_type_config_name)}</code>.\n" \
        "Отключено в конфигах."
    logger.info(f"Отправленный ответ: {reply}")
    return msg.answer(reply, reply_to_message_id=msg.message_id, parse_mode="HTML")
