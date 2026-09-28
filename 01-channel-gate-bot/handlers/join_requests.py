"""
Ядро бота: обработка заявок на вступление в закрытый канал.
Одобряет заявку, заносит лида в БД и отправляет ему прогревающий оффер в ЛС.
"""
import logging

from aiogram import Bot, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import ChatJoinRequest

from config import BONUS_LINK
from database.db import add_or_update_user, update_user_status

logger = logging.getLogger(__name__)
router = Router(name="join_requests")


@router.chat_join_request()
async def handle_chat_join_request(event: ChatJoinRequest, bot: Bot) -> None:
    user = event.from_user

    invite_link_name = "Прямой запуск"
    if event.invite_link:
        invite_link_name = event.invite_link.name or event.invite_link.invite_link

    await add_or_update_user(
        user_id=user.id,
        username=user.username or "Не указан",
        full_name=user.full_name,
        invite_link=invite_link_name,
    )

    try:
        await event.approve()
    except TelegramBadRequest as e:
        logger.warning("Заявка пользователя %s не найдена или уже обработана: %s", user.id, e)
        return
    except Exception:
        logger.exception("Ошибка при одобрении заявки пользователя %s", user.id)
        return

    welcome_text = (
        f"👋 <b>Здравствуйте, {user.first_name}!</b>\n\n"
        "Ваша заявка на вступление успешно одобрена! 🎉\n\n"
        "🎁 <b>Забирайте ваш обещанный бонус:</b>\n"
        "Мы подготовили закрытый материал с подробным разбором процесса.\n\n"
        f"👉 <a href=\"{BONUS_LINK}\">Нажмите сюда, чтобы скачать бонус</a>"
    )

    try:
        await bot.send_message(
            chat_id=user.id,
            text=welcome_text,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except TelegramForbiddenError:
        # Пользователь заблокировал бота до получения приветствия
        await update_user_status(user.id, "blocked")
    except Exception:
        logger.exception("Не удалось отправить приветствие пользователю %s", user.id)
