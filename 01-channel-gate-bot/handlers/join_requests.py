import asyncio
import logging
from typing import Dict, Tuple

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, ChatJoinRequest, InlineKeyboardButton, InlineKeyboardMarkup

from config import BONUS_LINK, CONFIRM_TIMEOUT_SECONDS
from database.db import add_user

router = Router()
logger = logging.getLogger(__name__)

# Хранилище запущенных таймеров. Ключ: (chat_id, user_id) -> asyncio.Task
pending_tasks: Dict[Tuple[int, int], asyncio.Task] = {}


def get_confirm_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    """Создает клавиатуру с кнопкой подтверждения."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Подтверждаю и забираю бонус",
                    callback_data=f"confirm_join:{chat_id}",
                )
            ]
        ]
    )


async def request_timeout_lifecycle(
    chat_id: int,
    user_id: int,
    message_id: int,
    bot: Bot,
) -> None:
    """
    Жизненный цикл ожидания подтверждения с 2 напоминаниями и итоговым отклонением.
    """
    key = (chat_id, user_id)
    try:
        # === Этап 1: Ждем первого таймаута ===
        await asyncio.sleep(CONFIRM_TIMEOUT_SECONDS)

        # Первое напоминание (мягкое)
        reminder_1 = (
            "⏳ **Доступ и бонус всё ещё ждут вас!**\n\n"
            "Вы подали заявку на вступление в канал. "
            "Подтвердите, что вы не бот, чтобы мы могли одобрить доступ."
        )
        await bot.edit_message_text(
            chat_id=user_id,
            message_id=message_id,
            text=reminder_1,
            reply_markup=get_confirm_keyboard(chat_id),
            parse_mode="Markdown",
        )
        logger.info(f"Отправлено 1-е напоминание пользователю {user_id}")

        # === Этап 2: Ждем второго таймаута ===
        await asyncio.sleep(CONFIRM_TIMEOUT_SECONDS)

        # Второе напоминание (строгое / последнее)
        reminder_2 = (
            "⚠️ **Это последний шанс!**\n\n"
            "Ваша заявка будет автоматически отклонена через несколько минут. "
            "Нажмите кнопку ниже, чтобы войти в канал и забрать бонус:"
        )
        await bot.edit_message_text(
            chat_id=user_id,
            message_id=message_id,
            text=reminder_2,
            reply_markup=get_confirm_keyboard(chat_id),
            parse_mode="Markdown",
        )
        logger.info(f"Отправлено 2-е напоминание пользователю {user_id}")

        # === Этап 3: Ждем финального таймаута ===
        await asyncio.sleep(CONFIRM_TIMEOUT_SECONDS)

        # Если не нажал — отклоняем заявку в канал
        await bot.decline_chat_join_request(chat_id=chat_id, user_id=user_id)

        await bot.edit_message_text(
            chat_id=user_id,
            message_id=message_id,
            text="❌ **Время ожидания истекло.**\n\nВаша заявка на вступление была отклонена. Если захотите вступить снова — подайте заявку заново.",
            parse_mode="Markdown",
        )
        logger.info(f"Заявка пользователя {user_id} в канал {chat_id} отклонена по таймауту.")

    except asyncio.CancelledError:
        # Задача была отменена, так как пользователь вовремя нажал кнопку
        logger.info(f"Таймаут для пользователя {user_id} отменен (подтвердил вход).")
    except TelegramBadRequest as e:
        logger.warning(f"Ошибка при обновлении сообщения для {user_id}: {e}")
    finally:
        # В любом случае удаляем задачу из реестра
        pending_tasks.pop(key, None)


@router.chat_join_request()
async def handle_join_request(event: ChatJoinRequest, bot: Bot) -> None:
    """Перехват заявки на вступление и запуск таймеров."""
    user_id = event.from_user.id
    chat_id = event.chat.id
    key = (chat_id, user_id)

    # Если для этого юзера уже есть запущенный таймер (например, подал заявку повторно) — отменяем старый
    if key in pending_tasks:
        pending_tasks[key].cancel()

    start_text = (
        f"👋 Здравствуйте, {event.from_user.full_name}!\n\n"
        f"Доступ в канал и бонус для новых участников уже готовы для вас.\n\n"
        f"Осталось одно — подтвердить, что вы здесь осознанно."
    )

    try:
        sent_message = await bot.send_message(
            chat_id=user_id,
            text=start_text,
            reply_markup=get_confirm_keyboard(chat_id),
        )

        # Запускаем фоновую задачу с цепочкой напоминаний
        task = asyncio.create_task(
            request_timeout_lifecycle(
                chat_id=chat_id,
                user_id=user_id,
                message_id=sent_message.message_id,
                bot=bot,
            )
        )
        pending_tasks[key] = task

    except TelegramBadRequest as e:
        logger.error(f"Не удалось отправить приветствие пользователю {user_id} (возможно, не запускал бота): {e}")


@router.callback_query(F.data.startswith("confirm_join:"))
async def process_confirm_join(callback: CallbackQuery, bot: Bot) -> None:
    """Обработка клика по кнопке подтверждения."""
    chat_id = int(callback.data.split(":")[1])
    user = callback.from_user
    key = (chat_id, user.id)

    # 1. Отменяем фоновый таймер с напоминаниями
    if key in pending_tasks:
        pending_tasks[key].cancel()
        pending_tasks.pop(key, None)

    try:
        # 2. Одобряем заявку в Telegram
        await bot.approve_chat_join_request(chat_id=chat_id, user_id=user.id)

        # 3. Сохраняем пользователя в БД
        await add_user(
            user_id=user.id,
            username=user.username,
            full_name=user.full_name,
        )

        # 4. Клавиатура со ссылкой на бонус
        bonus_keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🎁 Забрать бонус", url=BONUS_LINK)]
            ]
        )

        # 5. Меняем текст сообщения на финальное
        await callback.message.edit_text(
            text="✅ **Отлично, вы в канале!**\n\nЗабирайте ваш бонус по кнопке ниже:",
            reply_markup=bonus_keyboard,
            parse_mode="Markdown",
        )
        await callback.answer("Заявка успешно одобрена!")

    except TelegramBadRequest as e:
        logger.error(f"Ошибка при одобрении заявки {user.id}: {e}")
        await callback.answer(
            "Не удалось одобрить заявку. Возможно, она уже недействительна.",
            show_alert=True,
        )