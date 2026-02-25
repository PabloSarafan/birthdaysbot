# Базовый образ и setuptools — первым делом (pkg_resources нужен для apscheduler в python-telegram-bot)
FROM python:3.12
# Чтобы принудительно пересобрать без кэша: измените число (например на 2), закоммитьте и сделайте caprover deploy
ARG REBUILD=2
RUN pip install --no-cache-dir setuptools

WORKDIR /app
ENV PIP_ROOT_USER_ACTION=ignore
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

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

