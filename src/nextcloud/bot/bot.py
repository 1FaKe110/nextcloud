"""
Sync Nextcloud Talk Bot - фасад для синхронного использования.

Наследует BotCore и добавляет:
- Цикл опроса сообщений (polling loop)
- Фоновый поток для поддержания членства
- Контекстный менеджер для управления сессией

Пример использования:
    from nextcloud_talk_bot import Bot

    bot = Bot(
        host="https://nextcloud.example.com",
        user="bot_user",
        password="app-token",
        default_room="room_token"
    )

    @bot.command("start")
    def start(update, context):
        update.message.reply_text("Hello from sync bot!")

    bot.run_polling()
"""

import threading
import time
from typing import Optional, Dict, List, Callable
from loguru import logger

from .http.sync import SyncHTTPClient
from .core.bot_core import BotCore
from .core.models import Update, Message


class Bot(BotCore):
    """
    Синхронный бот для Nextcloud Talk.

    Поддерживает:
    - Режим одной комнаты (по умолчанию)
    - Автоматическое присоединение к комнатам
    - Отправку текста, файлов, фото, документов
    - Обработку команд и сообщений
    """

    def __init__(
            self,
            host: str,
            user: str,
            password: str,
            default_room: str = None,
            read_all_chat: bool = False,
            auto_join_room: bool = True
    ):
        """
        Инициализация синхронного бота.

        Args:
            host: URL Nextcloud сервера (например, https://nextcloud.example.com)
            user: Имя пользователя для авторизации
            password: Пароль пользователя или токен приложения (app-xxxxx)
            default_room: Токен комнаты по умолчанию
            read_all_chat: Если False - не читать историю чата при старте,
                          начинать получать сообщения только с момента запуска.
                          Если True - прочитать всю историю чата.
            auto_join_room: Автоматически присоединяться к комнате при старте
                           и периодически проверять членство
        """
        # Создаём HTTP клиент
        http_client = SyncHTTPClient(host, user, password)

        # Инициализируем ядро бота
        super().__init__(
            http_client=http_client,
            default_room=default_room,
            read_all_chat=read_all_chat,
            auto_join_room=auto_join_room
        )

        # Флаги и потоки
        self.running = False
        self.membership_thread: Optional[threading.Thread] = None
        self.polling_thread: Optional[threading.Thread] = None

        # Проверяем подключение при старте
        if not self.http.check_connection():
            logger.warning("Не удалось проверить подключение к серверу")

        logger.info(f"SyncBot инициализирован, пользователь: {self.http.user}")

    def _ensure_bot_in_room(self, chat_id: str, room_name: str = None) -> bool:
        """
        Полная проверка доступа бота к комнате.

        Args:
            chat_id: Токен комнаты
            room_name: Имя комнаты (для поиска если токен не работает)

        Returns:
            True если бот может отправлять сообщения
        """
        logger.info(f"Проверка доступа к комнате {chat_id}...")

        # 1. Проверяем, существует ли комната
        room_info = self.get_room_info(chat_id)
        if not room_info:
            logger.error(f"Комната {chat_id} не найдена")

            # Пробуем найти по имени
            if room_name:
                logger.info(f"Пробуем найти комнату по имени: {room_name}")
                rooms = self.get_rooms()
                for room in rooms:
                    if room.get('name') == room_name:
                        actual_chat_id = room.get('token')
                        logger.info(f"Найдена комната {room_name} с токеном {actual_chat_id}")
                        chat_id = actual_chat_id
                        room_info = room
                        break

            if not room_info:
                return False

        # 2. Убеждаемся, что бот участник
        if not self.ensure_room_membership(chat_id):
            logger.error(f"Не удалось добавить бота в комнату {chat_id}")
            return False

        logger.success(f"Бот успешно добавлен в комнату и может отправлять сообщения")
        return True

    def _maintain_membership_loop(self, chat_id: str):
        """
        Фоновый поток для поддержания членства в комнате.

        Args:
            chat_id: Токен комнаты
        """
        logger.debug(f"Запущен поток поддержания членства для комнаты {chat_id}")

        while self.running:
            time.sleep(300)  # Каждые 5 минут
            if self.running and self.auto_join_room:
                try:
                    self.ensure_room_membership(chat_id)
                except Exception as e:
                    logger.error(f"Ошибка в потоке поддержания членства: {e}")

        logger.debug(f"Поток поддержания членства для комнаты {chat_id} остановлен")

    def _polling_loop(self, chat_id: str, poll_interval: float = 2):
        """
        Основной цикл опроса сообщений.

        Args:
            chat_id: Токен комнаты
            poll_interval: Интервал опроса в секундах
        """
        logger.info(f"Запущен цикл опроса для комнаты {chat_id}")

        while self.running:
            try:
                new_messages = self.get_new_messages(chat_id, limit=100)

                for msg_data in new_messages:
                    self._process_update(msg_data, chat_id)

                time.sleep(poll_interval)

            except KeyboardInterrupt:
                logger.info("Получен сигнал остановки")
                self.running = False
                break
            except Exception as e:
                logger.error(f"Ошибка в цикле опроса: {e}")
                time.sleep(poll_interval * 2)  # Пауза подольше при ошибке

        logger.debug(f"Цикл опроса для комнаты {chat_id} остановлен")

    def run_polling(self, chat_id: str = None, poll_interval: float = 2):
        """
        Запустить бота в режиме пулинга (синхронный, блокирующий).

        Args:
            chat_id: ID комнаты (room_token). Если не указан, используется default_room
            poll_interval: Интервал опроса в секундах
        """
        # Определяем целевую комнату
        target_room = chat_id or self.default_room
        if not target_room:
            raise ValueError("Не указан chat_id и не задан default_room")

        # Убеждаемся, что бот в комнате
        if self.auto_join_room:
            if not self._ensure_bot_in_room(target_room):
                logger.error("Не удалось обеспечить доступ к комнате, бот не может работать")
                return

        logger.info(f"Бот запущен в комнате {target_room}")

        if self.read_all_chat:
            logger.info("Режим: читаем всю историю чата (от старых к новым)")
        else:
            logger.info("Режим: читаем только новые сообщения (история пропускается)")

        if self.last_message_id.get(target_room):
            logger.info(f"Продолжаем с сообщения ID: {self.last_message_id[target_room]}")

        logger.info("Нажми Ctrl+C для остановки\n")

        self.running = True

        # Запускаем фоновый поток для поддержания членства
        if self.auto_join_room:
            self.membership_thread = threading.Thread(
                target=self._maintain_membership_loop,
                args=(target_room,),
                daemon=True
            )
            self.membership_thread.start()

        # Запускаем основной цикл опроса (блокирующий)
        try:
            self._polling_loop(target_room, poll_interval)
        except KeyboardInterrupt:
            logger.info("\n👋 Бот остановлен")
        finally:
            self.running = False
            if self.membership_thread and self.membership_thread.is_alive():
                self.membership_thread.join(timeout=2)
            self.stop()

    def stop(self):
        """Остановка бота и закрытие соединений."""
        logger.info("Остановка бота...")
        self.running = False

        # Закрываем HTTP сессию
        if self.http:
            self.http.close()

        logger.info("Бот остановлен")

    # ========== Удобные методы для работы с сообщениями ==========

    def reply_to(self, message: Message, text: str, **kwargs) -> bool:
        """
        Ответить на сообщение.

        Args:
            message: Сообщение, на которое отвечаем
            text: Текст ответа
            **kwargs: Дополнительные параметры для send_message

        Returns:
            True если успешно отправлено
        """
        return self.send_message(
            chat_id=message.chat.id,
            text=text,
            reply_to_message_id=message.message_id,
            **kwargs
        )

    def send_typing(self, chat_id: str = None):
        """
        Отправить индикатор набора текста (не поддерживается в Talk API).
        Метод оставлен для совместимости с Telegram API.
        """
        logger.warning("send_typing не поддерживается в Nextcloud Talk API")
        return False

    # ========== Контекстный менеджер ==========

    def __enter__(self):
        """Вход в контекстный менеджер."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Выход из контекстного менеджера - закрываем соединения."""
        self.stop()

    # ========== Диагностика ==========

    def get_bot_info(self) -> dict:
        """
        Получить информацию о боте.

        Returns:
            Словарь с информацией о боте
        """
        status = self.check_session_status()
        return {
            'user': self.http.user,
            'host': self.http.host,
            'authenticated': status.get('authenticated', False),
            'default_room': self.default_room,
            'read_all_chat': self.read_all_chat,
            'auto_join_room': self.auto_join_room,
            'rooms_count': len(self.get_rooms()) if status.get('authenticated') else 0
        }