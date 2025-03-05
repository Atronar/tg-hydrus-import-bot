import os
from pathlib import Path
import re
import shutil

from loguru import logger
from human_bytes import HumanBytes
from config import CONF


def prepare_temp_folder():
    """Создаёт, либо очищает временную папку"""
    try:
        temp_path = CONF.TEMP_PATH
        if os.path.exists(temp_path):
            shutil.rmtree(temp_path)
            os.makedirs(temp_path, exist_ok=True)
            logger.info("Временные файлы удалены")
        else:
            os.makedirs(temp_path)
            logger.info(f"Создана папка временных файлов по пути {temp_path}")
    except PermissionError as e:
        logger.error(f"Ошибка доступа: {e}")
        raise
    except Exception as e:
        logger.critical(f"Неизвестная ошибка: {e}")
        raise


def get_temp_folder() -> Path:
    """Возвращает путь ко временной папке"""
    return Path(CONF.TEMP_PATH).resolve()


def prepare_text_for_html(text: str) -> str:
    """Экранирование текста для html-разметки в Telegram"""
    return (
        text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def add_urls_to_text(text: str, urls: list[str], videos: list[str]) -> str:
    all_content = [url for url in (videos + urls) if url not in text]

    if not all_content:
        return text

    # Первая ссылка вставляется через <a>, остальные в конец
    first_url = all_content[0]
    text = f'<a href="{first_url}"> </a>{text}\n\n{first_url}' if text else first_url
    # Добавление остальных URL
    text = '\n'.join([text, *(all_content[1:])])
    return text


def split_text(text: str, fragment_size: int) -> list[str]:
    """Разбивает большой текст на фрагменты указанного размера."""
    return [
        text[fragment : fragment + fragment_size]
        for fragment in range(0, len(text), fragment_size)
    ]

_SCHEME_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", re.IGNORECASE)

def url_with_schema(url: str) -> str:
    """Возвращает переданный url вместе со схемой"""
    # В наиболее частых случаях не нужно напрягать регулярку
    # https://url.example -> https://url.example
    if url.lower().startswith(("https://", "http://",)):
        return url
    # //url.example -> https://url.example
    if url.startswith("//"):
        return f"https:{url}"
    # ftp://url.example -> ftp://url.example
    if _SCHEME_PATTERN.match(url):
        return url
    # url.example -> https://url.example
    return f"https://{url}"


_ILLEGAL_CHARS = set('/\\?%*:|"<>')
_ILLEGAL_UNPRINTABLE = {chr(c) for c in [*range(31), 127]}
_RESERVED_WORDS = {
    'CON', 'CONIN$', 'CONOUT$', 'PRN', 'AUX', 'CLOCK$', 'NUL',
    'COM0', 'COM1', 'COM2', 'COM3', 'COM4', 'COM5', 'COM6', 'COM7', 'COM8', 'COM9',
    'LPT0', 'LPT1', 'LPT2', 'LPT3', 'LPT4', 'LPT5', 'LPT6', 'LPT7', 'LPT8', 'LPT9',
    'LST', 'KEYBD$', 'SCREEN$', '$IDLE$', 'CONFIG$'
}

def make_safe_filename(filename: str) -> str:
    """
    # Make title file system safe
# https://stackoverflow.com/questions/7406102/create-sane-safe-filename-from-any-unsafe-string
    """
    if not filename: return "file"
    if os.path.splitext(filename)[0].upper() in _RESERVED_WORDS: return f"__{filename}"
    if filename[0]==filename[-1]=='.' and set(filename)=={'.'}: return '\uff0e' * len(filename)
    return "".join(
        chr(ord(c)+65248) if c in _ILLEGAL_CHARS else c
        for c in filename
        if c not in _ILLEGAL_UNPRINTABLE
    ).rstrip('. ') or "file"

def bytes_strformat(num: int|float) -> str:
    """Преобразует число байт в человекопонятный вид"""
    return HumanBytes.fast_format(num)

def camelCase_to_snake_case(
        string: str,
        *,
        __re_pattern = re.compile('((?<=[a-zа-яё0-9])[A-ZА-ЯЁ]|(?!^)(?<!_)[A-ZА-ЯЁ](?=[a-zа-яё]))')
    ) -> str:
    """Преобразует строку camelCase в snake_case"""
    return __re_pattern.sub(r'_\1', string).lower()
