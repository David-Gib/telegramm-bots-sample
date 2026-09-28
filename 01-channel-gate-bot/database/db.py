"""
Слой работы с базой данных (SQLite / aiosqlite).
Хранит лидов, пришедших через заявку на вступление в закрытый канал.
"""
import logging

import aiosqlite

from config import DB_PATH

logger = logging.getLogger(__name__)


async def init_db() -> None:
    """Создаёт таблицу users при первом запуске (если её ещё нет)."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id     INTEGER PRIMARY KEY,
                    username    TEXT,
                    full_name   TEXT,
                    invite_link TEXT,
                    status      TEXT DEFAULT 'active',
                    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            await db.commit()
    except Exception:
        logger.exception("Не удалось инициализировать базу данных")
        raise


async def add_or_update_user(user_id: int, username: str, full_name: str, invite_link: str) -> None:
    """Добавляет нового лида или обновляет его данные и статус на 'active'."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """
                INSERT INTO users (user_id, username, full_name, invite_link, status)
                VALUES (?, ?, ?, ?, 'active')
                ON CONFLICT(user_id) DO UPDATE SET
                    username = excluded.username,
                    full_name = excluded.full_name,
                    status = 'active'
                """,
                (user_id, username, full_name, invite_link),
            )
            await db.commit()
    except Exception:
        logger.exception("Не удалось сохранить пользователя %s в БД", user_id)


async def update_user_status(user_id: int, status: str) -> None:
    """Обновляет статус пользователя (например, 'blocked', если он заблокировал бота)."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE users SET status = ? WHERE user_id = ?", (status, user_id))
            await db.commit()
    except Exception:
        logger.exception("Не удалось обновить статус пользователя %s", user_id)


async def get_stats() -> tuple[int, int]:
    """Возвращает (всего пользователей, активных пользователей)."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cursor:
            row = await cursor.fetchone()
            total = row[0] if row else 0
        async with db.execute("SELECT COUNT(*) FROM users WHERE status = 'active'") as cursor:
            row = await cursor.fetchone()
            active = row[0] if row else 0
    return total, active


async def get_active_user_ids() -> list[int]:
    """Возвращает ID всех активных пользователей — для рассылки."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM users WHERE status = 'active'") as cursor:
            rows = await cursor.fetchall()
    return [row[0] for row in rows]
