import os
import logging
import re
from datetime import datetime, date
from uuid import uuid4
from telegram import Update, BotCommand, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram import InlineQueryResultArticle, InputTextMessageContent
from telegram.error import Conflict
from telegram.ext import (
    Updater, 
    CommandHandler, 
    MessageHandler, 
    Filters, 
    CallbackContext,
    ConversationHandler,
    InlineQueryHandler
)
from dotenv import load_dotenv
import database
import scheduler
from scheduler import years_word

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
WAITING_IMPORT_TEXT, WAITING_IMPORT_CONFIRMATION = range(100, 102)


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


def parse_bulk_import(text: str):
    """
    Парсинг текста для массового импорта событий.
    
    Формат ожидаемого текста:
    1. 🎂 Имя (@username)
       📅 ДД.ММ.ГГГГ (дополнительный текст)
    
    Returns:
        tuple: (parsed_events, errors)
            - parsed_events: список кортежей (full_name, date_str, username, event_type, event_name)
            - errors: список строк с ошибками парсинга
    """
    parsed_events = []
    errors = []
    
    # Разбиваем текст на блоки по записям (по номерам)
    # Паттерн: номер с точкой, эмодзи, имя, опционально username, перевод строки, дата
    pattern = r'^\s*\d+\.\s*(🎂|🎊|📅)\s*(.+?)(?:\s*\((@[^)]+)\))?\s*\n\s*📅\s*(\d{2}\.\d{2}(?:\.\d{4})?)'
    
    lines = text.split('\n')
    i = 0
    
    while i < len(lines):
        line = lines[i].strip()
        
        # Ищем начало записи (номер с точкой и эмодзи)
        if re.match(r'^\d+\.\s*[🎂🎊📅]', line):
            # Собираем текущую запись и следующую строку (с датой)
            current_block = line
            if i + 1 < len(lines):
                current_block += '\n' + lines[i + 1]
                i += 1  # Пропускаем следующую строку, т.к. уже обработали
            
            # Пытаемся распарсить блок
            match = re.search(pattern, current_block, re.MULTILINE)
            
            if match:
                emoji = match.group(1)
                name_part = match.group(2).strip()
                username = match.group(3).strip() if match.group(3) else None
                date_str = match.group(4).strip()
                
                # Убираем @ из username если есть
                if username and username.startswith('@'):
                    username = username[1:]
                
                # Определяем тип события по эмодзи
                if emoji == '🎂':
                    event_type = 'birthday'
                    event_name = None
                    full_name = name_part
                elif emoji == '🎊':
                    event_type = 'holiday'
                    event_name = name_part
                    full_name = name_part
                elif emoji == '📅':
                    event_type = 'other'
                    event_name = name_part
                    full_name = name_part
                else:
                    event_type = 'birthday'
                    event_name = None
                    full_name = name_part
                
                # Проверяем и нормализуем дату
                try:
                    if len(date_str.split('.')) == 3:
                        # Формат ДД.ММ.ГГГГ
                        day, month, year = date_str.split('.')
                        parsed_date = datetime.strptime(date_str, '%d.%m.%Y').date()
                        normalized_date = parsed_date.strftime('%Y-%m-%d')
                    else:
                        # Формат ДД.ММ (для праздников/других событий)
                        day, month = date_str.split('.')
                        # Используем год 1900 для обозначения что год не указан
                        normalized_date = f"1900-{month.zfill(2)}-{day.zfill(2)}"
                        # Проверяем валидность даты
                        datetime.strptime(normalized_date, '%Y-%m-%d')
                    
                    parsed_events.append((full_name, normalized_date, username, event_type, event_name))
                    logger.info(f"Распарсено: {full_name}, {normalized_date}, @{username}, {event_type}")
                    
                except ValueError as e:
                    error_msg = f"Неверный формат даты '{date_str}' для записи: {name_part}"
                    errors.append(error_msg)
                    logger.warning(error_msg)
            else:
                error_msg = f"Не удалось распарсить запись: {current_block[:50]}..."
                errors.append(error_msg)
                logger.warning(error_msg)
        
        i += 1
    
    return parsed_events, errors


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
            # Создаем кнопку для пропуска
            keyboard = [
                [KeyboardButton("⏭ Пропустить")]
            ]
            reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
            
            update.message.reply_text(
                f"✅ Дата: {date_str}\n\n"
                "Добавьте Telegram контакт:\n\n"
                "📱 Нажмите 📎 (скрепка внизу) → Контакт → Выберите человека\n\n"
                "⏭ Или нажмите 'Пропустить'\n\n"
                "Отменить: /cancel",
                reply_markup=reply_markup
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
    full_name = context.user_data.get('full_name')
    birth_date = context.user_data.get('birth_date')
    formatted_date = context.user_data.get('formatted_date')
    event_type = context.user_data.get('event_type', 'birthday')
    event_name = context.user_data.get('event_name')
    user_id = update.effective_user.id
    bot = context.bot
    
    telegram_username = None
    
    # Обработка контакта (когда пользователь выбрал контакт из телефона)
    if update.message.contact:
        contact = update.message.contact
        logger.info(f"Получен контакт: {contact.first_name} {contact.last_name}, user_id: {contact.user_id}")
        
        # Пытаемся получить username через user_id
        if contact.user_id:
            try:
                chat = bot.get_chat(contact.user_id)
                telegram_username = chat.username
                logger.info(f"Username получен из контакта: @{telegram_username}")
            except Exception as e:
                logger.warning(f"Не удалось получить username из контакта: {e}")
                telegram_username = None
    
    # Обработка кнопки "Пропустить"
    elif update.message.text and update.message.text.strip() == "⏭ Пропустить":
        telegram_username = None
    
    # Если пришло что-то другое (не контакт и не кнопка) - объясняем что делать
    else:
        keyboard = [
            [KeyboardButton("⏭ Пропустить")]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
        
        update.message.reply_text(
            "❌ Пожалуйста, отправьте контакт или нажмите 'Пропустить'\n\n"
            "Чтобы отправить контакт:\n"
            "Нажмите 📎 → Контакт → Выберите человека",
            reply_markup=reply_markup
        )
        return WAITING_USERNAME
    
    # Сохраняем в базу данных
    if database.add_birthday(user_id, full_name, birth_date, telegram_username, event_type, event_name):
        username_text = f" (@{telegram_username})" if telegram_username else ""
        update.message.reply_text(
            f"✅ Успешно сохранено!\n\n"
            f"👤 {full_name}{username_text}\n"
            f"🎂 {formatted_date}\n\n"
            f"Я буду напоминать вам за 7, 3 и 1 день до дня рождения.",
            reply_markup=ReplyKeyboardRemove()  # Убираем клавиатуру
        )
        logger.info(f"Пользователь {user_id} добавил: {full_name}{username_text} - {formatted_date}")
    else:
        update.message.reply_text(
            "❌ Ошибка при сохранении. Попробуйте позже.",
            reply_markup=ReplyKeyboardRemove()
        )
    
    # Очищаем данные
    context.user_data.clear()
    return ConversationHandler.END


def list_birthdays(update: Update, context: CallbackContext) -> None:
    """Показать все события, отсортированные по дням до наступления."""
    user_id = update.effective_user.id
    logger.info(f"Команда /list от пользователя {user_id}")
    birthdays = database.get_all_birthdays(user_id)
    logger.info(f"Получено записей из БД: {len(birthdays)}")
    
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
        if age is not None and event_type == 'birthday':
            age_text = f", исполнится {age} {years_word(age)}"
        
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
            
            # Создаем кнопку для пропуска
            keyboard = [
                [KeyboardButton("⏭ Пропустить")]
            ]
            reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
            
            update.message.reply_text(
                f"✅ Дата: {date_str}\n\n"
                f"Текущий username:{username_info}\n\n"
                f"Обновите контакт:\n\n"
                f"📱 Отправить контакт:\n"
                f"   Нажмите 📎 → Контакт → Выберите человека из списка\n\n"
                f"⏭ Или нажмите 'Пропустить' (оставить текущий)\n\n"
                f"Отменить: /cancel",
                reply_markup=reply_markup
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
    birthday_id = context.user_data.get('edit_id')
    new_name = context.user_data.get('new_name')
    new_date = context.user_data.get('new_date')
    formatted_date = context.user_data.get('formatted_date')
    old_username = context.user_data.get('old_username')
    event_type = context.user_data.get('old_event_type', 'birthday')
    new_event_name = context.user_data.get('new_event_name')
    user_id = update.effective_user.id
    bot = context.bot
    
    telegram_username = old_username  # По умолчанию оставляем старый
    
    # Обработка контакта (когда пользователь выбрал контакт из телефона)
    if update.message.contact:
        contact = update.message.contact
        logger.info(f"Получен контакт при редактировании: {contact.first_name} {contact.last_name}, user_id: {contact.user_id}")
        
        # Пытаемся получить username через user_id
        if contact.user_id:
            try:
                chat = bot.get_chat(contact.user_id)
                telegram_username = chat.username
                logger.info(f"Username получен из контакта: @{telegram_username}")
            except Exception as e:
                logger.warning(f"Не удалось получить username из контакта: {e}")
                telegram_username = None
    
    # Обработка кнопки "Пропустить" (оставляет старый username)
    elif update.message.text and update.message.text.strip() == "⏭ Пропустить":
        pass  # telegram_username уже установлен = old_username
    
    # Если пришло что-то другое (не контакт и не кнопка) - объясняем что делать
    else:
        keyboard = [
            [KeyboardButton("⏭ Пропустить")]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
        
        update.message.reply_text(
            "❌ Пожалуйста, отправьте контакт или нажмите 'Пропустить'\n\n"
            "Чтобы отправить контакт:\n"
            "Нажмите 📎 → Контакт → Выберите человека",
            reply_markup=reply_markup
        )
        return WAITING_EDIT_USERNAME
    
    # Обновляем запись
    if database.update_birthday(birthday_id, user_id, new_name, new_date, telegram_username, event_type, new_event_name):
        username_text = f" (@{telegram_username})" if telegram_username else ""
        update.message.reply_text(
            f"✅ Запись обновлена!\n\n"
            f"👤 {new_name}{username_text}\n"
            f"🎂 {formatted_date}",
            reply_markup=ReplyKeyboardRemove()  # Убираем клавиатуру
        )
        logger.info(f"Пользователь {user_id} обновил запись {birthday_id}")
    else:
        update.message.reply_text(
            "❌ Ошибка при обновлении.",
            reply_markup=ReplyKeyboardRemove()
        )
    
    context.user_data.clear()
    return ConversationHandler.END


def parse_bulk_import(text: str):
    """
    Парсинг списка событий из форматированного текста.
    
    Формат:
    1. 🎂 Имя (@username)
       📅 ДД.ММ.ГГГГ (описание...)
    
    Returns:
        Tuple[List, List]: (успешно распарсенные записи, ошибки)
        Запись: (full_name, date_str, username, event_type, event_name)
    """
    records = []
    errors = []
    
    # Паттерн для парсинга каждой записи
    # Ищем строки вида: "N. 🎂 Имя (@username)" и следующую строку "📅 дата"
    pattern = r'^\d+\.\s*(🎂|🎊|📅)\s*(.+?)(?:\s*\(@([^)]+)\))?\s*$\s*^\s*📅\s*(\d{2}\.\d{2}(?:\.\d{4})?)'
    
    # Разбиваем текст на строки для обработки
    lines = text.split('\n')
    i = 0
    
    while i < len(lines):
        line = lines[i].strip()
        
        # Проверяем, начинается ли строка с номера и эмодзи
        if re.match(r'^\d+\.\s*(🎂|🎊|📅)', line):
            # Извлекаем эмодзи
            emoji_match = re.search(r'(🎂|🎊|📅)', line)
            if not emoji_match:
                i += 1
                continue
            
            emoji = emoji_match.group(1)
            
            # Определяем тип события
            if emoji == '🎂':
                event_type = 'birthday'
            elif emoji == '🎊':
                event_type = 'holiday'
            else:  # 📅
                event_type = 'other'
            
            # Извлекаем имя и username
            name_part = re.sub(r'^\d+\.\s*(🎂|🎊|📅)\s*', '', line)
            
            # Проверяем наличие @username
            username_match = re.search(r'\(@([^)]+)\)', name_part)
            username = username_match.group(1) if username_match else None
            
            # Удаляем @username из имени
            full_name = re.sub(r'\s*\(@[^)]+\)', '', name_part).strip()
            
            # Следующая строка должна содержать дату
            if i + 1 < len(lines):
                date_line = lines[i + 1].strip()
                date_match = re.search(r'📅\s*(\d{2}\.\d{2}(?:\.\d{4})?)', date_line)
                
                if date_match:
                    date_str = date_match.group(1)
                    
                    # Конвертируем дату в формат YYYY-MM-DD
                    try:
                        if len(date_str) == 10:  # ДД.ММ.ГГГГ
                            date_obj = datetime.strptime(date_str, '%d.%m.%Y')
                        else:  # ДД.ММ
                            # Для праздников и других событий используем год 1900
                            date_obj = datetime.strptime(date_str + '.1900', '%d.%m.%Y')
                        
                        db_date = date_obj.strftime('%Y-%m-%d')
                        
                        # Для праздников и других событий event_name = full_name
                        event_name = full_name if event_type in ['holiday', 'other'] else None
                        
                        records.append((full_name, db_date, username, event_type, event_name))
                        logger.info(f"Распарсена запись: {full_name} - {db_date} [{event_type}]")
                    except Exception as e:
                        errors.append(f"Ошибка парсинга даты для '{full_name}': {e}")
                        logger.warning(f"Ошибка парсинга даты: {date_str} - {e}")
                else:
                    errors.append(f"Не найдена дата для '{full_name}'")
                
                i += 2  # Пропускаем обе строки (имя и дата)
            else:
                errors.append(f"Не найдена строка с датой для '{full_name}'")
                i += 1
        else:
            i += 1
    
    return records, errors


def cancel(update: Update, context: CallbackContext) -> int:
    """Отмена текущей операции."""
    update.message.reply_text("❌ Операция отменена.", reply_markup=ReplyKeyboardRemove())
    context.user_data.clear()
    return ConversationHandler.END


def import_start(update: Update, context: CallbackContext) -> int:
    """Начало диалога массового импорта событий."""
    update.message.reply_text(
        "📥 Массовый импорт событий\n\n"
        "Отправьте список событий в следующем формате:\n\n"
        "1. 🎂 Имя Фамилия (@username)\n"
        "   📅 ДД.ММ.ГГГГ (дополнительная информация)\n\n"
        "2. 🎊 Название праздника\n"
        "   📅 ДД.ММ\n\n"
        "3. 📅 Другое событие\n"
        "   📅 ДД.ММ\n\n"
        "💡 Примеры:\n\n"
        "1. 🎂 Иван Петров (@ivan_petrov)\n"
        "   📅 15.03.1990 (через 125 дн., исполнится 35 лет)\n\n"
        "2. 🎊 Новый Год\n"
        "   📅 01.01\n\n"
        "Просто скопируйте и отправьте весь список одним сообщением.\n\n"
        "Отменить: /cancel"
    )
    return WAITING_IMPORT_TEXT


def import_receive_text(update: Update, context: CallbackContext) -> int:
    """Получение текста для импорта и показ превью."""
    text = update.message.text
    user_id = update.effective_user.id
    
    logger.info(f"Пользователь {user_id} отправил текст для импорта, длина: {len(text)}")
    
    # Парсим текст
    parsed_events, errors = parse_bulk_import(text)
    
    if not parsed_events and not errors:
        update.message.reply_text(
            "❌ Не удалось найти ни одной записи в нужном формате.\n\n"
            "Проверьте формат и попробуйте снова, или используйте /cancel для отмены."
        )
        return WAITING_IMPORT_TEXT
    
    # Сохраняем в context для следующего шага
    context.user_data['import_candidates'] = parsed_events
    context.user_data['import_errors'] = errors
    
    # Формируем превью
    message = f"📋 Найдено записей: {len(parsed_events)}\n\n"
    
    for idx, (full_name, date_str, username, event_type, event_name) in enumerate(parsed_events, 1):
        # Форматируем дату для отображения
        date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
        if date_obj.year == 1900:
            formatted_date = date_obj.strftime('%d.%m')
        else:
            formatted_date = date_obj.strftime('%d.%m.%Y')
        
        # Выбираем эмодзи
        if event_type == 'birthday':
            emoji = '🎂'
            display_name = full_name
        elif event_type == 'holiday':
            emoji = '🎊'
            display_name = event_name if event_name else full_name
        else:
            emoji = '📅'
            display_name = event_name if event_name else full_name
        
        username_text = f" (@{username})" if username else ""
        message += f"{idx}. {emoji} {display_name}{username_text} - {formatted_date}\n"
    
    if errors:
        message += f"\n⚠️ Не удалось распарсить: {len(errors)} записей\n"
        if len(errors) <= 3:
            for error in errors:
                message += f"  • {error}\n"
    
    message += "\n❓ Подтвердить импорт этих записей?"
    
    # Создаем кнопки подтверждения
    keyboard = [
        [KeyboardButton("✅ Подтвердить"), KeyboardButton("❌ Отменить")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    
    update.message.reply_text(message, reply_markup=reply_markup)
    
    return WAITING_IMPORT_CONFIRMATION


def import_confirm(update: Update, context: CallbackContext) -> int:
    """Подтверждение и сохранение импортированных записей."""
    user_id = update.effective_user.id
    choice = update.message.text.strip()
    
    if choice == "❌ Отменить":
        update.message.reply_text(
            "❌ Импорт отменен.",
            reply_markup=ReplyKeyboardRemove()
        )
        context.user_data.clear()
        return ConversationHandler.END
    
    if choice != "✅ Подтвердить":
        update.message.reply_text(
            "Пожалуйста, нажмите одну из кнопок: '✅ Подтвердить' или '❌ Отменить'"
        )
        return WAITING_IMPORT_CONFIRMATION
    
    # Получаем данные из context
    import_candidates = context.user_data.get('import_candidates', [])
    
    if not import_candidates:
        update.message.reply_text(
            "❌ Нет данных для импорта.",
            reply_markup=ReplyKeyboardRemove()
        )
        context.user_data.clear()
        return ConversationHandler.END
    
    # Импортируем записи
    success_count = 0
    failed_count = 0
    
    for full_name, date_str, username, event_type, event_name in import_candidates:
        try:
            if database.add_birthday(user_id, full_name, date_str, username, event_type, event_name):
                success_count += 1
            else:
                failed_count += 1
        except Exception as e:
            logger.error(f"Ошибка при импорте записи {full_name}: {e}")
            failed_count += 1
    
    # Показываем результат
    result_message = f"✅ Импорт завершен!\n\n"
    result_message += f"📊 Успешно добавлено: {success_count}\n"
    if failed_count > 0:
        result_message += f"❌ Ошибок: {failed_count}\n"
    result_message += f"\nИспользуйте /list чтобы посмотреть все события."
    
    update.message.reply_text(result_message, reply_markup=ReplyKeyboardRemove())
    
    logger.info(f"Пользователь {user_id} импортировал {success_count} записей")
    
    # Очищаем данные
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


def inline_query(update: Update, context: CallbackContext) -> None:
    """
    Обработка inline запросов для поиска username из уже добавленных контактов.
    Пользователь вводит: @botname имя
    Бот показывает список контактов с username.
    """
    query = update.inline_query.query.strip().lower()
    user_id = update.inline_query.from_user.id
    
    logger.info(f"Inline запрос от пользователя {user_id}: '{query}'")
    
    # Получаем все записи пользователя
    birthdays = database.get_all_birthdays(user_id)
    
    if not birthdays:
        # Если нет записей, показываем подсказку
        results = [
            InlineQueryResultArticle(
                id=str(uuid4()),
                title="У вас нет добавленных событий",
                description="Используйте /add чтобы добавить первое событие",
                input_message_content=InputTextMessageContent(
                    "Используйте /add чтобы добавить событие"
                )
            )
        ]
        update.inline_query.answer(results, cache_time=1)
        return
    
    # Фильтруем и ищем контакты с username
    results = []
    
    for birthday_id, full_name, birth_date, telegram_username, event_type, event_name in birthdays:
        # Пропускаем записи без username
        if not telegram_username:
            continue
        
        # Определяем имя для отображения
        if event_type == 'birthday':
            display_name = full_name
        else:
            display_name = event_name if event_name else full_name
        
        # Поиск (если query пустой, показываем все)
        if query and query not in display_name.lower() and query not in telegram_username.lower():
            continue
        
        # Форматируем дату
        birth_date_obj = datetime.strptime(birth_date, '%Y-%m-%d')
        if event_type == 'birthday':
            formatted_date = birth_date_obj.strftime('%d.%m.%Y')
        else:
            formatted_date = birth_date_obj.strftime('%d.%m')
        
        # Определяем эмодзи
        if event_type == 'holiday':
            emoji = "🎊"
        elif event_type == 'other':
            emoji = "📅"
        else:
            emoji = "🎂"
        
        # Создаем результат
        results.append(
            InlineQueryResultArticle(
                id=str(uuid4()),
                title=f"{emoji} {display_name}",
                description=f"@{telegram_username} • {formatted_date}",
                input_message_content=InputTextMessageContent(
                    f"@{telegram_username}"
                )
            )
        )
    
    # Если ничего не найдено
    if not results:
        if query:
            results = [
                InlineQueryResultArticle(
                    id=str(uuid4()),
                    title=f"Не найдено контактов по запросу '{query}'",
                    description="Попробуйте другой запрос",
                    input_message_content=InputTextMessageContent(
                        f"Не найдено контактов по запросу '{query}'"
                    )
                )
            ]
        else:
            results = [
                InlineQueryResultArticle(
                    id=str(uuid4()),
                    title="У вас нет контактов с username",
                    description="Добавьте username при создании события",
                    input_message_content=InputTextMessageContent(
                        "У вас нет контактов с username"
                    )
                )
            ]
    
    # Ограничиваем результаты (Telegram позволяет максимум 50)
    results = results[:50]
    
    # Отправляем результаты
    update.inline_query.answer(results, cache_time=10)
    logger.info(f"Отправлено {len(results)} результатов inline запроса")


def setup_commands(bot):
    """Установить меню команд бота."""
    commands = [
        BotCommand("start", "Начать работу с ботом"),
        BotCommand("add", "Добавить новое событие"),
        BotCommand("list", "Показать все события"),
        BotCommand("delete", "Удалить событие"),
        BotCommand("edit", "Редактировать событие"),
        BotCommand("import", "Массовый импорт событий из списка"),
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

    # Для polling нужно снять webhook (иначе Telegram отдаёт Conflict)
    try:
        bot.delete_webhook()
        logger.info("Webhook снят, используется long polling")
    except Exception as e:
        logger.warning("Не удалось снять webhook: %s", e)
    
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
            WAITING_USERNAME: [MessageHandler((Filters.text | Filters.contact) & ~Filters.command, add_username)],
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
            WAITING_EDIT_USERNAME: [MessageHandler((Filters.text | Filters.contact) & ~Filters.command, edit_username)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    dispatcher.add_handler(edit_handler)
    
    # ConversationHandler для /import
    import_handler = ConversationHandler(
        entry_points=[CommandHandler('import', import_start)],
        states={
            WAITING_IMPORT_TEXT: [MessageHandler(Filters.text & ~Filters.command, import_receive_text)],
            WAITING_IMPORT_CONFIRMATION: [MessageHandler(Filters.regex('^(✅ Подтвердить|❌ Отменить)$'), import_confirm)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    dispatcher.add_handler(import_handler)
    
    # Обработчик inline запросов
    dispatcher.add_handler(InlineQueryHandler(inline_query))
    logger.info("Inline режим активирован")
    
    # Останавливаем этот экземпляр при конфликте (уже запущен другой экземпляр с тем же токеном)
    def on_error(_update: object, context: CallbackContext) -> None:
        if isinstance(context.error, Conflict):
            logger.critical(
                "Conflict: уже запущен другой экземпляр бота с этим токеном. "
                "Остановите все остальные процессы (python/bot.py) и запустите только один."
            )
            updater.stop()

    dispatcher.add_error_handler(on_error)

    # Запускаем бота
    logger.info("Бот запущен и готов к работе")
    updater.start_polling()
    updater.idle()


if __name__ == '__main__':
    main()
