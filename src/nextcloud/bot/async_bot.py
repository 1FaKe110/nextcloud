"""
Async Nextcloud Talk Bot - фасад для асинхронного использования.

Наследует BotCore и добавляет:
- Асинхронный цикл опроса сообщений
- Поддержку нескольких комнат одновременно (multi-room mode)
- Асинхронные фоновые задачи для поддержания членства
- Синхронизацию комнат в реальном времени

Compatible with Nextcloud 33.x and later versions.

Пример использования (single-room):
    from nextcloud_talk_bot import AsyncBot
    import asyncio

    bot = AsyncBot(
        host="https://nextcloud.example.com",
        user="bot_user",
        password="app-token",
        default_room="room_token"
    )

    @bot.command("start")
    async def start(update, context):
        await update.message.reply_text("Hello from async bot!")

    asyncio.run(bot.run_polling())

Пример использования (multi-room):
    bot = AsyncBot(
        host="https://nextcloud.example.com",
        user="bot_user",
        password="app-token",
        listen_all_rooms=True,  # Включаем мульти-рум режим
        max_concurrent_rooms=50
    )

    @bot.command("help")
    async def help_command(update, context):
        await update.message.reply_text("I'm available in all rooms!")

    asyncio.run(bot.run_polling())  # Без указания комнаты
"""

import asyncio
import os
import mimetypes
import time
import traceback
from typing import Optional, Dict, List, Set, Callable, Union, Tuple
from loguru import logger

from .http.async_ import AsyncHTTPClient
from .core.bot_core import BotCore
from .core.models import Update, Message, File


class AsyncBot(BotCore):
    """
    Асинхронный бот для Nextcloud Talk.

    Поддерживает:
    - Single-room режим (одна комната)
    - Multi-room режим (все комнаты, где состоит бот)
    - Автоматическое присоединение к новым комнатам
    - Отправку текста, файлов, фото, документов
    - Обработку команд и сообщений (как async, так и sync обработчики)
    """

    def __init__(
        self,
        host: str,
        user: str,
        password: str,
        default_room: str = None,
        read_all_chat: bool = False,
        auto_join_room: bool = True,
        listen_all_rooms: bool = False,
        max_concurrent_rooms: int = 50
    ):
        """
        Инициализация асинхронного бота.

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
            listen_all_rooms: Если True - слушать все комнаты, где состоит бот.
                             Если False - только указанную комнату.
            max_concurrent_rooms: Максимальное количество комнат для одновременного опроса
        """
        # Сохраняем параметры multi-room режима
        self.listen_all_rooms = listen_all_rooms
        self.max_concurrent_rooms = max_concurrent_rooms

        # Создаём асинхронный HTTP клиент
        self._http_client = AsyncHTTPClient(host, user, password)

        # Инициализируем ядро бота
        super().__init__(
            http_client=self._http_client,
            default_room=default_room,
            read_all_chat=read_all_chat,
            auto_join_room=auto_join_room
        )

        # Состояние для multi-room режима
        self.running = False
        self.active_rooms: Set[str] = set()  # Комнаты, которые сейчас слушаем
        self.room_tasks: Dict[str, asyncio.Task] = {}  # {room_token: polling_task}

        # Фоновые задачи
        self.sync_task: Optional[asyncio.Task] = None
        self.membership_task: Optional[asyncio.Task] = None

        logger.info(f"AsyncBot инициализирован, режим: {'multi-room' if listen_all_rooms else 'single-room'}")

    # ========== Инициализация и проверки ==========

    async def _ensure_session(self):
        """Убедиться, что HTTP сессия создана."""
        if not self._http_client._session:
            await self._http_client._init_session()

    async def _ensure_bot_in_room(self, chat_id: str, room_name: str = None) -> bool:
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
        room_info = await self.get_room_info(chat_id)
        if not room_info:
            logger.error(f"Комната {chat_id} не найдена")

            # Пробуем найти по имени
            if room_name:
                logger.info(f"Пробуем найти комнату по имени: {room_name}")
                rooms = await self.get_rooms()
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
        if not await self.ensure_room_membership(chat_id):
            logger.error(f"Не удалось добавить бота в комнату {chat_id}")
            return False

        logger.success(f"Бот успешно добавлен в комнату и может отправлять сообщения")
        return True

    # ========== Асинхронные переопределения методов BotCore ==========

    async def _get_current_message_id_async(self, chat_id: str) -> int:
        """Асинхронно получить ID последнего сообщения в чате."""
        response = await self.http.get(
            f"/ocs/v2.php/apps/spreed/api/v1/chat/{chat_id}",
            params={'limit': 1, 'lookIntoFuture': 0}
        )

        if not response.data:
            return 0

        messages = response.data if isinstance(response.data, list) else response.data.get('data', [])
        if messages:
            return messages[0].get('id', 0)

        return 0

    async def get_new_messages(self, chat_id: str, limit: int = 100) -> List[Dict]:
        """
        Получить только новые сообщения из комнаты (асинхронная версия).

        Args:
            chat_id: Токен комнаты
            limit: Максимальное количество сообщений

        Returns:
            Список новых сообщений
        """
        response = await self.http.get(
            f"/ocs/v2.php/apps/spreed/api/v1/chat/{chat_id}",
            params={'limit': limit, 'lookIntoFuture': 0}
        )

        if not response.data:
            return []

        messages = response.data if isinstance(response.data, list) else response.data.get('data', [])
        if not messages:
            return []

        last_id = self.last_message_id.get(chat_id, 0)

        # При первом запуске и read_all_chat=False - начинаем с текущего момента
        if last_id == 0 and not self.read_all_chat:
            last_id = await self._get_current_message_id_async(chat_id)
            if last_id > 0:
                self.last_message_id[chat_id] = last_id
                logger.info(f"Комната {chat_id}: пропускаем историю, начинаем с ID: {last_id}")
                return []

        # Фильтруем новые сообщения
        new_messages = [msg for msg in messages if msg.get('id', 0) > last_id]

        if new_messages:
            max_id = max(msg.get('id', 0) for msg in new_messages)
            self.last_message_id[chat_id] = max_id
            logger.debug(f"Комната {chat_id}: найдено {len(new_messages)} новых сообщений (последний ID: {max_id})")

        return new_messages

    async def join_room(self, chat_id: str, password: str = None) -> bool:
        """
        Присоединиться к комнате (асинхронная версия).

        Args:
            chat_id: Токен комнаты
            password: Пароль комнаты (если требуется)

        Returns:
            True если успешно присоединился
        """
        try:
            endpoint = f"/ocs/v2.php/apps/spreed/api/v4/room/{chat_id}/participants/active"

            data = {}
            if password:
                data['password'] = password

            response = await self.http.post(endpoint, data=data)

            if response.data is not None:
                logger.info(f"Бот присоединился к комнате {chat_id}")
                return True
            else:
                logger.debug(f"Не удалось присоединиться к комнате {chat_id}")
                return False

        except Exception as e:
            logger.error(f"Ошибка при присоединении к комнате {chat_id}: {e}")
            return False

    async def ensure_room_membership(self, chat_id: str) -> bool:
        """
        Проверить и обеспечить членство бота в комнате (асинхронная версия).

        Args:
            chat_id: Токен комнаты

        Returns:
            True если бот является участником
        """
        try:
            endpoint = f"/ocs/v2.php/apps/spreed/api/v4/room/{chat_id}/participants"
            response = await self.http.get(endpoint)

            if response.data:
                participants = response.data if isinstance(response.data, list) else response.data.get('data', [])

                for participant in participants:
                    actor_id = participant.get('actorId', '')
                    user_id = participant.get('userId', '')
                    session_id = participant.get('sessionId', '')

                    if (actor_id == self.http.user or user_id == self.http.user) and session_id != '0':
                        logger.trace(f"Бот уже участник комнаты {chat_id}")
                        return True

                    if actor_id == self.http.user or user_id == self.http.user:
                        logger.info(f"Бот в комнате {chat_id} без активной сессии, присоединяемся...")
                        return await self.join_room(chat_id)

                logger.info(f"Бот не участник комнаты {chat_id}, присоединяемся...")
                return await self.join_room(chat_id)
            else:
                logger.warning(f"Не удалось получить участников комнаты {chat_id}, пробуем присоединиться...")
                return await self.join_room(chat_id)

        except Exception as e:
            logger.error(f"Ошибка проверки членства в комнате {chat_id}: {e}")
            return False

    async def get_rooms(self) -> List[Dict]:
        """Получить список всех комнат, доступных боту (асинхронная версия)."""
        response = await self.http.get("/ocs/v2.php/apps/spreed/api/v4/room")
        if response.data:
            return response.data if isinstance(response.data, list) else response.data.get('data', [])
        return []

    async def get_room_info(self, chat_id: str) -> dict:
        """Получить информацию о комнате (асинхронная версия)."""
        response = await self.http.get(f"/ocs/v2.php/apps/spreed/api/v4/room/{chat_id}")
        return response.data or {}

    async def send_message(
        self,
        chat_id: str = None,
        text: str = None,
        reply_to_message_id: int = None,
        parse_mode: str = None,
        ensure_membership: bool = True,
        file_path: str = None,
        file_content: bytes = None,
        file_name: str = None,
        file_url: str = None,
        mime_type: str = None,
        **kwargs
    ) -> bool:
        """
        Отправить сообщение с возможностью прикрепления файла (асинхронная версия).

        Returns:
            True если успешно отправлено
        """
        # Используем default_room если chat_id не указан
        if chat_id is None:
            chat_id = self.default_room
            if chat_id is None:
                logger.error("Не указан chat_id и не задан default_room")
                return False

        # Проверяем членство если нужно
        if ensure_membership:
            if not await self.ensure_room_membership(chat_id):
                logger.error(f"Не удалось обеспечить членство в комнате {chat_id}")
                return False

        # Подготовка файла для отправки
        file_to_send = None

        try:
            if file_path and os.path.exists(file_path):
                file_name = file_name or os.path.basename(file_path)
                mime_type = mime_type or mimetypes.guess_type(file_path)[0] or 'application/octet-stream'
                with open(file_path, 'rb') as f:
                    file_content = f.read()
                file_to_send = (file_name, file_content, mime_type)

            elif file_content:
                if not file_name:
                    file_name = f"file_{int(time.time())}"
                mime_type = mime_type or 'application/octet-stream'
                file_to_send = (file_name, file_content, mime_type)

            # Если есть файл - отправляем с файлом
            if file_to_send:
                return await self._send_message_with_file_async(
                    chat_id=chat_id,
                    text=text,
                    file=file_to_send,
                    reply_to_message_id=reply_to_message_id
                )

            # Если есть URL - отправляем ссылку
            elif file_url:
                file_display_name = file_name or 'файл'
                if text:
                    message_text = f"{text}\n\n📎 [{file_display_name}]({file_url})"
                else:
                    message_text = f"📎 [{file_display_name}]({file_url})"
                return await self._send_text_message_async(chat_id, message_text, reply_to_message_id)

            # Иначе просто текст
            else:
                if not text:
                    logger.warning("Нет текста и файла для отправки")
                    return False
                return await self._send_text_message_async(chat_id, text, reply_to_message_id)

        except Exception as e:
            logger.error(f"Ошибка при отправке сообщения: {e}")
            return False

    async def _send_text_message_async(self, chat_id: str, text: str, reply_to_message_id: int = None) -> bool:
        """Отправить только текстовое сообщение (асинхронная версия)."""
        if not text:
            logger.warning("Нет текста для отправки")
            return False

        endpoint = f"/ocs/v2.php/apps/spreed/api/v1/chat/{chat_id}"
        data = {'message': text}
        if reply_to_message_id:
            data['replyTo'] = reply_to_message_id

        params = {'lookIntoFuture': 0, 'setReadMarker': 0}

        # params теперь поддерживается
        response = await self.http.post(endpoint, data=data, params=params)

        if response.data is not None and response.data != {}:
            logger.success(f"Отправка текстового сообщения в {chat_id}: {text[:50]}...")
            return True

        # Fallback: пробуем без параметров
        response = await self.http.post(endpoint, data=data)
        if response.data is not None and response.data != {}:
            logger.success("Сообщение успешно отправлено (без параметров)")
            return True

        logger.error("Не удалось отправить текстовое сообщение")
        return False

    async def _send_message_with_file_async(
        self,
        chat_id: str,
        text: str,
        file: Tuple[str, bytes, str],
        reply_to_message_id: int = None
    ) -> bool:
        """
        Отправить сообщение с файлом-вложением (асинхронная версия).

        Args:
            file: Кортеж (filename, content, mime_type)
        """
        file_name, file_content, mime_type = file
        logger.info(f"📤 Отправка файла {file_name} в комнату {chat_id}")

        try:
            # ШАГ 1: Создаём директорию для файлов бота
            webdav_dir = f"/remote.php/dav/files/{self.http.user}/Talk"
            await self.http.mkcol(webdav_dir)  # Игнорируем ошибку если существует

            # ШАГ 2: Загружаем файл через WebDAV
            webdav_url = f"{webdav_dir}/{file_name}"
            headers = {'Content-Type': mime_type}

            response = await self.http.put(webdav_url, data=file_content, headers=headers)
            if response.status_code not in [200, 201, 204]:
                logger.error(f"Ошибка загрузки файла через WebDAV: {response.status_code}")
                return False

            logger.info(f"Файл {file_name} загружен на сервер")

            # ШАГ 3: Получаем ID файла
            file_id = await self._get_file_id_async(file_name)

            if file_id and file_id != "unknown":
                share_url = await self._create_public_share_async(file_id, file_name)
                if share_url:
                    file_link = share_url
                    logger.info(f"Создана публичная ссылка: {share_url}")
                else:
                    file_link = f"{self.http.host}/index.php/f/{file_id}"
                    logger.warning(f"Используем прямую ссылку: {file_link}")
            else:
                file_link = f"{self.http.host}{webdav_url}"
                logger.warning(f"Не удалось получить ID файла, используем WebDAV ссылку")

            # ШАГ 4: Отправляем сообщение со ссылкой
            if text:
                message_text = f"{text}\n\n📎 [{file_name}]({file_link})"
            else:
                message_text = f"📎 [{file_name}]({file_link})"

            return await self._send_text_message_async(chat_id, message_text, reply_to_message_id)

        except Exception as e:
            logger.error(f"Ошибка при отправке файла: {e}")
            return False

    async def _get_file_id_async(self, file_name: str) -> str:
        """
        Получить ID загруженного файла через WebDAV PROPFIND (асинхронная версия).

        Returns:
            ID файла или "unknown"
        """
        webdav_url = f"/remote.php/dav/files/{self.http.user}/Talk/{file_name}"

        propfind_body = '''<?xml version="1.0"?>
<d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns" xmlns:nc="http://nextcloud.org/ns">
  <d:prop>
    <oc:fileid />
    <oc:size />
    <d:getlastmodified />
  </d:prop>
</d:propfind>'''

        try:
            response = await self.http.propfind(webdav_url, propfind_body)

            if response.status_code == 207:  # Multi-status
                import xml.etree.ElementTree as ET
                root = ET.fromstring(response.raw_text)

                namespaces = {
                    'd': 'DAV:',
                    'oc': 'http://owncloud.org/ns',
                    'nc': 'http://nextcloud.org/ns'
                }

                fileid_elem = root.find('.//oc:fileid', namespaces)
                if fileid_elem is not None and fileid_elem.text:
                    return fileid_elem.text

                fileid_elem = root.find('.//nc:fileid', namespaces)
                if fileid_elem is not None and fileid_elem.text:
                    return fileid_elem.text

                return "unknown"
            else:
                return "unknown"

        except Exception as e:
            logger.error(f"Ошибка при получении ID файла {file_name}: {e}")
            return "unknown"

    async def _create_public_share_async(self, file_id: str, file_name: str, password: str = None) -> Optional[str]:
        """
        Создать публичную ссылку на файл через Sharing API (асинхронная версия).

        Returns:
            URL публичной ссылки или None
        """
        if not file_id or file_id == "unknown":
            return None

        endpoint = "/ocs/v2.php/apps/files_sharing/api/v1/shares"

        data = {
            'shareType': 3,
            'path': f"/Talk/{file_name}",
            'permissions': 1,
            'name': f"Shared: {file_name}"
        }

        if password:
            data['password'] = password

        try:
            response = await self.http.post(endpoint, data=data)

            if response.data and 'url' in response.data:
                return response.data['url']
            else:
                logger.warning(f"Не удалось создать публичную ссылку для {file_name}")
                return None

        except Exception as e:
            logger.error(f"Ошибка при создании публичной ссылки для {file_name}: {e}")
            return None

    async def download_file_async(self, file_obj: File, save_path: Optional[str] = None) -> Union[bytes, str, None]:
        """
        Скачать файл с сервера (асинхронная версия).

        Args:
            file_obj: File объект из сообщения
            save_path: Путь для сохранения. Если None - вернуть байты

        Returns:
            Байты или путь к файлу
        """
        try:
            # Пробуем через WebDAV
            file_url = f"/remote.php/dav/files/{self.http.user}/{file_obj.file_path}"
            response = await self.http.get(file_url)

            if response.status_code == 200:
                content = response.raw_text.encode() if isinstance(response.raw_text, str) else response.raw_text
                if save_path:
                    os.makedirs(os.path.dirname(save_path), exist_ok=True)
                    with open(save_path, 'wb') as f:
                        f.write(content if isinstance(content, bytes) else content.encode())
                    return save_path
                return content if isinstance(content, bytes) else content.encode()

            # Пробуем прямую ссылку
            if file_obj.download_url:
                response = await self.http.get(file_obj.download_url)
                if response.status_code == 200:
                    content = response.raw_text.encode() if isinstance(response.raw_text, str) else response.raw_text
                    if save_path:
                        with open(save_path, 'wb') as f:
                            f.write(content if isinstance(content, bytes) else content.encode())
                        return save_path
                    return content if isinstance(content, bytes) else content.encode()

            logger.error(f"Не удалось скачать файл {file_obj.file_name}")
            return None

        except Exception as e:
            logger.error(f"Ошибка при скачивании файла {file_obj.file_name}: {e}")
            return None

    # ========== Асинхронная обработка обновлений ==========

    async def _process_update_async(self, msg_data: Dict, chat_id: str):
        """
        Асинхронная обработка обновления (вызов обработчиков).

        Args:
            msg_data: Данные сообщения из API
            chat_id: ID чата
        """
        message = self._create_message_object(msg_data, chat_id)

        # Игнорируем свои сообщения
        if message.from_user.id == self.http.user:
            logger.trace("Игнорируем своё сообщение")
            return

        logger.info(f"[{chat_id}] {message.from_user.full_name}: {message.text[:50] if message.text else '(файл)'}")
        update = Update(message, message.message_id)

        # Обработка команд
        if message.text and message.text.startswith('/'):
            parts = message.text.split()
            command = parts[0][1:].lower()
            args = parts[1:] if len(parts) > 1 else []

            if command in self.command_handlers:
                try:
                    handler = self.command_handlers[command]
                    context = self._create_context()

                    if asyncio.iscoroutinefunction(handler):
                        await handler(update, context)
                    else:
                        # Запускаем синхронный обработчик в потоке
                        loop = asyncio.get_event_loop()
                        await loop.run_in_executor(None, handler, update, context)

                except Exception as e:
                    logger.error(f"Ошибка в обработчике команды /{command}: {e}")
                    logger.error(traceback.format_exc())
                return

        # Общие обработчики сообщений
        for handler in self.handlers:
            if handler['type'] == 'message':
                try:
                    callback = handler['callback']
                    context = self._create_context()

                    if asyncio.iscoroutinefunction(callback):
                        await callback(update, context)
                    else:
                        loop = asyncio.get_event_loop()
                        await loop.run_in_executor(None, callback, update, context)

                except Exception as e:
                    logger.error(f"Ошибка в обработчике: {e}")
                    logger.error(traceback.format_exc())

    # ========== Циклы опроса для single-room режима ==========

    async def _poll_room(self, chat_id: str, poll_interval: float = 2):
        """
        Асинхронный цикл опроса одной комнаты.

        Args:
            chat_id: Токен комнаты
            poll_interval: Интервал опроса в секундах
        """
        logger.info(f"Начинаем опрос комнаты {chat_id}")

        # Убеждаемся, что бот участник комнаты
        if self.auto_join_room:
            await self.ensure_room_membership(chat_id)

        while self.running and chat_id in self.active_rooms:
            try:
                new_messages = await self.get_new_messages(chat_id, limit=100)

                for msg_data in new_messages:
                    await self._process_update_async(msg_data, chat_id)

                await asyncio.sleep(poll_interval)

            except asyncio.CancelledError:
                logger.debug(f"Опрос комнаты {chat_id} отменён")
                break
            except Exception as e:
                logger.error(f"Ошибка при опросе комнаты {chat_id}: {e}")
                logger.error(traceback.format_exc())
                await asyncio.sleep(poll_interval * 2)  # Пауза подольше при ошибке

        logger.info(f"Остановлен опрос комнаты {chat_id}")

    async def run_single_room(self, chat_id: str, poll_interval: float = 2):
        """
        Запустить бота в режиме одной комнаты.

        Args:
            chat_id: ID комнаты (room_token)
            poll_interval: Интервал опроса в секундах
        """
        # Убеждаемся, что сессия создана
        await self._ensure_session()

        # Убеждаемся, что бот в комнате
        if self.auto_join_room:
            if not await self._ensure_bot_in_room(chat_id):
                logger.error("Не удалось обеспечить доступ к комнате, бот не может работать")
                return

        logger.info(f"Бот запущен в комнате {chat_id}")

        if self.read_all_chat:
            logger.info("Режим: читаем всю историю чата (от старых к новым)")
        else:
            logger.info("Режим: читаем только новые сообщения (история пропускается)")

        if self.last_message_id.get(chat_id):
            logger.info(f"Продолжаем с сообщения ID: {self.last_message_id[chat_id]}")

        logger.info("Нажми Ctrl+C для остановки\n")

        self.running = True
        self.active_rooms.add(chat_id)

        # Запускаем фоновую задачу поддержания членства
        self.membership_task = asyncio.create_task(self._maintain_membership_loop_single(chat_id))

        try:
            while self.running:
                new_messages = await self.get_new_messages(chat_id, limit=100)
                for msg_data in new_messages:
                    await self._process_update_async(msg_data, chat_id)
                await asyncio.sleep(poll_interval)
        except asyncio.CancelledError:
            pass
        finally:
            self.running = False
            self.active_rooms.discard(chat_id)
            if self.membership_task:
                self.membership_task.cancel()
                try:
                    await self.membership_task
                except asyncio.CancelledError:
                    pass

    async def _maintain_membership_loop_single(self, chat_id: str):
        """Фоновая задача для поддержания членства в одной комнате."""
        while self.running:
            await asyncio.sleep(300)  # Каждые 5 минут
            if self.running and self.auto_join_room:
                try:
                    await self.ensure_room_membership(chat_id)
                except Exception as e:
                    logger.error(f"Ошибка в потоке поддержания членства: {e}")

    # ========== Циклы опроса для multi-room режима ==========

    async def _start_room_polling(self, chat_id: str, poll_interval: float = 2):
        """
        Запустить опрос для комнаты.

        Args:
            chat_id: Токен комнаты
            poll_interval: Интервал опроса в секундах
        """
        if chat_id in self.room_tasks and not self.room_tasks[chat_id].done():
            logger.debug(f"Комната {chat_id} уже опрашивается")
            return

        self.active_rooms.add(chat_id)
        task = asyncio.create_task(self._poll_room(chat_id, poll_interval))
        self.room_tasks[chat_id] = task
        logger.info(f"Запущен polling для комнаты {chat_id}")

    async def _stop_room_polling(self, chat_id: str):
        """
        Остановить опрос для комнаты.

        Args:
            chat_id: Токен комнаты
        """
        if chat_id in self.room_tasks:
            task = self.room_tasks[chat_id]
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            del self.room_tasks[chat_id]

        self.active_rooms.discard(chat_id)
        logger.info(f"Остановлен polling для комнаты {chat_id}")

    async def _sync_rooms(self, poll_interval: float = 2):
        """
        Синхронизировать список комнат, где состоит бот.
        Обнаруживает новые комнаты и удаляет недоступные.

        Args:
            poll_interval: Интервал опроса для новых комнат
        """
        # Убеждаемся, что сессия создана
        await self._ensure_session()

        try:
            rooms = await self.get_rooms()
            logger.info(f"Синхронизация: найдено {len(rooms)} комнат")

            current_rooms = set()
            for room in rooms:
                room_token = room.get('token')
                if room_token:
                    current_rooms.add(room_token)
                    self.rooms_info[room_token] = room

                    # Если комната новая и мы её ещё не слушаем - добавляем
                    if room_token not in self.active_rooms:
                        if len(self.active_rooms) < self.max_concurrent_rooms:
                            logger.info(f"Обнаружена новая комната: {room.get('name', room_token)}")
                            await self._start_room_polling(room_token, poll_interval)
                        else:
                            logger.warning(
                                f"Достигнут лимит комнат ({self.max_concurrent_rooms}), "
                                f"комната {room_token} не будет обрабатываться"
                            )

            # Удаляем комнаты, которые больше не доступны
            for room_token in list(self.active_rooms):
                if room_token not in current_rooms:
                    logger.info(f"Комната {room_token} больше не доступна, останавливаем polling")
                    await self._stop_room_polling(room_token)

        except Exception as e:
            logger.error(f"Ошибка при синхронизации комнат: {e}")
            logger.error(traceback.format_exc())

    async def _sync_rooms_loop(self, poll_interval: float = 2, sync_interval: int = 60):
        """
        Циклическая синхронизация комнат.

        Args:
            poll_interval: Интервал опроса для новых комнат
            sync_interval: Интервал синхронизации в секундах
        """
        while self.running:
            await asyncio.sleep(sync_interval)
            if self.running:
                await self._sync_rooms(poll_interval)

    async def _maintain_membership_loop_multi(self):
        """Фоновая задача для поддержания членства во всех комнатах."""
        while self.running:
            await asyncio.sleep(300)  # Каждые 5 минут

            if not self.running:
                break

            # Проверяем все активные комнаты
            for room_token in list(self.active_rooms):
                if self.auto_join_room:
                    try:
                        await self.ensure_room_membership(room_token)
                    except Exception as e:
                        logger.error(f"Ошибка проверки членства в {room_token}: {e}")

    async def run_multi_room(self, poll_interval: float = 2, sync_interval: int = 60):
        """
        Запустить бота в режиме множества комнат.

        Args:
            poll_interval: Интервал опроса в секундах для каждой комнаты
            sync_interval: Интервал синхронизации списка комнат в секундах
        """
        logger.info("Запуск multi-room режима")

        # Убеждаемся, что сессия создана
        await self._ensure_session()

        # Проверяем аутентификацию
        status = await self.check_session_status()
        if not status.get('authenticated'):
            logger.error(f"Ошибка аутентификации: {status}")
            logger.error("Проверьте логин и пароль/токен приложения")
            return

        logger.info(f"Аутентификация успешна как: {status.get('user')}")

        # Получаем начальный список комнат
        rooms = await self.get_rooms()
        logger.info(f"Найдено {len(rooms)} доступных комнат")

        if not rooms:
            logger.warning("Не найдено ни одной комнаты. Проверьте:")
            logger.warning("1. Что бот добавлен в комнату")
            logger.warning("2. Правильность токена приложения")
            logger.warning("3. Права доступа бота")

        self.running = True

        # Запускаем опрос для каждой комнаты
        for room in rooms:
            room_token = room.get('token')
            if room_token:
                if len(self.active_rooms) < self.max_concurrent_rooms:
                    await self._start_room_polling(room_token, poll_interval)
                else:
                    logger.warning(
                        f"Достигнут лимит комнат ({self.max_concurrent_rooms}), "
                        f"комната {room_token} не будет обрабатываться"
                    )

        # Запускаем задачи синхронизации и поддержания членства
        self.sync_task = asyncio.create_task(
            self._sync_rooms_loop(poll_interval, sync_interval)
        )
        self.membership_task = asyncio.create_task(self._maintain_membership_loop_multi())

        logger.info(f"Multi-room бот запущен. Опрашивается {len(self.active_rooms)} комнат")
        logger.info("Нажми Ctrl+C для остановки\n")

        try:
            # Ждем, пока не остановят
            while self.running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass

    # ========== Основной метод запуска ==========

    async def run_polling(
        self,
        chat_id: str = None,
        poll_interval: float = 2,
        sync_interval: int = 60
    ):
        """
        Запустить бота в режиме пулинга (асинхронный).

        Args:
            chat_id: ID комнаты (room_token). Если не указан и listen_all_rooms=True,
                     бот будет слушать все доступные комнаты.
            poll_interval: Интервал опроса в секундах для каждой комнаты
            sync_interval: Интервал синхронизации списка комнат в секундах (multi-room режим)
        """
        # Определяем режим работы
        if self.listen_all_rooms and not chat_id:
            await self.run_multi_room(poll_interval, sync_interval)
        else:
            target_room = chat_id or self.default_room
            if not target_room:
                raise ValueError(
                    "Не указан chat_id и не задан default_room, и listen_all_rooms=False"
                )
            await self.run_single_room(target_room, poll_interval)

    async def stop(self):
        """Остановка бота и закрытие всех соединений."""
        logger.info("Остановка бота...")
        self.running = False

        # Останавливаем все задачи опроса комнат
        for task in self.room_tasks.values():
            if not task.done():
                task.cancel()

        if self.room_tasks:
            await asyncio.gather(*self.room_tasks.values(), return_exceptions=True)
        self.room_tasks.clear()
        self.active_rooms.clear()

        # Останавливаем фоновые задачи
        if self.sync_task and not self.sync_task.done():
            self.sync_task.cancel()
            try:
                await self.sync_task
            except asyncio.CancelledError:
                pass

        if self.membership_task and not self.membership_task.done():
            self.membership_task.cancel()
            try:
                await self.membership_task
            except asyncio.CancelledError:
                pass

        # Закрываем HTTP сессию
        await self.http.close()

        logger.info("Бот остановлен")

    # ========== Удобные методы для работы с сообщениями ==========

    async def reply_to(self, message: Message, text: str, **kwargs) -> bool:
        """
        Ответить на сообщение.

        Args:
            message: Сообщение, на которое отвечаем
            text: Текст ответа
            **kwargs: Дополнительные параметры для send_message

        Returns:
            True если успешно отправлено
        """
        return await self.send_message(
            chat_id=message.chat.id,
            text=text,
            reply_to_message_id=message.message_id,
            **kwargs
        )

    # ========== Диагностика ==========

    async def check_session_status(self) -> dict:
        """Проверка состояния сессии."""
        response = await self.http.get("/ocs/v2.php/cloud/user")
        if response.status_code == 200:
            return {
                'authenticated': True,
                'user': response.data.get('display-name', self.http.user)
            }
        else:
            return {
                'authenticated': False,
                'status_code': response.status_code
            }

    async def get_bot_info(self) -> dict:
        """
        Получить информацию о боте.

        Returns:
            Словарь с информацией о боте
        """
        status = await self.check_session_status()
        rooms = await self.get_rooms() if status.get('authenticated') else []

        return {
            'user': self.http.user,
            'host': self.http.host,
            'authenticated': status.get('authenticated', False),
            'default_room': self.default_room,
            'read_all_chat': self.read_all_chat,
            'auto_join_room': self.auto_join_room,
            'listen_all_rooms': self.listen_all_rooms,
            'rooms_count': len(rooms),
            'active_rooms_count': len(self.active_rooms) if self.running else 0
        }

    async def diagnose_room_access(self, chat_id: str, room_name: str = None) -> dict:
        """Диагностика доступа к комнате."""
        results = {
            'room_token': chat_id,
            'bot_user': self.http.user,
            'session': await self.check_session_status()
        }

        # Получаем список комнат
        try:
            rooms = await self.get_rooms()
            results['available_rooms_count'] = len(rooms)
            results['available_rooms'] = [
                {'name': r.get('name'), 'token': r.get('token'), 'type': r.get('type')}
                for r in rooms[:10]
            ]

            found = False
            for room in rooms:
                if room.get('token') == chat_id:
                    results['found_room'] = {
                        'name': room.get('name'),
                        'token': room.get('token'),
                        'type': room.get('type'),
                        'participant_count': room.get('participantCount', 0)
                    }
                    found = True
                    break
                elif room_name and room.get('name') == room_name:
                    results['found_room_by_name'] = {
                        'name': room.get('name'),
                        'token': room.get('token'),
                        'type': room.get('type')
                    }

            if not found:
                results['room_not_found'] = True

        except Exception as e:
            results['get_rooms_error'] = str(e)

        # Проверяем информацию о комнате
        try:
            room_info = await self.get_room_info(chat_id)
            if room_info:
                results['room_info'] = {
                    'name': room_info.get('name'),
                    'type': room_info.get('type'),
                    'participant_count': room_info.get('participantCount', 0)
                }
        except Exception as e:
            results['room_info_error'] = str(e)

        # Проверяем членство
        try:
            is_member = await self.ensure_room_membership(chat_id)
            results['is_member'] = is_member
        except Exception as e:
            results['membership_check_error'] = str(e)

        return results

    # ========== Контекстный менеджер ==========

    async def __aenter__(self):
        """Асинхронный контекстный менеджер."""
        await self._ensure_session()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Выход из контекстного менеджера."""
        await self.stop()