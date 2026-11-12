import os
from pathlib import Path
import re
import shutil

from loguru import logger
from human_bytes import HumanBytes
from config import CONF


def _validate_temp_path(temp_path: str|os.PathLike) -> Path:
    """Проверяет, что временную папку безопасно очищать.

    Защищает от случайного rmtree корня диска, домашнего каталога,
    текущего каталога и каталога, внутри которого находится проект.
    """
    raw_path = Path(temp_path).expanduser()
    if raw_path.is_symlink():
        raise ValueError(f"Временная папка не может быть символической ссылкой: {raw_path}")

    path = raw_path.resolve()
    cwd = Path.cwd().resolve()
    project_root = Path(__file__).resolve().parent.parent

    if path == Path(path.anchor):
        raise ValueError(f"Нельзя использовать корень диска как временную папку: {path}")
    if path == Path.home().resolve():
        raise ValueError(f"Нельзя использовать домашний каталог как временную папку: {path}")
    if cwd.is_relative_to(path):
        raise ValueError(
            f"Временная папка не должна содержать текущий каталог: {path}"
        )
    if project_root.is_relative_to(path):
        raise ValueError(
            f"Временная папка не должна содержать каталог проекта: {path}"
        )
    if path.exists() and not path.is_dir():
        raise NotADirectoryError(f"Временный путь не является каталогом: {path}")
    return path


def prepare_temp_folder():
    """Создаёт, либо очищает временную папку"""
    try:
        temp_path = _validate_temp_path(CONF.TEMP_PATH)
        if temp_path.exists():
            shutil.rmtree(temp_path)
            temp_path.mkdir(parents=True, exist_ok=True)
            logger.info("Временные файлы удалены")
        else:
            temp_path.mkdir(parents=True, exist_ok=True)
            logger.info(f"Создана папка временных файлов по пути {temp_path}")
    except PermissionError as e:
        logger.error(f"Ошибка доступа: {e}")
        raise
    except Exception as e:
        logger.critical(f"Неизвестная ошибка: {e}")
        raise


def get_temp_folder() -> Path:
    """Возвращает путь ко временной папке"""
    return _validate_temp_path(CONF.TEMP_PATH)


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

def multiline_to_hashtags(string: str, remove_namespaces: bool=False) -> str:
    """Преобразует построчный список теговых слов и фраз в строку с хэштегами.

    Parameters
    ----------
    string : str
        Строка с теговыми слов и фразами, разделёнными переносом строки

    remove_namespaces: bool
        Установленное в True удаляет пространства имён тегов и сортирует теги по основным категориям,
        разделённым каждая новой строкой

    Returns
    -------
    str
        Возвращается строка, состоящая из хэштегов
    """
    def clear_string(value: str) -> str:
        """Приводит тег к виду, допустимому в хэштеге Telegram."""
        value = value.strip().lstrip('#')
        # Символы, которые в исходных тегах было принято просто удалять
        value = value.replace('.', '').replace('(', '').replace(')', '').replace("'", '')
        value = value.replace('&', '_and_')
        # Всё, что не буква, цифра или подчёркивание, становится разделителем
        value = re.sub(r'[^\w]+', '_', value, flags=re.UNICODE)
        return re.sub(r'_+', '_', value).strip('_')

    def make_hashtag(value: str) -> str:
        value = clear_string(value)
        return f'#{value}' if value else ''

    if not remove_namespaces:
        hashtags = (make_hashtag(row) for row in string.split('\n'))
        return ' '.join(dict.fromkeys(tag for tag in hashtags if tag))

    # Группировка по упорядоченным категориям
    categories: dict[str, list[str]] = {
        "artists": [],
        "series": [],
        "characters": [],
        "meta": [],
        "unnamespaced_tags": []
    }
    namespace_categories = {
        "artist": "artists",
        "person": "artists",
        "series": "series",
        "pool": "series",
        "character": "characters",
        "meta": "meta",
    }

    for row in string.split('\n'):
        if not (line := row.strip()):
            continue
        namespace, separator, value = line.partition(':')
        category = namespace_categories.get(namespace.lower(), "unnamespaced_tags")
        tag = make_hashtag(value if separator else line)
        if tag and tag not in categories[category]:
            categories[category].append(tag)

    return '\n'.join(
        ' '.join(tags)
        for tags in categories.values()
        if tags
    )

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
