#!/usr/bin/env python3
"""Проверка BOT_TOKEN из apibot.env через Telegram API getMe."""
import os
import socket
from dotenv import load_dotenv

_BOT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_BOT_DIR, "apibot.env"), override=True)
load_dotenv(os.path.join(_BOT_DIR, ".env"), override=False)
token = (os.getenv("BOT_TOKEN") or "").strip()

if not token:
    print("BOT_TOKEN не найден в apibot.env")
    exit(1)

# Проверка формата: обычно 46 символов, есть двоеточие
if ":" not in token or len(token) < 40:
    print("Токен похож на неверный (должен быть вида 123456789:ABC...). Длина:", len(token))
    exit(1)

print("Токен загружен, длина:", len(token))

# Как в bot.py: IPv4 по умолчанию (Errno 101 на хостах без IPv6)
force_ipv4 = (os.getenv("TELEGRAM_FORCE_IPV4") or "1").strip().lower()
if force_ipv4 not in ("0", "false", "no", "off"):
    _orig = socket.getaddrinfo

    def _ipv4(host, port, family=0, type=0, proto=0, flags=0):
        return _orig(host, port, socket.AF_INET, type, proto, flags)

    socket.getaddrinfo = _ipv4  # type: ignore[assignment]
    print("Режим только IPv4 включён")

proxy_url = (
    os.getenv("TELEGRAM_PROXY")
    or os.getenv("TELEGRAM_HTTPS_PROXY")
    or os.getenv("HTTPS_PROXY")
    or os.getenv("https_proxy")
    or ""
).strip()
if proxy_url:
    print("Прокси:", proxy_url.split("@")[-1] if "@" in proxy_url else proxy_url)

try:
    from telegram import Bot
    from telegram.utils.request import Request

    request = Request(proxy_url=proxy_url) if proxy_url else Request()
    bot = Bot(token=token, request=request)
    me = bot.get_me()
    print("OK. Бот подключён:", me.username)
except Exception as e:
    if "401" in str(e) or "Unauthorized" in str(type(e).__name__):
        print("Ошибка: Telegram отклонил токен (неверный или отозван).")
        print("Получите новый токен: @BotFather → /mybots → ваш бот → API Token → Revoke → скопируйте новый токен в apibot.env")
    elif "unreachable" in str(e).lower() or "Failed to establish" in str(e):
        print("Ошибка сети: хост не достучится до api.telegram.org.")
        print("Задайте TELEGRAM_PROXY (HTTP/SOCKS5) или проверьте сеть/файрвол на сервере.")
        print("Детали:", e)
    else:
        print("Ошибка:", e)
    exit(1)
