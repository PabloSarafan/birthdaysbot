import os
import logging
from datetime import datetime, date
from telegram import Update
from telegram.ext import (
    Updater, 
    CommandHandler, 
    MessageHandler, 
    Filters, 
    CallbackContext,
    ConversationHandler
)
from dotenv import load_dotenv
import database
import scheduler

# Загружаем переменные окружения из .env файла (для локальной разработки)
load_dotenv()

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Состояния для ConversationHandler
WAITING_NAME, WAITING_DATE = range(2)
WAITING_DELETE_ID, WAITING_EDIT_ID, WAITING_EDIT_NAME, WAITING_EDIT_DATE = range(2, 6)


def start(update: Update, context: CallbackContext) -> None:
    """Обработчик команды /start."""
    user = update.effective_user
    welcome_message = f"""
👋 Привет, {user.first_name}!

Я бот-напоминалка о днях рождения. Помогу не забыть поздравить друзей и близких!

🎯 Доступные команды:

/add - Добавить новый день рождения
/list - Показать все дни рождения
/delete - Удалить запись
/edit - Редактировать запись
/check - Проверить уведомления вручную
/cancel - Отменить текущую операцию

💡 Как это работает:
• Добавьте дни рождения командой /add
• Я буду присылать напоминания за 7, 3, 1 день и в день рождения
• Напоминания приходят в 09:00 по МСК
• Используйте /check чтобы проверить уведомления прямо сейчас

Начнем? Используйте /add чтобы добавить первый день рождения!
"""
    update.message.reply_text(welcome_message)
    logger.info(f"Пользователь {user.id} ({user.username}) запустил бота")


def add_start(update: Update, context: CallbackContext) -> int:
    """Начало диалога добавления дня рождения."""
    update.message.reply_text(
        "📝 Добавление нового дня рождения.\n\n"
        "Введите ФИО человека (например: Иванов Иван Иванович):\n\n"
        "Отменить: /cancel"
    )
    return WAITING_NAME


def add_name(update: Update, context: CallbackContext) -> int:
    """Получение ФИО и запрос даты."""
    full_name = update.message.text.strip()
    
    if len(full_name) < 2:
        update.message.reply_text("❌ ФИО слишком короткое. Попробуйте еще раз:")
        return WAITING_NAME
    
    context.user_data['full_name'] = full_name
    update.message.reply_text(
        f"✅ ФИО: {full_name}\n\n"
        "Теперь введите дату рождения в формате ДД.ММ.ГГГГ\n"
        "Например: 15.03.1990\n\n"
        "Отменить: /cancel"
    )
    return WAITING_DATE


def add_date(update: Update, context: CallbackContext) -> int:
    """Получение даты и сохранение в базу данных."""
    date_str = update.message.text.strip()
    full_name = context.user_data.get('full_name')
    user_id = update.effective_user.id
    
    # Валидация формата даты
    try:
        birth_date = datetime.strptime(date_str, '%d.%m.%Y').date()
        
        # Проверка что дата не в будущем
        if birth_date > date.today():
            update.message.reply_text(
                "❌ Дата рождения не может быть в будущем.\n"
                "Введите корректную дату:"
            )
            return WAITING_DATE
        
        # Сохраняем в базу данных в формате YYYY-MM-DD
        db_date = birth_date.strftime('%Y-%m-%d')
        
        if database.add_birthday(user_id, full_name, db_date):
            update.message.reply_text(
                f"✅ Успешно сохранено!\n\n"
                f"👤 {full_name}\n"
                f"🎂 {date_str}\n\n"
                f"Я буду напоминать вам за 7, 3 и 1 день до дня рождения."
            )
            logger.info(f"Пользователь {user_id} добавил: {full_name} - {date_str}")
        else:
            update.message.reply_text("❌ Ошибка при сохранении. Попробуйте позже.")
        
        # Очищаем данные
        context.user_data.clear()
        return ConversationHandler.END
        
    except ValueError:
        update.message.reply_text(
            "❌ Неверный формат даты.\n"
            "Используйте формат ДД.ММ.ГГГГ (например: 15.03.1990)\n"
            "Попробуйте еще раз:"
        )
        return WAITING_DATE


def list_birthdays(update: Update, context: CallbackContext) -> None:
    """Показать все дни рождения, отсортированные по дням от начала года."""
    user_id = update.effective_user.id
    birthdays = database.get_all_birthdays(user_id)
    
    if not birthdays:
        update.message.reply_text(
            "📋 Список пуст.\n\n"
            "Добавьте первую запись командой /add"
        )
        return
    
    # Сортируем по дням до дня рождения
    today = date.today()
    birthdays_with_days = []
    
    for birthday_id, full_name, birth_date in birthdays:
        days_until = scheduler.calculate_days_until_birthday(birth_date)
        birth_date_obj = datetime.strptime(birth_date, '%Y-%m-%d').date()
        birthdays_with_days.append((birthday_id, full_name, birth_date_obj, days_until))
    
    # Сортируем по количеству дней до дня рождения
    birthdays_with_days.sort(key=lambda x: x[3])
    
    # Формируем сообщение
    message = "📋 Ваши дни рождения:\n\n"
    
    for idx, (birthday_id, full_name, birth_date, days_until) in enumerate(birthdays_with_days, 1):
        formatted_date = birth_date.strftime('%d.%m.%Y')
        
        if days_until == 0:
            days_text = "🎉 СЕГОДНЯ!"
        elif days_until == 1:
            days_text = "завтра"
        else:
            days_text = f"через {days_until} дн."
        
        message += f"{idx}. {full_name}\n   🎂 {formatted_date} ({days_text})\n\n"
    
    message += "Управление: /add /delete /edit"
    update.message.reply_text(message)


def delete_start(update: Update, context: CallbackContext) -> int:
    """Начало диалога удаления записи."""
    user_id = update.effective_user.id
    birthdays = database.get_all_birthdays(user_id)
    
    if not birthdays:
        update.message.reply_text("📋 Список пуст. Нечего удалять.")
        return ConversationHandler.END
    
    # Показываем список
    message = "🗑 Удаление записи\n\nВыберите номер записи для удаления:\n\n"
    
    for idx, (birthday_id, full_name, birth_date) in enumerate(birthdays, 1):
        birth_date_obj = datetime.strptime(birth_date, '%Y-%m-%d')
        formatted_date = birth_date_obj.strftime('%d.%m.%Y')
        message += f"{idx}. {full_name} - {formatted_date}\n"
    
    message += "\nВведите номер записи или /cancel для отмены:"
    update.message.reply_text(message)
    
    # Сохраняем список для дальнейшего использования
    context.user_data['birthdays'] = birthdays
    return WAITING_DELETE_ID


def delete_execute(update: Update, context: CallbackContext) -> int:
    """Удаление выбранной записи."""
    try:
        index = int(update.message.text.strip()) - 1
        birthdays = context.user_data.get('birthdays', [])
        
        if 0 <= index < len(birthdays):
            birthday_id, full_name, birth_date = birthdays[index]
            user_id = update.effective_user.id
            
            if database.delete_birthday(birthday_id, user_id):
                update.message.reply_text(f"✅ Удалено: {full_name}")
                logger.info(f"Пользователь {user_id} удалил: {full_name}")
            else:
                update.message.reply_text("❌ Ошибка при удалении.")
        else:
            update.message.reply_text("❌ Неверный номер записи.")
    
    except ValueError:
        update.message.reply_text("❌ Введите число.")
    
    context.user_data.clear()
    return ConversationHandler.END


def edit_start(update: Update, context: CallbackContext) -> int:
    """Начало диалога редактирования записи."""
    user_id = update.effective_user.id
    birthdays = database.get_all_birthdays(user_id)
    
    if not birthdays:
        update.message.reply_text("📋 Список пуст. Нечего редактировать.")
        return ConversationHandler.END
    
    # Показываем список
    message = "✏️ Редактирование записи\n\nВыберите номер записи для редактирования:\n\n"
    
    for idx, (birthday_id, full_name, birth_date) in enumerate(birthdays, 1):
        birth_date_obj = datetime.strptime(birth_date, '%Y-%m-%d')
        formatted_date = birth_date_obj.strftime('%d.%m.%Y')
        message += f"{idx}. {full_name} - {formatted_date}\n"
    
    message += "\nВведите номер записи или /cancel для отмены:"
    update.message.reply_text(message)
    
    context.user_data['birthdays'] = birthdays
    return WAITING_EDIT_ID


def edit_id(update: Update, context: CallbackContext) -> int:
    """Получение ID записи и запрос нового ФИО."""
    try:
        index = int(update.message.text.strip()) - 1
        birthdays = context.user_data.get('birthdays', [])
        
        if 0 <= index < len(birthdays):
            birthday_id, full_name, birth_date = birthdays[index]
            context.user_data['edit_id'] = birthday_id
            context.user_data['old_name'] = full_name
            context.user_data['old_date'] = birth_date
            
            update.message.reply_text(
                f"Текущее ФИО: {full_name}\n\n"
                f"Введите новое ФИО или /cancel для отмены:"
            )
            return WAITING_EDIT_NAME
        else:
            update.message.reply_text("❌ Неверный номер записи.")
            context.user_data.clear()
            return ConversationHandler.END
    
    except ValueError:
        update.message.reply_text("❌ Введите число.")
        context.user_data.clear()
        return ConversationHandler.END


def edit_name(update: Update, context: CallbackContext) -> int:
    """Получение нового ФИО и запрос новой даты."""
    full_name = update.message.text.strip()
    
    if len(full_name) < 2:
        update.message.reply_text("❌ ФИО слишком короткое. Попробуйте еще раз:")
        return WAITING_EDIT_NAME
    
    context.user_data['new_name'] = full_name
    old_date = context.user_data.get('old_date')
    old_date_obj = datetime.strptime(old_date, '%Y-%m-%d')
    formatted_date = old_date_obj.strftime('%d.%m.%Y')
    
    update.message.reply_text(
        f"✅ Новое ФИО: {full_name}\n\n"
        f"Текущая дата: {formatted_date}\n\n"
        f"Введите новую дату в формате ДД.ММ.ГГГГ или /cancel:"
    )
    return WAITING_EDIT_DATE


def edit_date(update: Update, context: CallbackContext) -> int:
    """Получение новой даты и обновление записи."""
    date_str = update.message.text.strip()
    
    try:
        birth_date = datetime.strptime(date_str, '%d.%m.%Y').date()
        
        if birth_date > date.today():
            update.message.reply_text("❌ Дата не может быть в будущем. Попробуйте еще раз:")
            return WAITING_EDIT_DATE
        
        birthday_id = context.user_data.get('edit_id')
        new_name = context.user_data.get('new_name')
        user_id = update.effective_user.id
        db_date = birth_date.strftime('%Y-%m-%d')
        
        if database.update_birthday(birthday_id, user_id, new_name, db_date):
            update.message.reply_text(
                f"✅ Запись обновлена!\n\n"
                f"👤 {new_name}\n"
                f"🎂 {date_str}"
            )
            logger.info(f"Пользователь {user_id} обновил запись {birthday_id}")
        else:
            update.message.reply_text("❌ Ошибка при обновлении.")
        
        context.user_data.clear()
        return ConversationHandler.END
        
    except ValueError:
        update.message.reply_text(
            "❌ Неверный формат даты.\n"
            "Используйте формат ДД.ММ.ГГГГ (например: 15.03.1990):"
        )
        return WAITING_EDIT_DATE


def cancel(update: Update, context: CallbackContext) -> int:
    """Отмена текущей операции."""
    update.message.reply_text("❌ Операция отменена.")
    context.user_data.clear()
    return ConversationHandler.END


def check_notifications(update: Update, context: CallbackContext) -> None:
    """Ручная проверка и отправка уведомлений (для тестирования)."""
    user = update.effective_user
    logger.info(f"Пользователь {user.id} запустил ручную проверку уведомлений")
    
    update.message.reply_text("🔍 Проверяю уведомления...")
    
    # Получаем бота из контекста
    bot = context.bot
    
    # Запускаем проверку
    scheduler.check_and_send_notifications(bot)
    
    update.message.reply_text("✅ Проверка завершена! Уведомления отправлены если есть подходящие даты.")


def main() -> None:
    """Запуск бота."""
    # Получаем токен из переменных окружения
    bot_token = os.getenv('BOT_TOKEN')
    
    if not bot_token:
        logger.error("BOT_TOKEN не найден в переменных окружения!")
        raise ValueError("BOT_TOKEN must be set in environment variables")
    
    logger.info("Инициализация базы данных...")
    database.init_db()
    
    logger.info("Запуск бота...")
    
    # Создаём updater и dispatcher
    updater = Updater(token=bot_token, use_context=True)
    dispatcher = updater.dispatcher
    bot = updater.bot
    
    # Запускаем планировщик уведомлений
    logger.info("Запуск планировщика уведомлений...")
    scheduler.start_scheduler(bot)
    
    # Обработчик команды /start
    dispatcher.add_handler(CommandHandler('start', start))
    
    # Обработчик команды /list
    dispatcher.add_handler(CommandHandler('list', list_birthdays))
    
    # Обработчик команды /check (ручная проверка уведомлений)
    dispatcher.add_handler(CommandHandler('check', check_notifications))
    
    # ConversationHandler для /add
    add_handler = ConversationHandler(
        entry_points=[CommandHandler('add', add_start)],
        states={
            WAITING_NAME: [MessageHandler(Filters.text & ~Filters.command, add_name)],
            WAITING_DATE: [MessageHandler(Filters.text & ~Filters.command, add_date)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    dispatcher.add_handler(add_handler)
    
    # ConversationHandler для /delete
    delete_handler = ConversationHandler(
        entry_points=[CommandHandler('delete', delete_start)],
        states={
            WAITING_DELETE_ID: [MessageHandler(Filters.text & ~Filters.command, delete_execute)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    dispatcher.add_handler(delete_handler)
    
    # ConversationHandler для /edit
    edit_handler = ConversationHandler(
        entry_points=[CommandHandler('edit', edit_start)],
        states={
            WAITING_EDIT_ID: [MessageHandler(Filters.text & ~Filters.command, edit_id)],
            WAITING_EDIT_NAME: [MessageHandler(Filters.text & ~Filters.command, edit_name)],
            WAITING_EDIT_DATE: [MessageHandler(Filters.text & ~Filters.command, edit_date)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    dispatcher.add_handler(edit_handler)
    
    # Запускаем бота
    logger.info("Бот запущен и готов к работе")
    updater.start_polling()
    updater.idle()


if __name__ == '__main__':
    main()
