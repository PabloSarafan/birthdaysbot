# Используем Python 3.12 slim образ
FROM python:3.12-slim

# Устанавливаем рабочую директорию
WORKDIR /app

# Копируем файл с зависимостями
COPY requirements.txt .

# Устанавливаем зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Копируем код бота и imghdr shim для Python 3.12+
COPY bot.py .
COPY imghdr.py /usr/local/lib/python3.12/

# Запускаем бота
CMD ["python", "bot.py"]

