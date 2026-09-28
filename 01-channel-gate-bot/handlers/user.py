"""Обработчики для обычных (не-админ) пользователей."""
from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from config import ADMIN_IDS

router = Router(name="user")


@router.message(CommandStart(), F.from_user.id.not_in(ADMIN_IDS))
async def cmd_user_start(message: Message) -> None:
    await message.answer("Здравствуйте! Бот работает в автоматическом режиме для обработки заявок.")
