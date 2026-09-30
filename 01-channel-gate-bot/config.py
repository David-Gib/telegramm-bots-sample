"""
Конфигурация приложения.
Все настройки читаются из .env — см. .env.example для списка обязательных переменных.
"""
import os

from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    """Читает обязательную переменную окружения и падает с понятной ошибкой, если её нет."""
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"Переменная окружения {name} не задана. "
            f"Проверь файл .env (см. .env.example) — он должен лежать рядом с bot.py."
        )
    return value


def _parse_admin_ids(raw: str) -> list[int]:
    try:
        ids = [int(x.strip()) for x in raw.split(",") if x.strip()]
    except ValueError as e:
        raise RuntimeError(
            "ADMIN_IDS должен содержать числовые Telegram ID через запятую, "
            f"например: ADMIN_IDS=123456789,987654321. Ошибка разбора: {e}"
        ) from e

    if not ids:
        raise RuntimeError("ADMIN_IDS задан, но не содержит ни одного ID.")
    return ids


BOT_TOKEN: str = _require("BOT_TOKEN")
ADMIN_IDS: list[int] = _parse_admin_ids(_require("ADMIN_IDS"))
DB_PATH: str = os.getenv("DB_PATH", "bot_database.db")
BONUS_LINK: str = os.getenv("BONUS_LINK", "https://example.com/bonus")
CONFIRM_TIMEOUT_SECONDS: int = int(os.getenv("CONFIRM_TIMEOUT_SECONDS", "300"))
MAX_REMINDER_ATTEMPTS: int = int(os.getenv("MAX_REMINDER_ATTEMPTS", "2"))
