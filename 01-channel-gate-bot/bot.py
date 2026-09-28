"""
Точка входа. Бот-привратник для закрытого Telegram-канала:
- авто-одобрение заявок на вступление
- прогревающий оффер с бонусом в ЛС
- админ-панель со статистикой и массовой рассылкой
"""
import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from config import BOT_TOKEN
from database.db import init_db
from handlers import admin, join_requests, user

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stdout,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())

    # Порядок важен: сначала admin (ловит только ID из ADMIN_IDS), затем user и join_requests
    dp.include_router(admin.router)
    dp.include_router(user.router)
    dp.include_router(join_requests.router)

    await init_db()
    logger.info("База данных инициализирована.")

    # Снимаем возможный старый webhook и висящие апдейты, иначе polling не запустится
    await bot.delete_webhook(drop_pending_updates=True)

    try:
        logger.info("Бот запущен, начинаю polling...")
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен.")
    except RuntimeError as e:
        logger.error("Ошибка конфигурации: %s", e)
        sys.exit(1)
