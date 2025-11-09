import os
import logging
from datetime import datetime, date
from telegram import Update, BotCommand
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
WAITING_NAME, WAITING_EVENT_TYPE, WAITING_EVENT_NAME, WAITING_DATE, WAITING_USERNAME = range(5)
WAITING_DELETE_ID, WAITING_EDIT_ID, WAITING_EDIT_NAME, WAITING_EDIT_DATE, WAITING_EDIT_USERNAME = range(5, 10)
WAITING_EDIT_EVENT_TYPE, WAITING_EDIT_EVENT_NAME = range(10, 12)


def start(update: Update, context: CallbackContext) -> None:
    """Обработчик команды /start."""
    user = update.effective_user
    welcome_message = f"""
👋 Привет, {user.first_name}!

Я бот-напоминалка о важных событиях. Помогу не забыть поздравить друзей и близких!

🎯 Доступные команды:

/add - Добавить новое событие
/list - Показать все события
/delete - Удалить запись
/edit - Редактировать запись
/check - Проверить уведомления вручную
/cancel - Отменить текущую операцию

🎉 Что я умею:
• 🎂 Дни рождения (с расчетом возраста)
• 🎊 Праздники (Новый Год, 8 Марта и т.д.)
• 📅 Другие важные даты (годовщины, события)

💡 Как это работает:
• Добавьте события командой /add
• Я буду присылать напоминания за 7, 3, 1 день и в день события
• Напоминания приходят в 09:00 по МСК
• Используйте /check чтобы проверить уведомления прямо сейчас

Начнем? Используйте /add чтобы добавить первое событие!
"""
    update.message.reply_text(welcome_message)
    logger.info(f"Пользователь {user.id} ({user.username}) запустил бота")


def add_start(update: Update, context: CallbackContext) -> int:
    """Начало диалога добавления события."""
    update.message.reply_text(
        "📝 Добавление нового события.\n\n"
        "Выберите тип события:\n\n"
        "1 - День рождения (с указанием имени и даты рождения)\n"
        "2 - Праздник (например: Новый Год, 8 Марта)\n"
        "3 - Другое событие (годовщина, важная дата)\n\n"
        "Введите номер (1, 2 или 3)\n\n"
        "Отменить: /cancel"
    )
    return WAITING_EVENT_TYPE


def add_event_type(update: Update, context: CallbackContext) -> int:
    """Обработка выбора типа события."""
    choice = update.message.text.strip()
    
    if choice == '1':
        context.user_data['event_type'] = 'birthday'
        update.message.reply_text(
            "🎂 Вы выбрали: День рождения\n\n"
            "Введите имя или ФИО человека (например: Иван Иванов)\n\n"
            "Отменить: /cancel"
        )
        return WAITING_NAME
    elif choice == '2':
        context.user_data['event_type'] = 'holiday'
        update.message.reply_text(
            "🎊 Вы выбрали: Праздник\n\n"
            "Введите название праздника (например: Новый Год, 8 Марта)\n\n"
            "Отменить: /cancel"
        )
        return WAITING_EVENT_NAME
    elif choice == '3':
        context.user_data['event_type'] = 'other'
        update.message.reply_text(
            "📅 Вы выбрали: Другое событие\n\n"
            "Введите название события (например: Годовщина свадьбы)\n\n"
            "Отменить: /cancel"
        )
        return WAITING_EVENT_NAME
    else:
        update.message.reply_text(
            "❌ Пожалуйста, введите 1, 2 или 3.\n\n"
            "1 - День рождения\n"
            "2 - Праздник\n"
            "3 - Другое событие"
        )
        return WAITING_EVENT_TYPE


def add_event_name(update: Update, context: CallbackContext) -> int:
    """Получение названия события для праздников и других событий."""
    event_name = update.message.text.strip()
    
    if len(event_name) < 2:
        update.message.reply_text("❌ Название слишком короткое. Попробуйте еще раз:")
        return WAITING_EVENT_NAME
    
    context.user_data['event_name'] = event_name
    context.user_data['full_name'] = event_name  # Для совместимости с остальными функциями
    
    update.message.reply_text(
        f"✅ Название: {event_name}\n\n"
        "Теперь введите дату:\n"
        "• В формате ДД.ММ (например: 01.01 для Нового Года)\n"
        "• Или ДД.ММ.ГГГГ (если хотите указать конкретный год)\n\n"
        "Отменить: /cancel"
    )
    return WAITING_DATE


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
    """Получение даты и запрос username (только для дня рождения)."""
    date_str = update.message.text.strip()
    event_type = context.user_data.get('event_type', 'birthday')
    
    # Валидация формата даты
    birth_date = None
    formatted_date = date_str
    
    try:
        # Для праздников и других событий поддерживаем формат ДД.ММ
        if event_type in ['holiday', 'other']:
            # Пробуем сначала формат ДД.ММ
            try:
                temp_date = datetime.strptime(date_str, '%d.%m')
                birth_date = date(1900, temp_date.month, temp_date.day)
                formatted_date = date_str  # Сохраняем исходный формат
            except ValueError:
                # Пробуем формат ДД.ММ.ГГГГ
                birth_date = datetime.strptime(date_str, '%d.%m.%Y').date()
                formatted_date = date_str
        else:
            # Для дней рождения только ДД.ММ.ГГГГ
            birth_date = datetime.strptime(date_str, '%d.%m.%Y').date()
            formatted_date = date_str
        
        # Проверка что дата не в будущем (только для дней рождения)
        if event_type == 'birthday' and birth_date > date.today():
            update.message.reply_text(
                "❌ Дата рождения не может быть в будущем.\n"
                "Введите корректную дату:"
            )
            return WAITING_DATE
        
        # Сохраняем дату в формате YYYY-MM-DD
        context.user_data['birth_date'] = birth_date.strftime('%Y-%m-%d')
        context.user_data['formatted_date'] = formatted_date
        
        # Для дня рождения спрашиваем username, для остальных - сохраняем сразу
        if event_type == 'birthday':
            update.message.reply_text(
                f"✅ Дата: {date_str}\n\n"
                "Теперь введите Telegram username этого человека (например: @ivan или ivan)\n\n"
                "Это поможет быстро найти контакт в уведомлениях.\n"
                "Если username нет, напишите: нет\n\n"
                "Отменить: /cancel"
            )
            return WAITING_USERNAME
        else:
            # Для праздников и других событий сохраняем сразу
            full_name = context.user_data.get('full_name')
            event_name = context.user_data.get('event_name')
            user_id = update.effective_user.id
            
            if database.add_birthday(user_id, full_name, birth_date.strftime('%Y-%m-%d'), 
                                    None, event_type, event_name):
                event_emoji = "🎊" if event_type == 'holiday' else "📅"
                update.message.reply_text(
                    f"✅ Успешно сохранено!\n\n"
                    f"{event_emoji} {event_name}\n"
                    f"📅 {date_str}\n\n"
                    f"Я буду напоминать вам за 7, 3 и 1 день до события."
                )
                logger.info(f"Пользователь {user_id} добавил событие: {event_name} ({event_type}) - {date_str}")
            else:
                update.message.reply_text("❌ Ошибка при сохранении. Попробуйте позже.")
            
            # Очищаем данные
            context.user_data.clear()
            return ConversationHandler.END
        
    except ValueError:
        if event_type in ['holiday', 'other']:
            update.message.reply_text(
                "❌ Неверный формат даты.\n"
                "Используйте формат ДД.ММ (например: 01.01) или ДД.ММ.ГГГГ\n"
                "Попробуйте еще раз:"
            )
        else:
            update.message.reply_text(
                "❌ Неверный формат даты.\n"
                "Используйте формат ДД.ММ.ГГГГ (например: 15.03.1990)\n"
                "Попробуйте еще раз:"
            )
        return WAITING_DATE


def add_username(update: Update, context: CallbackContext) -> int:
    """Получение username и сохранение в базу данных (только для дней рождения)."""
    username_input = update.message.text.strip()
    full_name = context.user_data.get('full_name')
    birth_date = context.user_data.get('birth_date')
    formatted_date = context.user_data.get('formatted_date')
    event_type = context.user_data.get('event_type', 'birthday')
    event_name = context.user_data.get('event_name')
    user_id = update.effective_user.id
    
    # Обработка username
    telegram_username = None
    if username_input.lower() not in ['нет', 'no', 'skip', '-']:
        # Убираем @ если есть
        username_clean = username_input.lstrip('@')
        
        # Валидация username (только буквы, цифры и подчеркивание)
        if username_clean and username_clean.replace('_', '').isalnum():
            telegram_username = username_clean
        else:
            update.message.reply_text(
                "❌ Неверный формат username.\n"
                "Username может содержать только буквы, цифры и подчеркивание.\n"
                "Попробуйте еще раз или напишите 'нет':"
            )
            return WAITING_USERNAME
    
    # Сохраняем в базу данных
    if database.add_birthday(user_id, full_name, birth_date, telegram_username, event_type, event_name):
        username_text = f" (@{telegram_username})" if telegram_username else ""
        update.message.reply_text(
            f"✅ Успешно сохранено!\n\n"
            f"👤 {full_name}{username_text}\n"
            f"🎂 {formatted_date}\n\n"
            f"Я буду напоминать вам за 7, 3 и 1 день до дня рождения."
        )
        logger.info(f"Пользователь {user_id} добавил: {full_name}{username_text} - {formatted_date}")
    else:
        update.message.reply_text("❌ Ошибка при сохранении. Попробуйте позже.")
    
    # Очищаем данные
    context.user_data.clear()
    return ConversationHandler.END


def list_birthdays(update: Update, context: CallbackContext) -> None:
    """Показать все события, отсортированные по дням до наступления."""
    user_id = update.effective_user.id
    birthdays = database.get_all_birthdays(user_id)
    
    if not birthdays:
        update.message.reply_text(
            "📋 Список пуст.\n\n"
            "Добавьте первую запись командой /add"
        )
        return
    
    # Сортируем по дням до события
    today = date.today()
    birthdays_with_days = []
    
    for birthday_id, full_name, birth_date, telegram_username, event_type, event_name in birthdays:
        days_until = scheduler.calculate_days_until_birthday(birth_date)
        birth_date_obj = datetime.strptime(birth_date, '%Y-%m-%d').date()
        
        # Вычисляем возраст для дней рождения
        age = None
        if event_type == 'birthday' and birth_date_obj.year != 1900:
            next_birthday_year = today.year if birth_date_obj.replace(year=today.year) >= today else today.year + 1
            age = next_birthday_year - birth_date_obj.year
        
        birthdays_with_days.append((
            birthday_id, full_name, birth_date_obj, telegram_username, 
            days_until, event_type, event_name, age
        ))
    
    # Сортируем по количеству дней до события
    birthdays_with_days.sort(key=lambda x: x[4])
    
    # Формируем сообщение
    message = "📋 Ваши события:\n\n"
    
    for idx, (birthday_id, full_name, birth_date, telegram_username, 
              days_until, event_type, event_name, age) in enumerate(birthdays_with_days, 1):
        
        # Выбираем эмодзи в зависимости от типа события
        if event_type == 'holiday':
            emoji = "🎊"
            name_display = event_name if event_name else full_name
        elif event_type == 'other':
            emoji = "📅"
            name_display = event_name if event_name else full_name
        else:  # birthday
            emoji = "🎂"
            name_display = full_name
            if telegram_username:
                name_display += f" (@{telegram_username})"
        
        # Форматируем дату
        if event_type == 'birthday':
            formatted_date = birth_date.strftime('%d.%m.%Y')
        else:
            formatted_date = birth_date.strftime('%d.%m')
        
        # Текст о днях до события
        if days_until == 0:
            days_text = "🎉 СЕГОДНЯ!"
        elif days_until == 1:
            days_text = "завтра"
        else:
            days_text = f"через {days_until} дн."
        
        # Добавляем информацию о возрасте для дней рождения
        age_text = ""
        if age and event_type == 'birthday':
            age_text = f", исполнится {age} лет"
        
        message += f"{idx}. {emoji} {name_display}\n   📅 {formatted_date} ({days_text}{age_text})\n\n"
    
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
    
    for idx, (birthday_id, full_name, birth_date, telegram_username, event_type, event_name) in enumerate(birthdays, 1):
        birth_date_obj = datetime.strptime(birth_date, '%Y-%m-%d')
        
        # Выбираем эмодзи и формат даты в зависимости от типа события
        if event_type == 'holiday':
            emoji = "🎊"
            formatted_date = birth_date_obj.strftime('%d.%m')
            display_name = event_name if event_name else full_name
        elif event_type == 'other':
            emoji = "📅"
            formatted_date = birth_date_obj.strftime('%d.%m')
            display_name = event_name if event_name else full_name
        else:  # birthday
            emoji = "🎂"
            formatted_date = birth_date_obj.strftime('%d.%m.%Y')
            display_name = full_name
            if telegram_username:
                display_name += f" (@{telegram_username})"
        
        message += f"{idx}. {emoji} {display_name} - {formatted_date}\n"
    
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
            birthday_id, full_name, birth_date, telegram_username, event_type, event_name = birthdays[index]
            user_id = update.effective_user.id
            
            # Определяем что именно удаляем для отображения
            display_name = event_name if (event_type in ['holiday', 'other'] and event_name) else full_name
            
            if database.delete_birthday(birthday_id, user_id):
                update.message.reply_text(f"✅ Удалено: {display_name}")
                logger.info(f"Пользователь {user_id} удалил: {display_name} [{event_type}]")
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
    
    for idx, (birthday_id, full_name, birth_date, telegram_username, event_type, event_name) in enumerate(birthdays, 1):
        birth_date_obj = datetime.strptime(birth_date, '%Y-%m-%d')
        
        # Выбираем эмодзи и формат даты в зависимости от типа события
        if event_type == 'holiday':
            emoji = "🎊"
            formatted_date = birth_date_obj.strftime('%d.%m')
            display_name = event_name if event_name else full_name
        elif event_type == 'other':
            emoji = "📅"
            formatted_date = birth_date_obj.strftime('%d.%m')
            display_name = event_name if event_name else full_name
        else:  # birthday
            emoji = "🎂"
            formatted_date = birth_date_obj.strftime('%d.%m.%Y')
            display_name = full_name
            if telegram_username:
                display_name += f" (@{telegram_username})"
        
        message += f"{idx}. {emoji} {display_name} - {formatted_date}\n"
    
    message += "\nВведите номер записи или /cancel для отмены:"
    update.message.reply_text(message)
    
    context.user_data['birthdays'] = birthdays
    return WAITING_EDIT_ID


def edit_id(update: Update, context: CallbackContext) -> int:
    """Получение ID записи и запрос нового имени/названия."""
    try:
        index = int(update.message.text.strip()) - 1
        birthdays = context.user_data.get('birthdays', [])
        
        if 0 <= index < len(birthdays):
            birthday_id, full_name, birth_date, telegram_username, event_type, event_name = birthdays[index]
            context.user_data['edit_id'] = birthday_id
            context.user_data['old_name'] = full_name
            context.user_data['old_date'] = birth_date
            context.user_data['old_username'] = telegram_username
            context.user_data['old_event_type'] = event_type if event_type else 'birthday'
            context.user_data['old_event_name'] = event_name
            
            # Определяем что редактируем в зависимости от типа события
            if event_type in ['holiday', 'other']:
                display_name = event_name if event_name else full_name
                prompt = f"Текущее название: {display_name}\n\nВведите новое название или /cancel для отмены:"
            else:
                prompt = f"Текущее ФИО: {full_name}\n\nВведите новое ФИО или /cancel для отмены:"
            
            update.message.reply_text(prompt)
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
    """Получение нового имени/названия и запрос новой даты."""
    new_name_input = update.message.text.strip()
    
    if len(new_name_input) < 2:
        update.message.reply_text("❌ Название слишком короткое. Попробуйте еще раз:")
        return WAITING_EDIT_NAME
    
    event_type = context.user_data.get('old_event_type', 'birthday')
    
    # Для праздников и других событий сохраняем как event_name
    if event_type in ['holiday', 'other']:
        context.user_data['new_event_name'] = new_name_input
        context.user_data['new_name'] = new_name_input  # Для совместимости
    else:
        context.user_data['new_name'] = new_name_input
        context.user_data['new_event_name'] = None
    
    old_date = context.user_data.get('old_date')
    old_date_obj = datetime.strptime(old_date, '%Y-%m-%d')
    
    # Формат даты зависит от типа события
    if event_type in ['holiday', 'other']:
        formatted_date = old_date_obj.strftime('%d.%m')
    else:
        formatted_date = old_date_obj.strftime('%d.%m.%Y')
    
    # Подсказка зависит от типа события
    if event_type in ['holiday', 'other']:
        date_hint = "Введите новую дату в формате ДД.ММ или ДД.ММ.ГГГГ"
    else:
        date_hint = "Введите новую дату в формате ДД.ММ.ГГГГ"
    
    update.message.reply_text(
        f"✅ Новое название: {new_name_input}\n\n"
        f"Текущая дата: {formatted_date}\n\n"
        f"{date_hint} или /cancel:"
    )
    return WAITING_EDIT_DATE


def edit_date(update: Update, context: CallbackContext) -> int:
    """Получение новой даты и запрос username (только для дней рождения)."""
    date_str = update.message.text.strip()
    event_type = context.user_data.get('old_event_type', 'birthday')
    
    # Валидация формата даты
    birth_date = None
    formatted_date = date_str
    
    try:
        # Для праздников и других событий поддерживаем формат ДД.ММ
        if event_type in ['holiday', 'other']:
            # Пробуем сначала формат ДД.ММ
            try:
                temp_date = datetime.strptime(date_str, '%d.%m')
                birth_date = date(1900, temp_date.month, temp_date.day)
                formatted_date = date_str
            except ValueError:
                # Пробуем формат ДД.ММ.ГГГГ
                birth_date = datetime.strptime(date_str, '%d.%m.%Y').date()
                formatted_date = date_str
        else:
            # Для дней рождения только ДД.ММ.ГГГГ
            birth_date = datetime.strptime(date_str, '%d.%m.%Y').date()
            formatted_date = date_str
        
        # Проверка что дата не в будущем (только для дней рождения)
        if event_type == 'birthday' and birth_date > date.today():
            update.message.reply_text("❌ Дата рождения не может быть в будущем. Попробуйте еще раз:")
            return WAITING_EDIT_DATE
        
        context.user_data['new_date'] = birth_date.strftime('%Y-%m-%d')
        context.user_data['formatted_date'] = formatted_date
        
        # Для дня рождения спрашиваем username, для остальных - сохраняем сразу
        if event_type == 'birthday':
            old_username = context.user_data.get('old_username')
            username_info = f" (@{old_username})" if old_username else " (нет)"
            
            update.message.reply_text(
                f"✅ Дата: {date_str}\n\n"
                f"Текущий username:{username_info}\n\n"
                f"Введите новый Telegram username (например: @ivan или ivan)\n"
                f"Если username нет или хотите оставить текущий, напишите: нет\n\n"
                f"Отменить: /cancel"
            )
            return WAITING_EDIT_USERNAME
        else:
            # Для праздников и других событий сохраняем сразу
            birthday_id = context.user_data.get('edit_id')
            new_name = context.user_data.get('new_name')
            new_event_name = context.user_data.get('new_event_name')
            user_id = update.effective_user.id
            
            if database.update_birthday(birthday_id, user_id, new_name, birth_date.strftime('%Y-%m-%d'), 
                                       None, event_type, new_event_name):
                event_emoji = "🎊" if event_type == 'holiday' else "📅"
                update.message.reply_text(
                    f"✅ Запись обновлена!\n\n"
                    f"{event_emoji} {new_event_name}\n"
                    f"📅 {date_str}"
                )
                logger.info(f"Пользователь {user_id} обновил событие {birthday_id} [{event_type}]")
            else:
                update.message.reply_text("❌ Ошибка при обновлении.")
            
            context.user_data.clear()
            return ConversationHandler.END
        
    except ValueError:
        if event_type in ['holiday', 'other']:
            update.message.reply_text(
                "❌ Неверный формат даты.\n"
                "Используйте формат ДД.ММ (например: 01.01) или ДД.ММ.ГГГГ:"
            )
        else:
            update.message.reply_text(
                "❌ Неверный формат даты.\n"
                "Используйте формат ДД.ММ.ГГГГ (например: 15.03.1990):"
            )
        return WAITING_EDIT_DATE


def edit_username(update: Update, context: CallbackContext) -> int:
    """Получение username и обновление записи (только для дней рождения)."""
    username_input = update.message.text.strip()
    birthday_id = context.user_data.get('edit_id')
    new_name = context.user_data.get('new_name')
    new_date = context.user_data.get('new_date')
    formatted_date = context.user_data.get('formatted_date')
    old_username = context.user_data.get('old_username')
    event_type = context.user_data.get('old_event_type', 'birthday')
    new_event_name = context.user_data.get('new_event_name')
    user_id = update.effective_user.id
    
    # Обработка username
    telegram_username = old_username  # По умолчанию оставляем старый
    if username_input.lower() not in ['нет', 'no', 'skip', '-']:
        # Убираем @ если есть
        username_clean = username_input.lstrip('@')
        
        # Валидация username
        if username_clean and username_clean.replace('_', '').isalnum():
            telegram_username = username_clean
        else:
            update.message.reply_text(
                "❌ Неверный формат username.\n"
                "Username может содержать только буквы, цифры и подчеркивание.\n"
                "Попробуйте еще раз или напишите 'нет':"
            )
            return WAITING_EDIT_USERNAME
    
    # Обновляем запись
    if database.update_birthday(birthday_id, user_id, new_name, new_date, telegram_username, event_type, new_event_name):
        username_text = f" (@{telegram_username})" if telegram_username else ""
        update.message.reply_text(
            f"✅ Запись обновлена!\n\n"
            f"👤 {new_name}{username_text}\n"
            f"🎂 {formatted_date}"
        )
        logger.info(f"Пользователь {user_id} обновил запись {birthday_id}")
    else:
        update.message.reply_text("❌ Ошибка при обновлении.")
    
    context.user_data.clear()
    return ConversationHandler.END


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


def setup_commands(bot):
    """Установить меню команд бота."""
    commands = [
        BotCommand("start", "Начать работу с ботом"),
        BotCommand("add", "Добавить новое событие"),
        BotCommand("list", "Показать все события"),
        BotCommand("delete", "Удалить событие"),
        BotCommand("edit", "Редактировать событие"),
        BotCommand("check", "Проверить уведомления вручную"),
        BotCommand("cancel", "Отменить текущую операцию"),
    ]
    bot.set_my_commands(commands)
    logger.info("Меню команд установлено")


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
    
    # Устанавливаем меню команд
    setup_commands(bot)
    
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
            WAITING_EVENT_TYPE: [MessageHandler(Filters.text & ~Filters.command, add_event_type)],
            WAITING_EVENT_NAME: [MessageHandler(Filters.text & ~Filters.command, add_event_name)],
            WAITING_NAME: [MessageHandler(Filters.text & ~Filters.command, add_name)],
            WAITING_DATE: [MessageHandler(Filters.text & ~Filters.command, add_date)],
            WAITING_USERNAME: [MessageHandler(Filters.text & ~Filters.command, add_username)],
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
            WAITING_EDIT_USERNAME: [MessageHandler(Filters.text & ~Filters.command, edit_username)],
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
