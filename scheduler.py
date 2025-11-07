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
        
        for user_id, full_name, birth_date in birthdays:
            days_until = calculate_days_until_birthday(birth_date)
            
            # Проверяем нужно ли отправить уведомление
            if days_until in [0, 1, 3, 7]:
                try:
                    # Форматируем дату для отображения
                    birth_date_obj = datetime.strptime(birth_date, '%Y-%m-%d')
                    formatted_date = birth_date_obj.strftime('%d.%m.%Y')
                    
                    # Формируем текст уведомления
                    if days_until == 0:
                        message = f"🎉 СЕГОДНЯ день рождения у {full_name} ({formatted_date})!\n\nНе забудь поздравить! 🎂🎁"
                    elif days_until == 1:
                        message = f"🎂 Не забудь поздравить {full_name} завтра ({formatted_date})!"
                    elif days_until == 3:
                        message = f"🎂 Не забудь поздравить {full_name} через 3 дня ({formatted_date})!"
                    else:  # 7 дней
                        message = f"🎂 Не забудь поздравить {full_name} через 7 дней ({formatted_date})!"
                    
                    # Отправляем уведомление
                    bot.send_message(chat_id=user_id, text=message)
                    notifications_sent += 1
                    logger.info(f"Отправлено уведомление пользователю {user_id}: {full_name} через {days_until} дней")
                    
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
