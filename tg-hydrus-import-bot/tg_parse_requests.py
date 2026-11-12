"""
Модуль связанный с обработкой и запросами к телеграму
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
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
JPEG_QUALITIES = (90, 85, 80, 75)
SCALE_STEP = 0.85

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
            # offset/length в Telegram считаются в UTF-16,
            # поэтому обычный срез по code points ломает текст с эмодзи
            url = entity.extract_from(msg)
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

# ffmpeg запускается отдельным процессом, поэтому потока достаточно:
# это не блокирует event loop и не требует spawn-копии интерпретатора на Windows.
_FFMPEG_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ffmpeg")

async def _async_convert(func: Callable, *args) -> bytes | None:
    loop = asyncio.get_running_loop()
    try:
        executor = _FFMPEG_EXECUTOR if func.__name__ == '_convert_video_to_mp4' else None
        return await loop.run_in_executor(executor, partial(func, *args))
    except Exception as e:
        logger.error(f"Ошибка конвертации {func.__name__}: {e}")
        return None

def _is_mp4(content: bytes) -> bool:
    """Проверяет контейнер MP4 по сигнатуре ftyp, не доверяя MIME-типу."""
    return len(content) >= 12 and content[4:8] == b"ftyp"

def _convert_video_to_mp4(content: bytes) -> bytes|None:
    """Конвертирует видео с приоритетом скорости.

    Формат входного потока определяет сам ffmpeg по байтам: MIME-тип
    от Telegram не всегда совпадает с demuxer'ом ffmpeg.
    """
    # Уже MP4 допустимого размера — перекодирование только ухудшит качество
    if _is_mp4(content) and len(content) <= MAX_FILE_SIZE:
        return content

    # Сначала самый быстрый вариант, затем более медленные fallback'и.
    # GPU-энкодер может отсутствовать — тогда просто переходим к следующему.
    for output_codec in ("x265-gpu", "x264-fast", "x264", "x265"):
        try:
            converted = get_io_mp4(content, output_codec=output_codec)
        except Exception as ex:
            logger.warning(f"FFmpeg {output_codec} недоступен: {ex}")
            continue
        if converted and len(converted) <= MAX_FILE_SIZE:
            return converted
    return None  # Возврат None при ошибке

def _resize_image(content: bytes, max_size: int) -> bytes | None:
    """Сжимает изображение до указанного размера с сохранением пропорций.

    Сначала пробуем сохранить исходное разрешение, постепенно снижая
    качество JPEG. Только если даже минимальное качество не помещается
    в лимит, уменьшаем размеры изображения. Так видимая детализация
    сохраняется лучше, чем при агрессивном даунскейле.
    """
    if len(content) <= max_size:
        return content
    try:
        with Image.open(BytesIO(content)) as img:
            original_width, original_height = img.size
            # JPEG не поддерживает альфа-канал
            image = img.convert("RGB")

            if max_dimension := max(original_width, original_height):
                # Изображение нельзя увеличивать: только уменьшаем
                scale = min(1.0, 10000 / max_dimension)
            else:
                scale = 1
            while scale > 0.001:
                new_size = (
                    max(1, int(original_width * scale)),
                    max(1, int(original_height * scale))
                )
                resized = (
                    image
                    if new_size == image.size
                    else image.resize(new_size, Image.Resampling.LANCZOS)
                )
                # libjpeg падает на optimize для очень узких изображений
                optimize = min(resized.size) >= 4
                for quality in JPEG_QUALITIES:
                    output = BytesIO()
                    try:
                        resized.save(
                            output,
                            format='JPEG',
                            optimize=optimize,
                            quality=quality
                        )
                    except OSError as ex:
                        # libjpeg иногда не умеет optimize для очень узких
                        # изображений — пробуем следующее качество/масштаб.
                        logger.debug(f"JPEG quality={quality}: {ex}")
                        continue
                    if output.tell() <= max_size:
                        return output.getvalue()
                scale *= SCALE_STEP

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
        if (mp4_content := await _async_convert(_convert_video_to_mp4, content)):
            answer_function = msg.answer_video
            answer_kwargs["supports_streaming"] = True
            content = mp4_content
        else:
            answer_function = msg.answer_document
    elif content_type in ("image/gif",):
        if (mp4_content := await _async_convert(_convert_video_to_mp4, content)):
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
