"""
Nextcloud Talk Bot — библиотека для создания ботов в Nextcloud Talk.
"""

from nextcloud.bot.bot import Bot
from nextcloud.bot.models import Update, Message, User, Chat

__version__ = "0.1.0"
__all__ = ["Bot", "Update", "Message", "User", "Chat"]