"""
Слой работы с базой данных (SQLite / aiosqlite).

Жизненный цикл записи пользователя:
  pending  — заявка получена, кнопка подтверждения отправлена, ждём клик
  active + subscribed=1 — подтвердил, заявка одобрена, можно слать рассылку
  declined_timeout — не подтвердил даже после всех напоминаний
  blocked  — реальная блокировка бота, обнаруженная во время рассылки
"""
import logging

import aiosqlite

from config import DB_PATH

logger = logging.getLogger(__name__)


async def init_db() -> None:
    """Создаёт таблицу users при первом запуске и докатывает старые базы миграцией."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id             INTEGER PRIMARY KEY,
                    username            TEXT,
                    full_name           TEXT,
                    invite_link         TEXT,
                    status              TEXT DEFAULT 'pending',
                    subscribed          INTEGER NOT NULL DEFAULT 0,
                    chat_id             INTEGER,
                    reminder_count      INTEGER NOT NULL DEFAULT 0,
                    confirm_message_id  INTEGER,
                    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            # Миграция для баз, созданных до появления системы напоминаний.
            for ddl in (
                "ALTER TABLE users ADD COLUMN subscribed INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE users ADD COLUMN chat_id INTEGER",
                "ALTER TABLE users ADD COLUMN reminder_count INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE users ADD COLUMN confirm_message_id INTEGER",
            ):
                try:
                    await db.execute(ddl)
                except aiosqlite.OperationalError:
                    pass  # колонка уже существует
            await db.commit()
    except Exception:
        logger.exception("Не удалось инициализировать базу данных")
        raise


async def create_pending_request(
    user_id: int,
    username: str,
    full_name: str,
    invite_link: str,
    chat_id: int,
    confirm_message_id: int,
) -> None:
    """Заносит новую заявку в БД в статусе 'pending', счётчик напоминаний обнуляется."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """
                INSERT INTO users (
                    user_id, username, full_name, invite_link,
                    status, subscribed, chat_id, reminder_count, confirm_message_id
                )
                VALUES (?, ?, ?, ?, 'pending', 0, ?, 0, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    username = excluded.username,
                    full_name = excluded.full_name,
                    invite_link = excluded.invite_link,
                    status = 'pending',
                    subscribed = 0,
                    chat_id = excluded.chat_id,
                    reminder_count = 0,
                    confirm_message_id = excluded.confirm_message_id
                """,
                (user_id, username, full_name, invite_link, chat_id, confirm_message_id),
            )
            await db.commit()
    except Exception:
        logger.exception("Не удалось создать заявку на подтверждение для %s", user_id)


async def get_pending_request(user_id: int) -> dict | None:
    """Возвращает текущее состояние заявки пользователя или None, если его нет в БД."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT user_id, status, chat_id, reminder_count, confirm_message_id "
            "FROM users WHERE user_id = ?",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
    return dict(row) if row else None


async def bump_reminder(user_id: int, confirm_message_id: int) -> None:
    """Увеличивает счётчик напоминаний и запоминает id нового сообщения с кнопкой."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE users SET reminder_count = reminder_count + 1, confirm_message_id = ? "
                "WHERE user_id = ?",
                (confirm_message_id, user_id),
            )
            await db.commit()
    except Exception:
        logger.exception("Не удалось обновить счётчик напоминаний для %s", user_id)


async def mark_verified(user_id: int) -> None:
    """Подтверждение получено, заявка одобрена — пользователь готов получать рассылку."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE users SET status = 'active', subscribed = 1 WHERE user_id = ?",
                (user_id,),
            )
            await db.commit()
    except Exception:
        logger.exception("Не удалось подтвердить верификацию пользователя %s", user_id)


async def mark_declined(user_id: int, reason: str) -> None:
    """Заявка отклонена (после всех напоминаний, или бот был заблокирован ещё до отправки)."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE users SET status = ? WHERE user_id = ?",
                (f"declined_{reason}", user_id),
            )
            await db.commit()
    except Exception:
        logger.exception("Не удалось пометить заявку пользователя %s как отклонённую", user_id)


async def update_user_status(user_id: int, status: str) -> None:
    """Обновление статуса (например, 'blocked', если бота заблокировали во время рассылки)."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE users SET status = ? WHERE user_id = ?", (status, user_id))
            await db.commit()
    except Exception:
        logger.exception("Не удалось обновить статус пользователя %s", user_id)


async def get_stats() -> tuple[int, int, int, int, int]:
    """Возвращает (всего, подтвердили, ждут ответа, отклонены по таймауту, заблокировали)."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cursor:
            total = (await cursor.fetchone())[0]
        async with db.execute(
            "SELECT COUNT(*) FROM users WHERE status = 'active' AND subscribed = 1"
        ) as cursor:
            verified = (await cursor.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM users WHERE status = 'pending'") as cursor:
            pending = (await cursor.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM users WHERE status = 'blocked'") as cursor:
            blocked = (await cursor.fetchone())[0]
    declined = total - verified - pending - blocked
    return total, verified, pending, declined, blocked


async def get_active_user_ids() -> list[int]:
    """ID пользователей, подтвердивших интерес — только им можно слать рассылку."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT user_id FROM users WHERE status = 'active' AND subscribed = 1"
        ) as cursor:
            rows = await cursor.fetchall()
    return [row[0] for row in rows]
