"""Nextcloud Python SDK"""

from nextcloud.bot import Bot
from nextcloud.bot.async_bot import AsyncBot
from nextcloud.bot.core.models import (
    Update, Message, User, Chat, File,
    Audio, Video, Document, Photo
)


# Экспортируем все классы
__version__ = "0.2.0"
__all__ = [
    "Bot",           # синхронная версия (по умолчанию)
    "AsyncBot",      # асинхронная версия (явно)
    "Update",
    "Message",
    "User",
    "Chat",
    "File",
    "Audio",
    "Video",
    "Document",
    "Photo"
]