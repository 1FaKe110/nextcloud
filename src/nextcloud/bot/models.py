# models.py
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


class Update:
    """Объект обновления (аналог telegram.Update)"""

    def __init__(self, message: 'Message', update_id: int):
        self.message = message
        self.update_id = update_id
        self.effective_message = message
        self.effective_user = message.from_user
        self.effective_chat = message.chat


@dataclass
class User:
    """Объект пользователя (аналог telegram.User)"""
    id: str
    first_name: str
    last_name: Optional[str] = None
    username: Optional[str] = None

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}" if self.last_name else self.first_name

    def __str__(self):
        return self.full_name


@dataclass
class Chat:
    """Объект чата (аналог telegram.Chat)"""
    id: str  # room_token
    title: Optional[str] = None
    type: str = "group"  # group, private, supergroup

    @property
    def token(self) -> str:
        return self.id


@dataclass
class Message:
    """Объект сообщения (аналог telegram.Message)"""
    message_id: int
    text: str
    from_user: User
    chat: Chat
    date: datetime
    reply_to_message: Optional['Message'] = None
    _bot: 'Bot' = None  # type: ignore

    # Алиасы для совместимости с оригинальным кодом
    @property
    def id(self) -> int:
        return self.message_id

    @property
    def actor_id(self) -> str:
        return self.from_user.id

    @property
    def actor_name(self) -> str:
        return self.from_user.full_name

    @property
    def room_token(self) -> str:
        return self.chat.id

    @property
    def timestamp(self) -> int:
        return int(self.date.timestamp())

    def reply_text(self, text: str, **kwargs) -> bool:
        """Ответить на сообщение (аналог message.reply_text)"""
        if self._bot:
            return self._bot.send_message(
                chat_id=self.chat.id,
                text=text,
                reply_to_message_id=self.message_id,
                **kwargs
            )
        return False

    def reply(self, text: str) -> bool:
        """Алиас для reply_text"""
        return self.reply_text(text)