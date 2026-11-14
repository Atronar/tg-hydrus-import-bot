"""Определение параметров локального медиафайла.

Параметры нужны, чтобы отличить точный перцептивный дубликат
от похожего файла, который может быть лучше существующего.
"""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Literal

import ffmpy
from loguru import logger
from PIL import Image

MediaKind = Literal["image", "video", "audio", "document"]

DURATION_TOLERANCE = 0.5


@dataclass(frozen=True)
class MediaInfo:
    kind: MediaKind
    size: int
    width: int | None = None
    height: int | None = None
    duration: float | None = None


def _source_path(source: str | os.PathLike) -> Path:
    return Path(source)


def _source_size(source: str | os.PathLike | object) -> int:
    if isinstance(source, (str, os.PathLike)):
        try:
            return _source_path(source).stat().st_size
        except OSError:
            return 0
    if hasattr(source, "getbuffer"):
        return source.getbuffer().nbytes  # type: ignore[attr-defined]
    if hasattr(source, "seek") and hasattr(source, "tell"):
        position = source.tell()  # type: ignore[attr-defined]
        source.seek(0, os.SEEK_END)  # type: ignore[attr-defined]
        size = source.tell()  # type: ignore[attr-defined]
        source.seek(position)  # type: ignore[attr-defined]
        return size
    return 0


def _kind_from_mime(mime: str | None) -> MediaKind | None:
    if not mime:
        return None
    media_type = mime.split(";", 1)[0].strip().lower()
    if media_type.startswith("image/"):
        return "image"
    if media_type.startswith("video/"):
        return "video"
    if media_type.startswith("audio/"):
        return "audio"
    return None


def is_image_mime(mime: str | None) -> bool:
    """Считает файл изображением по MIME-типу.

    Опираться на список форматов Hydrus нельзя: бот работает с более
    широким набором изображений. Для всего, что не ``image/*``,
    перцептивный поиск не применяется.
    """
    return _kind_from_mime(mime) == "image"


def _probe_image(source: str | os.PathLike | object) -> tuple[int, int] | None:
    try:
        with Image.open(source) as image:
            return image.size
    except Exception as ex:
        logger.debug(f"Не удалось определить размер изображения: {ex}")
        return None


def _probe_ffprobe(path: Path) -> tuple[MediaKind, int | None, int | None, float | None]:
    try:
        probe = ffmpy.FFprobe(
            global_options=[
                "-v quiet",
                "-print_format json",
                "-show_format",
                "-show_streams",
            ],
            inputs={str(path): None},
        )
        stdout, _ = probe.run(
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        data = json.loads(stdout)
    except Exception as ex:
        logger.debug(f"ffprobe не смог прочитать {path}: {ex}")
        return "document", None, None, None

    streams: list[dict[str, Any]] = data.get("streams", [])
    video_stream = next(
        (stream for stream in streams if stream.get("codec_type") == "video"),
        None,
    )
    audio_stream = next(
        (stream for stream in streams if stream.get("codec_type") == "audio"),
        None,
    )

    duration_value = data.get("format", {}).get("duration")
    if duration_value is None and video_stream:
        duration_value = video_stream.get("duration")
    if duration_value is None and audio_stream:
        duration_value = audio_stream.get("duration")
    try:
        duration = float(duration_value) if duration_value is not None else None
    except (TypeError, ValueError):
        duration = None

    if video_stream:
        return (
            "video",
            video_stream.get("width"),
            video_stream.get("height"),
            duration,
        )
    if audio_stream:
        return "audio", None, None, duration
    return "document", None, None, duration


def probe_media(
    source: str | os.PathLike | object,
    mime: str | None = None,
) -> MediaInfo:
    """Возвращает параметры файла, не изменяя позицию file-like объекта."""
    size = _source_size(source)
    kind = _kind_from_mime(mime)

    if kind == "image" or (kind is None and not isinstance(source, (str, os.PathLike))):
        dimensions = _probe_image(source)
        if dimensions:
            width, height = dimensions
            return MediaInfo("image", size, width, height)

    if isinstance(source, (str, os.PathLike)):
        probed_kind, width, height, duration = _probe_ffprobe(_source_path(source))
        if kind is None:
            kind = probed_kind
        return MediaInfo(kind, size, width, height, duration)

    return MediaInfo(kind or "document", size)


def _duration_equal(left: float | None, right: float | None) -> bool:
    if left is None or right is None:
        return False
    return abs(left - right) <= DURATION_TOLERANCE


def _not_larger(new: MediaInfo, existing_width: object, existing_height: object) -> bool:
    if new.width is None or new.height is None:
        return False
    if not isinstance(existing_width, int) or not isinstance(existing_height, int):
        return False
    return new.width <= existing_width and new.height <= existing_height


def is_exact_duplicate(
    new: MediaInfo,
    existing: dict[str, Any],
) -> bool:
    """Проверяет, является ли новый файл не лучшей версией существующего.

    Функция применяется только к кандидатам с перцептивным расстоянием 0.
    Для документов сравнивается размер, для аудио — длительность,
    для изображений и видео — размеры.
    """
    if new.kind == "image":
        return _not_larger(
            new,
            existing.get("width"),
            existing.get("height"),
        )

    if new.kind == "video":
        return (
            _duration_equal(new.duration, existing.get("duration"))
            and _not_larger(
                new,
                existing.get("width"),
                existing.get("height"),
            )
        )

    if new.kind == "audio":
        return _duration_equal(new.duration, existing.get("duration"))

    existing_size = existing.get("size")
    return isinstance(existing_size, int) and new.size == existing_size
