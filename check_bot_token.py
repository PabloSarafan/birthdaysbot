#!/usr/bin/env python3
"""Проверка BOT_TOKEN из apibot.env через Telegram API getMe."""
import os
from dotenv import load_dotenv

_BOT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_BOT_DIR, "apibot.env"), override=True)
token = (os.getenv("BOT_TOKEN") or "").strip()

if not token:
    print("BOT_TOKEN не найден в apibot.env")
    exit(1)

# Проверка формата: обычно 46 символов, есть двоеточие
if ":" not in token or len(token) < 40:
    print("Токен похож на неверный (должен быть вида 123456789:ABC...). Длина:", len(token))
    exit(1)

print("Токен загружен, длина:", len(token))

try:
    from telegram import Bot
    bot = Bot(token=token)
    me = bot.get_me()
    print("OK. Бот подключён:", me.username)
except Exception as e:
    if "401" in str(e) or "Unauthorized" in str(type(e).__name__):
        print("Ошибка: Telegram отклонил токен (неверный или отозван).")
        print("Получите новый токен: @BotFather → /mybots → ваш бот → API Token → Revoke → скопируйте новый токен в apibot.env")
    else:
        print("Ошибка:", e)
    exit(1)
