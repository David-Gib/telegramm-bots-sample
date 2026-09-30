"""Админ-панель: старт, статистика, массовая рассылка с защитой от Flood Control."""
import asyncio
import logging

from aiogram import F, Router
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import CallbackQuery, Message

from config import ADMIN_IDS
from database.db import get_active_user_ids, get_stats, update_user_status
from keyboards.admin_kb import get_admin_keyboard, get_cancel_keyboard
from states import BroadcastState

logger = logging.getLogger(__name__)
router = Router(name="admin")


@router.message(CommandStart(), F.from_user.id.in_(ADMIN_IDS))
async def cmd_admin_start(message: Message) -> None:
    await message.answer(
        "🛠 <b>Панель управления администратора</b>\n\nВыберите необходимое действие:",
        reply_markup=get_admin_keyboard(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "admin_stats", F.from_user.id.in_(ADMIN_IDS))
async def process_stats(callback: CallbackQuery) -> None:
    total, verified, pending, declined, blocked = await get_stats()
    text = (
        "📈 <b>Статистика бота:</b>\n\n"
        f"👤 Всего заявок: <b>{total}</b>\n"
        f"✅ Подтвердили интерес и подписаны: <b>{verified}</b>\n"
        f"⏳ Ждут ответа: <b>{pending}</b>\n"
        f"🚫 Отклонены по таймауту: <b>{declined}</b>\n"
        f"❌ Заблокировали бота: <b>{blocked}</b>"
    )
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=get_admin_keyboard())
    except Exception:
        logger.exception("Не удалось обновить сообщение со статистикой")
    await callback.answer()


@router.callback_query(F.data == "admin_broadcast", F.from_user.id.in_(ADMIN_IDS))
async def start_broadcast(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(BroadcastState.waiting_for_message)
    await callback.message.edit_text(
        "📝 <b>Пришлите сообщение для рассылки.</b>\n\nПоддерживаются текст, фото, видео и форматирование.",
        reply_markup=get_cancel_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "admin_cancel", StateFilter(BroadcastState.waiting_for_message))
async def cancel_broadcast(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("❌ Рассылка отменена.", reply_markup=get_admin_keyboard())
    await callback.answer()


@router.message(BroadcastState.waiting_for_message, F.from_user.id.in_(ADMIN_IDS))
async def execute_broadcast(message: Message, state: FSMContext) -> None:
    await state.clear()
    users = await get_active_user_ids()

    await message.answer(f"🚀 Рассылка запущена на <b>{len(users)}</b> пользователей...", parse_mode="HTML")

    count_success = 0
    count_blocked = 0

    for user_id in users:
        try:
            await message.copy_to(chat_id=user_id)
            count_success += 1
            # Пауза для соблюдения лимита Telegram (~30 сообщений в секунду)
            await asyncio.sleep(0.05)
        except TelegramForbiddenError:
            await update_user_status(user_id, "blocked")
            count_blocked += 1
        except TelegramRetryAfter as e:
            # Telegram попросил притормозить — ждём указанное время и повторяем попытку один раз
            logger.warning("Flood control: ждём %s сек. перед повтором для %s", e.retry_after, user_id)
            await asyncio.sleep(e.retry_after)
            try:
                await message.copy_to(chat_id=user_id)
                count_success += 1
            except Exception:
                logger.exception("Повторная отправка юзеру %s не удалась", user_id)
        except Exception:
            logger.exception("Ошибка при отправке юзеру %s", user_id)

    await message.answer(
        "✅ <b>Рассылка завершена!</b>\n\n"
        f"📥 Доставлено: <b>{count_success}</b>\n"
        f"🚫 Пользователь заблокировал бота: <b>{count_blocked}</b>",
        parse_mode="HTML",
        reply_markup=get_admin_keyboard(),
    )
