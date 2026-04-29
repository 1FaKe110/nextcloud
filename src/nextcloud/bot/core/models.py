# models.py
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List, Dict, Any, Union
from io import BytesIO
import os


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
class File:
    """
    Объект файла (аналог telegram.File)
    Представляет файл, прикрепленный к сообщению
    """
    file_id: str  # ID файла в Nextcloud
    file_name: str
    file_size: int
    mime_type: str
    file_path: str  # Путь на сервере
    download_url: Optional[str] = None
    direct_link: Optional[str] = None

    def __post_init__(self):
        # Создаем удобные алиасы
        self.id = self.file_id
        self.name = self.file_name
        self.size = self.file_size

    @property
    def is_image(self) -> bool:
        """Проверка, является ли файл изображением"""
        return self.mime_type.startswith('image/')

    @property
    def is_video(self) -> bool:
        """Проверка, является ли файл видео"""
        return self.mime_type.startswith('video/')

    @property
    def is_audio(self) -> bool:
        """Проверка, является ли файл аудио"""
        return self.mime_type.startswith('audio/')

    @property
    def is_document(self) -> bool:
        """Проверка, является ли файл документом (PDF, DOC, и т.д.)"""
        document_types = ['application/pdf', 'application/msword',
                          'application/vnd.openxmlformats-officedocument',
                          'text/plain']
        return any(self.mime_type.startswith(dt) for dt in document_types)

    def __repr__(self) -> str:
        return f"<File: {self.file_name} ({self.file_size} bytes, {self.mime_type})>"


@dataclass
class Audio:
    """
    Аудио файл (специализированный класс для аудио)
    """
    file_id: str
    file_name: str
    file_size: int
    mime_type: str
    duration: Optional[int] = None  # Длительность в секундах (есть в API)
    title: Optional[str] = None
    performer: Optional[str] = None

    @property
    def id(self) -> str:
        return self.file_id


@dataclass
class Video:
    """
    Видео файл (специализированный класс для видео)
    """
    file_id: str
    file_name: str
    file_size: int
    mime_type: str
    width: Optional[int] = None
    height: Optional[int] = None
    duration: Optional[int] = None

    @property
    def id(self) -> str:
        return self.file_id


@dataclass
class Document:
    """
    Документ (специализированный класс для документов)
    """
    file_id: str
    file_name: str
    file_size: int
    mime_type: str
    file_path: str

    @property
    def id(self) -> str:
        return self.file_id

    @property
    def is_pdf(self) -> bool:
        return self.mime_type == 'application/pdf'

    @property
    def is_text(self) -> bool:
        return self.mime_type == 'text/plain'


@dataclass
class Photo:
    """
    Фото (специализированный класс для изображений)
    """
    file_id: str
    file_name: str
    file_size: int
    mime_type: str
    width: Optional[int] = None
    height: Optional[int] = None

    @property
    def id(self) -> str:
        return self.file_id


@dataclass
class Message:
    """
    Объект сообщения (аналог telegram.Message) с поддержкой файлов
    """
    message_id: int
    text: str
    from_user: User
    chat: Chat
    date: datetime
    reply_to_message: Optional['Message'] = None
    _bot: 'Bot' = None  # type: ignore

    # Новые поля для поддержки файлов
    files: List[File] = field(default_factory=list)  # Все вложенные файлы
    audio: Optional[Audio] = None  # Первое аудио (если есть)
    video: Optional[Video] = None  # Первое видео (если есть)
    document: Optional[Document] = None  # Первый документ (если есть)
    photo: Optional[Photo] = None  # Первое фото (если есть)

    # Системные поля
    is_forwarded: bool = False  # Переслано ли сообщение
    forward_origin: Optional[str] = None  # Откуда переслано
    is_reply: bool = False  # Является ли ответом

    def __post_init__(self):
        # Автоматически определяем типы файлов
        self._classify_files()
        self.is_reply = self.reply_to_message is not None

    def _classify_files(self):
        """Классифицирует файлы по типам для удобного доступа"""
        for file in self.files:
            if file.is_audio and not self.audio:
                self.audio = Audio(
                    file_id=file.file_id,
                    file_name=file.file_name,
                    file_size=file.file_size,
                    mime_type=file.mime_type
                )
            elif file.is_video and not self.video:
                self.video = Video(
                    file_id=file.file_id,
                    file_name=file.file_name,
                    file_size=file.file_size,
                    mime_type=file.mime_type
                )
            elif file.is_image and not self.photo:
                self.photo = Photo(
                    file_id=file.file_id,
                    file_name=file.file_name,
                    file_size=file.file_size,
                    mime_type=file.mime_type
                )
            elif not self.document and not (file.is_audio or file.is_video or file.is_image):
                self.document = Document(
                    file_id=file.file_id,
                    file_name=file.file_name,
                    file_size=file.file_size,
                    mime_type=file.mime_type,
                    file_path=file.file_path
                )

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

    @property
    def has_files(self) -> bool:
        """Есть ли файлы в сообщении"""
        return len(self.files) > 0

    @property
    def has_media(self) -> bool:
        """Есть ли медиа (фото, видео, аудио) в сообщении"""
        return self.photo is not None or self.video is not None or self.audio is not None

    def get_file(self, index: int = 0) -> Optional[File]:
        """
        Получить файл по индексу

        Args:
            index: Индекс файла (0 - первый, 1 - второй и т.д.)

        Returns:
            File объект или None
        """
        if 0 <= index < len(self.files):
            return self.files[index]
        return None

    def get_all_files(self) -> List[File]:
        """Получить все файлы в сообщении"""
        return self.files.copy()

    def download_file(self, index: int = 0, save_path: Optional[str] = None) -> Union[bytes, str, None]:
        """
        Скачать файл из сообщения

        Args:
            index: Индекс файла для скачивания (0 - первый)
            save_path: Путь для сохранения. Если None - вернуть байты

        Returns:
            - Если save_path указан: путь к сохраненному файлу (str)
            - Если save_path не указан: содержимое файла (bytes)
            - None при ошибке

        Examples:
            # Скачать первое вложение в байты
            file_bytes = message.download_file()

            # Сохранить первый файл на диск
            file_path = message.download_file(0, "/path/to/save/file.pdf")

            # Скачать второй файл
            second_file = message.download_file(1)
        """
        if not self._bot:
            return None

        file_obj = self.get_file(index)
        if not file_obj:
            return None

        return self._bot.download_file(file_obj, save_path)

    def download_all_files(self, directory: str) -> List[str]:
        """
        Скачать все файлы из сообщения в указанную директорию

        Args:
            directory: Директория для сохранения файлов

        Returns:
            Список путей к сохраненным файлам
        """
        if not self._bot:
            return []

        saved_paths = []
        for file_obj in self.files:
            file_path = os.path.join(directory, file_obj.file_name)
            result = self._bot.download_file(file_obj, file_path)
            if result:
                saved_paths.append(result)

        return saved_paths

    def download_first_media(self, save_path: Optional[str] = None) -> Union[bytes, str, None]:
        """
        Скачать первое медиа (фото/видео/аудио)

        Returns:
            Байты или путь к файлу
        """
        if self.photo:
            return self.download_file(self.files.index(self.photo) if self.photo in self.files else 0, save_path)
        elif self.video:
            return self.download_file(self.files.index(self.video) if self.video in self.files else 0, save_path)
        elif self.audio:
            return self.download_file(self.files.index(self.audio) if self.audio in self.files else 0, save_path)
        return None

    def reply_text(self, text: str, **kwargs) -> bool:
        """Ответить на сообщение текстом"""
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

    def reply_with_file(self, file_path: str, caption: Optional[str] = None, **kwargs) -> bool:
        """
        Ответить на сообщение, отправив файл

        Args:
            file_path: Путь к файлу
            caption: Подпись к файлу

        Returns:
            True при успехе
        """
        if self._bot:
            return self._bot.send_message(
                chat_id=self.chat.id,
                text=caption,
                file_path=file_path,
                reply_to_message_id=self.message_id,
                **kwargs
            )
        return False

    def __str__(self) -> str:
        base = f"<Message id={self.message_id} from={self.from_user.full_name}>"
        if self.text:
            base += f" text='{self.text[:50]}'"
        if self.files:
            base += f" files={len(self.files)}"
        return base