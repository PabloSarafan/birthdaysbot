# Базовый образ (pkg_resources нужен для apscheduler в python-telegram-bot)
FROM python:3.12

WORKDIR /app
ENV PIP_ROOT_USER_ACTION=ignore
COPY requirements.txt .
# setuptools ставим через python -m pip, чтобы пакет попал в тот же Python, что запускает бота
RUN python -m pip install --no-cache-dir setuptools \
    && python -m pip install --no-cache-dir -r requirements.txt \
    && python -c "import pkg_resources; print('pkg_resources OK')"

# Копируем код бота и вспомогательные модули
COPY bot.py .
COPY database.py .
COPY scheduler.py .
COPY imghdr.py /usr/local/lib/python3.12/

# Создаем директорию для базы данных
RUN mkdir -p /app/data

# Устанавливаем переменную окружения для пути к БД
ENV DB_DIR=/app/data

# Запускаем бота
CMD ["python", "bot.py"]

