#!/usr/bin/env python3
"""Проверка доступа к OpenAI API: загрузка openai.env и один запрос."""
import os
from dotenv import load_dotenv

# Убираем из окружения, чтобы точно взять значение из файла
os.environ.pop("OPENAI_API_KEY", None)

# Загружаем из папки, где лежит этот скрипт
_script_dir = os.path.dirname(os.path.abspath(__file__))
env_path = os.path.join(_script_dir, "openai.env")
print("Читаю файл:", os.path.abspath(env_path))
if not os.path.isfile(env_path):
    print("Файл не найден:", env_path)
    exit(1)
load_dotenv(env_path, override=True)
key = (os.getenv("OPENAI_API_KEY") or "").strip()

print("Длина ключа:", len(key), "| Начало:", repr(key[:20]) if len(key) >= 20 else repr(key))

if not key:
    print("OPENAI_API_KEY не найден в openai.env или пустой")
    exit(1)
if key.startswith("sk-your-") or len(key) < 40:
    print("В openai.env похоже на плейсхолдер (sk-your-...) или ключ слишком короткий.")
    print("Вставьте реальный ключ с https://platform.openai.com/api-keys")
    exit(1)

print("Ключ загружен из файла (длина:", len(key), "символов)")

try:
    import openai
    client = openai.OpenAI(api_key=key.strip())
    r = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "Скажи одно слово: ок"}],
        max_tokens=10,
    )
    text = (r.choices[0].message.content or "").strip()
    print("Ответ API:", repr(text))
    print("Доступ к API есть.")
except Exception as e:
    print("Ошибка:", e)
    if "401" in str(e) or "invalid_api_key" in str(e).lower():
        print("Ключ неверный или отозван. Проверьте https://platform.openai.com/api-keys")
    elif "429" in str(e) or "rate_limit" in str(e).lower():
        print("Лимит запросов или нет оплаты. Проверьте https://platform.openai.com/account/billing")
    exit(1)
