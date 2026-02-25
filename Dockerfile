# Базовый образ Debian: ставим setuptools из репозитория (pkg_resources для apscheduler)
FROM python:3.11-bookworm

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3-setuptools \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV PIP_ROOT_USER_ACTION=ignore
COPY requirements.txt .
RUN python3 -m pip install --no-cache-dir -r requirements.txt

# Копируем код бота и вспомогательные модули
COPY bot.py .
COPY database.py .
COPY scheduler.py .
COPY imghdr.py /usr/local/lib/python3.11/

# Создаем директорию для базы данных
RUN mkdir -p /app/data

# Устанавливаем переменную окружения для пути к БД
ENV DB_DIR=/app/data

# Запускаем бота
CMD ["python", "bot.py"]

