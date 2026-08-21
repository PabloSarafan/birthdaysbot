import sqlite3
import logging
import os
from datetime import datetime
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# Используем переменную окружения для пути к БД или дефолтное значение
DB_DIR = os.getenv('DB_DIR', '.')
DB_NAME = os.path.join(DB_DIR, 'birthdays.db')


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
        
        # Выполняем миграции для существующих баз
        migrate_add_username()
        migrate_add_event_fields()
        migrate_add_remind_days()
        ensure_llm_usage_table()
        
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


def migrate_add_event_fields():
    """Миграция: добавить колонки event_type и event_name если их нет."""
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        # Проверяем существующие колонки
        cursor.execute("PRAGMA table_info(birthdays)")
        columns = [column[1] for column in cursor.fetchall()]
        
        # Добавляем event_type если его нет
        if 'event_type' not in columns:
            logger.info("Выполняется миграция: добавление колонки event_type")
            cursor.execute("ALTER TABLE birthdays ADD COLUMN event_type TEXT DEFAULT 'birthday'")
            # Устанавливаем 'birthday' для существующих записей
            cursor.execute("UPDATE birthdays SET event_type = 'birthday' WHERE event_type IS NULL")
            conn.commit()
            logger.info("Колонка event_type добавлена успешно")
        
        # Добавляем event_name если его нет
        if 'event_name' not in columns:
            logger.info("Выполняется миграция: добавление колонки event_name")
            cursor.execute("ALTER TABLE birthdays ADD COLUMN event_name TEXT")
            conn.commit()
            logger.info("Колонка event_name добавлена успешно")
        
        conn.close()
    except Exception as e:
        logger.error(f"Ошибка при миграции базы данных (event fields): {e}")
        raise


def migrate_add_remind_days():
    """Миграция: добавить колонку remind_days (за сколько дней напоминать)."""
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(birthdays)")
        columns = [col[1] for col in cursor.fetchall()]
        if 'remind_days' not in columns:
            logger.info("Выполняется миграция: добавление колонки remind_days")
            cursor.execute("ALTER TABLE birthdays ADD COLUMN remind_days TEXT DEFAULT '0,1,3,7'")
            cursor.execute("UPDATE birthdays SET remind_days = '0,1,3,7' WHERE remind_days IS NULL")
            conn.commit()
            logger.info("Колонка remind_days добавлена")
        conn.close()
    except Exception as e:
        logger.error(f"Ошибка при миграции remind_days: {e}")
        raise


DEFAULT_REMIND_DAYS = '0,1,3,7'


def ensure_llm_usage_table() -> None:
    """Таблица учёта генераций OpenAI на пользователя (lifetime)."""
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS llm_usage (
                user_id INTEGER PRIMARY KEY,
                used_count INTEGER NOT NULL DEFAULT 0,
                last_used_at REAL NOT NULL DEFAULT 0
            )
            '''
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Ошибка при создании llm_usage: {e}")
        raise


def try_consume_llm_generation(
    user_id: int,
    total_limit: int,
    cooldown_sec: float,
) -> Tuple[bool, str, int]:
    """
    Атомарно проверить лимит/cooldown и зарезервировать одну генерацию.

    Returns:
        (ok, error_or_empty, remaining_after) — remaining_after только при ok.
    """
    import time

    if total_limit <= 0:
        return False, "Генерация поздравлений отключена.", 0

    now = time.time()
    try:
        conn = sqlite3.connect(DB_NAME, timeout=10)
        try:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.cursor()
            cursor.execute(
                "SELECT used_count, last_used_at FROM llm_usage WHERE user_id = ?",
                (user_id,),
            )
            row = cursor.fetchone()
            used = int(row[0]) if row else 0
            last_at = float(row[1]) if row else 0.0

            if used >= total_limit:
                conn.rollback()
                return (
                    False,
                    f"Лимит генераций исчерпан ({total_limit} на аккаунт). "
                    f"Использовано: {used}/{total_limit}.",
                    0,
                )

            elapsed = now - last_at
            if last_at > 0 and elapsed < cooldown_sec:
                wait = max(1, int(cooldown_sec - elapsed + 0.999))
                conn.rollback()
                return (
                    False,
                    f"Подождите {wait} сек. перед следующей генерацией "
                    f"(осталось {total_limit - used} из {total_limit}).",
                    total_limit - used,
                )

            new_used = used + 1
            if row:
                cursor.execute(
                    "UPDATE llm_usage SET used_count = ?, last_used_at = ? WHERE user_id = ?",
                    (new_used, now, user_id),
                )
            else:
                cursor.execute(
                    "INSERT INTO llm_usage (user_id, used_count, last_used_at) VALUES (?, ?, ?)",
                    (user_id, new_used, now),
                )
            conn.commit()
            remaining = total_limit - new_used
            return True, "", remaining
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"Ошибка llm_usage для user_id={user_id}: {e}")
        return False, "Не удалось проверить лимит генераций. Попробуйте позже.", 0


def refund_llm_generation(user_id: int) -> None:
    """Вернуть одну генерацию (если API не вызывался / явный сбой до запроса)."""
    try:
        conn = sqlite3.connect(DB_NAME, timeout=10)
        try:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE llm_usage SET used_count = CASE WHEN used_count > 0 THEN used_count - 1 ELSE 0 END "
                "WHERE user_id = ?",
                (user_id,),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"Ошибка refund llm_usage для user_id={user_id}: {e}")


def add_birthday(user_id: int, full_name: str, birth_date: str, telegram_username: Optional[str] = None,
                 event_type: str = 'birthday', event_name: Optional[str] = None, remind_days: Optional[str] = None) -> bool:
    """
    Добавить новый день рождения.
    
    Args:
        user_id: Telegram ID пользователя
        full_name: ФИО человека
        birth_date: Дата рождения в формате YYYY-MM-DD
        telegram_username: Telegram username (опционально, без @)
        event_type: Тип события ('birthday', 'holiday', 'other')
        event_name: Название события (для праздников и других событий)
        remind_days: За сколько дней напоминать, через запятую (например '0,1,3,7'). 0 = в день события.
    
    Returns:
        True если успешно добавлено, False в случае ошибки
    """
    if remind_days is None:
        remind_days = DEFAULT_REMIND_DAYS
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        cursor.execute(
            'INSERT INTO birthdays (user_id, full_name, birth_date, telegram_username, event_type, event_name, remind_days) VALUES (?, ?, ?, ?, ?, ?, ?)',
            (user_id, full_name, birth_date, telegram_username, event_type, event_name, remind_days)
        )
        
        conn.commit()
        conn.close()
        username_info = f" (@{telegram_username})" if telegram_username else ""
        event_info = f" [{event_type}]" if event_type != 'birthday' else ""
        logger.info(f"Добавлен день рождения: {full_name}{username_info} ({birth_date}){event_info} для пользователя {user_id}")
        return True
    except Exception as e:
        logger.error(f"Ошибка при добавлении дня рождения: {e}")
        return False


def get_all_birthdays(user_id: int) -> List[Tuple[int, str, str, Optional[str], str, Optional[str], str]]:
    """
    Получить все дни рождения для пользователя.
    
    Returns:
        Список кортежей (id, full_name, birth_date, telegram_username, event_type, event_name, remind_days)
    """
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        cursor.execute(
            'SELECT id, full_name, birth_date, telegram_username, event_type, event_name, COALESCE(remind_days, ?) FROM birthdays WHERE user_id = ? ORDER BY birth_date',
            (DEFAULT_REMIND_DAYS, user_id)
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


def update_birthday(birthday_id: int, user_id: int, full_name: str, birth_date: str, telegram_username: Optional[str] = None,
                    event_type: str = 'birthday', event_name: Optional[str] = None, remind_days: Optional[str] = None) -> bool:
    """
    Обновить информацию о дне рождения.
    
    Args:
        remind_days: За сколько дней напоминать (например '0,1,3,7'). Если None — не менять.
    
    Returns:
        True если успешно обновлено, False в случае ошибки
    """
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        if remind_days is not None:
            cursor.execute(
                'UPDATE birthdays SET full_name = ?, birth_date = ?, telegram_username = ?, event_type = ?, event_name = ?, remind_days = ? WHERE id = ? AND user_id = ?',
                (full_name, birth_date, telegram_username, event_type, event_name, remind_days, birthday_id, user_id)
            )
        else:
            cursor.execute(
                'UPDATE birthdays SET full_name = ?, birth_date = ?, telegram_username = ?, event_type = ?, event_name = ? WHERE id = ? AND user_id = ?',
                (full_name, birth_date, telegram_username, event_type, event_name, birthday_id, user_id)
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


def get_all_birthdays_for_notifications(
    user_id: Optional[int] = None,
) -> List[Tuple[int, int, str, str, Optional[str], str, Optional[str], str]]:
    """
    Получить дни рождения для отправки уведомлений.

    Args:
        user_id: если задан — только записи этого пользователя (для /check);
                 если None — все записи (ежедневный cron).

    Returns:
        Список кортежей (id, user_id, full_name, birth_date, telegram_username, event_type, event_name, remind_days)
    """
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        if user_id is not None:
            cursor.execute(
                'SELECT id, user_id, full_name, birth_date, telegram_username, event_type, event_name, COALESCE(remind_days, ?) '
                'FROM birthdays WHERE user_id = ?',
                (DEFAULT_REMIND_DAYS, user_id),
            )
        else:
            cursor.execute(
                'SELECT id, user_id, full_name, birth_date, telegram_username, event_type, event_name, COALESCE(remind_days, ?) '
                'FROM birthdays',
                (DEFAULT_REMIND_DAYS,),
            )
        results = cursor.fetchall()
        conn.close()
        return results
    except Exception as e:
        logger.error(f"Ошибка при получении дней рождения для уведомлений: {e}")
        return []


def get_birthday_by_id(birthday_id: int, user_id: int) -> Optional[Tuple[int, str, str, Optional[str], str, Optional[str], str]]:
    """
    Получить запись о дне рождения по id и user_id.
    
    Returns:
        Кортеж (id, full_name, birth_date, telegram_username, event_type, event_name, remind_days) или None
    """
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute(
            'SELECT id, full_name, birth_date, telegram_username, event_type, event_name, COALESCE(remind_days, ?) FROM birthdays WHERE id = ? AND user_id = ?',
            (DEFAULT_REMIND_DAYS, birthday_id, user_id)
        )
        row = cursor.fetchone()
        conn.close()
        return row
    except Exception as e:
        logger.error(f"Ошибка при получении дня рождения по id: {e}")
        return None


