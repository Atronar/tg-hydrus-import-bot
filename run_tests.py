"""Запуск тестов проекта.

Обычный прогон (интеграционные тесты запускаются, если сервисы доступны):
    python run_tests.py

Принудительный запуск интеграционных тестов:
    RUN_INTEGRATION=1 python run_tests.py

Отключение интеграционных тестов:
    SKIP_INTEGRATION=1 python run_tests.py
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    suite = unittest.TestLoader().discover(
        start_dir=str(ROOT / "tests"),
        top_level_dir=str(ROOT),
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
