"""Обработчики для обычных (не-админ) пользователей."""
from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from config import ADMIN_IDS

router = Router(name="user")


@router.message(CommandStart(), F.from_user.id.not_in(ADMIN_IDS), F.text == "/start")
async def cmd_user_start(message: Message) -> None:
    # F.text == "/start" — намеренно узкий фильтр: он ловит только голый
    # /start без параметров, чтобы не перехватывать диплинк-подтверждение
    # вида "/start confirm_123" из handlers/join_requests.py.
    await message.answer("Здравствуйте! Бот работает в автоматическом режиме для обработки заявок.")
