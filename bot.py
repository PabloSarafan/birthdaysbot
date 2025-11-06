import os
import logging
from telegram import Update
from telegram.ext import Updater, MessageHandler, Filters, CallbackContext
from dotenv import load_dotenv

# Загружаем переменные окружения из .env файла (для локальной разработки)
load_dotenv()

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


def handle_message(update: Update, context: CallbackContext) -> None:
    """Обработчик всех текстовых сообщений."""
    if update.message and update.message.text:
        logger.info(f"Получено сообщение от {update.effective_user.id}: {update.message.text}")
        update.message.reply_text("Я писька")


def main() -> None:
    """Запуск бота."""
    # Получаем токен из переменных окружения
    bot_token = os.getenv('BOT_TOKEN')
    
    if not bot_token:
        logger.error("BOT_TOKEN не найден в переменных окружения!")
        raise ValueError("BOT_TOKEN must be set in environment variables")
    
    logger.info("Запуск бота...")
    
    # Создаём updater и dispatcher
    updater = Updater(token=bot_token, use_context=True)
    dispatcher = updater.dispatcher
    
    # Регистрируем обработчик текстовых сообщений
    dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))
    
    # Запускаем бота
    logger.info("Бот запущен и готов к работе")
    updater.start_polling()
    updater.idle()


if __name__ == '__main__':
    main()

