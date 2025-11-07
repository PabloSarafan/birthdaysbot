import sqlite3
import logging
from datetime import datetime
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

DB_NAME = 'birthdays.db'


def init_db():
    """Инициализация базы данных и создание таблицы birthdays."""
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS birthdays (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                full_name TEXT NOT NULL,
                birth_date TEXT NOT NULL,
                telegram_username TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.commit()
        conn.close()
        logger.info("База данных инициализирована успешно")
        
        # Выполняем миграцию для существующих баз
        migrate_add_username()
        
    except Exception as e:
        logger.error(f"Ошибка при инициализации базы данных: {e}")
        raise


def migrate_add_username():
    """Миграция: добавить колонку telegram_username если её нет."""
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        # Проверяем существует ли колонка telegram_username
        cursor.execute("PRAGMA table_info(birthdays)")
        columns = [column[1] for column in cursor.fetchall()]
        
        if 'telegram_username' not in columns:
            logger.info("Выполняется миграция: добавление колонки telegram_username")
            cursor.execute('ALTER TABLE birthdays ADD COLUMN telegram_username TEXT')
            conn.commit()
            logger.info("Миграция успешно выполнена")
        
        conn.close()
    except Exception as e:
        logger.error(f"Ошибка при миграции базы данных: {e}")
        raise


def add_birthday(user_id: int, full_name: str, birth_date: str, telegram_username: Optional[str] = None) -> bool:
    """
    Добавить новый день рождения.
    
    Args:
        user_id: Telegram ID пользователя
        full_name: ФИО человека
        birth_date: Дата рождения в формате YYYY-MM-DD
        telegram_username: Telegram username (опционально, без @)
    
    Returns:
        True если успешно добавлено, False в случае ошибки
    """
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        cursor.execute(
            'INSERT INTO birthdays (user_id, full_name, birth_date, telegram_username) VALUES (?, ?, ?, ?)',
            (user_id, full_name, birth_date, telegram_username)
        )
        
        conn.commit()
        conn.close()
        username_info = f" (@{telegram_username})" if telegram_username else ""
        logger.info(f"Добавлен день рождения: {full_name}{username_info} ({birth_date}) для пользователя {user_id}")
        return True
    except Exception as e:
        logger.error(f"Ошибка при добавлении дня рождения: {e}")
        return False


def get_all_birthdays(user_id: int) -> List[Tuple[int, str, str, Optional[str]]]:
    """
    Получить все дни рождения для пользователя.
    
    Args:
        user_id: Telegram ID пользователя
    
    Returns:
        Список кортежей (id, full_name, birth_date, telegram_username)
    """
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        cursor.execute(
            'SELECT id, full_name, birth_date, telegram_username FROM birthdays WHERE user_id = ? ORDER BY birth_date',
            (user_id,)
        )
        
        results = cursor.fetchall()
        conn.close()
        return results
    except Exception as e:
        logger.error(f"Ошибка при получении дней рождения: {e}")
        return []


def delete_birthday(birthday_id: int, user_id: int) -> bool:
    """
    Удалить день рождения.
    
    Args:
        birthday_id: ID записи
        user_id: Telegram ID пользователя (для проверки прав)
    
    Returns:
        True если успешно удалено, False в случае ошибки
    """
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        cursor.execute(
            'DELETE FROM birthdays WHERE id = ? AND user_id = ?',
            (birthday_id, user_id)
        )
        
        deleted = cursor.rowcount > 0
        conn.commit()
        conn.close()
        
        if deleted:
            logger.info(f"Удален день рождения с ID {birthday_id} для пользователя {user_id}")
        return deleted
    except Exception as e:
        logger.error(f"Ошибка при удалении дня рождения: {e}")
        return False


def update_birthday(birthday_id: int, user_id: int, full_name: str, birth_date: str, telegram_username: Optional[str] = None) -> bool:
    """
    Обновить информацию о дне рождения.
    
    Args:
        birthday_id: ID записи
        user_id: Telegram ID пользователя (для проверки прав)
        full_name: Новое ФИО
        birth_date: Новая дата рождения в формате YYYY-MM-DD
        telegram_username: Telegram username (опционально, без @)
    
    Returns:
        True если успешно обновлено, False в случае ошибки
    """
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        cursor.execute(
            'UPDATE birthdays SET full_name = ?, birth_date = ?, telegram_username = ? WHERE id = ? AND user_id = ?',
            (full_name, birth_date, telegram_username, birthday_id, user_id)
        )
        
        updated = cursor.rowcount > 0
        conn.commit()
        conn.close()
        
        if updated:
            logger.info(f"Обновлен день рождения с ID {birthday_id} для пользователя {user_id}")
        return updated
    except Exception as e:
        logger.error(f"Ошибка при обновлении дня рождения: {e}")
        return False


def get_all_birthdays_for_notifications() -> List[Tuple[int, str, str, Optional[str]]]:
    """
    Получить все дни рождения для отправки уведомлений.
    
    Returns:
        Список кортежей (user_id, full_name, birth_date, telegram_username)
    """
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        cursor.execute('SELECT user_id, full_name, birth_date, telegram_username FROM birthdays')
        
        results = cursor.fetchall()
        conn.close()
        return results
    except Exception as e:
        logger.error(f"Ошибка при получении дней рождения для уведомлений: {e}")
        return []


def get_birthday_by_id(birthday_id: int, user_id: int) -> Optional[Tuple[str, str, Optional[str]]]:
    """
    Получить информацию о конкретном дне рождения.
    
    Args:
        birthday_id: ID записи
        user_id: Telegram ID пользователя
    
    Returns:
        Кортеж (full_name, birth_date, telegram_username) или None если не найдено
    """
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        cursor.execute(
            'SELECT full_name, birth_date, telegram_username FROM birthdays WHERE id = ? AND user_id = ?',
            (birthday_id, user_id)
        )
        
        result = cursor.fetchone()
        conn.close()
        return result
    except Exception as e:
        logger.error(f"Ошибка при получении дня рождения: {e}")
        return None
