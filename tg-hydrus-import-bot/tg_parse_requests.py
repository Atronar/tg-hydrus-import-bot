"""
Модуль связанный с обработкой и запросами к телеграму
"""
import asyncio
from concurrent.futures import ProcessPoolExecutor
from io import BytesIO
import re
from functools import partial
from requests import Response
from typing import Callable, Iterable

import aiogram.methods
from aiogram.types import BufferedInputFile, Message, MessageEntity
from loguru import logger
from PIL import Image

from tools import camelCase_to_snake_case, bytes_strformat, url_with_schema
from ffmpeg import get_io_mp4

MAX_FILE_SIZE = 50000000 # 50 Mb
MAX_PHOTO_SIZE = 10000000 # 10 Mb
JPEG_QUALITY = 85

HASHTAG_PATTERN = re.compile(r"#([^\s#@]+)") # regex для хэштегов

def get_tags_from_msg(msg: Message) -> list[str]:
    """Достаёт из объекта сообщения телеграм список хештегов,
    заменяя подчёркивания на пробелы и отрезая символ #
    """
    if text := (msg.text or msg.caption):
        return get_tags_from_str(text)
    return []

def get_tags_from_str(msg: str) -> list[str]:
    """Достаёт из текста список хештегов,
    заменяя подчёркивания на пробелы и отрезая символ #
    """
    tags = [
        tag.replace('_', ' ').strip()
        for tag in HASHTAG_PATTERN.findall(msg)
    ]
    if tags:
        logger.debug(f"Теги: {tags}")
    return tags

def get_urls_from_msg(msg: Message) -> list[str]:
    """Достаёт из объекта сообщения телеграм список ссылок"""
    if msg.caption_entities:
        if msg.caption is None:
            logger.error(f"Ошибка структуры сообщения: {msg.text}")
            raise ValueError(msg)
        return get_urls_from_entities(msg.caption, msg.caption_entities)
    if msg.entities:
        if msg.text is None:
            logger.error(f"Ошибка структуры сообщения: {msg.text}")
            raise ValueError(msg)
        return get_urls_from_entities(msg.text, msg.entities)
    return []

def get_urls_from_entities(msg: str, entities: list[MessageEntity]) -> list[str]:
    """Преобразовывает список вхождений в список ссылок из текста"""
    urls: list[str] = []
    for entity in entities:
        if entity.type == "text_link" and (url := entity.url):
            urls.append(
                url_with_schema(
                    url
                )
            )
        elif entity.type == "url":
            url = msg[entity.offset:entity.offset+entity.length]
            urls.append(
                url_with_schema(
                    url
                )
            )
    if urls:
        urls = list(dict.fromkeys(urls)) # Удаление дубликатов
        logger.debug(f"Ссылки: {urls}")
    return urls

def answer_disabled_content(msg: Message, content_type_config_name: str) -> aiogram.methods.SendMessage:
    """Отправляет заранее заготовленный ответ об отключённости типа контента в конфигах"""
    reply = f"Тип <code>{camelCase_to_snake_case(content_type_config_name)}</code>.\n" \
        "Отключено в конфигах."
    logger.info(f"Отправленный ответ: {reply}")
    return msg.answer(reply, reply_to_message_id=msg.message_id, parse_mode="HTML")

_FFMPEG_EXECUTOR = ProcessPoolExecutor(max_workers=2)  # Для CPU-bound задач

async def _async_convert(func: Callable, *args) -> bytes | None:
    loop = asyncio.get_running_loop()
    try:
        executor = _FFMPEG_EXECUTOR if func.__name__ == '_convert_video_to_mp4' else None
        return await loop.run_in_executor(executor, partial(func, *args))
    except Exception as e:
        logger.error(f"Ошибка конвертации {func.__name__}: {e}")
        return None

def _convert_video_to_mp4(content: bytes, mime_type: str) -> bytes|None:
    """Конвертирует видео с приоритетом скорости."""
    input_format = mime_type.split("/", 1)[-1].lower()
    # Если уже MP4, проверяем только размер
    if input_format == "mp4" and len(content) <= MAX_FILE_SIZE:
        return content

    for output_codec in ("x264", "x265-gpu", "x265"):
        converted = get_io_mp4(
            content,
            input_format=input_format,
            output_codec=output_codec
        )
        if len(converted) <= MAX_FILE_SIZE:
            return converted
    return None  # Возврат None при ошибке

def _resize_image(content: bytes, max_size: int) -> bytes | None:
    """Сжимает изображение до указанного размера с сохранением пропорций."""
    if len(content) <= max_size:
        return content
    try:
        with Image.open(BytesIO(content)) as img:
            original_width, original_height = img.size

            # Уменьшаем размер изображения до 2/3 площади на каждой итерации
            pixel_size = original_width * original_height
            scale_step = (2/3)**(0.5)
            if max_dimension := max(original_width, original_height):
                scale = 10000 / max_dimension
            else:
                scale = 1
            while scale > 0.001 and pixel_size > 1:
                new_size = (int(original_width * scale), (int(original_height * scale)))
                output = BytesIO()

                img.resize(new_size, Image.Resampling.LANCZOS).save(
                    output,
                    format='JPEG',
                    optimize=True,
                    quality=JPEG_QUALITY
                )
                if output.tell() <= max_size:
                    return output.getvalue()
                scale *= scale_step
                pixel_size = new_size[0] * new_size[1]

        return None
    except Exception as e:
        logger.error(f"Ошибка сжатия изображения: {e}")
        return None

STREAM_CHUNK_SIZE = 8388608  # 8 Mb

async def _stream_content(response: Response, max_size: int) -> bytes | None:
    """Потоковое чтение контента с проверкой размера"""
    content = BytesIO()
    for chunk in response.iter_content(STREAM_CHUNK_SIZE):
        content.write(chunk)
        if content.tell() > max_size:
            return None
    return content.getvalue()

async def send_content_from_response(
    content_file: Response, msg: Message, filename: str
) -> Message | None:
    """Отправка содержимого результата запроса (из Гидруса) в Телеграм,
    основываясь на его Content-Type
    """
    content_type = content_file.headers.get("Content-Type", "")
    logger.debug(f"Content-Type: {content_type}")
    content_length = int(content_file.headers.get("Content-Length", "0"))
    logger.debug(f"Content-Length: {content_length}")

    if content_length > MAX_FILE_SIZE:
        logger.warning(f"Файл превысил лимит: {content_length} байт")
        return None

    # "Content-Length" отсутствует, смотрим реальный размер
    if content_length == 0:
        content = await _stream_content(content_file, MAX_FILE_SIZE)
        if content:
            content_length = len(content)
        else:
            logger.warning(f"Файл превысил лимит: {MAX_FILE_SIZE} байт")
            return None
    else:
        content = content_file.content

    answer_kwargs = {}

    if content_type.startswith("video/",):
        # mp4 правильного размера не конвертируются, а отдаются обратно
        if (mp4_content := await _async_convert(_convert_video_to_mp4, content, content_type)):
            answer_function = msg.answer_video
            answer_kwargs["supports_streaming"] = True
            content = mp4_content
        else:
            answer_function = msg.answer_document
    elif content_type in ("image/gif",):
        if (mp4_content := await _async_convert(_convert_video_to_mp4, content, content_type)):
            content = mp4_content
        answer_function = msg.answer_animation
    elif content_type.startswith("image/"):
        photo_content = None
        if (
            content_length <= MAX_PHOTO_SIZE
            or (photo_content := await _async_convert(_resize_image, content, MAX_PHOTO_SIZE))
        ):
            answer_function = msg.answer_photo
            if photo_content:
                content = photo_content
        else:
            logger.warning(f"Файл превысил лимит для фото: {content_length} байт")
            answer_function = msg.answer_document
    elif content_type in ("audio/mp3", "audio/m4a"):
        answer_function = msg.answer_audio
    else:
        answer_function = msg.answer_document
    logger.debug(f"Используется метод: {answer_function.__name__}")
    input_file = BufferedInputFile(content, filename)
    return await answer_function(
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
    reply_parts = [f"Тип: {type_content_name}.", resp_str]
    if isinstance(content_size, int):
        reply_parts.append(bytes_strformat(content_size))
    elif isinstance(content_size, Iterable):
        reply_parts.extend(
            bytes_strformat(content_size_item)
            for content_size_item in content_size
        )
    return "\n".join(reply_parts)
