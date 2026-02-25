import os
import logging
import re
from datetime import datetime, date
from typing import Optional
from urllib.parse import urlparse
from uuid import uuid4
from telegram import Update, BotCommand, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram import InlineQueryResultArticle, InputTextMessageContent
from telegram.error import Conflict, Unauthorized
from telegram.ext import (
    Updater, 
    CommandHandler, 
    MessageHandler, 
    Filters, 
    CallbackContext,
    ConversationHandler,
    InlineQueryHandler,
    CallbackQueryHandler,
)
from telegram.ext.filters import MessageFilter
from dotenv import load_dotenv
import database
import scheduler
from scheduler import years_word

# OpenAI для генерации поздравлений (опционально)
try:
    import openai
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

# Загружаем переменные окружения: общий .env и отдельные файлы для секретов
# Путь к папке с ботом — чтобы openai.env находился при любом текущем каталоге
_BOT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv()
load_dotenv(os.path.join(_BOT_DIR, 'apibot.env'), override=True)
load_dotenv(os.path.join(_BOT_DIR, 'openai.env'), override=True)

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Состояния для ConversationHandler
WAITING_NAME, WAITING_EVENT_TYPE, WAITING_EVENT_NAME, WAITING_DATE, WAITING_REMIND_DAYS, WAITING_USERNAME = range(6)
WAITING_DELETE_ID, WAITING_EDIT_ID, WAITING_EDIT_NAME, WAITING_EDIT_DATE, WAITING_EDIT_REMIND_DAYS, WAITING_EDIT_USERNAME = range(6, 12)
WAITING_EDIT_EVENT_TYPE, WAITING_EDIT_EVENT_NAME = range(12, 14)
WAITING_IMPORT_TEXT, WAITING_IMPORT_CONFIRMATION = range(100, 102)


def _parse_remind_days(text: str):
    """Парсит строку дней напоминаний (например '0,1,3,7') в отсортированный список int. 0 = в день события."""
    parts = [x.strip() for x in (text or "").split(",") if x.strip()]
    result = []
    for p in parts:
        if p.isdigit():
            d = int(p)
            if 0 <= d <= 365 and d not in result:
                result.append(d)
    result.sort()
    return result if result else [0]


def _menu_keyboard():
    """Inline-кнопки для быстрого управления."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➕ Добавить", callback_data="menu:add"),
            InlineKeyboardButton("📋 Список", callback_data="menu:list"),
        ],
        [
            InlineKeyboardButton("🗑 Удалить", callback_data="menu:delete"),
            InlineKeyboardButton("✏️ Редактировать", callback_data="menu:edit"),
        ],
        [InlineKeyboardButton("🔔 Проверить уведомления", callback_data="menu:check")],
    ])


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

Начнем? Используйте кнопки ниже или команды.
"""
    update.message.reply_text(welcome_message, reply_markup=_menu_keyboard())
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


ADD_PROMPT_TEXT = (
    "📝 Добавление нового события.\n\n"
    "Выберите тип события:"
)

ADD_EVENT_TYPE_KEYBOARD = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("🎂 День рождения", callback_data="add_type:birthday"),
        InlineKeyboardButton("🎊 Праздник", callback_data="add_type:holiday"),
    ],
    [InlineKeyboardButton("📅 Другое событие", callback_data="add_type:other")],
])


def add_start(update: Update, context: CallbackContext) -> int:
    """Начало диалога добавления события."""
    update.message.reply_text(
        ADD_PROMPT_TEXT + "\n\nОтменить: /cancel",
        reply_markup=ADD_EVENT_TYPE_KEYBOARD,
    )
    return WAITING_EVENT_TYPE


def menu_add_entry(update: Update, context: CallbackContext) -> int:
    """Вход в добавление события по inline-кнопке."""
    update.callback_query.answer()
    context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=ADD_PROMPT_TEXT + "\n\nОтменить: /cancel",
        reply_markup=ADD_EVENT_TYPE_KEYBOARD,
    )
    return WAITING_EVENT_TYPE


def _apply_event_type_choice(context: CallbackContext, event_type: str, chat_id: int, reply_method) -> int:
    """Устанавливает тип события и отправляет следующий вопрос. Возвращает следующее состояние."""
    context.user_data['event_type'] = event_type
    if event_type == 'birthday':
        reply_method(
            "🎂 Вы выбрали: День рождения\n\n"
            "Введите имя или ФИО человека (например: Иван Иванов)\n\n"
            "Отменить: /cancel"
        )
        return WAITING_NAME
    if event_type == 'holiday':
        reply_method(
            "🎊 Вы выбрали: Праздник\n\n"
            "Введите название праздника (например: Новый Год, 8 Марта)\n\n"
            "Отменить: /cancel"
        )
        return WAITING_EVENT_NAME
    if event_type == 'other':
        reply_method(
            "📅 Вы выбрали: Другое событие\n\n"
            "Введите название события (например: Годовщина свадьбы)\n\n"
            "Отменить: /cancel"
        )
        return WAITING_EVENT_NAME
    return WAITING_EVENT_TYPE


def add_event_type_callback(update: Update, context: CallbackContext) -> int:
    """Обработка выбора типа события по inline-кнопке."""
    query = update.callback_query
    query.answer()
    data = (query.data or "").strip()
    if not data.startswith("add_type:"):
        return WAITING_EVENT_TYPE
    event_type = data.split(":", 1)[1]
    if event_type not in ("birthday", "holiday", "other"):
        return WAITING_EVENT_TYPE

    def reply(text):
        context.bot.send_message(chat_id=update.effective_chat.id, text=text)
    return _apply_event_type_choice(context, event_type, update.effective_chat.id, reply)


def add_event_type(update: Update, context: CallbackContext) -> int:
    """Обработка выбора типа события (ввод цифрой 1, 2 или 3)."""
    choice = update.message.text.strip()
    if choice == '1':
        return _apply_event_type_choice(context, 'birthday', update.effective_chat.id, update.message.reply_text)
    if choice == '2':
        return _apply_event_type_choice(context, 'holiday', update.effective_chat.id, update.message.reply_text)
    if choice == '3':
        return _apply_event_type_choice(context, 'other', update.effective_chat.id, update.message.reply_text)
    update.message.reply_text(
        "❌ Пожалуйста, выберите тип кнопкой ниже или введите 1, 2 или 3.\n\n"
        "🎂 День рождения · 🎊 Праздник · 📅 Другое событие"
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
        
        # Для дня рождения спрашиваем дни напоминаний, затем username; для остальных — сохраняем сразу
        if event_type == 'birthday':
            update.message.reply_text(
                f"✅ Дата: {date_str}\n\n"
                "За сколько дней до события напоминать? (через запятую)\n"
                "0 = в сам день события.\n\n"
                "Примеры: 0 — только в день; 0,1,3,7 — за неделю, 3 дня, день и в день.\n"
                "По умолчанию: 0,1,3,7\n\n"
                "Введите числа через запятую или /skip для значения по умолчанию:\n\n"
                "Отменить: /cancel"
            )
            return WAITING_REMIND_DAYS
        else:
            # Для праздников и других событий сохраняем сразу
            full_name = context.user_data.get('full_name')
            event_name = context.user_data.get('event_name')
            user_id = update.effective_user.id
            
            if database.add_birthday(user_id, full_name, birth_date.strftime('%Y-%m-%d'), 
                                    None, event_type, event_name, database.DEFAULT_REMIND_DAYS):
                event_emoji = "🎊" if event_type == 'holiday' else "📅"
                update.message.reply_text(
                    f"✅ Успешно сохранено!\n\n"
                    f"{event_emoji} {event_name}\n"
                    f"📅 {date_str}\n\n"
                    f"Напоминания: за 0, 1, 3 и 7 дней до события (можно изменить в /edit)."
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


def add_remind_days(update: Update, context: CallbackContext) -> int:
    """Получение дней напоминаний и запрос username (для дня рождения)."""
    text = update.message.text.strip()
    if text.lower() == "/skip" or not text:
        context.user_data["remind_days"] = database.DEFAULT_REMIND_DAYS
    else:
        days_list = _parse_remind_days(text)
        if not days_list:
            update.message.reply_text(
                "❌ Введите числа через запятую (0 = в день события), например: 0,1,3,7\n"
                "Или /skip для значения по умолчанию."
            )
            return WAITING_REMIND_DAYS
        context.user_data["remind_days"] = ",".join(map(str, days_list))
    
    keyboard = [[KeyboardButton("⏭ Пропустить")]]
    reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    update.message.reply_text(
        "Добавьте Telegram контакт:\n\n"
        "📱 Нажмите 📎 → Контакт → Выберите человека\n\n"
        "⏭ Или нажмите 'Пропустить'\n\nОтменить: /cancel",
        reply_markup=reply_markup
    )
    return WAITING_USERNAME


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
    
    # Сохраняем в базу данных (дни напоминаний из предыдущего шага или по умолчанию)
    remind_days = context.user_data.get("remind_days") or database.DEFAULT_REMIND_DAYS
    if database.add_birthday(user_id, full_name, birth_date, telegram_username, event_type, event_name, remind_days):
        username_text = f" (@{telegram_username})" if telegram_username else ""
        update.message.reply_text(
            f"✅ Успешно сохранено!\n\n"
            f"👤 {full_name}{username_text}\n"
            f"🎂 {formatted_date}\n\n"
            f"Напоминания: за {remind_days.replace(',', ', ')} дн. до события (0 = в день). Изменить: /edit.",
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


def _send_to_chat(update: Update, context: CallbackContext, text: str, reply_markup=None):
    """Отправить сообщение в чат: из команды (reply) или из callback."""
    if update.message:
        update.message.reply_text(text, reply_markup=reply_markup)
    else:
        if update.callback_query:
            update.callback_query.answer()
        context.bot.send_message(chat_id=update.effective_chat.id, text=text, reply_markup=reply_markup)


def list_birthdays(update: Update, context: CallbackContext) -> None:
    """Показать все события, отсортированные по дням до наступления."""
    user_id = update.effective_user.id
    logger.info(f"Список от пользователя {user_id}")
    birthdays = database.get_all_birthdays(user_id)
    logger.info(f"Получено записей из БД: {len(birthdays)}")
    
    if not birthdays:
        _send_to_chat(update, context,
            "📋 Список пуст.\n\nДобавьте первую запись командой /add или кнопкой «➕ Добавить».",
            reply_markup=_menu_keyboard()
        )
        return
    
    # Сортируем по дням до события
    today = date.today()
    birthdays_with_days = []
    
    for birthday_id, full_name, birth_date, telegram_username, event_type, event_name, _ in birthdays:
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
    
    message += "Управление:"
    _send_to_chat(update, context, message, reply_markup=_menu_keyboard())


def _build_delete_list_message(birthdays):
    """Сформировать текст списка для удаления."""
    message = "🗑 Удаление записи\n\nВыберите номер записи для удаления:\n\n"
    for idx, (birthday_id, full_name, birth_date, telegram_username, event_type, event_name, _) in enumerate(birthdays, 1):
        birth_date_obj = datetime.strptime(birth_date, '%Y-%m-%d')
        if event_type == 'holiday':
            emoji, formatted_date = "🎊", birth_date_obj.strftime('%d.%m')
            display_name = event_name if event_name else full_name
        elif event_type == 'other':
            emoji, formatted_date = "📅", birth_date_obj.strftime('%d.%m')
            display_name = event_name if event_name else full_name
        else:
            emoji, formatted_date = "🎂", birth_date_obj.strftime('%d.%m.%Y')
            display_name = full_name + (f" (@{telegram_username})" if telegram_username else "")
        message += f"{idx}. {emoji} {display_name} - {formatted_date}\n"
    message += "\nВведите номер записи или /cancel для отмены:"
    return message


def delete_start(update: Update, context: CallbackContext) -> int:
    """Начало диалога удаления записи."""
    user_id = update.effective_user.id
    birthdays = database.get_all_birthdays(user_id)
    if not birthdays:
        update.message.reply_text("📋 Список пуст. Нечего удалять.")
        return ConversationHandler.END
    message = _build_delete_list_message(birthdays)
    update.message.reply_text(message)
    context.user_data['birthdays'] = birthdays
    return WAITING_DELETE_ID


def menu_delete_entry(update: Update, context: CallbackContext) -> int:
    """Вход в удаление по inline-кнопке."""
    update.callback_query.answer()
    user_id = update.effective_user.id
    birthdays = database.get_all_birthdays(user_id)
    if not birthdays:
        context.bot.send_message(chat_id=update.effective_chat.id, text="📋 Список пуст. Нечего удалять.")
        return ConversationHandler.END
    context.bot.send_message(chat_id=update.effective_chat.id, text=_build_delete_list_message(birthdays))
    context.user_data['birthdays'] = birthdays
    return WAITING_DELETE_ID


def delete_execute(update: Update, context: CallbackContext) -> int:
    """Удаление выбранной записи."""
    try:
        index = int(update.message.text.strip()) - 1
        birthdays = context.user_data.get('birthdays', [])
        
        if 0 <= index < len(birthdays):
            birthday_id, full_name, birth_date, telegram_username, event_type, event_name, _ = birthdays[index]
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


def _build_edit_list_message(birthdays):
    """Сформировать текст списка для редактирования."""
    message = "✏️ Редактирование записи\n\nВыберите номер записи для редактирования:\n\n"
    for idx, (birthday_id, full_name, birth_date, telegram_username, event_type, event_name, _) in enumerate(birthdays, 1):
        birth_date_obj = datetime.strptime(birth_date, '%Y-%m-%d')
        if event_type == 'holiday':
            emoji, formatted_date = "🎊", birth_date_obj.strftime('%d.%m')
            display_name = event_name if event_name else full_name
        elif event_type == 'other':
            emoji, formatted_date = "📅", birth_date_obj.strftime('%d.%m')
            display_name = event_name if event_name else full_name
        else:
            emoji, formatted_date = "🎂", birth_date_obj.strftime('%d.%m.%Y')
            display_name = full_name + (f" (@{telegram_username})" if telegram_username else "")
        message += f"{idx}. {emoji} {display_name} - {formatted_date}\n"
    message += "\nВведите номер записи или /cancel для отмены:"
    return message


def edit_start(update: Update, context: CallbackContext) -> int:
    """Начало диалога редактирования записи."""
    user_id = update.effective_user.id
    birthdays = database.get_all_birthdays(user_id)
    if not birthdays:
        update.message.reply_text("📋 Список пуст. Нечего редактировать.")
        return ConversationHandler.END
    update.message.reply_text(_build_edit_list_message(birthdays))
    context.user_data['birthdays'] = birthdays
    return WAITING_EDIT_ID


def menu_edit_entry(update: Update, context: CallbackContext) -> int:
    """Вход в редактирование по inline-кнопке."""
    update.callback_query.answer()
    user_id = update.effective_user.id
    birthdays = database.get_all_birthdays(user_id)
    if not birthdays:
        context.bot.send_message(chat_id=update.effective_chat.id, text="📋 Список пуст. Нечего редактировать.")
        return ConversationHandler.END
    context.bot.send_message(chat_id=update.effective_chat.id, text=_build_edit_list_message(birthdays))
    context.user_data['birthdays'] = birthdays
    return WAITING_EDIT_ID


def edit_id(update: Update, context: CallbackContext) -> int:
    """Получение ID записи и запрос нового имени/названия."""
    try:
        index = int(update.message.text.strip()) - 1
        birthdays = context.user_data.get('birthdays', [])
        
        if 0 <= index < len(birthdays):
            birthday_id, full_name, birth_date, telegram_username, event_type, event_name, remind_days = birthdays[index]
            context.user_data['edit_id'] = birthday_id
            context.user_data['old_name'] = full_name
            context.user_data['old_date'] = birth_date
            context.user_data['old_username'] = telegram_username
            context.user_data['old_event_type'] = event_type if event_type else 'birthday'
            context.user_data['old_event_name'] = event_name
            context.user_data['old_remind_days'] = remind_days or database.DEFAULT_REMIND_DAYS
            
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
        
        # Для дня рождения спрашиваем дни напоминаний, затем username; для остальных — сохраняем сразу
        if event_type == 'birthday':
            old_remind = context.user_data.get('old_remind_days') or database.DEFAULT_REMIND_DAYS
            update.message.reply_text(
                f"✅ Дата: {date_str}\n\n"
                f"Текущие дни напоминаний: {old_remind} (0 = в день события)\n\n"
                f"Введите новые значения через запятую (например 0,1,3,7) или /skip чтобы не менять:\n\n"
                f"Отменить: /cancel"
            )
            return WAITING_EDIT_REMIND_DAYS
        else:
            # Для праздников и других событий сохраняем сразу
            birthday_id = context.user_data.get('edit_id')
            new_name = context.user_data.get('new_name')
            new_event_name = context.user_data.get('new_event_name')
            old_remind = context.user_data.get('old_remind_days') or database.DEFAULT_REMIND_DAYS
            user_id = update.effective_user.id
            
            if database.update_birthday(birthday_id, user_id, new_name, birth_date.strftime('%Y-%m-%d'), 
                                       None, event_type, new_event_name, old_remind):
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


def edit_remind_days(update: Update, context: CallbackContext) -> int:
    """Получение новых дней напоминаний и запрос username (при редактировании)."""
    text = update.message.text.strip()
    if text.lower() == "/skip" or not text:
        context.user_data["new_remind_days"] = context.user_data.get("old_remind_days") or database.DEFAULT_REMIND_DAYS
    else:
        days_list = _parse_remind_days(text)
        if not days_list:
            update.message.reply_text(
                "❌ Введите числа через запятую (0 = в день события), например: 0,1,3,7\n"
                "Или /skip чтобы не менять."
            )
            return WAITING_EDIT_REMIND_DAYS
        context.user_data["new_remind_days"] = ",".join(map(str, days_list))
    
    old_username = context.user_data.get('old_username')
    username_info = f" (@{old_username})" if old_username else " (нет)"
    keyboard = [[KeyboardButton("⏭ Пропустить")]]
    reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    update.message.reply_text(
        f"Текущий username:{username_info}\n\n"
        f"Обновите контакт (📎 → Контакт) или нажмите 'Пропустить'.\n\nОтменить: /cancel",
        reply_markup=reply_markup
    )
    return WAITING_EDIT_USERNAME


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
    
    # Обновляем запись (дни напоминаний из шага edit_remind_days)
    new_remind_days = context.user_data.get("new_remind_days")
    if database.update_birthday(birthday_id, user_id, new_name, new_date, telegram_username, event_type, new_event_name, new_remind_days):
        username_text = f" (@{telegram_username})" if telegram_username else ""
        remind_info = f"\nНапоминания: за {new_remind_days.replace(',', ', ')} дн." if new_remind_days else ""
        update.message.reply_text(
            f"✅ Запись обновлена!\n\n"
            f"👤 {new_name}{username_text}\n"
            f"🎂 {formatted_date}{remind_info}",
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
    chat_id = update.effective_chat.id
    bot = context.bot
    if update.message:
        update.message.reply_text("🔍 Проверяю уведомления...")
    else:
        update.callback_query.answer()
        bot.send_message(chat_id=chat_id, text="🔍 Проверяю уведомления...")
    scheduler.check_and_send_notifications(bot)
    if update.message:
        update.message.reply_text("✅ Проверка завершена! Уведомления отправлены если есть подходящие даты.")
    else:
        bot.send_message(chat_id=chat_id, text="✅ Проверка завершена! Уведомления отправлены если есть подходящие даты.")


def menu_callback(update: Update, context: CallbackContext) -> None:
    """Обработка inline-кнопок меню: Список и Проверить уведомления."""
    data = (update.callback_query.data or "").strip()
    if data == "menu:list":
        list_birthdays(update, context)
    elif data == "menu:check":
        check_notifications(update, context)


# --- Генерация поздравлений через OpenAI ---

def _openai_client():
    """Создать клиент OpenAI если есть ключ."""
    if not OPENAI_AVAILABLE:
        return None
    key = (os.getenv('OPENAI_API_KEY') or '').strip()
    if not key:
        return None
    # Плейсхолдер из примера или слишком короткий ключ
    if key.startswith('sk-your-') or len(key) < 40:
        logger.warning("OpenAI: ключ похож на плейсхолдер или слишком короткий (длина %s)", len(key))
        return None
    logger.info("OpenAI: ключ загружен, длина %s символов", len(key))
    return openai.OpenAI(api_key=key)


def generate_congratulation(full_name: str, custom_prompt: Optional[str] = None) -> str:
    """
    Сгенерировать текст поздравления с днём рождения через OpenAI.
    
    Args:
        full_name: Имя именинника
        custom_prompt: Дополнительные пожелания (стиль, тон и т.д.), опционально
    
    Returns:
        Текст поздравления или сообщение об ошибке
    """
    client = _openai_client()
    if not client:
        return "Сервис генерации недоступен. Добавьте OPENAI_API_KEY в настройки бота."
    
    system = (
        "Ты помогаешь писать короткие тёплые поздравления с днём рождения. "
        "Пиши от первого лица, как будто пользователь сам поздравляет. "
        "Без обрамления в кавычки и без подписи в конце. Один короткий абзац."
    )
    user_msg = f"Напиши поздравление с днём рождения для {full_name}."
    if custom_prompt and custom_prompt.strip():
        user_msg += f" Дополнительные пожелания: {custom_prompt.strip()}"
    
    model = (os.getenv("OPENAI_MODEL") or "gpt-4o-mini").strip()
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=300,
        )
        text = (response.choices[0].message.content or "").strip()
        return text if text else "Не удалось сгенерировать поздравление."
    except Exception as e:
        logger.exception("Ошибка OpenAI при генерации поздравления")
        err_str = str(e).lower()
        if "401" in err_str or "invalid_api_key" in err_str or "incorrect api key" in err_str:
            return "Проверьте OPENAI_API_KEY в файле openai.env — ключ неверный или не задан."
        return "Ошибка при генерации. Попробуйте позже или проверьте openai.env."


# Пресеты промптов для кнопок «Свой промпт»
PROMPT_PRESETS = {
    "humor": "короткое и с юмором",
    "touching": "трогательное и душевное",
    "family": "для близкого человека, тёплое",
}


def congratulate_callback(update: Update, context: CallbackContext) -> None:
    """Обработка нажатия кнопок «Сгенерировать поздравление», «Свой промпт» и пресетов."""
    query = update.callback_query
    query.answer()
    
    data = (query.data or "").strip()
    user_id = update.effective_user.id
    
    # «Свой промпт» — показываем inline-кнопки (пресеты + свой текст)
    if data.startswith("congratulate_prompt:"):
        birthday_id_str = data.split(":", 1)[1]
        try:
            birthday_id = int(birthday_id_str)
        except ValueError:
            query.message.reply_text("Ошибка: неверные данные.")
            return
        record = database.get_birthday_by_id(birthday_id, user_id)
        if not record:
            query.message.reply_text("Запись не найдена или у вас нет доступа к ней.")
            return
        full_name = record[1]
        keyboard = [
            [
                InlineKeyboardButton("😄 С юмором", callback_data=f"congratulate_custom:{birthday_id}:humor"),
                InlineKeyboardButton("💝 Трогательное", callback_data=f"congratulate_custom:{birthday_id}:touching"),
            ],
            [
                InlineKeyboardButton("👨‍👩‍👧 Для близкого", callback_data=f"congratulate_custom:{birthday_id}:family"),
                InlineKeyboardButton("✏️ Свой текст", callback_data=f"congratulate_custom_text:{birthday_id}"),
            ],
        ]
        query.message.reply_text(
            f"✏️ Выберите стиль поздравления для {full_name} или введите свой промпт:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return
    
    # Пресет: сразу генерируем с выбранным промптом
    if data.startswith("congratulate_custom:"):
        parts = data.split(":", 2)
        if len(parts) < 3:
            return
        try:
            birthday_id = int(parts[1])
        except ValueError:
            return
        preset_key = parts[2]
        preset_text = PROMPT_PRESETS.get(preset_key)
        if not preset_text:
            return
        record = database.get_birthday_by_id(birthday_id, user_id)
        if not record:
            query.message.reply_text("Запись не найдена или у вас нет доступа к ней.")
            return
        full_name = record[1]
        query.message.reply_text("⏳ Генерирую поздравление...")
        text = generate_congratulation(full_name, custom_prompt=preset_text)
        query.message.reply_text(f"🎂 Поздравление для {full_name}:\n\n{text}")
        return
    
    # «Свой текст» — ждём следующее сообщение пользователя (reply не обязателен)
    if data.startswith("congratulate_custom_text:"):
        birthday_id_str = data.split(":", 1)[1]
        try:
            birthday_id = int(birthday_id_str)
        except ValueError:
            query.message.reply_text("Ошибка: неверные данные.")
            return
        record = database.get_birthday_by_id(birthday_id, user_id)
        if not record:
            query.message.reply_text("Запись не найдена или у вас нет доступа к ней.")
            return
        context.bot_data.setdefault("prompt_wait_user", {})[user_id] = (birthday_id, query.message.chat_id)
        query.message.reply_text(
            "✏️ Напишите ваш промпт в чат (одним сообщением). Ответ на это сообщение не обязателен."
        )
        return
    
    if not data.startswith("congratulate:"):
        return
    
    birthday_id_str = data.split(":", 1)[1]
    try:
        birthday_id = int(birthday_id_str)
    except ValueError:
        query.message.reply_text("Ошибка: неверные данные.")
        return
    
    record = database.get_birthday_by_id(birthday_id, user_id)
    if not record:
        query.message.reply_text("Запись не найдена или у вас нет доступа к ней.")
        return
    
    full_name = record[1]
    query.message.reply_text("⏳ Генерирую поздравление...")
    
    text = generate_congratulation(full_name, custom_prompt=None)
    query.message.reply_text(f"🎂 Поздравление для {full_name}:\n\n{text}")


class PromptWaitFilter(MessageFilter):
    """Фильтр: True, если ждём от пользователя текст промпта (по reply или по prompt_wait_user)."""

    def __init__(self, dispatcher):
        self._dispatcher = dispatcher

    def filter(self, message):
        if not message or not (message.text or "").strip():
            return False
        bot_data = self._dispatcher.bot_data
        uid = message.from_user.id if message.from_user else None
        cid = message.chat.id if message.chat else None
        if not uid or cid is None:
            return False
        if uid in bot_data.get("prompt_wait_user", {}):
            _, stored_chat_id = bot_data["prompt_wait_user"][uid]
            if stored_chat_id == cid:
                return True
        if message.reply_to_message:
            reply_to = message.reply_to_message
            if reply_to.from_user and reply_to.from_user.id == self._dispatcher.bot.id:
                key = (cid, reply_to.message_id)
                if key in bot_data.get("prompt_wait", {}):
                    return True
        return False


def prompt_reply_handler(update: Update, context: CallbackContext) -> None:
    """Обработка ввода промпта: по ответу на сообщение (reply) или по ожиданию из prompt_wait_user (reply не обязателен)."""
    if not update.message or not (update.message.text or "").strip():
        return
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    prompt_text = (update.message.text or "").strip()
    bot_data = context.bot_data
    birthday_id = None
    source = None

    # Путь 1: пользователь в списке ожидания (нажал «Свой текст») — reply не обязателен
    prompt_wait_user = bot_data.get("prompt_wait_user") or {}
    if user_id in prompt_wait_user:
        birthday_id, stored_chat_id = prompt_wait_user.pop(user_id)
        if stored_chat_id == chat_id:
            source = "user"

    # Путь 2: ответ на наше сообщение (старый сценарий с prompt_wait)
    if birthday_id is None and update.message.reply_to_message:
        reply_to = update.message.reply_to_message
        if reply_to.from_user and reply_to.from_user.id == context.bot.id:
            key = (chat_id, reply_to.message_id)
            prompt_wait = bot_data.get("prompt_wait") or {}
            if key in prompt_wait:
                birthday_id, uid = prompt_wait.pop(key)
                if uid == user_id:
                    source = "reply"

    if not source or birthday_id is None:
        return

    record = database.get_birthday_by_id(birthday_id, user_id)
    if not record:
        update.message.reply_text("Запись не найдена.")
        return
    full_name = record[1]
    update.message.reply_text("⏳ Генерирую поздравление по вашему промпту...")
    text = generate_congratulation(full_name, custom_prompt=prompt_text)
    update.message.reply_text(f"🎂 Поздравление для {full_name}:\n\n{text}")


def prompt_command(update: Update, context: CallbackContext) -> None:
    """
    Команда /prompt <birthday_id> <произвольный промпт> — генерация поздравления по своему промпту.
    """
    user_id = update.effective_user.id
    if not context.args or len(context.args) < 1:
        update.message.reply_text(
            "Использование: /prompt <номер_записи> ваш промпт\n\n"
            "Номер записи показывается в подсказке после нажатия кнопки «Свой промпт» в уведомлении о дне рождения."
        )
        return
    
    try:
        birthday_id = int(context.args[0])
    except ValueError:
        update.message.reply_text("Номер записи должен быть числом.")
        return
    
    custom_prompt = " ".join(context.args[1:]).strip() if len(context.args) > 1 else None
    if not custom_prompt:
        update.message.reply_text("Напишите промпт после номера записи, например: /prompt 5 короткое и с юмором")
        return
    
    record = database.get_birthday_by_id(birthday_id, user_id)
    if not record:
        update.message.reply_text("Запись не найдена или у вас нет доступа к ней.")
        return
    
    full_name = record[1]
    update.message.reply_text("⏳ Генерирую поздравление по вашему промпту...")
    
    text = generate_congratulation(full_name, custom_prompt=custom_prompt)
    update.message.reply_text(f"🎂 Поздравление для {full_name}:\n\n{text}")


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
    
    for birthday_id, full_name, birth_date, telegram_username, event_type, event_name, _ in birthdays:
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
    try:
        bot.set_my_commands(commands)
        logger.info("Меню команд установлено")
    except Unauthorized:
        raise ValueError(
            "Токен бота неверный или отозван. Проверьте BOT_TOKEN в apibot.env и при необходимости получите новый в @BotFather (Telegram)."
        )


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

    # Режим продакшена: webhook (если заданы WEBHOOK_URL и PORT), иначе — long polling
    webhook_url = os.getenv("WEBHOOK_URL", "").strip()
    port_str = os.getenv("PORT", "").strip()
    use_webhook = bool(webhook_url and port_str)
    if use_webhook:
        try:
            port = int(port_str)
        except ValueError:
            logger.warning("PORT должен быть числом, используем long polling")
            use_webhook = False
    if port_str and not webhook_url:
        logger.warning(
            "PORT задан, но WEBHOOK_URL не задан — бот будет в режиме polling. "
            "Чтобы бот отвечал на проде (CapRover и др.), задайте WEBHOOK_URL=https://ваш-домен (например https://birthdaybot.sarafannikov.work)"
        )
    if not use_webhook:
        try:
            bot.delete_webhook()
            logger.info("Webhook снят, используется long polling")
        except Unauthorized:
            logger.critical("BOT_TOKEN отклонён Telegram (Unauthorized). Проверьте apibot.env и @BotFather.")
            raise ValueError(
                "Токен бота неверный или отозван. Проверьте BOT_TOKEN в apibot.env, получите новый токен в @BotFather (Telegram)."
            )
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
    
    # Inline-меню: Список и Проверить (Добавить/Удалить/Редактировать — в entry_points диалогов ниже)
    dispatcher.add_handler(CallbackQueryHandler(menu_callback, pattern=r'^menu:(list|check)$'))
    
    # Генерация поздравлений: кнопки под уведомлением и команда /prompt
    dispatcher.add_handler(CallbackQueryHandler(congratulate_callback, pattern=r'^congratulate'))
    dispatcher.add_handler(CommandHandler('prompt', prompt_command))
    dispatcher.add_handler(MessageHandler(PromptWaitFilter(dispatcher), prompt_reply_handler))
    
    # ConversationHandler для /add
    add_handler = ConversationHandler(
        entry_points=[
            CommandHandler('add', add_start),
            CallbackQueryHandler(menu_add_entry, pattern=r'^menu:add$'),
        ],
        states={
            WAITING_EVENT_TYPE: [
                MessageHandler(Filters.text & ~Filters.command, add_event_type),
                CallbackQueryHandler(add_event_type_callback, pattern=r'^add_type:'),
            ],
            WAITING_EVENT_NAME: [MessageHandler(Filters.text & ~Filters.command, add_event_name)],
            WAITING_NAME: [MessageHandler(Filters.text & ~Filters.command, add_name)],
            WAITING_DATE: [MessageHandler(Filters.text & ~Filters.command, add_date)],
            WAITING_REMIND_DAYS: [MessageHandler(Filters.text & ~Filters.command, add_remind_days)],
            WAITING_USERNAME: [MessageHandler((Filters.text | Filters.contact) & ~Filters.command, add_username)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    dispatcher.add_handler(add_handler)
    
    # ConversationHandler для /delete
    delete_handler = ConversationHandler(
        entry_points=[
            CommandHandler('delete', delete_start),
            CallbackQueryHandler(menu_delete_entry, pattern=r'^menu:delete$'),
        ],
        states={
            WAITING_DELETE_ID: [MessageHandler(Filters.text & ~Filters.command, delete_execute)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    dispatcher.add_handler(delete_handler)
    
    # ConversationHandler для /edit
    edit_handler = ConversationHandler(
        entry_points=[
            CommandHandler('edit', edit_start),
            CallbackQueryHandler(menu_edit_entry, pattern=r'^menu:edit$'),
        ],
        states={
            WAITING_EDIT_ID: [MessageHandler(Filters.text & ~Filters.command, edit_id)],
            WAITING_EDIT_NAME: [MessageHandler(Filters.text & ~Filters.command, edit_name)],
            WAITING_EDIT_DATE: [MessageHandler(Filters.text & ~Filters.command, edit_date)],
            WAITING_EDIT_REMIND_DAYS: [MessageHandler(Filters.text & ~Filters.command, edit_remind_days)],
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

    # Запускаем бота: webhook на проде или polling локально
    if use_webhook:
        path = urlparse(webhook_url).path.strip("/") or ""
        logger.info("Запуск в режиме webhook: %s (порт %s, path %r)", webhook_url, port, path or "/")
        updater.start_webhook(
            listen="0.0.0.0",
            port=port,
            url_path=path,
            webhook_url=webhook_url,
        )
    else:
        logger.info("Бот запущен и готов к работе (long polling)")
        updater.start_polling()
    updater.idle()


if __name__ == '__main__':
    main()
