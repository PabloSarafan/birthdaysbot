# Используем Python 3.12 slim образ
FROM python:3.12-slim

# pkg_resources нужен для apscheduler (python-telegram-bot); в slim его нет — ставим системный пакет
RUN apt-get update && apt-get install -y --no-install-recommends python3-setuptools \
    && rm -rf /var/lib/apt/lists/*

# Устанавливаем рабочую директорию
WORKDIR /app

# Копируем файл с зависимостями и ставим зависимости
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

