import logging
from datetime import datetime, date
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz
import database

logger = logging.getLogger(__name__)

# Часовой пояс для планировщика
TIMEZONE = pytz.timezone('Europe/Moscow')


def calculate_days_until_birthday(birth_date_str: str) -> int:
    """
    Вычислить количество дней до ближайшего дня рождения.
    
    Args:
        birth_date_str: Дата рождения в формате YYYY-MM-DD
    
    Returns:
        Количество дней до дня рождения (0-365)
    """
    try:
        birth_date = datetime.strptime(birth_date_str, '%Y-%m-%d').date()
        today = date.today()
        
        # Следующий день рождения в этом году
        next_birthday = date(today.year, birth_date.month, birth_date.day)
        
        # Если день рождения уже прошел в этом году, берем следующий год
        if next_birthday < today:
            next_birthday = date(today.year + 1, birth_date.month, birth_date.day)
        
        # Вычисляем разницу в днях
        days_until = (next_birthday - today).days
        return days_until
    except Exception as e:
        logger.error(f"Ошибка при вычислении дней до дня рождения: {e}")
        return -1


def calculate_age(birth_date_str: str) -> int:
    """
    Вычислить текущий возраст человека.
    
    Args:
        birth_date_str: Дата рождения в формате YYYY-MM-DD
    
    Returns:
        Возраст в годах (или -1 если год не указан или ошибка)
    """
    try:
        birth_date = datetime.strptime(birth_date_str, '%Y-%m-%d').date()
        today = date.today()
        
        # Если год 1900 или раньше, считаем что год не указан
        if birth_date.year <= 1900:
            return -1
        
        # Вычисляем возраст
        age = today.year - birth_date.year
        
        # Проверяем, был ли уже день рождения в этом году
        if (today.month, today.day) < (birth_date.month, birth_date.day):
            age -= 1
        
        return age
    except Exception as e:
        logger.error(f"Ошибка при вычислении возраста: {e}")
        return -1


def check_and_send_notifications(bot):
    """
    Проверить все дни рождения и отправить уведомления.
    
    Отправляет уведомления за 7, 3 и 1 день до дня рождения.
    
    Args:
        bot: Экземпляр бота для отправки сообщений
    """
    logger.info("Запуск проверки дней рождения...")
    
    try:
        # Получаем все дни рождения из базы данных
        birthdays = database.get_all_birthdays_for_notifications()
        
        if not birthdays:
            logger.info("Нет дней рождения в базе данных")
            return
        
        notifications_sent = 0
        
        for user_id, full_name, birth_date, telegram_username, event_type, event_name in birthdays:
            days_until = calculate_days_until_birthday(birth_date)
            
            # Проверяем нужно ли отправить уведомление
            if days_until in [0, 1, 3, 7]:
                try:
                    # Форматируем дату для отображения
                    birth_date_obj = datetime.strptime(birth_date, '%Y-%m-%d')
                    formatted_date = birth_date_obj.strftime('%d.%m.%Y')
                    
                    # Формируем имя с username (только для дней рождения)
                    name_with_username = f"{full_name} (@{telegram_username})" if telegram_username else full_name
                    
                    # Определяем тип события (по умолчанию день рождения для обратной совместимости)
                    if not event_type:
                        event_type = 'birthday'
                    
                    # Формируем текст уведомления в зависимости от типа события и дней до события
                    if event_type == 'birthday':
                        # Вычисляем возраст если указан год
                        current_age = calculate_age(birth_date)
                        
                        # Для дня рождения сегодня показываем исполняющийся возраст
                        if days_until == 0:
                            if current_age > 0:
                                # Возраст который исполняется сегодня = текущий возраст + 1
                                age_text = f"\nИсполняется {current_age + 1} лет! "
                                message = f"🎉 СЕГОДНЯ день рождения у {name_with_username} ({formatted_date})!{age_text}Не забудь поздравить! 🎂🎁"
                            else:
                                message = f"🎉 СЕГОДНЯ день рождения у {name_with_username}!\nНе забудь поздравить! 🎂🎁"
                        elif days_until == 1:
                            age_will_be = f" (исполнится {current_age + 1} лет)" if current_age > 0 else ""
                            message = f"🎂 Не забудь поздравить {name_with_username} завтра ({formatted_date}){age_will_be}!"
                        elif days_until == 3:
                            age_will_be = f" (исполнится {current_age + 1} лет)" if current_age > 0 else ""
                            message = f"🎂 Не забудь поздравить {name_with_username} через 3 дня ({formatted_date}){age_will_be}!"
                        else:  # 7 дней
                            age_will_be = f" (исполнится {current_age + 1} лет)" if current_age > 0 else ""
                            message = f"🎂 Не забудь поздравить {name_with_username} через 7 дней ({formatted_date}){age_will_be}!"
                    
                    elif event_type == 'holiday':
                        # Для праздников используем название события
                        holiday_name = event_name if event_name else full_name
                        if days_until == 0:
                            message = f"🎊 СЕГОДНЯ {holiday_name}!\nНе забудь поздравить! 🎉"
                        elif days_until == 1:
                            message = f"🎊 Завтра {holiday_name} ({formatted_date})!\nНе забудь поздравить!"
                        elif days_until == 3:
                            message = f"🎊 Через 3 дня {holiday_name} ({formatted_date})!\nНе забудь поздравить!"
                        else:  # 7 дней
                            message = f"🎊 Через 7 дней {holiday_name} ({formatted_date})!"
                    
                    else:  # 'other'
                        # Для других событий
                        event_title = event_name if event_name else full_name
                        if days_until == 0:
                            message = f"📅 СЕГОДНЯ не забудь про {event_title}!"
                        elif days_until == 1:
                            message = f"📅 Завтра не забудь про {event_title} ({formatted_date})!"
                        elif days_until == 3:
                            message = f"📅 Через 3 дня не забудь про {event_title} ({formatted_date})!"
                        else:  # 7 дней
                            message = f"📅 Через 7 дней: {event_title} ({formatted_date})"
                    
                    # Отправляем уведомление
                    bot.send_message(chat_id=user_id, text=message)
                    notifications_sent += 1
                    logger.info(f"Отправлено уведомление пользователю {user_id}: {full_name} [{event_type}] через {days_until} дней")
                    
                except Exception as e:
                    logger.error(f"Ошибка при отправке уведомления пользователю {user_id}: {e}")
        
        logger.info(f"Проверка завершена. Отправлено уведомлений: {notifications_sent}")
    
    except Exception as e:
        logger.error(f"Ошибка при проверке дней рождения: {e}")


def start_scheduler(bot):
    """
    Запустить планировщик для ежедневной проверки дней рождения.
    
    Проверка происходит каждый день в 09:00 по московскому времени.
    
    Args:
        bot: Экземпляр бота для отправки сообщений
    """
    try:
        scheduler = BackgroundScheduler(timezone=TIMEZONE)
        
        # Добавляем задачу: проверка каждый день в 09:00
        scheduler.add_job(
            func=lambda: check_and_send_notifications(bot),
            trigger=CronTrigger(hour=9, minute=0, timezone=TIMEZONE),
            id='birthday_check',
            name='Проверка дней рождения',
            replace_existing=True
        )
        
        scheduler.start()
        logger.info("Планировщик уведомлений запущен (проверка в 09:00 MSK)")
        
        return scheduler
    
    except Exception as e:
        logger.error(f"Ошибка при запуске планировщика: {e}")
        return None
