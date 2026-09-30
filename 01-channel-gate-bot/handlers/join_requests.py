"""
Ядро бота: заявка на вступление не одобряется автоматически — сначала
пользователь должен подтвердить осознанный интерес.

Важно (это не очевидно и ломает рассылку, если не учесть): Telegram даёт
боту право писать пользователю в будущем, только если пользователь САМ
отправил боту настоящее сообщение (например, /start) — обычный клик по
инлайн-кнопке (callback) для этого НЕ считается. Поэтому подтверждение
сделано через кнопку-ссылку вида t.me/<bot>?start=confirm_<user_id>:
при нажатии Telegram открывает чат с ботом и автоматически отправляет
"/start confirm_<user_id>" как настоящее сообщение от пользователя —
для человека это один и тот же тап, но право писать ему закрепляется
надёжно.

Если пользователь не подтверждает вовремя, заявка не отклоняется сразу —
отправляется до MAX_REMINDER_ATTEMPTS напоминаний, и только потом отказ.
"""
import asyncio
import logging
import re

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import ChatJoinRequest, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import BONUS_LINK, CONFIRM_TIMEOUT_SECONDS, MAX_REMINDER_ATTEMPTS
from database.db import bump_reminder, create_pending_request, get_pending_request, mark_declined, mark_verified

logger = logging.getLogger(__name__)
router = Router(name="join_requests")

# Заполняется один раз при старте бота (см. bot.py) через set_bot_username().
_bot_username: str | None = None


def set_bot_username(username: str) -> None:
    """Вызывается один раз при старте бота — нужно для сборки диплинк-кнопки."""
    global _bot_username
    _bot_username = username


def _build_confirm_keyboard(user_id: int) -> InlineKeyboardMarkup:
    if not _bot_username:
        raise RuntimeError(
            "Имя бота не установлено. Убедись, что bot.py вызывает "
            "join_requests.set_bot_username() перед стартом polling."
        )
    deep_link = f"https://t.me/{_bot_username}?start=confirm_{user_id}"
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="✅ Подтверждаю и забираю бонус", url=deep_link)]]
    )


def _build_welcome_text(first_name: str) -> str:
    return (
        f"👋 Здравствуйте, {first_name}!\n\n"
        "Доступ в канал и бонус для новых участников уже готовы для вас.\n"
        "Осталось одно — подтвердить, что вы здесь осознанно."
    )


def _build_reminder_text(is_last: bool) -> str:
    if is_last:
        return (
            "⏳ Это последнее напоминание.\n\n"
            "Доступ в канал и бонус всё ещё вас ждут, но если не подтвердить сейчас, "
            "заявку придётся отклонить."
        )
    return (
        "⏳ Не забудьте забрать свой доступ и бонус — "
        "осталось только подтвердить, что вы здесь осознанно."
    )


@router.chat_join_request()
async def handle_chat_join_request(event: ChatJoinRequest, bot: Bot) -> None:
    user = event.from_user

    invite_link_name = "Прямой запуск"
    if event.invite_link:
        invite_link_name = event.invite_link.name or event.invite_link.invite_link

    try:
        sent = await bot.send_message(
            chat_id=user.id,
            text=_build_welcome_text(user.first_name),
            reply_markup=_build_confirm_keyboard(user.id),
        )
    except TelegramForbiddenError:
        # Пользователь заблокировал бота ещё до нашего сообщения — без диалога
        # с ним заявку подтвердить невозможно, поэтому отклоняем сразу.
        logger.warning("Не удалось написать пользователю %s: бот заблокирован", user.id)
        try:
            await event.decline()
        except Exception:
            logger.exception("Не удалось отклонить заявку пользователя %s", user.id)
        return
    except Exception:
        logger.exception("Не удалось отправить сообщение с подтверждением пользователю %s", user.id)
        return

    await create_pending_request(
        user_id=user.id,
        username=user.username or "Не указан",
        full_name=user.full_name,
        invite_link=invite_link_name,
        chat_id=event.chat.id,
        confirm_message_id=sent.message_id,
    )

    asyncio.create_task(_handle_timeout(bot, user.id, event.chat.id, sent.message_id))


async def _handle_timeout(bot: Bot, user_id: int, chat_id: int, message_id: int) -> None:
    """
    Срабатывает через CONFIRM_TIMEOUT_SECONDS после отправки сообщения.
    Если пользователь ещё не подтвердил — не отклоняем сразу, а шлём
    напоминание (новым сообщением, чтобы пришло уведомление), и только
    после MAX_REMINDER_ATTEMPTS напоминаний без ответа отклоняем заявку.
    """
    await asyncio.sleep(CONFIRM_TIMEOUT_SECONDS)

    pending = await get_pending_request(user_id)
    if pending is None or pending["status"] != "pending":
        return  # пользователь уже подтвердил, либо заявка уже отклонена

    attempt = pending["reminder_count"] + 1

    if attempt <= MAX_REMINDER_ATTEMPTS:
        is_last = attempt == MAX_REMINDER_ATTEMPTS
        try:
            sent = await bot.send_message(
                chat_id=user_id,
                text=_build_reminder_text(is_last),
                reply_markup=_build_confirm_keyboard(user_id),
            )
        except TelegramForbiddenError:
            try:
                await bot.decline_chat_join_request(chat_id=chat_id, user_id=user_id)
            except Exception:
                logger.exception("Не удалось отклонить заявку пользователя %s", user_id)
            await mark_declined(user_id, reason="blocked")
            return
        except Exception:
            logger.exception("Не удалось отправить напоминание пользователю %s", user_id)
            return

        await bump_reminder(user_id, sent.message_id)
        asyncio.create_task(_handle_timeout(bot, user_id, chat_id, sent.message_id))
        return

    # Напоминания закончились, подтверждения так и не было — отклоняем.
    try:
        await bot.decline_chat_join_request(chat_id=chat_id, user_id=user_id)
    except TelegramBadRequest as e:
        logger.warning("Заявка пользователя %s уже недоступна для отклонения: %s", user_id, e)
    except Exception:
        logger.exception("Не удалось отклонить просроченную заявку пользователя %s", user_id)

    await mark_declined(user_id, reason="timeout")

    try:
        await bot.edit_message_text(
            chat_id=user_id,
            message_id=message_id,
            text="⌛ Время ожидания истекло, заявка отклонена. Подайте заявку заново, если это ошибка.",
            reply_markup=None,
        )
    except Exception:
        logger.exception("Не удалось отредактировать сообщение для %s", user_id)


@router.message(F.text.regexp(r"^/start confirm_(\d+)$").as_("match"))
async def process_confirm_deep_link(message: Message, match: re.Match[str], bot: Bot) -> None:
    """
    Ловит ТОЛЬКО "/start confirm_<id>" — деплинк из нашей кнопки. Обычный
    голый /start этим фильтром не перехватывается и уходит в admin.py/user.py.
    """
    expected_user_id = int(match.group(1))
    user_id = message.from_user.id

    if user_id != expected_user_id:
        # Ссылка предназначалась другому пользователю — молча игнорируем.
        return

    pending = await get_pending_request(user_id)
    if pending is None or pending["status"] != "pending":
        await message.answer("Эта заявка уже обработана.")
        return

    try:
        await bot.approve_chat_join_request(chat_id=pending["chat_id"], user_id=user_id)
    except TelegramBadRequest as e:
        logger.warning("Заявка пользователя %s не найдена или уже обработана: %s", user_id, e)
        await message.answer("Не удалось одобрить заявку — возможно, она уже обработана.")
        return
    except Exception:
        logger.exception("Ошибка при одобрении заявки пользователя %s", user_id)
        await message.answer("Произошла ошибка, попробуйте позже.")
        return

    await mark_verified(user_id)

    welcome_text = (
        "✅ Отлично, вы в канале!\n\n"
        f"🎁 Забирайте ваш бонус: <a href=\"{BONUS_LINK}\">по этой ссылке</a>"
    )
    await message.answer(welcome_text, parse_mode="HTML", disable_web_page_preview=True)

    # Косметика: убираем кнопку с предыдущего сообщения, чтобы не висела бесполезная ссылка.
    if pending["confirm_message_id"]:
        try:
            await bot.edit_message_reply_markup(
                chat_id=user_id, message_id=pending["confirm_message_id"], reply_markup=None
            )
        except Exception:
            pass  # не критично — чисто косметическая правка
