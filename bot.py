import asyncio
import logging
import sys
import aiosqlite
from datetime import datetime

from aiogram import Bot, Dispatcher, F, Router, types
from aiogram.filters import Command, CommandStart, StateFilter

from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ChatJoinRequest,
    CallbackQuery
)
import os
from dotenv import load_dotenv

load_dotenv()  # Загружает переменные из файла .env
# -------------------------------------------------------------------
# КОНФИГУРАЦИЯ ПРИЛОЖЕНИЯ
# -------------------------------------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS").split(",")]
DB_PATH = os.getenv("DB_PATH", "bot_database.db")

# -------------------------------------------------------------------
# ИНИЦИАЛИЗАЦИЯ И FSM
# -------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, stream=sys.stdout)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)


class BroadcastState(StatesGroup):
    waiting_for_message = State()


# -------------------------------------------------------------------
# СЛОЙ РАБОТЫ С БАЗОЙ ДАННЫХ (SQLite / aiosqlite)
# -------------------------------------------------------------------
async def init_db():
    """Инициализация таблиц базы данных при старте"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
                         CREATE TABLE IF NOT EXISTS users
                         (
                             user_id
                             INTEGER
                             PRIMARY
                             KEY,
                             username
                             TEXT,
                             full_name
                             TEXT,
                             invite_link
                             TEXT,
                             status
                             TEXT
                             DEFAULT
                             'active',
                             created_at
                             TIMESTAMP
                             DEFAULT
                             CURRENT_TIMESTAMP
                         )
                         """)
        await db.commit()


async def add_or_update_user(user_id: int, username: str, full_name: str, invite_link: str):
    """Добавление нового лида или обновление его статуса"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
                         INSERT INTO users (user_id, username, full_name, invite_link, status)
                         VALUES (?, ?, ?, ?, 'active') ON CONFLICT(user_id) DO
                         UPDATE SET
                             username = excluded.username,
                             full_name = excluded.full_name,
                             status = 'active'
                         """, (user_id, username, full_name, invite_link))
        await db.commit()


async def update_user_status(user_id: int, status: str):
    """Обновление статуса (например, если бот заблокирован юзером)"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET status = ? WHERE user_id = ?", (status, user_id))
        await db.commit()


async def get_stats():
    """Получение метрик для админ-панели"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cursor:
            total = (await cursor.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM users WHERE status = 'active'") as cursor:
            active = (await cursor.fetchone())[0]
        return total, active


async def get_active_user_ids():
    """Получение всех активных ID для рассылки"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM users WHERE status = 'active'") as cursor:
            rows = await cursor.fetchall()
            return [row[0] for row in rows]


# -------------------------------------------------------------------
# КЛАВИАТУРЫ
# -------------------------------------------------------------------
def get_admin_keyboard():
    buttons = [
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="📢 Массовая рассылка", callback_data="admin_broadcast")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_cancel_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="admin_cancel")]]
    )


# -------------------------------------------------------------------
# ОБРАБОТЧИК СОБЫТИЯ ChatJoinRequest (ЯДРО БОТА)
# -------------------------------------------------------------------
@router.chat_join_request()
async def handle_chat_join_request(event: ChatJoinRequest):
    """
    Перехватывает подачу заявки в закрытый канал, заносит юзера в БД,
    одобряет заявку и отправляет оффер в ЛС.
    """
    user = event.from_user

    # Извлекаем название или URL пригласительной ссылки (для аналитики трафика)
    invite_link_name = "Прямой запуск"
    if event.invite_link:
        invite_link_name = event.invite_link.name or event.invite_link.invite_link

    # 1. Заносим в БД
    await add_or_update_user(
        user_id=user.id,
        username=user.username or "Не указан",
        full_name=user.full_name,
        invite_link=invite_link_name
    )

    # 2. Автоматически одобряем заявку в канал
    try:
        await event.approve()
    except TelegramBadRequest as e:
        logging.warning(f"Заявка пользователя {user.id} не найдена или уже обработана: {e}")
    except Exception as e:
        logging.error(f"Ошибка при одобрении заявки: {e}")

    # 3. Отправляем прогревающий оффер в ЛС
    welcome_text = (
        f"👋 **Здравствуйте, {user.first_name}!**\n\n"
        "Ваша заявка на вступление успешно одобрена! 🎉\n\n"
        "🎁 **Забирайте ваш обещанный бонус:**\n"
        "Мы подготовили закрытый материал с подробным разбором процесса.\n\n"
        "👉 [Нажмите сюда, чтобы скачать бонус](https://example.com/bonus)"
    )

    try:
        await bot.send_message(
            chat_id=user.id,
            text=welcome_text,
            parse_mode="Markdown",
            disable_web_page_preview=True
        )
    except TelegramForbiddenError:
        # Пользователь заблокировал бота до получения сообщения
        await update_user_status(user.id, "blocked")
    except Exception as e:
        logging.error(f"Не удалось отправить приветствие пользователю {user.id}: {e}")


# -------------------------------------------------------------------
# АДМИН-ПАНЕЛЬ И КОМАНДЫ
# -------------------------------------------------------------------
@router.message(CommandStart(), F.from_user.id.in_(ADMIN_IDS))
async def cmd_admin_start(message: Message):
    """Стартовое меню для администраторов"""
    await message.answer(
        "🛠 **Панель управления администратора**\n\n"
        "Выберите необходимое действие:",
        reply_markup=get_admin_keyboard(),
        parse_mode="Markdown"
    )


@router.message(CommandStart())
async def cmd_user_start(message: Message):
    """Старт для обычных пользователей"""
    await message.answer("Здравствуйте! Бот работает в автоматическом режиме для обработки заявок.")


@router.callback_query(F.data == "admin_stats", F.from_user.id.in_(ADMIN_IDS))
async def process_stats(callback: CallbackQuery):
    """Вывод метрик базы данных"""
    total, active = await get_stats()
    text = (
        "📈 **Статистика бота:**\n\n"
        f"👤 Всего пользователей в БД: **{total}**\n"
        f"✅ Активных (получают сообщения): **{active}**\n"
        f"❌ Заблокировали бота: **{total - active}**"
    )
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=get_admin_keyboard())
    await callback.answer()


@router.callback_query(F.data == "admin_broadcast", F.from_user.id.in_(ADMIN_IDS))
async def start_broadcast(callback: CallbackQuery, state: FSMContext):
    """Запуск FSM-сценария рассылки"""
    await state.set_state(BroadcastState.waiting_for_message)
    await callback.message.edit_text(
        "📝 **Пришлите сообщение для рассылки.**\n\n"
        "Поддерживаются текст, фото, видео и форматирование.",
        reply_markup=get_cancel_keyboard(),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.callback_query(F.data == "admin_cancel", StateFilter(BroadcastState.waiting_for_message))
async def cancel_broadcast(callback: CallbackQuery, state: FSMContext):
    """Отмена рассылки"""
    await state.clear()
    await callback.message.edit_text("❌ Рассылка отменена.", reply_markup=get_admin_keyboard())
    await callback.answer()


@router.message(BroadcastState.waiting_for_message, F.from_user.id.in_(ADMIN_IDS))
async def execute_broadcast(message: Message, state: FSMContext):
    """Выполнение рассылки с защитой от Flood Control"""
    await state.clear()
    users = await get_active_user_ids()

    await message.answer(f"🚀 Рассылка запущена на **{len(users)}** пользователей...", parse_mode="Markdown")

    count_success = 0
    count_blocked = 0

    for user_id in users:
        try:
            # Пересылаем точную копию сообщения админа
            await message.copy_to(chat_id=user_id)
            count_success += 1
            # Пауза 0.05 сек для соблюдения лимита Telegram (30 сообщений в секунду)
            await asyncio.sleep(0.05)
        except TelegramForbiddenError:
            await update_user_status(user_id, "blocked")
            count_blocked += 1
        except Exception as e:
            logging.error(f"Ошибка при отправке юзеру {user_id}: {e}")

    await message.answer(
        "✅ **Рассылка завершена!**\n\n"
        f"📥 Доставлено: **{count_success}**\n"
        f"🚫 Пользователь заблокировал бота: **{count_blocked}**",
        parse_mode="Markdown",
        reply_markup=get_admin_keyboard()
    )


# -------------------------------------------------------------------
# ТОЧКА ВХОДА И ЗАПУСК
# -------------------------------------------------------------------
async def main():
    await init_db()  # Автоматическое создание файла БД
    logging.info("База данных инициализирована.")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())