"""
Nextcloud Talk Bot — библиотека для создания ботов в Nextcloud Talk.
Поддерживает как синхронный, так и асинхронный режимы работы.
"""

# Синхронная версия (по умолчанию, для обратной совместимости)
from .bot.bot import Bot
from .bot.core.models import (
    Update, Message, User, Chat, File,
    Audio, Video, Document, Photo
)

# Асинхронная версия (новая)
from .bot.async_bot import AsyncBot

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