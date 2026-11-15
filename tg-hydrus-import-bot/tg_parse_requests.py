"""
Модуль связанный с обработкой и запросами к телеграму
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import os
from pathlib import Path
import re
import tempfile
from functools import partial
from requests import Response
from typing import Callable, Iterable, Mapping

import aiogram.methods
from aiogram import exceptions
from aiogram.types import (
    BufferedInputFile,
    FSInputFile,
    InputMediaPhoto,
    Message,
    MessageEntity,
)
from loguru import logger
from PIL import Image

from tools import camelCase_to_snake_case, bytes_strformat, get_temp_folder, url_with_schema
from ffmpeg import get_io_mp4

MAX_FILE_SIZE = 50000000 # 50 Mb
MAX_PHOTO_SIZE = 10000000 # 10 Mb
# Ограничения Telegram на фото: сумма сторон и соотношение сторон.
MAX_PHOTO_DIMENSION_SUM = 10000
MAX_PHOTO_ASPECT_RATIO = 20
JPEG_QUALITIES = (90, 85, 80, 75)
SCALE_STEP = 0.85

# Хэштег не должен начинаться внутри слова или URL-пути:
# https://example.com/page#section — это якорь, а не тег.
HASHTAG_PATTERN = re.compile(r"(?<![\w/])#([^\s#@]+)")

def mime_subtype(mime_type: str|None, default: str = "") -> str:
    """Возвращает подтип MIME без параметров, например video/mp4 -> mp4."""
    if not mime_type:
        return default
    return mime_type.split(";", 1)[0].split("/", 1)[-1].strip()

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

def _photo_aspect_ratio_is_valid(width: int, height: int) -> bool:
    """Проверяет соотношение сторон, допустимое Telegram для фото."""
    if width <= 0 or height <= 0:
        return False
    return max(width / height, height / width) <= MAX_PHOTO_ASPECT_RATIO


def _photo_dimensions_are_valid(width: int, height: int) -> bool:
    """Проверяет размеры фото по ограничениям Telegram Bot API."""
    if width <= 0 or height <= 0:
        return False
    if width + height > MAX_PHOTO_DIMENSION_SUM:
        return False
    return _photo_aspect_ratio_is_valid(width, height)


def _photo_dimension_scale(width: int, height: int) -> float:
    """Возвращает масштаб, достаточный для лимита суммы сторон."""
    dimension_sum = width + height
    if dimension_sum <= MAX_PHOTO_DIMENSION_SUM:
        return 1.0
    return MAX_PHOTO_DIMENSION_SUM / dimension_sum


def _image_dimensions(path: Path) -> tuple[int, int] | None:
    """Читает размеры изображения из файла, не загружая его целиком."""
    try:
        with Image.open(path) as image:
            return image.size
    except Exception as ex:
        logger.debug(f"Не удалось определить размеры изображения {path}: {ex}")
        return None


def _resize_image(content: bytes, max_size: int) -> bytes | None:
    """Сжимает изображение до указанного размера с сохранением пропорций.

    Сначала пробуем сохранить исходное разрешение, постепенно снижая
    качество JPEG. Только если даже минимальное качество не помещается
    в лимит, уменьшаем размеры изображения. Дополнительно размеры
    приводятся к ограничениям Telegram: сумма сторон и соотношение сторон.
    """
    if len(content) <= max_size:
        try:
            with Image.open(BytesIO(content)) as img:
                width, height = img.size
            if _photo_dimensions_are_valid(width, height):
                return content
        except Exception:
            # Не изображение, но размер уже в лимите — оставляем как есть.
            return content

    try:
        with Image.open(BytesIO(content)) as img:
            original_width, original_height = img.size
            if not _photo_aspect_ratio_is_valid(original_width, original_height):
                logger.debug(
                    "Соотношение сторон не подходит для фото Telegram: "
                    f"{original_width}x{original_height}"
                )
                return None

            # JPEG не поддерживает альфа-канал
            image = img.convert("RGB")

            # Изображение нельзя увеличивать: только уменьшаем
            scale = min(
                1.0,
                _photo_dimension_scale(original_width, original_height)
            )

            while scale > 0.001:
                new_size = (
                    max(1, int(original_width * scale)),
                    max(1, int(original_height * scale))
                )
                if not _photo_dimensions_are_valid(*new_size):
                    scale *= SCALE_STEP
                    continue
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

_CONTENT_SUFFIXES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "audio/mp3": ".mp3",
    "audio/mpeg": ".mp3",
    "audio/m4a": ".m4a",
}

# Типы, которые Telegram принимает как аудио (answer_audio)
_AUDIO_CONTENT_TYPES = {"audio/mp3", "audio/mpeg", "audio/m4a"}

def _suffix_for_content_type(content_type: str) -> str:
    """Возвращает безопасное расширение для временного файла."""
    if content_type in _CONTENT_SUFFIXES:
        return _CONTENT_SUFFIXES[content_type]
    subtype = content_type.split("/", 1)[-1].split(";", 1)[0]
    subtype = re.sub(r"[^A-Za-z0-9]+", "", subtype)[:10]
    return f".{subtype}" if subtype else ""

def _content_length(headers: Mapping[str, str]) -> int:
    """Возвращает Content-Length или 0, если заголовок отсутствует или некорректен.

    Ноль означает «размер заранее неизвестен»: лимит всё равно проверяется
    при потоковой записи во временный файл.
    """
    value = headers.get("Content-Length")
    if not value:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        logger.warning(f"Некорректный Content-Length: {value!r}")
        return 0

def _close_response(response: Response) -> None:
    close = getattr(response, "close", None)
    if close:
        close()

def _file_is_mp4(path: Path) -> bool:
    with path.open("rb") as file:
        return _is_mp4(file.read(12))

async def _stream_content_to_temp(
    response: Response,
    max_size: int,
    suffix: str = ""
) -> Path | None:
    """Потоково пишет ответ Hydrus во временный файл, не держа его в памяти."""
    def write_temp() -> Path | None:
        fd, temp_name = tempfile.mkstemp(
            prefix="hydrus_",
            suffix=suffix,
            dir=get_temp_folder()
        )
        too_large = False
        try:
            with os.fdopen(fd, "wb") as output:
                for chunk in response.iter_content(STREAM_CHUNK_SIZE):
                    if not chunk:
                        continue
                    if output.tell() + len(chunk) > max_size:
                        too_large = True
                        break
                    output.write(chunk)
            if too_large:
                os.unlink(temp_name)
                return None
            return Path(temp_name)
        except Exception:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise
        finally:
            _close_response(response)

    return await asyncio.to_thread(write_temp)

async def _prepare_temp_content(
    content_file: Response
) -> tuple[Path, str, int] | None:
    """Сохраняет ответ Hydrus во временный файл и возвращает его параметры."""
    content_type = content_file.headers.get("Content-Type", "")
    media_type = content_type.split(";", 1)[0].strip().lower()
    logger.debug(f"Content-Type: {content_type}")
    content_length = _content_length(content_file.headers)
    logger.debug(f"Content-Length: {content_length}")

    if content_length > MAX_FILE_SIZE:
        logger.warning(f"Файл превысил лимит: {content_length} байт")
        _close_response(content_file)
        return None

    temp_path = await _stream_content_to_temp(
        content_file,
        MAX_FILE_SIZE,
        _suffix_for_content_type(media_type)
    )
    if temp_path is None:
        logger.warning(f"Файл превысил лимит: {MAX_FILE_SIZE} байт")
        return None

    return temp_path, media_type, temp_path.stat().st_size


def _unlink_temp(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


async def send_content_from_response(
    content_file: Response, msg: Message, filename: str
) -> Message | None:
    """Отправка содержимого результата запроса (из Гидруса) в Телеграм,
    основываясь на его Content-Type.

    Файл потоково сохраняется во временный файл. В память он читается
    только если нужна конвертация видео или сжатие изображения.
    """
    prepared = await _prepare_temp_content(content_file)
    if prepared is None:
        return None
    temp_path, media_type, file_size = prepared
    try:
        return await _send_temp_content(
            temp_path, media_type, file_size, msg, filename
        )
    finally:
        _unlink_temp(temp_path)


async def _send_temp_content(
    temp_path: Path,
    media_type: str,
    file_size: int,
    msg: Message,
    filename: str,
    *,
    cleanup: bool = True
) -> Message | None:
    """Отправляет уже сохранённый во временный файл контент."""
    try:
        answer_kwargs = {}
        content: bytes | None = None
        is_photo = False

        if media_type.startswith("video/"):
            if _file_is_mp4(temp_path):
                # MP4 допустимого размера не перекодируем
                input_file = FSInputFile(temp_path, filename)
                answer_function = msg.answer_video
                answer_kwargs["supports_streaming"] = True
            else:
                content = await asyncio.to_thread(temp_path.read_bytes)
                if mp4_content := await _async_convert(_convert_video_to_mp4, content):
                    input_file = BufferedInputFile(mp4_content, filename)
                    answer_function = msg.answer_video
                    answer_kwargs["supports_streaming"] = True
                else:
                    input_file = FSInputFile(temp_path, filename)
                    answer_function = msg.answer_document
        elif media_type == "image/gif":
            # Telegram сам перекодирует GIF в MP4 и заметно портит качество,
            # поэтому всегда конвертируем заранее. Если не получилось —
            # отправляем исходный файл документом, но не анимацией.
            content = await asyncio.to_thread(temp_path.read_bytes)
            if mp4_content := await _async_convert(_convert_video_to_mp4, content):
                input_file = BufferedInputFile(mp4_content, filename)
                answer_function = msg.answer_animation
            else:
                input_file = FSInputFile(temp_path, filename)
                answer_function = msg.answer_document
        elif media_type.startswith("image/"):
            dimensions = await asyncio.to_thread(_image_dimensions, temp_path)
            if (
                dimensions
                and _photo_dimensions_are_valid(*dimensions)
                and file_size <= MAX_PHOTO_SIZE
            ):
                input_file = FSInputFile(temp_path, filename)
                answer_function = msg.answer_photo
                is_photo = True
            elif (
                not dimensions
                or not _photo_aspect_ratio_is_valid(*dimensions)
            ):
                logger.warning(
                    "Изображение не подходит для фото Telegram, "
                    f"отправляем документом: {dimensions}"
                )
                input_file = FSInputFile(temp_path, filename)
                answer_function = msg.answer_document
            else:
                content = await asyncio.to_thread(temp_path.read_bytes)
                if photo_content := await _async_convert(
                    _resize_image, content, MAX_PHOTO_SIZE
                ):
                    input_file = BufferedInputFile(photo_content, filename)
                    answer_function = msg.answer_photo
                    is_photo = True
                else:
                    logger.warning(
                        "Не удалось уменьшить изображение до лимита фото, "
                        f"отправляем документом: {file_size} байт"
                    )
                    input_file = FSInputFile(temp_path, filename)
                    answer_function = msg.answer_document
        elif media_type in _AUDIO_CONTENT_TYPES:
            input_file = FSInputFile(temp_path, filename)
            answer_function = msg.answer_audio
        else:
            input_file = FSInputFile(temp_path, filename)
            answer_function = msg.answer_document

        logger.debug(f"Используется метод: {answer_function.__name__}")
        try:
            return await answer_function(
                input_file,
                reply_to_message_id=msg.message_id,
                **answer_kwargs
            )
        except exceptions.TelegramBadRequest as ex:
            # Даже после проверки Telegram может отклонить конкретные
            # размеры фото. Тогда отправляем тот же файл документом.
            if is_photo and "PHOTO_INVALID_DIMENSIONS" in str(ex):
                logger.warning(
                    "Telegram отклонил размеры фото, отправляем документом: "
                    f"{ex}"
                )
                return await msg.answer_document(
                    input_file,
                    reply_to_message_id=msg.message_id
                )
            raise
    finally:
        if cleanup:
            _unlink_temp(temp_path)


MAX_MEDIA_GROUP_SIZE = 10


async def send_contents_from_responses(
    contents: Iterable[tuple[Response, str]], msg: Message
) -> None:
    """Отправляет несколько файлов, группируя соседние фото в альбомы."""
    photo_batch: list[tuple[Path, str, int, str]] = []

    async def flush_photos() -> None:
        nonlocal photo_batch
        if not photo_batch:
            return
        batch = photo_batch
        photo_batch = []
        try:
            for start in range(0, len(batch), MAX_MEDIA_GROUP_SIZE):
                chunk = batch[start:start + MAX_MEDIA_GROUP_SIZE]
                if len(chunk) == 1:
                    path, media_type, file_size, filename = chunk[0]
                    await _send_temp_content(
                        path, media_type, file_size, msg, filename,
                        cleanup=False
                    )
                    continue
                media = [
                    InputMediaPhoto(media=FSInputFile(path, filename))
                    for path, _media_type, _file_size, filename in chunk
                ]
                try:
                    await msg.answer_media_group(
                        media,
                        reply_to_message_id=msg.message_id
                    )
                except exceptions.TelegramBadRequest as ex:
                    logger.warning(
                        "Не удалось отправить альбом, отправляем по одному: "
                        f"{ex}"
                    )
                    for path, media_type, file_size, filename in chunk:
                        await _send_temp_content(
                            path, media_type, file_size, msg, filename,
                            cleanup=False
                        )
        finally:
            for path, _media_type, _file_size, _filename in batch:
                _unlink_temp(path)

    for content_file, filename in contents:
        prepared = await _prepare_temp_content(content_file)
        if prepared is None:
            continue
        temp_path, media_type, file_size = prepared
        if media_type.startswith("image/") and media_type != "image/gif":
            dimensions = await asyncio.to_thread(
                _image_dimensions, temp_path
            )
            if (
                dimensions
                and _photo_dimensions_are_valid(*dimensions)
                and file_size <= MAX_PHOTO_SIZE
            ):
                photo_batch.append(
                    (temp_path, media_type, file_size, filename)
                )
                continue
        await flush_photos()
        try:
            await _send_temp_content(
                temp_path, media_type, file_size, msg, filename,
                cleanup=False
            )
        finally:
            _unlink_temp(temp_path)
    await flush_photos()


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
