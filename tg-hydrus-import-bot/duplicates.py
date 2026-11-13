"""Правила приоритета источников при дедупликации.

Список приоритетных доменов хранится отдельно от основного конфига:
его удобно редактировать, и он не попадает в репозиторий.
"""

from pathlib import Path
from urllib.parse import urlparse

DUPLICATE_DOMAINS_PATH = Path("duplicate_domains.txt")


def load_priority_domains(
    path: str | Path = DUPLICATE_DOMAINS_PATH
) -> frozenset[str]:
    """Читает приоритетные домены из файла.

    Пустые строки и всё после ``#`` игнорируются. Домен приводится
    к нижнему регистру, ведущая точка отбрасывается. Если файла нет,
    возвращается пустой набор: приоритетных доменов просто нет.
    """
    try:
        content = Path(path).read_text(encoding="utf-8")
    except OSError:
        return frozenset()

    domains: set[str] = set()
    for raw_line in content.splitlines():
        line = raw_line.split("#", 1)[0].strip().lower().lstrip(".")
        if line:
            domains.add(line)
    return frozenset(domains)


def url_hostname(url: str) -> str | None:
    """Возвращает hostname URL в нижнем регистре без завершающей точки."""
    hostname = urlparse(url).hostname
    if not hostname:
        return None
    return hostname.lower().rstrip(".")


def is_priority_url(url: str, domains: frozenset[str] | set[str]) -> bool:
    """Проверяет, относится ли URL к приоритетному домену.

    Поддомены считаются частью домена: ``cdn.example.com`` подходит
    для записи ``example.com``.
    """
    if not domains:
        return False
    hostname = url_hostname(url)
    if not hostname:
        return False
    return any(
        hostname == domain or hostname.endswith(f".{domain}")
        for domain in domains
    )
