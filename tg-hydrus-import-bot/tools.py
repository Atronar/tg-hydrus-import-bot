import os
import re

from loguru import logger
from human_bytes import HumanBytes
from config import CONF


def prepare_temp_folder():
    temp_path = CONF.TEMP_PATH
    if os.path.exists(temp_path):
        for root, _, files in os.walk(temp_path):
            for file in files:
                os.remove(os.path.join(root, file))
        logger.info("Временные файлы удалены")
    else:
        os.makedirs(temp_path)
        logger.info(f"Создана папка временных файлов по пути {temp_path}")


def get_temp_folder() -> str:
    return os.path.abspath(CONF.TEMP_PATH)


def prepare_text_for_html(text: str) -> str:
    return (
        text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def add_urls_to_text(text: str, urls: list, videos: list) -> str:
    first_link = True
    urls = videos + urls

    if not urls:
        return text

    for url in urls:
        if url not in text:
            if first_link:
                text = f'<a href="{url}"> </a>{text}\n\n{url}' if text else url
                first_link = False
            else:
                text += f"\n{url}"
    return text


def split_text(text: str, fragment_size: int) -> list:
    fragments = []
    for fragment in range(0, len(text), fragment_size):
        fragments.append(text[fragment : fragment + fragment_size])
    return fragments

def url_with_schema(url: str) -> str:
    # В наиболее частых случаях не нужно напрягать регулярку
    # https://url.example -> https://url.example
    if url.startswith("https://") or url.startswith("http://"):
        return url
    # //url.example -> https://url.example
    if url.startswith("//"):
        return f"https:{url}"
    # ftp://url.example -> ftp://url.example
    if re.match(r"\w+://", url):
        return url
    # url.example -> https://url.example
    return f"https://{url}"


def make_safe_filename(filename: str) -> str:
    """
    # Make title file system safe
# https://stackoverflow.com/questions/7406102/create-sane-safe-filename-from-any-unsafe-string
    """
    illegal_chars = "/\\?%*:|\"<>"
    illegal_unprintable = {chr(c) for c in (*range(31), 127)}
    reserved_words = {
        'CON', 'CONIN$', 'CONOUT$', 'PRN', 'AUX', 'CLOCK$', 'NUL',
        'COM0', 'COM1', 'COM2', 'COM3', 'COM4', 'COM5', 'COM6', 'COM7', 'COM8', 'COM9',
        'LPT0', 'LPT1', 'LPT2', 'LPT3', 'LPT4', 'LPT5', 'LPT6', 'LPT7', 'LPT8', 'LPT9',
        'LST', 'KEYBD$', 'SCREEN$', '$IDLE$', 'CONFIG$'
    }
    if os.path.splitext(filename)[0].upper() in reserved_words: return f"__{filename}"
    if set(filename)=={'.'}: return filename.replace('.', '\uff0e')
    return "".join(
        chr(ord(c)+65248) if c in illegal_chars else c
        for c in filename
        if c not in illegal_unprintable
    ).rstrip().rstrip('.')

def bytes_strformat(num: int|float) -> str:
    return HumanBytes.format(num)

def camelCase_to_snake_case(
        string: str,
        *,
        __re_pattern = re.compile('((?<=[a-zа-яё0-9])[A-ZА-ЯЁ]|(?!^)(?<!_)[A-ZА-ЯЁ](?=[a-zа-яё]))')
    ) -> str:
    return __re_pattern.sub(r'_\1', string).lower()
